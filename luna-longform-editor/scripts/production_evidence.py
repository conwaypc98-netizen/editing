#!/usr/bin/env python3
"""Stable hashes and review validation for autonomous Luna productions."""

import hashlib
import json
import subprocess
from pathlib import Path


MUTABLE_SHOT_FIELDS = {
    "video",
    "voiceover",
    "recording_review",
    "voice_review",
}

RECORDING_VERDICTS = (
    "required_visual_state_visible",
    "no_private_information",
    "cursor_deliberate",
    "ui_readable",
    "actions_complete",
)

VOICE_VERDICTS = (
    "pronunciation_clear",
    "cadence_natural",
    "no_audio_artifacts",
    "speaker_identity_match",
    "emotional_delivery_match",
)

CLAIM_TYPES = {
    "explanation",
    "hook",
    "instruction",
    "measured_result",
    "observation",
    "promotion",
    "transition",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return sha256_bytes(payload)


def shot_spec(shot: dict) -> dict:
    return {
        key: value
        for key, value in shot.items()
        if key not in MUTABLE_SHOT_FIELDS
    }


def shot_spec_sha256(shot: dict) -> str:
    return canonical_sha256(shot_spec(shot))


def shot_plan_spec(plan: dict) -> dict:
    return {
        "schema_version": plan.get("schema_version"),
        "title": plan.get("title"),
        "story": plan.get("story"),
        "shots": [shot_spec(shot) for shot in plan.get("shots", [])],
    }


def shot_plan_spec_sha256(plan: dict) -> str:
    return canonical_sha256(shot_plan_spec(plan))


def narration_sha256(text: str) -> str:
    return sha256_bytes(text.strip().encode("utf-8"))


def media_identity(path: Path) -> dict:
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "sha256": sha256_file(resolved),
    }


def identity_matches(identity: object, path: Path) -> bool:
    if not isinstance(identity, dict) or not path.is_file():
        return False
    return identity == media_identity(path)


def xai_voice_provenance_errors(shot: dict, voiceover: Path) -> list[str]:
    shot_id = str(shot.get("id", "unknown"))
    sidecar = voiceover.with_suffix(voiceover.suffix + ".xai.json")
    if not sidecar.is_file():
        return [f"{shot_id} has no xAI provenance sidecar."]
    try:
        metadata = read_json(sidecar)
    except (json.JSONDecodeError, OSError) as error:
        return [f"{shot_id} xAI provenance sidecar is unreadable: {error}"]
    errors = []
    if metadata.get("provider") != "xai":
        errors.append(f"{shot_id} voice provenance provider is not xAI.")
    if metadata.get("shot_id") != shot_id:
        errors.append(f"{shot_id} voice provenance belongs to another shot.")
    if metadata.get("shot_spec_sha256") != shot_spec_sha256(shot):
        errors.append(f"{shot_id} voice provenance is stale because the shot specification changed.")
    if metadata.get("narration_sha256") != narration_sha256(str(shot.get("narration", ""))):
        errors.append(f"{shot_id} voice provenance does not match the approved narration.")
    if not identity_matches(metadata.get("media_identity"), voiceover):
        errors.append(f"{shot_id} voice provenance does not match the current audio bytes.")
    return errors


def transcript_source_errors(
    transcript: Path,
    source_media: Path,
    require_identity: bool = False,
) -> list[str]:
    try:
        payload = read_json(transcript)
    except (json.JSONDecodeError, OSError) as error:
        return [f"Transcript is unreadable: {error}"]
    identity = payload.get("source_media_identity")
    if identity is None:
        return ["Transcript has no source-media identity."] if require_identity else []
    if not identity_matches(identity, source_media):
        return ["Transcript was generated from different source-media bytes."]
    return []


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def resolve_media(value: str | None, base: Path, fallback: Path | None = None) -> Path | None:
    if value:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (base / path).resolve()
    return fallback.resolve() if fallback else None


def valid_box(value: object) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    try:
        left, top, right, bottom = [float(part) for part in value]
    except (TypeError, ValueError):
        return False
    return 0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0


def validate_shot_schema(shot: dict, index: int, schema_version: int = 2) -> list[str]:
    errors = []
    shot_id = str(shot.get("id", f"shot-{index:03d}"))
    for field in (
        "id",
        "story_role",
        "viewer_purpose",
        "rationale",
        "continuity",
        "narration",
        "required_visual_state",
        "timing_mode",
    ):
        if not str(shot.get(field, "")).strip():
            errors.append(f"{shot_id} is missing {field}.")
    actions = shot.get("computer_actions")
    if not isinstance(actions, list) or not actions or any(not str(action).strip() for action in actions):
        errors.append(f"{shot_id} must list non-empty computer_actions.")
    try:
        maximum_recording = float(shot.get("maximum_recording_seconds"))
        if maximum_recording <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"{shot_id} must have a positive maximum_recording_seconds value.")
    target_box = shot.get("target_box")
    if target_box is not None and not valid_box(target_box):
        errors.append(f"{shot_id} target_box must be a normalized [left, top, right, bottom] box.")
    include_boxes = shot.get("include_boxes", [])
    if not isinstance(include_boxes, list) or any(not valid_box(box) for box in include_boxes):
        errors.append(f"{shot_id} include_boxes must contain only normalized boxes.")
    if schema_version >= 3:
        claim = shot.get("claim_support")
        if not isinstance(claim, dict):
            errors.append(f"{shot_id} must define a claim_support object.")
        else:
            claim_type = str(claim.get("type", "")).strip().lower()
            if claim_type not in CLAIM_TYPES:
                errors.append(
                    f"{shot_id} claim_support.type must be one of: "
                    + ", ".join(sorted(CLAIM_TYPES))
                )
            if not str(claim.get("spoken_claim", "")).strip():
                errors.append(f"{shot_id} claim_support.spoken_claim is required.")
            if not str(claim.get("visible_evidence", "")).strip():
                errors.append(f"{shot_id} claim_support.visible_evidence is required.")
        for field in ("capture_checkpoints", "retake_triggers"):
            value = shot.get(field)
            if not isinstance(value, list) or not value or any(not str(item).strip() for item in value):
                errors.append(f"{shot_id} must list non-empty {field}.")
        if not str(shot.get("creator_style_rationale", "")).strip():
            errors.append(f"{shot_id} is missing creator_style_rationale.")
    return errors


def validate_shot_plan(plan: dict, project: dict) -> dict:
    shots = plan.get("shots", [])
    try:
        schema_version = int(plan.get("schema_version", 2))
    except (TypeError, ValueError):
        schema_version = 0
    errors = []
    warnings = []
    if not isinstance(shots, list) or not shots:
        errors.append("Shot plan contains no shots.")
        shots = []
    ids = []
    roles = set()
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            errors.append(f"shot[{index}] must be an object.")
            continue
        errors.extend(validate_shot_schema(shot, index, schema_version))
        shot_id = str(shot.get("id", "")).strip()
        if shot_id:
            ids.append(shot_id)
        role = str(shot.get("story_role", "")).strip().lower()
        if role:
            roles.add(role)
    duplicates = sorted({shot_id for shot_id in ids if ids.count(shot_id) > 1})
    if duplicates:
        errors.append("Duplicate shot IDs: " + ", ".join(duplicates))
    required_roles = {
        str(role).strip().lower()
        for role in project.get("required_story_roles", [])
        if str(role).strip()
    }
    missing_roles = sorted(required_roles - roles)
    if missing_roles:
        errors.append("Shot plan is missing required story roles: " + ", ".join(missing_roles))
    if len(shots) > 80:
        warnings.append("Shot count exceeds 80; confirm the tutorial is not fragmented into nervous micro-cuts.")
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "shot_count": len(shots),
        "story_roles": sorted(roles),
        "shot_plan_spec_sha256": shot_plan_spec_sha256(plan),
        "shots": [
            {
                "id": str(shot.get("id", f"shot-{index:03d}")),
                "shot_spec_sha256": shot_spec_sha256(shot),
                "narration_sha256": narration_sha256(str(shot.get("narration", ""))),
            }
            for index, shot in enumerate(shots, start=1)
            if isinstance(shot, dict)
        ],
    }


def validate_sealed_review(review: object, kind: str, shot: dict, media: Path) -> list[str]:
    errors = []
    shot_id = str(shot.get("id", "unknown"))
    if not isinstance(review, dict):
        return [f"{shot_id} has no sealed {kind} review."]
    if review.get("kind") != f"{kind}_review":
        errors.append(f"{shot_id} review kind does not match {kind}.")
    if review.get("shot_id") != shot_id:
        errors.append(f"{shot_id} review belongs to another shot.")
    if review.get("shot_spec_sha256") != shot_spec_sha256(shot):
        errors.append(f"{shot_id} {kind} review is stale because the shot specification changed.")
    if not identity_matches(review.get("media_identity"), media):
        errors.append(f"{shot_id} {kind} review does not match the current media bytes.")
    if review.get("passed") is not True:
        errors.append(f"{shot_id} {kind} review is not passing.")
    verdict = review.get("verdict", {})
    required = RECORDING_VERDICTS if kind == "recording" else VOICE_VERDICTS
    for field in required:
        if verdict.get(field) is not True:
            errors.append(f"{shot_id} {kind} verdict {field} is not true.")
    if not str(verdict.get("notes", "")).strip():
        errors.append(f"{shot_id} {kind} review needs concrete notes.")
    if kind == "recording" and not review.get("evidence"):
        errors.append(f"{shot_id} recording review has no extracted evidence frames.")
    return errors
