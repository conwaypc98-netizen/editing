#!/usr/bin/env python3
"""Capture reviewed desktop UI states and render them as a deterministic shot."""

import argparse
import json
import platform
import re
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from production_evidence import identity_matches, media_identity, read_json

_WINDOWS_DPI_CONFIGURED = False


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


def ffmpeg_signal_stats(path: Path, video_filter: str) -> dict:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(path),
            "-vf",
            f"{video_filter},signalstats,metadata=print" if video_filter else "signalstats,metadata=print",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    values = {
        key: float(value)
        for key, value in re.findall(
            r"lavfi\.signalstats\.(YMIN|YAVG|YMAX)=(-?\d+(?:\.\d+)?)",
            result.stderr,
        )
    }
    if set(values) != {"YMIN", "YAVG", "YMAX"}:
        raise SystemExit("Unable to measure the captured window pixels.")
    return values


def image_signal_stats(path: Path) -> dict:
    full = ffmpeg_signal_stats(path, "")
    content = ffmpeg_signal_stats(
        path,
        "crop=w=trunc(iw*0.96/2)*2:h=trunc(ih*0.80/2)*2:"
        "x=trunc(iw*0.02/2)*2:y=trunc(ih*0.15/2)*2",
    )
    result = dict(full)
    result["luma_range"] = round(full["YMAX"] - full["YMIN"], 3)
    for key, value in content.items():
        result[f"content_{key}"] = value
    result["content_luma_range"] = round(content["YMAX"] - content["YMIN"], 3)
    result["non_uniform"] = result["luma_range"] >= 4.0 and result["content_luma_range"] >= 4.0
    return result


def configure_windows_dpi_awareness() -> None:
    """Keep Win32 bounds and capture bitmaps in physical per-monitor pixels."""
    global _WINDOWS_DPI_CONFIGURED
    if _WINDOWS_DPI_CONFIGURED or platform.system() != "Windows":
        return

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if set_context is not None:
        set_context.argtypes = [wintypes.HANDLE]
        set_context.restype = wintypes.BOOL
        if set_context(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            _WINDOWS_DPI_CONFIGURED = True
            return

    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = wintypes.LONG
        shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        set_aware = getattr(user32, "SetProcessDPIAware", None)
        if set_aware is not None:
            set_aware.argtypes = []
            set_aware.restype = wintypes.BOOL
            set_aware()
    _WINDOWS_DPI_CONFIGURED = True


def window_matches(
    window: dict,
    owner: str | None,
    title_contains: str | None,
    window_id: int | None,
) -> bool:
    if window_id is not None and int(window.get("window_id", 0)) != window_id:
        return False
    if owner:
        candidate = str(window.get("owner", "")).casefold().removesuffix(".exe")
        expected = owner.casefold().removesuffix(".exe")
        if candidate != expected:
            return False
    return not title_contains or title_contains.casefold() in str(window.get("title", "")).casefold()


def normalized_owner(value: object) -> str:
    return str(value or "").casefold().removesuffix(".exe")


def target_window_identity(window: dict, platform_name: str) -> dict:
    process_id = int(window.get("process_id") or 0)
    return {
        "platform": platform_name,
        "owner": str(window.get("owner", "")),
        "process_id": process_id or None,
        "initial_window_id": int(window.get("window_id", 0)),
        "initial_title": str(window.get("title", "")),
    }


def window_provenance_errors(manifest: dict, window: dict, frame_platform: str) -> list[str]:
    errors = []
    target = manifest.get("target_window")
    if not isinstance(target, dict):
        return ["manifest target_window provenance is missing"]
    if frame_platform != target.get("platform"):
        errors.append("frame platform does not match the storyboard target")

    target_owner = normalized_owner(target.get("owner"))
    current_owner = normalized_owner(window.get("owner"))
    if target_owner and current_owner != target_owner:
        errors.append("captured owner/process name does not match the storyboard target")

    target_process = int(target.get("process_id") or 0)
    current_process = int(window.get("process_id") or 0)
    if target_process and current_process != target_process:
        errors.append("captured process ID does not match the storyboard target")

    if (
        not target_owner
        and not target_process
        and int(window.get("window_id") or 0) != int(target.get("initial_window_id") or 0)
    ):
        errors.append("captured window ID does not match the unidentified storyboard target")

    bounds = window.get("bounds")
    if not isinstance(bounds, dict) or float(bounds.get("Width", 0)) <= 0 or float(
        bounds.get("Height", 0)
    ) <= 0:
        errors.append("captured window bounds are missing or invalid")
    return errors


def mac_windows(
    owner: str | None,
    title_contains: str | None,
    window_id: int | None,
) -> list[dict]:
    if platform.system() != "Darwin":
        raise SystemExit("The macOS window enumerator can run only on macOS.")
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
    matches = []
    for window in raw:
        window_owner = str(window.get("kCGWindowOwnerName", ""))
        window_title = str(window.get("kCGWindowName", ""))
        bounds = dict(window.get("kCGWindowBounds") or {})
        width = float(bounds.get("Width", 0))
        height = float(bounds.get("Height", 0))
        if int(window.get("kCGWindowLayer", 0)) != 0 or width < 160 or height < 120:
            continue
        entry = {
            "window_id": int(window["kCGWindowNumber"]),
            "owner": window_owner,
            "process_id": int(window.get("kCGWindowOwnerPID", 0)),
            "title": window_title,
            "bounds": bounds,
            "onscreen": bool(window.get("kCGWindowIsOnscreen", False)),
            "minimized": False,
            "area": width * height,
        }
        if window_matches(entry, owner, title_contains, window_id):
            matches.append(entry)
    return sorted(matches, key=lambda item: (item["onscreen"], item["area"]), reverse=True)


def windows_windows(
    owner: str | None,
    title_contains: str | None,
    window_id: int | None,
) -> list[dict]:
    if platform.system() != "Windows":
        raise SystemExit("Win32 window enumeration is available only on Windows.")
    configure_windows_dpi_awareness()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def process_owner(hwnd: int) -> tuple[str, int]:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return "", int(process_id.value)
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return "", int(process_id.value)
            executable = buffer.value.replace("/", "\\").rsplit("\\", 1)[-1]
            return executable.removesuffix(".exe"), int(process_id.value)
        finally:
            kernel32.CloseHandle(handle)

    matches = []

    @callback_type
    def visit(hwnd: int, _lparam: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        bounds = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(bounds)):
            return True
        width = int(bounds.right - bounds.left)
        height = int(bounds.bottom - bounds.top)
        if width < 160 or height < 120:
            return True
        process_name, process_id = process_owner(hwnd)
        visible = bool(user32.IsWindowVisible(hwnd))
        minimized = bool(user32.IsIconic(hwnd))
        entry = {
            "window_id": int(hwnd),
            "owner": process_name,
            "process_id": process_id,
            "title": title,
            "bounds": {
                "X": int(bounds.left),
                "Y": int(bounds.top),
                "Width": width,
                "Height": height,
            },
            "onscreen": visible and not minimized,
            "minimized": minimized,
            "area": width * height,
        }
        if window_matches(entry, owner, title_contains, window_id):
            matches.append(entry)
        return True

    if not user32.EnumWindows(visit, 0):
        error = ctypes.get_last_error()
        if error:
            raise SystemExit(f"EnumWindows failed with Win32 error {error}.")
    return sorted(matches, key=lambda item: (item["onscreen"], item["area"]), reverse=True)


def available_windows(
    owner: str | None,
    title_contains: str | None,
    window_id: int | None,
) -> list[dict]:
    system = platform.system()
    if system == "Darwin":
        return mac_windows(owner, title_contains, window_id)
    if system == "Windows":
        return windows_windows(owner, title_contains, window_id)
    raise SystemExit("Exact-window state capture currently supports macOS and Windows.")


def capture_mac_window(window: dict, image_path: Path) -> None:
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


def capture_windows_window(window: dict, image_path: Path) -> None:
    configure_windows_dpi_awareness()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    hwnd = wintypes.HWND(int(window["window_id"]))
    width = int(window["bounds"]["Width"])
    height = int(window["bounds"]["Height"])
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("width", wintypes.LONG),
            ("height", wintypes.LONG),
            ("planes", wintypes.WORD),
            ("bit_count", wintypes.WORD),
            ("compression", wintypes.DWORD),
            ("image_size", wintypes.DWORD),
            ("x_pixels_per_meter", wintypes.LONG),
            ("y_pixels_per_meter", wintypes.LONG),
            ("colors_used", wintypes.DWORD),
            ("colors_important", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("header", BitmapInfoHeader), ("colors", wintypes.DWORD * 3)]

    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(BitmapInfo),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int

    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        raise SystemExit("GetWindowDC failed for the selected window.")
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old_object = gdi32.SelectObject(memory_dc, bitmap) if memory_dc and bitmap else None
    try:
        if not memory_dc or not bitmap or not old_object:
            raise SystemExit("Unable to create the Win32 capture bitmap.")
        if not user32.PrintWindow(hwnd, memory_dc, 0):
            raise SystemExit(
                "PrintWindow failed. Bring the target app on screen and retake; do not seal a blank fallback."
            )
        # GetDIBits requires the bitmap to be released from every device context first.
        if not gdi32.SelectObject(memory_dc, old_object):
            raise SystemExit("Unable to release the Win32 capture bitmap for pixel extraction.")
        old_object = None
        pixel_size = width * height * 4
        pixels = (ctypes.c_ubyte * pixel_size)()
        info = BitmapInfo(
            header=BitmapInfoHeader(
                size=ctypes.sizeof(BitmapInfoHeader),
                width=width,
                height=-height,
                planes=1,
                bit_count=32,
                compression=0,
                image_size=pixel_size,
                x_pixels_per_meter=0,
                y_pixels_per_meter=0,
                colors_used=0,
                colors_important=0,
            )
        )
        if gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(info), 0) != height:
            raise SystemExit("GetDIBits did not return the complete window image.")
        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as handle:
            bitmap_path = Path(handle.name)
            handle.write(struct.pack("<2sIHHI", b"BM", 54 + pixel_size, 0, 0, 54))
            handle.write(
                struct.pack(
                    "<IiiHHIIiiII",
                    40,
                    width,
                    -height,
                    1,
                    32,
                    0,
                    pixel_size,
                    0,
                    0,
                    0,
                    0,
                )
            )
            handle.write(bytes(pixels))
        try:
            if image_path.suffix.lower() == ".bmp":
                bitmap_path.replace(image_path)
            else:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(bitmap_path),
                        "-vf",
                        "format=rgb24",
                        "-frames:v",
                        "1",
                        str(image_path),
                    ],
                    check=True,
                )
        finally:
            if bitmap_path.exists():
                bitmap_path.unlink()
    finally:
        if old_object:
            gdi32.SelectObject(memory_dc, old_object)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def capture_window_image(window: dict, image_path: Path) -> None:
    if platform.system() == "Darwin":
        capture_mac_window(window, image_path)
        return
    if platform.system() == "Windows":
        capture_windows_window(window, image_path)
        return
    raise SystemExit("Exact-window state capture currently supports macOS and Windows.")


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
    if not args.owner and not args.title_contains and args.window_id is None:
        raise SystemExit("Capture requires --owner, --title-contains, or --window-id.")

    if manifest_path.is_file() and not args.replace_manifest:
        manifest = read_json(manifest_path)
        if manifest.get("schema_version") != 2 or manifest.get("capture_mode") != "state_storyboard":
            raise SystemExit("Existing manifest is not a schema-version 2 Luna state storyboard.")
        if not isinstance(manifest.get("frames"), list):
            raise SystemExit("Existing storyboard frames are invalid.")
    else:
        manifest = None

    retake_index = None
    if manifest is not None:
        matching_frames = [
            index
            for index, frame in enumerate(manifest["frames"])
            if resolve_frame(str(frame.get("image", "")), manifest_path) == image_path.resolve()
        ]
        if len(matching_frames) > 1:
            raise SystemExit("Storyboard contains more than one frame for the requested image path.")
        if matching_frames:
            retake_index = matching_frames[0]

    if image_path.exists() and not args.overwrite:
        raise SystemExit(f"Capture image already exists: {image_path}")
    windows = available_windows(args.owner, args.title_contains, args.window_id)
    if not windows:
        raise SystemExit(
            f"No capturable window found for owner={args.owner!r}, "
            f"title_contains={args.title_contains!r}, window_id={args.window_id!r}."
        )
    if len(windows) > 1 and args.window_id is None:
        candidates = [
            {
                "window_id": item.get("window_id"),
                "owner": item.get("owner"),
                "process_id": item.get("process_id"),
                "title": item.get("title"),
                "onscreen": item.get("onscreen"),
            }
            for item in windows[:10]
        ]
        raise SystemExit(
            "Selector matched multiple windows. Choose the intended exact --window-id:\n"
            + json.dumps(candidates, indent=2)
        )
    window = windows[0]
    if window.get("minimized"):
        raise SystemExit("Restore the selected window before capture; minimized pixels are not trustworthy.")
    platform_name = platform.system()
    if manifest is not None:
        provenance_errors = window_provenance_errors(manifest, window, platform_name)
        if provenance_errors:
            raise SystemExit("Storyboard target changed:\n- " + "\n- ".join(provenance_errors))

    image_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f"{image_path.stem}-capture-",
        suffix=image_path.suffix or ".png",
        dir=image_path.parent,
        delete=False,
    ) as handle:
        staged_image = Path(handle.name)
    staged_image.unlink()
    try:
        capture_window_image(window, staged_image)
        if not staged_image.is_file() or staged_image.stat().st_size == 0:
            raise SystemExit("Window capture did not create a usable image.")
        signal_stats = image_signal_stats(staged_image)
        if not signal_stats["non_uniform"]:
            raise SystemExit(
                "Captured pixels are nearly uniform, which usually means a blank/failed window capture. Retake it."
            )
        staged_image.replace(image_path)
    finally:
        if staged_image.exists():
            staged_image.unlink()

    if manifest is None:
        manifest = {
            "schema_version": 2,
            "capture_mode": "state_storyboard",
            "platform": platform_name,
            "selector": {
                "owner": args.owner,
                "title_contains": args.title_contains,
                "window_id": args.window_id,
            },
            "target_window": target_window_identity(window, platform_name),
            "frames": [],
        }

    identity = media_identity(image_path)
    identity["path"] = relative_or_absolute(image_path, manifest_path.parent)
    frame_index = retake_index + 1 if retake_index is not None else len(manifest["frames"]) + 1
    frame = {
        "index": frame_index,
        "image": relative_or_absolute(image_path, manifest_path.parent),
        "hold_seconds": round(float(args.hold_seconds), 3),
        "action": args.action.strip(),
        "visual_state": args.visual_state.strip(),
        "platform": platform_name,
        "window": window,
        "signal_stats": signal_stats,
        "media_identity": identity,
        "captured_at": utc_now(),
    }
    if retake_index is None:
        manifest["frames"].append(frame)
    else:
        manifest["frames"][retake_index] = frame
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
    if manifest.get("schema_version") != 2 or manifest.get("capture_mode") != "state_storyboard":
        raise SystemExit("Manifest is not a schema-version 2 Luna state storyboard.")
    if not isinstance(manifest.get("target_window"), dict):
        raise SystemExit("Storyboard target_window provenance is missing.")
    if manifest.get("platform") != manifest["target_window"].get("platform"):
        raise SystemExit("Storyboard platform does not match its target-window provenance.")
    frames = manifest.get("frames")
    if not isinstance(frames, list) or not frames:
        raise SystemExit("Storyboard must contain at least one captured frame.")

    resolved = []
    errors = []
    seen_images = set()
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
        if image_path in seen_images:
            errors.append(f"frame {expected_index}: image path is reused by another frame")
        seen_images.add(image_path)
        if hold < 0.1 or hold > 30:
            errors.append(f"frame {expected_index}: hold_seconds must be between 0.1 and 30")
        if not action or not visual_state:
            errors.append(f"frame {expected_index}: action and visual_state are required")
        expected_identity = dict(frame.get("media_identity") or {})
        expected_identity["path"] = str(image_path)
        if not identity_matches(expected_identity, image_path):
            errors.append(f"frame {expected_index}: image bytes changed after capture")
        frame_platform = str(frame.get("platform", ""))
        frame_window = frame.get("window")
        if not isinstance(frame_window, dict):
            errors.append(f"frame {expected_index}: exact-window provenance is missing")
        else:
            for error in window_provenance_errors(manifest, frame_window, frame_platform):
                errors.append(f"frame {expected_index}: {error}")
        stored_stats = frame.get("signal_stats")
        if not isinstance(stored_stats, dict) or stored_stats.get("non_uniform") is not True:
            errors.append(f"frame {expected_index}: passing capture pixel statistics are missing")
        try:
            current_stats = image_signal_stats(image_path)
        except (subprocess.CalledProcessError, SystemExit) as error:
            errors.append(f"frame {expected_index}: pixel validation failed: {error}")
            continue
        if not current_stats["non_uniform"]:
            errors.append(f"frame {expected_index}: current image pixels look blank or uniform")
        resolved.append((image_path, hold, frame, current_stats))
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
    for index, (image_path, hold, _frame, _stats) in enumerate(resolved):
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
        "schema_version": 2,
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
            for image_path, hold, frame, _stats in resolved
        ],
        "passed": True,
        "rendered_at": utc_now(),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def list_windows(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            available_windows(args.owner, args.title_contains, args.window_id),
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture exact macOS/Windows app states and render a reviewable tutorial shot."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    windows = subparsers.add_parser("windows", help="List matching capturable windows.")
    windows.add_argument("--owner")
    windows.add_argument("--title-contains")
    windows.add_argument("--window-id", type=int)
    windows.set_defaults(func=list_windows)

    capture = subparsers.add_parser("capture", help="Append one exact window state to a storyboard.")
    capture.add_argument("--owner")
    capture.add_argument("--title-contains")
    capture.add_argument("--window-id", type=int)
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
