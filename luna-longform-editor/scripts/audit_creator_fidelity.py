#!/usr/bin/env python3
"""Fail-closed creator-style and narration/visual-contract audit."""

import argparse
import difflib
import json
from pathlib import Path

from creator_fidelity import (
    ACTION_WORDS,
    lexical_overlap,
    text_style_metrics,
    tokens,
    transcript_style,
)
from production_evidence import (
    media_identity,
    read_json,
    shot_plan_spec_sha256,
    write_json,
)


def confidence_level(profile: dict) -> str:
    return str(
        profile.get("creator_fingerprint", {}).get(
            "confidence",
            profile.get("quantitative_confidence", "untrained"),
        )
    ).lower()


def metric_similarity(actual: object, expected: object) -> float | None:
    if actual is None or expected is None:
        return None
    actual_value = float(actual)
    expected_value = float(expected)
    scale = max(abs(expected_value), 0.25)
    return max(0.0, 1.0 - abs(actual_value - expected_value) / (scale * 1.5))


def fingerprint_score(metrics: dict, profile: dict) -> dict:
    expected = profile.get("creator_fingerprint", {}).get("linguistic_medians", {})
    details = {}
    values = []
    for key, target in expected.items():
        score = metric_similarity(metrics.get(key), target)
        if score is None:
            continue
        details[key] = {
            "actual": metrics.get(key),
            "expected_median": target,
            "similarity": round(score, 4),
        }
        values.append(score)
    return {
        "score": round(sum(values) / len(values), 4) if values else None,
        "features": details,
    }


def role_order_errors(plan: dict, profile: dict) -> list[str]:
    preferred = [
        str(role).strip().lower()
        for role in profile.get("story_and_visuals", {}).get("order", [])
    ]
    rank = {role: index for index, role in enumerate(preferred)}
    observed = [str(shot.get("story_role", "")).strip().lower() for shot in plan.get("shots", [])]
    errors = []
    prior = -1
    for shot, role in zip(plan.get("shots", []), observed):
        if role not in rank:
            continue
        if rank[role] < prior:
            errors.append(
                f"{shot.get('id', 'unknown')} role {role!r} moves backward in the learned story order."
            )
        prior = max(prior, rank[role])
    return errors


def narration_similarity(first: str, second: str) -> float:
    return difflib.SequenceMatcher(None, tokens(first), tokens(second)).ratio()


def audit_plan(plan: dict, project: dict, profile: dict) -> tuple[list[str], list[str], dict]:
    errors = role_order_errors(plan, profile)
    warnings = []
    shots = plan.get("shots", [])
    narrations = [str(shot.get("narration", "")).strip() for shot in shots]
    full_text = " ".join(narrations)
    target_wpm = float(
        profile.get("learned_measurements", {}).get("words_per_minute_median") or 190.0
    )
    estimated_duration = len(tokens(full_text)) / target_wpm * 60.0 if target_wpm else 0.0
    metrics = text_style_metrics(full_text, narrations, estimated_duration or None)

    if metrics["adjacent_repeated_phrases"]:
        errors.append(
            "Planned narration contains adjacent repeated wording: "
            + ", ".join(metrics["adjacent_repeated_phrases"])
        )
    if metrics["duplicate_units"]:
        errors.append(
            "Planned narration repeats complete shot lines: "
            + "; ".join(metrics["duplicate_units"])
        )
    if metrics["filler_words_per_100_words"] > 8.0:
        errors.append("Planned narration contains too many filler words to sound intentionally fluent.")
    elif metrics["filler_words_per_100_words"] > 4.0:
        warnings.append("Planned narration uses more filler wording than the accepted Luna sample.")

    exemplars = profile.get("creator_fingerprint", {}).get("accepted_exemplars", [])
    hook_counts = [len(tokens(str(item.get("hook", "")))) for item in exemplars if item.get("hook")]
    hook_limit = max(45, int(max(hook_counts or [22]) * 2.0))
    hook_shots = [shot for shot in shots if str(shot.get("story_role", "")).lower() == "hook"]
    if hook_shots:
        hook_words = sum(len(tokens(str(shot.get("narration", "")))) for shot in hook_shots)
        if hook_words > hook_limit:
            errors.append(
                f"Hook narration is {hook_words} words; the evidence-based maximum is {hook_limit}."
            )

    per_shot = []
    for shot in shots:
        shot_id = str(shot.get("id", "unknown"))
        narration = str(shot.get("narration", ""))
        actions = " ".join(str(value) for value in shot.get("computer_actions", []))
        required_state = str(shot.get("required_visual_state", ""))
        narration_words = tokens(narration)
        estimated = len(narration_words) / target_wpm * 60.0 if target_wpm else 0.0
        maximum = float(shot.get("maximum_recording_seconds") or 0.0)
        if maximum and estimated > maximum * 1.18:
            errors.append(
                f"{shot_id} narration needs about {estimated:.2f}s at the learned pace but the shot allows {maximum:.2f}s."
            )
        elif maximum and maximum > max(estimated * 3.0, estimated + 6.0):
            warnings.append(
                f"{shot_id} recording allowance is much longer than its narration; avoid leaving dead UI time."
            )

        action_language = any(word in ACTION_WORDS for word in narration_words)
        action_overlap = lexical_overlap(narration, actions)
        if str(shot.get("story_role", "")).lower() in {"setup", "tutorial", "proof"}:
            if not action_language and action_overlap < 0.04:
                warnings.append(
                    f"{shot_id} narration has weak lexical alignment with its computer actions; inspect it semantically."
                )

        contract = shot.get("claim_support") if isinstance(shot.get("claim_support"), dict) else {}
        spoken_claim = str(contract.get("spoken_claim", ""))
        visible_evidence = str(contract.get("visible_evidence", ""))
        claim_overlap = lexical_overlap(narration, spoken_claim) if spoken_claim else 0.0
        evidence_overlap = lexical_overlap(required_state, visible_evidence) if visible_evidence else 0.0
        if int(plan.get("schema_version", 0)) >= 3:
            if claim_overlap < 0.03:
                warnings.append(
                    f"{shot_id} spoken-claim contract shares little language with the approved narration."
                )
            if evidence_overlap < 0.03:
                warnings.append(
                    f"{shot_id} visible-evidence contract shares little language with the required state."
                )
        per_shot.append(
            {
                "id": shot_id,
                "word_count": len(narration_words),
                "estimated_narration_seconds": round(estimated, 3),
                "maximum_recording_seconds": maximum,
                "action_overlap": round(action_overlap, 4),
                "claim_overlap": round(claim_overlap, 4),
                "evidence_overlap": round(evidence_overlap, 4),
            }
        )

    target = project.get("target_duration_seconds", {})
    minimum = float(target.get("minimum") or 0.0)
    maximum = float(target.get("maximum") or 0.0)
    if minimum and estimated_duration < minimum * 0.60:
        warnings.append(
            f"Estimated narration is {estimated_duration:.1f}s, well below the project target minimum of {minimum:.1f}s."
        )
    if maximum and estimated_duration > maximum * 1.35:
        errors.append(
            f"Estimated narration is {estimated_duration:.1f}s, above the project target maximum of {maximum:.1f}s."
        )

    fidelity = fingerprint_score(metrics, profile)
    confidence = confidence_level(profile)
    if fidelity["score"] is not None and fidelity["score"] < 0.45:
        message = (
            f"Narration fingerprint similarity is {fidelity['score']:.3f}; wording and sentence behavior need a Luna-style rewrite."
        )
        (errors if confidence in {"medium", "high"} else warnings).append(message)
    return errors, warnings, {
        "estimated_duration_seconds": round(estimated_duration, 3),
        "target_words_per_minute": target_wpm,
        "style_metrics": metrics,
        "fingerprint": fidelity,
        "shots": per_shot,
    }


def audit_final(
    plan: dict,
    project: dict,
    profile: dict,
    transcript: dict,
) -> tuple[list[str], list[str], dict]:
    del project
    errors = []
    warnings = []
    metrics = transcript_style(transcript)
    wpm = metrics.get("words_per_minute")
    if wpm is None:
        errors.append("Final transcript has no measurable duration or words.")
    elif not 140.0 <= float(wpm) <= 320.0:
        errors.append(f"Final narration pace {wpm:.1f} WPM is outside the fluent tutorial guardrail.")

    if metrics["adjacent_repeated_phrases"]:
        errors.append(
            "Final narration contains adjacent repeated wording: "
            + ", ".join(metrics["adjacent_repeated_phrases"])
        )
    if metrics["duplicate_units"]:
        errors.append(
            "Final transcript repeats complete units: " + "; ".join(metrics["duplicate_units"])
        )
    if metrics["filler_words_per_100_words"] > 8.0:
        errors.append("Final narration contains too many filler words to match the fluent channel standard.")

    intended = " ".join(str(shot.get("narration", "")) for shot in plan.get("shots", []))
    actual = " ".join(str(segment.get("text", "")) for segment in transcript.get("segments", []))
    script_similarity = narration_similarity(intended, actual)
    if script_similarity < 0.86:
        errors.append(
            f"Final transcript similarity {script_similarity:.3f} does not match the approved narration closely enough."
        )

    learned_wpm = profile.get("learned_measurements", {}).get("words_per_minute_median")
    confidence = confidence_level(profile)
    if wpm is not None and learned_wpm:
        ratio = float(wpm) / float(learned_wpm)
        if not 0.68 <= ratio <= 1.32:
            message = (
                f"Final pace is {ratio:.2f}x the accepted Luna median ({float(learned_wpm):.1f} WPM)."
            )
            (errors if confidence in {"medium", "high"} else warnings).append(message)

    fidelity = fingerprint_score(metrics, profile)
    if fidelity["score"] is not None and fidelity["score"] < 0.45:
        message = f"Final creator-fingerprint similarity is only {fidelity['score']:.3f}."
        (errors if confidence in {"medium", "high"} else warnings).append(message)
    return errors, warnings, {
        "style_metrics": metrics,
        "script_similarity": round(script_similarity, 4),
        "fingerprint": fidelity,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a Luna production against its creator profile.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "final"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--shot-plan", required=True)
        sub.add_argument("--project", required=True)
        sub.add_argument("--channel-profile", required=True)
        sub.add_argument("--report", required=True)
        if name == "final":
            sub.add_argument("--transcript-json", required=True)
    args = parser.parse_args()

    plan_path = Path(args.shot_plan).expanduser().resolve()
    project_path = Path(args.project).expanduser().resolve()
    profile_path = Path(args.channel_profile).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    plan = read_json(plan_path)
    project = read_json(project_path)
    profile = read_json(profile_path)

    transcript_path = None
    if args.command == "plan":
        errors, warnings, evidence = audit_plan(plan, project, profile)
    else:
        transcript_path = Path(args.transcript_json).expanduser().resolve()
        errors, warnings, evidence = audit_final(
            plan,
            project,
            profile,
            read_json(transcript_path),
        )
    report = {
        "schema_version": 1,
        "mode": args.command,
        "shot_plan": str(plan_path),
        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
        "project": str(project_path),
        "project_identity": media_identity(project_path),
        "channel_profile": str(profile_path),
        "channel_profile_identity": media_identity(profile_path),
        "transcript": str(transcript_path) if transcript_path else None,
        "transcript_identity": media_identity(transcript_path) if transcript_path else None,
        "profile_confidence": confidence_level(profile),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "evidence": evidence,
    }
    write_json(report_path, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
