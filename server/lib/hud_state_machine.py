from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any


HUD_STATE_SCHEMA_VERSION = "rayban_pt_hud_encounter_state/v0"


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None and value != ""}


def _stable_uuid(*parts: object) -> str:
    key = ":".join(str(part or "") for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_missing(payload: dict[str, Any], fields: list[str]) -> list[str]:
    return [field for field in fields if not payload.get(field)]


def _item(
    target_table: str,
    payload: dict[str, Any],
    *,
    required_fields: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "target_table": target_table,
        "valid_for_upsert": not _required_missing(payload, required_fields),
        "missing_required_fields": _required_missing(payload, required_fields),
        "warnings": warnings or [],
        "payload": payload,
    }


def _observation_identity(event: dict[str, Any]) -> tuple[str, str]:
    test = str(event.get("test") or "").strip().lower()
    if test == "slr":
        return "straight_leg_raise", "Straight Leg Raise"
    if test == "slump":
        return "slump_test", "Slump Test"
    if test == "odi":
        return "oswestry_disability_index", "Oswestry Disability Index"
    if test == "nprs":
        return "numeric_pain_rating_scale", "Numeric Pain Rating Scale"
    return test.replace(" ", "_") or "clinical_observation", str(event.get("test") or "Clinical Observation")


def _observation_value_fields(event: dict[str, Any]) -> dict[str, Any]:
    raw_value = str(event.get("value") or "").strip()
    test = str(event.get("test") or "").strip().lower()
    if not raw_value:
        return {"value_type": "string", "value_string": "not recorded"}
    numeric = re.search(r"-?\d+(?:\.\d+)?", raw_value)
    if numeric and ("degree" in raw_value.lower() or "°" in raw_value or "도" in raw_value):
        return {
            "value_type": "quantity",
            "value_quantity": float(numeric.group(0)),
            "value_unit": "degrees",
        }
    if numeric and ("/" in raw_value or test in {"odi", "nprs"}):
        return {
            "value_type": "quantity",
            "value_quantity": float(numeric.group(0)),
            "value_unit": "score",
        }
    if raw_value.lower() in {"positive", "negative"}:
        return {"value_type": "string", "value_string": raw_value.lower()}
    return {"value_type": "string", "value_string": raw_value}


def build_hud_fixture(
    *,
    encounter_id: str = "enc-hud-fixture",
    organization_id: str = "org-fixture",
    subject_person_id: str = "person-fixture-subject",
    provider_person_id: str = "provider-fixture",
    patient_display: str = "Fixture / Lumbar pain",
    captured_at: str | None = None,
    approved: bool = True,
) -> dict[str, Any]:
    """Build a synthetic HUD encounter run without real patient data."""

    captured_at = captured_at or _now_iso()
    candidate_id = "hud-candidate-slr-left-45"
    review_status = "clinician_accepted" if approved else "auto_extracted"
    candidate_status = "confirmed_by_provider" if approved else "candidate"
    observation_status = "final" if approved else "preliminary"

    return {
        "schema_version": HUD_STATE_SCHEMA_VERSION,
        "mode": "design/dry_run",
        "safety": {
            "synthetic_non_phi": True,
            "writes_supabase": False,
            "training_started": False,
            "hud_contains_micro_cards_only": True,
        },
        "identity": {
            "organization_id": organization_id,
            "subject_person_id": subject_person_id,
            "provider_person_id": provider_person_id,
            "encounter_id": encounter_id,
        },
        "pages": [
            {
                "state": "patient_context",
                "display": {
                    "title": patient_display,
                    "lines": ["Last SLR 40 deg", "NPRS 7/10", "Today: Slump, SLR, ODI"],
                },
                "gesture": "thumb_index_tap",
                "transition": "session_mode",
            },
            {
                "state": "session_mode",
                "display": {
                    "counts": {"observations": 2, "tests": 1, "interventions": 0},
                    "recommendations": ["Slump", "Neuro screen", "ODI"],
                },
                "action": {"type": "voice_capture", "source": "rayban_meta_display"},
                "transition": "candidate_approval",
            },
            {
                "state": "candidate_approval",
                "source_text": "Synthetic therapist utterance: left SLR 45 degrees with posterior thigh pain.",
                "candidate": {
                    "id": candidate_id,
                    "event_type": "test_result",
                    "test": "SLR",
                    "side": "left",
                    "value": "45 degrees",
                    "symptom": "posterior thigh pain",
                    "source": "rayban_meta_display",
                    "status": candidate_status,
                    "confidence": None,
                },
                "gesture": "pinch_approve" if approved else "hold_for_later",
                "transition": "end_encounter",
            },
            {
                "state": "end_encounter",
                "display": {
                    "counts": {"observations": 6, "tests": 4, "interventions": 3},
                    "soap_draft": "ready",
                },
                "gesture": "pinch_complete",
            },
        ],
        "normalized_events": [
            {
                "id": candidate_id,
                "encounter_id": encounter_id,
                "event_type": "test_result",
                "test": "SLR",
                "side": "left",
                "value": "45 degrees",
                "symptom": "posterior thigh pain",
                "source": "rayban_meta_display",
                "status": candidate_status,
                "review_status": review_status,
                "observation_status": observation_status,
                "captured_at": captured_at,
            }
        ],
    }


def build_hud_moai_bundle(fixture: dict[str, Any]) -> dict[str, Any]:
    identity = fixture.get("identity") or {}
    organization_id = identity.get("organization_id")
    subject_person_id = identity.get("subject_person_id")
    provider_person_id = identity.get("provider_person_id")
    encounter_id = identity.get("encounter_id")
    event = (fixture.get("normalized_events") or [{}])[0]
    source_event_id = str(event.get("id") or "hud-candidate")
    captured_at = event.get("captured_at") or _now_iso()
    approved = event.get("status") == "confirmed_by_provider"
    discarded = event.get("status") == "discarded"
    observation_id = _stable_uuid("hud_observation", encounter_id, source_event_id)
    observation_code, observation_display = _observation_identity(event)
    observation_value = _observation_value_fields(event)

    context = _compact(
        {
            "source_system": "rayban_pt",
            "source_type": "hud_encounter_state_machine",
            "source_event_id": source_event_id,
            "organization_id": organization_id,
            "subject_person_id": subject_person_id,
            "provider_person_id": provider_person_id,
            "encounter_id": encounter_id,
            "captured_at": captured_at,
            "capture_device": "rayban_meta_display",
            "operating_mode": "design/dry_run",
        }
    )

    observation_payload = _compact(
        {
            "id": observation_id,
            "fhir_id": f"rayban-hud-observation-{source_event_id}",
            "organization_id": organization_id,
            "encounter_id": encounter_id,
            "subject_person_id": subject_person_id,
            "created_by": provider_person_id,
            "performer_person_id": provider_person_id,
            "category": ["exam"],
            "code": observation_code,
            "code_display": observation_display,
            **observation_value,
            "interpretation": "abnormal",
            "laterality": event.get("side"),
            "source_type": "manual" if approved else "ai",
            "status": event.get("observation_status") or "preliminary",
            "effective_datetime": captured_at,
            "note": "Synthetic HUD candidate approved by provider." if approved else "Synthetic HUD candidate awaiting provider approval.",
            "measurement_context": {
                "test": event.get("test"),
                "symptom": event.get("symptom"),
                "hud_source": event.get("source"),
                "candidate_status": event.get("status"),
            },
        }
    )

    review_payload = _compact(
        {
            "id": _stable_uuid("hud_review", encounter_id, source_event_id),
            "organization_id": organization_id,
            "encounter_id": encounter_id,
            "subject_person_id": subject_person_id,
            "source_modality": "transcript",
            "source_table": "other",
            "source_record_id": source_event_id,
            "source_locator": {"source": "rayban_pt_hud_state_machine", "source_event_id": source_event_id},
            "proposed_payload": event,
            "final_payload": event if approved else None,
            "review_status": event.get("review_status") or "auto_extracted",
            "resolved_observation_id": observation_id if approved and not discarded else None,
            "review_note": (
                "Synthetic HUD approval rehearsal."
                if approved
                else "Synthetic HUD candidate discarded." if discarded else "Synthetic HUD candidate awaiting review."
            ),
            "reviewed_at": captured_at if approved or discarded else None,
            "reviewer_person_id": provider_person_id if approved or discarded else None,
        }
    )

    audit_payload = _compact(
        {
            "id": _stable_uuid("hud_audit", encounter_id, source_event_id),
            "organization_id": organization_id,
            "subject_person_id": subject_person_id,
            "actor_id": provider_person_id,
            "actor_type": "user" if provider_person_id else "system",
            "event_type": "create",
            "event_subtype": (
                "rayban_pt_hud_candidate_approved"
                if approved
                else "rayban_pt_hud_candidate_discarded" if discarded else "rayban_pt_hud_candidate_created"
            ),
            "resource_type": "rayban_pt_hud_candidate",
            "resource_id": source_event_id,
            "action_description": "HUD encounter candidate normalized for moai_web dry-run.",
            "severity": "info",
            "occurred_at": captured_at,
            "new_value": {
                "source_system": "rayban_pt",
                "capture_device": "rayban_meta_display",
                "candidate_status": event.get("status"),
            },
        }
    )

    return {
        "context": context,
        "validation": {
            "schema_version": fixture.get("schema_version"),
            "warnings": ["Synthetic HUD fixture; not real patient data."],
        },
        "observations": [] if discarded else [
            _item(
                "observations",
                observation_payload,
                required_fields=[
                    "organization_id",
                    "created_by",
                    "subject_person_id",
                    "category",
                    "code",
                    "fhir_id",
                    "source_type",
                    "status",
                    "value_type",
                ],
            )
        ],
        "reviews": [
            _item(
                "clinical_extraction_reviews",
                review_payload,
                required_fields=[
                    "organization_id",
                    "subject_person_id",
                    "source_modality",
                    "source_table",
                    "proposed_payload",
                ],
            )
        ],
        "audit_events": [
            _item(
                "clinical_events",
                audit_payload,
                required_fields=["actor_type", "event_type", "resource_type", "action_description", "severity"],
            )
        ],
    }


def build_hud_moai_bundle_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return build_hud_moai_bundle(
        {
            "schema_version": HUD_STATE_SCHEMA_VERSION,
            "mode": "design/dry_run",
            "safety": {
                "synthetic_non_phi": False,
                "writes_supabase": False,
                "training_started": False,
                "hud_contains_micro_cards_only": True,
            },
            "identity": {
                "organization_id": candidate.get("organization_id"),
                "subject_person_id": candidate.get("subject_person_id"),
                "provider_person_id": candidate.get("provider_person_id"),
                "encounter_id": candidate.get("encounter_id"),
            },
            "normalized_events": [
                {
                    "id": candidate.get("id"),
                    "encounter_id": candidate.get("encounter_id"),
                    "event_type": candidate.get("event_type"),
                    "test": candidate.get("test"),
                    "side": candidate.get("side"),
                    "value": candidate.get("value"),
                    "symptom": candidate.get("symptom"),
                    "source": candidate.get("source"),
                    "status": candidate.get("status"),
                    "review_status": candidate.get("review_status"),
                    "observation_status": candidate.get("observation_status"),
                    "captured_at": candidate.get("created_at"),
                }
            ],
        }
    )


def summarize_hud_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    pages = fixture.get("pages") or []
    event = (fixture.get("normalized_events") or [{}])[0]
    return {
        "schema_version": fixture.get("schema_version"),
        "mode": fixture.get("mode"),
        "page_count": len(pages),
        "states": [page.get("state") for page in pages],
        "micro_card_only": bool((fixture.get("safety") or {}).get("hud_contains_micro_cards_only")),
        "candidate": {
            "event_type": event.get("event_type"),
            "test": event.get("test"),
            "side": event.get("side"),
            "value": event.get("value"),
            "status": event.get("status"),
            "review_status": event.get("review_status"),
        },
    }
