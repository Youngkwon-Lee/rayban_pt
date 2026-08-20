"""Patient consent capture, lookup, and revocation endpoints."""

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional, Union

from bridge_core import (
    DEFAULT_CONSENT_TEXT,
    _conn,
    _delete_raw_event_artifacts,
    _error,
    _latest_patient_consent,
    _scope_from_request,
)

router = APIRouter()


class ConsentPayload(BaseModel):
    patient_name: str
    scope: str = "capture_analysis_storage"
    consent_text: Optional[str] = None
    granted_by: Optional[str] = None
    owner_org_id: Optional[str] = None
    owner_provider_person_id: Optional[str] = None
    subject_person_id: Optional[str] = None


class ConsentLookupPayload(BaseModel):
    patient_name: str
    scope: str = "capture_analysis_storage"
    owner_org_id: Optional[str] = None
    owner_provider_person_id: Optional[str] = None
    subject_person_id: Optional[str] = None


def _consent_identity_from_payload(
    request: Request,
    payload: Union[ConsentPayload, ConsentLookupPayload],
) -> tuple[str, str, str]:
    org_id, provider_id = _scope_from_request(
        request,
        owner_org_id=payload.owner_org_id,
        owner_provider_person_id=payload.owner_provider_person_id,
    )
    subject_id = (payload.subject_person_id or "").strip()
    if not org_id or not provider_id or not subject_id:
        _error(422, "CONSENT_IDENTITY_REQUIRED", "조직, 치료사, 환자 person ID가 필요합니다.")
    return org_id, provider_id, subject_id


@router.post("/consents")
def record_consent(payload: ConsentPayload, request: Request):
    patient_name = payload.patient_name.strip()
    scope = payload.scope.strip() or "capture_analysis_storage"
    if not patient_name:
        _error(400, "INVALID_PATIENT_NAME", "patient_name은 비워둘 수 없습니다.")
    owner_org_id, owner_provider_person_id, subject_person_id = _consent_identity_from_payload(request, payload)

    consent_id = str(uuid.uuid4())
    consent_text = (payload.consent_text or DEFAULT_CONSENT_TEXT).strip()
    granted_by = (payload.granted_by or "").strip() or None

    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO patient_consents (
              id, patient_name, owner_org_id, owner_provider_person_id,
              subject_person_id, scope, consent_text, granted_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                consent_id,
                patient_name,
                owner_org_id,
                owner_provider_person_id,
                subject_person_id,
                scope,
                consent_text,
                granted_by,
            ),
        )
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), None, "info", f"consent recorded scope={scope}"),
        )
        conn.commit()

    return {
        "ok": True,
        "consent": {
            "id": consent_id,
            "patient_name": patient_name,
            "scope": scope,
            "granted_by": granted_by,
            "owner_org_id": owner_org_id,
            "owner_provider_person_id": owner_provider_person_id,
            "subject_person_id": subject_person_id,
        },
    }


@router.post("/consents/status")
def get_patient_consent(payload: ConsentLookupPayload, request: Request):
    name = payload.patient_name.strip()
    scope = payload.scope.strip() or "capture_analysis_storage"
    if not name:
        _error(400, "INVALID_PATIENT_NAME", "patient_name은 비워둘 수 없습니다.")
    owner_org_id, owner_provider_person_id, subject_person_id = _consent_identity_from_payload(request, payload)
    with _conn() as conn:
        row = _latest_patient_consent(
            conn,
            name,
            scope,
            owner_org_id=owner_org_id,
            owner_provider_person_id=owner_provider_person_id,
            subject_person_id=subject_person_id,
        )
    if not row:
        return {"patient_name": name, "scope": scope, "active": False, "consent": None}
    return {
        "patient_name": name,
        "scope": scope,
        "active": True,
        "consent": {
            "id": row[0],
            "patient_name": row[1],
            "scope": row[2],
            "consent_text": row[3],
            "granted_by": row[4],
            "created_at": row[5],
        },
    }


@router.delete("/consents")
def revoke_patient_consent(payload: ConsentLookupPayload, request: Request):
    name = payload.patient_name.strip()
    clean_scope = payload.scope.strip() or "capture_analysis_storage"
    if not name:
        _error(400, "INVALID_PATIENT_NAME", "patient_name은 비워둘 수 없습니다.")
    owner_org_id, owner_provider_person_id, subject_person_id = _consent_identity_from_payload(request, payload)

    with _conn() as conn:
        cur = conn.execute(
            """
            UPDATE patient_consents
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE patient_name = ? AND scope = ?
              AND owner_org_id = ? AND owner_provider_person_id = ? AND subject_person_id = ?
              AND revoked_at IS NULL
            """,
            (name, clean_scope, owner_org_id, owner_provider_person_id, subject_person_id),
        )
        event_ids = [
            row[0]
            for row in conn.execute(
                """
                SELECT id FROM events
                WHERE patient_name = ? AND owner_org_id = ?
                  AND owner_provider_person_id = ? AND subject_person_id = ?
                """,
                (name, owner_org_id, owner_provider_person_id, subject_person_id),
            ).fetchall()
        ]
        purged_raw_files = 0
        if cur.rowcount and clean_scope == "capture_analysis_storage":
            for event_id in event_ids:
                purged_raw_files += _delete_raw_event_artifacts(event_id)
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), None, "info", f"consent revoked scope={clean_scope} count={cur.rowcount}"),
        )
        conn.commit()

    return {
        "ok": True,
        "patient_name": name,
        "scope": clean_scope,
        "revoked": cur.rowcount,
        "purged_raw_files": purged_raw_files,
    }
