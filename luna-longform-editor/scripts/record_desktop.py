#!/usr/bin/env python3
import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def state_path_for(output: Path, explicit: str | None) -> Path:
    return Path(explicit).expanduser().resolve() if explicit else output.with_suffix(".recording.json")


def video_filter(width: int, height: int, fps: int, resize: str) -> str:
    if resize == "none":
        return f"fps={fps},format=yuv420p"
    if resize == "crop":
        return (
            f"scale=w={width}:h={height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},format=yuv420p"
        )
    return (
        f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},format=yuv420p"
    )


def capture_input(args: argparse.Namespace) -> list[str]:
    system = platform.system()
    if system == "Darwin":
        return [
            "-f",
            "avfoundation",
            "-framerate",
            str(args.fps),
            "-capture_cursor",
            "1" if args.cursor else "0",
            "-capture_mouse_clicks",
            "1" if args.mouse_clicks else "0",
            "-i",
            f"{args.screen_device}:none",
        ]
    if system == "Windows":
        return [
            "-f",
            "gdigrab",
            "-framerate",
            str(args.fps),
            "-draw_mouse",
            "1" if args.cursor else "0",
            "-i",
            "desktop",
        ]
    display = args.display or os.environ.get("DISPLAY", ":0.0")
    return [
        "-f",
        "x11grab",
        "-framerate",
        str(args.fps),
        "-draw_mouse",
        "1" if args.cursor else "0",
        "-i",
        display,
    ]


def build_command(args: argparse.Namespace, output: Path, duration: float | None) -> list[str]:
    command = ["ffmpeg", "-hide_banner", "-y"]
    command.extend(capture_input(args))
    if duration is not None:
        command.extend(["-t", f"{duration:.3f}"])
    command.extend(
        [
            "-an",
            "-vf",
            video_filter(args.width, args.height, args.fps, args.resize),
            "-c:v",
            "libx264",
            "-preset",
            args.preset,
            "-crf",
            str(args.crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return command


def verify_video(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-show_entries",
            "format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise SystemExit(f"Recording has no video stream: {path}")
    if float(data.get("format", {}).get("duration", 0.0)) <= 0.1:
        raise SystemExit(f"Recording is too short or invalid: {path}")
    return data


def ensure_tools() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required.")


def list_devices(_: argparse.Namespace) -> int:
    ensure_tools()
    if platform.system() == "Darwin":
        subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""])
    else:
        print(f"Desktop capture backend: {platform.system()}")
    return 0


def blocking_record(args: argparse.Namespace) -> int:
    ensure_tools()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = build_command(args, output, args.duration)
    subprocess.run(command, check=True)
    print(json.dumps(verify_video(output), indent=2))
    return 0


def start_recording(args: argparse.Namespace) -> int:
    ensure_tools()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    state_path = state_path_for(output, args.state)
    if state_path.exists():
        raise SystemExit(f"Recording state already exists: {state_path}")
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    log_path = output.with_suffix(".recording.log")
    log_handle = log_path.open("wb")
    kwargs = {"stdout": log_handle, "stderr": subprocess.STDOUT}
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(build_command(args, partial, None), **kwargs)
    log_handle.close()
    time.sleep(0.4)
    if process.poll() is not None:
        raise SystemExit(f"Recorder exited immediately. See: {log_path}")
    write_json(
        state_path,
        {
            "pid": process.pid,
            "started_at": now_iso(),
            "output": str(output),
            "partial": str(partial),
            "log": str(log_path),
            "platform": platform.system(),
        },
    )
    print(state_path)
    return 0


def stop_recording(args: argparse.Namespace) -> int:
    state_path = Path(args.state).expanduser().resolve()
    if not state_path.exists():
        raise SystemExit(f"Recording state not found: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pid = int(state["pid"])
    try:
        if state.get("platform") == "Windows":
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(pid, signal.SIGINT)
    except ProcessLookupError:
        pass

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        if state.get("platform") == "Windows":
            os.kill(pid, signal.SIGTERM)
        else:
            os.killpg(pid, signal.SIGTERM)
        time.sleep(0.5)

    partial = Path(state["partial"])
    output = Path(state["output"])
    if not partial.exists():
        raise SystemExit(f"Partial recording is missing. See: {state.get('log')}")
    partial.replace(output)
    verification = verify_video(output)
    state_path.unlink()
    print(json.dumps({"output": str(output), "probe": verification}, indent=2))
    return 0


def show_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state).expanduser().resolve()
    if not state_path.exists():
        print(json.dumps({"recording": False, "state": str(state_path)}, indent=2))
        return 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pid = int(state["pid"])
    running = True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        running = False
    print(json.dumps({"recording": running, **state}, indent=2))
    return 0 if running else 1


def add_capture_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--resize", choices=("pad", "crop", "none"), default="pad")
    parser.add_argument("--screen-device", default="2")
    parser.add_argument("--display")
    parser.add_argument("--preset", default="veryfast")
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--cursor", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mouse-clicks", action=argparse.BooleanOptionalAction, default=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-platform, voiceover-ready desktop recorder.")
    sub = parser.add_subparsers(dest="command", required=True)
    devices = sub.add_parser("devices")
    devices.set_defaults(func=list_devices)
    record = sub.add_parser("record")
    add_capture_arguments(record)
    record.add_argument("--duration", type=float, required=True)
    record.set_defaults(func=blocking_record)
    start = sub.add_parser("start")
    add_capture_arguments(start)
    start.add_argument("--state")
    start.set_defaults(func=start_recording)
    stop = sub.add_parser("stop")
    stop.add_argument("--state", required=True)
    stop.add_argument("--timeout", type=float, default=20.0)
    stop.set_defaults(func=stop_recording)
    status = sub.add_parser("status")
    status.add_argument("--state", required=True)
    status.set_defaults(func=show_status)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
