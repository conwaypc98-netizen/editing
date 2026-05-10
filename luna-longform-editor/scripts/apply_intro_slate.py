#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
from pathlib import Path


BODY_MARKERS = [
    r"\balright guys\b",
    r"\ball right guys\b",
    r"\bbefore you (run|apply)\b",
    r"\bonce (it'?s|its) downloaded\b",
    r"\byou want to make sure\b",
    r"\blet'?s get into\b",
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def probe_video(path: Path) -> tuple[int, int, float]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    fps_text = stream.get("r_frame_rate", "30/1")
    if "/" in fps_text:
        num, den = fps_text.split("/", 1)
        fps = float(num) / float(den)
    else:
        fps = float(fps_text)
    return int(stream["width"]), int(stream["height"]), fps or 30.0


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def load_segments(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    if not segments:
        raise SystemExit(f"No transcript segments found: {path}")
    return segments


def detect_intro_end(transcript_json: Path, min_intro: float, fallback: float) -> float:
    segments = load_segments(transcript_json)
    compiled = [re.compile(pattern) for pattern in BODY_MARKERS]

    for segment in segments:
        start = float(segment["start"])
        text = clean_text(str(segment.get("text", "")))
        if start < min_intro:
            continue
        if any(pattern.search(text) for pattern in compiled):
            return start

    for segment in segments:
        end = float(segment["end"])
        text = clean_text(str(segment.get("text", "")))
        if end < min_intro:
            continue
        if "download" in text and "description" in text:
            return end

    return fallback


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replace the opening spoken intro visuals with a static brand image while "
            "preserving the edited intro audio, then continue with the normal video."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--intro-image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--transcript-json")
    parser.add_argument("--intro-end", type=float)
    parser.add_argument("--min-intro", type=float, default=12.0)
    parser.add_argument("--fallback-intro-end", type=float, default=40.0)
    parser.add_argument("--intro-audio-output")
    parser.add_argument("--crf", default="18")
    parser.add_argument("--audio-bitrate", default="192k")
    args = parser.parse_args()

    source = Path(args.input).expanduser().resolve()
    image = Path(args.intro_image).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Input not found: {source}")
    if not image.exists():
        raise SystemExit(f"Intro image not found: {image}")

    if args.intro_end is not None:
        intro_end = float(args.intro_end)
    else:
        if not args.transcript_json:
            raise SystemExit("Provide --transcript-json or --intro-end.")
        intro_end = detect_intro_end(
            Path(args.transcript_json).expanduser().resolve(),
            args.min_intro,
            args.fallback_intro_end,
        )

    if intro_end <= 0.25:
        raise SystemExit(f"Detected intro is too short: {intro_end:.3f}s")

    width, height, fps = probe_video(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.intro_audio_output:
        intro_audio = Path(args.intro_audio_output).expanduser().resolve()
        intro_audio.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ss",
                "0",
                "-to",
                f"{intro_end:.6f}",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(intro_audio),
            ]
        )
        print(f"Extracted intro audio ({intro_end:.3f}s): {intro_audio}")

    filter_complex = (
        f"[1:v]scale=w={width}:h={height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps:.6f},"
        f"trim=duration={intro_end:.6f},setpts=PTS-STARTPTS,format=yuv420p[iv];"
        f"[0:v]trim=start={intro_end:.6f},setpts=PTS-STARTPTS,"
        f"fps={fps:.6f},format=yuv420p[bv];"
        f"[0:a]atrim=start=0:end={intro_end:.6f},asetpts=PTS-STARTPTS[ia];"
        f"[0:a]atrim=start={intro_end:.6f},asetpts=PTS-STARTPTS[ba];"
        "[iv][ia][bv][ba]concat=n=2:v=1:a=1[vout][aout]"
    )

    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(source),
            "-loop",
            "1",
            "-framerate",
            f"{fps:.6f}",
            "-i",
            str(image),
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
            args.audio_bitrate,
            "-movflags",
            "+faststart",
            str(output),
        ]
    )

    print(f"Applied intro slate through {intro_end:.3f}s: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
