#!/usr/bin/env python3
import argparse
import collections
import difflib
import hashlib
import json
import re
from pathlib import Path


def tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", value.lower())


def transcript_text(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return " ".join(str(segment.get("text", "")).strip() for segment in data.get("segments", [])).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_transcripts(values: list[str], directory: Path | None, shot_ids: list[str]) -> dict[str, Path]:
    mapping = {}
    for value in values:
        if "=" not in value:
            raise SystemExit("--transcript must be SHOT_ID=TRANSCRIPT_JSON")
        shot_id, path = value.split("=", 1)
        mapping[shot_id.strip()] = Path(path).expanduser().resolve()
    if directory:
        for shot_id in shot_ids:
            candidates = [directory / shot_id / "transcript.json", directory / f"{shot_id}.json"]
            found = next((candidate for candidate in candidates if candidate.is_file()), None)
            if found:
                mapping.setdefault(shot_id, found.resolve())
    return mapping


def repeated_phrases(actual: list[str]) -> list[str]:
    repeats = []
    for size in range(1, 5):
        for index in range(len(actual) - size * 2 + 1):
            phrase = actual[index : index + size]
            if phrase and phrase == actual[index + size : index + size * 2]:
                repeats.append(" ".join(phrase))
    return sorted(set(repeats))


def compare(intended_text: str, actual_text: str, minimum_similarity: float, maximum_missing: float) -> dict:
    intended = tokens(intended_text)
    actual = tokens(actual_text)
    if not intended or not actual:
        return {
            "passed": False,
            "similarity": 0.0,
            "missing_fraction": 1.0,
            "missing_words": intended,
            "extra_words": actual,
            "repeated_phrases": [],
            "errors": ["Narration or transcript is empty."],
        }
    similarity = difflib.SequenceMatcher(None, intended, actual).ratio()
    intended_counts = collections.Counter(intended)
    actual_counts = collections.Counter(actual)
    missing = list((intended_counts - actual_counts).elements())
    extra = list((actual_counts - intended_counts).elements())
    missing_fraction = len(missing) / len(intended)
    repeats = repeated_phrases(actual)
    errors = []
    if similarity < minimum_similarity:
        errors.append(f"Transcript similarity {similarity:.3f} is below {minimum_similarity:.3f}.")
    if missing_fraction > maximum_missing:
        errors.append(f"Missing-word fraction {missing_fraction:.3f} exceeds {maximum_missing:.3f}.")
    if repeats:
        errors.append("Synthesized narration contains adjacent repeated wording: " + ", ".join(repeats))
    return {
        "passed": not errors,
        "similarity": round(similarity, 4),
        "missing_fraction": round(missing_fraction, 4),
        "missing_words": missing,
        "extra_words": extra,
        "repeated_phrases": repeats,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare synthesized per-shot narration with its approved script.")
    parser.add_argument("--shot-plan", required=True)
    parser.add_argument("--transcript", action="append", default=[])
    parser.add_argument("--transcript-dir")
    parser.add_argument("--minimum-similarity", type=float, default=0.86)
    parser.add_argument("--maximum-missing-fraction", type=float, default=0.10)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    plan_path = Path(args.shot_plan).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    shots = plan.get("shots", [])
    if not shots:
        raise SystemExit("Shot plan contains no shots.")
    shot_ids = [str(shot.get("id", f"shot-{index:03d}")) for index, shot in enumerate(shots, start=1)]
    transcript_dir = Path(args.transcript_dir).expanduser().resolve() if args.transcript_dir else None
    mapping = resolve_transcripts(args.transcript, transcript_dir, shot_ids)

    results = []
    errors = []
    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.get("id", f"shot-{index:03d}"))
        path = mapping.get(shot_id)
        if path is None or not path.is_file():
            message = f"{shot_id} transcript is missing."
            errors.append(message)
            results.append({"id": shot_id, "passed": False, "errors": [message]})
            continue
        actual = transcript_text(path)
        comparison = compare(
            str(shot.get("narration", "")),
            actual,
            args.minimum_similarity,
            args.maximum_missing_fraction,
        )
        if not comparison["passed"]:
            errors.extend(f"{shot_id}: {message}" for message in comparison["errors"])
        results.append(
            {
                "id": shot_id,
                "transcript": str(path),
                "actual_text": actual,
                **comparison,
            }
        )

    report = {
        "schema_version": 1,
        "shot_plan": str(plan_path),
        "shot_plan_sha256": sha256_file(plan_path),
        "passed": not errors,
        "errors": errors,
        "shots": results,
        "listening_review_still_required": True,
    }
    report_path = Path(args.report).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
