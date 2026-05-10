#!/usr/bin/env python3
import argparse
import shutil
from pathlib import Path


def size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


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


def collect_targets(output_root: Path, final_output: Path) -> list[Path]:
    targets = []

    for child in sorted(output_root.iterdir()):
        if child.resolve() == final_output.resolve():
            continue
        if child.is_dir() and is_relative_to(final_output, child.resolve()):
            for nested in sorted(child.iterdir()):
                if nested.resolve() != final_output.resolve():
                    targets.append(nested)
            continue
        targets.append(child)

    return targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Delete generated Luna edit artifacts after the final MP4 is accepted. "
            "Keeps only the final output file inside the output root."
        )
    )
    parser.add_argument("--final-output", required=True)
    parser.add_argument(
        "--output-root",
        help=(
            "Root folder to clean. Defaults to the parent of the final output's "
            "manual-edit folder when present, otherwise the final output parent."
        ),
    )
    parser.add_argument("--delete", action="store_true", help="Actually delete files.")
    args = parser.parse_args()

    final_output = Path(args.final_output).expanduser().resolve()
    if not final_output.exists() or not final_output.is_file():
        raise SystemExit(f"Final output not found: {final_output}")
    if final_output.suffix.lower() != ".mp4":
        raise SystemExit("Refusing cleanup: final output must be an .mp4 file.")

    if args.output_root:
        output_root = Path(args.output_root).expanduser().resolve()
    elif final_output.parent.name.endswith("_manual_edit"):
        output_root = final_output.parent.parent.resolve()
    else:
        output_root = final_output.parent.resolve()

    if not output_root.exists() or not output_root.is_dir():
        raise SystemExit(f"Output root not found: {output_root}")
    if not is_relative_to(final_output, output_root):
        raise SystemExit("Refusing cleanup: final output is not inside the output root.")
    if output_root == Path.home().resolve() or output_root == Path("/").resolve():
        raise SystemExit("Refusing cleanup: output root is too broad.")

    targets = collect_targets(output_root, final_output)
    reclaimable = sum(size_bytes(target) for target in targets if target.exists())

    action = "Deleting" if args.delete else "Would delete"
    print(f"Keeping: {final_output}")
    print(f"{action} {len(targets)} generated artifact(s), reclaiming about {human_size(reclaimable)}:")
    for target in targets:
        print(f"- {target}")

    if args.delete:
        for target in targets:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
