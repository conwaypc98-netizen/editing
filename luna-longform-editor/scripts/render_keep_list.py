#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def parse_segments(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = data.get("keep", data if isinstance(data, list) else [])
    if not isinstance(segments, list) or not segments:
        raise SystemExit("Keep-list JSON must be a list or contain a non-empty 'keep' list.")

    normalized = []
    last_end = -1.0
    for index, seg in enumerate(segments):
        start = float(seg["start"])
        end = float(seg["end"])
        label = str(seg.get("label", f"segment-{index + 1}"))
        if end <= start:
            raise SystemExit(f"Invalid segment {index + 1}: end <= start")
        if start < last_end:
            raise SystemExit(f"Invalid segment {index + 1}: keep list is not chronological")
        last_end = end
        normalized.append({"start": start, "end": end, "label": label})
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--keep-list", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--crf", default="18")
    parser.add_argument(
        "--audio-fade-ms",
        type=float,
        default=18.0,
        help="Tiny fade at cut edges to avoid clicks/static without sounding like a dissolve.",
    )
    parser.add_argument("--target-lufs", type=float, default=-16.0)
    parser.add_argument("--true-peak", type=float, default=-1.5)
    parser.add_argument("--loudness-range", type=float, default=11.0)
    args = parser.parse_args()

    source = Path(args.input)
    keep_list = Path(args.keep_list)
    output = Path(args.output)

    if not source.exists():
        raise SystemExit(f"Input not found: {source}")
    if not keep_list.exists():
        raise SystemExit(f"Keep list not found: {keep_list}")
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found")

    output.parent.mkdir(parents=True, exist_ok=True)
    segments = parse_segments(keep_list)

    fade = max(0.0, args.audio_fade_ms / 1000.0)
    filter_parts = []
    concat_inputs = []

    for i, seg in enumerate(segments):
        duration = seg["end"] - seg["start"]
        edge_fade = min(fade, max(0.0, duration / 4.0))
        out_fade_start = max(0.0, duration - edge_fade)

        filter_parts.append(
            f"[0:v]trim=start={seg['start']:.6f}:end={seg['end']:.6f},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
        audio_filter = (
            f"[0:a]atrim=start={seg['start']:.6f}:end={seg['end']:.6f},"
            "asetpts=PTS-STARTPTS"
        )
        if edge_fade > 0:
            audio_filter += (
                f",afade=t=in:st=0:d={edge_fade:.6f},"
                f"afade=t=out:st={out_fade_start:.6f}:d={edge_fade:.6f}"
            )
        audio_filter += f"[a{i}]"
        filter_parts.append(audio_filter)
        concat_inputs.append(f"[v{i}][a{i}]")

    filter_parts.append(
        "".join(concat_inputs) + f"concat=n={len(segments)}:v=1:a=1[vout][ajoined]"
    )
    filter_parts.append(
        f"[ajoined]loudnorm=I={args.target_lufs}:TP={args.true_peak}:"
        f"LRA={args.loudness_range}[aout]"
    )
    filter_complex = ";".join(filter_parts)

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
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            args.crf,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "1024",
            str(output),
        ]
    )

    kept_duration = sum(seg["end"] - seg["start"] for seg in segments)
    print(f"Rendered {len(segments)} segments, {kept_duration / 60:.2f} minutes: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
