#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def fmt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    ms = millis % 1000
    total_seconds = millis // 1000
    s = total_seconds % 60
    total_minutes = total_seconds // 60
    m = total_minutes % 60
    h = total_minutes // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default="small.en")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        args.audio,
        language=args.language,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )

    payload = {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "segments": [],
    }

    lines = []
    word_lines = []
    for segment in segments:
        words = []
        if segment.words:
            for word in segment.words:
                words.append(
                    {
                        "start": word.start,
                        "end": word.end,
                        "word": word.word,
                        "probability": word.probability,
                    }
                )
                word_lines.append(
                    f"{fmt_time(word.start)} --> {fmt_time(word.end)}  {word.word.strip()}"
                )

        item = {
            "id": segment.id,
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
            "words": words,
        }
        payload["segments"].append(item)
        lines.append(f"[{fmt_time(segment.start)} - {fmt_time(segment.end)}] {item['text']}")

    (out_dir / "transcript.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "words.txt").write_text("\n".join(word_lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
