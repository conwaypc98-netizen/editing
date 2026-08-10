#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
from pathlib import Path


REQUIRED_FIELDS = (
    "story_role",
    "rationale",
    "viewer_purpose",
    "take_choice",
    "continuity",
    "evidence",
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_word(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower())


def parse_keep(plan: dict) -> list[dict]:
    keep = plan.get("keep", plan.get("segments"))
    if not isinstance(keep, list) or not keep:
        raise SystemExit("Edit plan must contain a non-empty keep array.")
    return keep


def load_words(path: Path | None) -> list[dict]:
    if path is None:
        return []
    data = load_json(path)
    words = []
    for segment in data.get("segments", []):
        for raw in segment.get("words", []):
            if raw.get("start") is None or raw.get("end") is None:
                continue
            words.append(
                {
                    "start": float(raw["start"]),
                    "end": float(raw["end"]),
                    "word": str(raw.get("word", "")).strip(),
                    "norm": normalize_word(str(raw.get("word", ""))),
                }
            )
    return words


def overlaps(start: float, end: float, other_start: float, other_end: float) -> bool:
    return end > other_start and start < other_end


def selected_segments(keep: list[dict], start: float, end: float) -> list[dict]:
    return [item for item in keep if overlaps(float(item["start"]), float(item["end"]), start, end)]


def boundary_inside_word(boundary: float, words: list[dict]) -> dict | None:
    for word in words:
        if word["start"] + 0.025 < boundary < word["end"] - 0.025:
            return word
    return None


def words_for_keep(keep: list[dict], words: list[dict]) -> list[dict]:
    selected = []
    for item in keep:
        start = float(item["start"])
        end = float(item["end"])
        selected.extend(word for word in words if word["start"] >= start - 0.03 and word["end"] <= end + 0.03)
    return selected


def validate(args: argparse.Namespace) -> dict:
    plan_path = Path(args.plan).expanduser().resolve()
    plan = load_json(plan_path)
    keep = parse_keep(plan)
    project = load_json(Path(args.project).expanduser().resolve()) if args.project else {}
    dossier = load_json(Path(args.dossier).expanduser().resolve()) if args.dossier else {}
    words = load_words(Path(args.transcript_json).expanduser().resolve() if args.transcript_json else None)

    errors = []
    warnings = []
    previous_end = -1.0
    roles = set()

    for index, item in enumerate(keep, start=1):
        prefix = f"keep[{index}]"
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{prefix} must have numeric start and end.")
            continue
        if start < 0 or end <= start:
            errors.append(f"{prefix} has an invalid range {start}-{end}.")
        if start < previous_end:
            errors.append(f"{prefix} is not chronological; {start:.3f} < {previous_end:.3f}.")
        previous_end = max(previous_end, end)

        for field in REQUIRED_FIELDS:
            value = item.get(field)
            if value is None or value == "" or value == [] or value == {}:
                errors.append(f"{prefix} is missing required reasoning field '{field}'.")
        role = str(item.get("story_role", "")).strip().lower()
        if role:
            roles.add(role)
        evidence = item.get("evidence")
        if isinstance(evidence, dict):
            has_transcript = bool(evidence.get("transcript_segment_ids"))
            has_frames = bool(evidence.get("frame_times") or evidence.get("frame_paths"))
            if not has_transcript and not has_frames:
                errors.append(f"{prefix} evidence has no transcript IDs or frame references.")

        if words and not item.get("lock"):
            start_word = boundary_inside_word(start, words)
            end_word = boundary_inside_word(end, words)
            if start_word:
                errors.append(
                    f"{prefix} starts inside '{start_word['word']}' "
                    f"({start_word['start']:.3f}-{start_word['end']:.3f})."
                )
            if end_word:
                errors.append(
                    f"{prefix} ends inside '{end_word['word']}' "
                    f"({end_word['start']:.3f}-{end_word['end']:.3f})."
                )

    required_roles = {str(role).strip().lower() for role in project.get("required_story_roles", [])}
    missing_roles = sorted(required_roles - roles)
    if missing_roles:
        errors.append("Missing required story roles: " + ", ".join(missing_roles))

    duplicate_resolutions = {
        str(item.get("group_id")): item
        for item in plan.get("duplicate_resolutions", [])
        if item.get("group_id")
    }
    for duplicate in dossier.get("language_evidence", {}).get("duplicate_candidates", []):
        group_id = str(duplicate.get("group_id", "unknown"))
        left = duplicate["left"]
        right = duplicate["right"]
        left_kept = selected_segments(keep, float(left["start"]), float(left["end"]))
        right_kept = selected_segments(keep, float(right["start"]), float(right["end"]))
        similarity = float(duplicate.get("sequence_similarity", 0.0))
        if left_kept and right_kept:
            allowed = all(bool(item.get("allow_repeat")) for item in left_kept + right_kept)
            resolution = duplicate_resolutions.get(group_id, {})
            if not allowed or not str(resolution.get("reason", "")).strip():
                errors.append(
                    f"{group_id} keeps both likely duplicate takes without a justified intentional repeat."
                )
        elif similarity >= 0.78 and (left_kept or right_kept) and group_id not in duplicate_resolutions:
            warnings.append(f"{group_id} appears resolved but has no duplicate_resolutions entry.")

    selected_words = words_for_keep(keep, words)
    for previous, current in zip(selected_words, selected_words[1:]):
        if previous["norm"] and previous["norm"] == current["norm"]:
            warnings.append(
                f"Rendered-order adjacent repeat candidate: '{previous['word']} {current['word']}' "
                f"from source {previous['start']:.3f}/{current['start']:.3f}."
            )

    selected_duration = sum(float(item["end"]) - float(item["start"]) for item in keep if "start" in item and "end" in item)
    target = project.get("target_duration_seconds", {})
    minimum = float(target.get("minimum", 0.0) or 0.0)
    maximum = float(target.get("maximum", 0.0) or 0.0)
    if minimum and selected_duration < minimum:
        warnings.append(f"Selected duration {selected_duration:.1f}s is below project minimum {minimum:.1f}s.")
    if maximum and selected_duration > maximum:
        warnings.append(f"Selected duration {selected_duration:.1f}s exceeds project maximum {maximum:.1f}s.")

    return {
        "schema_version": 1,
        "plan": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "passed": not errors,
        "selected_segments": len(keep),
        "selected_duration_seconds": round(selected_duration, 3),
        "story_roles": sorted(roles),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate semantic, continuity, and boundary evidence in a Luna edit plan.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--project")
    parser.add_argument("--transcript-json")
    parser.add_argument("--dossier")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = validate(args)
    write_json(Path(args.report).expanduser().resolve(), report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
