"""HUD candidate capture, review, and approval endpoints."""

import json
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional

from bridge_core import (
    _audit_log,
    _conn,
    _error,
    _extract_hud_candidate_from_transcript,
    _get_hud_candidate,
    _hud_candidate_from_row,
    _hud_candidate_plan,
    _scope_from_request,
    _set_active_hud_candidate,
    _short_lens_text,
)

router = APIRouter()


class HudCandidatePayload(BaseModel):
    encounter_id: str
    organization_id: Optional[str] = None
    subject_person_id: Optional[str] = None
    provider_person_id: Optional[str] = None
    event_type: str = "test_result"
    test: str = ""
    side: str = ""
    value: str = ""
    symptom: str = ""
    source: str = "rayban_meta_display"
    source_text: str = ""
    confidence: Optional[float] = None
    payload: dict = Field(default_factory=dict)


class HudCandidateExtractPayload(BaseModel):
    encounter_id: str
    organization_id: Optional[str] = None
    subject_person_id: Optional[str] = None
    provider_person_id: Optional[str] = None
    text: str
    source: str = "stt_transcript"
    confidence: Optional[float] = None
    create_candidate: bool = True


class HudCandidateDecisionPayload(BaseModel):
    reviewer_person_id: Optional[str] = None
    reason: Optional[str] = None


@router.post("/hud/candidates")
def create_hud_candidate(payload: HudCandidatePayload, request: Request):
    org_id, provider_from_scope = _scope_from_request(
        request,
        owner_org_id=payload.organization_id,
        owner_provider_person_id=payload.provider_person_id,
    )
    encounter_id = payload.encounter_id.strip()
    subject_person_id = (payload.subject_person_id or "").strip()
    provider_person_id = (payload.provider_person_id or provider_from_scope or "").strip()
    organization_id = (payload.organization_id or org_id or "").strip()
    event_type = payload.event_type.strip() or "test_result"
    if payload.confidence is not None and not (0 <= payload.confidence <= 1):
        _error(422, "INVALID_HUD_CONFIDENCE", "confidence는 0과 1 사이여야 합니다.")
    missing = [
        name
        for name, value in [
            ("encounter_id", encounter_id),
            ("organization_id", organization_id),
            ("subject_person_id", subject_person_id),
            ("provider_person_id", provider_person_id),
            ("event_type", event_type),
        ]
        if not value
    ]
    if missing:
        _error(422, "HUD_CANDIDATE_CONTEXT_REQUIRED", f"missing required fields: {', '.join(missing)}")

    candidate_id = str(uuid.uuid4())
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO hud_candidates (
              id, encounter_id, organization_id, subject_person_id, provider_person_id,
              event_type, test, side, value, symptom, source, status, review_status,
              confidence, source_text, payload_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', 'auto_extracted', ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                candidate_id,
                encounter_id,
                organization_id,
                subject_person_id,
                provider_person_id,
                event_type,
                payload.test.strip(),
                payload.side.strip().lower(),
                payload.value.strip(),
                payload.symptom.strip(),
                payload.source.strip() or "rayban_meta_display",
                payload.confidence,
                payload.source_text.strip(),
                json.dumps(payload.payload, ensure_ascii=False),
            ),
        )
        conn.commit()
        candidate = _get_hud_candidate(conn, candidate_id)
    _set_active_hud_candidate(candidate)
    _audit_log(None, "info", f"HUD candidate created id={candidate_id} encounter={encounter_id}")
    return {"status": "done", "candidate": candidate, "plan": {"summary": _hud_candidate_plan(candidate)["summary"]}}


@router.post("/hud/candidates/extract")
def extract_hud_candidate(payload: HudCandidateExtractPayload, request: Request):
    org_id, provider_from_scope = _scope_from_request(
        request,
        owner_org_id=payload.organization_id,
        owner_provider_person_id=payload.provider_person_id,
    )
    organization_id = (payload.organization_id or org_id or "").strip()
    provider_person_id = (payload.provider_person_id or provider_from_scope or "").strip()
    subject_person_id = (payload.subject_person_id or "").strip()
    encounter_id = payload.encounter_id.strip()
    if payload.confidence is not None and not (0 <= payload.confidence <= 1):
        _error(422, "INVALID_HUD_CONFIDENCE", "confidence는 0과 1 사이여야 합니다.")
    extracted = _extract_hud_candidate_from_transcript(payload.text)
    if not extracted:
        return {
            "status": "no_candidate",
            "reason": "unsupported_transcript",
            "source_text": _short_lens_text(payload.text, limit=120),
        }
    candidate_preview = {
        **extracted,
        "encounter_id": encounter_id,
        "organization_id": organization_id,
        "subject_person_id": subject_person_id,
        "provider_person_id": provider_person_id,
        "source": payload.source.strip() or "stt_transcript",
        "confidence": payload.confidence,
        "status": "candidate",
        "review_status": "auto_extracted",
    }
    if not payload.create_candidate:
        return {"status": "preview", "candidate": candidate_preview}
    missing = [
        name
        for name, value in [
            ("encounter_id", encounter_id),
            ("organization_id", organization_id),
            ("subject_person_id", subject_person_id),
            ("provider_person_id", provider_person_id),
        ]
        if not value
    ]
    if missing:
        _error(422, "HUD_CANDIDATE_CONTEXT_REQUIRED", f"missing required fields: {', '.join(missing)}")

    candidate_id = str(uuid.uuid4())
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO hud_candidates (
              id, encounter_id, organization_id, subject_person_id, provider_person_id,
              event_type, test, side, value, symptom, source, status, review_status,
              confidence, source_text, payload_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', 'auto_extracted', ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                candidate_id,
                encounter_id,
                organization_id,
                subject_person_id,
                provider_person_id,
                candidate_preview["event_type"],
                candidate_preview["test"],
                candidate_preview["side"],
                candidate_preview["value"],
                candidate_preview["symptom"],
                candidate_preview["source"],
                payload.confidence,
                candidate_preview["source_text"],
                json.dumps(
                    {
                        "extractor": "rayban_pt_rule_parser_v0",
                        "source_text": candidate_preview["source_text"],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
        candidate = _get_hud_candidate(conn, candidate_id)
    _set_active_hud_candidate(candidate)
    _audit_log(None, "info", f"HUD candidate extracted id={candidate_id} encounter={encounter_id}")
    return {"status": "done", "candidate": candidate, "plan": {"summary": _hud_candidate_plan(candidate)["summary"]}}


@router.get("/hud/candidates")
def list_hud_candidates(encounter_id: str = "", status: str = "all", limit: int = 20):
    n = max(1, min(limit, 100))
    clauses: list[str] = []
    params: list = []
    if encounter_id.strip():
        clauses.append("encounter_id = ?")
        params.append(encounter_id.strip())
    if status.strip() and status != "all":
        clauses.append("status = ?")
        params.append(status.strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(n)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, encounter_id, organization_id, subject_person_id, provider_person_id,
                   event_type, test, side, value, symptom, source, status, review_status,
                   confidence, source_text, payload_json, reviewer_person_id,
                   discarded_reason, reviewed_at, created_at, updated_at
            FROM hud_candidates
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {"status": "done", "count": len(rows), "items": [_hud_candidate_from_row(row) for row in rows]}


@router.get("/hud/candidates/{candidate_id}")
def get_hud_candidate(candidate_id: str):
    with _conn() as conn:
        candidate = _get_hud_candidate(conn, candidate_id)
    return {"status": "done", "candidate": candidate}


@router.post("/hud/candidates/{candidate_id}/approve")
def approve_hud_candidate(candidate_id: str, payload: HudCandidateDecisionPayload):
    with _conn() as conn:
        candidate = _get_hud_candidate(conn, candidate_id)
        if candidate["status"] == "discarded":
            _error(409, "HUD_CANDIDATE_DISCARDED", "discarded candidate cannot be approved")
        reviewer = (payload.reviewer_person_id or candidate.get("provider_person_id") or "").strip()
        conn.execute(
            """
            UPDATE hud_candidates
            SET status='confirmed_by_provider',
                review_status='clinician_accepted',
                reviewer_person_id=?,
                reviewed_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (reviewer, candidate_id),
        )
        conn.commit()
        candidate = _get_hud_candidate(conn, candidate_id)
    _set_active_hud_candidate(None)
    _audit_log(None, "info", f"HUD candidate approved id={candidate_id}")
    plan = _hud_candidate_plan(candidate)
    return {"status": "done", "candidate": candidate, "plan": plan}


@router.post("/hud/candidates/{candidate_id}/discard")
def discard_hud_candidate(candidate_id: str, payload: HudCandidateDecisionPayload):
    with _conn() as conn:
        candidate = _get_hud_candidate(conn, candidate_id)
        if candidate["status"] == "confirmed_by_provider":
            _error(409, "HUD_CANDIDATE_APPROVED", "approved candidate cannot be discarded")
        reviewer = (payload.reviewer_person_id or candidate.get("provider_person_id") or "").strip()
        conn.execute(
            """
            UPDATE hud_candidates
            SET status='discarded',
                review_status='rejected',
                reviewer_person_id=?,
                discarded_reason=?,
                reviewed_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (reviewer, (payload.reason or "").strip(), candidate_id),
        )
        conn.commit()
        candidate = _get_hud_candidate(conn, candidate_id)
    _set_active_hud_candidate(None)
    _audit_log(None, "info", f"HUD candidate discarded id={candidate_id}")
    plan = _hud_candidate_plan(candidate)
    return {"status": "done", "candidate": candidate, "plan": plan}


@router.get("/hud/candidates/{candidate_id}/moai-write-plan")
def get_hud_candidate_moai_write_plan(candidate_id: str):
    with _conn() as conn:
        candidate = _get_hud_candidate(conn, candidate_id)
    plan = _hud_candidate_plan(candidate)
    _audit_log(None, "info", f"HUD candidate moai write plan viewed id={candidate_id}")
    return {"status": "done", "result": plan}
