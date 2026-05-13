#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=True,
        capture_output=capture,
        text=capture,
    )


def probe(path: Path) -> tuple[int, int, float]:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    return int(stream["width"]), int(stream["height"]), duration


def load_plan(path: Path, duration: float, max_zoom: float) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    zooms = data.get("zooms", data if isinstance(data, list) else [])
    if not isinstance(zooms, list):
        raise SystemExit("Zoom plan must be a list or contain a 'zooms' list.")

    normalized = []
    last_end = 0.0
    for index, item in enumerate(zooms, start=1):
        start = float(item["start"])
        end = float(item["end"])
        if end <= start:
            raise SystemExit(f"Zoom {index} has end <= start.")
        if start < last_end:
            raise SystemExit(f"Zoom {index} overlaps the previous zoom.")
        if start >= duration:
            continue
        end = min(end, duration)

        center_x = min(1.0, max(0.0, float(item["center_x"])))
        center_y = min(1.0, max(0.0, float(item["center_y"])))
        zoom = min(max_zoom, max(1.0, float(item.get("zoom", 1.12))))
        if zoom <= 1.001:
            last_end = end
            continue

        normalized.append(
            {
                "start": max(0.0, start),
                "end": end,
                "center_x": center_x,
                "center_y": center_y,
                "zoom": zoom,
                "transition": float(item.get("transition", 0.45)),
                "label": str(item.get("label", f"zoom-{index}")),
            }
        )
        last_end = end
    return normalized


def ff_num(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def gate_expr(start: float, end: float, transition: float) -> str:
    duration = end - start
    ramp = max(0.001, min(transition, duration / 3.0))
    s = ff_num(start)
    si = ff_num(start + ramp)
    eo = ff_num(end - ramp)
    e = ff_num(end)
    r = ff_num(ramp)
    return (
        f"if(lt(t,{s}),0,"
        f"if(lt(t,{si}),(t-{s})/{r},"
        f"if(lt(t,{eo}),1,"
        f"if(lt(t,{e}),({e}-t)/{r},0))))"
    )


def build_exprs(zooms: list[dict]) -> tuple[str, str, str]:
    z_expr = "1"
    cx_expr = "0.5"
    cy_expr = "0.5"
    for zoom in zooms:
        gate = gate_expr(zoom["start"], zoom["end"], zoom["transition"])
        z_delta = ff_num(zoom["zoom"] - 1.0)
        x_delta = ff_num(zoom["center_x"] - 0.5)
        y_delta = ff_num(zoom["center_y"] - 0.5)
        z_expr = f"({z_expr})+({z_delta})*({gate})"
        cx_expr = f"({cx_expr})+({x_delta})*({gate})"
        cy_expr = f"({cy_expr})+({y_delta})*({gate})"
    return z_expr, cx_expr, cy_expr


def copy_video(source: Path, output: Path) -> None:
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(source),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply sparse smooth focus zooms to a rendered Luna tutorial video."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--zoom-plan", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--crf", default="18")
    parser.add_argument("--preset", default="veryfast")
    parser.add_argument("--max-zoom", type=float, default=1.22)
    args = parser.parse_args()

    source = Path(args.input).expanduser().resolve()
    plan_path = Path(args.zoom_plan).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()

    if not source.exists():
        raise SystemExit(f"Input not found: {source}")
    if not plan_path.exists():
        raise SystemExit(f"Zoom plan not found: {plan_path}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required.")

    width, height, duration = probe(source)
    zooms = load_plan(plan_path, duration, args.max_zoom)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not zooms:
        copy_video(source, output)
        print(f"No focus zooms in plan; copied input to: {output}")
        return 0

    z_expr, cx_expr, cy_expr = build_exprs(zooms)
    filter_complex = (
        "[0:v]"
        f"scale=w='trunc({width}*({z_expr})/2)*2':"
        f"h='trunc({height}*({z_expr})/2)*2':eval=frame,"
        f"crop={width}:{height}:"
        f"x='min(max(({cx_expr})*iw-{width}/2,0),iw-{width})':"
        f"y='min(max(({cy_expr})*ih-{height}/2,0),ih-{height})',"
        "setsar=1,format=yuv420p[vout]"
    )

    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(source),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            args.preset,
            "-crf",
            args.crf,
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "1024",
            str(output),
        ]
    )

    print(f"Applied {len(zooms)} focus zoom region(s): {output}")
    for zoom in zooms:
        print(
            f"- {zoom['start']:.2f}-{zoom['end']:.2f}s "
            f"zoom={zoom['zoom']:.2f} center=({zoom['center_x']:.2f},{zoom['center_y']:.2f}) "
            f"{zoom['label']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
