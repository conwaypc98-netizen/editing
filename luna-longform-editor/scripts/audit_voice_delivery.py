#!/usr/bin/env python3
"""Have Grok listen to exact generated narration and emit bound QA evidence."""

import argparse
import asyncio
import base64
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from array import array
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from urllib.parse import quote

from production_evidence import (
    canonical_sha256,
    identity_matches,
    media_identity,
    narration_sha256,
    probe_duration,
    read_json,
    resolve_media,
    sha256_bytes,
    shot_plan_spec_sha256,
    shot_spec_sha256,
    transcript_source_errors,
    voice_registration_errors,
    write_json,
    xai_voice_provenance_errors,
)

DEFAULT_MODEL = "grok-voice-think-fast-2.0"
DEFAULT_REFERENCE_SECONDS = 18.0
PCM_SAMPLE_RATE = 24000
PCM_BYTES_PER_SAMPLE = 2
PCM_CHUNK_SECONDS = 0.5

VERDICT_BOOLEAN_FIELDS = (
    "natural_delivery",
    "correct_pronunciation",
    "no_clipped_words",
    "no_stutter_or_duplicate",
    "no_audio_artifacts",
    "creator_cadence_match",
    "speaker_identity_match",
    "emotional_delivery_match",
    "confident_verdict",
)
VERDICT_FIELDS = set(VERDICT_BOOLEAN_FIELDS) | {"issues", "evidence", "summary"}


class VerdictFormatError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def strict_verdict(text: str) -> dict:
    candidate = text.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        candidate,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise VerdictFormatError(f"Model response is not valid JSON: {error.msg}.") from error
    if not isinstance(payload, dict):
        raise VerdictFormatError("Model response must be one JSON object.")
    missing = sorted(VERDICT_FIELDS - set(payload))
    extra = sorted(set(payload) - VERDICT_FIELDS)
    if missing:
        raise VerdictFormatError(
            "Model verdict is missing fields: " + ", ".join(missing) + "."
        )
    if extra:
        raise VerdictFormatError(
            "Model verdict has unexpected fields: " + ", ".join(extra) + "."
        )
    for field in VERDICT_BOOLEAN_FIELDS:
        if not isinstance(payload[field], bool):
            raise VerdictFormatError(f"Model verdict {field} must be a JSON boolean.")
    for field in ("issues", "evidence"):
        values = payload[field]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise VerdictFormatError(
                f"Model verdict {field} must be a list of non-empty strings."
            )
    if len(payload["evidence"]) < 2:
        raise VerdictFormatError(
            "Model verdict needs at least two concrete audible evidence notes."
        )
    if any(len(value.strip()) < 12 for value in payload["evidence"]):
        raise VerdictFormatError("Model audible evidence notes are too vague.")
    if not isinstance(payload["summary"], str) or len(payload["summary"].strip()) < 12:
        raise VerdictFormatError("Model verdict needs a concrete summary.")
    return payload


def verdict_failures(verdict: dict) -> list[str]:
    failures = [
        f"Grok audio verdict {field} is false."
        for field in VERDICT_BOOLEAN_FIELDS
        if verdict.get(field) is not True
    ]
    failures.extend(f"Grok audio issue: {issue.strip()}" for issue in verdict.get("issues", []))
    return failures


def pcm_signal_metrics(pcm: bytes) -> dict:
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % PCM_BYTES_PER_SAMPLE])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return {
            "duration_seconds": 0.0,
            "peak_dbfs": -120.0,
            "rms_dbfs": -120.0,
            "dc_offset_fraction": 0.0,
            "clipped_sample_count": 0,
            "severe_step_count": 0,
            "leading_silence_seconds": 0.0,
            "trailing_silence_seconds": 0.0,
            "longest_internal_silence_seconds": 0.0,
        }
    scale = 32768.0
    peak = max(abs(value) for value in samples) / scale
    rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / scale
    dc_offset = abs(sum(samples) / len(samples)) / scale
    clipped = sum(abs(value) >= 32760 for value in samples)
    severe_step = int(scale * 0.85)
    severe_steps = sum(
        abs(current - previous) >= severe_step
        for previous, current in pairwise(samples)
    )

    frame_size = max(1, PCM_SAMPLE_RATE // 100)
    frame_rms = []
    for start in range(0, len(samples), frame_size):
        frame = samples[start : start + frame_size]
        value = math.sqrt(sum(sample * sample for sample in frame) / len(frame)) / scale
        frame_rms.append(value)
    active = [value >= 0.0032 for value in frame_rms]
    if any(active):
        first_active = active.index(True)
        last_active = len(active) - 1 - active[::-1].index(True)
        longest_internal = 0
        current_silence = 0
        for is_active in active[first_active : last_active + 1]:
            if is_active:
                longest_internal = max(longest_internal, current_silence)
                current_silence = 0
            else:
                current_silence += 1
        longest_internal = max(longest_internal, current_silence)
        leading = first_active / 100
        trailing = (len(active) - 1 - last_active) / 100
    else:
        leading = len(active) / 100
        trailing = len(active) / 100
        longest_internal = len(active)
    return {
        "duration_seconds": round(len(samples) / PCM_SAMPLE_RATE, 6),
        "peak_dbfs": round(20 * math.log10(max(peak, 1e-6)), 3),
        "rms_dbfs": round(20 * math.log10(max(rms, 1e-6)), 3),
        "dc_offset_fraction": round(dc_offset, 6),
        "clipped_sample_count": clipped,
        "severe_step_count": severe_steps,
        "leading_silence_seconds": round(leading, 3),
        "trailing_silence_seconds": round(trailing, 3),
        "longest_internal_silence_seconds": round(longest_internal / 100, 3),
    }


def signal_failures(metrics: dict) -> list[str]:
    errors = []
    if float(metrics.get("duration_seconds", 0.0)) < 0.25:
        errors.append("Deterministic audio check: candidate is too short to review.")
    if int(metrics.get("clipped_sample_count", 0)) > 0:
        errors.append("Deterministic audio check: candidate contains clipped PCM samples.")
    if float(metrics.get("rms_dbfs", -120.0)) < -45.0:
        errors.append("Deterministic audio check: candidate speech level is implausibly quiet.")
    if float(metrics.get("dc_offset_fraction", 0.0)) > 0.02:
        errors.append("Deterministic audio check: candidate has excessive DC offset.")
    if int(metrics.get("severe_step_count", 0)) > 2:
        errors.append("Deterministic audio check: candidate has repeated click-like sample jumps.")
    if float(metrics.get("longest_internal_silence_seconds", 0.0)) > 1.8:
        errors.append("Deterministic audio check: candidate has an awkward internal pause over 1.8s.")
    return errors


def response_text_from_streams(streams: dict[str, list[str]]) -> str:
    for event_type in (
        "response.output_text.delta",
        "response.text.delta",
        "response.output_audio_transcript.delta",
    ):
        text = "".join(streams.get(event_type, [])).strip()
        if text:
            return text
    raise RuntimeError("xAI completed the review without a text verdict.")


def event_error(event: dict) -> RuntimeError:
    error = event.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or "unknown xAI error")
    else:
        message = str(error or "unknown xAI error")
    return RuntimeError(f"xAI realtime error: {message}")


async def wait_for_event(ws, event_type: str, timeout_seconds: float = 30.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for {event_type}.")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        if isinstance(raw, bytes):
            continue
        event = json.loads(raw)
        if event.get("type") == "error":
            raise event_error(event)
        if event.get("type") == event_type:
            return event


async def collect_response_text(ws, timeout_seconds: float) -> str:
    streams = {
        "response.output_text.delta": [],
        "response.text.delta": [],
        "response.output_audio_transcript.delta": [],
    }
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for xAI's completed audio verdict.")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        if isinstance(raw, bytes):
            continue
        event = json.loads(raw)
        event_type = event.get("type")
        if event_type == "error":
            raise event_error(event)
        if event_type in streams:
            delta = event.get("delta")
            if isinstance(delta, str):
                streams[event_type].append(delta)
        if event_type == "response.done":
            response = event.get("response")
            status = response.get("status") if isinstance(response, dict) else None
            if status in {"cancelled", "failed", "incomplete"}:
                raise RuntimeError(f"xAI realtime response ended with status {status}.")
            return response_text_from_streams(streams)


async def send_text_item(ws, text: str) -> None:
    await ws.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
    )


async def send_pcm_turn(ws, pcm: bytes) -> None:
    chunk_size = int(PCM_SAMPLE_RATE * PCM_BYTES_PER_SAMPLE * PCM_CHUNK_SECONDS)
    for start in range(0, len(pcm), chunk_size):
        encoded = base64.b64encode(pcm[start : start + chunk_size]).decode("ascii")
        await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": encoded}))
    await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
    await wait_for_event(ws, "input_audio_buffer.committed")


async def review_audio_pair(
    reference_pcm: bytes,
    candidate_pcm: bytes,
    model: str,
    instructions: str,
    timeout_seconds: float,
) -> str:
    try:
        import websockets
    except ImportError as error:
        raise RuntimeError(
            "The Luna transcription environment is missing websockets. Run setup again."
        ) from error

    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("XAI_API_KEY is not set.")
    url = f"wss://api.x.ai/v1/realtime?model={quote(model, safe='-.')}"
    async with websockets.connect(
        url,
        additional_headers={"Authorization": f"Bearer {key}"},
        open_timeout=30,
        close_timeout=10,
        max_size=8 * 1024 * 1024,
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "instructions": (
                            "You are a skeptical professional dialogue editor. Judge only audio "
                            "you actually receive. Never infer a pass from the supplied transcript "
                            "or provenance. The first committed audio turn is a known reviewed "
                            "speaker reference. The second is the candidate narration."
                        ),
                        "reasoning": {"effort": "high"},
                        "turn_detection": {"type": None},
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": PCM_SAMPLE_RATE},
                                "transport": "json",
                            }
                        },
                    },
                }
            )
        )
        await wait_for_event(ws, "session.updated")
        await send_text_item(
            ws,
            "REFERENCE AUDIO: known-good Colin/Luna tutorial voice. Compare identity and delivery texture.",
        )
        await send_pcm_turn(ws, reference_pcm)
        await send_text_item(
            ws,
            "CANDIDATE AUDIO: generated narration to judge against the reference and contract.",
        )
        await send_pcm_turn(ws, candidate_pcm)
        await ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "modalities": ["text"],
                        "instructions": instructions,
                        "metadata": {"purpose": "luna_voice_delivery_audit"},
                    },
                }
            )
        )
        return await collect_response_text(ws, timeout_seconds)


def pcm_from_audio(path: Path, start: float = 0.0, duration: float | None = None) -> bytes:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required for the Grok audio-listening audit.")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(path),
    ]
    if duration is not None:
        command.extend(["-t", f"{duration:.6f}"])
    command.extend(
        [
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            str(PCM_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
            "pipe:1",
        ]
    )
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise SystemExit(f"Unable to decode audio for listening review: {detail}")
    if len(completed.stdout) < PCM_SAMPLE_RATE * PCM_BYTES_PER_SAMPLE // 4:
        raise SystemExit(f"Audio is too short for a reliable listening review: {path}")
    return completed.stdout


def transcript_word_midpoints(path: Path) -> list[float]:
    try:
        transcript = read_json(path)
    except (json.JSONDecodeError, OSError):
        return []
    midpoints = []
    for segment in transcript.get("segments", []):
        words = segment.get("words") if isinstance(segment, dict) else None
        if not isinstance(words, list):
            continue
        for word in words:
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end >= start >= 0:
                midpoints.append((start + end) / 2)
    return sorted(midpoints)


def select_reference_window(
    reference: Path,
    transcript: Path,
    requested_seconds: float,
) -> dict:
    total = probe_duration(reference)
    duration = min(requested_seconds, total)
    if duration < 3.0:
        raise SystemExit("Registered owner reference is too short for identity comparison.")
    midpoints = transcript_word_midpoints(transcript)
    if not midpoints:
        start = max(0.0, (total - duration) / 2)
        method = "center_fallback"
    else:
        left = 0
        best_left = 0
        best_right = 0
        for right, value in enumerate(midpoints):
            while value - midpoints[left] > duration and left < right:
                left += 1
            if right - left > best_right - best_left:
                best_left, best_right = left, right
        start = max(0.0, min(midpoints[best_left] - 0.5, total - duration))
        method = "densest_word_window"
    return {
        "start_seconds": round(start, 6),
        "duration_seconds": round(duration, 6),
        "selection_method": method,
    }


def unique_shot(plan: dict, shot_id: str) -> dict:
    matches = [shot for shot in plan.get("shots", []) if str(shot.get("id")) == shot_id]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one shot named {shot_id}; found {len(matches)}.")
    return matches[0]


def unique_audit_entry(audit: dict, shot_id: str) -> dict:
    matches = [item for item in audit.get("shots", []) if str(item.get("id")) == shot_id]
    if len(matches) != 1:
        raise SystemExit(
            f"Transcript audit must contain exactly one entry for {shot_id}; found {len(matches)}."
        )
    return matches[0]


def resolve_identity_path(identity: object) -> Path | None:
    if not isinstance(identity, dict) or not str(identity.get("path", "")).strip():
        return None
    return Path(str(identity["path"])).expanduser().resolve()


def preflight_evidence(
    plan_path: Path,
    plan: dict,
    shot: dict,
    voiceover: Path,
    transcript_audit_path: Path,
    registration_path: Path,
) -> dict:
    shot_id = str(shot.get("id"))
    errors = voice_registration_errors(registration_path)
    registration = read_json(registration_path)
    voice_id = str(registration.get("voice_id", ""))
    errors.extend(
        xai_voice_provenance_errors(
            shot,
            voiceover,
            expected_voice_id=voice_id,
            registration=registration_path,
        )
    )
    transcript_audit = read_json(transcript_audit_path)
    if transcript_audit.get("passed") is not True:
        errors.append("The exact transcript comparison audit is not passing.")
    if transcript_audit.get("shot_plan_spec_sha256") != shot_plan_spec_sha256(plan):
        errors.append("The transcript comparison audit belongs to another shot-plan specification.")
    entry = unique_audit_entry(transcript_audit, shot_id)
    if entry.get("passed") is not True:
        errors.append("This shot failed exact transcript comparison.")
    if entry.get("shot_spec_sha256") != shot_spec_sha256(shot):
        errors.append("The transcript comparison entry is stale for this shot specification.")
    if entry.get("narration_sha256") != narration_sha256(str(shot.get("narration", ""))):
        errors.append("The transcript comparison entry has different approved narration.")
    if not identity_matches(entry.get("voiceover_identity"), voiceover):
        errors.append("The transcript comparison entry belongs to different voiceover bytes.")
    transcript = resolve_identity_path(entry.get("transcript_identity"))
    if transcript is None or not identity_matches(entry.get("transcript_identity"), transcript):
        errors.append("The transcript comparison entry has missing or stale transcript bytes.")
    elif transcript_source_errors(transcript, voiceover, require_identity=True):
        errors.append("The shot transcript was generated from different voiceover bytes.")
    provenance = voiceover.with_suffix(voiceover.suffix + ".xai.json")
    reference = resolve_identity_path(registration.get("reference_identity"))
    reference_transcript = resolve_identity_path(registration.get("transcript_identity"))
    if reference is None or not identity_matches(registration.get("reference_identity"), reference):
        errors.append("The registered owner-reference audio is missing or stale.")
    if reference_transcript is None or not identity_matches(
        registration.get("transcript_identity"), reference_transcript
    ):
        errors.append("The registered owner-reference transcript is missing or stale.")
    if errors:
        raise SystemExit("Voice-delivery audit preflight failed:\n- " + "\n- ".join(errors))
    return {
        "shot_id": shot_id,
        "voice_id": voice_id,
        "voiceover": voiceover,
        "voiceover_identity": media_identity(voiceover),
        "voice_provenance": provenance,
        "voice_provenance_identity": media_identity(provenance),
        "registration": registration,
        "registration_identity": media_identity(registration_path),
        "reference": reference,
        "reference_identity": media_identity(reference),
        "reference_transcript": reference_transcript,
        "transcript": transcript,
        "transcript_identity": media_identity(transcript),
        "transcript_audit_identity": media_identity(transcript_audit_path),
        "transcript_audit_entry": entry,
        "transcript_audit_entry_sha256": canonical_sha256(entry),
        "shot_plan_identity": media_identity(plan_path),
    }


def review_instructions(shot: dict, audit_entry: dict) -> str:
    performance = shot.get("voice_performance")
    performance = performance if isinstance(performance, dict) else {}
    schema = {
        "natural_delivery": True,
        "correct_pronunciation": True,
        "no_clipped_words": True,
        "no_stutter_or_duplicate": True,
        "no_audio_artifacts": True,
        "creator_cadence_match": True,
        "speaker_identity_match": True,
        "emotional_delivery_match": True,
        "confident_verdict": True,
        "issues": [],
        "evidence": [
            "Concrete audible comparison between reference and candidate.",
            "Concrete audible observation about timing, words, or audio integrity.",
        ],
        "summary": "A concise evidence-based conclusion.",
    }
    contract = {
        "approved_narration": str(shot.get("narration", "")),
        "transcribed_candidate": str(audit_entry.get("actual_text", "")),
        "delivery_intent": str(performance.get("delivery_intent", "")),
        "pronunciation_checks": performance.get("pronunciation_checks", []),
        "target_words_per_minute": performance.get("target_words_per_minute"),
        "retake_triggers": performance.get("retake_triggers", []),
    }
    return (
        "Listen to both audio turns from beginning to end. Compare the candidate directly with "
        "the reference for speaker identity and tutorial delivery texture. Check every candidate "
        "word against the approved narration; specifically reject clipped starts/endings, wrong "
        "pronunciation, stutters, duplicated words, awkward pauses, robotic prosody, static, clicks, "
        "or emotional delivery that conflicts with the contract. Do not pass an item you cannot "
        "hear confidently. Return only one JSON object with exactly the shown fields, no markdown.\n"
        f"CONTRACT: {json.dumps(contract, ensure_ascii=True)}\n"
        f"REQUIRED JSON SHAPE: {json.dumps(schema, ensure_ascii=True)}"
    )


def safe_attempt_error(error: BaseException) -> str:
    message = str(error)
    key = os.environ.get("XAI_API_KEY", "")
    if key:
        message = message.replace(key, "<redacted>")
    message = re.sub(
        r"Bearer\s+\S+",
        "Bearer <redacted>",
        message,
        flags=re.IGNORECASE,
    )
    return f"{type(error).__name__}: {message[:700]}"


def voice_delivery_audit_status(
    report_path: Path,
    plan: dict,
    shot: dict,
    voiceover: Path,
    transcript_audit_path: Path,
    registration_path: Path,
    expected_model: str = DEFAULT_MODEL,
) -> dict:
    if not report_path.is_file():
        return {"outcome": "stale", "errors": ["Voice-delivery audit is missing."]}
    try:
        report = read_json(report_path)
        transcript_audit = read_json(transcript_audit_path)
        registration = read_json(registration_path)
    except (json.JSONDecodeError, OSError) as error:
        return {"outcome": "stale", "errors": [f"Voice-delivery evidence is unreadable: {error}"]}
    shot_id = str(shot.get("id"))
    errors = []
    if report.get("kind") != "voice_delivery_audit":
        errors.append("Report kind is not voice_delivery_audit.")
    if report.get("provider") != "xai":
        errors.append("Voice-delivery report provider is not xAI.")
    if report.get("model") != expected_model:
        errors.append("Voice-delivery report used a different reviewer model.")
    if report.get("dry_run") is not False:
        errors.append("Dry-run evidence cannot approve generated narration.")
    if report.get("shot_id") != shot_id:
        errors.append("Voice-delivery report belongs to another shot.")
    if report.get("shot_spec_sha256") != shot_spec_sha256(shot):
        errors.append("Voice-delivery report is stale because the shot specification changed.")
    if report.get("narration_sha256") != narration_sha256(str(shot.get("narration", ""))):
        errors.append("Voice-delivery report has different approved narration.")
    if not identity_matches(report.get("voiceover_identity"), voiceover):
        errors.append("Voice-delivery report belongs to different voiceover bytes.")
    if not identity_matches(report.get("voice_registration_identity"), registration_path):
        errors.append("Voice-delivery report has stale xAI registration evidence.")
    registration_errors = voice_registration_errors(
        registration_path,
        str(report.get("voice_id", "")) or None,
    )
    errors.extend(registration_errors)
    if report.get("reference_identity") != registration.get("reference_identity"):
        errors.append("Voice-delivery report compared against a different owner reference.")
    reference = resolve_identity_path(report.get("reference_identity"))
    if reference is None or not identity_matches(report.get("reference_identity"), reference):
        errors.append("Voice-delivery report owner reference is missing or stale.")
    provenance = voiceover.with_suffix(voiceover.suffix + ".xai.json")
    if not identity_matches(report.get("voice_provenance_identity"), provenance):
        errors.append("Voice-delivery report has stale xAI generation provenance.")
    errors.extend(
        xai_voice_provenance_errors(
            shot,
            voiceover,
            expected_voice_id=str(registration.get("voice_id", "")) or None,
            registration=registration_path,
        )
    )
    try:
        entry = unique_audit_entry(transcript_audit, shot_id)
    except SystemExit as error:
        entry = {}
        errors.append(str(error))
    if transcript_audit.get("passed") is not True or entry.get("passed") is not True:
        errors.append("Current exact transcript comparison is not passing.")
    if transcript_audit.get("shot_plan_spec_sha256") != shot_plan_spec_sha256(plan):
        errors.append("Current exact transcript comparison is stale for the plan.")
    if report.get("transcript_audit_entry_sha256") != canonical_sha256(entry):
        errors.append("Voice-delivery report has stale per-shot transcript evidence.")
    current_contract = review_instructions(shot, entry)
    if report.get("review_contract_sha256") != sha256_bytes(
        current_contract.encode("utf-8")
    ):
        errors.append("Voice-delivery report used a different listening-review contract.")
    transcript = resolve_identity_path(entry.get("transcript_identity"))
    if transcript is None or not identity_matches(report.get("transcript_identity"), transcript):
        errors.append("Voice-delivery report has stale transcript bytes.")
    sample = report.get("reference_sample")
    if not isinstance(sample, dict) or not re.fullmatch(
        r"[0-9a-f]{64}", str(sample.get("pcm_sha256", ""))
    ):
        errors.append("Voice-delivery report has no exact reference-sample PCM identity.")
    signal = report.get("candidate_signal")
    stored_signal_errors = report.get("deterministic_signal_errors")
    if not isinstance(signal, dict) or not isinstance(stored_signal_errors, list):
        errors.append("Voice-delivery report has no deterministic candidate-signal evidence.")
    elif stored_signal_errors != signal_failures(signal):
        errors.append("Voice-delivery deterministic signal verdict was altered after analysis.")
    response_text = report.get("model_response")
    if (
        isinstance(response_text, str)
        and response_text
        and report.get("model_response_sha256")
        != sha256_bytes(response_text.encode("utf-8"))
    ):
        errors.append("Voice-delivery model response text changed after the audit.")
    if errors:
        return {"outcome": "stale", "errors": sorted(set(errors)), "report": report}

    status = report.get("status")
    if status == "failed" and report.get("failure_kind") == "deterministic_signal":
        if not stored_signal_errors:
            return {
                "outcome": "stale",
                "errors": ["Signal-failure report contains no concrete signal failures."],
                "report": report,
            }
        return {
            "outcome": "failed",
            "errors": list(stored_signal_errors),
            "verdict": None,
            "report": report,
        }
    if status == "inconclusive":
        attempts = report.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            return {
                "outcome": "stale",
                "errors": ["Inconclusive report has no bounded-attempt evidence."],
                "report": report,
            }
        return {
            "outcome": "inconclusive",
            "errors": list(report.get("errors", [])),
            "report": report,
        }
    if not isinstance(response_text, str) or not response_text.strip():
        return {
            "outcome": "stale",
            "errors": ["Completed voice-delivery report has no exact model response."],
            "report": report,
        }
    try:
        parsed = strict_verdict(response_text)
    except ValueError as error:
        return {"outcome": "stale", "errors": [str(error)], "report": report}
    if report.get("verdict") != parsed:
        return {
            "outcome": "stale",
            "errors": ["Stored voice verdict does not equal the exact model response."],
            "report": report,
        }
    failures = verdict_failures(parsed) + list(stored_signal_errors)
    expected_status = "passed" if not failures else "failed"
    if status != expected_status:
        return {
            "outcome": "stale",
            "errors": ["Voice-delivery status disagrees with its exact verdict."],
            "report": report,
        }
    return {
        "outcome": expected_status,
        "errors": failures,
        "verdict": parsed,
        "report": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Send an exact registered owner-reference excerpt and exact generated shot to "
            "Grok Voice for a fail-closed listening verdict."
        )
    )
    parser.add_argument("--shot-plan", required=True)
    parser.add_argument("--shot-id", required=True)
    parser.add_argument("--voiceover")
    parser.add_argument("--transcript-audit", required=True)
    parser.add_argument("--voice-registration", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reference-seconds", type=float, default=DEFAULT_REFERENCE_SECONDS)
    parser.add_argument("--maximum-candidate-seconds", type=float, default=90.0)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    if args.reference_seconds < 3 or args.reference_seconds > 30:
        raise SystemExit("--reference-seconds must be between 3 and 30.")
    if args.max_attempts < 1 or args.max_attempts > 4:
        raise SystemExit("--max-attempts must be between 1 and 4.")
    if args.maximum_candidate_seconds <= 0:
        raise SystemExit("--maximum-candidate-seconds must be positive.")

    plan_path = Path(args.shot_plan).expanduser().resolve()
    plan = read_json(plan_path)
    shot = unique_shot(plan, args.shot_id)
    voiceover = resolve_media(args.voiceover or shot.get("voiceover"), plan_path.parent)
    if voiceover is None or not voiceover.is_file():
        raise SystemExit(f"Voiceover media not found: {voiceover}")
    transcript_audit_path = Path(args.transcript_audit).expanduser().resolve()
    registration_path = Path(args.voice_registration).expanduser().resolve()
    if not transcript_audit_path.is_file() or not registration_path.is_file():
        raise SystemExit("Transcript audit and xAI voice registration are required.")
    evidence = preflight_evidence(
        plan_path,
        plan,
        shot,
        voiceover,
        transcript_audit_path,
        registration_path,
    )
    candidate_duration = probe_duration(voiceover)
    if candidate_duration > args.maximum_candidate_seconds:
        raise SystemExit(
            f"Candidate is {candidate_duration:.3f}s; split the shot before audio review "
            f"(maximum {args.maximum_candidate_seconds:.3f}s)."
        )
    reference_window = select_reference_window(
        evidence["reference"],
        evidence["reference_transcript"],
        args.reference_seconds,
    )
    reference_pcm = pcm_from_audio(
        evidence["reference"],
        reference_window["start_seconds"],
        reference_window["duration_seconds"],
    )
    candidate_pcm = pcm_from_audio(voiceover)
    candidate_signal = pcm_signal_metrics(candidate_pcm)
    deterministic_signal_errors = signal_failures(candidate_signal)
    instructions = review_instructions(shot, evidence["transcript_audit_entry"])
    reference_window.update(
        {
            "format": "pcm_s16le_mono_24000hz",
            "pcm_bytes": len(reference_pcm),
            "pcm_sha256": sha256_bytes(reference_pcm),
        }
    )
    report = {
        "schema_version": 1,
        "kind": "voice_delivery_audit",
        "provider": "xai",
        "model": args.model,
        "dry_run": bool(args.dry_run),
        "shot_id": args.shot_id,
        "shot_plan_identity": evidence["shot_plan_identity"],
        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
        "shot_spec_sha256": shot_spec_sha256(shot),
        "narration_sha256": narration_sha256(str(shot.get("narration", ""))),
        "voice_id": evidence["voice_id"],
        "voiceover_identity": evidence["voiceover_identity"],
        "voice_provenance_identity": evidence["voice_provenance_identity"],
        "voice_registration_identity": evidence["registration_identity"],
        "reference_identity": evidence["reference_identity"],
        "reference_sample": reference_window,
        "transcript_identity": evidence["transcript_identity"],
        "transcript_audit_identity_at_review": evidence["transcript_audit_identity"],
        "transcript_audit_entry_sha256": evidence["transcript_audit_entry_sha256"],
        "review_contract_sha256": sha256_bytes(instructions.encode("utf-8")),
        "candidate_duration_seconds": round(candidate_duration, 6),
        "candidate_pcm_sha256": sha256_bytes(candidate_pcm),
        "candidate_signal": candidate_signal,
        "deterministic_signal_errors": deterministic_signal_errors,
        "attempts": [],
        "status": "inconclusive",
        "failure_kind": None,
        "passed": False,
        "verdict": None,
        "model_response": None,
        "model_response_sha256": None,
        "errors": [],
        "audited_at": now_iso(),
    }
    if args.dry_run:
        report["attempts"].append(
            {"attempt": 0, "error": "Dry run: no audio was sent to xAI."}
        )
        report["errors"] = ["Dry-run evidence cannot approve generated narration."]
    elif deterministic_signal_errors:
        report["attempts"].append(
            {
                "attempt": 0,
                "skipped": "Grok call skipped because deterministic signal checks failed.",
            }
        )
        report["status"] = "failed"
        report["failure_kind"] = "deterministic_signal"
        report["errors"] = deterministic_signal_errors
    else:
        final_text = None
        final_verdict = None
        for attempt in range(1, args.max_attempts + 1):
            try:
                response_text = asyncio.run(
                    review_audio_pair(
                        reference_pcm,
                        candidate_pcm,
                        args.model,
                        instructions,
                        args.timeout_seconds,
                    )
                )
                response_hash = sha256_bytes(response_text.encode("utf-8"))
                try:
                    parsed = strict_verdict(response_text)
                except ValueError as error:
                    report["attempts"].append(
                        {
                            "attempt": attempt,
                            "response_sha256": response_hash,
                            "error": str(error),
                        }
                    )
                    if attempt < args.max_attempts:
                        time.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    final_text = response_text
                    break
                report["attempts"].append(
                    {"attempt": attempt, "response_sha256": response_hash, "parsed": True}
                )
                final_text = response_text
                final_verdict = parsed
                break
            except (Exception, SystemExit) as error:  # noqa: BLE001
                report["attempts"].append(
                    {"attempt": attempt, "error": safe_attempt_error(error)}
                )
                if attempt < args.max_attempts:
                    time.sleep(min(2 ** (attempt - 1), 4))
        if final_text is not None:
            report["model_response"] = final_text
            report["model_response_sha256"] = sha256_bytes(final_text.encode("utf-8"))
        if final_verdict is not None:
            failures = verdict_failures(final_verdict)
            report["verdict"] = final_verdict
            report["errors"] = failures
            report["status"] = "failed" if failures else "passed"
            report["failure_kind"] = "model_verdict" if failures else None
            report["passed"] = not failures
        else:
            report["errors"] = [
                str(item.get("error"))
                for item in report["attempts"]
                if str(item.get("error", "")).strip()
            ] or ["Grok did not return a parseable listening verdict."]

    output = Path(args.report).expanduser().resolve()
    write_json(output, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
