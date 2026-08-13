#!/usr/bin/env python3
"""Seal and verify an exact-byte xAI custom-voice reference review."""

import argparse
import array
import json
import math
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

from creator_fidelity import adjacent_repeated_phrases, tokens
from prepare_voice_reference import loudness
from production_evidence import (
    identity_matches,
    media_identity,
    probe_duration,
    read_json,
    transcript_source_errors,
    write_json,
)

VOICE_REFERENCE_VERDICTS = (
    "only_owner_speaks",
    "no_background_audio",
    "no_private_audio",
    "representative_tutorial_delivery",
    "no_clipped_words",
    "no_edit_artifacts",
)
REVIEWER_KINDS = ("human", "audio_capable_model")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audio_streams(path: Path) -> list[dict]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout).get("streams", [])


def pcm_signal_metrics(path: Path) -> dict:
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise SystemExit("Signal analysis requires uncompressed 16-bit PCM WAV audio.")
        samples = array.array("h", audio.readframes(audio.getnframes()))
        if sys.byteorder == "big":
            samples.byteswap()
        sample_rate = audio.getframerate()
    if not samples:
        raise SystemExit("Voice reference contains no PCM samples.")
    maximum = max(abs(value) for value in samples)
    clipped = sum(abs(value) >= 32767 for value in samples)
    mean = sum(samples) / len(samples)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    return {
        "sample_count": len(samples),
        "maximum_sample_fraction": round(maximum / 32768.0, 6),
        "clipped_sample_count": clipped,
        "clipped_sample_fraction": round(clipped / len(samples), 9),
        "dc_offset_fraction": round(mean / 32768.0, 9),
        "rms_dbfs": round(20.0 * math.log10(rms / 32768.0), 3) if rms > 0 else None,
        "duration_seconds": round(len(samples) / sample_rate, 6),
    }


def transcript_evidence(path: Path, reference: Path, duration: float) -> tuple[dict, list[str]]:
    errors = transcript_source_errors(path, reference, require_identity=True)
    payload = read_json(path)
    timed_words = []
    text_units = []
    for segment in payload.get("segments", []):
        text_units.append(str(segment.get("text", "")).strip())
        for word in segment.get("words", []):
            if word.get("start") is None or word.get("end") is None:
                continue
            timed_words.append(
                {
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "probability": word.get("probability"),
                }
            )
    timed_words.sort(key=lambda item: (item["start"], item["end"]))
    spoken_tokens = tokens(" ".join(text_units))
    probabilities = [
        float(item["probability"])
        for item in timed_words
        if item.get("probability") is not None
    ]
    gaps = [
        max(0.0, current["start"] - previous["end"])
        for previous, current in zip(timed_words, timed_words[1:])
    ]
    mean_probability = sum(probabilities) / len(probabilities) if probabilities else None
    low_probability_fraction = (
        sum(value < 0.5 for value in probabilities) / len(probabilities)
        if probabilities
        else None
    )
    wpm = len(spoken_tokens) / (duration / 60.0) if duration > 0 else 0.0
    repeats = adjacent_repeated_phrases(spoken_tokens)
    first_word = timed_words[0]["start"] if timed_words else None
    last_word = timed_words[-1]["end"] if timed_words else None

    if len(spoken_tokens) < 180:
        errors.append("Reference transcript has fewer than 180 spoken words.")
    if not 120.0 <= wpm <= 320.0:
        errors.append(f"Reference cadence {wpm:.1f} WPM is outside the 120-320 WPM guardrail.")
    if mean_probability is not None and mean_probability < 0.75:
        errors.append("Reference transcript mean word probability is below 0.75.")
    if low_probability_fraction is not None and low_probability_fraction > 0.25:
        errors.append("More than 25% of reference words have probability below 0.5.")
    if repeats:
        errors.append("Reference transcript contains adjacent repeated wording: " + ", ".join(repeats))
    if first_word is None or first_word > 2.0:
        errors.append("Reference begins with more than two seconds without transcribed owner speech.")
    if last_word is None or duration - last_word > 2.0:
        errors.append("Reference ends with more than two seconds without transcribed owner speech.")
    if gaps and max(gaps) > 1.5:
        errors.append(f"Reference contains an unexplained {max(gaps):.2f}s inter-word gap.")

    return {
        "word_count": len(spoken_tokens),
        "words_per_minute": round(wpm, 3),
        "mean_word_probability": round(mean_probability, 4)
        if mean_probability is not None
        else None,
        "low_probability_fraction": round(low_probability_fraction, 4)
        if low_probability_fraction is not None
        else None,
        "first_word_seconds": round(first_word, 3) if first_word is not None else None,
        "last_word_seconds": round(last_word, 3) if last_word is not None else None,
        "maximum_inter_word_gap_seconds": round(max(gaps), 3) if gaps else None,
        "adjacent_repeated_phrases": repeats,
    }, errors


def quality_evidence(
    reference: Path,
    preparation_report: Path,
    transcript: Path,
) -> tuple[dict, list[str], list[str]]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required.")
    preparation = read_json(preparation_report)
    errors = []
    warnings = []
    if preparation.get("provider_target") != "xai_custom_voice":
        errors.append("Preparation report does not target an xAI custom voice.")
    if preparation.get("owner_consent_confirmed") is not True:
        errors.append("Preparation report does not confirm owner consent.")
    if int(preparation.get("schema_version", 0)) < 2:
        errors.append("Preparation report predates source/transcript binding; rebuild the reference.")
    if not identity_matches(preparation.get("output_identity"), reference):
        errors.append("Preparation report belongs to different reference-audio bytes.")
    for field in ("source_identity", "source_transcript_identity"):
        identity = preparation.get(field)
        path = Path(str(identity.get("path", ""))).expanduser() if isinstance(identity, dict) else None
        if path is None or not identity_matches(identity, path):
            errors.append(f"Preparation report has missing or stale {field} evidence.")

    duration = probe_duration(reference)
    if not 90.0 <= duration <= 120.0:
        errors.append(f"Reference duration {duration:.3f}s is outside the 90-120 second target.")
    streams = audio_streams(reference)
    stream = streams[0] if len(streams) == 1 else {}
    if len(streams) != 1:
        errors.append("Reference must contain exactly one audio stream.")
    codec = str(stream.get("codec_name", ""))
    if codec != "pcm_s16le":
        errors.append("Reference must use uncompressed 16-bit little-endian PCM audio.")
    try:
        sample_rate = int(stream.get("sample_rate", 0))
    except (TypeError, ValueError):
        sample_rate = 0
    if sample_rate != 24000:
        warnings.append(
            f"Reference uses {sample_rate or 'unknown'} Hz; xAI recommends 24 kHz and will downsample higher rates."
        )
    if int(stream.get("channels", 0) or 0) != 1:
        errors.append("Reference must be mono.")

    signal = pcm_signal_metrics(reference) if codec == "pcm_s16le" else None
    if signal and signal["clipped_sample_count"]:
        errors.append("Reference contains full-scale clipped PCM samples.")
    if signal and abs(signal["dc_offset_fraction"]) > 0.02:
        errors.append("Reference has excessive DC offset.")

    audio = loudness(reference)
    integrated = audio.get("integrated_loudness_lufs")
    peak = audio.get("true_peak_dbtp")
    if integrated is None or not -30.0 <= integrated <= -12.0:
        errors.append("Reference integrated loudness must stay between -30 and -12 LUFS.")
    if peak is None or peak > -1.0:
        errors.append("Reference true peak must stay at or below -1 dBTP.")

    transcript_metrics, transcript_errors = transcript_evidence(transcript, reference, duration)
    errors.extend(transcript_errors)
    evidence = {
        "duration_seconds": round(duration, 3),
        "stream": {
            "codec": codec or None,
            "sample_rate_hz": sample_rate or None,
            "channels": stream.get("channels"),
        },
        "audio": audio,
        "signal": signal,
        "transcript": transcript_metrics,
    }
    return evidence, errors, warnings


def add_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference", required=True)
    parser.add_argument("--preparation-report", required=True)
    parser.add_argument("--transcript-json", required=True)


def resolved_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    paths = tuple(
        Path(value).expanduser().resolve()
        for value in (args.reference, args.preparation_report, args.transcript_json)
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("Voice-reference evidence not found: " + ", ".join(missing))
    return paths


def preflight(args: argparse.Namespace) -> int:
    reference, preparation_report, transcript = resolved_paths(args)
    evidence, errors, warnings = quality_evidence(reference, preparation_report, transcript)
    result = {
        "schema_version": 1,
        "kind": "voice_reference_preflight",
        "reference_identity": media_identity(reference),
        "preparation_report_identity": media_identity(preparation_report),
        "transcript_identity": media_identity(transcript),
        "quality_evidence": evidence,
        "warnings": warnings,
        "errors": errors,
        "technical_passed": not errors,
        "auditory_review_required": True,
        "upload_ready": False,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["technical_passed"] else 1


def seal(args: argparse.Namespace) -> int:
    reference, preparation_report, transcript = resolved_paths(args)
    evidence, errors, warnings = quality_evidence(reference, preparation_report, transcript)
    verdict = {field: bool(getattr(args, field)) for field in VOICE_REFERENCE_VERDICTS}
    verdict["notes"] = args.notes.strip()
    auditory_review = {
        "reviewer_kind": args.reviewer_kind,
        "reviewer_name": args.reviewer_name.strip(),
        "listened_from_start_to_finish": args.listened_from_start_to_finish,
    }
    for field in VOICE_REFERENCE_VERDICTS:
        if verdict[field] is not True:
            errors.append(f"Voice-reference verdict {field} is not true.")
    if len(verdict["notes"].split()) < 5:
        errors.append("Voice-reference review needs at least five words of concrete listening notes.")
    if not auditory_review["reviewer_name"]:
        errors.append("Voice-reference review needs the name of the actual auditory reviewer.")
    if auditory_review["listened_from_start_to_finish"] is not True:
        errors.append("The auditory reviewer must listen from start to finish before sealing.")
    report = {
        "schema_version": 1,
        "kind": "voice_reference_review",
        "provider_target": "xai_custom_voice",
        "reference_identity": media_identity(reference),
        "preparation_report_identity": media_identity(preparation_report),
        "transcript_identity": media_identity(transcript),
        "quality_evidence": evidence,
        "auditory_review": auditory_review,
        "verdict": verdict,
        "warnings": warnings,
        "errors": errors,
        "passed": not errors,
        "upload_ready": not errors,
        "reviewed_at": now_iso(),
    }
    output = Path(args.output).expanduser().resolve()
    write_json(output, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


def verify(args: argparse.Namespace) -> int:
    reference, preparation_report, transcript = resolved_paths(args)
    review_path = Path(args.review).expanduser().resolve()
    if not review_path.is_file():
        raise SystemExit(f"Voice-reference review not found: {review_path}")
    review = read_json(review_path)
    evidence, errors, warnings = quality_evidence(reference, preparation_report, transcript)
    if review.get("kind") != "voice_reference_review":
        errors.append("Review kind is not voice_reference_review.")
    if review.get("passed") is not True or review.get("upload_ready") is not True:
        errors.append("Voice-reference review is not upload-ready.")
    for field, path in (
        ("reference_identity", reference),
        ("preparation_report_identity", preparation_report),
        ("transcript_identity", transcript),
    ):
        if not identity_matches(review.get(field), path):
            errors.append(f"Voice-reference review has stale {field} evidence.")
    verdict = review.get("verdict") if isinstance(review.get("verdict"), dict) else {}
    auditory_review = (
        review.get("auditory_review") if isinstance(review.get("auditory_review"), dict) else {}
    )
    if auditory_review.get("reviewer_kind") not in REVIEWER_KINDS:
        errors.append("Voice-reference review has no supported auditory reviewer kind.")
    if not str(auditory_review.get("reviewer_name", "")).strip():
        errors.append("Voice-reference review has no auditory reviewer name.")
    if auditory_review.get("listened_from_start_to_finish") is not True:
        errors.append("Voice-reference review does not attest a start-to-finish listen.")
    for field in VOICE_REFERENCE_VERDICTS:
        if verdict.get(field) is not True:
            errors.append(f"Voice-reference review verdict {field} is not true.")
    if len(str(verdict.get("notes", "")).split()) < 5:
        errors.append("Voice-reference review has no concrete listening notes.")
    result = {
        "schema_version": 1,
        "kind": "voice_reference_review_verification",
        "reference_identity": media_identity(reference),
        "review_identity": media_identity(review_path),
        "quality_evidence": evidence,
        "warnings": warnings,
        "errors": errors,
        "passed": not errors,
        "upload_ready": not errors,
        "verified_at": now_iso(),
    }
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seal or verify an exact-byte owner listening/privacy review before xAI upload."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    preflight_parser = sub.add_parser("preflight")
    add_paths(preflight_parser)
    preflight_parser.set_defaults(func=preflight)

    seal_parser = sub.add_parser("seal")
    add_paths(seal_parser)
    for field in VOICE_REFERENCE_VERDICTS:
        seal_parser.add_argument(f"--{field.replace('_', '-')}", action="store_true")
    seal_parser.add_argument("--reviewer-kind", choices=REVIEWER_KINDS, required=True)
    seal_parser.add_argument("--reviewer-name", required=True)
    seal_parser.add_argument("--listened-from-start-to-finish", action="store_true")
    seal_parser.add_argument("--notes", required=True)
    seal_parser.add_argument("--output", required=True)
    seal_parser.set_defaults(func=seal)

    verify_parser = sub.add_parser("verify")
    add_paths(verify_parser)
    verify_parser.add_argument("--review", required=True)
    verify_parser.set_defaults(func=verify)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
