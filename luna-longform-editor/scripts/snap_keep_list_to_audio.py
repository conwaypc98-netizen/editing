#!/usr/bin/env python3
import argparse
import array
import json
import math
import sys
import wave
from pathlib import Path


def load_words(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    words = []
    for segment in data.get("segments", []):
        for word in segment.get("words", []):
            if "start" in word and "end" in word:
                words.append(
                    {
                        "start": float(word["start"]),
                        "end": float(word["end"]),
                        "word": str(word.get("word", "")).strip(),
                    }
                )
    if not words:
        raise SystemExit(f"No word timings found in transcript: {path}")
    return words


def load_mono_wav(path: Path) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frame_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width != 2:
        raise SystemExit("Expected 16-bit PCM WAV audio.")

    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()

    if channels > 1:
        samples = array.array("h", samples[::channels])
    return samples, frame_rate


def rms(samples: array.array, frame_rate: int, center: float, window: float) -> float:
    start = max(0, int((center - window / 2) * frame_rate))
    end = min(len(samples), int((center + window / 2) * frame_rate))
    if end <= start:
        return 0.0
    total = 0
    for value in samples[start:end]:
        total += value * value
    return math.sqrt(total / (end - start))


def quietest_time(
    samples: array.array,
    frame_rate: int,
    start: float,
    end: float,
    target: float,
    step: float,
    window: float,
) -> float:
    start = max(0.0, start)
    end = max(start, end)
    if end <= start:
        return max(0.0, target)

    best_t = start
    best_score = float("inf")
    t = start
    while t <= end:
        # Add a tiny distance cost so equally quiet spots stay near the intended cut.
        score = rms(samples, frame_rate, t, window) + abs(t - target) * 12
        if score < best_score:
            best_score = score
            best_t = t
        t += step
    return best_t


def parse_keep_list(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    keep = data.get("keep", data if isinstance(data, list) else [])
    if not keep:
        raise SystemExit(f"No keep segments found: {path}")
    return keep


def segment_words(words: list[dict], start: float, end: float, tolerance: float) -> list[dict]:
    # For final cut snapping, prefer words whose own boundaries live inside the
    # chosen semantic segment. Overlap-only matching can accidentally pull in the
    # tail of a false start that ends exactly where the good take begins.
    contained = [
        w
        for w in words
        if w["start"] >= start - tolerance and w["end"] <= end + tolerance
    ]
    if contained:
        return contained
    return [w for w in words if w["end"] > start and w["start"] < end]


def warn_long_pauses(words: list[dict], max_pause: float, label: str) -> None:
    for prev, nxt in zip(words, words[1:]):
        gap = nxt["start"] - prev["end"]
        if gap > max_pause:
            print(
                f"pause warning {gap:.2f}s in '{label}' between "
                f"{prev['end']:.3f} and {nxt['start']:.3f}",
                file=sys.stderr,
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-list", required=True)
    parser.add_argument("--transcript-json", required=True)
    parser.add_argument("--audio-wav", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pre-roll", type=float, default=0.055)
    parser.add_argument("--post-roll", type=float, default=0.105)
    parser.add_argument("--search-before", type=float, default=0.10)
    parser.add_argument("--search-after", type=float, default=0.12)
    parser.add_argument("--max-pause", type=float, default=0.72)
    parser.add_argument("--step", type=float, default=0.005)
    parser.add_argument("--rms-window", type=float, default=0.012)
    args = parser.parse_args()

    keep = parse_keep_list(Path(args.keep_list))
    words = load_words(Path(args.transcript_json))
    samples, frame_rate = load_mono_wav(Path(args.audio_wav))

    refined = []
    previous_end = -1.0
    for index, segment in enumerate(keep, start=1):
        original_start = float(segment["start"])
        original_end = float(segment["end"])
        label = str(segment.get("label", f"segment-{index}"))
        lock = bool(segment.get("lock", False)) or "locked" in label.lower()
        local_words = segment_words(words, original_start, original_end, 0.025)

        if lock or not local_words:
            start = original_start
            end = original_end
        else:
            first = local_words[0]
            last = local_words[-1]
            intended_start = max(0.0, first["start"] - args.pre_roll)
            intended_end = last["end"] + args.post_roll

            start = quietest_time(
                samples,
                frame_rate,
                intended_start - args.search_before,
                intended_start + args.search_after / 2,
                intended_start,
                args.step,
                args.rms_window,
            )
            end = quietest_time(
                samples,
                frame_rate,
                intended_end - args.search_before / 2,
                intended_end + args.search_after,
                intended_end,
                args.step,
                args.rms_window,
            )
            warn_long_pauses(local_words, args.max_pause, label)

        if start < previous_end:
            start = previous_end + 0.001
        if end <= start:
            end = start + 0.050

        updated = dict(segment)
        updated["start"] = round(start, 3)
        updated["end"] = round(end, 3)
        refined.append(updated)
        previous_end = end

    Path(args.output).write_text(json.dumps({"keep": refined}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote refined keep list: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
