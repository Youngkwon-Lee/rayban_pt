from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


VISIT_PHASES = {"pre_review", "assessment", "intervention", "home_program", "summary"}
VISIT_EVENT_ROLES = {"assessment", "intervention", "home_program", "observation"}
PROVIDER_ROLES = {
    "physical_therapist",
    "occupational_therapist",
    "pilates_instructor",
    "personal_trainer",
    "caregiver",
    "unspecified",
    "other",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_visit_session_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS visit_sessions (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            provider_person_id TEXT NOT NULL,
            provider_role TEXT NOT NULL DEFAULT 'unspecified',
            subject_person_id TEXT NOT NULL,
            encounter_id TEXT NOT NULL,
            patient_alias TEXT NOT NULL DEFAULT '',
            phase TEXT NOT NULL DEFAULT 'pre_review',
            status TEXT NOT NULL DEFAULT 'active',
            recording_status TEXT NOT NULL DEFAULT 'idle',
            selected_at TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            session_timer_started_at TEXT,
            recording_started_at TEXT,
            history_summary TEXT NOT NULL DEFAULT '',
            readiness TEXT NOT NULL DEFAULT 'ready',
            error_state TEXT,
            cue TEXT NOT NULL DEFAULT '',
            event_ids TEXT NOT NULL DEFAULT '[]',
            event_refs TEXT NOT NULL DEFAULT '[]',
            draft_progress_note TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_visit_sessions_org_updated_at
            ON visit_sessions(organization_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_visit_sessions_subject_updated_at
            ON visit_sessions(subject_person_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_visit_sessions_encounter_id
            ON visit_sessions(encounter_id);
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(visit_sessions)").fetchall()}
    if "event_refs" not in columns:
        conn.execute("ALTER TABLE visit_sessions ADD COLUMN event_refs TEXT NOT NULL DEFAULT '[]'")
    if "recording_started_at" not in columns:
        conn.execute("ALTER TABLE visit_sessions ADD COLUMN recording_started_at TEXT")
    if "provider_role" not in columns:
        conn.execute("ALTER TABLE visit_sessions ADD COLUMN provider_role TEXT NOT NULL DEFAULT 'unspecified'")


def _as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _from_json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except Exception:
        pass
    return []


def _from_json_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _from_json_refs(value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
        if isinstance(parsed, list):
            refs = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                event_id = str(item.get("event_id") or "").strip()
                if not event_id:
                    continue
                refs.append(
                    {
                        "event_id": event_id,
                        "role": str(item.get("role") or "observation"),
                        "phase": str(item.get("phase") or ""),
                        "attached_at": str(item.get("attached_at") or ""),
                    }
                )
            return refs
    except Exception:
        pass
    return []


def _event_role_counts(event_refs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {role: 0 for role in sorted(VISIT_EVENT_ROLES)}
    for ref in event_refs:
        role = normalize_event_role(str(ref.get("role") or ""))
        counts[role] = counts.get(role, 0) + 1
    return counts


def normalize_phase(phase: str) -> str:
    clean = (phase or "").strip()
    if clean not in VISIT_PHASES:
        raise ValueError(f"phase must be one of: {', '.join(sorted(VISIT_PHASES))}")
    return clean


def normalize_event_role(role: str | None, phase: str | None = None) -> str:
    clean = (role or "").strip()
    if clean in VISIT_EVENT_ROLES:
        return clean
    phase_clean = (phase or "").strip()
    if phase_clean in {"assessment", "intervention", "home_program"}:
        return phase_clean
    return "observation"


def row_to_visit_session(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "organization_id": row[1],
        "provider_person_id": row[2],
        "provider_role": row[3] or "unspecified",
        "subject_person_id": row[4],
        "encounter_id": row[5],
        "patient_alias": row[6],
        "phase": row[7],
        "status": row[8],
        "recording_status": row[9],
        "selected_at": row[10],
        "started_at": row[11],
        "ended_at": row[12],
        "session_timer_started_at": row[13],
        "recording_started_at": row[14],
        "history_summary": row[15],
        "readiness": row[16],
        "error_state": row[17],
        "cue": row[18],
        "event_ids": _from_json_list(row[19]),
        "event_refs": _from_json_refs(row[20]),
        "draft_progress_note": _from_json_dict(row[21]),
        "created_at": row[22],
        "updated_at": row[23],
    }


def get_visit_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, organization_id, provider_person_id, provider_role, subject_person_id, encounter_id,
               patient_alias, phase, status, recording_status, selected_at, started_at, ended_at,
               session_timer_started_at, recording_started_at, history_summary, readiness, error_state, cue, event_ids,
               event_refs, draft_progress_note, created_at, updated_at
        FROM visit_sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    return row_to_visit_session(row) if row else None


def create_visit_session(
    conn: sqlite3.Connection,
    *,
    organization_id: str,
    provider_person_id: str,
    subject_person_id: str,
    provider_role: str = "unspecified",
    encounter_id: str | None = None,
    patient_alias: str = "",
    history_summary: str = "",
) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    canonical_encounter_id = (encounter_id or "").strip() or str(uuid.uuid4())
    now = utc_now()
    conn.execute(
        """
        INSERT INTO visit_sessions (
            id, organization_id, provider_person_id, provider_role, subject_person_id, encounter_id,
            patient_alias, phase, status, recording_status, selected_at, started_at,
            session_timer_started_at, history_summary, readiness, cue, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pre_review', 'active', 'idle', ?, ?, ?, ?, 'ready',
                '기록 확인 후 평가로 진행', ?)
        """,
        (
            session_id,
            organization_id,
            provider_person_id,
            provider_role.strip() if provider_role.strip() in PROVIDER_ROLES else "unspecified",
            subject_person_id,
            canonical_encounter_id,
            patient_alias.strip() or "Patient",
            now,
            now,
            now,
            history_summary.strip(),
            now,
        ),
    )
    return get_visit_session(conn, session_id) or {}


def update_visit_phase(conn: sqlite3.Connection, session_id: str, phase: str, cue: str | None = None) -> dict[str, Any]:
    normalized = normalize_phase(phase)
    existing = get_visit_session(conn, session_id)
    if not existing:
        raise KeyError(session_id)
    conn.execute(
        """
        UPDATE visit_sessions
        SET phase = ?, cue = COALESCE(?, cue), updated_at = ?
        WHERE id = ?
        """,
        (normalized, cue, utc_now(), session_id),
    )
    return get_visit_session(conn, session_id) or {}


def set_visit_recording(conn: sqlite3.Connection, session_id: str, is_recording: bool) -> dict[str, Any]:
    existing = get_visit_session(conn, session_id)
    if not existing:
        raise KeyError(session_id)
    now = utc_now()
    conn.execute(
        """
        UPDATE visit_sessions
        SET recording_status = ?,
            recording_started_at = CASE WHEN ? = 'recording' THEN ? ELSE NULL END,
            updated_at = ?
        WHERE id = ?
        """,
        ("recording" if is_recording else "idle", "recording" if is_recording else "idle", now, now, session_id),
    )
    return get_visit_session(conn, session_id) or {}


def attach_visit_event(
    conn: sqlite3.Connection,
    session_id: str,
    event_id: str,
    *,
    role: str | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    existing = get_visit_session(conn, session_id)
    if not existing:
        raise KeyError(session_id)
    event_ids = existing["event_ids"]
    if event_id not in event_ids:
        event_ids.append(event_id)
    ref_role = normalize_event_role(role, phase or existing.get("phase"))
    ref_phase = (phase or existing.get("phase") or "").strip()
    now = utc_now()
    next_ref = {
        "event_id": event_id,
        "role": ref_role,
        "phase": ref_phase,
        "attached_at": now,
    }
    event_refs = []
    updated_ref = False
    for ref in existing.get("event_refs") or []:
        if ref.get("event_id") == event_id:
            event_refs.append({**ref, **next_ref})
            updated_ref = True
        else:
            event_refs.append(ref)
    if not updated_ref:
        event_refs.append(next_ref)
    conn.execute(
        "UPDATE visit_sessions SET event_ids = ?, event_refs = ?, updated_at = ? WHERE id = ?",
        (_as_json(event_ids), _as_json(event_refs), now, session_id),
    )
    return get_visit_session(conn, session_id) or {}


def build_draft_progress_note(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "note_format": "progress",
        "status": "draft",
        "requires_approval": True,
        "subjective": session.get("history_summary") or "기록 확인 후 방문 재활 세션 진행.",
        "objective": f"방문 세션 phase={session.get('phase')}, linked_events={len(session.get('event_ids') or [])}.",
        "assessment": "AI 추출 결과는 clinician review 전 draft 상태.",
        "plan": "평가/중재/가정 과제 내용을 검토 후 확정.",
    }


def end_visit_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    existing = get_visit_session(conn, session_id)
    if not existing:
        raise KeyError(session_id)
    draft = build_draft_progress_note(existing)
    conn.execute(
        """
        UPDATE visit_sessions
        SET status = 'ended',
            phase = 'summary',
            recording_status = 'idle',
            recording_started_at = NULL,
            ended_at = COALESCE(ended_at, ?),
            draft_progress_note = ?,
            cue = '진행 노트 초안 검토 필요',
            updated_at = ?
        WHERE id = ?
        """,
        (utc_now(), _as_json(draft), utc_now(), session_id),
    )
    return get_visit_session(conn, session_id) or {}


def visit_hud_state(session: dict[str, Any]) -> dict[str, Any]:
    recording = session.get("recording_status") == "recording"
    return {
        "visit_session_id": session.get("id"),
        "patient": session.get("patient_alias") or "Patient",
        "mode": "recording" if recording else session.get("phase") or "ready",
        "message": session.get("cue") or f"{session.get('phase', 'ready')} 준비",
        "is_recording": recording,
        "recording_start": session.get("recording_started_at") if recording else None,
        "session_count": len(session.get("event_ids") or []),
        "event_role_counts": _event_role_counts(session.get("event_refs") or []),
        "phase": session.get("phase") or "pre_review",
        "readiness": session.get("readiness") or "ready",
        "error_state": session.get("error_state"),
        "last_insight": {
            "id": session.get("id"),
            "title": "Visit Session",
            "body": session.get("cue") or session.get("phase") or "ready",
            "severity": "info" if session.get("readiness") == "ready" else "warning",
            "lens_safe": True,
            "source": "visit_session_orchestrator",
        },
    }
