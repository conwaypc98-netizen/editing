#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from production_evidence import (
    identity_matches,
    media_identity,
    narration_sha256,
    read_json,
    resolve_media,
    shot_plan_spec_sha256,
    shot_spec_sha256,
    transcript_source_errors,
    validate_sealed_review,
    validate_shot_plan,
    xai_voice_provenance_errors,
)

SCRIPT_DIR = Path(__file__).resolve().parent


def resolve_job(value: str) -> tuple[Path, dict]:
    root = Path(value).expanduser().resolve()
    marker = root / ".luna-job.json"
    if not marker.is_file():
        raise SystemExit(f"Luna job marker not found: {marker}")
    manifest = read_json(marker)
    if Path(manifest.get("job_root", "")).resolve() != root:
        raise SystemExit("Job marker path and job_root disagree.")
    if manifest.get("mode") != "synthetic":
        raise SystemExit("production_director.py only operates synthetic Luna jobs.")
    return root, manifest


def transcriber_python() -> Path | None:
    explicit = os.environ.get("LUNA_EDITOR_TRANSCRIBE_PYTHON")
    if explicit and Path(explicit).expanduser().is_file():
        # Keep the virtual-environment launcher path intact. Resolving its
        # symlink selects the base interpreter and drops the venv packages.
        return Path(explicit).expanduser().absolute()
    tool_root = Path(
        os.environ.get(
            "LUNA_EDITOR_TOOL_DIR",
            str(Path.home() / ".codex" / "tools" / "luna-longform-editor"),
        )
    )
    candidates = [
        tool_root / "transcribe-venv" / "bin" / "python",
        tool_root / "transcribe-venv" / "Scripts" / "python.exe",
    ]
    return next((path.absolute() for path in candidates if path.is_file()), None)


def report_is_fresh(report_path: Path, inputs: list[Path]) -> bool:
    if not report_path.is_file():
        return False
    existing_inputs = [path for path in inputs if path.is_file()]
    if not existing_inputs:
        return True
    newest_input = max(path.stat().st_mtime_ns for path in existing_inputs)
    return report_path.stat().st_mtime_ns >= newest_input


def manifest_final_path(manifest: dict) -> Path | None:
    value = manifest.get("final_output")
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        return None
    return Path(value).expanduser()


def accepted_delivery_is_current(manifest: dict, candidate: Path) -> bool:
    accepted_path = manifest_final_path(manifest)
    if (
        manifest.get("status") != "accepted"
        or accepted_path is None
        or not accepted_path.is_file()
        or not candidate.is_file()
    ):
        return False
    accepted_sha = manifest.get("final_identity", {}).get("sha256")
    return (
        bool(accepted_sha)
        and accepted_sha == media_identity(accepted_path)["sha256"]
        and accepted_sha == media_identity(candidate)["sha256"]
    )


def command_action(action: str, reason: str, command: list[str], **extra) -> dict:
    return {
        "action": action,
        "reason": reason,
        "automatic": True,
        "command": command,
        **extra,
    }


def agent_action(action: str, reason: str, **extra) -> dict:
    return {
        "action": action,
        "reason": reason,
        "automatic": False,
        **extra,
    }


def voice_transcript(job: Path, shot_id: str) -> Path | None:
    candidates = [
        job / "voice" / "transcripts" / shot_id / "transcript.json",
        job / "voice" / "transcripts" / f"{shot_id}.json",
        job / "voice" / shot_id / "transcript.json",
    ]
    return next((path for path in candidates if path.is_file()), None)


def report_matches_plan(report_path: Path, plan: dict, project_path: Path) -> bool:
    if not report_path.is_file():
        return False
    report = read_json(report_path)
    return (
        report.get("passed") is True
        and report.get("shot_plan_spec_sha256") == shot_plan_spec_sha256(plan)
        and identity_matches(report.get("project_identity"), project_path)
    )


def creator_report_is_current(
    report_path: Path,
    mode: str,
    plan: dict,
    project_path: Path,
    profile_path: Path,
    transcript_path: Path | None = None,
) -> bool:
    if not report_path.is_file() or not profile_path.is_file():
        return False
    report = read_json(report_path)
    if report.get("mode") != mode:
        return False
    if report.get("shot_plan_spec_sha256") != shot_plan_spec_sha256(plan):
        return False
    if not identity_matches(report.get("project_identity"), project_path):
        return False
    if not identity_matches(report.get("channel_profile_identity"), profile_path):
        return False
    return transcript_path is None or identity_matches(
        report.get("transcript_identity"), transcript_path
    )


def derive_status(job: Path) -> dict:
    project_path = job / "project.json"
    plan_path = job / "plans" / "shot_plan.json"
    project = read_json(project_path)
    plan = read_json(plan_path)
    validation = validate_shot_plan(plan, project)
    base = plan_path.parent
    plan_report = job / "qa" / "shot_plan_validation.json"
    result = {
        "schema_version": 1,
        "job": str(job),
        "mode": "synthetic",
        "shot_plan_spec_sha256": validation["shot_plan_spec_sha256"],
        "plan_validation": validation,
        "shots": [],
        "complete": False,
    }
    if not validation["passed"]:
        result["stage"] = "planning"
        result["next"] = agent_action(
            "write_or_repair_shot_plan",
            "The immutable shot specification is incomplete or invalid.",
            errors=validation["errors"],
            shot_plan=str(plan_path),
            project=str(project_path),
        )
        return result
    if not report_matches_plan(plan_report, plan, project_path):
        result["stage"] = "planning"
        result["next"] = command_action(
            "validate_shot_plan",
            "The current immutable shot specification has not been sealed by the validator.",
            [
                sys.executable,
                str(SCRIPT_DIR / "validate_shot_plan.py"),
                "--shot-plan",
                str(plan_path),
                "--project",
                str(project_path),
                "--report",
                str(plan_report),
            ],
        )
        return result

    profile_path = job / "channel_profile.json"
    creator_plan_report = job / "qa" / "creator_fidelity_plan.json"
    if not profile_path.is_file():
        result["stage"] = "creator_fidelity"
        result["next"] = agent_action(
            "restore_channel_profile",
            "The job has no creator profile, so Luna likeness cannot be evaluated.",
            expected_path=str(profile_path),
        )
        return result
    if not creator_report_is_current(
        creator_plan_report,
        "plan",
        plan,
        project_path,
        profile_path,
    ):
        result["stage"] = "creator_fidelity"
        result["next"] = command_action(
            "audit_creator_fidelity_plan",
            "The current script and shot plan have no current creator-fidelity audit.",
            [
                sys.executable,
                str(SCRIPT_DIR / "audit_creator_fidelity.py"),
                "plan",
                "--shot-plan",
                str(plan_path),
                "--project",
                str(project_path),
                "--channel-profile",
                str(profile_path),
                "--report",
                str(creator_plan_report),
            ],
            acceptable_return_codes=[0, 1],
        )
        return result
    creator_plan = read_json(creator_plan_report)
    if creator_plan.get("passed") is not True:
        result["stage"] = "creator_fidelity"
        result["next"] = agent_action(
            "rewrite_plan_for_creator_fidelity",
            "The current script violates the creator-fidelity or narration/visual-contract gates.",
            errors=creator_plan.get("errors", []),
            warnings=creator_plan.get("warnings", []),
            report=str(creator_plan_report),
            shot_plan=str(plan_path),
        )
        return result

    voice_dir = job / "voice"
    recording_review_dir = job / "qa" / "reviews" / "recording"
    voice_review_dir = job / "qa" / "reviews" / "voice"
    audit_path = job / "qa" / "voiceover_audit.json"
    audit = read_json(audit_path) if audit_path.is_file() else {}
    audit_plan_current = (
        audit.get("shot_plan_spec_sha256") == validation["shot_plan_spec_sha256"]
    )
    audit_by_shot = {item.get("id"): item for item in audit.get("shots", [])}
    voice_config = project.get("voice", {})
    voice_provider = str(voice_config.get("provider", "xai")).strip().lower()
    require_xai_provenance = voice_provider == "xai"
    missing_voices = []
    missing_transcripts = []
    missing_recordings = []
    missing_voice_reviews = []
    missing_recording_reviews = []
    voice_provenance_failures = {}
    transcript_provenance_failures = {}
    audit_entry_failures = {}
    audit_failed_shots = []
    shots = plan.get("shots", [])
    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.get("id", f"shot-{index:03d}"))
        video = resolve_media(shot.get("video"), base, job / "shots" / f"{shot_id}.mp4")
        voice = resolve_media(shot.get("voiceover"), base, voice_dir / f"{shot_id}.wav")
        transcript = voice_transcript(job, shot_id)
        recording_review_path = recording_review_dir / f"{shot_id}.json"
        voice_review_path = voice_review_dir / f"{shot_id}.json"
        recording_review = read_json(recording_review_path) if recording_review_path.is_file() else None
        voice_review = read_json(voice_review_path) if voice_review_path.is_file() else None
        shot_audit = audit_by_shot.get(shot_id, {})
        recording_errors = (
            validate_sealed_review(recording_review, "recording", shot, video)
            if video and video.is_file()
            else ["recording missing"]
        )
        voice_errors = (
            validate_sealed_review(voice_review, "voice", shot, voice)
            if voice and voice.is_file()
            else ["voiceover missing"]
        )
        voice_provenance_errors = []
        if not voice or not voice.is_file():
            voice_provenance_errors.append("voiceover missing")
        elif require_xai_provenance:
            voice_provenance_errors.extend(xai_voice_provenance_errors(shot, voice))
        if voice_provenance_errors:
            missing_voices.append(shot_id)
            voice_provenance_failures[shot_id] = voice_provenance_errors

        transcript_errors = []
        if transcript is None:
            transcript_errors.append("transcript missing")
        elif voice and voice.is_file() and not voice_provenance_errors:
            transcript_errors.extend(
                transcript_source_errors(
                    transcript,
                    voice,
                    require_identity=require_xai_provenance,
                )
            )
            if shot_audit and not identity_matches(shot_audit.get("voiceover_identity"), voice):
                transcript_errors.append("Voice bytes changed after the current transcript audit.")
            if shot_audit and not identity_matches(shot_audit.get("transcript_identity"), transcript):
                transcript_errors.append("Transcript bytes changed after the current transcript audit.")
        if transcript_errors:
            missing_transcripts.append(shot_id)
            transcript_provenance_failures[shot_id] = transcript_errors

        audit_errors = []
        if not shot_audit:
            audit_errors.append("audit entry is missing")
        else:
            if shot_audit.get("passed") is not True:
                audit_failed_shots.append(shot_id)
            if shot_audit.get("shot_spec_sha256") != shot_spec_sha256(shot):
                audit_errors.append("audit shot specification is stale")
            if shot_audit.get("narration_sha256") != narration_sha256(str(shot.get("narration", ""))):
                audit_errors.append("audit narration is stale")
            if voice and voice.is_file() and not identity_matches(shot_audit.get("voiceover_identity"), voice):
                audit_errors.append("audit audio identity is stale")
            if transcript and transcript.is_file() and not identity_matches(
                shot_audit.get("transcript_identity"), transcript
            ):
                audit_errors.append("audit transcript identity is stale")
        if audit_errors:
            audit_entry_failures[shot_id] = audit_errors

        if not video or not video.is_file():
            missing_recordings.append(shot_id)
        elif recording_errors:
            missing_recording_reviews.append(shot_id)
        if voice and voice.is_file() and (voice_errors or shot_audit.get("passed") is not True):
            missing_voice_reviews.append(shot_id)
        result["shots"].append(
            {
                "id": shot_id,
                "video": str(video) if video else None,
                "voiceover": str(voice) if voice else None,
                "voice_provenance_errors": voice_provenance_errors,
                "voice_transcript": str(transcript) if transcript else None,
                "transcript_provenance_errors": transcript_errors,
                "voice_audit_errors": audit_errors,
                "recording_review": str(recording_review_path),
                "recording_review_errors": recording_errors,
                "voice_review": str(voice_review_path),
                "voice_review_errors": voice_errors,
            }
        )

    audit_evidence_current = audit_plan_current and not audit_entry_failures
    audit_current = (
        audit_evidence_current
        and audit.get("passed") is True
        and not audit_failed_shots
    )

    if missing_voices:
        if not require_xai_provenance:
            result["stage"] = "voice_generation"
            result["next"] = agent_action(
                "provide_current_voiceovers",
                "The configured non-xAI provider has missing or stale narration that cannot be regenerated automatically.",
                missing_shots=missing_voices,
                provenance_errors=voice_provenance_failures,
            )
            return result
        consent = str(voice_config.get("owner_consent", "")).lower() == "confirmed"
        if not consent:
            result["stage"] = "voice_configuration"
            result["next"] = agent_action(
                "confirm_voice_ownership",
                "The project brief must record owner_consent as confirmed before cloned narration is generated.",
                project=str(project_path),
            )
            return result
        cadence_failures = {
            shot_id: errors
            for shot_id, errors in voice_provenance_failures.items()
            if errors and all("cadence is outside" in error for error in errors)
        }
        if cadence_failures:
            result["stage"] = "voice_direction"
            result["next"] = agent_action(
                "adjust_voice_performance",
                "Generated speech missed the approved creator cadence after automatic speed correction. "
                "Adjust the affected shot's voice_performance speed or target range from actual evidence; "
                "do not regenerate unchanged settings.",
                failed_shots=cadence_failures,
                voice_performance={
                    str(shot.get("id")): shot.get("voice_performance")
                    for shot in shots
                    if str(shot.get("id")) in cadence_failures
                },
            )
            return result
        if not os.environ.get("XAI_API_KEY") or not os.environ.get("XAI_VOICE_ID"):
            result["stage"] = "voice_configuration"
            result["next"] = agent_action(
                "configure_verified_xai_voice",
                "XAI_API_KEY and XAI_VOICE_ID are required for missing or stale narration files.",
                missing_shots=missing_voices,
                provenance_errors=voice_provenance_failures,
            )
            return result
        command = [
            sys.executable,
            str(SCRIPT_DIR / "xai_voiceover.py"),
            "synthesize-plan",
            "--shot-plan",
            str(plan_path),
            "--output-dir",
            str(voice_dir),
            "--language",
            str(voice_config.get("language", "en")),
            "--owner-consent-confirmed",
        ]
        for shot_id in missing_voices:
            command.extend(["--shot-id", shot_id])
        result["stage"] = "voice_generation"
        result["next"] = command_action(
            "generate_xai_voiceovers",
            "Generate only missing or stale approved shot lines; preserve every current reviewed xAI take.",
            command,
            missing_shots=missing_voices,
            provenance_errors=voice_provenance_failures,
        )
        return result

    if missing_transcripts:
        shot_id = missing_transcripts[0]
        voice = next(Path(item["voiceover"]) for item in result["shots"] if item["id"] == shot_id)
        python = transcriber_python()
        if python is None:
            result["stage"] = "voice_audit"
            result["next"] = agent_action(
                "install_transcription_environment",
                "The voiceover must be transcribed, but the Luna transcription environment is missing.",
            )
            return result
        output_dir = job / "voice" / "transcripts" / shot_id
        result["stage"] = "voice_audit"
        result["next"] = command_action(
            "transcribe_voiceover",
            "The exact current audio bytes must be transcribed before comparison with the approved narration.",
            [
                str(python),
                str(SCRIPT_DIR / "transcribe_with_faster_whisper.py"),
                str(voice),
                "--out-dir",
                str(output_dir),
                "--model",
                "small.en",
            ],
            shot_id=shot_id,
            provenance_errors=transcript_provenance_failures.get(shot_id, []),
        )
        return result

    if not audit_evidence_current:
        command = [
            sys.executable,
            str(SCRIPT_DIR / "audit_voiceover.py"),
            "--shot-plan",
            str(plan_path),
            "--voiceover-dir",
            str(voice_dir),
            "--report",
            str(audit_path),
        ]
        for shot in result["shots"]:
            command.extend(["--transcript", f"{shot['id']}={shot['voice_transcript']}"])
        result["stage"] = "voice_audit"
        result["next"] = command_action(
            "audit_voiceovers",
            "The transcript audit is missing or stale for the current narration/media.",
            command,
        )
        return result

    if not audit_current:
        result["stage"] = "voice_audit"
        result["next"] = agent_action(
            "repair_failed_voiceovers",
            "The current transcript audit disproves one or more generated takes; regenerate or rewrite those shots before resuming.",
            failed_shots=audit_failed_shots,
            errors=audit.get("errors", []),
            audit_report=str(audit_path),
        )
        return result

    if missing_voice_reviews:
        shot_id = missing_voice_reviews[0]
        shot = next(shot for shot in shots if shot.get("id") == shot_id)
        voice = next(item["voiceover"] for item in result["shots"] if item["id"] == shot_id)
        xai_metadata_path = Path(voice).with_suffix(Path(voice).suffix + ".xai.json")
        xai_metadata = read_json(xai_metadata_path) if xai_metadata_path.is_file() else {}
        output = voice_review_dir / f"{shot_id}.json"
        result["stage"] = "voice_listening_review"
        result["next"] = agent_action(
            "listen_and_seal_voice_review",
            "Transcript equality is not proof of natural delivery; this exact audio still needs a listening verdict.",
            shot_id=shot_id,
            narration=shot.get("narration"),
            voiceover=voice,
            voice_performance=shot.get("voice_performance"),
            measured_cadence=xai_metadata.get("cadence"),
            seal_command=[
                sys.executable,
                str(SCRIPT_DIR / "seal_production_review.py"),
                "voice",
                "--shot-plan",
                str(plan_path),
                "--shot-id",
                shot_id,
                "--voiceover",
                voice,
                "--audit-report",
                str(audit_path),
                "--pronunciation-clear",
                "--cadence-natural",
                "--no-audio-artifacts",
                "--speaker-identity-match",
                "--emotional-delivery-match",
                "--notes",
                "<specific listening evidence>",
                "--output",
                str(output),
            ],
        )
        return result

    if missing_recordings:
        shot_id = missing_recordings[0]
        shot = next(shot for shot in shots if shot.get("id") == shot_id)
        output = job / "shots" / f"{shot_id}.mp4"
        state = output.with_suffix(".recording.json")
        result["stage"] = "desktop_recording"
        result["next"] = agent_action(
            "record_shot_with_computer_use",
            "This shot has no desktop recording. Codex must execute the specified actions deliberately.",
            shot_id=shot_id,
            narration=shot.get("narration"),
            computer_actions=shot.get("computer_actions"),
            required_visual_state=shot.get("required_visual_state"),
            maximum_recording_seconds=shot.get("maximum_recording_seconds"),
            start_command=[
                sys.executable,
                str(SCRIPT_DIR / "record_desktop.py"),
                "start",
                "--output",
                str(output),
                "--state",
                str(state),
            ],
            stop_command=[
                sys.executable,
                str(SCRIPT_DIR / "record_desktop.py"),
                "stop",
                "--state",
                str(state),
            ],
            state_storyboard_fallback={
                "use_when": (
                    "The full-screen recorder cannot see the app Computer Use is controlling, "
                    "or a state-by-state tutorial is clearer than real-time capture."
                ),
                "manifest": str(output.with_suffix(".capture.json")),
                "frames_directory": str(output.with_suffix(".capture-frames")),
                "capture_script": str(SCRIPT_DIR / "capture_window_storyboard.py"),
                "selector_method": (
                    "List windows, then select the intended macOS/Windows app by owner/process, "
                    "title substring, or exact window ID. Restore it before capture if minimized."
                ),
                "required_method": (
                    "Capture the clean pre-action state, each consequential action/result, and the final proof state; "
                    "inspect every captured image for the intended real UI, then render the storyboard to the exact video path above."
                ),
            },
        )
        return result

    if missing_recording_reviews:
        shot_id = missing_recording_reviews[0]
        shot = next(shot for shot in shots if shot.get("id") == shot_id)
        video = next(item["video"] for item in result["shots"] if item["id"] == shot_id)
        output = recording_review_dir / f"{shot_id}.json"
        result["stage"] = "recording_review"
        result["next"] = agent_action(
            "inspect_and_seal_recording_review",
            "The recording exists but has no current media-bound visual verdict.",
            shot_id=shot_id,
            video=video,
            required_visual_state=shot.get("required_visual_state"),
            seal_command=[
                sys.executable,
                str(SCRIPT_DIR / "seal_production_review.py"),
                "recording",
                "--shot-plan",
                str(plan_path),
                "--shot-id",
                shot_id,
                "--video",
                video,
                "--evidence-time",
                "<reviewed timestamp>",
                "--required-visual-state-visible",
                "--no-private-information",
                "--cursor-deliberate",
                "--ui-readable",
                "--actions-complete",
                "--notes",
                "<specific visual evidence>",
                "--output",
                str(output),
            ],
        )
        return result

    assembled = job / "renders" / "assembled.mp4"
    assembly_report = job / "qa" / "assembly_report.json"
    assembly_current = False
    if assembly_report.is_file() and assembled.is_file():
        report = read_json(assembly_report)
        assembly_current = (
            report.get("passed") is True
            and report.get("shot_plan_spec_sha256") == validation["shot_plan_spec_sha256"]
            and identity_matches(report.get("project_identity"), project_path)
            and identity_matches(report.get("output_identity"), assembled)
        )
    zoom_plan = job / "plans" / "generated_focus_zoom.json"
    if not assembly_current:
        result["stage"] = "assembly"
        result["next"] = command_action(
            "assemble_reviewed_shots",
            "All shot evidence is sealed; the current plan has no matching assembly.",
            [
                sys.executable,
                str(SCRIPT_DIR / "assemble_shot_plan.py"),
                "--shot-plan",
                str(plan_path),
                "--project",
                str(project_path),
                "--voiceover-dir",
                str(voice_dir),
                "--output",
                str(assembled),
                "--report",
                str(assembly_report),
                "--zoom-plan-output",
                str(zoom_plan),
            ],
        )
        return result

    focused = job / "renders" / "focused.mp4"
    zooms = read_json(zoom_plan).get("zooms", []) if zoom_plan.is_file() else []
    final_candidate = assembled
    if zooms:
        if not focused.is_file() or focused.stat().st_mtime_ns < max(assembled.stat().st_mtime_ns, zoom_plan.stat().st_mtime_ns):
            result["stage"] = "focus_zoom"
            result["next"] = command_action(
                "apply_focus_zoom",
                "The reviewed target boxes have not been applied to the current assembly.",
                [
                    sys.executable,
                    str(SCRIPT_DIR / "apply_focus_zoom.py"),
                    "--input",
                    str(assembled),
                    "--zoom-plan",
                    str(zoom_plan),
                    "--output",
                    str(focused),
                ],
            )
            return result
        final_candidate = focused

    final_transcript_dir = job / "analysis" / "final_transcript"
    final_transcript = final_transcript_dir / "transcript.json"
    if not final_transcript.is_file() or final_transcript.stat().st_mtime_ns < final_candidate.stat().st_mtime_ns:
        python = transcriber_python()
        if python is None:
            result["stage"] = "final_transcript"
            result["next"] = agent_action(
                "install_transcription_environment",
                "Final semantic QA requires a transcript of the exact rendered candidate.",
            )
            return result
        result["stage"] = "final_transcript"
        result["next"] = command_action(
            "transcribe_final_candidate",
            "Final QA must inspect the exact rendered narration, not the source script.",
            [
                str(python),
                str(SCRIPT_DIR / "transcribe_with_faster_whisper.py"),
                str(final_candidate),
                "--out-dir",
                str(final_transcript_dir),
                "--model",
                "small.en",
            ],
        )
        return result

    qa_dir = job / "qa" / "final"
    creator_final_report = qa_dir / "creator_fidelity.json"
    if not creator_report_is_current(
        creator_final_report,
        "final",
        plan,
        project_path,
        profile_path,
        final_transcript,
    ):
        result["stage"] = "creator_fidelity"
        result["next"] = command_action(
            "audit_final_creator_fidelity",
            "The exact final transcript has no current creator-fidelity verdict.",
            [
                sys.executable,
                str(SCRIPT_DIR / "audit_creator_fidelity.py"),
                "final",
                "--shot-plan",
                str(plan_path),
                "--project",
                str(project_path),
                "--channel-profile",
                str(profile_path),
                "--transcript-json",
                str(final_transcript),
                "--report",
                str(creator_final_report),
            ],
            acceptable_return_codes=[0, 1],
        )
        return result
    creator_final = read_json(creator_final_report)
    if creator_final.get("passed") is not True:
        result["stage"] = "creator_fidelity"
        result["next"] = agent_action(
            "repair_final_creator_fidelity",
            "The exact final narration does not yet match the approved script and creator profile.",
            errors=creator_final.get("errors", []),
            warnings=creator_final.get("warnings", []),
            report=str(creator_final_report),
        )
        return result

    qa_report = qa_dir / "final_qa_report.json"
    completed_visual = qa_dir / "visual_review_completed.json"
    completed_gaps = qa_dir / "speech_gap_review_completed.json"
    qa = read_json(qa_report) if qa_report.is_file() else {}
    candidate_identity = media_identity(final_candidate)
    qa_inputs = [
        final_candidate,
        final_transcript,
        assembly_report,
        zoom_plan,
        creator_final_report,
    ]
    qa_inputs.extend(path for path in (completed_visual, completed_gaps) if path.is_file())
    qa_current = (
        qa.get("input") == str(final_candidate)
        and isinstance(qa.get("media_identity"), dict)
        and qa.get("media_identity", {}).get("sha256") == candidate_identity["sha256"]
        and report_is_fresh(qa_report, qa_inputs)
    )
    verify_command = [
        sys.executable,
        str(SCRIPT_DIR / "verify_final_video.py"),
        "--input",
        str(final_candidate),
        "--output-dir",
        str(qa_dir),
        "--transcript-json",
        str(final_transcript),
        "--plan-report",
        str(assembly_report),
        "--zoom-plan",
        str(zoom_plan),
        "--creator-fidelity-report",
        str(creator_final_report),
    ]
    if completed_visual.is_file():
        verify_command.extend(["--visual-review", str(completed_visual)])
    if completed_gaps.is_file():
        verify_command.extend(["--speech-gap-review", str(completed_gaps)])
    if not qa_current:
        result["stage"] = "final_qa"
        result["next"] = command_action(
            "generate_final_qa_evidence",
            "The exact final candidate has no current technical, speech, plan, and visual QA packet.",
            verify_command,
            acceptable_return_codes=[0, 1],
        )
        return result
    if qa.get("passed") is not True:
        result["stage"] = "final_qa"
        if not completed_visual.is_file() or not completed_gaps.is_file():
            result["next"] = agent_action(
                "complete_adversarial_final_review",
                "Final QA is fail-closed until every timeline/zoom frame and every flagged speech gap has a concrete verdict.",
                candidate=str(final_candidate),
                visual_template=str(qa_dir / "visual_review_template.json"),
                visual_review=str(completed_visual),
                speech_gap_template=str(qa_dir / "speech_gap_review_template.json"),
                speech_gap_review=str(completed_gaps),
                rerun_command=verify_command,
            )
        else:
            result["next"] = agent_action(
                "repair_failed_final",
                "Completed reviews still leave one or more final gates failing; revise the responsible shots or narration.",
                gates=qa.get("gates"),
            )
        return result

    manifest = read_json(job / ".luna-job.json")
    accepted_path = manifest_final_path(manifest)
    accepted_current = accepted_delivery_is_current(manifest, final_candidate)
    if not accepted_current:
        result["stage"] = "acceptance"
        result["next"] = command_action(
            "accept_verified_final",
            "Every gate passed for the exact candidate; seal it into delivery.",
            [
                sys.executable,
                str(SCRIPT_DIR / "luna_editor.py"),
                "accept",
                "--job",
                str(job),
                "--final",
                str(final_candidate),
                "--qa-report",
                str(qa_report),
            ],
        )
        return result
    result["stage"] = "complete"
    result["complete"] = True
    result["final"] = str(accepted_path)
    result["next"] = None
    return result


def resume(args: argparse.Namespace) -> int:
    job, _ = resolve_job(args.job)
    executed = []
    for _ in range(args.maximum_automatic_steps):
        status = derive_status(job)
        action = status.get("next")
        if not args.execute_safe or not action or action.get("automatic") is not True:
            status["executed"] = executed
            print(json.dumps(status, indent=2))
            return 0 if status.get("complete") else 2
        command = action["command"]
        completed = subprocess.run(command, capture_output=True, text=True)
        acceptable = set(action.get("acceptable_return_codes", [0]))
        executed.append(
            {
                "action": action["action"],
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
        if completed.returncode not in acceptable:
            status["executed"] = executed
            status["automatic_failure"] = executed[-1]
            print(json.dumps(status, indent=2))
            return completed.returncode or 1
    status = derive_status(job)
    status["executed"] = executed
    status["automatic_failure"] = "Maximum automatic steps reached."
    print(json.dumps(status, indent=2))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume a fail-closed autonomous Luna production from its evidence on disk."
    )
    parser.add_argument("--job", required=True)
    parser.add_argument("--execute-safe", action="store_true")
    parser.add_argument("--maximum-automatic-steps", type=int, default=20)
    args = parser.parse_args()
    if args.maximum_automatic_steps < 0:
        raise SystemExit("--maximum-automatic-steps cannot be negative.")
    return resume(args)


if __name__ == "__main__":
    raise SystemExit(main())
