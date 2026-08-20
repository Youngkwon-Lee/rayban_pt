"""Agent gateway: tool-scoped, dry-run-only session cue generation."""

from fastapi import APIRouter
from datetime import datetime
from typing import Optional

from bridge_core import (
    AgentCueDryRunRequest,
    _audit_log,
    _build_dry_run_session_cue,
    _conn,
    _error,
    _glass_lock,
    _glass_state,
)

router = APIRouter()


AGENT_ALLOWED_TOOLS = {"generate_session_cue"}


AGENT_BLOCKED_ACTIONS = [
    "production_supabase_write",
    "patient_message",
    "billing",
    "delete_data",
    "model_training",
    "model_promotion",
]


def _existing_event_id_for_audit(event_id: Optional[str]) -> Optional[str]:
    if not event_id:
        return None
    try:
        with _conn() as conn:
            row = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        return event_id if row else None
    except Exception:
        return None


@router.post("/agent/cue-dry-run")
def agent_cue_dry_run(payload: AgentCueDryRunRequest):
    requested_tool = (payload.requested_tool or "").strip()
    audit_event_id = _existing_event_id_for_audit(payload.event_id)
    if requested_tool not in AGENT_ALLOWED_TOOLS:
        _audit_log(audit_event_id, "warning", f"agent blocked tool={requested_tool or '-'}")
        _error(403, "AGENT_TOOL_NOT_ALLOWED", "Only generate_session_cue is enabled in dry-run mode.")

    cue = _build_dry_run_session_cue(payload)
    glass_state_updated = False

    if payload.update_glass:
        with _glass_lock:
            _glass_state["last_insight"] = cue
            _glass_state["updated_at"] = datetime.utcnow().isoformat() + "Z"
        glass_state_updated = True

    _audit_log(
        audit_event_id,
        "info",
        f"agent dry-run cue generated update_glass={str(glass_state_updated).lower()} mode={payload.mode}",
    )
    return {
        "status": "dry_run",
        "tool": requested_tool,
        "cue": cue,
        "glass_state_updated": glass_state_updated,
        "allowed_actions": sorted(AGENT_ALLOWED_TOOLS),
        "blocked_actions": AGENT_BLOCKED_ACTIONS,
        "requires_clinician_review": True,
        "writes_enabled": False,
    }
