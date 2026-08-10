#!/usr/bin/env python3
import argparse
import json
import re
import statistics
import subprocess
from pathlib import Path

from creator_fidelity import SIGNATURE_PHRASES, transcript_style
from production_evidence import sha256_file


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,r_frame_rate",
            "-show_entries",
            "format=duration,size,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def scene_cut_count(path: Path, threshold: float) -> int:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    return sum(1 for line in result.stderr.splitlines() if "showinfo" in line and "pts_time:" in line)


def integrated_loudness(path: Path) -> float | None:
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
    matches = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s+LUFS", result.stderr)
    return float(matches[-1]) if matches else None


def transcript_metrics(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    words = []
    segments = data.get("segments", [])
    for segment in segments:
        for raw in segment.get("words", []):
            if raw.get("start") is None or raw.get("end") is None:
                continue
            words.append(
                {
                    "start": float(raw["start"]),
                    "end": float(raw["end"]),
                    "word": str(raw.get("word", "")).strip(),
                }
            )
    duration = float(data.get("duration") or (words[-1]["end"] if words else 0.0))
    gaps = [max(0.0, current["start"] - previous["end"]) for previous, current in zip(words, words[1:])]
    positive_gaps = [gap for gap in gaps if gap >= 0.02]
    transcript_text = " ".join(str(segment.get("text", "")).strip() for segment in segments)
    intro_end = None
    for segment in segments:
        if float(segment["start"]) < 2.0:
            continue
        if re.search(r"\b(all right|alright) guys\b|\bto start (the )?tutorial\b", str(segment.get("text", "")), re.I):
            intro_end = float(segment["start"])
            break
    return {
        "word_count": len(words),
        "words_per_minute": round(len(words) / (duration / 60), 2) if duration else None,
        "positive_gap_median": round(statistics.median(positive_gaps), 3) if positive_gaps else None,
        "positive_gap_p90": round(percentile(positive_gaps, 0.90), 3) if positive_gaps else None,
        "positive_gap_p95": round(percentile(positive_gaps, 0.95), 3) if positive_gaps else None,
        "intro_end_seconds": intro_end,
        "ending_excerpt": transcript_text[-500:],
        "creator_style": transcript_style(data),
    }


def parse_transcript_map(values: list[str]) -> dict[Path, Path]:
    mapping = {}
    for value in values:
        if "=" not in value:
            raise SystemExit("--transcript must be VIDEO=TRANSCRIPT_JSON")
        video, transcript = value.split("=", 1)
        mapping[Path(video).expanduser().resolve()] = Path(transcript).expanduser().resolve()
    return mapping


def median_metric(examples: list[dict], key: str) -> float | None:
    values = [float(example[key]) for example in examples if example.get(key) is not None]
    return round(statistics.median(values), 3) if values else None


def nested_median(examples: list[dict], *keys: str) -> float | None:
    values = []
    for example in examples:
        value = example
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            values.append(float(value))
    return round(statistics.median(values), 4) if values else None


def stored_measurements(examples: list[dict]) -> list[dict]:
    stored = json.loads(json.dumps(examples))
    for example in stored:
        example.pop("ending_excerpt", None)
        style = example.get("creator_style", {})
        style.pop("opening_excerpt", None)
        style.pop("closing_excerpt", None)
        sections = style.get("sections", {})
        for key in list(sections):
            if key.endswith("_excerpt"):
                sections.pop(key, None)
    return stored


def main() -> int:
    parser = argparse.ArgumentParser(description="Learn a measurable Luna channel profile from finished videos.")
    parser.add_argument("--video", action="append", required=True)
    parser.add_argument("--transcript", action="append", default=[])
    parser.add_argument("--raw-pair", action="append", default=[], help="RAW_VIDEO=FINAL_VIDEO")
    parser.add_argument("--scene-threshold", type=float, default=0.30)
    parser.add_argument(
        "--base-profile",
        help="Profile whose direct-feedback rules and quality guardrails should be preserved.",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    transcript_map = parse_transcript_map(args.transcript)
    videos = [Path(value).expanduser().resolve() for value in args.video]
    for video in videos:
        if not video.is_file():
            raise SystemExit(f"Finished video not found: {video}")
    examples = []
    for video in videos:
        technical = probe(video)
        duration = float(technical.get("format", {}).get("duration", 0.0))
        cuts = scene_cut_count(video, args.scene_threshold)
        entry = {
            "video": video.name,
            "video_sha256": sha256_file(video),
            "duration_seconds": round(duration, 3),
            "scene_change_count": cuts,
            "scene_changes_per_minute": round(cuts / (duration / 60), 3) if duration else None,
            "integrated_loudness_lufs": integrated_loudness(video),
            "technical": technical,
        }
        transcript = transcript_map.get(video)
        if transcript:
            if not transcript.is_file():
                raise SystemExit(f"Transcript not found: {transcript}")
            entry.update(transcript_metrics(transcript))
        examples.append(entry)

    compression_ratios = []
    for raw_pair in args.raw_pair:
        if "=" not in raw_pair:
            raise SystemExit("--raw-pair must be RAW_VIDEO=FINAL_VIDEO")
        raw_value, final_value = raw_pair.split("=", 1)
        raw_duration = float(probe(Path(raw_value).expanduser().resolve())["format"]["duration"])
        final_duration = float(probe(Path(final_value).expanduser().resolve())["format"]["duration"])
        compression_ratios.append(final_duration / raw_duration)

    confidence = "high" if len(examples) >= 8 else "medium" if len(examples) >= 3 else "low"
    default_base = Path(__file__).resolve().parent.parent / "channel_profile.json"
    base_path = Path(args.base_profile).expanduser().resolve() if args.base_profile else default_base
    profile = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else {}
    observed_loudness = median_metric(examples, "integrated_loudness_lufs")
    warnings = []
    if observed_loudness is not None and not -20.0 <= observed_loudness <= -12.0:
        warnings.append(
            f"Observed loudness {observed_loudness:.1f} LUFS is outside the delivery guardrail and was not adopted as a target."
        )
    if any(example.get("positive_gap_p90") is None for example in examples):
        warnings.append("At least one transcript has no reliable positive word-gap sample.")

    phrase_counts = {
        phrase: sum(
            int(example.get("creator_style", {}).get("signature_phrase_counts", {}).get(phrase, 0))
            for example in examples
        )
        for phrase in SIGNATURE_PHRASES
    }
    observed_phrases = {
        phrase: count
        for phrase, count in phrase_counts.items()
        if count
    }
    exemplars = []
    for example in examples:
        sections = example.get("creator_style", {}).get("sections", {})
        exemplars.append(
            {
                "video": example["video"],
                "hook": sections.get("hook_excerpt", ""),
                "tutorial_transition": sections.get("transition_excerpt", ""),
                "cta_opening": sections.get("cta_opening_excerpt", ""),
                "signoff": sections.get("signoff_excerpt", ""),
            }
        )

    profile.update(
        {
            "schema_version": max(2, int(profile.get("schema_version", 1))),
            "channel": "Luna Tweak",
            "quantitative_confidence": confidence,
            "accepted_tutorial_examples": len(examples),
            "learned_examples": stored_measurements(examples),
            "learned_measurements": {
                "duration_seconds_median": median_metric(examples, "duration_seconds"),
                "scene_changes_per_minute_median": median_metric(examples, "scene_changes_per_minute"),
                "words_per_minute_median": median_metric(examples, "words_per_minute"),
                "positive_speech_gap_p90_median": median_metric(examples, "positive_gap_p90"),
                "intro_end_seconds_median": median_metric(examples, "intro_end_seconds"),
                "raw_to_final_duration_ratio_median": round(statistics.median(compression_ratios), 3) if compression_ratios else None,
                "observed_integrated_loudness_lufs_median": observed_loudness,
            },
            "measurement_warnings": warnings,
            "creator_fingerprint": {
                "confidence": confidence,
                "policy": (
                    "At low confidence, exemplars and numeric measurements guide review but do not require copied wording. "
                    "At medium/high confidence, generated work must remain inside broad learned ranges unless the topic requires a documented exception."
                ),
                "linguistic_medians": {
                    key: nested_median(examples, "creator_style", key)
                    for key in (
                        "unique_word_ratio",
                        "average_unit_words",
                        "viewer_address_per_100_words",
                        "first_person_per_100_words",
                        "action_words_per_100_words",
                        "transition_words_per_100_words",
                        "filler_words_per_100_words",
                        "contractions_per_100_words",
                        "action_unit_fraction",
                    )
                },
                "section_medians": {
                    key: nested_median(examples, "creator_style", "sections", key)
                    for key in (
                        "tutorial_transition_seconds",
                        "cta_start_seconds",
                        "hook_duration_fraction",
                        "tutorial_duration_fraction",
                        "cta_duration_fraction",
                    )
                },
                "observed_signature_phrases": observed_phrases,
                "accepted_exemplars": exemplars,
            },
            "usage": (
                "Use learned measurements as ranges, not rigid goals, and only at medium or high confidence. "
                "Direct feedback, content clarity, visible proof, and delivery guardrails take priority."
            ),
        }
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
