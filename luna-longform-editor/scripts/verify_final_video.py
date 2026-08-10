#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def run_capture(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)


def load_json(path: Path | None) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path else {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def plan_gate(report: dict) -> dict:
    errors = []
    if report.get("passed") is not True:
        errors.append("Validated edit/assembly plan report is missing or not passing.")
        return {"passed": False, "errors": errors}
    raw_path = report.get("plan") or report.get("shot_plan")
    expected_hash = report.get("plan_sha256") or report.get("shot_plan_sha256")
    if not raw_path or not expected_hash:
        errors.append("Plan report is missing its plan path or SHA-256 identity.")
        return {"passed": False, "errors": errors}
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        errors.append(f"Validated plan no longer exists: {path}")
    elif sha256_file(path) != expected_hash:
        errors.append("Validated plan changed after its report was created.")
    return {"passed": not errors, "errors": errors, "plan": str(path), "plan_sha256": expected_hash}


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower())


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
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


def transcript_gate(path: Path | None, max_gap: float, completed_gap_review: dict | None) -> dict:
    if path is None:
        return {"passed": False, "errors": ["Rendered transcript is missing."], "warnings": []}
    data = load_json(path)
    words = []
    for segment in data.get("segments", []):
        for word in segment.get("words", []):
            if word.get("start") is None or word.get("end") is None:
                continue
            token = str(word.get("word", "")).strip()
            words.append(
                {
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "word": token,
                    "norm": normalize(token),
                }
            )
    if not words:
        return {"passed": False, "errors": ["Rendered transcript has no word timings."], "warnings": []}

    errors = []
    gap_candidates = []
    for previous, current in zip(words, words[1:]):
        gap = current["start"] - previous["end"]
        if gap > max_gap:
            gap_candidates.append(
                {
                    "id": f"gap-{len(gap_candidates) + 1:03d}",
                    "start": round(previous["end"], 3),
                    "end": round(current["start"], 3),
                    "duration": round(gap, 3),
                    "before": previous["word"],
                    "after": current["word"],
                    "justified": None,
                    "reason": "",
                }
            )
        if previous["norm"] and previous["norm"] == current["norm"]:
            errors.append(
                f"adjacent repeated word at {previous['start']:.3f}: "
                f"{previous['word']} {current['word']}"
            )
    tokens = [word["norm"] for word in words]
    for size in range(2, 6):
        for index in range(len(tokens) - size * 2 + 1):
            if tokens[index : index + size] == tokens[index + size : index + size * 2]:
                errors.append(
                    f"repeated {size}-word phrase at {words[index]['start']:.3f}: "
                    + " ".join(word["word"] for word in words[index : index + size])
                )

    if gap_candidates:
        if completed_gap_review is None:
            errors.append(f"{len(gap_candidates)} speech gap(s) require visual/context review.")
        else:
            completed = {
                str(item.get("id")): item
                for item in completed_gap_review.get("gaps", [])
                if item.get("id")
            }
            for candidate in gap_candidates:
                reviewed = completed.get(candidate["id"])
                if not reviewed:
                    errors.append(f"Speech review is missing {candidate['id']}.")
                    continue
                try:
                    timestamps_match = (
                        abs(float(reviewed["start"]) - candidate["start"]) <= 0.02
                        and abs(float(reviewed["end"]) - candidate["end"]) <= 0.02
                    )
                except (KeyError, TypeError, ValueError):
                    timestamps_match = False
                if not timestamps_match:
                    errors.append(f"{candidate['id']} review timestamps do not match the current render.")
                elif reviewed.get("justified") is not True or not str(reviewed.get("reason", "")).strip():
                    errors.append(f"{candidate['id']} is not explicitly justified with a reason.")
    return {
        "passed": not errors,
        "errors": sorted(set(errors)),
        "warnings": [],
        "gap_review_template": {"maximum_gap_seconds": max_gap, "gaps": gap_candidates},
    }


def analyze_media(path: Path) -> dict:
    decode = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    black = run_capture(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=1.5:pix_th=0.02",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    silence = run_capture(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "silencedetect=n=-45dB:d=1.2",
            "-vn",
            "-f",
            "null",
            "-",
        ]
    )
    black_segments = [line.strip() for line in black.stderr.splitlines() if "black_" in line]
    silence_events = [line.strip() for line in silence.stderr.splitlines() if "silence_" in line]
    return {
        "passed": decode.returncode == 0,
        "decode_errors": decode.stderr.strip().splitlines(),
        "black_events": black_segments,
        "silence_events": silence_events,
    }


def loudness_gate(path: Path, minimum: float, maximum: float, true_peak_maximum: float) -> dict:
    result = run_capture(
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
        ]
    )
    integrated_matches = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s+LUFS", result.stderr)
    peak_matches = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s+dBFS", result.stderr)
    integrated = float(integrated_matches[-1]) if integrated_matches else None
    true_peak = float(peak_matches[-1]) if peak_matches else None
    errors = []
    if integrated is None:
        errors.append("Integrated loudness could not be measured.")
    elif integrated < minimum:
        errors.append(f"Integrated loudness {integrated:.1f} LUFS is below {minimum:.1f} LUFS.")
    elif integrated > maximum:
        errors.append(f"Integrated loudness {integrated:.1f} LUFS exceeds {maximum:.1f} LUFS.")
    if true_peak is None:
        errors.append("True peak could not be measured.")
    elif true_peak > true_peak_maximum:
        errors.append(f"True peak {true_peak:.1f} dBFS exceeds {true_peak_maximum:.1f} dBFS.")
    return {
        "passed": not errors,
        "errors": errors,
        "integrated_loudness_lufs": integrated,
        "true_peak_dbtp": true_peak,
        "accepted_loudness_range_lufs": [minimum, maximum],
        "true_peak_maximum_dbtp": true_peak_maximum,
    }


def extract_frame(video: Path, time_value: float, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{time_value:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "format=rgb24",
            str(output),
        ],
        check=True,
    )


def prepare_visual_evidence(video: Path, zoom_plan: dict, output_dir: Path, duration: float) -> dict:
    frame_dir = output_dir / "visual_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.jpg", "*.png"):
        for old in frame_dir.glob(pattern):
            old.unlink()
    regions = []
    zooms = zoom_plan.get("zooms", [])
    for index, zoom in enumerate(zooms, start=1):
        start = max(0.0, float(zoom["start"]))
        end = min(duration, float(zoom["end"]))
        sample_times = sorted(
            {
                max(start, min(end, start + 0.20)),
                (start + end) / 2,
                max(start, min(end, end - 0.20)),
            }
        )
        paths = []
        for sample_index, time_value in enumerate(sample_times, start=1):
            path = frame_dir / f"zoom_{index:03d}_{sample_index}_{time_value:.2f}.png"
            extract_frame(video, time_value, path)
            paths.append(str(path))
        regions.append(
            {
                "label": str(zoom.get("label", f"zoom-{index}")),
                "start": start,
                "end": end,
                "frames": paths,
                "passed": None,
                "target_visible": None,
                "required_context_visible": None,
                "notes": "",
            }
        )
    timeline_times = [duration * fraction for fraction in (0.02, 0.15, 0.35, 0.55, 0.75, 0.92, 0.98)]
    timeline_frames = []
    for index, time_value in enumerate(timeline_times, start=1):
        path = frame_dir / f"timeline_{index:02d}_{time_value:.2f}.png"
        extract_frame(video, time_value, path)
        timeline_frames.append(
            {
                "id": f"timeline-{index:02d}",
                "time": round(time_value, 3),
                "frame": str(path),
                "passed": None,
                "ui_readable": None,
                "narration_context_consistent": None,
                "private_information_clear": None,
                "notes": "",
            }
        )
    return {
        "overall": {
            "story_continuity": None,
            "ui_readability": None,
            "claims_match_visuals": None,
            "no_private_information": None,
            "notes": "",
        },
        "zoom_regions": regions,
        "timeline_frames": timeline_frames,
    }


def visual_gate(template: dict, completed: dict | None) -> dict:
    if completed is None:
        return {
            "passed": False,
            "errors": ["Visual review has not been completed."],
            "review_template": template,
        }
    errors = []
    overall = completed.get("overall", {})
    for field in ("story_continuity", "ui_readability", "claims_match_visuals", "no_private_information"):
        if overall.get(field) is not True:
            errors.append(f"Visual review overall.{field} is not true.")
    expected_labels = {str(region["label"]) for region in template.get("zoom_regions", [])}
    completed_regions = {str(region.get("label")): region for region in completed.get("zoom_regions", [])}
    for label in sorted(expected_labels):
        region = completed_regions.get(label)
        if not region:
            errors.append(f"Visual review is missing zoom region '{label}'.")
            continue
        for field in ("passed", "target_visible", "required_context_visible"):
            if region.get(field) is not True:
                errors.append(f"Zoom region '{label}' has {field} != true.")
    expected_frames = {str(frame["id"]): frame for frame in template.get("timeline_frames", [])}
    completed_frames = {
        str(frame.get("id")): frame
        for frame in completed.get("timeline_frames", [])
        if isinstance(frame, dict) and frame.get("id")
    }
    for frame_id, expected in expected_frames.items():
        frame = completed_frames.get(frame_id)
        if not frame:
            errors.append(f"Visual review is missing timeline frame '{frame_id}'.")
            continue
        try:
            time_matches = abs(float(frame["time"]) - float(expected["time"])) <= 0.02
        except (KeyError, TypeError, ValueError):
            time_matches = False
        if not time_matches:
            errors.append(f"Timeline frame '{frame_id}' timestamp does not match the current render.")
        for field in ("passed", "ui_readable", "narration_context_consistent", "private_information_clear"):
            if frame.get(field) is not True:
                errors.append(f"Timeline frame '{frame_id}' has {field} != true.")
    return {"passed": not errors, "errors": errors, "review": completed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed final acceptance audit for Luna videos.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--transcript-json")
    parser.add_argument("--plan-report")
    parser.add_argument("--zoom-plan")
    parser.add_argument("--visual-review")
    parser.add_argument("--speech-gap-review")
    parser.add_argument("--max-speech-gap", type=float, default=0.62)
    parser.add_argument("--minimum-lufs", type=float, default=-20.0)
    parser.add_argument("--maximum-lufs", type=float, default=-12.0)
    parser.add_argument("--maximum-true-peak", type=float, default=-1.0)
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required.")
    video = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not video.is_file():
        raise SystemExit(f"Final candidate not found: {video}")

    technical = probe(video)
    duration = float(technical.get("format", {}).get("duration", 0.0))
    streams = technical.get("streams", [])
    technical_errors = []
    if not any(stream.get("codec_type") == "video" for stream in streams):
        technical_errors.append("No video stream.")
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        technical_errors.append("No audio stream.")
    if duration <= 0.5:
        technical_errors.append("Duration is too short.")
    media = analyze_media(video)
    technical_errors.extend(media["decode_errors"] if not media["passed"] else [])
    audio = loudness_gate(video, args.minimum_lufs, args.maximum_lufs, args.maximum_true_peak)

    transcript_path = Path(args.transcript_json).expanduser().resolve() if args.transcript_json else None
    completed_gap_review = (
        load_json(Path(args.speech_gap_review).expanduser().resolve())
        if args.speech_gap_review
        else None
    )
    speech = transcript_gate(transcript_path, args.max_speech_gap, completed_gap_review)
    gap_template_path = output_dir / "speech_gap_review_template.json"
    write_json(gap_template_path, speech.get("gap_review_template", {"gaps": []}))
    plan_report = load_json(Path(args.plan_report).expanduser().resolve()) if args.plan_report else {}
    validated_plan = plan_gate(plan_report)

    zoom_plan = load_json(Path(args.zoom_plan).expanduser().resolve()) if args.zoom_plan else {"zooms": []}
    visual_template = prepare_visual_evidence(video, zoom_plan, output_dir, duration)
    template_path = output_dir / "visual_review_template.json"
    write_json(template_path, visual_template)
    completed_visual = load_json(Path(args.visual_review).expanduser().resolve()) if args.visual_review else None
    visuals = visual_gate(visual_template, completed_visual)

    gates = {
        "technical": {
            "passed": not technical_errors,
            "errors": technical_errors,
            "probe": technical,
            "media_analysis": media,
        },
        "audio": audio,
        "plan": validated_plan,
        "speech": speech,
        "visual": visuals,
    }
    passed = all(gate.get("passed") is True for gate in gates.values())
    report = {
        "schema_version": 1,
        "input": str(video),
        "media_identity": file_identity(video),
        "passed": passed,
        "gates": gates,
        "visual_review_template": str(template_path),
        "speech_gap_review_template": str(gap_template_path),
    }
    report_path = output_dir / "final_qa_report.json"
    write_json(report_path, report)
    print(
        json.dumps(
            {
                "passed": passed,
                "report": str(report_path),
                "visual_review_template": str(template_path),
                "speech_gap_review_template": str(gap_template_path),
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
