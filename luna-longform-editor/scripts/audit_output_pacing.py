#!/usr/bin/env python3
import argparse
import json
import re
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
            norm = normalize_word(token)
            if not norm:
                continue
            words.append(
                {
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "word": token,
                    "norm": norm,
                }
            )
    if not words:
        raise SystemExit(f"No word timings found in transcript: {path}")
    return words


def is_short_pause_token(word: dict) -> bool:
    token = word["norm"]
    if any(char.isdigit() for char in token):
        return False
    return token in SHORT_WORDS or len(token) <= 3


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit a rendered edit transcript for obvious stutters and unnatural pacing."
    )
    parser.add_argument("--transcript-json", required=True)
    parser.add_argument("--max-gap", type=float, default=0.55)
    parser.add_argument("--max-short-word", type=float, default=0.58)
    parser.add_argument("--near-repeat-window", type=float, default=1.20)
    parser.add_argument("--fail-on-warnings", action="store_true")
    args = parser.parse_args()

    words = load_words(Path(args.transcript_json))
    warnings = []

    for prev, nxt in zip(words, words[1:]):
        gap = nxt["start"] - prev["end"]
        if gap > args.max_gap:
            warnings.append(
                f"long gap {gap:.2f}s at {prev['end']:.3f}->{nxt['start']:.3f}: "
                f"{prev['word']} | {nxt['word']}"
            )

        if (
            prev["norm"] == nxt["norm"]
            and nxt["start"] - prev["start"] <= args.near_repeat_window
        ):
            warnings.append(
                f"adjacent repeat at {prev['start']:.3f}: {prev['word']} {nxt['word']}"
            )

    for word in words:
        duration = word["end"] - word["start"]
        if is_short_pause_token(word) and duration > args.max_short_word:
            warnings.append(
                f"held short word {duration:.2f}s at {word['start']:.3f}: {word['word']}"
            )

    tokens = [w["norm"] for w in words]
    for ngram in range(2, 6):
        for i in range(0, len(tokens) - (ngram * 2) + 1):
            if tokens[i : i + ngram] == tokens[i + ngram : i + (ngram * 2)]:
                start = words[i]["start"]
                warnings.append(
                    f"repeated {ngram}-word phrase at {start:.3f}: "
                    + " ".join(w["word"] for w in words[i : i + ngram])
                )

    if warnings:
        print("Pacing/stutter warnings:")
        for warning in warnings:
            print(f"- {warning}")
        return 1 if args.fail_on_warnings else 0

    print("No obvious pacing/stutter warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
