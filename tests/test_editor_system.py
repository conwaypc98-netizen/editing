import ast
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "luna-longform-editor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import xai_voiceover as xai_voiceover_module  # noqa: E402
from audit_creator_fidelity import audit_plan  # noqa: E402
from audit_voice_delivery import (  # noqa: E402
    DEFAULT_MODEL,
    collect_response_text,
    pcm_signal_metrics,
    review_instructions,
    signal_failures,
    strict_verdict,
    verdict_failures,
    voice_delivery_audit_status,
)
from capture_window_storyboard import (  # noqa: E402
    build_parser,
    image_signal_stats,
    window_matches,
)
from creator_fidelity import transcript_style  # noqa: E402
from production_director import (  # noqa: E402
    accepted_delivery_is_current,
    creator_report_is_current,
    derive_status,
    manifest_final_path,
    missing_required_outputs,
    report_is_fresh,
    transcriber_python,
)
from production_evidence import (  # noqa: E402
    canonical_sha256,
    media_identity,
    narration_sha256,
    sha256_bytes,
    shot_plan_spec_sha256,
    shot_spec_sha256,
    transcript_source_errors,
    validate_sealed_review,
    validate_shot_plan,
    voice_registration_errors,
    xai_voice_provenance_errors,
)
from verify_final_video import creator_fidelity_gate  # noqa: E402

storyboard_parser = build_parser


def passing_voice_delivery_verdict() -> dict:
    return {
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
            "The candidate keeps the same speaker timbre as the reference.",
            "Every approved word is complete with clean joins and natural timing.",
        ],
        "summary": "The candidate audibly matches the reference and delivery contract.",
    }


def run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        capture_output=True,
        text=True,
    )


def write_passing_shot_evidence(
    job: Path,
    plan_data: dict,
    video: Path,
    voice: Path,
    include_audit: bool = True,
) -> None:
    shot = plan_data["shots"][0]
    shot_id = shot["id"]
    recording_dir = job / "qa" / "reviews" / "recording"
    voice_dir = job / "qa" / "reviews" / "voice"
    recording_dir.mkdir(parents=True, exist_ok=True)
    voice_dir.mkdir(parents=True, exist_ok=True)
    (recording_dir / f"{shot_id}.json").write_text(
        json.dumps(
            {
                "kind": "recording_review",
                "shot_id": shot_id,
                "shot_spec_sha256": shot_spec_sha256(shot),
                "media_identity": media_identity(video),
                "passed": True,
                "evidence": [{"time": 0.5, "frame": "fixture.png", "frame_sha256": "fixture"}],
                "verdict": {
                    "required_visual_state_visible": True,
                    "no_private_information": True,
                    "cursor_deliberate": True,
                    "ui_readable": True,
                    "actions_complete": True,
                    "notes": "The synthetic fixture visibly contains the required state.",
                },
            }
        ),
        encoding="utf-8",
    )
    (voice_dir / f"{shot_id}.json").write_text(
        json.dumps(
            {
                "kind": "voice_review",
                "shot_id": shot_id,
                "shot_spec_sha256": shot_spec_sha256(shot),
                "media_identity": media_identity(voice),
                "passed": True,
                "verdict": {
                    "pronunciation_clear": True,
                    "cadence_natural": True,
                    "no_audio_artifacts": True,
                    "speaker_identity_match": True,
                    "emotional_delivery_match": True,
                    "notes": "The synthetic fixture has clean and intentionally reviewed audio.",
                },
            }
        ),
        encoding="utf-8",
    )
    if not include_audit:
        return
    (job / "qa" / "voiceover_audit.json").write_text(
        json.dumps(
            {
                "passed": True,
                "shot_plan_spec_sha256": shot_plan_spec_sha256(plan_data),
                "shots": [
                    {
                        "id": shot_id,
                        "passed": True,
                        "shot_spec_sha256": shot_spec_sha256(shot),
                        "narration_sha256": narration_sha256(shot["narration"]),
                        "voiceover_identity": media_identity(voice),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def write_passing_creator_plan_evidence(job: Path, plan_data: dict) -> None:
    profile = job / "channel_profile.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "channel": "Luna Tweak",
                "quantitative_confidence": "untrained",
                "story_and_visuals": {"order": ["hook", "setup", "tutorial", "proof", "cta"]},
                "learned_measurements": {},
            }
        ),
        encoding="utf-8",
    )
    (job / "qa" / "creator_fidelity_plan.json").write_text(
        json.dumps(
            {
                "mode": "plan",
                "passed": True,
                "shot_plan_spec_sha256": shot_plan_spec_sha256(plan_data),
                "project_identity": media_identity(job / "project.json"),
                "channel_profile_identity": media_identity(profile),
            }
        ),
        encoding="utf-8",
    )


def write_voice_registration_fixture(job: Path, voice_id: str = "abcd1234") -> dict:
    voice_dir = job / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    reference = voice_dir / "owner-reference.wav"
    preparation = voice_dir / "owner-reference-report.json"
    transcript = voice_dir / "owner-reference-transcript" / "transcript.json"
    review = voice_dir / "owner-reference-review.json"
    registration = voice_dir / "voice_registration.json"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(b"exact reviewed owner reference")
    preparation.write_text('{"kind":"preparation"}', encoding="utf-8")
    transcript.write_text('{"segments":[]}', encoding="utf-8")
    review.write_text(
        json.dumps(
            {
                "kind": "voice_reference_review",
                "passed": True,
                "upload_ready": True,
                "reference_identity": media_identity(reference),
                "preparation_report_identity": media_identity(preparation),
                "transcript_identity": media_identity(transcript),
                "auditory_review": {
                    "reviewer_kind": "human",
                    "reviewer_name": "Fixture Reviewer",
                    "listened_from_start_to_finish": True,
                },
            }
        ),
        encoding="utf-8",
    )
    registration.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "xai",
                "voice_id": voice_id,
                "verified": True,
                "reference_download_verified": True,
                "reference_identity": media_identity(reference),
                "preparation_report_identity": media_identity(preparation),
                "transcript_identity": media_identity(transcript),
                "reference_review_identity": media_identity(review),
                "downloaded_reference_sha256": media_identity(reference)["sha256"],
                "voice_metadata": {"voice_id": voice_id},
                "dry_run": False,
            }
        ),
        encoding="utf-8",
    )
    return {
        "reference": reference,
        "preparation": preparation,
        "transcript": transcript,
        "review": review,
        "registration": registration,
    }


def write_schema_three_director_job(job: Path) -> dict:
    (job / "plans").mkdir(parents=True)
    (job / "qa").mkdir()
    (job / "voice").mkdir()
    project = {
        "schema_version": 3,
        "mode": "synthetic",
        "required_story_roles": ["hook"],
        "voice": {
            "provider": "xai",
            "owner_consent": "confirmed",
            "language": "en",
            "reference": {
                "audio": "voice/owner-reference.wav",
                "preparation_report": "voice/owner-reference-report.json",
                "transcript": "voice/owner-reference-transcript/transcript.json",
                "review": "voice/owner-reference-review.json",
            },
            "registration": "voice/voice_registration.json",
        },
    }
    (job / "project.json").write_text(json.dumps(project), encoding="utf-8")
    shot = {
        "id": "shot-001",
        "story_role": "hook",
        "viewer_purpose": "State the result.",
        "rationale": "Direct opening.",
        "continuity": "Starts the video.",
        "narration": "This is the exact registered voice test.",
        "computer_actions": ["Show the desktop"],
        "required_visual_state": "Desktop is visible.",
        "timing_mode": "fit",
        "maximum_recording_seconds": 4.0,
    }
    plan = {
        "schema_version": 2,
        "title": "Registered voice test",
        "story": "A current owner voice is mandatory.",
        "shots": [shot],
    }
    (job / "plans" / "shot_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (job / "qa" / "shot_plan_validation.json").write_text(
        json.dumps(
            {
                "passed": True,
                "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
                "project_identity": media_identity(job / "project.json"),
            }
        ),
        encoding="utf-8",
    )
    write_passing_creator_plan_evidence(job, plan)
    return plan


def write_ready_registered_voice(job: Path, plan: dict, registration_paths: dict) -> dict:
    shot = plan["shots"][0]
    shot_id = shot["id"]
    voice = job / "voice" / f"{shot_id}.wav"
    voice.write_bytes(b"exact generated voice bytes")
    registration = registration_paths["registration"]
    reference_hash = media_identity(registration_paths["reference"])["sha256"]
    provenance = voice.with_suffix(".wav.xai.json")
    provenance.write_text(
        json.dumps(
            {
                "provider": "xai",
                "voice_id": "abcd1234",
                "shot_id": shot_id,
                "shot_spec_sha256": shot_spec_sha256(shot),
                "narration_sha256": narration_sha256(shot["narration"]),
                "voice_registration_identity": media_identity(registration),
                "voice_reference_download_verified_at_generation": True,
                "voice_reference_sha256_at_generation": reference_hash,
                "media_identity": media_identity(voice),
            }
        ),
        encoding="utf-8",
    )
    transcript = job / "voice" / "transcripts" / shot_id / "transcript.json"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps(
            {
                "source_media_identity": media_identity(voice),
                "segments": [{"text": shot["narration"]}],
            }
        ),
        encoding="utf-8",
    )
    entry = {
        "id": shot_id,
        "passed": True,
        "shot_spec_sha256": shot_spec_sha256(shot),
        "narration_sha256": narration_sha256(shot["narration"]),
        "voiceover_identity": media_identity(voice),
        "transcript_identity": media_identity(transcript),
        "actual_text": shot["narration"],
    }
    transcript_audit = job / "qa" / "voiceover_audit.json"
    transcript_audit.write_text(
        json.dumps(
            {
                "passed": True,
                "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
                "shots": [entry],
            }
        ),
        encoding="utf-8",
    )
    return {
        "shot": shot,
        "voice": voice,
        "provenance": provenance,
        "transcript": transcript,
        "transcript_audit": transcript_audit,
        "transcript_entry": entry,
        "registration": registration,
        "reference": registration_paths["reference"],
    }


def write_voice_delivery_report(
    job: Path,
    plan: dict,
    evidence: dict,
    status: str = "passed",
    dry_run: bool = False,
) -> Path:
    verdict = passing_voice_delivery_verdict()
    if status == "failed":
        verdict["natural_delivery"] = False
        verdict["issues"] = ["The candidate has an awkward pause before the final phrase."]
    response = json.dumps(verdict, separators=(",", ":")) if status != "inconclusive" else None
    errors = verdict_failures(verdict) if status == "failed" else []
    if status == "inconclusive":
        errors = ["TimeoutError: bounded fixture timeout"]
    report = {
        "schema_version": 1,
        "kind": "voice_delivery_audit",
        "provider": "xai",
        "model": DEFAULT_MODEL,
        "dry_run": dry_run,
        "shot_id": evidence["shot"]["id"],
        "shot_plan_identity": media_identity(job / "plans" / "shot_plan.json"),
        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
        "shot_spec_sha256": shot_spec_sha256(evidence["shot"]),
        "narration_sha256": narration_sha256(evidence["shot"]["narration"]),
        "voice_id": "abcd1234",
        "voiceover_identity": media_identity(evidence["voice"]),
        "voice_provenance_identity": media_identity(evidence["provenance"]),
        "voice_registration_identity": media_identity(evidence["registration"]),
        "reference_identity": media_identity(evidence["reference"]),
        "reference_sample": {
            "start_seconds": 1.0,
            "duration_seconds": 18.0,
            "format": "pcm_s16le_mono_24000hz",
            "pcm_sha256": "a" * 64,
        },
        "transcript_identity": media_identity(evidence["transcript"]),
        "transcript_audit_identity_at_review": media_identity(
            evidence["transcript_audit"]
        ),
        "transcript_audit_entry_sha256": canonical_sha256(
            evidence["transcript_entry"]
        ),
        "review_contract_sha256": sha256_bytes(
            review_instructions(evidence["shot"], evidence["transcript_entry"]).encode()
        ),
        "candidate_signal": {
            "duration_seconds": 1.0,
            "peak_dbfs": -6.0,
            "rms_dbfs": -20.0,
            "dc_offset_fraction": 0.0,
            "clipped_sample_count": 0,
            "severe_step_count": 0,
            "leading_silence_seconds": 0.1,
            "trailing_silence_seconds": 0.1,
            "longest_internal_silence_seconds": 0.2,
        },
        "deterministic_signal_errors": [],
        "attempts": [{"attempt": 1, "parsed": status != "inconclusive"}],
        "status": status,
        "passed": status == "passed",
        "verdict": verdict if response is not None else None,
        "model_response": response,
        "model_response_sha256": sha256_bytes(response.encode()) if response else None,
        "errors": errors,
    }
    path = job / "qa" / "voice_delivery" / f"{evidence['shot']['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


class CompatibilityTests(unittest.TestCase):
    def test_python_scripts_parse_with_python_310_grammar(self):
        for script in SCRIPTS.glob("*.py"):
            with self.subTest(script=script.name):
                ast.parse(script.read_text(encoding="utf-8"), filename=str(script), feature_version=(3, 10))

    def test_windows_setup_installs_pinned_grok_audio_dependency(self):
        setup = (SCRIPTS / "setup_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("websockets==15.0.1", setup)


class ProductionEvidenceTests(unittest.TestCase):
    def test_voice_registration_tracks_exact_reviewed_reference_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = write_voice_registration_fixture(Path(temporary))
            self.assertEqual(
                voice_registration_errors(paths["registration"], "abcd1234"),
                [],
            )
            paths["reference"].write_bytes(b"changed after registration")
            errors = voice_registration_errors(paths["registration"], "abcd1234")
            self.assertTrue(any("stale reference_identity" in error for error in errors))

    def test_voice_provenance_binds_voice_id_registration_and_live_source_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = write_voice_registration_fixture(root)
            voice = root / "shot-001.wav"
            voice.write_bytes(b"generated voice bytes")
            shot = {"id": "shot-001", "narration": "Use the exact owner voice."}
            reference_hash = media_identity(paths["reference"])["sha256"]
            sidecar = {
                "provider": "xai",
                "voice_id": "abcd1234",
                "shot_id": "shot-001",
                "shot_spec_sha256": shot_spec_sha256(shot),
                "narration_sha256": narration_sha256(shot["narration"]),
                "voice_registration_identity": media_identity(paths["registration"]),
                "voice_reference_download_verified_at_generation": True,
                "voice_reference_sha256_at_generation": reference_hash,
                "media_identity": media_identity(voice),
            }
            voice.with_suffix(".wav.xai.json").write_text(
                json.dumps(sidecar), encoding="utf-8"
            )
            self.assertEqual(
                xai_voice_provenance_errors(
                    shot,
                    voice,
                    expected_voice_id="abcd1234",
                    registration=paths["registration"],
                ),
                [],
            )
            sidecar["voice_reference_download_verified_at_generation"] = False
            voice.with_suffix(".wav.xai.json").write_text(
                json.dumps(sidecar), encoding="utf-8"
            )
            errors = xai_voice_provenance_errors(
                shot,
                voice,
                expected_voice_id="abcd1234",
                registration=paths["registration"],
            )
            self.assertTrue(any("reverify xAI source audio" in error for error in errors))

    def test_mutable_review_fields_do_not_change_shot_spec_hash(self):
        shot = {
            "id": "shot-001",
            "story_role": "hook",
            "viewer_purpose": "State the result.",
            "rationale": "Direct opening.",
            "continuity": "Starts the video.",
            "narration": "Lower your ping with these settings.",
            "computer_actions": ["Show Network Connections"],
            "required_visual_state": "Ethernet adapter is visible.",
            "timing_mode": "fit",
            "maximum_recording_seconds": 5.0,
        }
        original = shot_spec_sha256(shot)
        shot["recording_review"] = {"passed": True}
        shot["voice_review"] = {"passed": True}
        shot["video"] = "new-location.mp4"
        shot["voiceover"] = "new-location.wav"
        self.assertEqual(original, shot_spec_sha256(shot))
        shot["narration"] = "This changed approved narration."
        self.assertNotEqual(original, shot_spec_sha256(shot))

    def test_media_change_invalidates_sealed_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "shot.mp4"
            media.write_bytes(b"reviewed bytes")
            shot = {
                "id": "shot-001",
                "story_role": "hook",
                "viewer_purpose": "State the result.",
                "rationale": "Direct opening.",
                "continuity": "Starts the video.",
                "narration": "Test line.",
                "computer_actions": ["Show state"],
                "required_visual_state": "State is visible.",
                "timing_mode": "fit",
                "maximum_recording_seconds": 2.0,
            }
            review = {
                "kind": "recording_review",
                "shot_id": "shot-001",
                "shot_spec_sha256": shot_spec_sha256(shot),
                "media_identity": media_identity(media),
                "passed": True,
                "evidence": [{"time": 0.5}],
                "verdict": {
                    "required_visual_state_visible": True,
                    "no_private_information": True,
                    "cursor_deliberate": True,
                    "ui_readable": True,
                    "actions_complete": True,
                    "notes": "The reviewed state is visible and readable.",
                },
            }
            self.assertEqual(validate_sealed_review(review, "recording", shot, media), [])
            media.write_bytes(b"changed after review")
            errors = validate_sealed_review(review, "recording", shot, media)
            self.assertTrue(any("current media bytes" in error for error in errors))

    def test_xai_voice_provenance_invalidates_changed_narration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            voice = root / "shot-001.wav"
            voice.write_bytes(b"generated voice bytes")
            shot = {
                "id": "shot-001",
                "story_role": "hook",
                "viewer_purpose": "State the result.",
                "rationale": "Direct opening.",
                "continuity": "Starts the video.",
                "narration": "Use the current generated line.",
                "computer_actions": ["Show the setting"],
                "required_visual_state": "The setting is visible.",
                "timing_mode": "fit",
                "maximum_recording_seconds": 4.0,
            }
            sidecar = voice.with_suffix(voice.suffix + ".xai.json")
            sidecar.write_text(
                json.dumps(
                    {
                        "provider": "xai",
                        "shot_id": shot["id"],
                        "shot_spec_sha256": shot_spec_sha256(shot),
                        "narration_sha256": narration_sha256(shot["narration"]),
                        "media_identity": media_identity(voice),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(xai_voice_provenance_errors(shot, voice), [])
            shot["narration"] = "This approved line changed."
            errors = xai_voice_provenance_errors(shot, voice)
            self.assertTrue(any("shot specification changed" in error for error in errors))
            self.assertTrue(any("approved narration" in error for error in errors))

    def test_schema_four_voice_provenance_requires_verified_in_range_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            voice = root / "shot-001.wav"
            voice.write_bytes(b"generated voice bytes")
            shot = {
                "id": "shot-001",
                "narration": "Open the network settings now.",
                "voice_performance": {
                    "tts_text": "Open the network settings [pause] now.",
                    "speed": 1.05,
                    "target_words_per_minute": {"minimum": 210, "maximum": 260},
                },
            }
            sidecar = voice.with_suffix(".wav.xai.json")
            metadata = {
                "provider": "xai",
                "shot_id": "shot-001",
                "shot_spec_sha256": shot_spec_sha256(shot),
                "narration_sha256": narration_sha256(shot["narration"]),
                "tts_text_sha256": narration_sha256(
                    shot["voice_performance"]["tts_text"]
                ),
                "requested_speed": 1.05,
                "voice_verified_at_generation": True,
                "cadence": {"within_target": True},
                "media_identity": media_identity(voice),
            }
            sidecar.write_text(json.dumps(metadata), encoding="utf-8")
            self.assertEqual(xai_voice_provenance_errors(shot, voice), [])

            metadata["cadence"]["within_target"] = False
            sidecar.write_text(json.dumps(metadata), encoding="utf-8")
            errors = xai_voice_provenance_errors(shot, voice)
            self.assertTrue(any("cadence is outside" in error for error in errors))

    def test_transcript_source_identity_invalidates_changed_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            voice = root / "shot.wav"
            transcript = root / "transcript.json"
            voice.write_bytes(b"first voice")
            transcript.write_text(
                json.dumps(
                    {
                        "source_media_identity": media_identity(voice),
                        "segments": [{"text": "First voice."}],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(transcript_source_errors(transcript, voice, require_identity=True), [])
            voice.write_bytes(b"replacement voice")
            errors = transcript_source_errors(transcript, voice, require_identity=True)
            self.assertTrue(any("different source-media bytes" in error for error in errors))

    def test_version_three_plan_requires_claim_and_retake_reasoning(self):
        shot = {
            "id": "shot-001",
            "story_role": "hook",
            "viewer_purpose": "Promise the tutorial result.",
            "rationale": "Open directly.",
            "continuity": "Starts the video.",
            "narration": "Today I'm showing you how to lower your ping.",
            "computer_actions": ["Show the network settings result"],
            "required_visual_state": "The network settings are readable.",
            "timing_mode": "fit",
            "maximum_recording_seconds": 5.0,
            "target_box": None,
            "include_boxes": [],
        }
        plan = {"schema_version": 3, "shots": [shot]}
        project = {"required_story_roles": ["hook"]}
        missing = validate_shot_plan(plan, project)
        self.assertFalse(missing["passed"])
        self.assertTrue(any("claim_support" in error for error in missing["errors"]))
        self.assertTrue(any("retake_triggers" in error for error in missing["errors"]))

        shot.update(
            {
                "claim_support": {
                    "type": "hook",
                    "spoken_claim": "The tutorial will lower ping.",
                    "visible_evidence": "The network settings supporting the tutorial are visible.",
                },
                "capture_checkpoints": ["Network settings are open and readable."],
                "retake_triggers": ["Retake if the settings are obscured or cropped."],
                "creator_style_rationale": "Uses Colin's direct outcome-first hook without copying an old line.",
            }
        )
        passing = validate_shot_plan(plan, project)
        self.assertTrue(passing["passed"], passing["errors"])

    def test_version_four_plan_binds_spoken_words_and_luna_voice_direction(self):
        narration = "Today I'm showing you how to lower your ping."
        shot = {
            "id": "shot-001",
            "story_role": "hook",
            "viewer_purpose": "Promise the tutorial result.",
            "rationale": "Open directly.",
            "continuity": "Starts the video.",
            "narration": narration,
            "computer_actions": ["Show the network result"],
            "required_visual_state": "The network result is readable.",
            "timing_mode": "fit",
            "maximum_recording_seconds": 5.0,
            "target_box": None,
            "include_boxes": [],
            "claim_support": {
                "type": "hook",
                "spoken_claim": "The tutorial will lower ping.",
                "visible_evidence": "The network result is visible.",
            },
            "capture_checkpoints": ["The network result is readable."],
            "retake_triggers": ["Retake if the result is cropped."],
            "creator_style_rationale": "Uses Colin's direct outcome-first hook.",
            "voice_performance": {
                "tts_text": "Today I'm showing you [pause] how to lower your ping.",
                "speed": 1.05,
                "target_words_per_minute": {"minimum": 220, "maximum": 270},
                "delivery_intent": "Direct and conversational, without an announcer voice.",
                "pronunciation_checks": ["ping"],
                "retake_triggers": ["Retake if the pause sounds hesitant."],
            },
        }
        plan = {"schema_version": 4, "shots": [shot]}
        project = {"required_story_roles": ["hook"]}
        passing = validate_shot_plan(plan, project)
        self.assertTrue(passing["passed"], passing["errors"])

        shot["voice_performance"]["tts_text"] += " Extra words."
        changed_words = validate_shot_plan(plan, project)
        self.assertTrue(any("exactly the approved narration" in error for error in changed_words["errors"]))

        shot["voice_performance"]["tts_text"] = "Today I'm showing you [laugh] how to lower your ping."
        overacted = validate_shot_plan(plan, project)
        self.assertTrue(any("not approved for Luna" in error for error in overacted["errors"]))

        shot["voice_performance"]["tts_text"] = (
            "Today I'm showing you <emphasis>how to lower your ping."
        )
        unbalanced = validate_shot_plan(plan, project)
        self.assertTrue(any("unclosed speech tags" in error for error in unbalanced["errors"]))

    def test_recording_review_seals_extracted_frame_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "shot.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=green:s=320x180:r=30:d=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(video),
                ],
                check=True,
            )
            plan = root / "shot_plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "shots": [
                            {
                                "id": "shot-001",
                                "story_role": "hook",
                                "viewer_purpose": "Show proof.",
                                "rationale": "Visible fixture.",
                                "continuity": "Starts the fixture.",
                                "narration": "The green state is visible.",
                                "computer_actions": ["Show green state"],
                                "required_visual_state": "Green state is visible.",
                                "timing_mode": "fit",
                                "maximum_recording_seconds": 2.0,
                                "video": str(video),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            report = root / "qa" / "reviews" / "recording" / "shot-001.json"
            result = run_script(
                "seal_production_review.py",
                "recording",
                "--shot-plan",
                str(plan),
                "--shot-id",
                "shot-001",
                "--evidence-time",
                "0.5",
                "--required-visual-state-visible",
                "--no-private-information",
                "--cursor-deliberate",
                "--ui-readable",
                "--actions-complete",
                "--notes",
                "The green state is centered, readable, and free of private data.",
                "--output",
                str(report),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            sealed = json.loads(report.read_text())
            self.assertTrue(sealed["passed"])
            self.assertTrue(Path(sealed["evidence"][0]["frame"]).is_file())


class VoiceDeliveryAuditTests(unittest.TestCase):
    def test_deterministic_signal_gate_rejects_clipping_and_long_dead_air(self):
        clean = array("h", [0, 500, -500, 800, -800] * 2400).tobytes()
        self.assertEqual(signal_failures(pcm_signal_metrics(clean)), [])

        clipped = array("h", [32767] * 24000).tobytes()
        failures = signal_failures(pcm_signal_metrics(clipped))
        self.assertTrue(any("clipped PCM" in error for error in failures))
        self.assertTrue(any("DC offset" in error for error in failures))

    def test_strict_verdict_accepts_plain_or_fenced_json_and_rejects_uncertainty(self):
        verdict = passing_voice_delivery_verdict()
        encoded = json.dumps(verdict)
        self.assertEqual(strict_verdict(encoded), verdict)
        self.assertEqual(strict_verdict(f"```json\n{encoded}\n```"), verdict)

        uncertain = dict(verdict)
        uncertain["confident_verdict"] = False
        uncertain["issues"] = ["The identity comparison is not confident enough to approve."]
        self.assertTrue(verdict_failures(strict_verdict(json.dumps(uncertain))))

        malformed = dict(verdict)
        malformed.pop("speaker_identity_match")
        with self.assertRaisesRegex(ValueError, "missing fields"):
            strict_verdict(json.dumps(malformed))
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            strict_verdict("Here is the verdict: " + encoded)

    def test_realtime_collector_avoids_alias_duplication_and_has_transcript_fallback(self):
        class FakeWebSocket:
            def __init__(self, events):
                self.events = list(events)

            async def recv(self):
                return json.dumps(self.events.pop(0))

        preferred = FakeWebSocket(
            [
                {"type": "response.text.delta", "delta": "duplicate alias"},
                {"type": "response.output_text.delta", "delta": "exact JSON"},
                {"type": "response.done", "response": {"status": "completed"}},
            ]
        )
        self.assertEqual(asyncio.run(collect_response_text(preferred, 1)), "exact JSON")

        fallback = FakeWebSocket(
            [
                {
                    "type": "response.output_audio_transcript.delta",
                    "delta": "fallback transcript",
                },
                {"type": "response.done", "response": {"status": "completed"}},
            ]
        )
        self.assertEqual(
            asyncio.run(collect_response_text(fallback, 1)),
            "fallback transcript",
        )

        failed = FakeWebSocket(
            [{"type": "error", "error": {"message": "fixture failure"}}]
        )
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            asyncio.run(collect_response_text(failed, 1))

    def test_delivery_report_is_bound_to_exact_audio_and_dry_runs_never_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            plan = write_schema_three_director_job(job)
            registration = write_voice_registration_fixture(job)
            evidence = write_ready_registered_voice(job, plan, registration)
            report = write_voice_delivery_report(job, plan, evidence)
            status = voice_delivery_audit_status(
                report,
                plan,
                evidence["shot"],
                evidence["voice"],
                evidence["transcript_audit"],
                evidence["registration"],
            )
            self.assertEqual(status["outcome"], "passed", status["errors"])

            write_voice_delivery_report(job, plan, evidence, dry_run=True)
            dry_run = voice_delivery_audit_status(
                report,
                plan,
                evidence["shot"],
                evidence["voice"],
                evidence["transcript_audit"],
                evidence["registration"],
            )
            self.assertEqual(dry_run["outcome"], "stale")
            self.assertTrue(any("Dry-run" in error for error in dry_run["errors"]))

            write_voice_delivery_report(job, plan, evidence)
            evidence["voice"].write_bytes(b"changed after Grok listened")
            changed = voice_delivery_audit_status(
                report,
                plan,
                evidence["shot"],
                evidence["voice"],
                evidence["transcript_audit"],
                evidence["registration"],
            )
            self.assertEqual(changed["outcome"], "stale")
            self.assertTrue(any("different voiceover bytes" in error for error in changed["errors"]))

    def test_model_sealer_requires_current_passing_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            plan = write_schema_three_director_job(job)
            registration = write_voice_registration_fixture(job)
            evidence = write_ready_registered_voice(job, plan, registration)
            delivery = write_voice_delivery_report(job, plan, evidence)
            output = job / "qa" / "reviews" / "voice" / "shot-001.json"
            sealed = run_script(
                "seal_production_review.py",
                "voice-model",
                "--shot-plan",
                str(job / "plans" / "shot_plan.json"),
                "--shot-id",
                "shot-001",
                "--voiceover",
                str(evidence["voice"]),
                "--transcript-audit",
                str(evidence["transcript_audit"]),
                "--delivery-audit",
                str(delivery),
                "--voice-registration",
                str(evidence["registration"]),
                "--output",
                str(output),
            )
            self.assertEqual(sealed.returncode, 0, sealed.stdout + sealed.stderr)
            review = json.loads(output.read_text())
            self.assertEqual(review["reviewer"]["kind"], "xai_audio_model")
            self.assertEqual(
                validate_sealed_review(
                    review,
                    "voice",
                    evidence["shot"],
                    evidence["voice"],
                ),
                [],
            )

            write_voice_delivery_report(job, plan, evidence, status="failed")
            invalidated = validate_sealed_review(
                review,
                "voice",
                evidence["shot"],
                evidence["voice"],
            )
            self.assertTrue(any("stale audit bytes" in error for error in invalidated))
            refused = run_script(
                "seal_production_review.py",
                "voice-model",
                "--shot-plan",
                str(job / "plans" / "shot_plan.json"),
                "--shot-id",
                "shot-001",
                "--voiceover",
                str(evidence["voice"]),
                "--transcript-audit",
                str(evidence["transcript_audit"]),
                "--delivery-audit",
                str(delivery),
                "--voice-registration",
                str(evidence["registration"]),
                "--output",
                str(output),
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("cannot be sealed", refused.stderr)

    def test_dry_run_decodes_exact_media_but_cannot_create_passing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            plan = write_schema_three_director_job(job)
            paths = write_voice_registration_fixture(job)
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=220:duration=4",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-c:a",
                    "pcm_s16le",
                    str(paths["reference"]),
                ],
                check=True,
            )
            review = json.loads(paths["review"].read_text())
            review["reference_identity"] = media_identity(paths["reference"])
            paths["review"].write_text(json.dumps(review), encoding="utf-8")
            registration = json.loads(paths["registration"].read_text())
            registration["reference_identity"] = media_identity(paths["reference"])
            registration["reference_review_identity"] = media_identity(paths["review"])
            registration["downloaded_reference_sha256"] = media_identity(
                paths["reference"]
            )["sha256"]
            paths["registration"].write_text(json.dumps(registration), encoding="utf-8")

            evidence = write_ready_registered_voice(job, plan, paths)
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=330:duration=1.2",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-c:a",
                    "pcm_s16le",
                    str(evidence["voice"]),
                ],
                check=True,
            )
            provenance = json.loads(evidence["provenance"].read_text())
            provenance["media_identity"] = media_identity(evidence["voice"])
            evidence["provenance"].write_text(json.dumps(provenance), encoding="utf-8")
            evidence["transcript"].write_text(
                json.dumps(
                    {
                        "source_media_identity": media_identity(evidence["voice"]),
                        "segments": [{"text": evidence["shot"]["narration"]}],
                    }
                ),
                encoding="utf-8",
            )
            entry = evidence["transcript_entry"]
            entry["voiceover_identity"] = media_identity(evidence["voice"])
            entry["transcript_identity"] = media_identity(evidence["transcript"])
            evidence["transcript_audit"].write_text(
                json.dumps(
                    {
                        "passed": True,
                        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
                        "shots": [entry],
                    }
                ),
                encoding="utf-8",
            )
            report = job / "qa" / "voice_delivery" / "shot-001.json"
            result = run_script(
                "audit_voice_delivery.py",
                "--shot-plan",
                str(job / "plans" / "shot_plan.json"),
                "--shot-id",
                "shot-001",
                "--voiceover",
                str(evidence["voice"]),
                "--transcript-audit",
                str(evidence["transcript_audit"]),
                "--voice-registration",
                str(evidence["registration"]),
                "--reference-seconds",
                "3",
                "--dry-run",
                "--report",
                str(report),
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(report.read_text())
            self.assertTrue(payload["dry_run"])
            self.assertFalse(payload["passed"])
            self.assertRegex(payload["reference_sample"]["pcm_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(payload["candidate_pcm_sha256"], r"^[0-9a-f]{64}$")


class ProductionDirectorTests(unittest.TestCase):
    def test_automatic_action_cannot_succeed_without_its_required_evidence_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            expected = Path(temporary) / "audit.json"
            action = {"required_outputs": [str(expected)]}
            self.assertEqual(missing_required_outputs(action), [str(expected)])
            expected.write_text("{}", encoding="utf-8")
            self.assertEqual(missing_required_outputs(action), [])

    def test_schema_three_requires_exact_owner_reference_chain(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            write_schema_three_director_job(job)
            with patch.dict("os.environ", {}, clear=True):
                status = derive_status(job)
            self.assertEqual(status["next"]["action"], "prepare_and_review_xai_reference")
            self.assertEqual(
                set(status["next"]["missing"]),
                {"reference", "preparation_report", "transcript", "review"},
            )

    def test_schema_three_registers_reviewed_reference_before_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            write_schema_three_director_job(job)
            paths = write_voice_registration_fixture(job)
            paths["registration"].unlink()
            with (
                patch(
                    "production_director.validate_review_evidence",
                    return_value=({}, [], []),
                ),
                patch.dict(
                    "os.environ",
                    {"XAI_API_KEY": "test-key", "XAI_VOICE_ID": "abcd1234"},
                    clear=True,
                ),
            ):
                status = derive_status(job)
            self.assertEqual(status["next"]["action"], "register_exact_xai_voice")
            command = status["next"]["command"]
            self.assertEqual(command[command.index("--voice-id") + 1], "abcd1234")
            self.assertEqual(
                Path(command[command.index("--reference-review") + 1]).resolve(),
                paths["review"].resolve(),
            )

    def test_schema_three_generation_uses_sealed_registration_without_env_voice_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            write_schema_three_director_job(job)
            paths = write_voice_registration_fixture(job)
            with (
                patch(
                    "production_director.validate_review_evidence",
                    return_value=({}, [], []),
                ),
                patch.dict("os.environ", {"XAI_API_KEY": "test-key"}, clear=True),
            ):
                status = derive_status(job)
            self.assertEqual(status["next"]["action"], "generate_xai_voiceovers")
            command = status["next"]["command"]
            self.assertEqual(command[command.index("--voice-id") + 1], "abcd1234")
            self.assertEqual(
                Path(command[command.index("--voice-registration") + 1]).resolve(),
                paths["registration"].resolve(),
            )
            self.assertEqual(command[command.index("--shot-id") + 1], "shot-001")

    def test_director_runs_grok_audio_audit_then_seals_passing_exact_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            plan = write_schema_three_director_job(job)
            registration = write_voice_registration_fixture(job)
            evidence = write_ready_registered_voice(job, plan, registration)
            patches = (
                patch(
                    "production_director.validate_review_evidence",
                    return_value=({}, [], []),
                ),
                patch("production_director.transcriber_python", return_value=Path(sys.executable)),
                patch("production_director.python_module_available", return_value=True),
                patch.dict("os.environ", {"XAI_API_KEY": "test-key"}, clear=True),
            )
            with patches[0], patches[1], patches[2], patches[3]:
                missing = derive_status(job)
            self.assertEqual(missing["next"]["action"], "audit_grok_voice_delivery")
            self.assertEqual(
                missing["next"]["command"][
                    missing["next"]["command"].index("--model") + 1
                ],
                DEFAULT_MODEL,
            )

            write_voice_delivery_report(job, plan, evidence)
            with (
                patch(
                    "production_director.validate_review_evidence",
                    return_value=({}, [], []),
                ),
                patch.dict("os.environ", {"XAI_API_KEY": "test-key"}, clear=True),
            ):
                passing = derive_status(job)
            self.assertEqual(passing["next"]["action"], "seal_grok_voice_review")
            self.assertIn("voice-model", passing["next"]["command"])

    def test_director_repairs_failed_grok_delivery_and_keeps_human_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            plan = write_schema_three_director_job(job)
            registration = write_voice_registration_fixture(job)
            evidence = write_ready_registered_voice(job, plan, registration)
            write_voice_delivery_report(job, plan, evidence, status="failed")
            with (
                patch(
                    "production_director.validate_review_evidence",
                    return_value=({}, [], []),
                ),
                patch.dict("os.environ", {"XAI_API_KEY": "test-key"}, clear=True),
            ):
                failed = derive_status(job)
            self.assertEqual(failed["next"]["action"], "repair_failed_voice_delivery")
            self.assertTrue(
                any("awkward pause" in error for error in failed["next"]["errors"])
            )

            write_voice_delivery_report(job, plan, evidence, status="inconclusive")
            with (
                patch(
                    "production_director.validate_review_evidence",
                    return_value=({}, [], []),
                ),
                patch.dict("os.environ", {"XAI_API_KEY": "test-key"}, clear=True),
            ):
                inconclusive = derive_status(job)
            self.assertEqual(
                inconclusive["next"]["action"],
                "listen_and_seal_voice_review",
            )
            self.assertEqual(
                inconclusive["next"]["automated_review"]["outcome"],
                "inconclusive",
            )

    def test_accepted_delivery_must_equal_the_current_qa_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accepted = root / "delivery.mp4"
            candidate = root / "candidate.mp4"
            accepted.write_bytes(b"accepted bytes")
            candidate.write_bytes(b"new candidate bytes")
            manifest = {
                "status": "accepted",
                "final_output": str(accepted),
                "final_identity": media_identity(accepted),
            }
            self.assertFalse(accepted_delivery_is_current(manifest, candidate))
            candidate.write_bytes(accepted.read_bytes())
            self.assertTrue(accepted_delivery_is_current(manifest, candidate))

    def test_fresh_manifest_has_no_accepted_output_path(self):
        self.assertIsNone(manifest_final_path({"final_output": None}))
        self.assertIsNone(manifest_final_path({"final_output": ""}))

    def test_qa_report_becomes_stale_when_a_review_is_added(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate.mp4"
            report = root / "qa.json"
            review = root / "visual-review.json"
            candidate.write_bytes(b"candidate")
            report.write_text("{}", encoding="utf-8")
            self.assertTrue(report_is_fresh(report, [candidate]))
            review.write_text("{}", encoding="utf-8")
            report_time = report.stat().st_mtime_ns
            review_time = max(review.stat().st_mtime_ns, report_time + 1_000_000)
            import os

            os.utime(review, ns=(review_time, review_time))
            self.assertFalse(report_is_fresh(report, [candidate, review]))

    def test_project_change_invalidates_creator_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project.json"
            profile = root / "profile.json"
            report = root / "creator.json"
            project.write_text('{"target":180}', encoding="utf-8")
            profile.write_text('{"channel":"Luna Tweak"}', encoding="utf-8")
            plan = {"schema_version": 2, "shots": []}
            report.write_text(
                json.dumps(
                    {
                        "mode": "plan",
                        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
                        "project_identity": media_identity(project),
                        "channel_profile_identity": media_identity(profile),
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                creator_report_is_current(report, "plan", plan, project, profile)
            )
            project.write_text('{"target":300}', encoding="utf-8")
            self.assertFalse(
                creator_report_is_current(report, "plan", plan, project, profile)
            )

    def test_transcriber_keeps_virtual_environment_launcher_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_python = root / "base-python"
            base_python.touch()
            venv_python = root / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(base_python)
            with patch.dict(
                "os.environ",
                {"LUNA_EDITOR_TRANSCRIBE_PYTHON": str(venv_python)},
                clear=False,
            ):
                selected = transcriber_python()
            self.assertEqual(selected, venv_python.absolute())
            self.assertNotEqual(selected, base_python.resolve())

    def test_resume_validates_plan_then_requests_voice_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialized = run_script(
                "luna_editor.py",
                "init",
                "--mode",
                "synthetic",
                "--title",
                "Director Test",
                "--jobs-root",
                str(root),
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            job = Path(initialized.stdout.strip())
            project = json.loads((job / "project.json").read_text())
            project["required_story_roles"] = ["hook"]
            (job / "project.json").write_text(json.dumps(project), encoding="utf-8")
            plan = {
                "schema_version": 2,
                "title": "Director Test",
                "story": "Hook fixture.",
                "shots": [
                    {
                        "id": "shot-001",
                        "story_role": "hook",
                        "viewer_purpose": "State the result.",
                        "rationale": "Direct opening.",
                        "continuity": "Starts the video.",
                        "narration": "This is the director test.",
                        "computer_actions": ["Show the desktop"],
                        "required_visual_state": "Desktop is visible.",
                        "timing_mode": "fit",
                        "maximum_recording_seconds": 4.0,
                        "target_box": None,
                        "include_boxes": [],
                    }
                ],
            }
            (job / "plans" / "shot_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            resumed = run_script(
                "production_director.py",
                "--job",
                str(job),
                "--execute-safe",
            )
            self.assertEqual(resumed.returncode, 2, resumed.stdout + resumed.stderr)
            status = json.loads(resumed.stdout)
            self.assertEqual(status["next"]["action"], "confirm_voice_ownership")
            self.assertEqual(status["executed"][0]["action"], "validate_shot_plan")

    def test_changed_narration_routes_stale_xai_voice_to_regeneration(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            (job / "plans").mkdir()
            (job / "qa").mkdir()
            (job / "voice").mkdir()
            project = {
                "mode": "synthetic",
                "required_story_roles": ["hook"],
                "voice": {"provider": "xai", "owner_consent": "confirmed"},
            }
            (job / "project.json").write_text(json.dumps(project), encoding="utf-8")
            original_shot = {
                "id": "shot-001",
                "story_role": "hook",
                "viewer_purpose": "State the result.",
                "rationale": "Direct opening.",
                "continuity": "Starts the video.",
                "narration": "This was the original line.",
                "computer_actions": ["Show the desktop"],
                "required_visual_state": "Desktop is visible.",
                "timing_mode": "fit",
                "maximum_recording_seconds": 4.0,
            }
            voice = job / "voice" / "shot-001.wav"
            voice.write_bytes(b"old generated voice")
            voice.with_suffix(".wav.xai.json").write_text(
                json.dumps(
                    {
                        "provider": "xai",
                        "shot_id": "shot-001",
                        "shot_spec_sha256": shot_spec_sha256(original_shot),
                        "narration_sha256": narration_sha256(original_shot["narration"]),
                        "media_identity": media_identity(voice),
                    }
                ),
                encoding="utf-8",
            )
            current_shot = dict(original_shot)
            current_shot["narration"] = "This is the newly approved line."
            plan = {
                "schema_version": 2,
                "title": "Voice provenance test",
                "story": "A changed line must regenerate.",
                "shots": [current_shot],
            }
            (job / "plans" / "shot_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            (job / "qa" / "shot_plan_validation.json").write_text(
                json.dumps(
                    {
                        "passed": True,
                        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
                        "project_identity": media_identity(job / "project.json"),
                    }
                ),
                encoding="utf-8",
            )
            write_passing_creator_plan_evidence(job, plan)
            with patch.dict("os.environ", {}, clear=True):
                status = derive_status(job)
            self.assertEqual(status["next"]["action"], "configure_verified_xai_voice")
            errors = status["next"]["provenance_errors"]["shot-001"]
            self.assertTrue(any("shot specification changed" in error for error in errors))

            with patch.dict(
                "os.environ",
                {"XAI_API_KEY": "test-key", "XAI_VOICE_ID": "abcd1234"},
                clear=True,
            ):
                configured = derive_status(job)
            self.assertEqual(configured["next"]["action"], "generate_xai_voiceovers")
            command = configured["next"]["command"]
            selected = [
                command[index + 1]
                for index, value in enumerate(command[:-1])
                if value == "--shot-id"
            ]
            self.assertEqual(selected, ["shot-001"])

    def test_out_of_range_generated_cadence_routes_to_direction_instead_of_looping(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            (job / "plans").mkdir()
            (job / "qa").mkdir()
            (job / "voice").mkdir()
            project = {
                "mode": "synthetic",
                "required_story_roles": ["hook"],
                "voice": {"provider": "xai", "owner_consent": "confirmed"},
            }
            (job / "project.json").write_text(json.dumps(project), encoding="utf-8")
            shot = {
                "id": "shot-001",
                "story_role": "hook",
                "viewer_purpose": "State the result.",
                "rationale": "Direct opening.",
                "continuity": "Starts the video.",
                "narration": "Today I'm showing you the network fix.",
                "computer_actions": ["Show the network result"],
                "required_visual_state": "The network result is visible.",
                "timing_mode": "fit",
                "maximum_recording_seconds": 5.0,
                "target_box": None,
                "include_boxes": [],
                "claim_support": {
                    "type": "hook",
                    "spoken_claim": "The video shows the network fix.",
                    "visible_evidence": "The network result is visible.",
                },
                "capture_checkpoints": ["The network result is readable."],
                "retake_triggers": ["Retake if the result is cropped."],
                "creator_style_rationale": "Uses a direct Luna outcome-first hook.",
                "voice_performance": {
                    "tts_text": "Today I'm showing you [pause] the network fix.",
                    "speed": 1.05,
                    "target_words_per_minute": {"minimum": 220, "maximum": 270},
                    "delivery_intent": "Direct and conversational.",
                    "pronunciation_checks": ["network"],
                    "retake_triggers": ["Retake if cadence sounds hesitant."],
                },
            }
            plan = {
                "schema_version": 4,
                "title": "Cadence routing test",
                "story": "A hook with measured delivery.",
                "shots": [shot],
            }
            (job / "plans" / "shot_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            (job / "qa" / "shot_plan_validation.json").write_text(
                json.dumps(
                    {
                        "passed": True,
                        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
                        "project_identity": media_identity(job / "project.json"),
                    }
                ),
                encoding="utf-8",
            )
            write_passing_creator_plan_evidence(job, plan)
            voice = job / "voice" / "shot-001.wav"
            voice.write_bytes(b"out of range generated voice")
            voice.with_suffix(".wav.xai.json").write_text(
                json.dumps(
                    {
                        "provider": "xai",
                        "shot_id": "shot-001",
                        "shot_spec_sha256": shot_spec_sha256(shot),
                        "narration_sha256": narration_sha256(shot["narration"]),
                        "tts_text_sha256": narration_sha256(
                            shot["voice_performance"]["tts_text"]
                        ),
                        "requested_speed": 1.05,
                        "voice_verified_at_generation": True,
                        "cadence": {"within_target": False},
                        "media_identity": media_identity(voice),
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                status = derive_status(job)
            self.assertEqual(status["next"]["action"], "adjust_voice_performance")
            self.assertIn("shot-001", status["next"]["failed_shots"])

    def test_changed_voice_bytes_route_back_to_transcription(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            (job / "plans").mkdir()
            (job / "qa").mkdir()
            (job / "voice" / "transcripts" / "shot-001").mkdir(parents=True)
            project = {
                "mode": "synthetic",
                "required_story_roles": ["hook"],
                "voice": {"provider": "accepted-fixture", "owner_consent": "confirmed"},
            }
            (job / "project.json").write_text(json.dumps(project), encoding="utf-8")
            shot = {
                "id": "shot-001",
                "story_role": "hook",
                "viewer_purpose": "State the result.",
                "rationale": "Direct opening.",
                "continuity": "Starts the video.",
                "narration": "This line is transcribed.",
                "computer_actions": ["Show the desktop"],
                "required_visual_state": "Desktop is visible.",
                "timing_mode": "fit",
                "maximum_recording_seconds": 4.0,
            }
            plan = {
                "schema_version": 2,
                "title": "Transcript provenance test",
                "story": "Changed audio must be transcribed again.",
                "shots": [shot],
            }
            (job / "plans" / "shot_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            (job / "qa" / "shot_plan_validation.json").write_text(
                json.dumps(
                    {
                        "passed": True,
                        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
                        "project_identity": media_identity(job / "project.json"),
                    }
                ),
                encoding="utf-8",
            )
            write_passing_creator_plan_evidence(job, plan)
            voice = job / "voice" / "shot-001.wav"
            voice.write_bytes(b"first voice bytes")
            transcript = job / "voice" / "transcripts" / "shot-001" / "transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "source_media_identity": media_identity(voice),
                        "segments": [{"text": shot["narration"]}],
                    }
                ),
                encoding="utf-8",
            )
            voice.write_bytes(b"replacement voice bytes")
            with patch.dict(
                "os.environ",
                {"LUNA_EDITOR_TRANSCRIBE_PYTHON": sys.executable},
                clear=True,
            ):
                status = derive_status(job)
            self.assertEqual(status["next"]["action"], "transcribe_voiceover")
            errors = status["next"]["provenance_errors"]
            self.assertTrue(any("different source-media bytes" in error for error in errors))


class VoiceReferenceTests(unittest.TestCase):
    def test_reference_preparation_requires_consent_and_creates_xai_length_wav(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "accepted.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=91",
                    "-c:a",
                    "pcm_s16le",
                    str(source),
                ],
                check=True,
            )
            output = root / "reference.wav"
            report = root / "report.json"
            denied = run_script(
                "prepare_voice_reference.py",
                "--input",
                str(source),
                "--output",
                str(output),
                "--report",
                str(report),
                "--target-seconds",
                "90",
                "--maximum-seconds",
                "90",
            )
            self.assertNotEqual(denied.returncode, 0)
            allowed = run_script(
                "prepare_voice_reference.py",
                "--input",
                str(source),
                "--output",
                str(output),
                "--report",
                str(report),
                "--target-seconds",
                "90",
                "--maximum-seconds",
                "90",
                "--owner-consent-confirmed",
            )
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
            payload = json.loads(report.read_text())
            self.assertGreaterEqual(payload["output_duration_seconds"], 89.9)
            self.assertLessEqual(payload["output_duration_seconds"], 90.1)
            self.assertTrue(payload["manual_listening_review_required"])
            self.assertFalse(payload["upload_ready"])
            sample_rate = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=sample_rate",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(sample_rate.stdout.strip(), "24000")

    def test_reference_preparation_rejects_stale_selection_transcript(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "accepted.wav"
            source.write_bytes(b"accepted source identity")
            other = root / "other.wav"
            other.write_bytes(b"different source identity")
            transcript = root / "transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "source_media_identity": media_identity(other),
                        "segments": [],
                    }
                ),
                encoding="utf-8",
            )
            result = run_script(
                "prepare_voice_reference.py",
                "--input",
                str(source),
                "--transcript-json",
                str(transcript),
                "--output",
                str(root / "reference.wav"),
                "--report",
                str(root / "report.json"),
                "--owner-consent-confirmed",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale or unbound", result.stderr)

    def test_voice_reference_review_seals_and_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=90",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-c:a",
                    "pcm_s16le",
                    str(reference),
                ],
                check=True,
            )
            preparation = root / "preparation.json"
            preparation.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "provider_target": "xai_custom_voice",
                        "owner_consent_confirmed": True,
                        "source_identity": media_identity(reference),
                        "source_transcript_identity": media_identity(reference),
                        "output_identity": media_identity(reference),
                    }
                ),
                encoding="utf-8",
            )
            transcript = root / "transcript.json"
            words = [
                {
                    "start": round(index * 0.3, 3),
                    "end": round(index * 0.3 + 0.24, 3),
                    "word": f" word{index}",
                    "probability": 0.95,
                }
                for index in range(300)
            ]
            transcript.write_text(
                json.dumps(
                    {
                        "source_media_identity": media_identity(reference),
                        "duration": 90.0,
                        "segments": [
                            {
                                "start": 0.0,
                                "end": 89.94,
                                "text": " ".join(f"word{index}" for index in range(300)),
                                "words": words,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            review = root / "review.json"
            seal = run_script(
                "seal_voice_reference_review.py",
                "seal",
                "--reference",
                str(reference),
                "--preparation-report",
                str(preparation),
                "--transcript-json",
                str(transcript),
                "--only-owner-speaks",
                "--no-background-audio",
                "--no-private-audio",
                "--representative-tutorial-delivery",
                "--no-clipped-words",
                "--no-edit-artifacts",
                "--reviewer-kind",
                "human",
                "--reviewer-name",
                "Fixture Reviewer",
                "--listened-from-start-to-finish",
                "--notes",
                "Owner voice is clean, natural, private, and artifact free.",
                "--output",
                str(review),
            )
            self.assertEqual(seal.returncode, 0, seal.stdout + seal.stderr)
            sealed_payload = json.loads(review.read_text())
            self.assertTrue(sealed_payload["upload_ready"])
            self.assertEqual(
                sealed_payload["auditory_review"]["reviewer_kind"],
                "human",
            )
            self.assertEqual(
                sealed_payload["quality_evidence"]["signal"]["clipped_sample_count"],
                0,
            )

            verify = run_script(
                "seal_voice_reference_review.py",
                "verify",
                "--reference",
                str(reference),
                "--preparation-report",
                str(preparation),
                "--transcript-json",
                str(transcript),
                "--review",
                str(review),
            )
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
            reference.write_bytes(reference.read_bytes() + b"changed")
            stale = run_script(
                "seal_voice_reference_review.py",
                "verify",
                "--reference",
                str(reference),
                "--preparation-report",
                str(preparation),
                "--transcript-json",
                str(transcript),
                "--review",
                str(review),
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("stale reference_identity", stale.stdout)


class CreatorFidelityTests(unittest.TestCase):
    def test_plan_duration_uses_each_shots_voice_performance_contract(self):
        shots = [
            {
                "id": "shot-001",
                "story_role": "tutorial",
                "narration": "Open network settings and inspect every option before changing the selected adapter now.",
                "computer_actions": ["Open network settings"],
                "required_visual_state": "Network settings are visible.",
                "maximum_recording_seconds": 10.0,
                "voice_performance": {
                    "target_words_per_minute": {"minimum": 120, "maximum": 120}
                },
            },
            {
                "id": "shot-002",
                "story_role": "proof",
                "narration": "Confirm latency improves while the final benchmark result remains clearly visible onscreen today.",
                "computer_actions": ["Show benchmark result"],
                "required_visual_state": "Benchmark result is visible.",
                "maximum_recording_seconds": 10.0,
                "voice_performance": {
                    "target_words_per_minute": {"minimum": 240, "maximum": 240}
                },
            },
        ]
        _errors, _warnings, evidence = audit_plan(
            {"schema_version": 4, "shots": shots},
            {"target_duration_seconds": {}},
            {
                "learned_measurements": {"words_per_minute_median": 200.0},
                "creator_fingerprint": {"confidence": "low"},
            },
        )

        expected = 13 / 120 * 60 + 13 / 240 * 60
        self.assertAlmostEqual(evidence["estimated_duration_seconds"], expected, places=3)
        self.assertAlmostEqual(evidence["effective_planned_words_per_minute"], 160.0)

    def test_final_creator_gate_is_bound_to_exact_profile_and_transcript(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project.json"
            profile = root / "profile.json"
            transcript = root / "transcript.json"
            project.write_text('{"mode":"synthetic"}', encoding="utf-8")
            profile.write_text('{"channel":"Luna Tweak"}', encoding="utf-8")
            transcript.write_text('{"segments":[]}', encoding="utf-8")
            report = {
                "mode": "final",
                "passed": True,
                "shot_plan_spec_sha256": "plan-hash",
                "project": str(project),
                "project_identity": media_identity(project),
                "channel_profile": str(profile),
                "channel_profile_identity": media_identity(profile),
                "transcript": str(transcript),
                "transcript_identity": media_identity(transcript),
                "profile_confidence": "low",
                "evidence": {"fingerprint": {"score": 0.9}},
            }
            self.assertTrue(creator_fidelity_gate(report, "plan-hash")["passed"])
            transcript.write_text('{"segments":[{"text":"changed"}]}', encoding="utf-8")
            rejected = creator_fidelity_gate(report, "plan-hash")
            self.assertFalse(rejected["passed"])
            self.assertTrue(any("changed after review" in error for error in rejected["errors"]))

    def test_mid_tutorial_download_link_is_not_mistaken_for_the_cta(self):
        payload = {
            "duration": 100.0,
            "segments": [
                {"start": 0.0, "end": 4.0, "text": "Today I'm showing you the fix."},
                {"start": 4.0, "end": 8.0, "text": "All right guys, to start the tutorial open settings."},
                {"start": 40.0, "end": 45.0, "text": "Download it from the link in the description."},
                {"start": 88.0, "end": 93.0, "text": "If this video helped you, check out the full optimization."},
                {"start": 93.0, "end": 100.0, "text": "Thank you guys for watching."},
            ],
        }
        sections = transcript_style(payload)["sections"]
        self.assertEqual(sections["cta_start_seconds"], 88.0)
        self.assertGreater(sections["tutorial_duration_fraction"], 0.75)

    def test_plan_and_final_fidelity_audits_reject_repeated_or_changed_narration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "channel_profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "quantitative_confidence": "low",
                        "story_and_visuals": {
                            "order": ["hook", "setup", "tutorial", "proof", "cta"]
                        },
                        "learned_measurements": {"words_per_minute_median": 220.0},
                        "creator_fingerprint": {
                            "confidence": "low",
                            "linguistic_medians": {
                                "viewer_address_per_100_words": 8.0,
                                "action_words_per_100_words": 12.0,
                                "transition_words_per_100_words": 5.0,
                            },
                            "accepted_exemplars": [
                                {"hook": "Today I'm showing you how to lower your ping."}
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            project = root / "project.json"
            project.write_text(
                json.dumps(
                    {
                        "mode": "synthetic",
                        "target_duration_seconds": {"minimum": 10, "maximum": 60},
                    }
                ),
                encoding="utf-8",
            )

            def shot(shot_id: str, role: str, narration: str, claim_type: str) -> dict:
                return {
                    "id": shot_id,
                    "story_role": role,
                    "viewer_purpose": f"Deliver the {role}.",
                    "rationale": "Keep the tutorial direct.",
                    "continuity": "Follows the previous step.",
                    "narration": narration,
                    "computer_actions": ["Open the network setting and show the result"],
                    "required_visual_state": "The network setting and result are visible.",
                    "claim_support": {
                        "type": claim_type,
                        "spoken_claim": narration,
                        "visible_evidence": "The network setting and result are visible.",
                    },
                    "capture_checkpoints": ["The required setting is centered and readable."],
                    "retake_triggers": ["Retake if the setting or result is cropped."],
                    "creator_style_rationale": "Uses direct second-person Luna tutorial wording.",
                    "timing_mode": "fit",
                    "maximum_recording_seconds": 12.0,
                    "target_box": None,
                    "include_boxes": [],
                }

            plan_data = {
                "schema_version": 3,
                "title": "Fidelity fixture",
                "story": "A direct network tutorial.",
                "shots": [
                    shot(
                        "shot-001",
                        "hook",
                        "Today I'm showing you how to lower your ping. Let's get into it.",
                        "hook",
                    ),
                    shot(
                        "shot-002",
                        "tutorial",
                        "All right guys, open the network settings, then click advanced and make sure the option is disabled.",
                        "instruction",
                    ),
                    shot(
                        "shot-003",
                        "cta",
                        "If this video helped, check the link in the description. Thank you guys for watching.",
                        "promotion",
                    ),
                ],
            }
            plan = root / "shot_plan.json"
            plan.write_text(json.dumps(plan_data), encoding="utf-8")
            plan_report = root / "plan_fidelity.json"
            passing_plan = run_script(
                "audit_creator_fidelity.py",
                "plan",
                "--shot-plan",
                str(plan),
                "--project",
                str(project),
                "--channel-profile",
                str(profile),
                "--report",
                str(plan_report),
            )
            self.assertEqual(passing_plan.returncode, 0, passing_plan.stdout + passing_plan.stderr)

            combined = " ".join(item["narration"] for item in plan_data["shots"])
            transcript = root / "transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "duration": 16.0,
                        "segments": [{"start": 0.0, "end": 16.0, "text": combined}],
                    }
                ),
                encoding="utf-8",
            )
            final_report = root / "final_fidelity.json"
            passing_final = run_script(
                "audit_creator_fidelity.py",
                "final",
                "--shot-plan",
                str(plan),
                "--project",
                str(project),
                "--channel-profile",
                str(profile),
                "--transcript-json",
                str(transcript),
                "--report",
                str(final_report),
            )
            self.assertEqual(passing_final.returncode, 0, passing_final.stdout + passing_final.stderr)

            plan_data["shots"][2]["narration"] = plan_data["shots"][1]["narration"]
            plan.write_text(json.dumps(plan_data), encoding="utf-8")
            failed_plan = run_script(
                "audit_creator_fidelity.py",
                "plan",
                "--shot-plan",
                str(plan),
                "--project",
                str(project),
                "--channel-profile",
                str(profile),
                "--report",
                str(plan_report),
            )
            self.assertNotEqual(failed_plan.returncode, 0)
            self.assertTrue(json.loads(plan_report.read_text())["evidence"]["style_metrics"]["duplicate_units"])


class CleanupTests(unittest.TestCase):
    def test_cleanup_stays_in_final_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job_a = root / "job-a"
            job_b = root / "job-b"
            job_a.mkdir()
            job_b.mkdir()
            final = job_a / "final.mp4"
            draft = job_a / "draft.mp4"
            sibling = job_b / "old-final.mp4"
            final.write_bytes(b"final")
            draft.write_bytes(b"draft")
            sibling.write_bytes(b"sibling")

            result = run_script(
                "cleanup_edit_artifacts.py",
                "--final-output",
                str(final),
                "--delete",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(final.exists())
            self.assertFalse(draft.exists())
            self.assertTrue(sibling.exists())

    def test_cleanup_refuses_broad_root_by_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = root / "job"
            job.mkdir()
            final = job / "final.mp4"
            final.write_bytes(b"final")
            result = run_script(
                "cleanup_edit_artifacts.py",
                "--final-output",
                str(final),
                "--output-root",
                str(root),
                "--delete",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(final.exists())

    def test_manifest_cleanup_keeps_only_delivery_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary) / "job"
            delivery = job / "delivery"
            analysis = job / "analysis"
            delivery.mkdir(parents=True)
            analysis.mkdir()
            final = delivery / "final.mp4"
            final.write_bytes(b"final")
            (analysis / "audio.wav").write_bytes(b"scratch")
            project = job / "project.json"
            project.write_text("{}", encoding="utf-8")
            manifest = job / ".luna-job.json"
            manifest.write_text(
                json.dumps(
                    {
                        "job_root": str(job),
                        "owned_artifacts": [".luna-job.json", "analysis", "project.json"],
                    }
                ),
                encoding="utf-8",
            )
            result = run_script(
                "cleanup_edit_artifacts.py",
                "--final-output",
                str(final),
                "--manifest",
                str(manifest),
                "--delete",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(final.exists())
            self.assertEqual([path.relative_to(job) for path in job.rglob("*")], [Path("delivery"), Path("delivery/final.mp4")])

    def test_manifest_cannot_delete_final_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary) / "job"
            delivery = job / "delivery"
            delivery.mkdir(parents=True)
            final = delivery / "final.mp4"
            final.write_bytes(b"final")
            manifest = job / ".luna-job.json"
            manifest.write_text(
                json.dumps({"job_root": str(job), "owned_artifacts": ["delivery"]}),
                encoding="utf-8",
            )
            result = run_script(
                "cleanup_edit_artifacts.py",
                "--final-output",
                str(final),
                "--manifest",
                str(manifest),
                "--delete",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(final.exists())


class PlanValidationTests(unittest.TestCase):
    def write_fixture(self, root: Path, include_reasoning: bool = True) -> tuple[Path, Path, Path, Path]:
        plan_item = {
            "start": 0.0,
            "end": 0.9,
            "label": "hook",
            "story_role": "hook",
            "rationale": "Clean complete take.",
            "viewer_purpose": "State the value.",
            "take_choice": "Only complete fluent take in this fixture.",
            "continuity": "Opens the video.",
            "evidence": {"transcript_segment_ids": [0], "frame_times": [0.4]},
        }
        if not include_reasoning:
            plan_item.pop("rationale")
        plan = root / "plan.json"
        plan.write_text(json.dumps({"keep": [plan_item]}), encoding="utf-8")
        project = root / "project.json"
        project.write_text(
            json.dumps({"required_story_roles": ["hook"], "target_duration_seconds": {}}),
            encoding="utf-8",
        )
        transcript = root / "transcript.json"
        transcript.write_text(
            json.dumps(
                {
                    "segments": [
                        {
                            "id": 0,
                            "start": 0.0,
                            "end": 0.9,
                            "text": "Lower your ping today",
                            "words": [
                                {"start": 0.05, "end": 0.20, "word": "Lower"},
                                {"start": 0.25, "end": 0.36, "word": "your"},
                                {"start": 0.40, "end": 0.55, "word": "ping"},
                                {"start": 0.62, "end": 0.82, "word": "today"},
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        dossier = root / "dossier.json"
        dossier.write_text(json.dumps({"language_evidence": {"duplicate_candidates": []}}), encoding="utf-8")
        return plan, project, transcript, dossier

    def test_reasoned_plan_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, project, transcript, dossier = self.write_fixture(root)
            report = root / "report.json"
            result = run_script(
                "validate_edit_plan.py",
                "--plan",
                str(plan),
                "--project",
                str(project),
                "--transcript-json",
                str(transcript),
                "--dossier",
                str(dossier),
                "--report",
                str(report),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(report.read_text())["passed"])

    def test_unreasoned_plan_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, project, transcript, dossier = self.write_fixture(root, include_reasoning=False)
            report = root / "report.json"
            result = run_script(
                "validate_edit_plan.py",
                "--plan",
                str(plan),
                "--project",
                str(project),
                "--transcript-json",
                str(transcript),
                "--dossier",
                str(dossier),
                "--report",
                str(report),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("rationale", " ".join(json.loads(report.read_text())["errors"]))


class PlanTransformTests(unittest.TestCase):
    def test_mechanical_passes_preserve_semantic_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "story": "Hook then tutorial.",
                        "keep": [
                            {
                                "start": 0.0,
                                "end": 0.8,
                                "label": "hook",
                                "story_role": "hook",
                                "rationale": "Best take.",
                                "viewer_purpose": "State the result.",
                                "take_choice": "Only complete take.",
                                "continuity": "Opens cleanly.",
                                "evidence": {"transcript_segment_ids": [1]},
                            }
                        ],
                        "duplicate_resolutions": [
                            {"group_id": "duplicate-001", "reason": "Kept the fluent take."}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            transcript = root / "transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "segments": [
                            {
                                "id": 1,
                                "start": 0.0,
                                "end": 0.8,
                                "text": "Clean take",
                                "words": [
                                    {"start": 0.10, "end": 0.28, "word": "Clean"},
                                    {"start": 0.42, "end": 0.66, "word": "take"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            audio = root / "audio.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(audio),
                ],
                check=True,
            )
            tightened = root / "tightened.json"
            tighten = run_script(
                "tighten_spoken_pacing.py",
                "--keep-list",
                str(plan),
                "--transcript-json",
                str(transcript),
                "--output",
                str(tightened),
            )
            self.assertEqual(tighten.returncode, 0, tighten.stdout + tighten.stderr)
            snapped = root / "snapped.json"
            snap = run_script(
                "snap_keep_list_to_audio.py",
                "--keep-list",
                str(tightened),
                "--transcript-json",
                str(transcript),
                "--audio-wav",
                str(audio),
                "--output",
                str(snapped),
            )
            self.assertEqual(snap.returncode, 0, snap.stdout + snap.stderr)
            transformed = json.loads(snapped.read_text())
            self.assertEqual(transformed["story"], "Hook then tutorial.")
            self.assertEqual(transformed["duplicate_resolutions"][0]["group_id"], "duplicate-001")
            self.assertEqual(transformed["keep"][0]["rationale"], "Best take.")


class VoiceTests(unittest.TestCase):
    def test_xai_registration_requires_exact_downloaded_reference_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = write_voice_registration_fixture(root)
            paths["registration"].unlink()
            args = SimpleNamespace(
                voice_id="abcd1234",
                built_in_voice=False,
                owner_consent_confirmed=True,
                reference=str(paths["reference"]),
                preparation_report=str(paths["preparation"]),
                transcript_json=str(paths["transcript"]),
                reference_review=str(paths["review"]),
                output=str(paths["registration"]),
                dry_run=False,
            )
            with (
                patch.object(
                    xai_voiceover_module,
                    "validate_review_evidence",
                    return_value=({"technical": "passed"}, [], []),
                ),
                patch.object(
                    xai_voiceover_module,
                    "verify_voice",
                    return_value={
                        "verified": True,
                        "metadata": {"voice_id": "abcd1234"},
                    },
                ),
                patch.object(
                    xai_voiceover_module,
                    "request_bytes",
                    return_value=(paths["reference"].read_bytes(), "audio/wav"),
                ),
            ):
                self.assertEqual(xai_voiceover_module.cmd_register(args), 0)
            self.assertEqual(
                voice_registration_errors(paths["registration"], "abcd1234"),
                [],
            )

            paths["registration"].unlink()
            with (
                patch.object(
                    xai_voiceover_module,
                    "validate_review_evidence",
                    return_value=({}, [], []),
                ),
                patch.object(
                    xai_voiceover_module,
                    "verify_voice",
                    return_value={
                        "verified": True,
                        "metadata": {"voice_id": "abcd1234"},
                    },
                ),
                patch.object(
                    xai_voiceover_module,
                    "request_bytes",
                    return_value=(b"different uploaded source", "audio/wav"),
                ),
                self.assertRaisesRegex(SystemExit, "different reference-audio bytes"),
            ):
                xai_voiceover_module.cmd_register(args)
            self.assertFalse(paths["registration"].exists())

    def test_registered_source_is_rechecked_before_real_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = write_voice_registration_fixture(Path(temporary))
            with patch.object(
                xai_voiceover_module,
                "request_bytes",
                return_value=(b"different current xAI source", "audio/wav"),
            ), self.assertRaisesRegex(SystemExit, "no longer matches"):
                xai_voiceover_module.registered_voice_evidence(
                    str(paths["registration"]),
                    "abcd1234",
                    False,
                )

            with patch.object(
                xai_voiceover_module,
                "request_bytes",
                return_value=(paths["reference"].read_bytes(), "audio/wav"),
            ):
                registration, evidence = xai_voiceover_module.registered_voice_evidence(
                    str(paths["registration"]),
                    "abcd1234",
                    False,
                )
            self.assertEqual(registration.resolve(), paths["registration"].resolve())
            self.assertTrue(evidence["voice_reference_download_verified_at_generation"])

    def test_custom_voice_id_format_is_checked_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "voice.wav"
            invalid = run_script(
                "xai_voiceover.py",
                "synthesize",
                "--text",
                "Test narration.",
                "--output",
                str(output),
                "--voice-id",
                "NOT-A-VOICE-ID",
                "--owner-consent-confirmed",
                "--dry-run",
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("8 lowercase letters/digits", invalid.stderr)

    def test_custom_voice_dry_run_requires_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "voice.wav"
            denied = run_script(
                "xai_voiceover.py",
                "synthesize",
                "--text",
                "Test narration.",
                "--output",
                str(output),
                "--voice-id",
                "abcd1234",
                "--dry-run",
            )
            self.assertNotEqual(denied.returncode, 0)
            allowed = run_script(
                "xai_voiceover.py",
                "synthesize",
                "--text",
                "Test narration.",
                "--output",
                str(output),
                "--voice-id",
                "abcd1234",
                "--owner-consent-confirmed",
                "--dry-run",
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertFalse(output.exists())

    def test_plan_generation_targets_only_requested_stale_shots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_dir = root / "voice"
            output_dir.mkdir()
            shots = [
                {"id": "shot-001", "narration": "Keep this approved take."},
                {"id": "shot-002", "narration": "Generate only this stale take."},
            ]
            plan = root / "shot_plan.json"
            plan.write_text(
                json.dumps({"schema_version": 2, "shots": shots}),
                encoding="utf-8",
            )
            current_voice = output_dir / "shot-001.wav"
            current_voice.write_bytes(b"current reviewed take")
            manifest = output_dir / "voiceover_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "generated": [
                            {
                                "shot_id": "shot-001",
                                "shot_spec_sha256": shot_spec_sha256(shots[0]),
                                "output": str(current_voice),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            generated = run_script(
                "xai_voiceover.py",
                "synthesize-plan",
                "--shot-plan",
                str(plan),
                "--output-dir",
                str(output_dir),
                "--shot-id",
                "shot-002",
                "--voice-id",
                "abcd1234",
                "--owner-consent-confirmed",
                "--dry-run",
            )
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            self.assertEqual(current_voice.read_bytes(), b"current reviewed take")
            self.assertFalse((output_dir / "shot-002.wav").exists())
            payload = json.loads(manifest.read_text())
            self.assertEqual(payload["requested_shot_ids"], ["shot-002"])
            self.assertEqual(
                [entry["shot_id"] for entry in payload["generated"]],
                ["shot-001", "shot-002"],
            )

    def test_cadence_generation_retries_speed_and_replaces_output_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "shot.wav"
            output.write_bytes(b"previous reviewed audio")
            narration = " ".join(f"word{index}" for index in range(20))
            observed_speeds = []

            def fake_attempt(_chunks, attempt_output, _voice, _language, speed, *_args):
                observed_speeds.append(speed)
                attempt_output.write_bytes(b"RIFF" + bytes([len(observed_speeds)]) * 256)
                return {
                    "output": attempt_output,
                    "duration_seconds": 10.0 if len(observed_speeds) == 1 else 5.2,
                    "chunks": [{"characters": len(narration), "duration": 5.2}],
                    "audio_timestamps": None,
                }

            with patch.object(
                xai_voiceover_module,
                "synthesize_attempt",
                side_effect=fake_attempt,
            ):
                result = xai_voiceover_module.synthesize_text(
                    narration,
                    output,
                    "abcd1234",
                    "en",
                    False,
                    1.0,
                    False,
                    False,
                    approved_narration=narration,
                    target_wpm=(220.0, 240.0),
                    maximum_cadence_attempts=2,
                )

            self.assertEqual(observed_speeds, [1.0, 1.5])
            self.assertTrue(result["cadence"]["within_target"])
            self.assertAlmostEqual(result["cadence"]["actual_words_per_minute"], 230.769, places=3)
            self.assertNotEqual(output.read_bytes(), b"previous reviewed audio")
            self.assertEqual(
                json.loads(output.with_suffix(".wav.xai.json").read_text())["media_identity"],
                media_identity(output),
            )

    def test_failed_voice_attempt_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shot.wav"
            output.write_bytes(b"previous reviewed audio")
            with (
                patch.object(
                    xai_voiceover_module,
                    "synthesize_attempt",
                    side_effect=SystemExit("simulated API failure"),
                ),
                self.assertRaisesRegex(SystemExit, "simulated API failure"),
            ):
                xai_voiceover_module.synthesize_text(
                    "This generation should fail safely.",
                    output,
                    "abcd1234",
                    "en",
                    False,
                    1.0,
                    False,
                    False,
                )
            self.assertEqual(output.read_bytes(), b"previous reviewed audio")
            self.assertFalse(output.with_suffix(".wav.xai.json").exists())

    def test_failed_cadence_retry_keeps_first_generated_take_for_direction(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shot.wav"
            narration = " ".join(f"word{index}" for index in range(20))
            attempts = 0

            def fake_attempt(_chunks, attempt_output, *_args):
                nonlocal attempts
                attempts += 1
                if attempts == 2:
                    raise SystemExit("simulated corrective retry failure")
                attempt_output.write_bytes(b"RIFF" + b"a" * 256)
                return {
                    "output": attempt_output,
                    "duration_seconds": 10.0,
                    "chunks": [{"characters": len(narration), "duration": 10.0}],
                    "audio_timestamps": None,
                }

            with patch.object(
                xai_voiceover_module,
                "synthesize_attempt",
                side_effect=fake_attempt,
            ):
                result = xai_voiceover_module.synthesize_text(
                    narration,
                    output,
                    "abcd1234",
                    "en",
                    False,
                    1.0,
                    False,
                    False,
                    approved_narration=narration,
                    target_wpm=(220.0, 240.0),
                    maximum_cadence_attempts=2,
                )

            self.assertEqual(attempts, 2)
            self.assertTrue(output.exists())
            self.assertFalse(result["cadence"]["within_target"])
            self.assertEqual(
                result["cadence"]["retry_failure"],
                {"attempt": 2, "error_type": "SystemExit"},
            )

    def test_voiceover_audit_detects_changed_and_repeated_words(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / "shot_plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "shots": [
                            {
                                "id": "shot-001",
                                "narration": "Open the network settings and click advanced.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            transcript = root / "shot-001.json"
            voiceover = root / "shot-001.wav"
            voiceover.write_bytes(b"reviewed-voice-fixture")
            transcript.write_text(
                json.dumps(
                    {
                        "segments": [
                            {
                                "text": "Open the network network settings and click basic.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            report = root / "audit.json"
            failed = run_script(
                "audit_voiceover.py",
                "--shot-plan",
                str(plan),
                "--transcript",
                f"shot-001={transcript}",
                "--voiceover",
                f"shot-001={voiceover}",
                "--report",
                str(report),
            )
            self.assertNotEqual(failed.returncode, 0)
            failed_report = json.loads(report.read_text())
            self.assertFalse(failed_report["passed"])
            self.assertTrue(failed_report["shots"][0]["repeated_phrases"])

            transcript.write_text(
                json.dumps(
                    {
                        "segments": [
                            {
                                "text": "Open the network settings and click advanced.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            passed = run_script(
                "audit_voiceover.py",
                "--shot-plan",
                str(plan),
                "--transcript",
                f"shot-001={transcript}",
                "--voiceover",
                f"shot-001={voiceover}",
                "--report",
                str(report),
            )
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            self.assertTrue(json.loads(report.read_text())["passed"])


class JobTests(unittest.TestCase):
    def test_init_creates_isolated_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"video-identity")
            jobs = root / "jobs"
            result = run_script(
                "luna_editor.py",
                "init",
                "--source",
                str(source),
                "--jobs-root",
                str(jobs),
                "--title",
                "Test Video",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            job = Path(result.stdout.strip())
            self.assertTrue((job / ".luna-job.json").exists())
            self.assertTrue((job / "project.json").exists())
            self.assertTrue((job / "channel_profile.json").exists())
            self.assertTrue((job / "delivery").is_dir())

    def test_accept_rejects_candidate_changed_after_qa(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            initialized = run_script(
                "luna_editor.py",
                "init",
                "--source",
                str(source),
                "--jobs-root",
                str(root / "jobs"),
                "--title",
                "Integrity Test",
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            job = Path(initialized.stdout.strip())
            candidate = root / "candidate.mp4"
            candidate.write_bytes(b"verified-candidate")

            def identity(path: Path) -> dict:
                stat = path.stat()
                return {
                    "path": str(path.resolve()),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }

            report = root / "qa.json"
            report.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "input": str(candidate.resolve()),
                        "media_identity": identity(candidate),
                    }
                ),
                encoding="utf-8",
            )
            candidate.write_bytes(b"changed-after-review")
            rejected = run_script(
                "luna_editor.py",
                "accept",
                "--job",
                str(job),
                "--final",
                str(candidate),
                "--qa-report",
                str(report),
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("changed after QA", rejected.stderr)

            report.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "input": str(candidate.resolve()),
                        "media_identity": identity(candidate),
                    }
                ),
                encoding="utf-8",
            )
            accepted = run_script(
                "luna_editor.py",
                "accept",
                "--job",
                str(job),
                "--final",
                str(candidate),
                "--qa-report",
                str(report),
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertTrue(Path(accepted.stdout.strip()).exists())


class DossierTests(unittest.TestCase):
    def test_dossier_builds_from_media_and_transcript(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=320x180:r=30:d=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ],
                check=True,
            )
            transcript = root / "transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "segments": [
                            {
                                "id": 0,
                                "start": 0.0,
                                "end": 0.8,
                                "text": "This is the clean take",
                                "words": [
                                    {"start": 0.05, "end": 0.20, "word": "This"},
                                    {"start": 0.24, "end": 0.35, "word": "is"},
                                    {"start": 0.40, "end": 0.52, "word": "the"},
                                    {"start": 0.56, "end": 0.68, "word": "clean"},
                                    {"start": 0.70, "end": 0.80, "word": "take"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = root / "analysis"
            result = run_script(
                "build_editorial_dossier.py",
                "--source",
                str(source),
                "--transcript-json",
                str(transcript),
                "--output-dir",
                str(output),
                "--frame-interval",
                "0.5",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            dossier = json.loads((output / "dossier.json").read_text())
            self.assertFalse(dossier["decision_contract"]["automatic_decisions_allowed"])
            self.assertTrue((output / "EDITORIAL_REVIEW.md").exists())
            self.assertTrue((output / "review.html").exists())
            review = (output / "EDITORIAL_REVIEW.md").read_text(encoding="utf-8")
            self.assertIn("Do not inspect every sampled frame sequentially", review)


class IntroSlateTests(unittest.TestCase):
    def test_intro_boundary_comes_from_reasoned_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "source.mp4"
            image = root / "intro.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x180:r=30:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=2",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(video),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=320x180:d=0.1",
                    "-frames:v",
                    "1",
                    str(image),
                ],
                check=True,
            )
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "keep": [
                            {"start": 0.0, "end": 0.6, "intro_slate": True},
                            {"start": 0.6, "end": 2.0, "intro_slate": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = root / "with_intro.mp4"
            report = root / "intro_report.json"
            result = run_script(
                "apply_intro_slate.py",
                "--input",
                str(video),
                "--intro-image",
                str(image),
                "--edit-plan",
                str(plan),
                "--output",
                str(output),
                "--report",
                str(report),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            evidence = json.loads(report.read_text())
            self.assertEqual(evidence["method"], "reasoned_edit_plan")
            self.assertAlmostEqual(evidence["intro_end_seconds"], 0.6, places=2)
            self.assertTrue(output.exists())


class RecorderTests(unittest.TestCase):
    def test_missing_recording_state_reports_false(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "missing-state.json"
            result = run_script("record_desktop.py", "status", "--state", str(state))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(json.loads(result.stdout)["recording"])


class FinalVerificationTests(unittest.TestCase):
    def test_visual_review_is_fail_closed_then_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "candidate.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x180:r=30:d=1.2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1.2",
                    "-filter_complex",
                    "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]",
                    "-map",
                    "0:v",
                    "-map",
                    "[a]",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(video),
                ],
                check=True,
            )
            transcript = root / "transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "segments": [
                            {
                                "id": 0,
                                "start": 0.0,
                                "end": 1.0,
                                "text": "Clean final line",
                                "words": [
                                    {"start": 0.08, "end": 0.24, "word": "Clean"},
                                    {"start": 0.90, "end": 1.00, "word": "final"},
                                    {"start": 1.05, "end": 1.15, "word": "line"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            plan_report = root / "plan_report.json"
            validated_plan = root / "validated_plan.json"
            validated_plan.write_text(json.dumps({"keep": [{"start": 0.0, "end": 1.0}]}), encoding="utf-8")
            plan_report.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "plan": str(validated_plan),
                        "plan_sha256": hashlib.sha256(validated_plan.read_bytes()).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            qa = root / "qa"
            first = run_script(
                "verify_final_video.py",
                "--input",
                str(video),
                "--output-dir",
                str(qa),
                "--transcript-json",
                str(transcript),
                "--plan-report",
                str(plan_report),
            )
            self.assertNotEqual(first.returncode, 0)
            report_path = qa / "final_qa_report.json"
            self.assertTrue(report_path.exists(), first.stdout + first.stderr)
            first_report = json.loads(report_path.read_text())
            self.assertTrue(first_report["gates"]["audio"]["passed"])
            self.assertFalse(first_report["gates"]["speech"]["passed"])
            self.assertFalse(first_report["gates"]["visual"]["passed"])

            template = json.loads((qa / "visual_review_template.json").read_text())
            template["overall"] = {
                "story_continuity": True,
                "ui_readability": True,
                "claims_match_visuals": True,
                "no_private_information": True,
                "notes": "Synthetic verification fixture.",
            }
            for frame in template["timeline_frames"]:
                frame.update(
                    {
                        "passed": True,
                        "ui_readable": True,
                        "narration_context_consistent": True,
                        "private_information_clear": True,
                        "notes": "Synthetic verification fixture.",
                    }
                )
            completed = qa / "visual_review_completed.json"
            completed.write_text(json.dumps(template), encoding="utf-8")
            gap_review = json.loads((qa / "speech_gap_review_template.json").read_text())
            self.assertEqual(len(gap_review["gaps"]), 1)
            gap_review["gaps"][0]["justified"] = True
            gap_review["gaps"][0]["reason"] = "Viewer needs time to inspect the visible result."
            completed_gaps = qa / "speech_gap_review_completed.json"
            completed_gaps.write_text(json.dumps(gap_review), encoding="utf-8")
            second = run_script(
                "verify_final_video.py",
                "--input",
                str(video),
                "--output-dir",
                str(qa),
                "--transcript-json",
                str(transcript),
                "--plan-report",
                str(plan_report),
                "--visual-review",
                str(completed),
                "--speech-gap-review",
                str(completed_gaps),
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertTrue(json.loads((qa / "final_qa_report.json").read_text())["passed"])


class StyleLearningTests(unittest.TestCase):
    def test_learning_preserves_direct_rules_and_reports_low_confidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "accepted.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=green:s=320x180:r=30:d=1.2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=600:duration=1.2",
                    "-filter_complex",
                    "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]",
                    "-map",
                    "0:v",
                    "-map",
                    "[a]",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(video),
                ],
                check=True,
            )
            transcript = root / "transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "duration": 1.2,
                        "segments": [
                            {
                                "id": 0,
                                "start": 0.0,
                                "end": 1.0,
                                "text": "Clean tutorial pacing",
                                "words": [
                                    {"start": 0.05, "end": 0.20, "word": "Clean"},
                                    {"start": 0.32, "end": 0.52, "word": "tutorial"},
                                    {"start": 0.72, "end": 0.92, "word": "pacing"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            baseline = root / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "voice_and_pacing": {"duplicate_policy": "Keep the fluent take."},
                        "delivery_guardrails": {"target_loudness_lufs": -16.0},
                    }
                ),
                encoding="utf-8",
            )
            output = root / "profile.json"
            result = run_script(
                "learn_channel_style.py",
                "--video",
                str(video),
                "--transcript",
                f"{video}={transcript}",
                "--raw-pair",
                f"{video}={video}",
                "--base-profile",
                str(baseline),
                "--output",
                str(output),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            profile = json.loads(output.read_text())
            self.assertEqual(profile["voice_and_pacing"]["duplicate_policy"], "Keep the fluent take.")
            self.assertEqual(profile["quantitative_confidence"], "low")
            self.assertEqual(profile["accepted_tutorial_examples"], 1)
            self.assertGreater(profile["learned_measurements"]["positive_speech_gap_p90_median"], 0)
            self.assertEqual(profile["schema_version"], 2)
            self.assertIn("creator_fingerprint", profile)
            self.assertEqual(profile["learned_examples"][0]["video"], video.name)
            self.assertNotIn("opening_excerpt", profile["learned_examples"][0]["creator_style"])


class ShotAssemblyTests(unittest.TestCase):
    def test_synthetic_assembly_requires_passing_voice_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary) / "job"
            plans = job / "plans"
            qa = job / "qa"
            plans.mkdir(parents=True)
            qa.mkdir()
            video = job / "shot.mp4"
            voice = job / "shot.wav"
            video.write_bytes(b"fixture")
            voice.write_bytes(b"fixture")
            (job / "project.json").write_text(
                json.dumps({"mode": "synthetic", "required_story_roles": ["hook"]}),
                encoding="utf-8",
            )
            plan = plans / "shot_plan.json"
            plan_data = {
                "schema_version": 2,
                "shots": [
                    {
                        "id": "shot-001",
                        "story_role": "hook",
                        "viewer_purpose": "State the result.",
                        "rationale": "Concise opening.",
                        "continuity": "Starts the video.",
                        "narration": "Test line.",
                        "computer_actions": ["Show state"],
                        "required_visual_state": "State is visible.",
                        "timing_mode": "fit",
                        "maximum_recording_seconds": 2.0,
                        "video": str(video),
                        "voiceover": str(voice),
                    }
                ],
            }
            plan.write_text(json.dumps(plan_data), encoding="utf-8")
            write_passing_shot_evidence(job, plan_data, video, voice, include_audit=False)
            report = qa / "assembly.json"
            command = (
                "assemble_shot_plan.py",
                "--shot-plan",
                str(plan),
                "--output",
                str(job / "output.mp4"),
                "--report",
                str(report),
                "--validate-only",
            )
            missing = run_script(*command)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("voiceover audit", " ".join(json.loads(report.read_text())["errors"]))

            write_passing_shot_evidence(job, plan_data, video, voice)
            passing = run_script(*command)
            self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)

    def test_unreviewed_synthetic_shot_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "shot.mp4"
            voice = root / "shot.wav"
            video.write_bytes(b"fixture")
            voice.write_bytes(b"fixture")
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "shots": [
                            {
                                "id": "shot-001",
                                "story_role": "hook",
                                "viewer_purpose": "State the result.",
                                "rationale": "Concise opening.",
                                "continuity": "Starts the video.",
                                "narration": "Test line.",
                                "computer_actions": ["Show state"],
                                "required_visual_state": "State is visible.",
                                "timing_mode": "fit",
                                "maximum_recording_seconds": 2.0,
                                "video": str(video),
                                "voiceover": str(voice),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            project = root / "project.json"
            project.write_text(
                json.dumps({"mode": "synthetic", "required_story_roles": ["hook"]}),
                encoding="utf-8",
            )
            report = root / "report.json"
            result = run_script(
                "assemble_shot_plan.py",
                "--shot-plan",
                str(plan),
                "--project",
                str(project),
                "--output",
                str(root / "output.mp4"),
                "--report",
                str(report),
                "--validate-only",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sealed recording review", " ".join(json.loads(report.read_text())["errors"]))

    def test_one_shot_assembly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "shot.mp4"
            voice = root / "shot.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=640x360:r=30:d=1.2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(video),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1",
                    "-c:a",
                    "pcm_s16le",
                    str(voice),
                ],
                check=True,
            )
            plan = root / "plan.json"
            plan_data = {
                "schema_version": 2,
                "shots": [
                    {
                        "id": "shot-001",
                        "story_role": "hook",
                        "viewer_purpose": "State the result.",
                        "rationale": "A concise visible opening.",
                        "continuity": "Opens the synthetic fixture.",
                        "narration": "Test line.",
                        "computer_actions": ["Show blue state"],
                        "required_visual_state": "Blue screen is visible.",
                        "timing_mode": "fit",
                        "maximum_recording_seconds": 1.3,
                        "video": str(video),
                        "voiceover": str(voice),
                    }
                ],
            }
            plan.write_text(json.dumps(plan_data), encoding="utf-8")
            project = root / "project.json"
            project.write_text(
                json.dumps({"mode": "synthetic", "required_story_roles": ["hook"]}),
                encoding="utf-8",
            )
            write_passing_shot_evidence(root, plan_data, video, voice)
            output = root / "assembled.mp4"
            report = root / "assembly.json"
            result = run_script(
                "assemble_shot_plan.py",
                "--shot-plan",
                str(plan),
                "--project",
                str(project),
                "--voice-audit-report",
                str(root / "qa" / "voiceover_audit.json"),
                "--recording-review-dir",
                str(root / "qa" / "reviews" / "recording"),
                "--voice-review-dir",
                str(root / "qa" / "reviews" / "voice"),
                "--output",
                str(output),
                "--report",
                str(report),
                "--resolution",
                "640x360",
                "--fps",
                "30",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(output.exists())
            self.assertTrue(json.loads(report.read_text())["passed"])


class WindowStoryboardTests(unittest.TestCase):
    def test_blank_capture_is_distinguished_from_real_ui_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            black = Path(temporary) / "black.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=640x360:d=0.1",
                    "-frames:v",
                    "1",
                    str(black),
                ],
                check=True,
            )
            self.assertFalse(image_signal_stats(black)["non_uniform"])

            blank_client = Path(temporary) / "blank-client.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=640x360:d=0.1",
                    "-vf",
                    "drawbox=x=0:y=0:w=iw:h=32:color=white:t=fill",
                    "-frames:v",
                    "1",
                    str(blank_client),
                ],
                check=True,
            )
            blank_client_stats = image_signal_stats(blank_client)
            self.assertGreater(blank_client_stats["luma_range"], 4.0)
            self.assertFalse(blank_client_stats["non_uniform"])

            real = REPO / "luna-longform-editor" / "assets" / "luna_intro_background.png"
            self.assertTrue(image_signal_stats(real)["non_uniform"])

    def test_window_selector_matches_windows_process_title_and_handle(self):
        window = {
            "window_id": 4242,
            "owner": "SystemSettings",
            "title": "Settings - Network & internet",
        }
        self.assertTrue(window_matches(window, "SystemSettings.exe", "network", 4242))
        self.assertFalse(window_matches(window, "explorer", "network", 4242))
        self.assertFalse(window_matches(window, "SystemSettings", "display", 4242))
        self.assertFalse(window_matches(window, "SystemSettings", "network", 9999))

    def test_storyboard_parser_accepts_exact_windows_handle(self):
        args = storyboard_parser().parse_args(
            [
                "capture",
                "--window-id",
                "4242",
                "--manifest",
                "capture.json",
                "--image",
                "state.png",
                "--hold-seconds",
                "1.5",
                "--action",
                "Open network settings",
                "--visual-state",
                "Network settings are visible",
            ]
        )
        self.assertEqual(args.window_id, 4242)
        self.assertIsNone(args.owner)

    def test_capture_rejects_an_ambiguous_broad_window_selector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = storyboard_parser().parse_args(
                [
                    "capture",
                    "--owner",
                    "Explorer",
                    "--manifest",
                    str(root / "capture.json"),
                    "--image",
                    str(root / "state.png"),
                    "--hold-seconds",
                    "1.0",
                    "--action",
                    "Show Downloads",
                    "--visual-state",
                    "Downloads is visible",
                ]
            )
            windows = [
                {
                    "window_id": window_id,
                    "owner": "Explorer",
                    "process_id": 9001,
                    "title": title,
                    "onscreen": True,
                }
                for window_id, title in ((100, "Downloads"), (200, "Documents"))
            ]
            with (
                patch("capture_window_storyboard.available_windows", return_value=windows),
                patch("capture_window_storyboard.capture_window_image") as capture,
                self.assertRaisesRegex(SystemExit, "multiple windows"),
            ):
                args.func(args)
            capture.assert_not_called()

    def test_rejected_capture_does_not_replace_an_existing_good_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "state.png"
            image.write_bytes(b"previous reviewed frame")
            args = storyboard_parser().parse_args(
                [
                    "capture",
                    "--window-id",
                    "4242",
                    "--manifest",
                    str(root / "capture.json"),
                    "--image",
                    str(image),
                    "--hold-seconds",
                    "1.5",
                    "--action",
                    "Open network settings",
                    "--visual-state",
                    "Network settings are visible",
                    "--overwrite",
                ]
            )
            fake_window = {
                "window_id": 4242,
                "owner": "SystemSettings",
                "title": "Settings",
                "bounds": {"Width": 640, "Height": 360},
                "minimized": False,
            }

            def write_rejected_capture(_window, path):
                path.write_bytes(b"blank capture")

            with (
                patch(
                    "capture_window_storyboard.available_windows",
                    return_value=[fake_window],
                ),
                patch(
                    "capture_window_storyboard.capture_window_image",
                    side_effect=write_rejected_capture,
                ),
                patch(
                    "capture_window_storyboard.image_signal_stats",
                    return_value={"non_uniform": False},
                ),
                self.assertRaises(SystemExit),
            ):
                args.func(args)

            self.assertEqual(image.read_bytes(), b"previous reviewed frame")
            self.assertFalse(list(root.glob("*-capture-*")))

    def test_successful_retake_replaces_the_existing_manifest_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "state.png"
            image.write_bytes(b"old frame")
            fake_window = {
                "window_id": 4242,
                "owner": "SystemSettings",
                "process_id": 9001,
                "title": "Settings",
                "bounds": {"Width": 640, "Height": 360},
                "minimized": False,
            }
            identity = media_identity(image)
            identity["path"] = image.name
            manifest = root / "capture.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "capture_mode": "state_storyboard",
                        "platform": "Darwin",
                        "selector": {"window_id": 4242},
                        "target_window": {
                            "platform": "Darwin",
                            "owner": "SystemSettings",
                            "process_id": 9001,
                            "initial_window_id": 4242,
                            "initial_title": "Settings",
                        },
                        "frames": [
                            {
                                "index": 1,
                                "image": image.name,
                                "hold_seconds": 1.0,
                                "action": "Old action",
                                "visual_state": "Old state",
                                "platform": "Darwin",
                                "window": fake_window,
                                "signal_stats": {"non_uniform": True},
                                "media_identity": identity,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = storyboard_parser().parse_args(
                [
                    "capture",
                    "--window-id",
                    "4242",
                    "--manifest",
                    str(manifest),
                    "--image",
                    str(image),
                    "--hold-seconds",
                    "1.5",
                    "--action",
                    "New action",
                    "--visual-state",
                    "New state",
                    "--overwrite",
                ]
            )

            def write_retake(_window, path):
                path.write_bytes(b"new frame")

            with (
                patch(
                    "capture_window_storyboard.available_windows",
                    return_value=[fake_window],
                ),
                patch(
                    "capture_window_storyboard.capture_window_image",
                    side_effect=write_retake,
                ),
                patch(
                    "capture_window_storyboard.image_signal_stats",
                    return_value={"non_uniform": True, "luma_range": 100.0},
                ),
                patch("builtins.print"),
            ):
                self.assertEqual(args.func(args), 0)

            updated = json.loads(manifest.read_text())
            self.assertEqual(len(updated["frames"]), 1)
            self.assertEqual(updated["frames"][0]["index"], 1)
            self.assertEqual(updated["frames"][0]["action"], "New action")
            self.assertEqual(updated["frames"][0]["media_identity"]["sha256"], hashlib.sha256(b"new frame").hexdigest())
            self.assertEqual(image.read_bytes(), b"new frame")

    def test_render_binds_exact_frames_and_expected_duration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames_dir = root / "frames"
            frames_dir.mkdir()
            frames = []
            for index, source in enumerate(
                ("testsrc=size=640x360:rate=1", "smptebars=size=640x360:rate=1"),
                start=1,
            ):
                image = frames_dir / f"{index:04d}.png"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        source,
                        "-frames:v",
                        "1",
                        str(image),
                    ],
                    check=True,
                )
                identity = media_identity(image)
                identity["path"] = f"frames/{image.name}"
                window = {
                    "window_id": 4241 + index,
                    "owner": "SystemSettings",
                    "process_id": 9001,
                    "title": f"Settings state {index}",
                    "bounds": {"Width": 640, "Height": 360},
                }
                frames.append(
                    {
                        "index": index,
                        "image": f"frames/{image.name}",
                        "hold_seconds": 1.0,
                        "action": f"Show state {index}",
                        "visual_state": f"State {index} is clearly visible.",
                        "platform": "Windows",
                        "window": window,
                        "signal_stats": image_signal_stats(image),
                        "media_identity": identity,
                    }
                )
            manifest = root / "capture.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "capture_mode": "state_storyboard",
                        "platform": "Windows",
                        "selector": {"owner": "SystemSettings"},
                        "target_window": {
                            "platform": "Windows",
                            "owner": "SystemSettings",
                            "process_id": 9001,
                            "initial_window_id": 4242,
                            "initial_title": "Settings state 1",
                        },
                        "frames": frames,
                    }
                ),
                encoding="utf-8",
            )
            output = root / "shot.mp4"
            report = root / "report.json"
            result = run_script(
                "capture_window_storyboard.py",
                "render",
                "--manifest",
                str(manifest),
                "--output",
                str(output),
                "--report",
                str(report),
                "--resolution",
                "640x360",
                "--fps",
                "30",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rendered = json.loads(report.read_text())
            self.assertTrue(rendered["passed"])
            self.assertEqual(rendered["frame_count"], 2)
            self.assertAlmostEqual(rendered["expected_duration_seconds"], 2.0, places=2)
            duration = float(rendered["probe"]["format"]["duration"])
            self.assertAlmostEqual(duration, 2.0, delta=0.08)

            changed_manifest = json.loads(manifest.read_text())
            changed_manifest["frames"][1]["window"]["owner"] = "Explorer"
            manifest.write_text(json.dumps(changed_manifest), encoding="utf-8")
            rejected = run_script(
                "capture_window_storyboard.py",
                "render",
                "--manifest",
                str(manifest),
                "--output",
                str(root / "rejected.mp4"),
                "--resolution",
                "640x360",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("owner/process name", rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
