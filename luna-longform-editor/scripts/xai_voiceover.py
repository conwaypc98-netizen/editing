#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


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


def request_bytes(path: str, method: str = "GET", payload: dict | None = None) -> tuple[bytes, str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key()}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(API_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"xAI API error {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"Unable to reach xAI API: {error}") from error


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


def decode_audio_response(body: bytes, content_type: str) -> bytes:
    looks_json = "json" in content_type.lower() or body.lstrip().startswith(b"{")
    if not looks_json:
        return body
    payload = json.loads(body.decode("utf-8"))
    encoded = payload.get("audio")
    if not encoded:
        raise SystemExit(f"xAI TTS response did not contain audio: {payload}")
    return base64.b64decode(encoded)


def synthesize_chunk(text: str, voice: str, language: str, output: Path) -> None:
    body, content_type = request_bytes(
        "/v1/tts",
        method="POST",
        payload={
            "text": text,
            "voice_id": voice,
            "language": language,
            "output_format": {
                "codec": "wav",
                "sample_rate": 44100,
            },
        },
    )
    audio = decode_audio_response(body, content_type)
    if len(audio) < 128:
        raise SystemExit("xAI TTS returned an unexpectedly small audio payload.")
    output.write_bytes(audio)


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
                "44100",
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
    if not re.fullmatch(r"[a-z0-9]{8}", voice):
        raise SystemExit(
            "Custom xAI voice IDs are expected to be 8 lowercase letters/digits. "
            "Use --built-in-voice only for an xAI stock voice."
        )


def synthesize_text(
    text: str,
    output: Path,
    voice: str,
    language: str,
    dry_run: bool,
) -> dict:
    chunks = split_text(text)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "provider": "xai",
        "voice_id": voice,
        "language": language,
        "characters": len(text),
        "requests": len(chunks),
        "output": str(output),
        "dry_run": dry_run,
    }
    if dry_run:
        return manifest
    with tempfile.TemporaryDirectory(prefix="luna-xai-voice-") as directory:
        parts = []
        for index, chunk in enumerate(chunks, start=1):
            part = Path(directory) / f"part_{index:03d}.wav"
            synthesize_chunk(chunk, voice, language, part)
            parts.append(part)
        concatenate_wav(parts, output)
    if not output.exists() or output.stat().st_size < 128:
        raise SystemExit("Narration output was not created correctly.")
    return manifest


def read_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    return Path(args.text_file).expanduser().read_text(encoding="utf-8")


def cmd_check(args: argparse.Namespace) -> int:
    voice = voice_id(args.voice_id)
    enforce_consent(args, voice)
    if args.dry_run:
        print(json.dumps({"provider": "xai", "voice_id": voice, "dry_run": True}, indent=2))
        return 0
    endpoint = f"/v1/tts/voices/{voice}" if args.built_in_voice else f"/v1/custom-voices/{voice}"
    body, _ = request_bytes(endpoint)
    print(body.decode("utf-8", errors="replace"))
    return 0


def cmd_synthesize(args: argparse.Namespace) -> int:
    voice = voice_id(args.voice_id)
    enforce_consent(args, voice)
    output = Path(args.output).expanduser().resolve()
    result = synthesize_text(read_text(args), output, voice, args.language, args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    voice = voice_id(args.voice_id)
    enforce_consent(args, voice)
    plan_path = Path(args.shot_plan).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    shots = plan.get("shots", [])
    if not shots:
        raise SystemExit("Shot plan contains no shots.")
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.get("id", f"shot-{index:03d}"))
        narration = str(shot.get("narration", "")).strip()
        if not narration:
            raise SystemExit(f"{shot_id} has no narration.")
        output = output_dir / f"{shot_id}.wav"
        generated.append(synthesize_text(narration, output, voice, args.language, args.dry_run))
    manifest = {"shot_plan": str(plan_path), "generated": generated}
    manifest_path = output_dir / "voiceover_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


def add_voice_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--voice-id")
    parser.add_argument("--language", default="en")
    parser.add_argument("--owner-consent-confirmed", action="store_true")
    parser.add_argument("--built-in-voice", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


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
    plan.set_defaults(func=cmd_plan)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
