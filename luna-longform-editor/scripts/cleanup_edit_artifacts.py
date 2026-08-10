#!/usr/bin/env python3
import argparse
import json
import shutil
from pathlib import Path


def size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def human_size(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_safe_root(root: Path, final_output: Path, allow_broad: bool) -> None:
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Output root not found: {root}")
    if not is_relative_to(final_output, root):
        raise SystemExit("Refusing cleanup: final output is not inside the output root.")
    if root in {Path.home().resolve(), Path("/").resolve()}:
        raise SystemExit("Refusing cleanup: output root is too broad.")
    if root != final_output.parent and not allow_broad:
        raise SystemExit(
            "Refusing broad cleanup outside the final file's job folder. "
            "Use the job manifest or pass --allow-broad-root only after reviewing a dry run."
        )


def collect_folder_targets(root: Path, final_output: Path) -> list[Path]:
    return [
        child
        for child in sorted(root.iterdir())
        if child.resolve() != final_output.resolve()
    ]


def collect_manifest_targets(manifest_path: Path, final_output: Path) -> list[Path]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    job_root_raw = data.get("job_root")
    if not job_root_raw:
        raise SystemExit(f"Manifest has no job_root: {manifest_path}")
    job_root = Path(job_root_raw).expanduser().resolve()
    if not job_root.exists() or not job_root.is_dir():
        raise SystemExit(f"Manifest job_root does not exist: {job_root}")
    if not is_relative_to(final_output, job_root):
        raise SystemExit("Refusing manifest cleanup: final output is outside the job root.")

    targets = []
    for raw in data.get("owned_artifacts", []):
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = job_root / path
        path = path.resolve()
        if path == final_output.resolve() or not path.exists():
            continue
        if not is_relative_to(path, job_root):
            raise SystemExit(f"Refusing manifest cleanup outside job root: {path}")
        if is_relative_to(final_output, path):
            raise SystemExit(
                f"Refusing manifest cleanup: target contains the accepted final output: {path}"
            )
        targets.append(path)
    return sorted(set(targets))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Delete generated Luna artifacts while keeping the accepted final MP4. "
            "Cleanup is job-scoped by default and never scans sibling edit folders."
        )
    )
    parser.add_argument("--final-output", required=True)
    parser.add_argument(
        "--output-root",
        help="Job folder to clean. Defaults to the final MP4's own parent folder.",
    )
    parser.add_argument(
        "--manifest",
        help="Optional .luna-job.json; deletes only its explicit owned_artifacts.",
    )
    parser.add_argument(
        "--allow-broad-root",
        action="store_true",
        help="Permit an explicitly supplied root above the final file's parent.",
    )
    parser.add_argument("--delete", action="store_true", help="Actually delete files.")
    args = parser.parse_args()

    final_output = Path(args.final_output).expanduser().resolve()
    if not final_output.exists() or not final_output.is_file():
        raise SystemExit(f"Final output not found: {final_output}")
    if final_output.suffix.lower() != ".mp4":
        raise SystemExit("Refusing cleanup: final output must be an .mp4 file.")

    if args.manifest:
        manifest = Path(args.manifest).expanduser().resolve()
        if not manifest.exists():
            raise SystemExit(f"Manifest not found: {manifest}")
        targets = collect_manifest_targets(manifest, final_output)
    else:
        root = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else final_output.parent.resolve()
        )
        validate_safe_root(root, final_output, args.allow_broad_root)
        targets = collect_folder_targets(root, final_output)

    reclaimable = sum(size_bytes(target) for target in targets if target.exists())
    action = "Deleting" if args.delete else "Would delete"
    print(f"Keeping: {final_output}")
    print(
        f"{action} {len(targets)} job artifact(s), "
        f"reclaiming about {human_size(reclaimable)}:"
    )
    for target in targets:
        print(f"- {target}")

    if args.delete:
        for target in targets:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        print("Deleted artifacts are not recoverable from this tool.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
