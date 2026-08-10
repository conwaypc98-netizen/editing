#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


SHORT_WORDS = {
    "a",
    "an",
    "and",
    "but",
    "if",
    "it",
    "or",
    "so",
    "the",
    "to",
    "uh",
    "um",
    "you",
}


def normalize_word(word: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", word.lower())


def load_words(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    words = []
    for segment in data.get("segments", []):
        for word in segment.get("words", []):
            if "start" not in word or "end" not in word:
                continue
            token = str(word.get("word", "")).strip()
            words.append(
                {
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "word": token,
                    "norm": normalize_word(token),
                }
            )
    if not words:
        raise SystemExit(f"No word timings found in transcript: {path}")
    return words


def parse_keep_document(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    keep = data.get("keep", data if isinstance(data, list) else [])
    if not isinstance(keep, list) or not keep:
        raise SystemExit(f"No keep segments found: {path}")
    document = dict(data) if isinstance(data, dict) else {"keep": keep}
    return document, keep


def segment_words(words: list[dict], start: float, end: float, tolerance: float) -> list[dict]:
    contained = [
        w
        for w in words
        if w["start"] >= start - tolerance and w["end"] <= end + tolerance
    ]
    if contained:
        return contained
    return [w for w in words if w["end"] > start and w["start"] < end]


def is_short_pause_token(word: dict) -> bool:
    token = word["norm"]
    if any(char.isdigit() for char in token):
        return False
    return token in SHORT_WORDS or (0 < len(token) <= 3)


def add_piece(out: list[dict], source: dict, start: float, end: float, suffix: str) -> None:
    if end <= start:
        return
    piece = dict(source)
    label = str(source.get("label", "segment"))
    piece["start"] = round(start, 3)
    piece["end"] = round(end, 3)
    piece["label"] = f"{label} / {suffix}"
    out.append(piece)


def split_segment(segment: dict, words: list[dict], args: argparse.Namespace) -> list[dict]:
    start = float(segment["start"])
    end = float(segment["end"])
    label = str(segment.get("label", "segment"))
    lock = bool(segment.get("lock", False)) or "locked" in label.lower()
    if lock or len(words) < 2:
        return [segment]

    pieces = []
    current_start = start

    for index, (prev, nxt) in enumerate(zip(words, words[1:]), start=1):
        split_reason = ""
        cut_end = None
        next_start = None

        gap = nxt["start"] - prev["end"]
        if gap > args.max_gap:
            split_reason = f"gap {gap:.2f}s"
            cut_end = prev["end"] + args.tail
            next_start = nxt["start"] - args.lead

        prev_duration = prev["end"] - prev["start"]
        hidden_pause = (
            is_short_pause_token(prev)
            and prev_duration > args.max_short_word
            and nxt["start"] - prev["end"] <= args.hidden_gap_tolerance
        )
        if hidden_pause:
            split_reason = f"held {prev['word']} {prev_duration:.2f}s"
            cut_end = prev["start"] + args.short_word_keep + args.tail
            next_start = nxt["start"] - args.lead

        if cut_end is None or next_start is None:
            continue

        cut_end = min(max(cut_end, current_start), end)
        next_start = min(max(next_start, start), end)
        removed = next_start - cut_end
        before_len = cut_end - current_start
        after_len = end - next_start

        if (
            removed >= args.min_removed
            and before_len >= args.min_piece
            and after_len >= args.min_piece
        ):
            add_piece(pieces, segment, current_start, cut_end, f"pace {index}: {split_reason}")
            current_start = next_start
            print(
                f"tightened '{label}' at {cut_end:.3f}->{next_start:.3f} "
                f"removed {removed:.2f}s ({split_reason})",
                file=sys.stderr,
            )

    if current_start < end:
        add_piece(pieces, segment, current_start, end, "pace tail")
    return pieces or [segment]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Split kept speech segments around unnatural pauses/stretched short words. "
            "Run this after semantic keep-list selection and before final audio snapping."
        )
    )
    parser.add_argument("--keep-list", required=True)
    parser.add_argument("--transcript-json", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-gap", type=float, default=0.42)
    parser.add_argument("--max-short-word", type=float, default=0.52)
    parser.add_argument("--short-word-keep", type=float, default=0.24)
    parser.add_argument("--hidden-gap-tolerance", type=float, default=0.06)
    parser.add_argument("--lead", type=float, default=0.050)
    parser.add_argument("--tail", type=float, default=0.070)
    parser.add_argument("--min-removed", type=float, default=0.130)
    parser.add_argument("--min-piece", type=float, default=0.220)
    parser.add_argument("--word-tolerance", type=float, default=0.035)
    args = parser.parse_args()

    document, keep = parse_keep_document(Path(args.keep_list))
    words = load_words(Path(args.transcript_json))

    tightened = []
    previous_end = -1.0
    for index, segment in enumerate(keep, start=1):
        start = float(segment["start"])
        end = float(segment["end"])
        local_words = segment_words(words, start, end, args.word_tolerance)
        for piece in split_segment(segment, local_words, args):
            piece_start = float(piece["start"])
            piece_end = float(piece["end"])
            if piece_start < previous_end:
                piece_start = previous_end + 0.001
                piece["start"] = round(piece_start, 3)
            if piece_end <= piece_start:
                continue
            tightened.append(piece)
            previous_end = piece_end

    document["keep"] = tightened
    Path(args.output).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote pacing-tightened keep list with {len(tightened)} segments: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
