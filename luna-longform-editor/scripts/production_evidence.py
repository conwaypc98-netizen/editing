#!/usr/bin/env python3
"""Stable hashes and review validation for autonomous Luna productions."""

import hashlib
import json
import re
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

XAI_INLINE_SPEECH_TAGS = {
    "breath",
    "chuckle",
    "cry",
    "exhale",
    "giggle",
    "hum-tune",
    "inhale",
    "laugh",
    "lip-smack",
    "long-pause",
    "pause",
    "sigh",
    "tongue-click",
    "tsk",
}

XAI_WRAPPING_SPEECH_TAGS = {
    "build-intensity",
    "decrease-intensity",
    "emphasis",
    "fast",
    "higher-pitch",
    "laugh-speak",
    "loud",
    "lower-pitch",
    "sing-song",
    "singing",
    "slow",
    "soft",
    "whisper",
}

LUNA_INLINE_SPEECH_TAGS = {"breath", "exhale", "inhale", "pause"}
LUNA_WRAPPING_SPEECH_TAGS = {
    "build-intensity",
    "decrease-intensity",
    "emphasis",
    "fast",
    "slow",
    "soft",
}

SPEECH_TAG_PATTERN = re.compile(r"\[([a-z-]+)\]|<(/?)([a-z-]+)>", re.I)
WORD_PATTERN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.I)


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


def strip_speech_tags(text: str) -> str:
    return re.sub(r"\[[a-z-]+\]|</?[a-z-]+>", " ", text, flags=re.I)


def spoken_tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in WORD_PATTERN.finditer(strip_speech_tags(text))]


def tts_text_for_shot(shot: dict) -> str:
    performance = shot.get("voice_performance")
    if isinstance(performance, dict) and str(performance.get("tts_text", "")).strip():
        return str(performance["tts_text"]).strip()
    return str(shot.get("narration", "")).strip()


def target_wpm_for_shot(shot: dict) -> tuple[float, float] | None:
    performance = shot.get("voice_performance")
    if not isinstance(performance, dict):
        return None
    value = performance.get("target_words_per_minute")
    if not isinstance(value, dict):
        return None
    try:
        return float(value["minimum"]), float(value["maximum"])
    except (KeyError, TypeError, ValueError):
        return None


def voice_performance_errors(shot: dict, shot_id: str) -> list[str]:
    performance = shot.get("voice_performance")
    if not isinstance(performance, dict):
        return [f"{shot_id} must define a voice_performance object."]

    errors = []
    tts_text = str(performance.get("tts_text", "")).strip()
    if not tts_text:
        errors.append(f"{shot_id} voice_performance.tts_text is required.")
    elif len(tts_text) > 15000:
        errors.append(f"{shot_id} voice_performance.tts_text exceeds xAI's 15,000-character limit.")
    elif spoken_tokens(tts_text) != spoken_tokens(str(shot.get("narration", ""))):
        errors.append(
            f"{shot_id} voice_performance.tts_text must speak exactly the approved narration words."
        )

    try:
        speed = float(performance.get("speed"))
        if not 0.7 <= speed <= 1.5:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"{shot_id} voice_performance.speed must be between 0.7 and 1.5.")

    target_wpm = target_wpm_for_shot(shot)
    if target_wpm is None:
        errors.append(
            f"{shot_id} voice_performance.target_words_per_minute needs minimum and maximum values."
        )
    else:
        minimum, maximum = target_wpm
        if not 100 <= minimum <= maximum <= 360:
            errors.append(
                f"{shot_id} voice_performance.target_words_per_minute must stay between 100 and 360."
            )

    if not str(performance.get("delivery_intent", "")).strip():
        errors.append(f"{shot_id} voice_performance.delivery_intent is required.")
    pronunciation_checks = performance.get("pronunciation_checks")
    if not isinstance(pronunciation_checks, list) or any(
        not str(item).strip() for item in pronunciation_checks
    ):
        errors.append(f"{shot_id} voice_performance.pronunciation_checks must be a list of terms.")
    retake_triggers = performance.get("retake_triggers")
    if not isinstance(retake_triggers, list) or not retake_triggers or any(
        not str(item).strip() for item in retake_triggers
    ):
        errors.append(f"{shot_id} voice_performance.retake_triggers must list concrete failures.")

    wrapping_stack = []
    tag_count = 0
    for match in SPEECH_TAG_PATTERN.finditer(tts_text):
        tag_count += 1
        inline, closing, wrapping = match.groups()
        if inline:
            tag = inline.casefold()
            if tag not in XAI_INLINE_SPEECH_TAGS:
                errors.append(f"{shot_id} uses unsupported xAI speech tag [{inline}].")
            elif tag not in LUNA_INLINE_SPEECH_TAGS:
                errors.append(f"{shot_id} uses [{inline}], which is not approved for Luna tutorials.")
            continue
        tag = str(wrapping).casefold()
        if tag not in XAI_WRAPPING_SPEECH_TAGS:
            errors.append(f"{shot_id} uses unsupported xAI wrapping tag <{wrapping}>.")
        elif tag not in LUNA_WRAPPING_SPEECH_TAGS:
            errors.append(f"{shot_id} uses <{wrapping}>, which is not approved for Luna tutorials.")
        if closing:
            if not wrapping_stack or wrapping_stack[-1] != tag:
                errors.append(f"{shot_id} has an unbalanced closing speech tag </{wrapping}>.")
            else:
                wrapping_stack.pop()
        else:
            wrapping_stack.append(tag)
    if wrapping_stack:
        errors.append(f"{shot_id} has unclosed speech tags: " + ", ".join(wrapping_stack))
    maximum_tags = max(2, len(spoken_tokens(tts_text)) // 25 + 1)
    if tag_count > maximum_tags:
        errors.append(
            f"{shot_id} uses {tag_count} speech tags; the Luna limit for this line is {maximum_tags}."
        )
    return errors


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


def voice_registration_errors(registration: Path, expected_voice_id: str | None = None) -> list[str]:
    try:
        payload = read_json(registration)
    except (json.JSONDecodeError, OSError) as error:
        return [f"xAI voice registration is unreadable: {error}"]
    errors = []
    if payload.get("provider") != "xai":
        errors.append("Voice registration provider is not xAI.")
    if payload.get("verified") is not True:
        errors.append("xAI voice registration is not verified.")
    if payload.get("reference_download_verified") is not True:
        errors.append("xAI voice registration did not verify the uploaded reference bytes.")
    voice_id = str(payload.get("voice_id", ""))
    if not re.fullmatch(r"[a-z0-9]{8}", voice_id):
        errors.append("xAI voice registration has no valid custom voice ID.")
    if expected_voice_id and voice_id != expected_voice_id:
        errors.append("xAI voice registration belongs to a different voice ID.")
    if payload.get("dry_run") is not False:
        errors.append("xAI voice registration is a dry run or has no real-run attestation.")
    voice_metadata = payload.get("voice_metadata")
    if not isinstance(voice_metadata, dict) or voice_metadata.get("voice_id") != voice_id:
        errors.append("xAI voice registration metadata does not match its voice ID.")
    identities = {}
    for field in (
        "reference_identity",
        "preparation_report_identity",
        "transcript_identity",
        "reference_review_identity",
    ):
        identity = payload.get(field)
        path = Path(str(identity.get("path", ""))).expanduser() if isinstance(identity, dict) else None
        if path is None or not identity_matches(identity, path):
            errors.append(f"xAI voice registration has missing or stale {field} evidence.")
        else:
            identities[field] = identity
    reference_identity = payload.get("reference_identity")
    if isinstance(reference_identity, dict) and (
        payload.get("downloaded_reference_sha256") != reference_identity.get("sha256")
    ):
        errors.append("xAI voice registration download hash does not match the reviewed reference.")
    review_identity = identities.get("reference_review_identity")
    if review_identity:
        try:
            review = read_json(Path(review_identity["path"]))
        except (json.JSONDecodeError, OSError) as error:
            errors.append(f"xAI voice registration listening review is unreadable: {error}")
            return errors
        if review.get("passed") is not True or review.get("upload_ready") is not True:
            errors.append("xAI voice registration points to a review that is not upload-ready.")
        for registration_field, review_field in (
            ("reference_identity", "reference_identity"),
            ("preparation_report_identity", "preparation_report_identity"),
            ("transcript_identity", "transcript_identity"),
        ):
            if identities.get(registration_field) != review.get(review_field):
                errors.append(
                    f"xAI voice registration and listening review disagree on {review_field}."
                )
    return errors


def xai_voice_provenance_errors(
    shot: dict,
    voiceover: Path,
    expected_voice_id: str | None = None,
    registration: Path | None = None,
) -> list[str]:
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
    if expected_voice_id and metadata.get("voice_id") != expected_voice_id:
        errors.append(f"{shot_id} voice provenance used a different xAI voice ID.")
    if registration is not None and not identity_matches(
        metadata.get("voice_registration_identity"), registration
    ):
        errors.append(f"{shot_id} voice provenance has stale xAI voice registration evidence.")
    if registration is not None:
        try:
            registration_payload = read_json(registration)
        except (json.JSONDecodeError, OSError) as error:
            errors.append(f"{shot_id} xAI voice registration is unreadable: {error}")
            registration_payload = {}
        reference_hash = registration_payload.get("reference_identity", {}).get("sha256")
        if metadata.get("voice_reference_download_verified_at_generation") is not True:
            errors.append(f"{shot_id} did not reverify xAI source audio at generation time.")
        if metadata.get("voice_reference_sha256_at_generation") != reference_hash:
            errors.append(f"{shot_id} generation used different owner-reference evidence.")
    if metadata.get("shot_id") != shot_id:
        errors.append(f"{shot_id} voice provenance belongs to another shot.")
    if metadata.get("shot_spec_sha256") != shot_spec_sha256(shot):
        errors.append(f"{shot_id} voice provenance is stale because the shot specification changed.")
    if metadata.get("narration_sha256") != narration_sha256(str(shot.get("narration", ""))):
        errors.append(f"{shot_id} voice provenance does not match the approved narration.")
    performance = shot.get("voice_performance")
    if isinstance(performance, dict):
        if metadata.get("tts_text_sha256") != narration_sha256(tts_text_for_shot(shot)):
            errors.append(f"{shot_id} voice provenance does not match the approved TTS performance text.")
        try:
            requested_speed = float(metadata.get("requested_speed"))
            planned_speed = float(performance.get("speed"))
            if abs(requested_speed - planned_speed) > 0.0001:
                errors.append(f"{shot_id} voice provenance used a different planned speech speed.")
        except (TypeError, ValueError):
            errors.append(f"{shot_id} voice provenance has no valid planned speech speed.")
        if metadata.get("voice_verified_at_generation") is not True:
            errors.append(f"{shot_id} custom voice was not verified at generation time.")
        cadence = metadata.get("cadence")
        if not isinstance(cadence, dict) or cadence.get("within_target") is not True:
            errors.append(f"{shot_id} generated cadence is outside the approved target range.")
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
    if schema_version >= 4:
        errors.extend(voice_performance_errors(shot, shot_id))
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
