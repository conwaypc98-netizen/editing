#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
from pathlib import Path


BODY_MARKERS = [
    r"\balright guys\b",
    r"\ball right guys\b",
    r"\bto start (the )?tutorial\b",
    r"\bon your desktop\b",
    r"\bfirst thing (you|we)\b",
    r"\bgo ahead and (open|click|press)\b",
    r"\bbefore you (run|apply)\b",
    r"\bonce (it'?s|its) downloaded\b",
    r"\byou want to make sure\b",
    r"\blet'?s get into\b",
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def probe_video(path: Path) -> tuple[int, int, float, float]:
    result = subprocess.run(
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
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    fps_text = stream.get("r_frame_rate", "30/1")
    if "/" in fps_text:
        num, den = fps_text.split("/", 1)
        fps = float(num) / float(den)
    else:
        fps = float(fps_text)
    duration = float(payload.get("format", {}).get("duration", 0.0))
    return int(stream["width"]), int(stream["height"]), fps or 30.0, duration


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def load_segments(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    if not segments:
        raise SystemExit(f"No transcript segments found: {path}")
    return segments


def detect_intro_end(transcript_json: Path, min_intro: float) -> tuple[float | None, str | None]:
    segments = load_segments(transcript_json)
    compiled = [re.compile(pattern) for pattern in BODY_MARKERS]

    for segment in segments:
        start = float(segment["start"])
        text = clean_text(str(segment.get("text", "")))
        if start < min_intro:
            continue
        if any(pattern.search(text) for pattern in compiled):
            return start, f"body marker in transcript: {text}"

    for segment in segments:
        end = float(segment["end"])
        text = clean_text(str(segment.get("text", "")))
        if end < min_intro:
            continue
        if "download" in text and "description" in text:
            return end, f"intro download/setup marker in transcript: {text}"

    return None, None


def intro_end_from_plan(path: Path) -> float | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    keep = data.get("keep", data.get("segments", []))
    if not isinstance(keep, list) or not keep:
        raise SystemExit(f"Edit plan has no kept ranges: {path}")
    duration = 0.0
    reached_screen = False
    found_slate = False
    for index, item in enumerate(keep, start=1):
        slate = item.get("intro_slate") is True or str(item.get("intro_visual", "")).lower() == "slate"
        if slate and reached_screen:
            raise SystemExit(f"Intro slate ranges must be consecutive at the start; keep[{index}] is late.")
        if not slate:
            reached_screen = True
            continue
        found_slate = True
        duration += float(item["end"]) - float(item["start"])
    return duration if found_slate else None


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
    parser.add_argument("--edit-plan")
    parser.add_argument("--intro-end", type=float)
    parser.add_argument("--min-intro", type=float, default=2.0)
    parser.add_argument("--fallback-intro-end", type=float)
    parser.add_argument("--intro-audio-output")
    parser.add_argument("--report")
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

    method = None
    evidence = None
    if args.intro_end is not None:
        intro_end = float(args.intro_end)
        method = "explicit"
        evidence = "Explicit --intro-end supplied after review."
    else:
        intro_end = None
        if args.edit_plan:
            plan_path = Path(args.edit_plan).expanduser().resolve()
            intro_end = intro_end_from_plan(plan_path)
            if intro_end is not None:
                method = "reasoned_edit_plan"
                evidence = f"Consecutive intro_slate ranges in {plan_path}"
        if intro_end is None and args.transcript_json:
            transcript_path = Path(args.transcript_json).expanduser().resolve()
            intro_end, evidence = detect_intro_end(transcript_path, args.min_intro)
            if intro_end is not None:
                method = "transcript_body_marker"
        if intro_end is None and args.fallback_intro_end is not None:
            intro_end = float(args.fallback_intro_end)
            method = "explicit_low_confidence_fallback"
            evidence = "Explicit fallback supplied; visual confirmation is still required."
        if intro_end is None:
            raise SystemExit(
                "Intro end is unknown. Mark initial plan ranges with intro_slate=true, "
                "provide a rendered transcript with a body marker, or pass an explicitly reviewed --intro-end."
            )

    if intro_end <= 0.25:
        raise SystemExit(f"Detected intro is too short: {intro_end:.3f}s")

    width, height, fps, duration = probe_video(source)
    if duration and intro_end >= duration - 0.10:
        raise SystemExit(f"Intro end {intro_end:.3f}s leaves no tutorial body in a {duration:.3f}s video.")
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
    if args.report:
        report = Path(args.report).expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "input": str(source),
                    "output": str(output),
                    "intro_end_seconds": round(intro_end, 3),
                    "method": method,
                    "evidence": evidence,
                    "requires_visual_confirmation": method == "explicit_low_confidence_fallback",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Intro report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
