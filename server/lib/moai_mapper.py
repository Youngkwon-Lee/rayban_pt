from __future__ import annotations

import uuid
from typing import Any, Optional


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _item(
    target_table: str,
    payload: dict[str, Any],
    *,
    missing_required_fields: Optional[list[str]] = None,
    warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    missing = missing_required_fields or []
    warn = warnings or []
    return {
        "target_table": target_table,
        "valid_for_upsert": not missing,
        "missing_required_fields": missing,
        "warnings": warn,
        "payload": payload,
    }


def _stable_uuid(*parts: object) -> str:
    key = ":".join(str(part or "") for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _event_value(event: dict[str, Any], key: str) -> Any:
    return event.get(key) if isinstance(event, dict) else None


def _visit_session_type(label: Optional[dict[str, Any]]) -> str:
    if not label:
        return "general"
    session_type = str(label.get("session_type") or "").strip()
    if session_type in {"general", "reassessment", "discharge"}:
        return session_type
    return "general"


def _activity_type(label: dict[str, Any]) -> str:
    session_type = str(label.get("session_type") or "").strip().lower()
    if "assessment" in session_type or "eval" in session_type:
        return "assessment"
    return "clinic_exercise"


def _media_ref_type(event_type: str) -> str:
    if event_type == "video":
        return "video_upload"
    if event_type == "audio":
        return "voice_memo"
    return "image_upload"


def _analysis_status(event_status: Any) -> str:
    status = str(event_status or "").strip().lower()
    if status in {"processed", "completed", "done"}:
        return "completed"
    if status in {"failed", "error"}:
        return "failed"
    if status in {"processing", "pending", "skipped"}:
        return status
    return "pending"


def _summary_text(event: dict[str, Any], soap: Optional[dict[str, Any]], label: Optional[dict[str, Any]]) -> str:
    if label and label.get("notes"):
        return str(label["notes"])
    if soap and soap.get("a"):
        return str(soap["a"])
    return str(_event_value(event, "raw_text") or "")


def build_moai_export_bundle(
    *,
    event: dict[str, Any],
    soap: Optional[dict[str, Any]] = None,
    label: Optional[dict[str, Any]] = None,
    review: Optional[dict[str, Any]] = None,
    artifacts: Optional[list[dict[str, Any]]] = None,
    subject_person_id: Optional[str] = None,
    provider_person_id: Optional[str] = None,
    encounter_id: Optional[str] = None,
    capture_device: str = "rayban",
) -> dict[str, Any]:
    artifacts = artifacts or []
    event_id = str(_event_value(event, "id") or "")
    event_type = str(_event_value(event, "event_type") or "")
    created_at = _event_value(event, "created_at")
    organization_id = _event_value(event, "owner_org_id")
    provider_id = provider_person_id or _event_value(event, "owner_provider_person_id")
    subject_id = subject_person_id or _event_value(event, "subject_person_id")
    canonical_encounter_id = encounter_id or _event_value(event, "physio_session_id") or event_id
    encounter_fhir_id = f"rayban-encounter-{event_id}"

    missing_context: list[str] = []
    warnings: list[str] = []
    if not organization_id:
        missing_context.append("organization_id")
    if not provider_id:
        missing_context.append("provider_person_id")
    if not subject_id:
        missing_context.append("subject_person_id")
    if canonical_encounter_id == event_id:
        warnings.append("encounter_id fell back to source event id")
    if _event_value(event, "patient_name"):
        warnings.append("patient_name remained an identity hint and was not resolved to person_id")

    context = _compact(
        {
            "source_system": "rayban_pt",
            "source_event_id": event_id,
            "organization_id": organization_id,
            "provider_person_id": provider_id,
            "subject_person_id": subject_id,
            "encounter_id": canonical_encounter_id,
            "captured_at": created_at,
            "capture_device": capture_device,
            "capture_mode": event_type,
            "identity_hints": _compact(
                {
                    "patient_name": _event_value(event, "patient_name"),
                    "physio_client_id": _event_value(event, "physio_client_id"),
                }
            ),
        }
    )

    encounter_payload = _compact(
        {
            "id": canonical_encounter_id,
            "fhir_id": encounter_fhir_id,
            "organization_id": organization_id,
            "provider_person_id": provider_id,
            "subject_person_id": subject_id,
            "class": "outpatient",
            "status": "in-progress",
            "session_type": _visit_session_type(label),
            "service_domain": "clinical",
            "source_system": "rayban_pt",
            "flow_mode": "simple",
            "care_setting": "home_visit",
            "period_start": created_at,
            "created_by": provider_id,
            "chief_complaint": None if soap else _event_value(event, "raw_text"),
        }
    )
    encounter_item = _item(
        "encounters",
        encounter_payload,
        missing_required_fields=[
            field
            for field in [
                "organization_id",
                "provider_person_id",
                "subject_person_id",
                "period_start",
                "created_by",
                "fhir_id",
            ]
            if encounter_payload.get(field) is None
        ],
        warnings=warnings.copy(),
    )

    media_items: list[dict[str, Any]] = []
    if event_type in {"image", "video"}:
        if artifacts:
            for artifact in artifacts:
                media_id = _stable_uuid("encounter_media", event_id, artifact.get("filename"), artifact.get("download_path"))
                media_payload = _compact(
                    {
                        "id": media_id,
                        "organization_id": organization_id,
                        "encounter_id": canonical_encounter_id,
                        "subject_person_id": subject_id,
                        "created_by": provider_id,
                        "media_type": "photo" if str(artifact.get("content_type") or "").startswith("image/") else event_type,
                        "media_subtype": artifact.get("kind"),
                        "content_type": artifact.get("content_type"),
                        "storage_bucket": "rayban-local-bridge",
                        "storage_path": artifact.get("download_path"),
                        "original_filename": artifact.get("filename"),
                        "captured_at": created_at,
                        "file_size_bytes": artifact.get("file_size_bytes"),
                        "analysis_status": _analysis_status(_event_value(event, "status")),
                        "metadata": {
                            "source_system": "rayban_pt",
                            "source_event_id": event_id,
                            "capture_device": capture_device,
                        },
                    }
                )
                media_items.append(
                    _item(
                        "encounter_media",
                        media_payload,
                        missing_required_fields=[
                            field
                            for field in [
                                "organization_id",
                                "subject_person_id",
                                "created_by",
                                "content_type",
                                "storage_path",
                            ]
                            if media_payload.get(field) is None
                        ],
                    )
                )
        else:
            media_items.append(
                _item(
                    "encounter_media",
                    _compact(
                        {
                            "id": _stable_uuid("encounter_media", event_id, event_type),
                            "organization_id": organization_id,
                            "encounter_id": canonical_encounter_id,
                            "subject_person_id": subject_id,
                            "created_by": provider_id,
                            "media_type": "photo" if event_type == "image" else event_type,
                            "content_type": "image/jpeg" if event_type == "image" else "video/mp4",
                            "storage_bucket": "rayban-local-bridge",
                            "storage_path": f"rayban_pt://events/{event_id}/{event_type}",
                            "captured_at": created_at,
                            "metadata": {
                                "source_system": "rayban_pt",
                                "source_event_id": event_id,
                                "capture_device": capture_device,
                            },
                        }
                    ),
                    missing_required_fields=[
                        field
                        for field in ["organization_id", "subject_person_id", "created_by"]
                        if not _compact(
                            {
                                "organization_id": organization_id,
                                "subject_person_id": subject_id,
                                "created_by": provider_id,
                            }
                        ).get(field)
                    ],
                    warnings=["logical storage_path placeholder used because no artifact record was found"],
                )
            )

    voice_items: list[dict[str, Any]] = []
    if event_type == "audio":
        voice_payload = _compact(
            {
                "id": _stable_uuid("voice_memo", event_id),
                "organization_id": organization_id,
                "encounter_id": canonical_encounter_id,
                "provider_person_id": provider_id,
                "subject_person_id": subject_id,
                "file_path": f"rayban_pt://events/{event_id}/audio",
                "recorded_at": created_at,
                "processing_status": "completed",
                "processed_at": created_at,
                "transcript_text": _event_value(event, "raw_text"),
            }
        )
        voice_items.append(
            _item(
                "voice_memos",
                voice_payload,
                missing_required_fields=[
                    field
                    for field in [
                        "organization_id",
                        "encounter_id",
                        "provider_person_id",
                        "subject_person_id",
                        "file_path",
                    ]
                    if voice_payload.get(field) is None
                ],
                warnings=["logical file_path placeholder used because raw audio path is not retained in event storage"],
            )
        )

    ai_items: list[dict[str, Any]] = []
    if soap:
        ai_items.append(
            _item(
                "ai_inference_log",
                _compact(
                    {
                        "id": _stable_uuid("ai_inference_log", event_id, "soap"),
                        "organization_id": organization_id,
                        "encounter_id": canonical_encounter_id,
                        "subject_person_id": subject_id,
                        "agent_type": "soap_draft",
                        "model_key": "bridge_generated",
                        "model_version": "local",
                        "prompt_version": "rayban_pt_local",
                        "output_type": "soap_note",
                        "review_status": "accepted" if review else "pending",
                        "target_resource_type": "encounter_note",
                        "target_resource_id": canonical_encounter_id,
                        "output_snapshot": soap,
                    }
                ),
                missing_required_fields=[
                    field
                    for field in [
                        "organization_id",
                        "agent_type",
                        "model_key",
                        "model_version",
                        "output_type",
                        "output_snapshot",
                    ]
                    if field not in _compact(
                        {
                            "organization_id": organization_id,
                            "agent_type": "soap_draft",
                            "model_key": "bridge_generated",
                            "model_version": "local",
                            "output_type": "soap_note",
                            "output_snapshot": soap,
                        }
                    )
                ],
            )
        )

    media_summary_items: list[dict[str, Any]] = []
    if event_type in {"image", "video"}:
        structured_findings = _compact(
            {
                "session_type": label.get("session_type") if label else None,
                "core_task": label.get("core_task") if label else None,
                "custom_task": label.get("custom_task") if label else None,
                "body_position": label.get("body_position") if label else None,
                "assist_level": label.get("assist_level") if label else None,
                "performance_level": label.get("performance_level") if label else None,
                "review_status": label.get("review_status") if label else None,
                "label_confidence": label.get("label_confidence") if label else None,
                "usable_for_training": label.get("usable_for_training") if label else None,
                "flags": label.get("flags") if label else None,
            }
        )
        media_summary_items.append(
            _item(
                "client_media_summaries",
                _compact(
                    {
                        "id": _stable_uuid("client_media_summary", event_id),
                        "organization_id": organization_id,
                        "encounter_id": canonical_encounter_id,
                        "subject_person_id": subject_id,
                        "author_person_id": provider_id,
                        "media_kind": event_type,
                        "media_ref_type": _media_ref_type(event_type),
                        "media_ref_id": _stable_uuid("client_media_ref", event_id, event_type),
                        "observed_at": created_at,
                        "summary_text": _summary_text(event, soap, label),
                        "structured_findings": structured_findings,
                        "metadata": {
                            "source_system": "rayban_pt",
                            "source_event_id": event_id,
                            "capture_device": capture_device,
                        },
                    }
                ),
                missing_required_fields=[
                    field
                    for field in ["organization_id", "subject_person_id", "media_kind", "media_ref_type", "summary_text"]
                    if field not in _compact(
                        {
                            "organization_id": organization_id,
                            "subject_person_id": subject_id,
                            "media_kind": event_type,
                            "media_ref_type": _media_ref_type(event_type),
                            "summary_text": _summary_text(event, soap, label),
                        }
                    )
                ],
            )
        )

    note_items: list[dict[str, Any]] = []
    if soap:
        note_items.append(
            _item(
                "encounter_notes",
                _compact(
                    {
                        "id": _stable_uuid("encounter_note", event_id),
                        "organization_id": organization_id,
                        "encounter_id": canonical_encounter_id,
                        "provider_person_id": provider_id,
                        "subject_person_id": subject_id,
                        "note_format": "soap",
                        "status": "draft",
                        "source_system": "rayban_pt",
                        "source_type": "rayban_pt_bridge",
                        "subjective": soap.get("s"),
                        "objective": soap.get("o"),
                        "assessment": soap.get("a"),
                        "plan": soap.get("p"),
                        "ai_draft_snapshot": {
                            "source_event_id": event_id,
                            "source_system": "rayban_pt",
                        },
                        "requires_approval": True,
                        "approval_status": "approved" if review else "pending",
                    }
                ),
                missing_required_fields=[
                    field
                    for field in [
                        "organization_id",
                        "encounter_id",
                        "provider_person_id",
                        "subject_person_id",
                        "note_format",
                    ]
                    if field not in _compact(
                        {
                            "organization_id": organization_id,
                            "encounter_id": canonical_encounter_id,
                            "provider_person_id": provider_id,
                            "subject_person_id": subject_id,
                            "note_format": "soap",
                        }
                    )
                ],
            )
        )

    observation_items: list[dict[str, Any]] = []
    if label:
        base_observation = {
            "organization_id": organization_id,
            "encounter_id": canonical_encounter_id,
            "subject_person_id": subject_id,
            "created_by": provider_id,
            "performer_person_id": provider_id,
            "source_type": "ai",
            "status": "final",
            "effective_datetime": label.get("updated_at") or created_at,
        }
        observation_defs = [
            (
                "assist_level",
                "Assist Level",
                "string",
                {"value_string": label.get("assist_level")},
                ["functional_status"],
            ),
            (
                "task_performance",
                "Task Performance",
                "string",
                {
                    "value_string": label.get("performance_level") or label.get("performance"),
                    "measurement_context": _compact(
                        {
                            "core_task": label.get("core_task"),
                            "custom_task": label.get("custom_task"),
                            "body_position": label.get("body_position"),
                            "review_status": label.get("review_status"),
                        }
                    ),
                },
                ["functional_status"],
            ),
            (
                "body_position",
                "Body Position",
                "string",
                {
                    "value_string": label.get("body_position"),
                    "measurement_context": _compact(
                        {
                            "core_task": label.get("core_task"),
                            "custom_task": label.get("custom_task"),
                        }
                    ),
                },
                ["functional_status"],
            ),
            (
                "session_flags",
                "Session Flags",
                "json",
                {
                    "value_json": _compact(
                        {
                            "flags": label.get("flags"),
                            "compensations": label.get("compensations"),
                            "fatigue_level": label.get("fatigue_level"),
                            "caregiver_present": label.get("caregiver_present"),
                        }
                    )
                },
                ["risk_flag"],
            ),
        ]
        for code, display, value_type, value_fields, categories in observation_defs:
            payload = _compact(
                {
                    "id": _stable_uuid("observation", event_id, code),
                    "fhir_id": f"rayban-observation-{event_id}-{code}",
                    **base_observation,
                    "category": categories,
                    "code": code,
                    "code_display": display,
                    "value_type": value_type,
                    **value_fields,
                    "note": label.get("notes"),
                }
            )
            observation_items.append(
                _item(
                    "observations",
                    payload,
                    missing_required_fields=[
                        field
                        for field in [
                            "organization_id",
                            "created_by",
                            "subject_person_id",
                            "category",
                            "code",
                            "fhir_id",
                            "source_type",
                            "status",
                            "value_type",
                        ]
                        if field not in payload
                    ],
                )
            )

    activity_items: list[dict[str, Any]] = []
    if label and (label.get("core_task") or label.get("notes")):
        activity_items.append(
            _item(
                "activity_sessions",
                _compact(
                    {
                        "id": _stable_uuid("activity_session", event_id),
                        "organization_id": organization_id,
                        "encounter_id": canonical_encounter_id,
                        "subject_person_id": subject_id,
                        "created_by": provider_id,
                        "activity_type": _activity_type(label),
                        "performed_at": label.get("updated_at") or created_at,
                        "source": "camera",
                        "status": "completed",
                        "notes": label.get("notes"),
                        "metrics": _compact(
                            {
                                "assist_level": label.get("assist_level"),
                                "performance_level": label.get("performance_level") or label.get("performance"),
                                "original_activity_type": label.get("custom_task") or label.get("core_task"),
                                "core_task": label.get("core_task"),
                                "custom_task": label.get("custom_task"),
                                "body_position": label.get("body_position"),
                                "review_status": label.get("review_status"),
                                "usable_for_training": label.get("usable_for_training"),
                                "label_confidence": label.get("label_confidence"),
                                "repetition_count": label.get("repetition_count"),
                                "hold_duration_seconds": label.get("hold_duration_seconds"),
                                "tolerance": label.get("tolerance"),
                                "fatigue_level": label.get("fatigue_level"),
                                "compensations": label.get("compensations"),
                                "caregiver_present": label.get("caregiver_present"),
                                "flags": label.get("flags"),
                            }
                        ),
                        "has_timeseries": False,
                    }
                ),
                missing_required_fields=[
                    field
                    for field in ["subject_person_id", "activity_type", "source", "status", "metrics"]
                    if field not in _compact(
                        {
                            "subject_person_id": subject_id,
                            "activity_type": _activity_type(label),
                            "source": "camera",
                            "status": "completed",
                            "metrics": _compact(
                                {
                                    "assist_level": label.get("assist_level"),
                                    "performance_level": label.get("performance_level") or label.get("performance"),
                                    "flags": label.get("flags"),
                                }
                            ),
                        }
                    )
                ],
            )
        )

    review_items: list[dict[str, Any]] = []
    if review:
        review_items.append(
            _item(
                "clinical_extraction_reviews",
                _compact(
                    {
                        "id": _stable_uuid("clinical_extraction_review", event_id),
                        "organization_id": organization_id,
                        "encounter_id": canonical_encounter_id,
                        "subject_person_id": subject_id,
                        "source_modality": event_type,
                        "source_table": "other",
                        "source_record_id": event_id,
                        "source_locator": {"source_event_id": event_id, "source_table": "rayban_pt.events"},
                        "proposed_payload": _compact({"soap": soap, "label": label}),
                        "final_payload": _compact({"soap": soap, "label": label}),
                        "review_status": "clinician_accepted",
                        "review_note": review.get("notes"),
                        "reviewed_at": review.get("reviewed_at"),
                        "reviewer_person_id": review.get("reviewer"),
                    }
                ),
                missing_required_fields=[
                    field
                    for field in [
                        "organization_id",
                        "subject_person_id",
                        "source_modality",
                        "source_table",
                        "proposed_payload",
                    ]
                    if field not in _compact(
                        {
                            "organization_id": organization_id,
                            "subject_person_id": subject_id,
                            "source_modality": event_type,
                            "source_table": "other",
                            "proposed_payload": _compact({"soap": soap, "label": label}),
                        }
                    )
                ],
            )
        )

    audit_items = [
        _item(
            "clinical_events",
            _compact(
                {
                    "id": _stable_uuid("clinical_event", event_id),
                    "organization_id": organization_id,
                    "subject_person_id": subject_id,
                    "actor_id": provider_id,
                    "actor_type": "user" if provider_id else "system",
                    "event_type": "create",
                    "event_subtype": f"rayban_pt_{event_type}",
                    "resource_type": "rayban_pt_event",
                    "resource_id": event_id,
                    "action_description": f"Bridge event normalized for moai_web export ({event_type}).",
                    "severity": "info",
                    "occurred_at": created_at,
                    "new_value": {
                        "source_event_id": event_id,
                        "source_system": "rayban_pt",
                        "capture_device": capture_device,
                    },
                }
            ),
            missing_required_fields=[
                field
                for field in ["actor_type", "event_type", "resource_type", "action_description", "severity"]
                if field not in _compact(
                    {
                        "actor_type": "user" if provider_id else "system",
                        "event_type": "create",
                        "resource_type": "rayban_pt_event",
                        "action_description": f"Bridge event normalized for moai_web export ({event_type}).",
                        "severity": "info",
                    }
                )
            ],
        )
    ]

    return {
        "context": context,
        "validation": {
            "missing_context_fields": missing_context,
            "warnings": warnings,
        },
        "encounter": encounter_item,
        "media": media_items,
        "voice_memos": voice_items,
        "ai_inference_logs": ai_items,
        "media_summaries": media_summary_items,
        "notes": note_items,
        "observations": observation_items,
        "activity_sessions": activity_items,
        "reviews": review_items,
        "audit_events": audit_items,
    }
