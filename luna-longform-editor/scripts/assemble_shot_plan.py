#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from production_evidence import (
    identity_matches,
    media_identity,
    read_json,
    resolve_media,
    shot_plan_spec_sha256,
    validate_sealed_review,
    validate_shot_plan,
)


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def render_shot(
    shot: dict,
    video: Path,
    voiceover: Path,
    output: Path,
    args: argparse.Namespace,
) -> dict:
    video_duration = probe_duration(video)
    audio_duration = probe_duration(voiceover)
    maximum_recording = float(shot["maximum_recording_seconds"])
    if video_duration > maximum_recording + 0.05:
        raise ValueError(
            f"recording is {video_duration:.2f}s, above the reviewed maximum of {maximum_recording:.2f}s; retake or update the shot plan with a reason."
        )
    target = audio_duration + args.pre_roll + args.post_roll
    rate = video_duration / target
    allow_trim = bool(shot.get("allow_trim", False))
    warnings = []

    if rate > args.max_speed and not allow_trim:
        raise ValueError(
            f"recording is {video_duration:.2f}s but narration needs {target:.2f}s; "
            f"required speed {rate:.2f}x exceeds {args.max_speed:.2f}x. Retake the shot or explicitly allow trim."
        )
    if rate > args.max_speed:
        applied_rate = args.max_speed
        warnings.append("tail trimmed after maximum speed adjustment")
    elif rate < args.min_speed:
        applied_rate = args.min_speed
        warnings.append("last frame held because the recording is shorter than narration")
    else:
        applied_rate = rate

    width, height = [int(part) for part in args.resolution.lower().split("x", 1)]
    delay_ms = int(round(args.pre_roll * 1000))
    video_filter = (
        f"[0:v]setpts=PTS/{applied_rate:.8f},"
        f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"tpad=stop_mode=clone:stop_duration={target:.6f},"
        f"trim=duration={target:.6f},setpts=PTS-STARTPTS,fps={args.fps},format=yuv420p[v];"
        f"[1:a]adelay=delays={delay_ms}:all=1,apad,atrim=duration={target:.6f},"
        "loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(voiceover),
            "-filter_complex",
            video_filter,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            args.preset,
            "-crf",
            str(args.crf),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )
    return {
        "video_duration": round(video_duration, 3),
        "voiceover_duration": round(audio_duration, 3),
        "output_duration": round(target, 3),
        "requested_rate": round(rate, 4),
        "applied_rate": round(applied_rate, 4),
        "warnings": warnings,
    }


def concatenate(parts: list[Path], output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="luna-shot-concat-") as directory:
        concat_file = Path(directory) / "parts.txt"
        concat_file.write_text(
            "\n".join(f"file '{part.as_posix()}'" for part in parts) + "\n",
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
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output),
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble desktop shots against per-shot cloned narration.")
    parser.add_argument("--shot-plan", required=True)
    parser.add_argument("--project")
    parser.add_argument("--voice-audit-report")
    parser.add_argument("--voiceover-dir")
    parser.add_argument("--recording-review-dir")
    parser.add_argument("--voice-review-dir")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--zoom-plan-output")
    parser.add_argument("--work-dir")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--resolution", default="2560x1440")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--pre-roll", type=float, default=0.12)
    parser.add_argument("--post-roll", type=float, default=0.22)
    parser.add_argument("--min-speed", type=float, default=0.90)
    parser.add_argument("--max-speed", type=float, default=1.15)
    parser.add_argument("--preset", default="veryfast")
    parser.add_argument("--crf", type=int, default=18)
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required.")
    plan_path = Path(args.shot_plan).expanduser().resolve()
    base = plan_path.parent
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    shots = plan.get("shots", [])
    if not shots:
        raise SystemExit("Shot plan contains no shots.")
    voiceover_dir = Path(args.voiceover_dir).expanduser().resolve() if args.voiceover_dir else None
    output = Path(args.output).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    errors = []
    resolved = []
    inferred_project = plan_path.parent.parent / "project.json"
    project_path = Path(args.project).expanduser().resolve() if args.project else inferred_project
    project = json.loads(project_path.read_text(encoding="utf-8")) if project_path.is_file() else {}
    synthetic_mode = str(project.get("mode", "synthetic")).lower() != "edit"
    plan_validation = validate_shot_plan(plan, project)
    errors.extend(plan_validation["errors"])
    roles = set(plan_validation["story_roles"])
    job_root = plan_path.parent.parent
    recording_review_dir = (
        Path(args.recording_review_dir).expanduser().resolve()
        if args.recording_review_dir
        else job_root / "qa" / "reviews" / "recording"
    )
    voice_review_dir = (
        Path(args.voice_review_dir).expanduser().resolve()
        if args.voice_review_dir
        else job_root / "qa" / "reviews" / "voice"
    )
    inferred_voice_audit = plan_path.parent.parent / "qa" / "voiceover_audit.json"
    voice_audit_path = (
        Path(args.voice_audit_report).expanduser().resolve()
        if args.voice_audit_report
        else inferred_voice_audit
    )
    voice_audit = read_json(voice_audit_path) if voice_audit_path.is_file() else {}
    if synthetic_mode and voice_audit.get("passed") is not True:
        errors.append("Synthetic assembly requires a passing voiceover audit report.")
    if synthetic_mode and voice_audit.get("shot_plan_spec_sha256") != shot_plan_spec_sha256(plan):
        errors.append("Voiceover audit does not match the current immutable shot specification.")
    audit_by_shot = {item.get("id"): item for item in voice_audit.get("shots", [])}
    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.get("id", f"shot-{index:03d}"))
        video = resolve_media(shot.get("video"), base)
        fallback_voice = voiceover_dir / f"{shot_id}.wav" if voiceover_dir else None
        voiceover = resolve_media(shot.get("voiceover"), base, fallback_voice)
        if video is None or not video.is_file():
            errors.append(f"{shot_id} recording not found: {video}")
        if voiceover is None or not voiceover.is_file():
            errors.append(f"{shot_id} voiceover not found: {voiceover}")
        if synthetic_mode and video is not None and video.is_file():
            review_path = recording_review_dir / f"{shot_id}.json"
            review = read_json(review_path) if review_path.is_file() else None
            errors.extend(validate_sealed_review(review, "recording", shot, video))
        if synthetic_mode and voiceover is not None and voiceover.is_file():
            review_path = voice_review_dir / f"{shot_id}.json"
            review = read_json(review_path) if review_path.is_file() else None
            errors.extend(validate_sealed_review(review, "voice", shot, voiceover))
            shot_audit = audit_by_shot.get(shot_id, {})
            if shot_audit.get("passed") is not True:
                errors.append(f"{shot_id} has no passing transcript voice audit entry.")
            if not identity_matches(shot_audit.get("voiceover_identity"), voiceover):
                errors.append(f"{shot_id} transcript voice audit does not match the current audio bytes.")
        resolved.append((shot_id, shot, video, voiceover))

    report = {
        "schema_version": 1,
        "shot_plan": str(plan_path),
        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
        "project": str(project_path) if project else None,
        "project_identity": media_identity(project_path) if project else None,
        "voice_audit_report": str(voice_audit_path) if voice_audit else None,
        "recording_review_dir": str(recording_review_dir),
        "voice_review_dir": str(voice_review_dir),
        "passed": False,
        "errors": errors,
        "story_roles": sorted(role for role in roles if role),
        "shots": [],
    }
    if errors or args.validate_only:
        report["passed"] = not errors
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1

    temporary = None
    if args.work_dir:
        work_dir = Path(args.work_dir).expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="luna-shot-assembly-")
        work_dir = Path(temporary.name)

    parts = []
    zooms = []
    timeline = 0.0
    try:
        for index, (shot_id, shot, video, voiceover) in enumerate(resolved, start=1):
            part = work_dir / f"{index:03d}_{shot_id}.mp4"
            try:
                metrics = render_shot(shot, video, voiceover, part, args)
            except ValueError as error:
                report["errors"].append(f"{shot_id}: {error}")
                continue
            parts.append(part)
            report["shots"].append({"id": shot_id, "video": str(video), "voiceover": str(voiceover), **metrics})
            duration = metrics["output_duration"]
            if shot.get("target_box"):
                zooms.append(
                    {
                        "start": round(timeline, 3),
                        "end": round(timeline + duration, 3),
                        "target_box": shot["target_box"],
                        "include_boxes": shot.get("include_boxes", []),
                        "zoom": float(shot.get("zoom", 1.10)),
                        "transition": float(shot.get("zoom_transition", 0.45)),
                        "label": shot_id,
                    }
                )
            timeline += duration

        if report["errors"]:
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2))
            return 1
        concatenate(parts, output)
        report["passed"] = True
        report["output"] = str(output)
        report["output_identity"] = media_identity(output)
        report["duration_seconds"] = round(probe_duration(output), 3)
        zoom_path = (
            Path(args.zoom_plan_output).expanduser().resolve()
            if args.zoom_plan_output
            else report_path.with_name("synthetic_focus_zoom.json")
        )
        zoom_path.write_text(json.dumps({"zooms": zooms}, indent=2) + "\n", encoding="utf-8")
        report["zoom_plan"] = str(zoom_path)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(output)
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
