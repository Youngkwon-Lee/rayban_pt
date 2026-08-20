"""MOAI synchronisation job status endpoints."""

from fastapi import APIRouter, HTTPException

from bridge_core import (
    _conn,
    _error,
    _list_moai_sync_jobs,
    _moai_sync_job_from_row,
)

router = APIRouter()


@router.get("/moai-sync/jobs")
def get_moai_sync_jobs(status: str = "pending", limit: int = 20):
    clean_status = (status or "").strip().lower()
    if clean_status not in {"pending", "planned", "blocked", "synced", "error", "all"}:
        _error(400, "INVALID_SYNC_STATUS", "status must be pending, planned, blocked, synced, error, or all")
    return {"status": "done", "items": _list_moai_sync_jobs(status=clean_status, limit=limit)}


@router.get("/moai-sync/jobs/{event_id}")
def get_moai_sync_job(event_id: str):
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT id, event_id, status, trigger_reason, operation_count, skipped_count, attempts,
                   last_error, last_plan_summary, last_result_summary, last_attempted_at,
                   synced_at, created_at, updated_at
            FROM moai_sync_jobs
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="moai sync job not found")
    return {"status": "done", "job": _moai_sync_job_from_row(row)}
