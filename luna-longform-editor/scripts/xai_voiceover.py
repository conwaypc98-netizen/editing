#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from production_evidence import (
    media_identity,
    narration_sha256,
    probe_duration,
    shot_plan_spec_sha256,
    shot_spec_sha256,
    spoken_tokens,
    target_wpm_for_shot,
    tts_text_for_shot,
    write_json,
)

API_BASE = "https://api.x.ai"
MAX_REQUEST_CHARS = 14000


def api_key() -> str:
    value = os.environ.get("XAI_API_KEY", "").strip()
    if not value:
        raise SystemExit("XAI_API_KEY is not set.")
    return value


def voice_id(explicit: str | None) -> str:
    value = (explicit or os.environ.get("XAI_VOICE_ID", "")).strip()
    if not value:
        raise SystemExit("Provide --voice-id or set XAI_VOICE_ID after creating a verified xAI voice.")
    return value


def request_bytes(
    path: str,
    method: str = "GET",
    payload: dict | None = None,
    max_retries: int = 4,
) -> tuple[bytes, str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key()}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(max_retries):
        request = urllib.request.Request(API_BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code in {429, 500, 503} and attempt + 1 < max_retries:
                time.sleep(2**attempt)
                continue
            raise SystemExit(f"xAI API error {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            if attempt + 1 < max_retries:
                time.sleep(2**attempt)
                continue
            raise SystemExit(f"Unable to reach xAI API: {error}") from error
    raise SystemExit("xAI API request exhausted all retries.")


def split_text(text: str, limit: int = MAX_REQUEST_CHARS) -> list[str]:
    text = text.strip()
    if not text:
        raise SystemExit("Narration text is empty.")
    if len(text) <= limit:
        return [text]
    parts = re.split(r"(?<=[.!?])\s+|\n\s*\n", text)
    chunks = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > limit:
            raise SystemExit(
                "A narration sentence exceeds the xAI request limit. Split it manually so speech tags remain valid."
            )
        candidate = f"{current} {part}".strip()
        if len(candidate) > limit and current:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def decode_audio_response(body: bytes, content_type: str) -> tuple[bytes, dict]:
    looks_json = "json" in content_type.lower() or body.lstrip().startswith(b"{")
    if not looks_json:
        return body, {"content_type": content_type}
    payload = json.loads(body.decode("utf-8"))
    encoded = payload.get("audio")
    if not encoded:
        raise SystemExit(f"xAI TTS response did not contain audio: {payload}")
    metadata = {
        "content_type": payload.get("content_type", content_type),
        "duration": payload.get("duration"),
        "audio_timestamps": payload.get("audio_timestamps"),
    }
    return base64.b64decode(encoded), metadata


def synthesize_chunk(
    text: str,
    voice: str,
    language: str,
    output: Path,
    speed: float,
    text_normalization: bool,
    with_timestamps: bool,
) -> dict:
    body, content_type = request_bytes(
        "/v1/tts",
        method="POST",
        payload={
            "text": text,
            "voice_id": voice,
            "language": language,
            "output_format": {
                "codec": "wav",
                "sample_rate": 48000,
            },
            "speed": speed,
            "text_normalization": text_normalization,
            "with_timestamps": with_timestamps,
        },
    )
    audio, metadata = decode_audio_response(body, content_type)
    if len(audio) < 128:
        raise SystemExit("xAI TTS returned an unexpectedly small audio payload.")
    output.write_bytes(audio)
    return metadata


def concatenate_wav(parts: list[Path], output: Path) -> None:
    if len(parts) == 1:
        shutil.copy2(parts[0], output)
        return
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required to concatenate long narration.")
    with tempfile.TemporaryDirectory(prefix="luna-xai-concat-") as directory:
        concat_file = Path(directory) / "parts.txt"
        escaped_parts = [part.as_posix().replace("'", "'\\''") for part in parts]
        concat_file.write_text(
            "\n".join(f"file '{part}'" for part in escaped_parts) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "1",
                str(output),
            ],
            check=True,
        )


def enforce_consent(args: argparse.Namespace, voice: str) -> None:
    if args.built_in_voice:
        return
    if not args.owner_consent_confirmed:
        raise SystemExit(
            "Custom voice use requires --owner-consent-confirmed. The voice must belong to the user "
            "and must have completed xAI's ownership verification flow."
        )


def verify_voice(voice: str, built_in: bool, dry_run: bool) -> dict:
    if not built_in and not re.fullmatch(r"[a-z0-9]{8}", voice):
        raise SystemExit(
            "Custom xAI voice IDs are expected to be 8 lowercase letters/digits. "
            "Use --built-in-voice only for an xAI stock voice."
        )
    if dry_run:
        return {"voice_id": voice, "verified": False, "dry_run": True}
    endpoint = f"/v1/tts/voices/{voice}" if built_in else f"/v1/custom-voices/{voice}"
    body, _ = request_bytes(endpoint)
    payload = json.loads(body.decode("utf-8"))
    if payload.get("voice_id") != voice:
        raise SystemExit("xAI returned a different voice ID than requested.")
    return {"voice_id": voice, "verified": True, "metadata": payload}


def synthesize_attempt(
    chunks: list[str],
    output: Path,
    voice: str,
    language: str,
    speed: float,
    text_normalization: bool,
    with_timestamps: bool,
) -> dict:
    parts = []
    chunk_metadata = []
    timestamp_offset = 0.0
    combined_chars = []
    combined_times = []
    for index, chunk in enumerate(chunks, start=1):
        part = output.parent / f"{output.stem}-part-{index:03d}.wav"
        metadata = synthesize_chunk(
            chunk,
            voice,
            language,
            part,
            speed,
            text_normalization,
            with_timestamps,
        )
        parts.append(part)
        duration = float(metadata.get("duration") or 0.0) or probe_duration(part)
        timestamps = metadata.get("audio_timestamps") or {}
        chars = timestamps.get("graph_chars", [])
        times = timestamps.get("graph_times", [])
        if len(chars) == len(times):
            combined_chars.extend(chars)
            combined_times.extend(
                [
                    [
                        round(float(start) + timestamp_offset, 6),
                        round(float(end) + timestamp_offset, 6),
                    ]
                    for start, end in times
                ]
            )
        chunk_metadata.append({"characters": len(chunk), "duration": round(duration, 6)})
        timestamp_offset += duration
    concatenate_wav(parts, output)
    if not output.exists() or output.stat().st_size < 128:
        raise SystemExit("Narration output was not created correctly.")
    return {
        "output": output,
        "duration_seconds": probe_duration(output),
        "chunks": chunk_metadata,
        "audio_timestamps": {
            "graph_chars": combined_chars,
            "graph_times": combined_times,
        }
        if with_timestamps
        else None,
    }


def cadence_distance(words_per_minute: float, target: tuple[float, float] | None) -> float:
    if target is None:
        return 0.0
    minimum, maximum = target
    if minimum <= words_per_minute <= maximum:
        return 0.0
    return minimum - words_per_minute if words_per_minute < minimum else words_per_minute - maximum


def synthesize_text(
    text: str,
    output: Path,
    voice: str,
    language: str,
    dry_run: bool,
    speed: float,
    text_normalization: bool,
    with_timestamps: bool,
    shot_context: dict | None = None,
    approved_narration: str | None = None,
    target_wpm: tuple[float, float] | None = None,
    maximum_cadence_attempts: int = 2,
) -> dict:
    chunks = split_text(text)
    approved_narration = approved_narration if approved_narration is not None else text
    word_count = len(spoken_tokens(approved_narration))
    if target_wpm is not None:
        minimum, maximum = target_wpm
        if not 100 <= minimum <= maximum <= 360:
            raise SystemExit("Target narration cadence must stay between 100 and 360 words per minute.")
    if maximum_cadence_attempts < 1 or maximum_cadence_attempts > 4:
        raise SystemExit("Maximum cadence attempts must be between 1 and 4.")
    cadence_target = target_wpm if word_count >= 8 else None

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "provider": "xai",
        "voice_id": voice,
        "language": language,
        "characters": len(text),
        "spoken_words": word_count,
        "requests_per_attempt": len(chunks),
        "output": str(output),
        "dry_run": dry_run,
        "narration_sha256": narration_sha256(approved_narration),
        "tts_text_sha256": narration_sha256(text),
        "requested_speed": speed,
        "text_normalization": text_normalization,
        "with_timestamps": with_timestamps,
    }
    manifest.update(shot_context or {})
    if dry_run:
        manifest["cadence"] = {
            "target_words_per_minute": {
                "minimum": target_wpm[0],
                "maximum": target_wpm[1],
            }
            if target_wpm
            else None,
            "maximum_attempts": maximum_cadence_attempts,
            "enforced": cadence_target is not None,
        }
        return manifest

    with tempfile.TemporaryDirectory(prefix="luna-xai-voice-") as directory:
        directory_path = Path(directory)
        attempts = []
        retry_failure = None
        current_speed = speed
        for attempt_index in range(1, maximum_cadence_attempts + 1):
            attempt_output = directory_path / f"attempt-{attempt_index:02d}.wav"
            try:
                generated = synthesize_attempt(
                    chunks,
                    attempt_output,
                    voice,
                    language,
                    current_speed,
                    text_normalization,
                    with_timestamps,
                )
            except (SystemExit, OSError, subprocess.SubprocessError, ValueError) as error:
                if not attempts:
                    raise
                retry_failure = {
                    "attempt": attempt_index,
                    "error_type": type(error).__name__,
                }
                break
            duration = float(generated["duration_seconds"])
            words_per_minute = word_count / (duration / 60.0) if duration > 0 else 0.0
            attempt = {
                **generated,
                "attempt": attempt_index,
                "speed": round(current_speed, 4),
                "words_per_minute": round(words_per_minute, 3),
                "distance_from_target": round(
                    cadence_distance(words_per_minute, cadence_target), 3
                ),
            }
            attempts.append(attempt)
            if cadence_target is None or attempt["distance_from_target"] == 0:
                break
            if words_per_minute <= 0:
                break
            target_midpoint = sum(cadence_target) / 2.0
            adjusted_speed = min(1.5, max(0.7, current_speed * target_midpoint / words_per_minute))
            if abs(adjusted_speed - current_speed) < 0.01:
                break
            current_speed = round(adjusted_speed, 4)

        selected = min(attempts, key=lambda item: item["distance_from_target"])
        with tempfile.NamedTemporaryFile(
            prefix=f"{output.stem}-approved-",
            suffix=output.suffix or ".wav",
            dir=output.parent,
            delete=False,
        ) as handle:
            staged_output = Path(handle.name)
        try:
            shutil.copy2(selected["output"], staged_output)
            staged_output.replace(output)
        finally:
            if staged_output.exists():
                staged_output.unlink()

    cadence_passed = selected["distance_from_target"] == 0
    manifest["speed"] = selected["speed"]
    manifest["media_identity"] = media_identity(output)
    manifest["chunks"] = selected["chunks"]
    manifest["cadence"] = {
        "target_words_per_minute": {
            "minimum": target_wpm[0],
            "maximum": target_wpm[1],
        }
        if target_wpm
        else None,
        "actual_words_per_minute": selected["words_per_minute"],
        "duration_seconds": round(float(selected["duration_seconds"]), 6),
        "within_target": cadence_passed,
        "enforced": cadence_target is not None,
        "attempts": [
            {
                key: value
                for key, value in attempt.items()
                if key not in {"output", "chunks", "audio_timestamps"}
            }
            for attempt in attempts
        ],
        "retry_failure": retry_failure,
    }
    if with_timestamps:
        manifest["audio_timestamps"] = selected["audio_timestamps"]
    sidecar = output.with_suffix(output.suffix + ".xai.json")
    manifest["metadata_sidecar"] = str(sidecar)
    write_json(sidecar, manifest)
    return manifest


def read_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    return Path(args.text_file).expanduser().read_text(encoding="utf-8")


def cmd_check(args: argparse.Namespace) -> int:
    voice = voice_id(args.voice_id)
    enforce_consent(args, voice)
    print(json.dumps(verify_voice(voice, args.built_in_voice, args.dry_run), indent=2))
    return 0


def cmd_synthesize(args: argparse.Namespace) -> int:
    voice = voice_id(args.voice_id)
    enforce_consent(args, voice)
    verification = verify_voice(voice, args.built_in_voice, args.dry_run)
    output = Path(args.output).expanduser().resolve()
    result = synthesize_text(
        read_text(args),
        output,
        voice,
        args.language,
        args.dry_run,
        args.speed,
        args.text_normalization,
        args.with_timestamps,
        maximum_cadence_attempts=args.maximum_cadence_attempts,
    )
    result["voice_verification"] = verification
    print(json.dumps(result, indent=2))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    voice = voice_id(args.voice_id)
    enforce_consent(args, voice)
    verification = verify_voice(voice, args.built_in_voice, args.dry_run)
    plan_path = Path(args.shot_plan).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    shots = plan.get("shots", [])
    if not shots:
        raise SystemExit("Shot plan contains no shots.")
    shots_by_id = {
        str(shot.get("id", f"shot-{index:03d}")): shot
        for index, shot in enumerate(shots, start=1)
    }
    requested_ids = list(dict.fromkeys(args.shot_id or shots_by_id))
    unknown_ids = [shot_id for shot_id in requested_ids if shot_id not in shots_by_id]
    if unknown_ids:
        raise SystemExit("Unknown --shot-id values: " + ", ".join(unknown_ids))
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for shot_id in requested_ids:
        shot = shots_by_id[shot_id]
        narration = str(shot.get("narration", "")).strip()
        if not narration:
            raise SystemExit(f"{shot_id} has no narration.")
        performance = shot.get("voice_performance")
        shot_speed = (
            float(performance.get("speed", args.speed))
            if isinstance(performance, dict)
            else args.speed
        )
        tts_text = tts_text_for_shot(shot)
        output = output_dir / f"{shot_id}.wav"
        result = synthesize_text(
            tts_text,
            output,
            voice,
            args.language,
            args.dry_run,
            shot_speed,
            args.text_normalization,
            args.with_timestamps,
            {
                "shot_id": shot_id,
                "shot_spec_sha256": shot_spec_sha256(shot),
                "voice_verified_at_generation": verification.get("verified") is True,
            },
            approved_narration=narration,
            target_wpm=target_wpm_for_shot(shot),
            maximum_cadence_attempts=args.maximum_cadence_attempts,
        )
        result.update(
            {
                "shot_id": shot_id,
                "shot_spec_sha256": shot_spec_sha256(shot),
            }
        )
        generated.append(result)
    manifest_path = output_dir / "voiceover_manifest.json"
    existing = {}
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing = {
                item.get("shot_id"): item
                for item in payload.get("generated", [])
                if isinstance(item, dict)
            }
        except (json.JSONDecodeError, OSError):
            existing = {}
    current_entries = {}
    for shot_id, shot in shots_by_id.items():
        entry = existing.get(shot_id)
        if entry and entry.get("shot_spec_sha256") == shot_spec_sha256(shot):
            current_entries[shot_id] = entry
    current_entries.update({item["shot_id"]: item for item in generated})
    manifest = {
        "shot_plan": str(plan_path),
        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
        "voice_verification": verification,
        "requested_shot_ids": requested_ids,
        "generated_this_run": generated,
        "generated": [
            current_entries[shot_id]
            for shot_id in shots_by_id
            if shot_id in current_entries
        ],
    }
    write_json(manifest_path, manifest)
    print(manifest_path)
    return 0


def add_voice_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--voice-id")
    parser.add_argument("--language", default="en")
    parser.add_argument("--owner-consent-confirmed", action="store_true")
    parser.add_argument("--built-in-voice", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--text-normalization", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--with-timestamps", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--maximum-cadence-attempts", type=int, default=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate consent-gated Luna narration with xAI TTS.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    add_voice_args(check)
    check.set_defaults(func=cmd_check)
    synthesize = sub.add_parser("synthesize")
    add_voice_args(synthesize)
    text_group = synthesize.add_mutually_exclusive_group(required=True)
    text_group.add_argument("--text")
    text_group.add_argument("--text-file")
    synthesize.add_argument("--output", required=True)
    synthesize.set_defaults(func=cmd_synthesize)
    plan = sub.add_parser("synthesize-plan")
    add_voice_args(plan)
    plan.add_argument("--shot-plan", required=True)
    plan.add_argument("--output-dir", required=True)
    plan.add_argument("--shot-id", action="append")
    plan.set_defaults(func=cmd_plan)
    args = parser.parse_args()
    if not 0.7 <= args.speed <= 1.5:
        raise SystemExit("xAI speech speed must be between 0.7 and 1.5.")
    if not 1 <= args.maximum_cadence_attempts <= 4:
        raise SystemExit("Maximum cadence attempts must be between 1 and 4.")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
