"""Event lifecycle endpoints: read, merge, MOAI export/write, pilot manifests, retention."""

import requests
import uuid

from fastapi import APIRouter, HTTPException
from lib.moai_writer import build_moai_write_plan, execute_moai_write_plan, load_moai_writer_config
from pydantic import BaseModel
from typing import Optional

import bridge_core as core
from bridge_core import (
    ASYNC_RESULTS,
    _audit_log,
    _build_moai_bundle_for_event,
    _build_physio_session_export_item,
    _build_pilot_manifest_for_event,
    _conn,
    _create_merged_event,
    _error,
    _get_event_for_merge,
    _get_event_snapshot,
    _get_label_by_event_id,
    _prune_async_results,
    logger,
)

router = APIRouter()


class MergeEventsPayload(BaseModel):
    image_event_id: str
    audio_event_id: str
    patient_name: Optional[str] = None


def _delete_event_artifacts(event_id: str) -> list[str]:
    deleted: list[str] = []
    candidates = [core.CHART_DIR / f"{event_id}_11.txt"]
    candidates.extend(
        path for path in core.MASKED_DIR.iterdir()
        if path.name.startswith(f"{event_id}_")
    )
    candidates.extend(
        path for path in core.RAW_MEDIA_DIR.iterdir()
        if path.name.startswith(f"{event_id}_")
    )
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                path.unlink()
                deleted.append(path.name)
        except Exception as e:
            logger.warning("artifact delete failed event_id=%s err=%s", event_id, e)
    return deleted


@router.post("/events/merge")
def merge_events(payload: MergeEventsPayload):
    with _conn() as conn:
        image_event = _get_event_for_merge(conn, payload.image_event_id)
        audio_event = _get_event_for_merge(conn, payload.audio_event_id)

    result = _create_merged_event(
        image_event=image_event,
        audio_event=audio_event,
        patient_name=payload.patient_name or "",
    )
    return {
        "event_id": result["event_id"],
        "status": "processed",
        "message": "통합 차트 생성 완료",
        "patient_name": result["patient_name"],
        "soap": result["soap"],
    }


@router.get("/events/{event_id}")
def get_event(event_id: str):
    _prune_async_results()
    row = ASYNC_RESULTS.get(event_id)
    if row:
        return row

    event_obj, soap_obj, label, _review, _artifacts = _get_event_snapshot(event_id)
    _audit_log(event_id, "info", "event viewed")
    return {"status": "done", "result": {"event": event_obj, "soap": soap_obj, "label": label}}


@router.get("/events/{event_id}/moai-export")
def get_event_moai_export(
    event_id: str,
    subject_person_id: str = "",
    provider_person_id: str = "",
    encounter_id: str = "",
    capture_device: str = "rayban",
    resolve_identity: bool = False,
):
    _prune_async_results()
    row = ASYNC_RESULTS.get(event_id)
    if row and row.get("status") not in {"done", "error"}:
        return {
            "status": row.get("status"),
            "message": "event is not ready for moai export yet",
            "event_id": event_id,
        }

    export_bundle = _build_moai_bundle_for_event(
        event_id,
        subject_person_id=(subject_person_id or "").strip() or None,
        provider_person_id=(provider_person_id or "").strip() or None,
        encounter_id=(encounter_id or "").strip() or None,
        capture_device=(capture_device or "").strip() or "rayban",
        resolve_identity=resolve_identity,
    )
    _audit_log(event_id, "info", "moai export viewed")
    return {"status": "done", "result": export_bundle}


@router.get("/events/{event_id}/moai-write-plan")
def get_event_moai_write_plan(
    event_id: str,
    subject_person_id: str = "",
    provider_person_id: str = "",
    encounter_id: str = "",
    capture_device: str = "rayban",
    resolve_identity: bool = False,
):
    bundle = _build_moai_bundle_for_event(
        event_id,
        subject_person_id=(subject_person_id or "").strip() or None,
        provider_person_id=(provider_person_id or "").strip() or None,
        encounter_id=(encounter_id or "").strip() or None,
        capture_device=(capture_device or "").strip() or "rayban",
        resolve_identity=resolve_identity,
    )
    plan = build_moai_write_plan(bundle)
    _audit_log(event_id, "info", "moai write plan viewed")
    return {"status": "done", "result": plan}


@router.post("/events/{event_id}/moai-write")
def write_event_to_moai(
    event_id: str,
    subject_person_id: str = "",
    provider_person_id: str = "",
    encounter_id: str = "",
    capture_device: str = "rayban",
    dry_run: bool = True,
    resolve_identity: bool = False,
):
    bundle = _build_moai_bundle_for_event(
        event_id,
        subject_person_id=(subject_person_id or "").strip() or None,
        provider_person_id=(provider_person_id or "").strip() or None,
        encounter_id=(encounter_id or "").strip() or None,
        capture_device=(capture_device or "").strip() or "rayban",
        resolve_identity=resolve_identity,
    )
    plan = build_moai_write_plan(bundle)
    if dry_run:
        _audit_log(event_id, "info", "moai dry-run write viewed")
        return {"status": "dry_run", "result": plan}

    config = load_moai_writer_config()
    if config is None:
        _error(
            503,
            "MOAI_WRITER_NOT_CONFIGURED",
            "Set MOAI_WEB_SUPABASE_URL and MOAI_WEB_SUPABASE_SECRET_KEY or MOAI_WEB_SUPABASE_SERVICE_ROLE_KEY.",
        )
    try:
        result = execute_moai_write_plan(plan, config=config)
    except requests.HTTPError as exc:
        response = exc.response
        detail = response.text[:2000] if response is not None else str(exc)
        _error(502, "MOAI_WRITE_FAILED", detail)
    except Exception as exc:
        _error(500, "MOAI_WRITE_FAILED", str(exc))

    _audit_log(event_id, "info", "moai write completed")
    return {"status": "done", "result": result}


@router.get("/events/{event_id}/pilot-manifest")
def get_event_pilot_manifest(event_id: str, resolve_identity: bool = True):
    return {"status": "done", "manifest": _build_pilot_manifest_for_event(event_id, resolve_identity=resolve_identity)}


@router.get("/events/{event_id}/pilot-readiness")
def get_event_pilot_readiness(event_id: str, resolve_identity: bool = True):
    manifest = _build_pilot_manifest_for_event(event_id, resolve_identity=resolve_identity)
    return {
        "status": "done",
        "event_id": event_id,
        "readiness": manifest["readiness"],
        "identity": manifest["identity"],
        "agent_dry_run": manifest["agent_dry_run"],
    }


@router.delete("/events/{event_id}")
def delete_event(event_id: str):
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")
        deleted_files = _delete_event_artifacts(event_id)
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), None, "info", f"event deleted id={event_id} files={len(deleted_files)}"),
        )
        conn.commit()
    ASYNC_RESULTS.pop(event_id, None)
    return {"ok": True, "event_id": event_id, "deleted_files": deleted_files}


@router.delete("/retention/events")
def purge_old_events(days: int = 30):
    if days < 1:
        _error(400, "INVALID_RETENTION_DAYS", "days는 1 이상이어야 합니다.")

    deleted_files: list[str] = []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM events WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        ).fetchall()
        event_ids = [r[0] for r in rows]
        for event_id in event_ids:
            deleted_files.extend(_delete_event_artifacts(event_id))
        if event_ids:
            conn.executemany("DELETE FROM events WHERE id = ?", [(event_id,) for event_id in event_ids])
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), None, "info", f"retention purge days={days} events={len(event_ids)} files={len(deleted_files)}"),
        )
        conn.commit()

    for event_id in event_ids:
        ASYNC_RESULTS.pop(event_id, None)

    return {"ok": True, "days": days, "purged_events": len(event_ids), "deleted_files": deleted_files}


@router.get("/recent-events")
def recent_events(limit: int = 10):
    n = max(1, min(limit, 50))
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, source, event_type, intent, status, created_at, patient_name,
                   owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id
            FROM events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
    items = []
    with _conn() as conn:
        for r in rows:
            label = _get_label_by_event_id(conn, r[0])
            identity_fields = {
                "owner_org_id": r[7] or None,
                "owner_provider_person_id": r[8] or None,
                "subject_person_id": r[9] or None,
                "physio_client_id": r[10] or None,
                "physio_session_id": r[11] or None,
            }
            present_identity_count = sum(1 for value in identity_fields.values() if value)
            missing_identity = [
                key
                for key in ("owner_org_id", "owner_provider_person_id", "physio_session_id")
                if not identity_fields.get(key)
            ]
            if not (identity_fields.get("subject_person_id") or identity_fields.get("physio_client_id")):
                missing_identity.append("subject_person_id_or_physio_client_id")
            items.append(
                {
                    "id": r[0],
                    "source": r[1],
                    "event_type": r[2],
                    "intent": r[3],
                    "status": r[4],
                    "created_at": r[5],
                    "has_label": label is not None,
                    "patient_name": r[6] or None,
                    **identity_fields,
                    "identity_completeness": {
                        "present": present_identity_count,
                        "required": 4,
                        "complete": not missing_identity,
                        "missing": missing_identity,
                    },
                }
            )
    return {"items": items}


@router.get("/physio/sessions")
def physio_sessions(
    limit: int = 20,
    patient_name: str = "",
    org_id: str = "",
    provider_person_id: str = "",
    subject_person_id: str = "",
    client_id: str = "",
    session_id: str = "",
    include_unscoped: bool = False,
):
    """physio_app에서 바로 읽을 수 있는 현장 세션 피드."""
    n = max(1, min(limit, 100))
    clean_patient_name = patient_name.strip()
    clean_org_id = (org_id or "").strip()
    clean_provider_person_id = (provider_person_id or "").strip()
    clean_subject_person_id = (subject_person_id or "").strip()
    clean_client_id = (client_id or "").strip()
    clean_session_id = (session_id or "").strip()
    clauses: list[str] = []
    params: list[object] = []

    if clean_patient_name:
        clauses.append("patient_name = ?")
        params.append(clean_patient_name)
    if clean_org_id:
        if include_unscoped:
            clauses.append("(owner_org_id = ? OR owner_org_id IS NULL OR owner_org_id = '')")
        else:
            clauses.append("owner_org_id = ?")
        params.append(clean_org_id)
    if clean_provider_person_id:
        if include_unscoped:
            clauses.append("(owner_provider_person_id = ? OR owner_provider_person_id IS NULL OR owner_provider_person_id = '')")
        else:
            clauses.append("owner_provider_person_id = ?")
        params.append(clean_provider_person_id)
    if clean_subject_person_id:
        clauses.append("subject_person_id = ?")
        params.append(clean_subject_person_id)
    if clean_client_id:
        clauses.append("physio_client_id = ?")
        params.append(clean_client_id)
    if clean_session_id:
        clauses.append("physio_session_id = ?")
        params.append(clean_session_id)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(n)

    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, source, event_type, intent, status, created_at, patient_name, owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id
            FROM events
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        items = [_build_physio_session_export_item(conn, row) for row in rows]

    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "storage": "rayban-local-bridge.sqlite",
        "schema_version": "physio-session-feed/v2",
        "scope": {
            "org_id": clean_org_id or None,
            "provider_person_id": clean_provider_person_id or None,
            "subject_person_id": clean_subject_person_id or None,
            "client_id": clean_client_id or None,
            "session_id": clean_session_id or None,
            "include_unscoped": include_unscoped,
        },
    }
