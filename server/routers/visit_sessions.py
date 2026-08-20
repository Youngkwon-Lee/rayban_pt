"""Visit session orchestration and capture-event endpoints."""

import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from datetime import datetime
from lib.transcript_capture import TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION, capture_action_type, extract_transcript_capture_candidates
from lib.visit_session import PROVIDER_ROLES, attach_visit_event, create_visit_session, end_visit_session, get_visit_session, set_visit_recording, update_visit_phase
from pydantic import BaseModel, Field
from typing import Optional

from bridge_core import (
    CAPTURE_EVENT_SOURCE_TYPES,
    CAPTURE_EVENT_STATUSES,
    _apply_visit_session_hud,
    _apply_visit_sync_pending_hud,
    _attach_pre_review_to_session,
    _audit_log,
    _build_visit_session_write_plan,
    _capture_event_from_row,
    _capture_event_select,
    _capture_origin_from_source,
    _clean_scope_value,
    _conn,
    _create_transcript_capture_events,
    _enqueue_visit_session_sync_job,
    _error,
    _refresh_visit_progress_note_from_events,
    _scope_from_request,
    _short_lens_text,
)

router = APIRouter()


class VisitSessionStartRequest(BaseModel):
    organization_id: str
    provider_person_id: str
    provider_role: str = "unspecified"
    subject_person_id: str
    encounter_id: Optional[str] = None
    patient_alias: str = "Patient"
    history_summary: str = ""
    update_glass: bool = True


class VisitSessionPhaseRequest(BaseModel):
    phase: str
    cue: Optional[str] = None
    update_glass: bool = True


class VisitSessionRecordingRequest(BaseModel):
    is_recording: bool
    update_glass: bool = True


class VisitSessionEventRequest(BaseModel):
    event_id: str
    role: Optional[str] = None
    phase: Optional[str] = None
    update_glass: bool = True


class CaptureEventPayload(BaseModel):
    visit_session_id: Optional[str] = None
    encounter_id: Optional[str] = None
    organization_id: Optional[str] = None
    provider_person_id: Optional[str] = None
    subject_person_id: Optional[str] = None
    source_media_id: Optional[str] = None
    source_event_id: Optional[str] = None
    source_type: str = "therapist_tag"
    event_type: str
    candidate_type: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    confidence: Optional[float] = None
    status: str = "draft"
    payload: dict = Field(default_factory=dict)
    reviewed_by: Optional[str] = None


class CaptureEventUpdatePayload(BaseModel):
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    confidence: Optional[float] = None
    status: Optional[str] = None
    payload: Optional[dict] = None
    reviewed_by: Optional[str] = None


class CaptureEventExtractPayload(BaseModel):
    visit_session_id: Optional[str] = None
    encounter_id: Optional[str] = None
    organization_id: Optional[str] = None
    provider_person_id: Optional[str] = None
    subject_person_id: Optional[str] = None
    source_event_id: Optional[str] = None
    source_media_id: Optional[str] = None
    text: str
    source_type: str = "transcript"
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    confidence: Optional[float] = None
    capture_origin: Optional[str] = None
    create_events: bool = True


@router.post("/visit-sessions/start")
def visit_session_start(payload: VisitSessionStartRequest):
    provider_role = payload.provider_role.strip()
    if provider_role not in PROVIDER_ROLES:
        _error(400, "INVALID_PROVIDER_ROLE", f"provider_role must be one of: {', '.join(sorted(PROVIDER_ROLES))}")
    with _conn() as conn:
        session = create_visit_session(
            conn,
            organization_id=payload.organization_id.strip(),
            provider_person_id=payload.provider_person_id.strip(),
            provider_role=provider_role,
            subject_person_id=payload.subject_person_id.strip(),
            encounter_id=(payload.encounter_id or "").strip() or None,
            patient_alias=payload.patient_alias,
            history_summary=payload.history_summary,
        )
        session, pre_review = _attach_pre_review_to_session(conn, session)
        conn.commit()
    hud = _apply_visit_session_hud(session, insight=pre_review) if payload.update_glass else None
    _audit_log(None, "info", f"visit session started id={session['id']}")
    return {"status": "started", "session": session, "glass_state": hud}


@router.get("/visit-sessions/{session_id}")
def visit_session_get(session_id: str):
    with _conn() as conn:
        session = get_visit_session(conn, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="visit session not found")
    return {"status": "done", "session": session}


@router.post("/visit-sessions/{session_id}/phase")
def visit_session_set_phase(session_id: str, payload: VisitSessionPhaseRequest):
    try:
        with _conn() as conn:
            session = update_visit_phase(conn, session_id, payload.phase, payload.cue)
            conn.commit()
    except ValueError as exc:
        _error(400, "INVALID_VISIT_PHASE", str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="visit session not found")
    hud = _apply_visit_session_hud(session) if payload.update_glass else None
    _audit_log(None, "info", f"visit session phase id={session_id} phase={session['phase']}")
    return {"status": "updated", "session": session, "glass_state": hud}


@router.post("/visit-sessions/{session_id}/recording")
def visit_session_set_recording(session_id: str, payload: VisitSessionRecordingRequest):
    try:
        with _conn() as conn:
            session = set_visit_recording(conn, session_id, payload.is_recording)
            conn.commit()
    except KeyError:
        raise HTTPException(status_code=404, detail="visit session not found")
    hud = _apply_visit_session_hud(session) if payload.update_glass else None
    _audit_log(None, "info", f"visit session recording id={session_id} recording={payload.is_recording}")
    return {"status": "updated", "session": session, "glass_state": hud}


@router.post("/visit-sessions/{session_id}/events")
def visit_session_attach_event(session_id: str, payload: VisitSessionEventRequest):
    with _conn() as conn:
        event_exists = conn.execute("SELECT id FROM events WHERE id = ?", (payload.event_id,)).fetchone()
        if not event_exists:
            raise HTTPException(status_code=404, detail="event not found")
        try:
            session = attach_visit_event(conn, session_id, payload.event_id, role=payload.role, phase=payload.phase)
        except KeyError:
            raise HTTPException(status_code=404, detail="visit session not found")
        conn.commit()
    hud = _apply_visit_session_hud(session) if payload.update_glass else None
    _audit_log(payload.event_id, "info", f"event attached to visit session id={session_id}")
    return {"status": "attached", "session": session, "glass_state": hud}


@router.post("/capture-events")
def capture_event_create(payload: CaptureEventPayload, request: Request):
    source_type = payload.source_type.strip().lower()
    event_type = payload.event_type.strip()
    candidate_type = (payload.candidate_type or event_type).strip()
    status = payload.status.strip().lower()
    if source_type not in CAPTURE_EVENT_SOURCE_TYPES:
        _error(400, "INVALID_CAPTURE_SOURCE", f"source_type must be one of: {', '.join(sorted(CAPTURE_EVENT_SOURCE_TYPES))}")
    if not event_type or not candidate_type:
        _error(400, "INVALID_CAPTURE_EVENT", "event_type and candidate_type are required")
    if status not in CAPTURE_EVENT_STATUSES:
        _error(400, "INVALID_CAPTURE_STATUS", f"status must be one of: {', '.join(sorted(CAPTURE_EVENT_STATUSES))}")
    if source_type == "human_label":
        # Mapping spec §C.2: human labels are never machine drafts, and
        # approval always names a reviewer.
        if status == "draft":
            _error(400, "INVALID_HUMAN_LABEL_STATUS", "human_label events must be status edited, approved, or rejected")
        if status == "approved" and not (payload.reviewed_by or "").strip():
            _error(400, "HUMAN_LABEL_REVIEWER_REQUIRED", "approved human_label events require reviewed_by")
        clip_payload = payload.payload or {}
        if not str(clip_payload.get("clip_id") or "").strip() or not str(clip_payload.get("label_schema") or "").strip():
            _error(400, "INVALID_HUMAN_LABEL_PAYLOAD", "human_label events require payload.clip_id and payload.label_schema")
    if payload.start_ms is not None and payload.start_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "start_ms must be non-negative")
    if payload.end_ms is not None and payload.end_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be non-negative")
    if payload.start_ms is not None and payload.end_ms is not None and payload.end_ms < payload.start_ms:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be greater than or equal to start_ms")
    if payload.confidence is not None and not 0 <= payload.confidence <= 1:
        _error(400, "INVALID_CAPTURE_CONFIDENCE", "confidence must be between 0 and 1")

    with _conn() as conn:
        session = None
        if payload.visit_session_id:
            session = get_visit_session(conn, payload.visit_session_id.strip())
            if not session:
                raise HTTPException(status_code=404, detail="visit session not found")

        organization_id, provider_person_id = _scope_from_request(
            request,
            owner_org_id=payload.organization_id or (session or {}).get("organization_id"),
            owner_provider_person_id=payload.provider_person_id or (session or {}).get("provider_person_id"),
        )
        organization_id = organization_id or _clean_scope_value(payload.organization_id)
        provider_person_id = provider_person_id or _clean_scope_value(payload.provider_person_id)
        encounter_id = _clean_scope_value(payload.encounter_id) or (session or {}).get("encounter_id")
        subject_person_id = _clean_scope_value(payload.subject_person_id) or (session or {}).get("subject_person_id")
        visit_session_id = _clean_scope_value(payload.visit_session_id)
        source_media_id = _clean_scope_value(payload.source_media_id)
        source_event_id = _clean_scope_value(payload.source_event_id)
        reviewed_by = _clean_scope_value(payload.reviewed_by)
        reviewed_at = datetime.utcnow().isoformat() if reviewed_by and status != "draft" else None
        event_payload = dict(payload.payload or {})
        event_payload.setdefault("action_type", capture_action_type(candidate_type))
        if session and session.get("provider_role"):
            event_payload.setdefault("provider_role", session["provider_role"])
        event_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO capture_events (
                id, visit_session_id, encounter_id, organization_id, provider_person_id,
                subject_person_id, source_media_id, source_event_id, source_type, event_type,
                candidate_type, start_ms, end_ms, confidence, status, payload_json,
                reviewed_by, reviewed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                visit_session_id,
                encounter_id,
                organization_id,
                provider_person_id,
                subject_person_id,
                source_media_id,
                source_event_id,
                source_type,
                event_type,
                candidate_type,
                payload.start_ms,
                payload.end_ms,
                payload.confidence,
                status,
                json.dumps(event_payload, ensure_ascii=False, separators=(",", ":")),
                reviewed_by,
                reviewed_at,
                now,
                now,
            ),
        )
        row = conn.execute(f"{_capture_event_select()} WHERE id = ?", (event_id,)).fetchone()
        conn.commit()
    event = _capture_event_from_row(row)
    _audit_log(None, "info", f"capture event created id={event_id} type={event_type} source_event={source_event_id or '-'}")
    return {"status": "created", "event": event}


@router.post("/capture-events/extract")
def capture_event_extract(payload: CaptureEventExtractPayload, request: Request):
    """Create review-first capture evidence from explicit therapist language."""

    text = payload.text.strip()
    if not text:
        _error(422, "CAPTURE_TRANSCRIPT_REQUIRED", "text는 비어 있을 수 없습니다.")
    if payload.start_ms is not None and payload.start_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "start_ms must be non-negative")
    if payload.end_ms is not None and payload.end_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be non-negative")
    if payload.start_ms is not None and payload.end_ms is not None and payload.end_ms < payload.start_ms:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be greater than or equal to start_ms")
    if payload.confidence is not None and not 0 <= payload.confidence <= 1:
        _error(400, "INVALID_CAPTURE_CONFIDENCE", "confidence must be between 0 and 1")

    with _conn() as conn:
        session = None
        if payload.visit_session_id:
            session = get_visit_session(conn, payload.visit_session_id.strip())
            if not session:
                raise HTTPException(status_code=404, detail="visit session not found")

        organization_id, provider_person_id = _scope_from_request(
            request,
            owner_org_id=payload.organization_id or (session or {}).get("organization_id"),
            owner_provider_person_id=payload.provider_person_id or (session or {}).get("provider_person_id"),
        )
        organization_id = organization_id or _clean_scope_value(payload.organization_id)
        provider_person_id = provider_person_id or _clean_scope_value(payload.provider_person_id)
        encounter_id = _clean_scope_value(payload.encounter_id) or (session or {}).get("encounter_id")
        subject_person_id = _clean_scope_value(payload.subject_person_id) or (session or {}).get("subject_person_id")
        visit_session_id = _clean_scope_value(payload.visit_session_id)

        if not encounter_id and not visit_session_id:
            _error(422, "CAPTURE_SCOPE_REQUIRED", "encounter_id or visit_session_id is required")

        candidates = extract_transcript_capture_candidates(
            text,
            provider_role=(session or {}).get("provider_role"),
        )
        if not payload.create_events:
            return {
                "status": "preview" if candidates else "no_candidates",
                "extractor_version": TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION,
                "source_text": _short_lens_text(text, limit=2_000),
                "candidates": candidates,
            }

        events = _create_transcript_capture_events(
            conn,
            text=text,
            visit_session_id=visit_session_id,
            encounter_id=encounter_id,
            organization_id=organization_id,
            provider_person_id=provider_person_id,
            provider_role=(session or {}).get("provider_role"),
            subject_person_id=subject_person_id,
            source_event_id=payload.source_event_id,
            source_media_id=payload.source_media_id,
            start_ms=payload.start_ms,
            end_ms=payload.end_ms,
            confidence=payload.confidence,
            capture_origin=_capture_origin_from_source(payload.capture_origin),
            derived_from=payload.source_type.strip().lower() or "transcript",
        )
        conn.commit()

    # source_event_id may refer to an upstream media event that is not present
    # in this bridge's local `events` table, so it must not be used as the
    # audit_logs foreign key without a local existence check.
    audit_event_id = None
    if payload.source_event_id:
        with _conn() as audit_conn:
            if audit_conn.execute("SELECT 1 FROM events WHERE id = ?", (payload.source_event_id,)).fetchone():
                audit_event_id = payload.source_event_id
    _audit_log(
        audit_event_id,
        "info",
        f"transcript capture extraction candidates={len(events)} source_event={payload.source_event_id or '-'}",
    )
    return {
        "status": "created" if events else "no_candidates",
        "extractor_version": TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION,
        "source_event_id": payload.source_event_id,
        "events": events,
    }


@router.get("/visit-sessions/{session_id}/capture-events")
def capture_event_list_by_session(session_id: str, limit: int = 100):
    if limit < 1 or limit > 500:
        _error(400, "INVALID_CAPTURE_LIMIT", "limit must be between 1 and 500")
    with _conn() as conn:
        session = get_visit_session(conn, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="visit session not found")
        rows = conn.execute(
            f"{_capture_event_select()} WHERE visit_session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return {"items": [_capture_event_from_row(row) for row in rows]}


@router.get("/capture-events")
def capture_event_list(
    request: Request,
    encounter_id: Optional[str] = None,
    visit_session_id: Optional[str] = None,
    limit: int = 100,
):
    if not _clean_scope_value(encounter_id) and not _clean_scope_value(visit_session_id):
        _error(400, "CAPTURE_SCOPE_REQUIRED", "encounter_id or visit_session_id is required")
    if limit < 1 or limit > 500:
        _error(400, "INVALID_CAPTURE_LIMIT", "limit must be between 1 and 500")
    filters: list[str] = []
    values: list[object] = []
    scoped_org_id, scoped_provider_person_id = _scope_from_request(request)
    if scoped_org_id:
        filters.append("organization_id = ?")
        values.append(scoped_org_id)
    if scoped_provider_person_id:
        filters.append("provider_person_id = ?")
        values.append(scoped_provider_person_id)
    if encounter_id and encounter_id.strip():
        filters.append("encounter_id = ?")
        values.append(encounter_id.strip())
    if visit_session_id and visit_session_id.strip():
        filters.append("visit_session_id = ?")
        values.append(visit_session_id.strip())
    with _conn() as conn:
        rows = conn.execute(
            f"{_capture_event_select()} WHERE {' AND '.join(filters)} ORDER BY created_at ASC LIMIT ?",
            (*values, limit),
        ).fetchall()
    return {"items": [_capture_event_from_row(row) for row in rows]}


@router.patch("/capture-events/{event_id}")
def capture_event_update(event_id: str, payload: CaptureEventUpdatePayload, request: Request):
    status = payload.status.strip().lower() if payload.status is not None else None
    if status is not None and status not in CAPTURE_EVENT_STATUSES:
        _error(400, "INVALID_CAPTURE_STATUS", f"status must be one of: {', '.join(sorted(CAPTURE_EVENT_STATUSES))}")
    if payload.start_ms is not None and payload.start_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "start_ms must be non-negative")
    if payload.end_ms is not None and payload.end_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be non-negative")
    if payload.start_ms is not None and payload.end_ms is not None and payload.end_ms < payload.start_ms:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be greater than or equal to start_ms")
    if payload.confidence is not None and not 0 <= payload.confidence <= 1:
        _error(400, "INVALID_CAPTURE_CONFIDENCE", "confidence must be between 0 and 1")

    with _conn() as conn:
        existing = conn.execute(f"{_capture_event_select()} WHERE id = ?", (event_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="capture event not found")
        existing_event = _capture_event_from_row(existing)
        scoped_org_id, scoped_provider_person_id = _scope_from_request(request)
        if scoped_org_id and existing_event["organization_id"] and scoped_org_id != existing_event["organization_id"]:
            raise HTTPException(status_code=403, detail="capture event organization scope mismatch")
        if (
            scoped_provider_person_id
            and existing_event["provider_person_id"]
            and scoped_provider_person_id != existing_event["provider_person_id"]
        ):
            raise HTTPException(status_code=403, detail="capture event provider scope mismatch")
        next_start_ms = payload.start_ms if payload.start_ms is not None else existing_event["start_ms"]
        next_end_ms = payload.end_ms if payload.end_ms is not None else existing_event["end_ms"]
        if next_start_ms is not None and next_end_ms is not None and next_end_ms < next_start_ms:
            _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be greater than or equal to start_ms")
        next_status = status or existing_event["status"]
        reviewed_by = _clean_scope_value(payload.reviewed_by) or existing_event["reviewed_by"]
        reviewed_at = existing_event["reviewed_at"]
        if next_status != "draft" and reviewed_by:
            reviewed_at = reviewed_at or datetime.utcnow().isoformat()
        next_payload = payload.payload if payload.payload is not None else existing_event["payload"]
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE capture_events
            SET start_ms = ?, end_ms = ?, confidence = ?, status = ?, payload_json = ?,
                reviewed_by = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_start_ms,
                next_end_ms,
                payload.confidence if payload.confidence is not None else existing_event["confidence"],
                next_status,
                json.dumps(next_payload or {}, ensure_ascii=False, separators=(",", ":")),
                reviewed_by,
                reviewed_at,
                now,
                event_id,
            ),
        )
        row = conn.execute(f"{_capture_event_select()} WHERE id = ?", (event_id,)).fetchone()
        conn.commit()
    event = _capture_event_from_row(row)
    _audit_log(None, "info", f"capture event updated id={event_id} status={event['status']}")
    return {"status": "updated", "event": event}


@router.post("/visit-sessions/{session_id}/end")
def visit_session_end(session_id: str, update_glass: bool = True):
    try:
        with _conn() as conn:
            session = end_visit_session(conn, session_id)
            session = _refresh_visit_progress_note_from_events(conn, session)
            plan = _build_visit_session_write_plan(session)
            sync_job = _enqueue_visit_session_sync_job(conn, session, plan)
            conn.commit()
    except KeyError:
        raise HTTPException(status_code=404, detail="visit session not found")
    hud = _apply_visit_sync_pending_hud(session, sync_job) if update_glass else None
    _audit_log(None, "info", f"visit session ended id={session_id}")
    return {"status": "ended", "session": session, "glass_state": hud, "moai_write_plan": plan, "moai_sync_job": sync_job}
