#!/usr/bin/env python3
import argparse
import difflib
import html
import json
import math
import re
import shutil
import subprocess
from pathlib import Path


SHORT_WORDS = {"a", "an", "and", "but", "if", "it", "or", "so", "the", "to", "uh", "um", "you"}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def normalize_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def probe(source: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
            "-show_entries",
            "format=duration,size,bit_rate",
            "-of",
            "json",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def load_transcript(path: Path | None) -> tuple[list[dict], list[dict]]:
    if path is None:
        return [], []
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = []
    words = []
    for raw in data.get("segments", []):
        segment = {
            "id": int(raw.get("id", len(segments))),
            "start": float(raw["start"]),
            "end": float(raw["end"]),
            "text": str(raw.get("text", "")).strip(),
        }
        segments.append(segment)
        for word in raw.get("words", []):
            if word.get("start") is None or word.get("end") is None:
                continue
            token = str(word.get("word", "")).strip()
            words.append(
                {
                    "segment_id": segment["id"],
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "word": token,
                    "norm": "".join(normalize_tokens(token)),
                    "probability": word.get("probability"),
                }
            )
    return segments, words


def find_duplicate_candidates(segments: list[dict], window: float) -> list[dict]:
    candidates = []
    for left_index, left in enumerate(segments):
        left_tokens = normalize_tokens(left["text"])
        if len(left_tokens) < 4:
            continue
        for right in segments[left_index + 1 :]:
            if right["start"] - left["end"] > window:
                break
            right_tokens = normalize_tokens(right["text"])
            if len(right_tokens) < 4:
                continue
            ratio = difflib.SequenceMatcher(None, left_tokens, right_tokens).ratio()
            shared = len(set(left_tokens) & set(right_tokens))
            union = max(1, len(set(left_tokens) | set(right_tokens)))
            jaccard = shared / union
            shorter = min(len(left_tokens), len(right_tokens))
            containment = shared / max(1, shorter)
            if ratio < 0.68 and not (jaccard >= 0.55 and containment >= 0.72):
                continue
            candidates.append(
                {
                    "group_id": f"duplicate-{len(candidates) + 1:03d}",
                    "left": left,
                    "right": right,
                    "sequence_similarity": round(ratio, 3),
                    "token_jaccard": round(jaccard, 3),
                    "instruction": "Choose the more fluent complete take unless both add distinct value.",
                }
            )
            if len(candidates) >= 250:
                return candidates
    return candidates


def find_speech_issues(words: list[dict]) -> dict:
    repeats = []
    pauses = []
    held_words = []
    low_confidence = []
    for prev, nxt in zip(words, words[1:]):
        gap = nxt["start"] - prev["end"]
        if gap > 0.55:
            pauses.append(
                {
                    "start": round(prev["end"], 3),
                    "end": round(nxt["start"], 3),
                    "duration": round(gap, 3),
                    "before": prev["word"],
                    "after": nxt["word"],
                }
            )
        if prev["norm"] and prev["norm"] == nxt["norm"] and nxt["start"] - prev["start"] < 1.5:
            repeats.append(
                {
                    "start": round(prev["start"], 3),
                    "end": round(nxt["end"], 3),
                    "text": f"{prev['word']} {nxt['word']}",
                    "kind": "adjacent_word_repeat",
                }
            )
    for word in words:
        duration = word["end"] - word["start"]
        if word["norm"] in SHORT_WORDS and duration > 0.58:
            held_words.append(
                {
                    "start": round(word["start"], 3),
                    "end": round(word["end"], 3),
                    "duration": round(duration, 3),
                    "word": word["word"],
                }
            )
        probability = word.get("probability")
        if probability is not None and float(probability) < 0.45:
            low_confidence.append(
                {
                    "start": round(word["start"], 3),
                    "end": round(word["end"], 3),
                    "word": word["word"],
                    "probability": round(float(probability), 3),
                }
            )

    tokens = [word["norm"] for word in words]
    for length in range(2, 6):
        index = 0
        while index + length * 2 <= len(tokens):
            if tokens[index : index + length] == tokens[index + length : index + length * 2]:
                repeats.append(
                    {
                        "start": round(words[index]["start"], 3),
                        "end": round(words[index + length * 2 - 1]["end"], 3),
                        "text": " ".join(word["word"] for word in words[index : index + length]),
                        "kind": f"repeated_{length}_word_phrase",
                    }
                )
                index += length * 2
            else:
                index += 1
    return {
        "repeats": repeats,
        "pauses": pauses,
        "held_short_words": held_words,
        "low_confidence_words": low_confidence,
    }


def extract_frames(source: Path, frame_dir: Path, interval: float, max_frames: int) -> list[dict]:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required.")
    frame_dir.mkdir(parents=True, exist_ok=True)
    for old in frame_dir.glob("frame_*.jpg"):
        old.unlink()
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"fps=1/{interval:.6f},scale=640:-2",
            "-frames:v",
            str(max_frames),
            "-q:v",
            "3",
            str(frame_dir / "frame_%05d.jpg"),
        ],
        check=True,
    )
    frames = []
    for index, path in enumerate(sorted(frame_dir.glob("frame_*.jpg"))):
        frames.append(
            {
                "index": index,
                "time": round(index * interval, 3),
                "path": str(path.resolve()),
            }
        )
    return frames


def nearest_frame(frames: list[dict], time_value: float) -> dict | None:
    if not frames:
        return None
    return min(frames, key=lambda frame: abs(frame["time"] - time_value))


def attach_visual_evidence(segments: list[dict], frames: list[dict]) -> list[dict]:
    enriched = []
    for segment in segments:
        item = dict(segment)
        frame = nearest_frame(frames, (segment["start"] + segment["end"]) / 2)
        item["nearest_frame"] = frame
        enriched.append(item)
    return enriched


def write_markdown(path: Path, source: Path, dossier: dict) -> None:
    duplicates = dossier["language_evidence"]["duplicate_candidates"]
    issues = dossier["language_evidence"]["speech_issues"]
    lines = [
        "# Editorial Evidence Review",
        "",
        f"Source: `{source}`",
        "",
        "This file is an evidence index, not an automatic edit decision. Inspect the transcript and frames, then write a reasoned edit plan.",
        "",
        "## Required Review",
        "",
        "- Build a complete first-pass timeline from the transcript and overview evidence before opening individual frames.",
        "- Do not inspect every sampled frame sequentially; open detailed frames only around actual decisions or uncertainty.",
        "- Resolve every plausible duplicate take.",
        "- Inspect every candidate stutter and long pause in context.",
        "- Confirm the visual state before and after each proposed cut.",
        "- Assign a story role and viewer purpose to every kept range.",
        "- Record uncertainty instead of guessing.",
        "",
        f"## Duplicate Candidates ({len(duplicates)})",
        "",
    ]
    for candidate in duplicates[:80]:
        left = candidate["left"]
        right = candidate["right"]
        lines.append(
            f"- `{candidate['group_id']}` {left['start']:.2f}-{left['end']:.2f} "
            f"vs {right['start']:.2f}-{right['end']:.2f}: "
            f"\"{left['text']}\" / \"{right['text']}\""
        )
    lines.extend(
        [
            "",
            f"## Repeats And Stutters ({len(issues['repeats'])})",
            "",
        ]
    )
    for issue in issues["repeats"][:100]:
        lines.append(f"- {issue['start']:.2f}-{issue['end']:.2f}: {issue['kind']} `{issue['text']}`")
    lines.extend(["", f"## Long Speech Gaps ({len(issues['pauses'])})", ""])
    for issue in issues["pauses"][:120]:
        lines.append(
            f"- {issue['start']:.2f}-{issue['end']:.2f} ({issue['duration']:.2f}s): "
            f"`{issue['before']}` -> `{issue['after']}`"
        )
    lines.extend(["", "## Visual Index", "", "Open `review.html` or inspect images in `frames/`.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_html(path: Path, source: Path, segments: list[dict], frames: list[dict]) -> None:
    segment_rows = []
    for segment in segments:
        frame = segment.get("nearest_frame")
        image = ""
        if frame:
            relative = Path(frame["path"]).relative_to(path.parent.resolve())
            image = f'<img src="{html.escape(relative.as_posix())}" loading="lazy">'
        segment_rows.append(
            "<article>"
            f"{image}<div><strong>{segment['start']:.2f}-{segment['end']:.2f}</strong> "
            f"{html.escape(segment['text'])}</div></article>"
        )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Luna editorial review</title><style>
body{{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:24px}}
h1{{font-size:24px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}}
article{{background:#1c1c1c;border:1px solid #333;padding:8px}}img{{width:100%;display:block;margin-bottom:8px}}
strong{{color:#62e6cf}}p{{color:#bbb}}</style></head><body>
<h1>Editorial review</h1><p>{html.escape(str(source))}</p><div class="grid">{''.join(segment_rows)}</div>
</body></html>"""
    path.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build transcript and visual evidence for an intelligent edit.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--transcript-json")
    parser.add_argument("--frame-interval", type=float, default=4.0)
    parser.add_argument("--max-frames", type=int, default=240)
    parser.add_argument("--duplicate-window", type=float, default=120.0)
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    output = Path(args.output_dir).expanduser().resolve()
    transcript = Path(args.transcript_json).expanduser().resolve() if args.transcript_json else None
    if not source.is_file():
        raise SystemExit(f"Source not found: {source}")
    if transcript is not None and not transcript.is_file():
        raise SystemExit(f"Transcript not found: {transcript}")
    if args.frame_interval <= 0:
        raise SystemExit("--frame-interval must be positive.")
    output.mkdir(parents=True, exist_ok=True)

    technical = probe(source)
    duration = float(technical.get("format", {}).get("duration", 0.0))
    max_by_duration = max(1, int(math.ceil(duration / args.frame_interval)))
    frames = extract_frames(
        source,
        output / "frames",
        args.frame_interval,
        min(args.max_frames, max_by_duration),
    )
    segments, words = load_transcript(transcript)
    enriched_segments = attach_visual_evidence(segments, frames)
    dossier = {
        "schema_version": 2,
        "source": str(source),
        "technical": technical,
        "frame_interval": args.frame_interval,
        "frames": frames,
        "transcript_segments": enriched_segments,
        "language_evidence": {
            "duplicate_candidates": find_duplicate_candidates(segments, args.duplicate_window),
            "speech_issues": find_speech_issues(words),
        },
        "decision_contract": {
            "automatic_decisions_allowed": False,
            "required_keep_fields": [
                "story_role",
                "rationale",
                "viewer_purpose",
                "take_choice",
                "continuity",
                "evidence",
            ],
        },
    }
    write_json(output / "dossier.json", dossier)
    write_markdown(output / "EDITORIAL_REVIEW.md", source, dossier)
    write_html(output / "review.html", source, enriched_segments, frames)
    print(output / "dossier.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
