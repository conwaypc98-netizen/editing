#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_DIR / "templates"
MARKER_NAME = ".luna-job.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return slug or "luna-video"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_identity(path: Path) -> dict:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        head = handle.read(1024 * 1024)
        digest.update(head)
        if stat.st_size > 1024 * 1024:
            handle.seek(max(0, stat.st_size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "edge_sha256": digest.hexdigest(),
    }


def final_identity(path: Path) -> dict:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def resolve_job(path: str) -> tuple[Path, Path, dict]:
    candidate = Path(path).expanduser().resolve()
    marker = candidate if candidate.name == MARKER_NAME else candidate / MARKER_NAME
    if not marker.exists():
        raise SystemExit(f"Luna job marker not found: {marker}")
    data = read_json(marker)
    job_root = Path(data.get("job_root", marker.parent)).expanduser().resolve()
    if job_root != marker.parent.resolve():
        raise SystemExit("Job marker path and job_root disagree.")
    return job_root, marker, data


def update_job(marker: Path, data: dict, status: str, **fields) -> None:
    data.update(fields)
    data["status"] = status
    data["updated_at"] = now_iso()
    write_json(marker, data)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def probe_streams(source: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
            "-show_entries",
            "format=duration,size",
            "-of",
            "json",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def find_transcriber_python() -> Path | None:
    explicit = os.environ.get("LUNA_EDITOR_TRANSCRIBE_PYTHON")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.exists():
            return candidate

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
    return next((path for path in candidates if path.exists()), None)


def init_job(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve() if args.source else None
    if args.mode == "edit" and (source is None or not source.is_file()):
        raise SystemExit("Edit mode requires an existing --source video.")
    if source is not None and not source.is_file():
        raise SystemExit(f"Source not found: {source}")

    title = args.title or (source.stem if source else "Luna autonomous production")
    slug = slugify(args.slug or title)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    jobs_root = Path(args.jobs_root).expanduser().resolve()
    job_root = jobs_root / f"{slug}-{timestamp}"
    if job_root.exists():
        raise SystemExit(f"Job already exists: {job_root}")

    for directory in (
        "analysis/frames",
        "plans",
        "renders",
        "qa",
        "voice",
        "shots",
        "delivery",
    ):
        (job_root / directory).mkdir(parents=True, exist_ok=True)

    project = read_json(TEMPLATE_DIR / "project.json")
    project["mode"] = args.mode
    project["title"] = title
    project["source"] = str(source) if source else None
    write_json(job_root / "project.json", project)
    shutil.copy2(SKILL_DIR / "channel_profile.json", job_root / "channel_profile.json")
    shutil.copy2(TEMPLATE_DIR / "shot_plan.json", job_root / "plans" / "shot_plan.json")

    manifest = {
        "schema_version": 1,
        "job_id": job_root.name,
        "job_root": str(job_root),
        "mode": args.mode,
        "source": source_identity(source) if source else None,
        "status": "initialized",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "owned_artifacts": [
            ".luna-job.json",
            "README.md",
            "analysis",
            "channel_profile.json",
            "plans",
            "project.json",
            "renders",
            "qa",
            "voice",
            "shots",
        ],
        "final_output": None,
    }
    write_json(job_root / MARKER_NAME, manifest)
    (job_root / "README.md").write_text(
        "# Luna Production Job\n\n"
        "Edit `project.json` first. Evidence lives in `analysis/`, reasoned plans in "
        "`plans/`, drafts in `renders/`, acceptance evidence in `qa/`, and only "
        "accepted deliverables in `delivery/`.\n",
        encoding="utf-8",
    )
    print(job_root)
    return 0


def prepare_job(args: argparse.Namespace) -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required.")
    job_root, marker, manifest = resolve_job(args.job)
    source_info = manifest.get("source")
    if not source_info:
        raise SystemExit("This job has no source recording. Use the shot-plan workflow.")
    source = Path(source_info["path"])
    if not source.exists():
        raise SystemExit(f"Source moved or was deleted: {source}")
    if source_identity(source) != source_info:
        raise SystemExit("Source identity changed since job creation. Create a new job.")

    analysis = job_root / "analysis"
    probe = probe_streams(source)
    write_json(analysis / "probe.json", probe)
    has_audio = any(s.get("codec_type") == "audio" for s in probe.get("streams", []))
    transcript = analysis / "transcript.json"

    if has_audio and not args.skip_transcript:
        audio = analysis / "audio_16k.wav"
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-vn",
                str(audio),
            ]
        )
        transcriber_python = find_transcriber_python()
        if transcriber_python is None:
            raise SystemExit(
                "Transcription environment is missing. Run setup_windows.ps1 or "
                "create ~/.codex/tools/luna-longform-editor/transcribe-venv."
            )
        run(
            [
                str(transcriber_python),
                str(SCRIPT_DIR / "transcribe_with_faster_whisper.py"),
                str(audio),
                "--out-dir",
                str(analysis),
                "--model",
                args.model,
            ]
        )

    dossier_cmd = [
        sys.executable,
        str(SCRIPT_DIR / "build_editorial_dossier.py"),
        "--source",
        str(source),
        "--output-dir",
        str(analysis),
        "--frame-interval",
        str(args.frame_interval),
    ]
    if transcript.exists():
        dossier_cmd.extend(["--transcript-json", str(transcript)])
    run(dossier_cmd)
    update_job(marker, manifest, "evidence_ready")
    print(analysis / "EDITORIAL_REVIEW.md")
    return 0


def validate_plan(args: argparse.Namespace) -> int:
    job_root, marker, manifest = resolve_job(args.job)
    plan = Path(args.plan).expanduser().resolve()
    transcript = job_root / "analysis" / "transcript.json"
    dossier = job_root / "analysis" / "dossier.json"
    project = job_root / "project.json"
    report = job_root / "qa" / "edit_plan_validation.json"
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "validate_edit_plan.py"),
        "--plan",
        str(plan),
        "--project",
        str(project),
        "--report",
        str(report),
    ]
    if transcript.exists():
        cmd.extend(["--transcript-json", str(transcript)])
    if dossier.exists():
        cmd.extend(["--dossier", str(dossier)])
    completed = subprocess.run(cmd)
    if completed.returncode == 0:
        update_job(marker, manifest, "plan_validated", validated_plan=str(plan))
    return completed.returncode


def accept_final(args: argparse.Namespace) -> int:
    job_root, marker, manifest = resolve_job(args.job)
    source = Path(args.final).expanduser().resolve()
    report = Path(args.qa_report).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Final candidate not found: {source}")
    if not report.is_file():
        raise SystemExit(f"QA report not found: {report}")
    qa = read_json(report)
    if qa.get("passed") is not True:
        raise SystemExit("Final candidate cannot be accepted: QA report is not passing.")
    reported_input = Path(str(qa.get("input", ""))).expanduser().resolve()
    if reported_input != source:
        raise SystemExit("Final candidate cannot be accepted: QA report belongs to a different file.")
    identity = final_identity(source)
    if qa.get("media_identity") != identity:
        raise SystemExit("Final candidate cannot be accepted: the file changed after QA.")
    delivery = job_root / "delivery" / f"{slugify(job_root.name)}_final.mp4"
    shutil.copy2(source, delivery)
    update_job(
        marker,
        manifest,
        "accepted",
        final_output=str(delivery),
        final_identity=final_identity(delivery),
        acceptance_report=str(report),
    )
    print(delivery)
    return 0


def show_status(args: argparse.Namespace) -> int:
    job_root, _, manifest = resolve_job(args.job)
    payload = {
        "job_root": str(job_root),
        "status": manifest.get("status"),
        "mode": manifest.get("mode"),
        "source": manifest.get("source", {}).get("path") if manifest.get("source") else None,
        "project": str(job_root / "project.json"),
        "dossier": str(job_root / "analysis" / "dossier.json"),
        "edit_plan": manifest.get("validated_plan"),
        "final_output": manifest.get("final_output"),
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Job-scoped orchestration for evidence-driven Luna video production."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--mode", choices=("edit", "synthetic"), default="edit")
    init.add_argument("--source")
    init.add_argument("--title")
    init.add_argument("--slug")
    init.add_argument("--jobs-root", default="output/luna_jobs")
    init.set_defaults(func=init_job)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--job", required=True)
    prepare.add_argument("--model", default="small.en")
    prepare.add_argument("--frame-interval", type=float, default=4.0)
    prepare.add_argument("--skip-transcript", action="store_true")
    prepare.set_defaults(func=prepare_job)

    validate = sub.add_parser("validate-plan")
    validate.add_argument("--job", required=True)
    validate.add_argument("--plan", required=True)
    validate.set_defaults(func=validate_plan)

    accept = sub.add_parser("accept")
    accept.add_argument("--job", required=True)
    accept.add_argument("--final", required=True)
    accept.add_argument("--qa-report", required=True)
    accept.set_defaults(func=accept_final)

    status = sub.add_parser("status")
    status.add_argument("--job", required=True)
    status.set_defaults(func=show_status)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
