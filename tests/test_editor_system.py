import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "luna-longform-editor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from production_evidence import (  # noqa: E402
    media_identity,
    narration_sha256,
    shot_plan_spec_sha256,
    shot_spec_sha256,
    transcript_source_errors,
    validate_sealed_review,
    xai_voice_provenance_errors,
)
from production_director import (  # noqa: E402
    derive_status,
    manifest_final_path,
    report_is_fresh,
    transcriber_python,
)


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


class CompatibilityTests(unittest.TestCase):
    def test_python_scripts_parse_with_python_310_grammar(self):
        for script in SCRIPTS.glob("*.py"):
            with self.subTest(script=script.name):
                ast.parse(script.read_text(encoding="utf-8"), filename=str(script), feature_version=(3, 10))


class ProductionEvidenceTests(unittest.TestCase):
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


class ProductionDirectorTests(unittest.TestCase):
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
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                status = derive_status(job)
            self.assertEqual(status["next"]["action"], "configure_verified_xai_voice")
            errors = status["next"]["provenance_errors"]["shot-001"]
            self.assertTrue(any("shot specification changed" in error for error in errors))

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
                    }
                ),
                encoding="utf-8",
            )
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
    def test_render_binds_exact_frames_and_expected_duration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames_dir = root / "frames"
            frames_dir.mkdir()
            frames = []
            for index, color in enumerate(("black", "white"), start=1):
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
                        f"color=c={color}:s=640x360:d=0.1",
                        "-frames:v",
                        "1",
                        str(image),
                    ],
                    check=True,
                )
                identity = media_identity(image)
                identity["path"] = f"frames/{image.name}"
                frames.append(
                    {
                        "index": index,
                        "image": f"frames/{image.name}",
                        "hold_seconds": 1.0,
                        "action": f"Show state {index}",
                        "visual_state": f"State {index} is clearly visible.",
                        "media_identity": identity,
                    }
                )
            manifest = root / "capture.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "capture_mode": "state_storyboard",
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


if __name__ == "__main__":
    unittest.main()
