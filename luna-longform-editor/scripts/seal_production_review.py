#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from audit_voice_delivery import DEFAULT_MODEL, voice_delivery_audit_status
from production_evidence import (
    VOICE_VERDICTS,
    identity_matches,
    media_identity,
    narration_sha256,
    probe_duration,
    read_json,
    resolve_media,
    sha256_file,
    shot_spec_sha256,
    validate_sealed_review,
    write_json,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_shot(plan_path: Path, shot_id: str) -> tuple[dict, dict]:
    plan = read_json(plan_path)
    matches = [shot for shot in plan.get("shots", []) if str(shot.get("id")) == shot_id]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one shot named {shot_id}; found {len(matches)}.")
    return plan, matches[0]


def resolve_shot_media(shot: dict, plan_path: Path, explicit: str | None, field: str) -> Path:
    value = explicit or shot.get(field)
    path = resolve_media(str(value) if value else None, plan_path.parent)
    if path is None or not path.is_file():
        raise SystemExit(f"{field} media not found: {path}")
    return path


def extract_evidence_frame(video: Path, time_value: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
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
            "scale=1280:-2:force_original_aspect_ratio=decrease",
            str(output),
        ],
        check=True,
    )


def seal_recording(args: argparse.Namespace) -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required.")
    plan_path = Path(args.shot_plan).expanduser().resolve()
    _, shot = load_shot(plan_path, args.shot_id)
    video = resolve_shot_media(shot, plan_path, args.video, "video")
    duration = probe_duration(video)
    times = sorted(set(round(float(value), 3) for value in args.evidence_time))
    if not times:
        raise SystemExit("Provide at least one --evidence-time after inspecting the recording.")
    if any(value < 0 or value > duration for value in times):
        raise SystemExit(f"Evidence times must be between 0 and {duration:.3f} seconds.")
    output = Path(args.output).expanduser().resolve()
    frame_dir = output.parent.parent / "review_frames" / args.shot_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    for old in frame_dir.glob("*.png"):
        old.unlink()
    evidence = []
    for index, time_value in enumerate(times, start=1):
        frame = frame_dir / f"{index:02d}_{time_value:.3f}.png"
        extract_evidence_frame(video, time_value, frame)
        evidence.append(
            {
                "time": time_value,
                "frame": str(frame),
                "frame_sha256": sha256_file(frame),
            }
        )
    verdict = {
        "required_visual_state_visible": args.required_visual_state_visible,
        "no_private_information": args.no_private_information,
        "cursor_deliberate": args.cursor_deliberate,
        "ui_readable": args.ui_readable,
        "actions_complete": args.actions_complete,
        "notes": args.notes.strip(),
    }
    passed = all(value is True for key, value in verdict.items() if key != "notes") and bool(verdict["notes"])
    report = {
        "schema_version": 1,
        "kind": "recording_review",
        "shot_id": args.shot_id,
        "shot_spec_sha256": shot_spec_sha256(shot),
        "media_identity": media_identity(video),
        "duration_seconds": round(duration, 3),
        "required_visual_state": shot.get("required_visual_state"),
        "evidence": evidence,
        "verdict": verdict,
        "passed": passed,
        "reviewed_at": now_iso(),
    }
    write_json(output, report)
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


def seal_voice(args: argparse.Namespace) -> int:
    plan_path = Path(args.shot_plan).expanduser().resolve()
    _, shot = load_shot(plan_path, args.shot_id)
    voiceover = resolve_shot_media(shot, plan_path, args.voiceover, "voiceover")
    audit_path = Path(args.audit_report).expanduser().resolve()
    audit = read_json(audit_path)
    shot_audits = [item for item in audit.get("shots", []) if item.get("id") == args.shot_id]
    errors = []
    if audit.get("passed") is not True:
        errors.append("The transcript voice audit is not passing.")
    if len(shot_audits) != 1:
        errors.append("The transcript voice audit has no unique entry for this shot.")
        shot_audit = {}
    else:
        shot_audit = shot_audits[0]
    if shot_audit.get("passed") is not True:
        errors.append("This shot failed transcript comparison.")
    if shot_audit.get("shot_spec_sha256") != shot_spec_sha256(shot):
        errors.append("The voice audit is stale because the shot specification changed.")
    if shot_audit.get("narration_sha256") != narration_sha256(str(shot.get("narration", ""))):
        errors.append("The voice audit narration hash does not match the approved line.")
    if not identity_matches(shot_audit.get("voiceover_identity"), voiceover):
        errors.append("The voice audit belongs to different audio bytes.")

    verdict = {
        "pronunciation_clear": args.pronunciation_clear,
        "cadence_natural": args.cadence_natural,
        "no_audio_artifacts": args.no_audio_artifacts,
        "speaker_identity_match": args.speaker_identity_match,
        "emotional_delivery_match": args.emotional_delivery_match,
        "notes": args.notes.strip(),
    }
    if not verdict["notes"]:
        errors.append("Voice review needs concrete listening notes.")
    for field in VOICE_VERDICTS:
        if verdict[field] is not True:
            errors.append(f"Voice verdict {field} is not true.")
    report = {
        "schema_version": 1,
        "kind": "voice_review",
        "shot_id": args.shot_id,
        "shot_spec_sha256": shot_spec_sha256(shot),
        "narration_sha256": narration_sha256(str(shot.get("narration", ""))),
        "media_identity": media_identity(voiceover),
        "transcript_audit": str(audit_path),
        "transcript_audit_sha256": sha256_file(audit_path),
        "verdict": verdict,
        "errors": errors,
        "passed": not errors,
        "reviewed_at": now_iso(),
    }
    output = Path(args.output).expanduser().resolve()
    write_json(output, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


def seal_model_voice(args: argparse.Namespace) -> int:
    plan_path = Path(args.shot_plan).expanduser().resolve()
    plan, shot = load_shot(plan_path, args.shot_id)
    voiceover = resolve_shot_media(shot, plan_path, args.voiceover, "voiceover")
    transcript_audit = Path(args.transcript_audit).expanduser().resolve()
    registration = Path(args.voice_registration).expanduser().resolve()
    delivery_audit = Path(args.delivery_audit).expanduser().resolve()
    status = voice_delivery_audit_status(
        delivery_audit,
        plan,
        shot,
        voiceover,
        transcript_audit,
        registration,
        args.model,
    )
    if status.get("outcome") != "passed":
        errors = status.get("errors", [])
        raise SystemExit(
            "Automated voice review cannot be sealed:\n- "
            + "\n- ".join(str(error) for error in errors or ["audit is not passing"])
        )
    audit = status["report"]
    model_verdict = status["verdict"]
    evidence = [str(item).strip() for item in model_verdict.get("evidence", [])]
    notes = (
        f"Grok audio comparison ({args.model}): {model_verdict['summary'].strip()} "
        "Audible evidence: "
        + " | ".join(evidence)
    )
    verdict = {
        "pronunciation_clear": model_verdict["correct_pronunciation"]
        and model_verdict["no_clipped_words"],
        "cadence_natural": model_verdict["natural_delivery"]
        and model_verdict["creator_cadence_match"]
        and model_verdict["no_stutter_or_duplicate"],
        "no_audio_artifacts": model_verdict["no_audio_artifacts"]
        and model_verdict["no_clipped_words"],
        "speaker_identity_match": model_verdict["speaker_identity_match"],
        "emotional_delivery_match": model_verdict["emotional_delivery_match"],
        "notes": notes,
    }
    report = {
        "schema_version": 2,
        "kind": "voice_review",
        "reviewer": {
            "kind": "xai_audio_model",
            "provider": "xai",
            "model": args.model,
            "human_review": False,
        },
        "shot_id": args.shot_id,
        "shot_spec_sha256": shot_spec_sha256(shot),
        "narration_sha256": narration_sha256(str(shot.get("narration", ""))),
        "media_identity": media_identity(voiceover),
        "delivery_audit_identity": media_identity(delivery_audit),
        "voice_registration_identity": audit.get("voice_registration_identity"),
        "reference_identity": audit.get("reference_identity"),
        "transcript_identity": audit.get("transcript_identity"),
        "verdict": verdict,
        "errors": [],
        "passed": True,
        "reviewed_at": now_iso(),
    }
    validation_errors = validate_sealed_review(report, "voice", shot, voiceover)
    if validation_errors:
        raise SystemExit(
            "Automated voice review failed its own exact-evidence validation:\n- "
            + "\n- ".join(validation_errors)
        )
    output = Path(args.output).expanduser().resolve()
    write_json(output, report)
    print(json.dumps(report, indent=2))
    return 0


def add_boolean_flag(parser: argparse.ArgumentParser, name: str) -> None:
    parser.add_argument(f"--{name.replace('_', '-')}", action="store_true")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seal media-hash-bound human or evidence-backed model reviews for Luna shots."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    recording = sub.add_parser("recording")
    recording.add_argument("--shot-plan", required=True)
    recording.add_argument("--shot-id", required=True)
    recording.add_argument("--video")
    recording.add_argument("--evidence-time", action="append", type=float, default=[])
    for field in (
        "required_visual_state_visible",
        "no_private_information",
        "cursor_deliberate",
        "ui_readable",
        "actions_complete",
    ):
        add_boolean_flag(recording, field)
    recording.add_argument("--notes", required=True)
    recording.add_argument("--output", required=True)
    recording.set_defaults(func=seal_recording)

    voice = sub.add_parser("voice")
    voice.add_argument("--shot-plan", required=True)
    voice.add_argument("--shot-id", required=True)
    voice.add_argument("--voiceover")
    voice.add_argument("--audit-report", required=True)
    for field in VOICE_VERDICTS:
        add_boolean_flag(voice, field)
    voice.add_argument("--notes", required=True)
    voice.add_argument("--output", required=True)
    voice.set_defaults(func=seal_voice)

    model_voice = sub.add_parser("voice-model")
    model_voice.add_argument("--shot-plan", required=True)
    model_voice.add_argument("--shot-id", required=True)
    model_voice.add_argument("--voiceover")
    model_voice.add_argument("--transcript-audit", required=True)
    model_voice.add_argument("--delivery-audit", required=True)
    model_voice.add_argument("--voice-registration", required=True)
    model_voice.add_argument("--model", default=DEFAULT_MODEL)
    model_voice.add_argument("--output", required=True)
    model_voice.set_defaults(func=seal_model_voice)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
