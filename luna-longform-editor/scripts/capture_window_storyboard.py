#!/usr/bin/env python3
"""Capture reviewed desktop UI states and render them as a deterministic shot."""

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from production_evidence import identity_matches, media_identity, read_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe_video(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def mac_windows(owner: str, title_contains: str | None) -> list[dict]:
    if platform.system() != "Darwin":
        raise SystemExit("Window-state capture is currently available only on macOS.")
    try:
        import Quartz
    except ImportError as error:
        raise SystemExit(
            "macOS window capture requires PyObjC Quartz in the active Python environment."
        ) from error

    raw = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll,
        Quartz.kCGNullWindowID,
    )
    owner_key = owner.casefold()
    title_key = title_contains.casefold() if title_contains else None
    matches = []
    for window in raw:
        window_owner = str(window.get("kCGWindowOwnerName", ""))
        window_title = str(window.get("kCGWindowName", ""))
        bounds = dict(window.get("kCGWindowBounds") or {})
        width = float(bounds.get("Width", 0))
        height = float(bounds.get("Height", 0))
        if window_owner.casefold() != owner_key:
            continue
        if title_key and title_key not in window_title.casefold():
            continue
        if int(window.get("kCGWindowLayer", 0)) != 0 or width < 160 or height < 120:
            continue
        matches.append(
            {
                "window_id": int(window["kCGWindowNumber"]),
                "owner": window_owner,
                "title": window_title,
                "bounds": bounds,
                "onscreen": bool(window.get("kCGWindowIsOnscreen", False)),
                "area": width * height,
            }
        )
    return sorted(matches, key=lambda item: (item["onscreen"], item["area"]), reverse=True)


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def resolve_frame(path_value: str, manifest_path: Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def capture_frame(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().absolute()
    image_path = Path(args.image).expanduser().absolute()
    if image_path.exists() and not args.overwrite:
        raise SystemExit(f"Capture image already exists: {image_path}")
    windows = mac_windows(args.owner, args.title_contains)
    if not windows:
        raise SystemExit(
            f"No capturable window found for owner={args.owner!r}, "
            f"title_contains={args.title_contains!r}."
        )
    window = windows[0]
    image_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "/usr/sbin/screencapture",
            "-x",
            "-o",
            f"-l{window['window_id']}",
            str(image_path),
        ],
        check=True,
    )
    if not image_path.is_file() or image_path.stat().st_size == 0:
        raise SystemExit("Window capture did not create a usable image.")

    if manifest_path.is_file() and not args.replace_manifest:
        manifest = read_json(manifest_path)
        if manifest.get("schema_version") != 1 or manifest.get("capture_mode") != "state_storyboard":
            raise SystemExit("Existing manifest is not a Luna state storyboard.")
    else:
        manifest = {
            "schema_version": 1,
            "capture_mode": "state_storyboard",
            "window": {
                "owner": args.owner,
                "title_contains": args.title_contains,
            },
            "frames": [],
        }

    identity = media_identity(image_path)
    identity["path"] = relative_or_absolute(image_path, manifest_path.parent)
    frame = {
        "index": len(manifest["frames"]) + 1,
        "image": relative_or_absolute(image_path, manifest_path.parent),
        "hold_seconds": round(float(args.hold_seconds), 3),
        "action": args.action.strip(),
        "visual_state": args.visual_state.strip(),
        "window": window,
        "media_identity": identity,
        "captured_at": utc_now(),
    }
    manifest["frames"].append(frame)
    manifest["updated_at"] = utc_now()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(frame, indent=2))
    return 0


def render_storyboard(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().absolute()
    output = Path(args.output).expanduser().absolute()
    report_path = (
        Path(args.report).expanduser().absolute()
        if args.report
        else output.with_suffix(".storyboard-report.json")
    )
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("capture_mode") != "state_storyboard":
        raise SystemExit("Manifest is not a Luna state storyboard.")
    frames = manifest.get("frames")
    if not isinstance(frames, list) or not frames:
        raise SystemExit("Storyboard must contain at least one captured frame.")

    resolved = []
    errors = []
    for expected_index, frame in enumerate(frames, start=1):
        image_path = resolve_frame(str(frame.get("image", "")), manifest_path)
        hold = float(frame.get("hold_seconds", 0))
        action = str(frame.get("action", "")).strip()
        visual_state = str(frame.get("visual_state", "")).strip()
        if frame.get("index") != expected_index:
            errors.append(f"frame {expected_index}: index is not sequential")
        if not image_path.is_file():
            errors.append(f"frame {expected_index}: image missing: {image_path}")
            continue
        if hold < 0.1 or hold > 30:
            errors.append(f"frame {expected_index}: hold_seconds must be between 0.1 and 30")
        if not action or not visual_state:
            errors.append(f"frame {expected_index}: action and visual_state are required")
        expected_identity = dict(frame.get("media_identity") or {})
        expected_identity["path"] = str(image_path)
        if not identity_matches(expected_identity, image_path):
            errors.append(f"frame {expected_index}: image bytes changed after capture")
        resolved.append((image_path, hold, frame))
    if errors:
        raise SystemExit("Storyboard validation failed:\n- " + "\n- ".join(errors))

    try:
        width, height = [int(part) for part in args.resolution.lower().split("x", 1)]
    except (TypeError, ValueError) as error:
        raise SystemExit("--resolution must look like 2560x1440") from error
    if width < 640 or height < 360:
        raise SystemExit("Storyboard resolution must be at least 640x360.")

    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    filters = []
    concat_inputs = []
    for index, (image_path, hold, _frame) in enumerate(resolved):
        command.extend(
            [
                "-loop",
                "1",
                "-framerate",
                str(args.fps),
                "-t",
                f"{hold:.3f}",
                "-i",
                str(image_path),
            ]
        )
        filters.append(
            f"[{index}:v]scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1,fps={args.fps},format=yuv420p[v{index}]"
        )
        concat_inputs.append(f"[v{index}]")
    filters.append(
        "".join(concat_inputs)
        + f"concat=n={len(resolved)}:v=1:a=0,format=yuv420p[v]"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            args.preset,
            "-crf",
            str(args.crf),
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    subprocess.run(command, check=True)
    probe = probe_video(output)
    report = {
        "schema_version": 1,
        "capture_mode": "state_storyboard",
        "manifest": str(manifest_path),
        "manifest_identity": media_identity(manifest_path),
        "output": str(output),
        "output_identity": media_identity(output),
        "frame_count": len(resolved),
        "expected_duration_seconds": round(sum(item[1] for item in resolved), 3),
        "probe": probe,
        "timeline": [
            {
                "index": frame["index"],
                "image": str(image_path),
                "hold_seconds": hold,
                "action": frame["action"],
                "visual_state": frame["visual_state"],
            }
            for image_path, hold, frame in resolved
        ],
        "passed": True,
        "rendered_at": utc_now(),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def list_windows(args: argparse.Namespace) -> int:
    print(json.dumps(mac_windows(args.owner, args.title_contains), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture exact macOS app states and render a reviewable tutorial shot."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    windows = subparsers.add_parser("windows", help="List matching capturable windows.")
    windows.add_argument("--owner", required=True)
    windows.add_argument("--title-contains")
    windows.set_defaults(func=list_windows)

    capture = subparsers.add_parser("capture", help="Append one exact window state to a storyboard.")
    capture.add_argument("--owner", required=True)
    capture.add_argument("--title-contains")
    capture.add_argument("--manifest", required=True)
    capture.add_argument("--image", required=True)
    capture.add_argument("--hold-seconds", type=float, required=True)
    capture.add_argument("--action", required=True)
    capture.add_argument("--visual-state", required=True)
    capture.add_argument("--overwrite", action="store_true")
    capture.add_argument("--replace-manifest", action="store_true")
    capture.set_defaults(func=capture_frame)

    render = subparsers.add_parser("render", help="Render a captured storyboard as MP4.")
    render.add_argument("--manifest", required=True)
    render.add_argument("--output", required=True)
    render.add_argument("--report")
    render.add_argument("--resolution", default="2560x1440")
    render.add_argument("--fps", type=int, default=30)
    render.add_argument("--preset", default="medium")
    render.add_argument("--crf", type=int, default=18)
    render.set_defaults(func=render_storyboard)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Media command failed with exit code {error.returncode}.") from error


if __name__ == "__main__":
    sys.exit(main())
