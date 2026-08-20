"""Glass HUD relay: scope tokens, visit selection, state, commands, and neural band events."""

from fastapi import APIRouter, Request
from datetime import datetime, timedelta, timezone
from lib.visit_session import create_visit_session
from pydantic import BaseModel
from typing import Optional
from urllib.parse import urlencode

import bridge_core as core
from bridge_core import (
    NEURAL_BAND_GESTURE_MAP,
    _apply_hud_test_visit_state,
    _apply_visit_session_hud,
    _attach_pre_review_to_session,
    _attach_record_preview_to_candidate,
    _audit_log,
    _conn,
    _error,
    _get_glass_visit_candidate,
    _glass_lock,
    _glass_state,
    _hud_scope_from_request,
    _hud_test_state,
    _is_hud_test_request,
    _queue_glass_command,
    _visit_candidate_history_summary,
    build_hud_scope_token,
)

router = APIRouter()


class HudTokenIssuePayload(BaseModel):
    organization_id: str
    provider_person_id: str
    expires_in_minutes: int = 720
    bridge_url: Optional[str] = None
    app_path: str = "/glass-app/"


class GlassStateUpdate(BaseModel):
    patient: Optional[str] = None
    mode: Optional[str] = None
    message: Optional[str] = None
    is_recording: Optional[bool] = None
    recording_start: Optional[str] = None
    session_count: Optional[int] = None
    event_role_counts: Optional[dict] = None
    capture_role: Optional[str] = None
    active_hud_candidate: Optional[dict] = None
    visit_session_id: Optional[str] = None
    phase: Optional[str] = None
    readiness: Optional[str] = None
    error_state: Optional[str] = None
    last_insight: Optional[dict] = None


class GlassCommandRequest(BaseModel):
    command: str


class NeuralBandEventRequest(BaseModel):
    gesture: str
    device_id: Optional[str] = None
    source: str = "neural_band"
    metadata: Optional[dict] = None


class GlassVisitStartRequest(BaseModel):
    candidate_id: Optional[str] = None
    update_glass: bool = True


@router.post("/glass/hud-token")
def issue_hud_scope_token(payload: HudTokenIssuePayload):
    organization_id = payload.organization_id.strip()
    provider_person_id = payload.provider_person_id.strip()
    if not organization_id or not provider_person_id:
        _error(422, "HUD_TOKEN_SCOPE_REQUIRED", "organization_id and provider_person_id are required")
    minutes = max(5, min(int(payload.expires_in_minutes or 720), 7 * 24 * 60))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    token = build_hud_scope_token(
        organization_id=organization_id,
        provider_person_id=provider_person_id,
        expires_at=expires_at,
    )
    app_path = payload.app_path.strip() or "/glass-app/"
    if not app_path.startswith("/"):
        app_path = "/" + app_path
    bridge_url = (payload.bridge_url or "").strip().rstrip("/")
    token_query = urlencode({"hud_token": token})
    glass_app_url = f"{app_path}?{token_query}"
    if bridge_url:
        glass_app_url = f"{bridge_url}{glass_app_url}"
    return {
        "status": "done",
        "hud_token": token,
        "token_type": "hud_scope",
        "scope": {
            "organization_id": organization_id,
            "provider_person_id": provider_person_id,
        },
        "expires_at": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expires_in_minutes": minutes,
        "glass_app_url": glass_app_url,
    }


@router.get("/glass/visits/next")
def glass_visits_next(request: Request, offset: int = 0, candidate_id: Optional[str] = None):
    scope = _hud_scope_from_request(request)
    with _conn() as conn:
        candidate = _get_glass_visit_candidate(
            conn,
            candidate_id=(candidate_id or "").strip() or None,
            offset=max(0, offset),
            scope=scope,
        )
        candidate = _attach_record_preview_to_candidate(conn, candidate)
    if not candidate:
        return {
            "status": "empty",
            "candidate": None,
            "message": "No visit candidate with canonical identity is available.",
        }
    return {"status": "ready", "candidate": candidate}


@router.post("/glass/visits/start")
def glass_visits_start(payload: GlassVisitStartRequest, request: Request):
    is_hud_test = _is_hud_test_request(request)
    scope = _hud_scope_from_request(request)
    with _conn() as conn:
        candidate = _get_glass_visit_candidate(
            conn,
            candidate_id=(payload.candidate_id or "").strip() or None,
            scope=scope,
        )
        if not candidate:
            _error(404, "NO_GLASS_VISIT_CANDIDATE", "No visit candidate with canonical identity is available.")
        if candidate["readiness"] != "ready":
            _error(409, "GLASS_VISIT_IDENTITY_REQUIRED", "Visit candidate requires organization, provider, and subject IDs.")
        session = create_visit_session(
            conn,
            organization_id=candidate["organization_id"],
            provider_person_id=candidate["provider_person_id"],
            subject_person_id=candidate["subject_person_id"],
            encounter_id=candidate["encounter_id"],
            patient_alias=candidate["patient_alias"],
            history_summary=_visit_candidate_history_summary(candidate),
        )
        session, pre_review = _attach_pre_review_to_session(conn, session)
        conn.commit()
    hud = (
        _apply_hud_test_visit_state(session)
        if is_hud_test and payload.update_glass
        else _apply_visit_session_hud(session, insight=pre_review)
        if payload.update_glass
        else None
    )
    _audit_log(None, "info", f"glass visit started session={session['id']} candidate={candidate['id']}")
    return {"status": "started", "candidate": candidate, "session": session, "glass_state": hud}


@router.get("/glass/state")
def glass_state_get(request: Request):
    with _glass_lock:
        if _is_hud_test_request(request):
            return dict(_hud_test_state)
        return dict(_glass_state)


@router.post("/glass/state")
def glass_state_post(update: GlassStateUpdate):
    fields_set = getattr(update, "model_fields_set", getattr(update, "__fields_set__", set()))
    with _glass_lock:
        if "patient" in fields_set:
            _glass_state["patient"] = update.patient
        if "mode" in fields_set:
            _glass_state["mode"] = update.mode
        if "message" in fields_set:
            _glass_state["message"] = update.message
        if "is_recording" in fields_set:
            _glass_state["is_recording"] = update.is_recording
        if "recording_start" in fields_set:
            _glass_state["recording_start"] = update.recording_start
        if "session_count" in fields_set:
            _glass_state["session_count"] = update.session_count
        if "event_role_counts" in fields_set:
            _glass_state["event_role_counts"] = update.event_role_counts
        if "capture_role" in fields_set:
            _glass_state["capture_role"] = update.capture_role
        if "active_hud_candidate" in fields_set:
            _glass_state["active_hud_candidate"] = update.active_hud_candidate
        if "visit_session_id" in fields_set:
            _glass_state["visit_session_id"] = update.visit_session_id
        if "phase" in fields_set:
            _glass_state["phase"] = update.phase
        if "readiness" in fields_set:
            _glass_state["readiness"] = update.readiness
        if "error_state" in fields_set:
            _glass_state["error_state"] = update.error_state
        if "last_insight" in fields_set:
            _glass_state["last_insight"] = update.last_insight
        _glass_state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    return {"ok": True}


@router.post("/glass/command")
def glass_command_post(cmd: GlassCommandRequest, request: Request):
    scope = _hud_scope_from_request(request)
    queued = _queue_glass_command(cmd.command, scope=scope)
    response = {"ok": True, "command": queued["command"], "id": queued["id"]}
    if "executed" in queued:
        response["executed"] = queued["executed"]
    return response


@router.post("/neural-band/event")
def neural_band_event_post(event: NeuralBandEventRequest, request: Request):
    scope = _hud_scope_from_request(request)
    gesture = event.gesture.strip().lower()
    command = NEURAL_BAND_GESTURE_MAP.get(gesture)
    if command is None:
        allowed = ", ".join(sorted(NEURAL_BAND_GESTURE_MAP.keys()))
        _error(400, "INVALID_NEURAL_BAND_GESTURE", f"gesture must map to one of: {allowed}")

    metadata = dict(event.metadata or {})
    if event.device_id:
        metadata["device_id"] = event.device_id
    metadata["gesture"] = gesture

    queued = _queue_glass_command(
        command,
        source=event.source or "neural_band",
        metadata=metadata,
        scope=scope,
        delivery="device",
    )
    return {
        "ok": True,
        "gesture": gesture,
        "mapped_command": queued["command"],
        "id": queued["id"],
        "executed": queued.get("executed"),
    }


@router.get("/glass/command")
def glass_command_get():
    with _glass_lock:
        cmd = core._glass_pending_command.pop(0) if core._glass_pending_command else None
    if cmd is None:
        return {"command": None}
    return cmd


@router.get("/glass/device-command")
def glass_device_command_get():
    """Consume one command intended for the paired native iOS app."""
    with _glass_lock:
        cmd = core._glass_pending_device_command.pop(0) if core._glass_pending_device_command else None
    if cmd is None:
        return {"command": None}
    return cmd
