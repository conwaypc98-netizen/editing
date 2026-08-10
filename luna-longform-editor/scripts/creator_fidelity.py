#!/usr/bin/env python3
"""Language and structure measurements for a reusable Luna creator profile."""

import collections
import re
import statistics
from pathlib import Path


WORD_PATTERN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.I)

ACTION_WORDS = {
    "apply",
    "check",
    "choose",
    "click",
    "close",
    "connect",
    "disable",
    "download",
    "drag",
    "enable",
    "enter",
    "exit",
    "find",
    "go",
    "hit",
    "install",
    "open",
    "press",
    "restart",
    "right",
    "run",
    "scroll",
    "select",
    "set",
    "start",
    "type",
    "uncheck",
    "verify",
}

TRANSITION_WORDS = {
    "after",
    "also",
    "before",
    "first",
    "finally",
    "next",
    "now",
    "once",
    "so",
    "then",
}

FILLER_WORDS = {
    "basically",
    "honestly",
    "kinda",
    "kindof",
    "like",
    "literally",
    "sorta",
    "sortof",
    "uh",
    "um",
}

VIEWER_WORDS = {"you", "you'll", "you're", "you've", "your", "yours"}
FIRST_PERSON_WORDS = {"i", "i'll", "i'm", "i've", "me", "my", "we", "we'll", "we're"}

SIGNATURE_PHRASES = (
    "all right guys",
    "today i'm showing you",
    "let's get into it",
    "you want to",
    "make sure",
    "just so you guys know",
    "once you've done that",
    "if this video helped",
    "link in the description",
    "thank you guys for watching",
    "hope you have a great day",
)

TUTORIAL_TRANSITION_PATTERN = re.compile(
    r"\b(all right|alright) guys\b|\bto start (?:the )?tutorial\b",
    re.I,
)
CTA_STRONG_PATTERN = re.compile(
    r"\bif this video helped\b|\bthank you guys for watching\b|\bhope you have a great day\b",
    re.I,
)
CTA_PROMOTION_PATTERN = re.compile(
    r"\bi (?:highly )?recommend\b|\blink in (?:the )?description\b|\buse code\b",
    re.I,
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def tokens(value: str) -> list[str]:
    return [match.group(0).lower() for match in WORD_PATTERN.finditer(value)]


def normalized_text(value: str) -> str:
    return " ".join(tokens(value))


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def adjacent_repeated_phrases(words: list[str], maximum_size: int = 5) -> list[str]:
    repeats = set()
    for size in range(1, maximum_size + 1):
        for index in range(len(words) - size * 2 + 1):
            phrase = words[index : index + size]
            if phrase and phrase == words[index + size : index + size * 2]:
                repeats.add(" ".join(phrase))
    return sorted(repeats)


def content_tokens(value: str) -> set[str]:
    return {
        token
        for token in tokens(value)
        if token not in STOPWORDS and len(token) > 1
    }


def lexical_overlap(first: str, second: str) -> float:
    left = content_tokens(first)
    right = content_tokens(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def text_style_metrics(
    text: str,
    units: list[str] | None = None,
    duration: float | None = None,
) -> dict:
    words = tokens(text)
    count = len(words)
    denominator = count / 100.0 if count else 1.0
    unit_words = [len(tokens(unit)) for unit in (units or []) if tokens(unit)]
    normalized_units = [normalized_text(unit) for unit in (units or []) if normalized_text(unit)]
    unit_counts = collections.Counter(normalized_units)
    duplicate_units = sorted(unit for unit, amount in unit_counts.items() if amount > 1)
    phrase_counts = {
        phrase: len(re.findall(rf"\b{re.escape(phrase)}\b", normalized_text(text)))
        for phrase in SIGNATURE_PHRASES
    }
    return {
        "word_count": count,
        "words_per_minute": round_or_none(count / (duration / 60.0), 2) if duration else None,
        "unique_word_ratio": round_or_none(len(set(words)) / count if count else 0.0),
        "average_unit_words": round_or_none(statistics.mean(unit_words), 3) if unit_words else None,
        "median_unit_words": round_or_none(statistics.median(unit_words), 3) if unit_words else None,
        "viewer_address_per_100_words": round_or_none(
            sum(word in VIEWER_WORDS for word in words) / denominator,
            3,
        ),
        "first_person_per_100_words": round_or_none(
            sum(word in FIRST_PERSON_WORDS for word in words) / denominator,
            3,
        ),
        "action_words_per_100_words": round_or_none(
            sum(word in ACTION_WORDS for word in words) / denominator,
            3,
        ),
        "transition_words_per_100_words": round_or_none(
            sum(word in TRANSITION_WORDS for word in words) / denominator,
            3,
        ),
        "filler_words_per_100_words": round_or_none(
            sum(word in FILLER_WORDS for word in words) / denominator,
            3,
        ),
        "contractions_per_100_words": round_or_none(
            sum("'" in word for word in words) / denominator,
            3,
        ),
        "action_unit_fraction": round_or_none(
            sum(any(word in ACTION_WORDS for word in tokens(unit)) for unit in (units or []))
            / len(units or [1]),
            4,
        ),
        "signature_phrase_counts": phrase_counts,
        "adjacent_repeated_phrases": adjacent_repeated_phrases(words),
        "duplicate_units": duplicate_units,
        "opening_excerpt": " ".join(words[:40]),
        "closing_excerpt": " ".join(words[-60:]),
    }


def transcript_style(payload: dict) -> dict:
    segments = payload.get("segments", [])
    units = [str(segment.get("text", "")).strip() for segment in segments]
    text = " ".join(unit for unit in units if unit)
    duration = float(payload.get("duration") or 0.0)
    if not duration and segments:
        duration = max(float(segment.get("end", 0.0)) for segment in segments)
    metrics = text_style_metrics(text, units, duration or None)

    tutorial_index = next(
        (
            index
            for index, segment in enumerate(segments)
            if float(segment.get("start", 0.0)) >= 2.0
            and TUTORIAL_TRANSITION_PATTERN.search(str(segment.get("text", "")))
        ),
        None,
    )
    cta_index = next(
        (
            index
            for index, segment in enumerate(segments)
            if float(segment.get("start", 0.0)) >= duration * 0.50
            and CTA_STRONG_PATTERN.search(str(segment.get("text", "")))
        ),
        None,
    )
    if cta_index is None:
        cta_index = next(
            (
                index
                for index, segment in enumerate(segments)
                if float(segment.get("start", 0.0)) >= duration * 0.70
                and CTA_PROMOTION_PATTERN.search(str(segment.get("text", "")))
            ),
            None,
        )
    tutorial_start = (
        float(segments[tutorial_index].get("start", 0.0))
        if tutorial_index is not None
        else None
    )
    cta_start = (
        float(segments[cta_index].get("start", 0.0))
        if cta_index is not None
        else None
    )
    hook_segments = segments[:tutorial_index] if tutorial_index is not None else segments[:1]
    cta_segments = segments[cta_index:] if cta_index is not None else []
    tutorial_end = cta_start if cta_start is not None else duration
    metrics["sections"] = {
        "tutorial_transition_seconds": round_or_none(tutorial_start, 3),
        "cta_start_seconds": round_or_none(cta_start, 3),
        "hook_duration_fraction": round_or_none(tutorial_start / duration, 4)
        if tutorial_start is not None and duration
        else None,
        "tutorial_duration_fraction": round_or_none(
            max(0.0, tutorial_end - (tutorial_start or 0.0)) / duration,
            4,
        )
        if duration
        else None,
        "cta_duration_fraction": round_or_none((duration - cta_start) / duration, 4)
        if cta_start is not None and duration
        else None,
        "hook_excerpt": " ".join(str(segment.get("text", "")).strip() for segment in hook_segments).strip(),
        "transition_excerpt": str(segments[tutorial_index].get("text", "")).strip()
        if tutorial_index is not None
        else "",
        "cta_excerpt": " ".join(str(segment.get("text", "")).strip() for segment in cta_segments).strip(),
        "cta_opening_excerpt": str(segments[cta_index].get("text", "")).strip()
        if cta_index is not None
        else "",
        "signoff_excerpt": str(segments[-1].get("text", "")).strip() if segments else "",
    }
    return metrics


def transcript_style_from_path(path: Path) -> dict:
    import json

    return transcript_style(json.loads(path.read_text(encoding="utf-8")))
