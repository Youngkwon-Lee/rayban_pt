from __future__ import annotations

from typing import Any


PHYSIO_ALLOWED_VALUES: dict[str, dict[str, set[str]]] = {
    "activity_sessions": {
        "activity_type": {
            "home_exercise",
            "clinic_exercise",
            "gym_training",
            "competition",
            "assessment",
            "daily_walk",
            "telehealth",
            "other",
        },
        "source": {"manual", "apple_health", "samsung_health", "garmin", "imu", "camera", "app_guided"},
        "status": {"planned", "in_progress", "completed", "cancelled", "skipped"},
    },
    "ai_inference_log": {
        "review_status": {"pending", "accepted", "modified", "rejected"},
    },
    "assessment_form_responses": {
        "source_type": {"clinical", "patient_self", "intake_form", "import", "system"},
        "mcid_status": {"achieved", "missed", "stable", "no_baseline", "no_rule"},
    },
    "care_plans": {
        "category": {"medical", "wellness", "fitness", "rehabilitation"},
        "intent": {"proposal", "plan", "order", "option"},
        "status": {"draft", "active", "on-hold", "revoked", "completed", "entered-in-error", "unknown"},
    },
    "client_media_summaries": {
        "media_kind": {"image", "video", "audio", "document"},
        "media_ref_type": {"encounter_file", "voice_memo", "image_upload", "video_upload"},
        "body_region": {"lumbar", "cervical", "shoulder", "knee", "ankle", "hip"},
    },
    "clinical_events": {
        "severity": {"debug", "info", "warning", "error", "critical"},
    },
    "clinical_extraction_reviews": {
        "source_modality": {"transcript", "audio", "image", "video", "text", "mixed", "other"},
        "source_table": {
            "voice_memos",
            "encounter_media",
            "encounter_notes",
            "client_media_summaries",
            "client_memory_chunks",
            "observations",
            "other",
        },
        "review_status": {"auto_extracted", "clinician_accepted", "clinician_corrected", "validated", "rejected"},
    },
    "encounter_media": {
        "media_type": {"photo", "video", "audio", "document", "attachment"},
        "analysis_status": {"pending", "processing", "completed", "failed", "skipped"},
        "doc_status": {"current", "superseded", "entered-in-error"},
        "laterality": {"left", "right", "bilateral"},
    },
    "encounter_notes": {
        "note_format": {"soap", "dap", "progress", "free", "training_log", "coaching_note", "wellness_note"},
        "status": {"draft", "final", "amended", "entered_in_error"},
        "approval_status": {"none", "pending", "approved", "rejected"},
        "review_action": {"accepted", "modified", "rejected"},
    },
    "encounters": {
        "class": {
            "AMB",
            "IMP",
            "EMER",
            "HH",
            "wellness",
            "training",
            "coaching",
            "home-based",
            "outpatient",
            "inpatient",
            "emergency",
        },
        "status": {"planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled"},
        "session_type": {"general", "reassessment", "discharge"},
        "care_setting": {"outpatient", "home_visit", "inpatient", "telehealth", "field_side", "facility"},
        "finish_reason": {
            "clinical",
            "non_clinical",
            "no_show",
            "admin_close",
            "wellness",
            "phone_consult",
            "education_only",
        },
        "flow_mode": {"full", "simple"},
        "service_domain": {"clinical", "fitness", "wellness"},
    },
    "observations": {
        "interpretation": {"normal", "abnormal", "high", "low", "critical", "improved", "worsened", "unchanged"},
        "laterality": {"left", "right", "bilateral"},
        "source_type": {"manual", "device", "patient_report", "ai", "import", "form"},
        "status": {"registered", "preliminary", "final", "amended", "cancelled", "entered-in-error"},
        "value_type": {"quantity", "string", "boolean", "integer", "range", "ratio", "codeable_concept", "json"},
    },
    "org_clients": {
        "status": {"active", "inactive", "referred", "discharged", "archived"},
    },
    "voice_memos": {
        "processing_status": {"pending", "processing", "completed", "failed"},
    },
}


def validate_physio_payload_constraints(target_table: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    allowed_by_field = PHYSIO_ALLOWED_VALUES.get(target_table, {})
    violations: list[dict[str, str]] = []
    for field, allowed in allowed_by_field.items():
        value = payload.get(field)
        if value is None or value in allowed:
            continue
        violations.append(
            {
                "field": field,
                "value": str(value),
                "allowed": ", ".join(sorted(allowed)),
            }
        )
    return violations
