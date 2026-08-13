#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from production_evidence import (
    media_identity,
    probe_duration,
    read_json,
    transcript_source_errors,
    write_json,
)


def transcript_words(path: Path | None) -> tuple[list[dict], list[dict]]:
    if path is None:
        return [], []
    data = read_json(path)
    segments = data.get("segments", [])
    words = []
    for segment in segments:
        for raw in segment.get("words", []):
            if raw.get("start") is None or raw.get("end") is None:
                continue
            words.append(
                {
                    "start": float(raw["start"]),
                    "end": float(raw["end"]),
                    "word": str(raw.get("word", "")).strip().lower(),
                    "probability": raw.get("probability"),
                }
            )
    return segments, words


def score_window(start: float, end: float, words: list[dict]) -> dict:
    selected = [word for word in words if word["start"] >= start and word["end"] <= end]
    duration = end - start
    spoken = sum(max(0.0, word["end"] - word["start"]) for word in selected)
    probabilities = [float(word["probability"]) for word in selected if word.get("probability") is not None]
    mean_probability = sum(probabilities) / len(probabilities) if probabilities else 0.0
    normalized = [re.sub(r"[^a-z0-9']+", "", word["word"]) for word in selected]
    normalized = [word for word in normalized if word]
    lexical_diversity = len(set(normalized)) / len(normalized) if normalized else 0.0
    speech_occupancy = spoken / duration if duration else 0.0
    score = speech_occupancy * 3.0 + mean_probability + lexical_diversity * 0.35
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "word_count": len(selected),
        "speech_occupancy": round(speech_occupancy, 4),
        "mean_word_probability": round(mean_probability, 4),
        "lexical_diversity": round(lexical_diversity, 4),
        "score": round(score, 4),
    }


def complete_segment_end(start: float, proposed_end: float, minimum: float, segments: list[dict]) -> float:
    eligible = [
        float(segment["end"])
        for segment in segments
        if start + minimum <= float(segment.get("end", 0.0)) <= proposed_end
    ]
    return max(eligible) if eligible else proposed_end


def choose_window(
    duration: float,
    target: float,
    minimum: float,
    segments: list[dict],
    words: list[dict],
    explicit_start: float | None,
) -> dict:
    target = min(target, duration)
    if target < minimum:
        raise SystemExit(f"Source is only {duration:.1f}s; xAI reference target requires at least {minimum:.1f}s.")
    if explicit_start is not None:
        if explicit_start < 0 or explicit_start + minimum > duration:
            raise SystemExit("--start-seconds does not leave enough source audio for the minimum reference length.")
        end = complete_segment_end(
            explicit_start,
            min(duration, explicit_start + target),
            minimum,
            segments,
        )
        return score_window(explicit_start, end, words) if words else {
            "start": round(explicit_start, 3),
            "end": round(end, 3),
            "duration": round(end - explicit_start, 3),
            "word_count": None,
            "speech_occupancy": None,
            "mean_word_probability": None,
            "lexical_diversity": None,
            "score": None,
        }
    if not words:
        return {
            "start": 0.0,
            "end": round(target, 3),
            "duration": round(target, 3),
            "word_count": None,
            "speech_occupancy": None,
            "mean_word_probability": None,
            "lexical_diversity": None,
            "score": None,
        }
    starts = {0.0}
    starts.update(float(segment.get("start", 0.0)) for segment in segments)
    candidates = []
    for start in sorted(starts):
        end = complete_segment_end(start, min(duration, start + target), minimum, segments)
        if end - start < minimum:
            continue
        candidates.append(score_window(start, end, words))
    if not candidates:
        raise SystemExit("No transcript-aligned voice-reference window satisfies the requested duration.")
    return max(candidates, key=lambda item: (item["score"], item["word_count"], -item["start"]))


def loudness(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true",
            "-vn",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    integrated = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s+LUFS", result.stderr)
    peak = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s+dBFS", result.stderr)
    return {
        "integrated_loudness_lufs": float(integrated[-1]) if integrated else None,
        "true_peak_dbtp": float(peak[-1]) if peak else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare an owner-consented 90-120 second xAI custom-voice reference from an accepted video."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--transcript-json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--target-seconds", type=float, default=110.0)
    parser.add_argument("--minimum-seconds", type=float, default=90.0)
    parser.add_argument("--maximum-seconds", type=float, default=118.0)
    parser.add_argument("--start-seconds", type=float)
    parser.add_argument("--owner-consent-confirmed", action="store_true")
    args = parser.parse_args()

    if not args.owner_consent_confirmed:
        raise SystemExit("Preparing a voice clone reference requires --owner-consent-confirmed.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required.")
    source = Path(args.input).expanduser().resolve()
    transcript = Path(args.transcript_json).expanduser().resolve() if args.transcript_json else None
    output = Path(args.output).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Input not found: {source}")
    if transcript is not None and not transcript.is_file():
        raise SystemExit(f"Transcript not found: {transcript}")
    if transcript is not None:
        provenance_errors = transcript_source_errors(transcript, source, require_identity=True)
        if provenance_errors:
            raise SystemExit(
                "Voice-reference selection transcript is stale or unbound: "
                + " ".join(provenance_errors)
            )
    if not 90.0 <= args.minimum_seconds <= args.maximum_seconds <= 120.0:
        raise SystemExit("Reference duration limits must stay within xAI's 90-120 second quality target.")
    target = min(args.target_seconds, args.maximum_seconds)
    segments, words = transcript_words(transcript)
    selection = choose_window(
        probe_duration(source),
        target,
        args.minimum_seconds,
        segments,
        words,
        args.start_seconds,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(selection["start"]),
            "-i",
            str(source),
            "-t",
            str(selection["duration"]),
            "-map",
            "0:a:0",
            "-af",
            "highpass=f=70,lowpass=f=16000,loudnorm=I=-18:TP=-2:LRA=7",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )
    report = {
        "schema_version": 2,
        "provider_target": "xai_custom_voice",
        "owner_consent_confirmed": True,
        "source_identity": media_identity(source),
        "transcript": str(transcript) if transcript else None,
        "source_transcript_identity": media_identity(transcript) if transcript else None,
        "selection": selection,
        "output_identity": media_identity(output),
        "output_duration_seconds": round(probe_duration(output), 3),
        "output_audio_profile": {
            "container": "wav",
            "codec": "pcm_s16le",
            "sample_rate_hz": 24000,
            "channels": 1,
        },
        "audio": loudness(output),
        "manual_listening_review_required": True,
        "upload_ready": False,
        "review_requirements": [
            "Only Colin speaks.",
            "No music, notification sounds, or other background audio is audible.",
            "Delivery is natural and representative of Luna tutorials.",
            "No clipped words or abrupt edit artifacts are audible.",
            "No private spoken information is present.",
            "Transcribe the exact prepared WAV and seal an exact-byte review before upload.",
        ],
    }
    write_json(report_path, report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
