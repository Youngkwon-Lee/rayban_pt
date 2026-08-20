"""Shared state, configuration, and helpers for the rayban local bridge.

This module holds everything the HTTP layer needs but that is not itself an
endpoint: environment-derived configuration, mutable process state, database
access, and the domain helpers used by the routers in ``routers/``.

Routers must read mutable configuration and state through this module
namespace (``bridge_core.DB_PATH``) rather than importing the value, so that
tests and runtime overrides are observed by every caller.
"""

import os
import ipaddress
import re
import sqlite3
import uuid
import logging
import concurrent.futures
import threading
import base64
import hashlib
import hmac
import requests
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlencode
import json

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

# ── auto-chart 통합 ──────────────────────────────────────────────────────────
from lib.auto_chart import generate_chart, mask_faces as _mask_faces, save_chart
from lib.hud_state_machine import build_hud_moai_bundle_from_candidate
from lib.moai_identity import resolve_moai_identity
from lib.moai_mapper import build_moai_export_bundle
from lib.moai_writer import build_moai_write_plan, execute_moai_write_plan, load_moai_writer_config
from lib.raw_media import RawMediaStage, delete_raw_media, list_raw_media_artifacts, resolve_raw_media, stage_raw_media
from lib.pose_capture import (
    POSE_EXTRACTOR_VERSION,
    analyze_pose_frames,
)
from lib.transcript_capture import (
    TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION,
    capture_action_type,
    extract_transcript_capture_candidates,
)
from lib.visit_session import (
    PROVIDER_ROLES,
    attach_visit_event,
    create_visit_session,
    end_visit_session,
    ensure_visit_session_schema,
    get_visit_session,
    set_visit_recording,
    update_visit_phase,
    visit_hud_state,
)

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "storage" / "bridge.db"
UPLOAD_DIR = ROOT / "storage" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CHART_DIR = ROOT / "storage" / "charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)
MASKED_DIR = ROOT / "storage" / "masked"
MASKED_DIR.mkdir(parents=True, exist_ok=True)
RAW_MEDIA_DIR = ROOT / "storage" / "raw-media"
RAW_MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "").strip()
HUD_SCOPE_SECRET = os.getenv("RAYBAN_HUD_SCOPE_SECRET", "").strip()
REQUIRE_API_KEY = _env_bool("REQUIRE_API_KEY", True)
ALLOW_INSECURE_LAN = _env_bool("ALLOW_INSECURE_LAN", False)
ALLOW_DOCS_WITHOUT_AUTH = _env_bool("ALLOW_DOCS_WITHOUT_AUTH", False)
ENABLE_FILE_DOWNLOADS = _env_bool("ENABLE_FILE_DOWNLOADS", False)
ALLOW_UNMASKED_IMAGE = _env_bool("ALLOW_UNMASKED_IMAGE", False)
REQUIRE_PATIENT_CONSENT = _env_bool("REQUIRE_PATIENT_CONSENT", False)
AUDIO_STORE = _env_bool("AUDIO_STORE", False)
VIDEO_STORE = _env_bool("VIDEO_STORE", False)
PILOT_CAPTURE_MODE = _env_bool("PILOT_CAPTURE_MODE", False)

PUBLIC_PATHS = {"/", "/health", "/label-taxonomy"}
PUBLIC_PATH_PREFIXES = ("/glass-app", "/neural-band-console", "/g")
HUD_TOKEN_AUTH_PATH_PREFIXES = ("/glass/", "/neural-band/event", "/hud/candidates")
HUD_TOKEN_ISSUE_PATH = "/glass/hud-token"
# The public Display Web App test route is deliberately constrained to the
# synthetic, non-PHI fixture.  It is only a device-load check: commands,
# native capture, and normal HUD state remain authenticated.
HUD_TEST_HEADER = "x-hud-test"
HUD_TEST_SCOPE = {"organization_id": "t1", "provider_person_id": "p1"}
HUD_TEST_ALLOWED_REQUESTS = {
    ("GET", "/glass/state"),
    ("GET", "/glass/visits/next"),
    ("POST", "/glass/visits/start"),
}
DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


def _is_hud_test_request(request: Request) -> bool:
    return (
        request.headers.get(HUD_TEST_HEADER, "") == "1"
        and (request.method, request.url.path) in HUD_TEST_ALLOWED_REQUESTS
    )

ASYNC_RESULTS: dict[str, dict] = {}
ASYNC_RESULT_TTL_MINUTES = int(os.getenv("ASYNC_RESULT_TTL_MINUTES", "60"))
ASYNC_RESULT_MAX_ITEMS = int(os.getenv("ASYNC_RESULT_MAX_ITEMS", "1000"))
UPLOAD_MAX_MB = int(os.getenv("UPLOAD_MAX_MB", "25"))
PROCESS_TIMEOUT_SECONDS = int(os.getenv("PROCESS_TIMEOUT_SECONDS", "180"))

logger = logging.getLogger("rayban-local-bridge")
if not logger.handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)
CONSENT_MEDIA_LOCK = threading.RLock()


def _client_host(request: Request) -> str:
    direct_host = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and _is_loopback_host(direct_host):
        return forwarded.split(",", 1)[0].strip()
    return direct_host


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class IngestPayload(BaseModel):
    source: str
    event_type: str  # audio/text/command/image
    text: Optional[str] = None
    audio_path: Optional[str] = None
    image_base64: Optional[str] = None  # base64 encoded JPEG/PNG
    patient_name: Optional[str] = None
    owner_org_id: Optional[str] = None
    owner_provider_person_id: Optional[str] = None
    org_id: Optional[str] = None
    provider_person_id: Optional[str] = None
    subject_person_id: Optional[str] = None
    physio_client_id: Optional[str] = None
    physio_session_id: Optional[str] = None
    client_id: Optional[str] = None
    session_id: Optional[str] = None
    encounter_id: Optional[str] = None


class RehabLabelPayload(BaseModel):
    provider_role: str = "unspecified"
    action_type: str = "observation"
    session_type: str
    core_task: str
    custom_task: str = ""
    body_position: str = ""
    assist_level: str
    performance: Optional[str] = None
    performance_level: Optional[str] = None
    review_status: str = "reviewed"
    reviewer_person_id: str = ""
    usable_for_training: bool = False
    label_confidence: Optional[float] = None
    repetition_count: Optional[int] = None
    hold_duration_seconds: Optional[float] = None
    tolerance: str = ""
    fatigue_level: str = ""
    compensations: list[str] = Field(default_factory=list)
    caregiver_present: Optional[bool] = None
    flags: list[str] = Field(default_factory=list)
    safety_flags: Optional[list[str]] = None
    notes: str = ""


LABEL_TAXONOMY_V0 = {
    "schema_version": "rayban_pt_label_taxonomy/v0",
    "provider_role": [
        {"value": "physical_therapist", "label": "Physical therapist"},
        {"value": "occupational_therapist", "label": "Occupational therapist"},
        {"value": "pilates_instructor", "label": "Pilates instructor"},
        {"value": "personal_trainer", "label": "Personal trainer"},
        {"value": "caregiver", "label": "Caregiver"},
        {"value": "unspecified", "label": "Unspecified"},
        {"value": "other", "label": "Other"},
    ],
    "action_type": [
        {"value": "observation", "label": "Observation"},
        {"value": "assessment", "label": "Assessment"},
        {"value": "instruction", "label": "Instruction"},
        {"value": "intervention", "label": "Intervention"},
        {"value": "reassessment", "label": "Reassessment"},
        {"value": "home_program", "label": "Home program"},
        {"value": "safety_check", "label": "Safety check"},
    ],
    "assessment_name": [
        {"value": "pain_assessment", "label": "Pain assessment"},
        {"value": "range_of_motion", "label": "Range of motion"},
        {"value": "manual_muscle_test", "label": "Manual muscle test"},
        {"value": "gait_assessment", "label": "Gait assessment"},
        {"value": "balance_assessment", "label": "Balance assessment"},
        {"value": "movement_screen", "label": "Movement screen"},
        {"value": "posture_assessment", "label": "Posture assessment"},
        {"value": "breathing_assessment", "label": "Breathing assessment"},
        {"value": "strength_assessment", "label": "Strength assessment"},
        {"value": "endurance_assessment", "label": "Endurance assessment"},
        {"value": "special_test", "label": "Special test"},
    ],
    "activity_name": [
        {"value": "sit_to_stand", "label": "Sit to stand"},
        {"value": "bridge", "label": "Bridge"},
        {"value": "dead_bug", "label": "Dead bug"},
        {"value": "bird_dog", "label": "Bird dog"},
        {"value": "plank", "label": "Plank"},
        {"value": "side_plank", "label": "Side plank"},
        {"value": "push_up", "label": "Push-up"},
        {"value": "deadlift", "label": "Deadlift"},
        {"value": "row", "label": "Row"},
        {"value": "overhead_press", "label": "Overhead press"},
        {"value": "squat", "label": "Squat"},
        {"value": "lunge", "label": "Lunge"},
        {"value": "step_up", "label": "Step-up"},
        {"value": "clamshell", "label": "Clamshell"},
        {"value": "heel_raise", "label": "Heel raise"},
        {"value": "shoulder_flexion", "label": "Shoulder flexion"},
        {"value": "pelvic_tilt", "label": "Pelvic tilt"},
        {"value": "cat_cow", "label": "Cat-cow"},
        {"value": "roll_down", "label": "Roll down"},
        {"value": "hundred", "label": "The hundred"},
        {"value": "teaser", "label": "Teaser"},
        {"value": "reformer_footwork", "label": "Reformer footwork"},
        {"value": "single_leg_stance", "label": "Single-leg stance"},
        {"value": "tandem_stance", "label": "Tandem stance"},
        {"value": "breathing", "label": "Breathing"},
        {"value": "gait_training", "label": "Gait training"},
        {"value": "balance_training", "label": "Balance training"},
    ],
    "intervention_type": [
        {"value": "movement_correction", "label": "Movement correction"},
        {"value": "joint_mobilization", "label": "Joint mobilization"},
        {"value": "manual_therapy", "label": "Manual therapy"},
        {"value": "massage", "label": "Massage"},
        {"value": "soft_tissue_mobilization", "label": "Soft-tissue mobilization"},
        {"value": "stretching", "label": "Stretching"},
        {"value": "relaxation", "label": "Relaxation"},
        {"value": "neuromuscular_reeducation", "label": "Neuromuscular re-education"},
        {"value": "therapeutic_exercise", "label": "Therapeutic exercise"},
        {"value": "breathing_training", "label": "Breathing training"},
        {"value": "pilates_mat", "label": "Pilates mat"},
        {"value": "pilates_reformer", "label": "Pilates reformer"},
        {"value": "resistance_training", "label": "Resistance training"},
        {"value": "gait_training", "label": "Gait training"},
        {"value": "balance_training", "label": "Balance training"},
        {"value": "taping", "label": "Taping"},
        {"value": "cueing", "label": "Cueing/facilitation"},
        {"value": "orthosis_assistive_device", "label": "Orthosis/assistive device"},
        {"value": "other_intervention", "label": "Other intervention"},
    ],
    "session_type": [
        {"value": "assessment", "label": "Assessment"},
        {"value": "therapeutic_exercise", "label": "Therapeutic exercise"},
        {"value": "neuromotor_training", "label": "Neuromotor training"},
        {"value": "gait_training", "label": "Gait training"},
        {"value": "balance_training", "label": "Balance training"},
        {"value": "caregiver_training", "label": "Caregiver training"},
        {"value": "home_exercise_review", "label": "Home exercise review"},
        {"value": "other", "label": "Other"},
    ],
    "core_task": [
        {"value": "prone_head_control", "label": "Prone head control"},
        {"value": "sitting_balance", "label": "Sitting balance"},
        {"value": "standing_balance", "label": "Standing balance"},
        {"value": "gait_practice", "label": "Gait practice"},
        {"value": "sit_to_stand", "label": "Sit to stand"},
        {"value": "reaching", "label": "Reaching"},
        {"value": "balance_test", "label": "Balance test"},
        {"value": "strength_test", "label": "Strength test"},
        {"value": "movement_screen", "label": "Movement screen"},
        {"value": "breathing_control", "label": "Breathing control"},
        {"value": "strength_training", "label": "Strength training"},
        {"value": "motor_control", "label": "Motor control"},
        {"value": "pilates_control", "label": "Pilates control"},
        {"value": "range_of_motion", "label": "Range of motion"},
        {"value": "caregiver_handling", "label": "Caregiver handling"},
        {"value": "positioning", "label": "Positioning"},
        {"value": "other", "label": "Other"},
    ],
    "body_position": [
        {"value": "supine", "label": "Supine"},
        {"value": "prone", "label": "Prone"},
        {"value": "side_lying", "label": "Side lying"},
        {"value": "sitting", "label": "Sitting"},
        {"value": "quadruped", "label": "Quadruped"},
        {"value": "kneeling", "label": "Kneeling"},
        {"value": "standing", "label": "Standing"},
        {"value": "walking", "label": "Walking"},
        {"value": "unknown", "label": "Unknown"},
    ],
    "assist_level": [
        {"value": "independent", "label": "Independent"},
        {"value": "supervision", "label": "Supervision"},
        {"value": "standby_assist", "label": "Standby assist"},
        {"value": "contact_guard", "label": "Contact guard"},
        {"value": "minimal_assist", "label": "Minimal assist"},
        {"value": "moderate_assist", "label": "Moderate assist"},
        {"value": "maximal_assist", "label": "Maximal assist"},
        {"value": "dependent", "label": "Dependent"},
        {"value": "not_tested", "label": "Not tested"},
    ],
    "performance": [
        {"value": "improved", "label": "Improved"},
        {"value": "stable", "label": "Stable"},
        {"value": "declined", "label": "Declined"},
        {"value": "variable", "label": "Variable"},
        {"value": "unable", "label": "Unable"},
        {"value": "not_observed", "label": "Not observed"},
    ],
    "review_status": [
        {"value": "unreviewed", "label": "Unreviewed"},
        {"value": "reviewed", "label": "Reviewed"},
        {"value": "corrected", "label": "Corrected"},
        {"value": "approved", "label": "Approved"},
        {"value": "rejected", "label": "Rejected"},
    ],
    "tolerance": [
        {"value": "good", "label": "Good"},
        {"value": "fair", "label": "Fair"},
        {"value": "poor", "label": "Poor"},
        {"value": "tolerated", "label": "Tolerated"},
        {"value": "not_observed", "label": "Not observed"},
    ],
    "fatigue_level": [
        {"value": "none", "label": "None"},
        {"value": "mild", "label": "Mild"},
        {"value": "moderate", "label": "Moderate"},
        {"value": "severe", "label": "Severe"},
        {"value": "uncertain", "label": "Uncertain"},
    ],
    "compensations": [
        "right_weight_shift",
        "left_weight_shift",
        "trunk_lateral_flexion",
        "excessive_extension",
        "excessive_flexion",
        "shoulder_elevation",
        "pelvic_rotation",
        "knee_valgus",
        "pelvic_drop",
        "forward_trunk_lean",
        "lumbar_extension",
        "breath_holding",
        "scapular_winging",
        "foot_pronation",
        "cervical_extension",
    ],
    "flags": [
        "fatigue",
        "postural_sway",
        "pain",
        "caregiver_assist",
        "safety_risk",
        "low_attention",
        "equipment_used",
        "needs_review",
    ],
    "presets": [
        {
            "name": "Head Control",
            "session_type": "neuromotor_training",
            "core_task": "prone_head_control",
            "custom_task": "",
            "body_position": "prone",
            "assist_level": "minimal_assist",
            "performance_level": "stable",
            "review_status": "reviewed",
            "flags": ["fatigue"],
            "notes": "Head/trunk control task reviewed by therapist.",
        },
        {
            "name": "Sitting Balance",
            "session_type": "balance_training",
            "core_task": "sitting_balance",
            "custom_task": "",
            "body_position": "sitting",
            "assist_level": "contact_guard",
            "performance_level": "variable",
            "review_status": "reviewed",
            "flags": ["postural_sway"],
            "notes": "Sitting balance quality and safety reviewed.",
        },
        {
            "name": "Gait Practice",
            "session_type": "gait_training",
            "core_task": "gait_practice",
            "custom_task": "",
            "body_position": "walking",
            "assist_level": "moderate_assist",
            "performance_level": "stable",
            "review_status": "reviewed",
            "flags": ["caregiver_assist"],
            "notes": "Gait practice reviewed; assist level confirmed.",
        },
        {
            "name": "Supported Kneeling",
            "session_type": "neuromotor_training",
            "core_task": "other",
            "custom_task": "supported_kneeling",
            "body_position": "kneeling",
            "assist_level": "minimal_assist",
            "performance_level": "stable",
            "review_status": "reviewed",
            "flags": ["caregiver_assist", "needs_review"],
            "notes": "Custom task candidate kept outside core_task until taxonomy review.",
        },
    ],
}


class MergeEventsPayload(BaseModel):
    image_event_id: str
    audio_event_id: str
    patient_name: Optional[str] = None


class ChartUpdatePayload(BaseModel):
    chart: str


class ChartReviewPayload(BaseModel):
    reviewer: Optional[str] = "therapist"
    notes: Optional[str] = ""


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


class HudTokenIssuePayload(BaseModel):
    organization_id: str
    provider_person_id: str
    expires_in_minutes: int = 720
    bridge_url: Optional[str] = None
    app_path: str = "/glass-app/"


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


def _error(status_code: int, code: str, detail: str):
    raise HTTPException(status_code=status_code, detail={"code": code, "message": detail})


def _clean_scope_value(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def _scope_from_request(
    request: Request,
    *,
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    hud_scope: dict[str, str] = {}
    raw_hud_token = request.headers.get("x-hud-token", "") or request.query_params.get("hud_token", "")
    if raw_hud_token:
        try:
            hud_scope = _decode_hud_scope_token(raw_hud_token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail={"code": "INVALID_HUD_SCOPE_TOKEN", "message": str(exc)})
    org_id = (
        _clean_scope_value(owner_org_id)
        or _clean_scope_value(request.headers.get("x-glasspt-org-id"))
        or _clean_scope_value(request.headers.get("x-org-id"))
        or _clean_scope_value(hud_scope.get("organization_id"))
    )
    provider_person_id = (
        _clean_scope_value(owner_provider_person_id)
        or _clean_scope_value(request.headers.get("x-glasspt-provider-person-id"))
        or _clean_scope_value(request.headers.get("x-provider-person-id"))
        or _clean_scope_value(hud_scope.get("provider_person_id"))
    )
    return org_id, provider_person_id


def _validate_upload_size(content: bytes, kind: str):
    size_mb = len(content) / (1024 * 1024)
    if size_mb > UPLOAD_MAX_MB:
        _error(413, "UPLOAD_TOO_LARGE", f"{kind} 파일 용량이 너무 큽니다. max={UPLOAD_MAX_MB}MB, current={size_mb:.1f}MB")


def _touch_async_result(event_id: str, payload: dict):
    ASYNC_RESULTS[event_id] = {
        **payload,
        "updated_at": datetime.utcnow().isoformat(),
    }
    _prune_async_results()


def _normalize_error(exc: Exception):
    if isinstance(exc, TimeoutError):
        return "PROCESS_TIMEOUT", str(exc), True
    if isinstance(exc, sqlite3.Error):
        return "DB_ERROR", str(exc), True
    if isinstance(exc, HTTPException):
        if isinstance(exc.detail, dict):
            return exc.detail.get("code", "HTTP_ERROR"), exc.detail.get("message", str(exc.detail)), exc.status_code >= 500
        return "HTTP_ERROR", str(exc.detail), exc.status_code >= 500
    return "PROCESSING_ERROR", str(exc), True


def _audit_log(event_id: Optional[str], level: str, message: str):
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), event_id, level, message),
            )
            conn.commit()
    except Exception as e:
        logger.warning("audit log failed event_id=%s err=%s", event_id, e)


def _run_with_timeout(fn, timeout_seconds: int, *args, **kwargs):
    fut = EXECUTOR.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"processing exceeded timeout ({timeout_seconds}s)")


def _prune_async_results():
    if not ASYNC_RESULTS:
        return

    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=ASYNC_RESULT_TTL_MINUTES)
    expired = []
    for k, v in ASYNC_RESULTS.items():
        t = v.get("updated_at")
        if not t:
            continue
        try:
            ts = datetime.fromisoformat(t)
        except Exception:
            continue
        if ts < cutoff:
            expired.append(k)
    for k in expired:
        ASYNC_RESULTS.pop(k, None)

    if len(ASYNC_RESULTS) > ASYNC_RESULT_MAX_ITEMS:
        keys_sorted = sorted(
            ASYNC_RESULTS.keys(),
            key=lambda x: ASYNC_RESULTS[x].get("updated_at", ""),
        )
        to_drop = len(ASYNC_RESULTS) - ASYNC_RESULT_MAX_ITEMS
        for k in keys_sorted[:to_drop]:
            ASYNC_RESULTS.pop(k, None)


def _ensure_runtime_schema(conn: sqlite3.Connection):
    ensure_visit_session_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chart_reviews (
            event_id TEXT PRIMARY KEY,
            reviewer TEXT NOT NULL DEFAULT 'therapist',
            notes TEXT NOT NULL DEFAULT '',
            quality_score INTEGER NOT NULL DEFAULT 0,
            quality_level TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_chart_reviews_reviewed_at ON chart_reviews(reviewed_at);
        CREATE TABLE IF NOT EXISTS moai_sync_jobs (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            trigger_reason TEXT NOT NULL,
            operation_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            last_plan_summary TEXT NOT NULL DEFAULT '{}',
            last_result_summary TEXT NOT NULL DEFAULT '{}',
            last_attempted_at TEXT,
            synced_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_moai_sync_jobs_status_updated_at ON moai_sync_jobs(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_moai_sync_jobs_event_id ON moai_sync_jobs(event_id);
        CREATE TABLE IF NOT EXISTS hud_candidates (
            id TEXT PRIMARY KEY,
            encounter_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            subject_person_id TEXT NOT NULL,
            provider_person_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            test TEXT NOT NULL DEFAULT '',
            side TEXT NOT NULL DEFAULT '',
            value TEXT NOT NULL DEFAULT '',
            symptom TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'rayban_meta_display',
            status TEXT NOT NULL DEFAULT 'candidate',
            review_status TEXT NOT NULL DEFAULT 'auto_extracted',
            confidence REAL,
            source_text TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            reviewer_person_id TEXT NOT NULL DEFAULT '',
            discarded_reason TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_hud_candidates_encounter_updated_at ON hud_candidates(encounter_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_hud_candidates_status_updated_at ON hud_candidates(status, updated_at);
        CREATE TABLE IF NOT EXISTS capture_events (
            id TEXT PRIMARY KEY,
            visit_session_id TEXT,
            encounter_id TEXT,
            organization_id TEXT,
            provider_person_id TEXT,
            subject_person_id TEXT,
            source_media_id TEXT,
            source_event_id TEXT,
            source_type TEXT NOT NULL,
            event_type TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            start_ms INTEGER,
            end_ms INTEGER,
            confidence REAL,
            status TEXT NOT NULL DEFAULT 'draft',
            payload_json TEXT NOT NULL DEFAULT '{}',
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_capture_events_visit_created_at
          ON capture_events(visit_session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_capture_events_encounter_created_at
          ON capture_events(encounter_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_capture_events_source_event_id
          ON capture_events(source_event_id);
        """
    )
    label_columns = {row[1] for row in conn.execute("PRAGMA table_info(rehab_labels)").fetchall()}
    label_column_specs = {
        "provider_role": "TEXT NOT NULL DEFAULT 'unspecified'",
        "action_type": "TEXT NOT NULL DEFAULT 'observation'",
        "custom_task": "TEXT NOT NULL DEFAULT ''",
        "body_position": "TEXT NOT NULL DEFAULT ''",
        "review_status": "TEXT NOT NULL DEFAULT 'reviewed'",
        "reviewer_person_id": "TEXT NOT NULL DEFAULT ''",
        "usable_for_training": "INTEGER NOT NULL DEFAULT 0",
        "label_confidence": "REAL",
        "repetition_count": "INTEGER",
        "hold_duration_seconds": "REAL",
        "tolerance": "TEXT NOT NULL DEFAULT ''",
        "fatigue_level": "TEXT NOT NULL DEFAULT ''",
        "compensations": "TEXT NOT NULL DEFAULT '[]'",
        "caregiver_present": "INTEGER",
    }
    for column_name, column_spec in label_column_specs.items():
        if column_name not in label_columns:
            conn.execute(f"ALTER TABLE rehab_labels ADD COLUMN {column_name} {column_spec}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rehab_labels_review_status_updated_at "
        "ON rehab_labels(review_status, updated_at)"
    )
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "owner_org_id" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN owner_org_id TEXT")
    if "owner_provider_person_id" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN owner_provider_person_id TEXT")
    if "subject_person_id" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN subject_person_id TEXT")
    if "physio_client_id" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN physio_client_id TEXT")
    if "physio_session_id" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN physio_session_id TEXT")
    consent_columns = {row[1] for row in conn.execute("PRAGMA table_info(patient_consents)").fetchall()}
    if "owner_org_id" not in consent_columns:
        conn.execute("ALTER TABLE patient_consents ADD COLUMN owner_org_id TEXT NOT NULL DEFAULT ''")
    if "owner_provider_person_id" not in consent_columns:
        conn.execute("ALTER TABLE patient_consents ADD COLUMN owner_provider_person_id TEXT NOT NULL DEFAULT ''")
    if "subject_person_id" not in consent_columns:
        conn.execute("ALTER TABLE patient_consents ADD COLUMN subject_person_id TEXT NOT NULL DEFAULT ''")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_events_owner_org_created_at ON events(owner_org_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_owner_provider_created_at ON events(owner_provider_person_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_subject_created_at ON events(subject_person_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_physio_client_created_at ON events(physio_client_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_physio_session_created_at ON events(physio_session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_patient_consents_identity_scope
          ON patient_consents(owner_org_id, owner_provider_person_id, subject_person_id, scope, revoked_at, created_at);
        """
    )


def _conn():
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail="DB not initialized. Run: python init_db.py")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_runtime_schema(conn)
    return conn


CAPTURE_EVENT_STATUSES = {"draft", "edited", "approved", "rejected"}
CAPTURE_EVENT_SOURCE_TYPES = {"audio", "video", "pose", "therapist_tag", "transcript", "system"}
CAPTURE_ORIGIN_ALIASES = {
    "rayban_dat_camera": "rayban_dat_camera",
    "rayban-camera": "rayban_dat_camera",
    "rayban_hfp_microphone": "rayban_hfp_microphone",
    "rayban-microphone": "rayban_hfp_microphone",
    "iphone_camera": "iphone_camera",
    "iphone-camera": "iphone_camera",
    "ios_demo_synthetic": "ios_demo_synthetic",
    "ios_demo_autotest": "ios_demo_autotest",
}


def _capture_origin_from_source(source: Optional[str]) -> Optional[str]:
    """Normalize trusted media source labels into capture-event provenance."""

    normalized = (source or "").strip().lower()
    return CAPTURE_ORIGIN_ALIASES.get(normalized)


def _backfill_capture_origin(
    conn: sqlite3.Connection,
    event: dict,
    capture_origin: Optional[str],
) -> dict:
    """Repair provenance on idempotent readback of older capture candidates."""

    if not capture_origin or event["payload"].get("capture_origin"):
        return event
    payload = {**event["payload"], "capture_origin": capture_origin}
    updated_at = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE capture_events SET payload_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), updated_at, event["id"]),
    )
    event["payload"] = payload
    event["updated_at"] = updated_at
    return event


def _capture_event_from_row(row: tuple) -> dict:
    try:
        payload = json.loads(row[15] or "{}")
    except (TypeError, ValueError):
        payload = {}
    if isinstance(payload, dict):
        action_type = payload.get("action_type")
        if not isinstance(action_type, str) or action_type not in {
            "observation",
            "assessment",
            "instruction",
            "intervention",
            "reassessment",
            "home_program",
            "safety_check",
        }:
            payload["action_type"] = capture_action_type(str(row[10] or ""))
    return {
        "id": row[0],
        "visit_session_id": row[1],
        "encounter_id": row[2],
        "organization_id": row[3],
        "provider_person_id": row[4],
        "subject_person_id": row[5],
        "source_media_id": row[6],
        "source_event_id": row[7],
        "source_type": row[8],
        "event_type": row[9],
        "candidate_type": row[10],
        "start_ms": row[11],
        "end_ms": row[12],
        "confidence": row[13],
        "status": row[14],
        "payload": payload if isinstance(payload, dict) else {},
        "reviewed_by": row[16],
        "reviewed_at": row[17],
        "created_at": row[18],
        "updated_at": row[19],
    }


def _capture_event_select() -> str:
    return (
        "SELECT id, visit_session_id, encounter_id, organization_id, provider_person_id, "
        "subject_person_id, source_media_id, source_event_id, source_type, event_type, "
        "candidate_type, start_ms, end_ms, confidence, status, payload_json, reviewed_by, "
        "reviewed_at, created_at, updated_at FROM capture_events"
    )


def _create_transcript_capture_events(
    conn: sqlite3.Connection,
    *,
    text: str,
    visit_session_id: Optional[str] = None,
    encounter_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    provider_person_id: Optional[str] = None,
    provider_role: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    source_media_id: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    confidence: Optional[float] = None,
    capture_origin: Optional[str] = None,
    derived_from: str = "transcript",
) -> list[dict]:
    candidates = extract_transcript_capture_candidates(text, provider_role=provider_role)
    if not candidates:
        return []

    clean_source_event_id = _clean_scope_value(source_event_id)
    existing_rows = conn.execute(
        f"{_capture_event_select()} WHERE encounter_id IS ? AND source_event_id IS ?",
        (encounter_id, clean_source_event_id),
    ).fetchall()
    existing_by_key = {}
    for row in existing_rows:
        event = _backfill_capture_origin(
            conn,
            _capture_event_from_row(row),
            capture_origin,
        )
        key = event["payload"].get("extraction_key")
        if isinstance(key, str) and key:
            existing_by_key[key] = event

    created: list[dict] = []
    for index, candidate in enumerate(candidates):
        raw_payload = candidate.get("payload") or {}
        # Preserve structured semantic fields (lists, numbers, and the nested
        # semantic snapshot) through capture_events JSON. Older candidates are
        # still string-valued, so this remains backward-compatible.
        payload = (
            {str(key): value for key, value in raw_payload.items()}
            if isinstance(raw_payload, dict)
            else {}
        )
        source_text = payload.get("source_text", "")
        extraction_key = hashlib.sha256(
            f"{clean_source_event_id or ''}:{index}:{candidate.get('candidate_type')}:{source_text}".encode("utf-8")
        ).hexdigest()
        payload.update(
            {
                "extraction_key": extraction_key,
                "extractor_version": TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION,
                "derived_from": derived_from,
            }
        )
        if provider_role:
            payload.setdefault("provider_role", provider_role)
        if capture_origin:
            payload.setdefault("capture_origin", capture_origin)
        if extraction_key in existing_by_key:
            created.append(existing_by_key[extraction_key])
            continue

        candidate_confidence = float(candidate.get("confidence") or 0.5)
        effective_confidence = (
            min(candidate_confidence, max(0.0, min(1.0, float(confidence))))
            if confidence is not None
            else candidate_confidence
        )
        event_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO capture_events (
              id, visit_session_id, encounter_id, organization_id, provider_person_id,
              subject_person_id, source_media_id, source_event_id, source_type, event_type,
              candidate_type, start_ms, end_ms, confidence, status, payload_json,
              reviewed_by, reviewed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'transcript', ?, ?, ?, ?, ?, 'draft', ?, NULL, NULL, ?, ?)
            """,
            (
                event_id,
                visit_session_id,
                encounter_id,
                organization_id,
                provider_person_id,
                subject_person_id,
                source_media_id,
                clean_source_event_id,
                candidate["event_type"],
                candidate["candidate_type"],
                start_ms,
                end_ms,
                effective_confidence,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )
        row = conn.execute(f"{_capture_event_select()} WHERE id = ?", (event_id,)).fetchone()
        if row:
            event = _capture_event_from_row(row)
            existing_by_key[extraction_key] = event
            created.append(event)
    return created


def _create_pose_capture_events(
    conn: sqlite3.Connection,
    *,
    candidates: list[dict],
    visit_session_id: Optional[str] = None,
    encounter_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    provider_person_id: Optional[str] = None,
    provider_role: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    source_media_id: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    capture_origin: Optional[str] = None,
) -> list[dict]:
    """Persist MediaPipe measurements as idempotent provider-review candidates."""

    if not candidates:
        return []

    clean_source_event_id = _clean_scope_value(source_event_id)
    existing_rows = conn.execute(
        f"{_capture_event_select()} WHERE encounter_id IS ? AND source_event_id IS ? AND source_type = 'pose'",
        (encounter_id, clean_source_event_id),
    ).fetchall()
    existing_by_key: dict[str, dict] = {}
    for row in existing_rows:
        event = _backfill_capture_origin(
            conn,
            _capture_event_from_row(row),
            capture_origin,
        )
        key = event["payload"].get("extraction_key")
        if isinstance(key, str) and key:
            existing_by_key[key] = event

    created: list[dict] = []
    for index, candidate in enumerate(candidates):
        payload = dict(candidate.get("payload") or {})
        candidate_key = json.dumps(
            {
                "candidate_type": candidate.get("candidate_type"),
                "event_type": candidate.get("event_type"),
                "metric_id": payload.get("metric_id"),
                "side": payload.get("side"),
                "label": payload.get("label"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        extraction_key = hashlib.sha256(
            f"{clean_source_event_id or ''}:pose:{index}:{candidate_key}".encode("utf-8")
        ).hexdigest()
        payload.update(
            {
                "extraction_key": extraction_key,
                "extractor_version": POSE_EXTRACTOR_VERSION,
                "derived_from": "video_pose",
                "review_required": "true",
            }
        )
        if provider_role:
            payload.setdefault("provider_role", provider_role)
        if capture_origin:
            payload.setdefault("capture_origin", capture_origin)
        if extraction_key in existing_by_key:
            created.append(existing_by_key[extraction_key])
            continue

        event_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        candidate_confidence = float(candidate.get("confidence") or 0.5)
        conn.execute(
            """
            INSERT INTO capture_events (
              id, visit_session_id, encounter_id, organization_id, provider_person_id,
              subject_person_id, source_media_id, source_event_id, source_type, event_type,
              candidate_type, start_ms, end_ms, confidence, status, payload_json,
              reviewed_by, reviewed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pose', ?, ?, ?, ?, ?, 'draft', ?, NULL, NULL, ?, ?)
            """,
            (
                event_id,
                visit_session_id,
                encounter_id,
                organization_id,
                provider_person_id,
                subject_person_id,
                source_media_id,
                clean_source_event_id,
                candidate["event_type"],
                candidate["candidate_type"],
                start_ms,
                end_ms,
                candidate_confidence,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )
        row = conn.execute(f"{_capture_event_select()} WHERE id = ?", (event_id,)).fetchone()
        if row:
            event = _capture_event_from_row(row)
            existing_by_key[extraction_key] = event
            created.append(event)
    return created


DEFAULT_CONSENT_TEXT = (
    "환자 또는 보호자가 치료 기록을 위해 사진/영상/음성/텍스트를 캡처하고, "
    "로컬 서버에서 분석 및 차트 생성을 수행하며, 필요한 기간 동안 저장하는 것에 동의했습니다."
)


def _latest_patient_consent(
    conn: sqlite3.Connection,
    patient_name: str,
    scope: str = "capture_analysis_storage",
    *,
    owner_org_id: str,
    owner_provider_person_id: str,
    subject_person_id: str,
):
    return conn.execute(
        """
        SELECT id, patient_name, scope, consent_text, granted_by, created_at,
               owner_org_id, owner_provider_person_id, subject_person_id
        FROM patient_consents
        WHERE patient_name = ? AND scope = ?
          AND owner_org_id = ? AND owner_provider_person_id = ? AND subject_person_id = ?
          AND revoked_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (
            patient_name.strip(),
            scope.strip() or "capture_analysis_storage",
            owner_org_id.strip(),
            owner_provider_person_id.strip(),
            subject_person_id.strip(),
        ),
    ).fetchone()


def _require_patient_consent(
    event_type: str,
    patient_name: str,
    *,
    owner_org_id: Optional[str],
    owner_provider_person_id: Optional[str],
    subject_person_id: Optional[str],
) -> Optional[str]:
    if not REQUIRE_PATIENT_CONSENT or event_type not in {"audio", "image", "video", "text"}:
        return None

    name = (patient_name or "").strip()
    if not name:
        _error(428, "PATIENT_CONSENT_REQUIRED", "환자 동의 확인을 위해 patient_name이 필요합니다.")
    org_id = (owner_org_id or "").strip()
    provider_id = (owner_provider_person_id or "").strip()
    subject_id = (subject_person_id or "").strip()
    if not org_id or not provider_id or not subject_id:
        _error(428, "CONSENT_IDENTITY_REQUIRED", "조직, 치료사, 환자 person ID가 있어야 동의를 확인할 수 있습니다.")

    with _conn() as conn:
        row = _latest_patient_consent(
            conn,
            name,
            owner_org_id=org_id,
            owner_provider_person_id=provider_id,
            subject_person_id=subject_id,
        )
    if not row:
        _error(428, "PATIENT_CONSENT_REQUIRED", "활성 capture/analysis/storage 동의 기록이 필요합니다.")
    return row[0]


def _stage_raw_media_if_consent_active(
    source_path: Path,
    stage: RawMediaStage,
    *,
    owner_org_id: Optional[str],
    owner_provider_person_id: Optional[str],
    subject_person_id: Optional[str],
) -> Optional[Path]:
    consent_id = stage.consent_id.strip()
    identity = (
        (owner_org_id or "").strip(),
        (owner_provider_person_id or "").strip(),
        (subject_person_id or "").strip(),
    )
    if not consent_id or not all(identity):
        source_path.unlink(missing_ok=True)
        return None

    with CONSENT_MEDIA_LOCK:
        with _conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT 1 FROM patient_consents
                WHERE id = ? AND owner_org_id = ? AND owner_provider_person_id = ?
                  AND subject_person_id = ? AND scope = 'capture_analysis_storage'
                  AND revoked_at IS NULL
                """,
                (consent_id, *identity),
            ).fetchone()
            if not active:
                conn.rollback()
                source_path.unlink(missing_ok=True)
                return None
            staged_path = stage_raw_media(source_path, RAW_MEDIA_DIR, stage)
            conn.commit()
            return staged_path


def _pilot_metadata_gaps(
    *,
    event_type: str,
    patient_name: str,
    owner_org_id: Optional[str],
    owner_provider_person_id: Optional[str],
    subject_person_id: Optional[str],
    physio_client_id: Optional[str],
    physio_session_id: Optional[str],
) -> list[str]:
    if event_type == "command":
        return []
    gaps: list[str] = []
    if not _clean_scope_value(patient_name):
        gaps.append("patient_name")
    if not _clean_scope_value(owner_org_id):
        gaps.append("owner_org_id")
    if not _clean_scope_value(owner_provider_person_id):
        gaps.append("owner_provider_person_id")
    if not (_clean_scope_value(subject_person_id) or _clean_scope_value(physio_client_id)):
        gaps.append("physio_client_id_or_subject_person_id")
    if not _clean_scope_value(physio_session_id):
        gaps.append("physio_session_id_or_encounter_id")
    return gaps


def _require_pilot_capture_metadata(
    *,
    event_type: str,
    patient_name: str,
    owner_org_id: Optional[str],
    owner_provider_person_id: Optional[str],
    subject_person_id: Optional[str],
    physio_client_id: Optional[str],
    physio_session_id: Optional[str],
) -> None:
    if not PILOT_CAPTURE_MODE:
        return
    gaps = _pilot_metadata_gaps(
        event_type=event_type,
        patient_name=patient_name,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
        subject_person_id=subject_person_id,
        physio_client_id=physio_client_id,
        physio_session_id=physio_session_id,
    )
    if gaps:
        _error(
            422,
            "PILOT_METADATA_REQUIRED",
            "Pilot capture requires canonical metadata: " + ", ".join(gaps),
        )


def _json_list(value: Optional[str]) -> list:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _optional_bool_from_db(value) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _get_label_by_event_id(conn: sqlite3.Connection, event_id: str):
    row = conn.execute(
        """
        SELECT event_id, provider_role, action_type, session_type, core_task, custom_task, body_position,
               assist_level, performance, review_status, reviewer_person_id,
               usable_for_training, label_confidence, repetition_count,
               hold_duration_seconds, tolerance, fatigue_level, compensations,
               caregiver_present, flags, notes, updated_at
        FROM rehab_labels
        WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "event_id": row[0],
        "provider_role": row[1] or "unspecified",
        "action_type": row[2] or "observation",
        "session_type": row[3],
        "core_task": row[4],
        "custom_task": row[5] or "",
        "body_position": row[6] or "",
        "assist_level": row[7],
        "performance": row[8],
        "performance_level": row[8],
        "review_status": row[9] or "reviewed",
        "reviewer_person_id": row[10] or "",
        "usable_for_training": bool(row[11]),
        "label_confidence": row[12],
        "repetition_count": row[13],
        "hold_duration_seconds": row[14],
        "tolerance": row[15] or "",
        "fatigue_level": row[16] or "",
        "compensations": _json_list(row[17]),
        "caregiver_present": _optional_bool_from_db(row[18]),
        "flags": _json_list(row[19]),
        "safety_flags": _json_list(row[19]),
        "notes": row[20] or "",
        "updated_at": row[21],
    }


def _get_chart_review_by_event_id(conn: sqlite3.Connection, event_id: str):
    row = conn.execute(
        """
        SELECT event_id, reviewer, notes, quality_score, quality_level, reviewed_at
        FROM chart_reviews
        WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "event_id": row[0],
        "reviewer": row[1],
        "notes": row[2] or "",
        "quality_score": int(row[3] or 0),
        "quality_level": row[4] or "",
        "reviewed_at": row[5],
    }


def _get_latest_soap_by_event_id(conn: sqlite3.Connection, event_id: str):
    row = conn.execute(
        "SELECT s, o, a, p, created_at FROM soap_notes WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "s": row[0] or "",
        "o": row[1] or "",
        "a": row[2] or "",
        "p": row[3] or "",
        "created_at": row[4],
    }


def _read_chart_export(event_id: str) -> tuple[str, Optional[dict]]:
    chart_path = CHART_DIR / f"{event_id}_11.txt"
    if not chart_path.exists():
        return "", None
    chart = chart_path.read_text(encoding="utf-8")
    return chart, _chart_quality(chart)


def _list_event_artifacts(event_id: str) -> list[dict]:
    artifacts: list[dict] = []
    for path in sorted(MASKED_DIR.glob(f"{event_id}*")):
        if not path.is_file():
            continue
        kind = "masked_image" if path.name.endswith("_masked.jpg") else "masked_video_frame"
        artifacts.append(
            {
                "kind": kind,
                "filename": path.name,
                "content_type": "image/jpeg",
                "file_size_bytes": path.stat().st_size,
                "download_path": f"/masked-files/{path.name}",
            }
        )
    artifacts.extend(list_raw_media_artifacts(RAW_MEDIA_DIR, event_id))
    return artifacts


def _get_event_snapshot(event_id: str) -> tuple[dict, Optional[dict], Optional[dict], Optional[dict], list[dict]]:
    with _conn() as conn:
        ev = conn.execute(
            "SELECT id, source, event_type, raw_text, intent, status, created_at, patient_name, owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id "
            "FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")
        soap_row = conn.execute(
            "SELECT s, o, a, p, created_at FROM soap_notes WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        label = _get_label_by_event_id(conn, event_id)
        review = _get_chart_review_by_event_id(conn, event_id)

    event_obj = {
        "id": ev[0],
        "source": ev[1],
        "event_type": ev[2],
        "raw_text": ev[3],
        "intent": ev[4],
        "status": ev[5],
        "created_at": ev[6],
        "patient_name": ev[7],
        "owner_org_id": ev[8],
        "owner_provider_person_id": ev[9],
        "subject_person_id": ev[10],
        "physio_client_id": ev[11],
        "physio_session_id": ev[12],
    }
    soap = None
    if soap_row:
        soap = {"s": soap_row[0], "o": soap_row[1], "a": soap_row[2], "p": soap_row[3], "created_at": soap_row[4]}
    return event_obj, soap, label, review, _list_event_artifacts(event_id)


def _build_moai_bundle_for_event(
    event_id: str,
    *,
    subject_person_id: Optional[str] = None,
    provider_person_id: Optional[str] = None,
    encounter_id: Optional[str] = None,
    capture_device: str = "rayban",
    resolve_identity: bool = False,
) -> dict:
    event_obj, soap_obj, label, review, artifacts = _get_event_snapshot(event_id)
    identity_resolution = None
    if resolve_identity:
        identity_resolution = resolve_moai_identity(
            event=event_obj,
            subject_person_id=subject_person_id,
            provider_person_id=provider_person_id,
            encounter_id=encounter_id,
        )
        subject_person_id = identity_resolution.subject_person_id
        provider_person_id = identity_resolution.provider_person_id
        encounter_id = identity_resolution.encounter_id
        event_obj["owner_org_id"] = identity_resolution.organization_id or event_obj.get("owner_org_id")
        event_obj["subject_person_id"] = identity_resolution.subject_person_id or event_obj.get("subject_person_id")
        event_obj["physio_client_id"] = identity_resolution.physio_client_id or event_obj.get("physio_client_id")

    bundle = build_moai_export_bundle(
        event=event_obj,
        soap=soap_obj,
        label=label,
        review=review,
        artifacts=artifacts,
        subject_person_id=subject_person_id,
        provider_person_id=provider_person_id,
        encounter_id=encounter_id,
        capture_device=capture_device,
    )
    if identity_resolution:
        bundle.setdefault("context", {})["identity_resolution"] = identity_resolution.as_dict()
    return bundle


def _hud_candidate_from_row(row) -> dict:
    return {
        "id": row[0],
        "encounter_id": row[1],
        "organization_id": row[2],
        "subject_person_id": row[3],
        "provider_person_id": row[4],
        "event_type": row[5],
        "test": row[6] or "",
        "side": row[7] or "",
        "value": row[8] or "",
        "symptom": row[9] or "",
        "source": row[10] or "rayban_meta_display",
        "status": row[11],
        "review_status": row[12],
        "confidence": row[13],
        "source_text": row[14] or "",
        "payload": _safe_json_loads(row[15], {}),
        "reviewer_person_id": row[16] or "",
        "discarded_reason": row[17] or "",
        "reviewed_at": row[18],
        "created_at": row[19],
        "updated_at": row[20],
        "observation_status": "final" if row[11] == "confirmed_by_provider" else "preliminary",
    }


def _get_hud_candidate(conn: sqlite3.Connection, candidate_id: str) -> dict:
    row = conn.execute(
        """
        SELECT id, encounter_id, organization_id, subject_person_id, provider_person_id,
               event_type, test, side, value, symptom, source, status, review_status,
               confidence, source_text, payload_json, reviewer_person_id,
               discarded_reason, reviewed_at, created_at, updated_at
        FROM hud_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="hud candidate not found")
    return _hud_candidate_from_row(row)


def _hud_candidate_plan(candidate: dict) -> dict:
    return build_moai_write_plan(build_hud_moai_bundle_from_candidate(candidate))


def _extract_side_from_transcript(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(left|lt|lft)\b", lower) or "좌측" in text or "왼쪽" in text or "좌 " in text:
        return "left"
    if re.search(r"\b(right|rt)\b", lower) or "우측" in text or "오른쪽" in text or "우 " in text:
        return "right"
    if "양측" in text or "bilateral" in lower or "both" in lower:
        return "bilateral"
    return ""


def _extract_test_from_transcript(text: str) -> str:
    lower = text.lower()
    if re.search(r"\bslr\b", lower) or "straight leg raise" in lower or "하지직거상" in text:
        return "SLR"
    if "slump" in lower or "슬럼프" in text:
        return "Slump"
    if re.search(r"\bodi\b", lower) or "oswestry" in lower or "오스웨스트리" in text:
        return "ODI"
    if re.search(r"\bnprs\b", lower) or "통증점수" in text or "통증 점수" in text:
        return "NPRS"
    return ""


def _extract_value_from_transcript(text: str, test: str) -> str:
    lower = text.lower()
    degree_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:도|deg(?:ree)?s?|°)", lower)
    if degree_match:
        value = degree_match.group(1)
        return f"{value} degrees"
    score_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|out of)\s*(10|100)", lower)
    if score_match:
        return f"{score_match.group(1)}/{score_match.group(2)}"
    if test == "ODI":
        odi_match = re.search(r"(?:odi|오스웨스트리)[^\d]*(\d+(?:\.\d+)?)", lower)
        if odi_match:
            return odi_match.group(1)
    if re.search(r"\bpositive\b", lower) or "양성" in text:
        return "positive"
    if re.search(r"\bnegative\b", lower) or "음성" in text:
        return "negative"
    return ""


def _extract_symptom_from_transcript(text: str) -> str:
    lower = text.lower()
    if "posterior thigh pain" in lower:
        return "posterior thigh pain"
    if "radiating pain" in lower:
        return "radiating pain"
    if "hamstring tightness" in lower:
        return "hamstring tightness"
    symptom_patterns = [
        r"(posterior\s+thigh\s+pain.*)$",
        r"(radiating\s+pain.*)$",
        r"(hamstring\s+tightness.*)$",
        r"(허벅지\s*뒤쪽\s*통증.*)$",
        r"(방사통.*)$",
        r"(저림.*)$",
        r"(당김.*)$",
    ]
    for pattern in symptom_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _short_lens_text(match.group(1), limit=80)
    if "통증" in text:
        idx = text.find("통증")
        start = max(0, idx - 12)
        end = min(len(text), idx + 24)
        return _short_lens_text(text[start:end], limit=80)
    return ""


def _extract_hud_candidate_from_transcript(text: str) -> Optional[dict]:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return None
    test = _extract_test_from_transcript(clean)
    if not test:
        return None
    side = _extract_side_from_transcript(clean)
    value = _extract_value_from_transcript(clean, test)
    symptom = _extract_symptom_from_transcript(clean)
    event_type = "test_result" if test in {"SLR", "Slump", "ODI", "NPRS"} else "observation"
    return {
        "event_type": event_type,
        "test": test,
        "side": side,
        "value": value,
        "symptom": symptom,
        "source_text": clean,
    }


def _hud_candidate_micro_card(candidate: Optional[dict]) -> Optional[dict]:
    if not candidate:
        return None
    test = str(candidate.get("test") or candidate.get("event_type") or "Candidate").strip()
    side = str(candidate.get("side") or "").strip()
    value = str(candidate.get("value") or "").strip()
    symptom = str(candidate.get("symptom") or "").strip()
    source_text = str(candidate.get("source_text") or "").strip()
    parts = [
        test,
        side,
        value,
    ]
    title = " / ".join(str(part) for part in parts if part)
    lines = [
        _short_lens_text(title or "Candidate", limit=44),
        _short_lens_text(symptom or source_text or "승인 대기", limit=54),
    ]
    if candidate.get("confidence") is not None:
        try:
            confidence = round(float(candidate["confidence"]) * 100)
            lines.append(f"AI confidence {confidence}%")
        except Exception:
            pass
    return {
        "id": candidate.get("id"),
        "encounter_id": candidate.get("encounter_id"),
        "title": _short_lens_text(title or "Candidate", limit=34),
        "body": _short_lens_text(symptom or source_text or "승인 대기", limit=54),
        "lines": [line for line in lines if line],
        "primary_action": "approve_candidate",
        "secondary_action": "discard_candidate",
        "status": candidate.get("status"),
        "review_status": candidate.get("review_status"),
        "source": candidate.get("source"),
        "lens_safe": True,
    }


def _latest_hud_candidate_for_encounter(
    conn: sqlite3.Connection,
    encounter_id: str,
    *,
    status: str = "candidate",
) -> Optional[dict]:
    if not encounter_id:
        return None
    row = conn.execute(
        """
        SELECT id, encounter_id, organization_id, subject_person_id, provider_person_id,
               event_type, test, side, value, symptom, source, status, review_status,
               confidence, source_text, payload_json, reviewer_person_id,
               discarded_reason, reviewed_at, created_at, updated_at
        FROM hud_candidates
        WHERE encounter_id = ?
          AND (? = 'all' OR status = ?)
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (encounter_id, status, status),
    ).fetchone()
    return _hud_candidate_from_row(row) if row else None


def _active_hud_candidate(conn: sqlite3.Connection, *, status: str = "candidate") -> Optional[dict]:
    with _glass_lock:
        explicit = _glass_state.get("active_hud_candidate") or {}
    if isinstance(explicit, dict) and explicit.get("id"):
        try:
            candidate = _get_hud_candidate(conn, str(explicit["id"]))
            if status == "all" or candidate.get("status") == status:
                return candidate
        except HTTPException:
            pass
    session_id = _active_visit_session_id_from_hud()
    if session_id:
        session = get_visit_session(conn, session_id)
        if session:
            candidate = _latest_hud_candidate_for_encounter(conn, str(session.get("encounter_id") or ""), status=status)
            if candidate:
                return candidate
    return None


def _set_active_hud_candidate(candidate: Optional[dict]) -> None:
    with _glass_lock:
        _glass_state["active_hud_candidate"] = _hud_candidate_micro_card(candidate)
        _glass_state["updated_at"] = datetime.utcnow().isoformat() + "Z"


def _safe_json_loads(value: Optional[str], default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _summarize_moai_plan_for_job(plan: dict) -> dict:
    context = dict(plan.get("context") or {})
    if context.get("identity_hints"):
        context["identity_hints_present"] = True
        context.pop("identity_hints", None)
    return {
        "context": context,
        "summary": plan.get("summary") or {},
        "operations": [
            {
                "target_table": op.get("target_table"),
                "action": op.get("action"),
                "on_conflict": op.get("on_conflict"),
                "warnings": op.get("warnings") or [],
            }
            for op in plan.get("operations") or []
        ],
        "skipped": plan.get("skipped") or [],
    }


def _summarize_moai_write_result_for_job(result: Optional[dict]) -> dict:
    if not result:
        return {}
    return {
        "summary": result.get("summary") or {},
        "results": [
            {
                "target_table": item.get("target_table"),
                "action": item.get("action"),
                "status_code": item.get("status_code"),
                "ok": item.get("ok"),
            }
            for item in result.get("results") or []
        ],
        "skipped": result.get("skipped") or [],
    }


def _moai_sync_job_from_row(row) -> dict:
    return {
        "id": row[0],
        "event_id": row[1],
        "status": row[2],
        "trigger_reason": row[3],
        "operation_count": row[4],
        "skipped_count": row[5],
        "attempts": row[6],
        "last_error": row[7],
        "last_plan_summary": _safe_json_loads(row[8], {}),
        "last_result_summary": _safe_json_loads(row[9], {}),
        "last_attempted_at": row[10],
        "synced_at": row[11],
        "created_at": row[12],
        "updated_at": row[13],
    }


def _enqueue_moai_sync_job(conn: sqlite3.Connection, event_id: str, trigger_reason: str) -> None:
    conn.execute(
        """
        INSERT INTO moai_sync_jobs (id, event_id, status, trigger_reason, updated_at)
        VALUES (?, ?, 'pending', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(event_id) DO UPDATE SET
          status='pending',
          trigger_reason=excluded.trigger_reason,
          last_error=NULL,
          updated_at=CURRENT_TIMESTAMP
        """,
        (str(uuid.uuid4()), event_id, trigger_reason),
    )


def _record_moai_sync_job_attempt(
    event_id: str,
    *,
    status: str,
    plan: Optional[dict] = None,
    result: Optional[dict] = None,
    error: Optional[str] = None,
) -> dict:
    plan_summary = _summarize_moai_plan_for_job(plan or {})
    result_summary = _summarize_moai_write_result_for_job(result)
    operation_count = int((plan or {}).get("summary", {}).get("operation_count") or 0)
    skipped_count = int((plan or {}).get("summary", {}).get("skipped_count") or 0)
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO moai_sync_jobs (
              id, event_id, status, trigger_reason, operation_count, skipped_count,
              attempts, last_error, last_plan_summary, last_result_summary,
              last_attempted_at, synced_at, updated_at
            )
            VALUES (?, ?, ?, 'manual', ?, ?, 1, ?, ?, ?, CURRENT_TIMESTAMP,
                    CASE WHEN ? = 'synced' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET
              status=excluded.status,
              operation_count=excluded.operation_count,
              skipped_count=excluded.skipped_count,
              attempts=moai_sync_jobs.attempts + 1,
              last_error=excluded.last_error,
              last_plan_summary=excluded.last_plan_summary,
              last_result_summary=excluded.last_result_summary,
              last_attempted_at=CURRENT_TIMESTAMP,
              synced_at=CASE WHEN excluded.status = 'synced' THEN CURRENT_TIMESTAMP ELSE moai_sync_jobs.synced_at END,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                str(uuid.uuid4()),
                event_id,
                status,
                operation_count,
                skipped_count,
                error,
                json.dumps(plan_summary, ensure_ascii=False),
                json.dumps(result_summary, ensure_ascii=False),
                status,
            ),
        )
        conn.commit()
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
        return _moai_sync_job_from_row(row)


def _list_moai_sync_jobs(status: str = "pending", limit: int = 20) -> list[dict]:
    clean_status = (status or "").strip()
    n = max(1, min(limit, 200))
    clauses = []
    params: list[object] = []
    if clean_status and clean_status != "all":
        clauses.append("status = ?")
        params.append(clean_status)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(n)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, event_id, status, trigger_reason, operation_count, skipped_count, attempts,
                   last_error, last_plan_summary, last_result_summary, last_attempted_at,
                   synced_at, created_at, updated_at
            FROM moai_sync_jobs
            {where_sql}
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_moai_sync_job_from_row(row) for row in rows]


def _event_consent_status(event: dict) -> dict:
    patient_name = str(event.get("patient_name") or "").strip()
    if not patient_name:
        return {"status": "unknown", "scope": "capture_analysis_storage", "active": False}
    with _conn() as conn:
        row = _latest_patient_consent(
            conn,
            patient_name,
            owner_org_id=str(event.get("owner_org_id") or ""),
            owner_provider_person_id=str(event.get("owner_provider_person_id") or ""),
            subject_person_id=str(event.get("subject_person_id") or ""),
        )
    if not row:
        return {"status": "missing", "scope": "capture_analysis_storage", "active": False}
    return {
        "status": "granted",
        "scope": row[2],
        "active": True,
        "consent_id": row[0],
        "checked_at": row[5],
    }


def _build_pilot_manifest_for_event(event_id: str, *, resolve_identity: bool = True) -> dict:
    event, soap, label, review, artifacts = _get_event_snapshot(event_id)
    identity = resolve_moai_identity(event=event) if resolve_identity else None
    subject_person_id = (identity.subject_person_id if identity else None) or event.get("subject_person_id")
    organization_id = (identity.organization_id if identity else None) or event.get("owner_org_id")
    provider_person_id = (identity.provider_person_id if identity else None) or event.get("owner_provider_person_id")
    encounter_id = (identity.encounter_id if identity else None) or event.get("physio_session_id")
    physio_client_id = (identity.physio_client_id if identity else None) or event.get("physio_client_id")
    event_type = str(event.get("event_type") or "")
    has_artifact = bool(artifacts)
    has_note = bool(soap or event.get("raw_text"))
    consent = _event_consent_status(event)
    sync_jobs = [job for job in _list_moai_sync_jobs(status="all", limit=200) if job["event_id"] == event_id]
    latest_sync = sync_jobs[0] if sync_jobs else None

    manifest = {
        "schema_version": "rayban_pt_pilot_session_manifest/v0",
        "operating_mode": "design/dry_run",
        "session": {
            "pilot_session_id": event_id,
            "captured_at": event.get("created_at"),
            "capture_device": "rayban",
            "capture_context": "internal_non_production",
            "event_type": event_type,
            "source": event.get("source"),
            "notes": "",
        },
        "identity": {
            "organization_id": organization_id,
            "provider_person_id": provider_person_id,
            "subject_person_id": subject_person_id,
            "physio_client_id": physio_client_id,
            "encounter_id": encounter_id,
            "identity_resolution_status": identity.status if identity else "not_checked",
            "identity_resolution_notes": "; ".join(identity.warnings) if identity else "",
        },
        "consent": {
            "status": consent["status"],
            "scope": consent["scope"],
            "checked_by": "rayban-local-bridge",
            "checked_at": consent.get("checked_at") or "",
            "notes": "",
        },
        "modalities": {
            "text_note": {
                "captured": event_type in {"text", "combined"} and has_note,
                "event_id": event_id if event_type in {"text", "combined"} else "",
                "usable": bool(has_note),
                "notes": "",
            },
            "audio": {
                "captured": event_type == "audio",
                "event_id": event_id if event_type == "audio" else "",
                "transcript_available": bool(event.get("raw_text")) if event_type == "audio" else False,
                "usable": bool(event.get("raw_text")) if event_type == "audio" else None,
                "notes": "",
            },
            "image": {
                "captured": event_type == "image",
                "event_id": event_id if event_type == "image" else "",
                "masked_artifact_available": has_artifact if event_type == "image" else False,
                "usable_for_pose_context": has_artifact if event_type == "image" else None,
                "notes": "",
            },
            "video": {
                "captured": event_type == "video",
                "event_id": event_id if event_type == "video" else "",
                "masked_or_sampled_artifact_available": has_artifact if event_type == "video" else False,
                "usable_for_temporal_context": has_artifact if event_type == "video" else None,
                "notes": "",
            },
        },
        "therapist_labels_v0": {
            "session_type": label.get("session_type", "") if label else "",
            "core_task": label.get("core_task", "") if label else "",
            "custom_task": label.get("custom_task", "") if label else "",
            "body_position": label.get("body_position", "") if label else "",
            "assist_level": label.get("assist_level", "") if label else "",
            "performance_level": label.get("performance_level", "") if label else "",
            "review_status": label.get("review_status", "unreviewed") if label else "unreviewed",
            "reviewer_person_id": label.get("reviewer_person_id", "") if label else "",
            "usable_for_training": bool(label.get("usable_for_training")) if label else False,
            "label_confidence": label.get("label_confidence") if label else None,
            "repetition_count": label.get("repetition_count") if label else None,
            "hold_duration_seconds": label.get("hold_duration_seconds") if label else None,
            "tolerance": label.get("tolerance", "") if label else "",
            "fatigue_level": label.get("fatigue_level", "") if label else "",
            "compensations": label.get("compensations", []) if label else [],
            "safety_flags": label.get("flags", []) if label else [],
            "caregiver_present": label.get("caregiver_present") if label else None,
            "notes": label.get("notes", "") if label else "",
        },
        "ai_outputs": {
            "soap_draft_generated": bool(soap),
            "label_draft_generated": bool(label),
            "pose_or_visual_analysis_generated": event_type in {"image", "video"} and has_artifact,
            "accepted_corrected_rejected": "corrected" if review else ("not_reviewed" if soap else "not_generated"),
            "unsupported_detail_observed": False,
            "notes": "",
        },
        "review": {
            "reviewer_person_id": review.get("reviewer", "") if review else "",
            "reviewed_at": review.get("reviewed_at", "") if review else "",
            "chart_review_status": "reviewed" if review else "unreviewed",
            "label_review_status": label.get("review_status", "unreviewed") if label else "unreviewed",
            "media_quality": "unknown",
            "corrections_summary": review.get("notes", "") if review else "",
        },
        "agent_dry_run": {
            "moai_export_checked": False,
            "moai_write_plan_checked": bool(latest_sync and latest_sync.get("last_plan_summary")),
            "sync_job_enqueued": bool(latest_sync),
            "operation_count": latest_sync.get("operation_count") if latest_sync else None,
            "skipped_count": latest_sync.get("skipped_count") if latest_sync else None,
            "blocked_reasons": latest_sync.get("last_plan_summary", {}).get("skipped", []) if latest_sync else [],
            "phi_safe_log_confirmed": True,
        },
    }
    manifest["readiness"] = _pilot_readiness_from_manifest(manifest)
    return manifest


def _pilot_readiness_from_manifest(manifest: dict) -> dict:
    missing_schema: list[str] = []
    missing_gold: list[str] = []
    identity = manifest.get("identity") or {}
    consent = manifest.get("consent") or {}
    labels = manifest.get("therapist_labels_v0") or {}
    review = manifest.get("review") or {}
    modalities = manifest.get("modalities") or {}
    reviewed_label_statuses = {"reviewed", "corrected", "approved"}

    if not identity.get("organization_id"):
        missing_schema.append("organization_id")
    if not identity.get("provider_person_id"):
        missing_schema.append("provider_person_id")
    if not (identity.get("subject_person_id") or identity.get("physio_client_id")):
        missing_schema.append("subject_person_id_or_physio_client_id")
    if not identity.get("encounter_id"):
        missing_schema.append("encounter_id")
    if consent.get("status") != "granted":
        missing_schema.append("consent_granted")
    if not any((value or {}).get("captured") for value in modalities.values() if isinstance(value, dict)):
        missing_schema.append("at_least_one_modality")
    if not labels.get("session_type"):
        missing_schema.append("session_type")
    if not labels.get("core_task"):
        missing_schema.append("core_task")
    if labels.get("review_status") not in reviewed_label_statuses:
        missing_schema.append("review_status")

    missing_gold.extend(missing_schema)
    if not identity.get("subject_person_id"):
        missing_gold.append("resolved_subject_person_id")
    if not labels.get("assist_level"):
        missing_gold.append("assist_level")
    if not labels.get("performance_level"):
        missing_gold.append("performance_level")
    if review.get("chart_review_status") != "reviewed" and labels.get("review_status") not in reviewed_label_statuses:
        missing_gold.append("chart_reviewed")
    if not labels.get("usable_for_training"):
        missing_gold.append("usable_for_training")

    return {
        "usable_for_schema_eval": not missing_schema,
        "eligible_for_gold_dataset": not missing_gold,
        "gate": "gate_1_pilot",
        "missing_requirements": sorted(set(missing_schema)),
        "gold_missing_requirements": sorted(set(missing_gold)),
    }


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _build_physio_session_export_item(conn: sqlite3.Connection, event_row):
    event_id = event_row[0]
    label = _get_label_by_event_id(conn, event_id)
    soap = _get_latest_soap_by_event_id(conn, event_id)
    review = _get_chart_review_by_event_id(conn, event_id)
    chart_text, quality = _read_chart_export(event_id)
    sections = _parse_chart_sections(chart_text) if chart_text else {}
    consent = (
        _latest_patient_consent(
            conn,
            event_row[6],
            owner_org_id=str(event_row[7] or ""),
            owner_provider_person_id=str(event_row[8] or ""),
            subject_person_id=str(event_row[9] or ""),
        )
        if event_row[6]
        else None
    )

    title = _first_non_empty(
        " · ".join(
            part
            for part in [
                label.get("session_type") if label else "",
                label.get("core_task") if label else "",
            ]
            if part
        ),
        sections.get("Dx.>"),
        event_row[3],
        soap.get("a") if soap else "",
        event_row[2],
    )
    summary = _first_non_empty(
        label.get("notes") if label else "",
        soap.get("a") if soap else "",
        soap.get("o") if soap else "",
        sections.get("A>"),
        sections.get("O>"),
        event_row[3],
        _clip_chart_text(chart_text, 360),
    )

    return {
        "id": event_id,
        "event_id": event_id,
        "source": event_row[1],
        "event_type": event_row[2],
        "intent": event_row[3],
        "status": event_row[4],
        "created_at": event_row[5],
        "patient_name": event_row[6] or None,
        "owner_org_id": event_row[7] or None,
        "owner_provider_person_id": event_row[8] or None,
        "subject_person_id": event_row[9] or None,
        "physio_client_id": event_row[10] or None,
        "physio_session_id": event_row[11] or None,
        "title": title,
        "summary": summary,
        "label": label,
        "soap": soap,
        "chart_excerpt": _clip_chart_text(chart_text, 900) if chart_text else "",
        "quality": quality,
        "review": review,
        "consent_verified": consent is not None,
        "consent_id": consent[0] if consent else None,
        "artifacts": _list_event_artifacts(event_id),
        "persisted": True,
        "storage": "rayban-local-bridge.sqlite",
    }


def redact_phi(text: str) -> str:
    text = re.sub(r"(01[0-9]-?\d{3,4}-?\d{4})", "[REDACTED_PHONE]", text)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", text)
    text = re.sub(r"\b\d{6}-?[1-4]\d{6}\b", "[REDACTED_RRN]", text)
    text = re.sub(r"(?i)\b(mrn|chart\s*no|등록번호|병록번호)\s*[:#]?\s*[A-Za-z0-9-]{4,}\b", r"\1:[REDACTED_ID]", text)
    text = re.sub(r"\b(환아|환자|보호자)\s*([가-힣]{2,4})\b", r"\1 [REDACTED_NAME]", text)
    return text


def classify_intent(text: str, event_type: str) -> str:
    if event_type == "command":
        return "command"

    command_keywords = [
        "잡아줘",
        "설정해줘",
        "예약해줘",
        "보내줘",
        "추가해줘",
        "알려줘",
        "생성해줘",
        "정리해줘",
    ]
    if any(k in text for k in command_keywords):
        return "command"

    question_keywords = ["질문", "왜", "어떻게", "?", "문의", "확인", "가능할까", "가능한가", "맞아?"]
    if any(k in text for k in question_keywords):
        return "question"

    return "note"


def _extract_measurements(text: str) -> str:
    """O 섹션: ROM / VAS / MMT / 기타 수치 추출"""
    results = []

    # ROM: 숫자 + 도 (예: 90도, 120도, -5도)
    rom_hits = re.findall(r"(?:ROM|관절범위|굴곡|신전|외전|내전|외회전|내회전|거상)\s*[:\-]?\s*(-?\d+)\s*도", text)
    if rom_hits:
        results.append("ROM " + " / ".join(h + "°" for h in rom_hits))

    # 도 단독 (앞에 방향/부위 있는 경우)
    stand_alone_deg = re.findall(r"(?<![가-힣])(-?\d+)\s*도(?!\s*[가-힣])", text)
    if stand_alone_deg and not rom_hits:
        results.append("측정값 " + " / ".join(d + "°" for d in stand_alone_deg[:4]))

    # VAS: VAS 숫자/10 or 통증 숫자점
    vas_hits = re.findall(r"(?:VAS|바스)\s*(\d+)(?:\s*/\s*10)?", text, re.IGNORECASE)
    if not vas_hits:
        vas_hits = re.findall(r"통증\s*(\d+)\s*(?:점|/10)", text)
    if vas_hits:
        results.append("VAS " + vas_hits[0] + "/10")

    # MMT / 근력 등급: 4/5, 4등급, grade 4
    mmt_hits = re.findall(r"(?:근력|MMT|grade)\s*[:\-]?\s*(\d+)\s*(?:/5|등급|단계)?", text, re.IGNORECASE)
    if mmt_hits:
        results.append("MMT " + mmt_hits[0] + "/5")

    # 일반 수치 (회, 분, 초, m, cm, kg, %)
    misc = re.findall(r"\b\d+(?:\.\d+)?\s*(?:회|분|초|m|cm|kg|%)\b", text)
    if misc:
        results.extend(misc[:4])

    return " · ".join(results) if results else "관찰/측정 수치 미입력"


def _extract_risk_flags(text: str) -> str:
    """A 섹션: 위험징후 + 임상 해석 (개선/악화/안정)"""
    parts = []

    def negated(term):
        return any(f"{term}{s}" in text for s in [" 없음", " 없", " 부인", " 아님", " 해당없음"])

    # 위험징후
    risk_rules = [
        ("낙상", "낙상 위험"),
        ("통증 악화", "통증 악화 추세"),
        ("호흡 곤란", "호흡 이슈"),
        ("피로 누적", "피로 누적"),
        ("순응도 낮", "홈프로그램 순응도 저하"),
        ("불안정", "균형/보행 불안정"),
        ("부종", "부종 관찰"),
    ]
    flags = [label for term, label in risk_rules if term in text and not negated(term)]
    if flags:
        parts.append("⚠ " + ", ".join(flags))

    # 개선 신호
    improve_kw = ["호전", "개선", "감소", "향상", "증가", "좋아", "완화", "회복"]
    if any(k in text for k in improve_kw):
        parts.append("기능 호전 소견")

    # 안정
    stable_kw = ["유지", "안정", "변화 없음", "동일"]
    if any(k in text for k in stable_kw) and not any(k in text for k in improve_kw):
        parts.append("현 상태 안정적 유지")

    # 통증 호소 (위험은 아니지만 기록)
    if "통증" in text and not negated("통증") and "통증 악화" not in text:
        vas = re.search(r"(?:VAS|바스)\s*(\d+)", text, re.IGNORECASE)
        pain_note = f"통증 호소 (VAS {vas.group(1)}/10)" if vas else "통증 호소"
        parts.append(pain_note)

    return ", ".join(parts) if parts else "특이 위험징후 미확인, 전반적 안정"


def _build_plan(text: str) -> str:
    """P 섹션: 텍스트 내용 기반 맞춤 치료 계획"""
    plans = []

    # ROM 제한 → 관절가동술
    if any(k in text for k in ["ROM", "관절범위", "굴곡", "신전", "외전", "제한"]):
        plans.append("관절가동범위 회복 운동 (PROM → AROM 진행)")

    # 통증 → 통증 관리
    if "통증" in text and not any(f"통증{s}" in text for s in [" 없음", " 해결"]):
        plans.append("통증 관리: 물리치료 병행 (열/냉 치료, TENS)")

    # 근력 저하 → 근강화
    if any(k in text for k in ["근력", "MMT", "약화", "weakness"]):
        plans.append("점진적 근력 강화 운동 (저항 운동 단계 조정)")

    # 보행/균형
    if any(k in text for k in ["보행", "걷기", "균형", "낙상"]):
        plans.append("보행 훈련 및 균형 운동 강화")

    # 부종
    if "부종" in text:
        plans.append("부종 관리 (압박/거상/냉찜질)")

    # ADL
    if any(k in text for k in ["ADL", "일상", "자립"]):
        plans.append("ADL 자립 향상 훈련")

    # 기본 공통
    plans.append("가정운동 프로그램 재교육 및 순응도 확인")
    plans.append("다음 방문 시 기능 재평가")

    return chr(10).join(f"· {p}" for p in plans[:5])


def _normalize_clinical_terms(text: str) -> str:
    """Clean common Korean STT variants before drafting SOAP."""
    normalized = text or ""
    normalized = re.sub(r"\b바스\s*(\d+)", r"VAS \1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"외전\s*시통증", "외전 시 통증", normalized)
    normalized = re.sub(r"(호문독|호문동|홈문동)\s*교육[관함]?", "홈운동 교육함", normalized)
    normalized = re.sub(r"홈\s*운동", "홈운동", normalized)
    return normalized


def _is_operational_image_note(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if stripped.startswith(("[마스킹", "[face_", "[Ray-Ban 영상]", "[영상 분석")):
        return True
    if re.match(r"^t\+\d+(?:\.\d+)?s:", stripped):
        return True
    return any(token in stripped for token in ("detector=", "segmenter=", "파일=", "masked.jpg"))


def _strip_operational_image_notes(text: str) -> str:
    cleaned: list[str] = []
    for line in (text or "").splitlines():
        if _is_operational_image_note(line):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


CHART_SECTION_KEYS = ["F/U>", "Dx.>", "S>", "O>", "P/E>", "A>", "rehab device>", "PTx.>", "Comment>"]


def _parse_chart_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_key = ""
    for line in (text or "").splitlines():
        trimmed = line.strip()
        if trimmed in CHART_SECTION_KEYS:
            current_key = trimmed
            sections.setdefault(current_key, [])
        elif current_key:
            sections.setdefault(current_key, []).append(line)
    return {
        key: "\n".join(lines).strip()
        for key, lines in sections.items()
        if "\n".join(lines).strip()
    }


def _chart_quality(chart_text: str) -> dict:
    sections = _parse_chart_sections(chart_text)
    issues: list[dict[str, str]] = []

    def add(code: str, section: str, message: str, severity: str = "review"):
        issues.append({"code": code, "section": section, "message": message, "severity": severity})

    required = {
        "S>": "주관적 소견",
        "O>": "객관적 측정값",
        "P/E>": "신체 검사",
        "A>": "임상 해석",
        "PTx.>": "치료 계획",
    }
    for key, label in required.items():
        if not sections.get(key):
            add("missing_section", key, f"{label} 섹션이 비어 있습니다.", "needs_edit")

    placeholders = [
        "수동 입력 필요",
        "내용 없음",
        "환자 주관적 호소 미입력",
        "이미지 기반 임상 소견 미입력",
        "관찰/측정 수치 미입력",
        "임상 검수 필요",
    ]
    for key, body in sections.items():
        if any(token in body for token in placeholders):
            add("placeholder", key, "자동 기본값이 남아 있어 실제 검수가 필요합니다.")

    stt_noise = [
        "구독",
        "좋아요",
        "알림 설정",
        "시청해 주셔서",
        "자막",
        "영상에서",
        "채널",
    ]
    if any(token in chart_text for token in stt_noise):
        add("stt_noise", "S>", "비임상 음성 인식 문구가 섞였을 가능성이 있습니다.", "needs_edit")

    operational_tokens = ["masking completed", "detector=", "segmenter=", "consent_id=", "_masked.jpg", "[마스킹 완료]"]
    if any(token in chart_text for token in operational_tokens):
        add("operational_text", "chart", "마스킹/운영 로그 문구가 차트 본문에 남아 있습니다.", "needs_edit")

    s_text = sections.get("S>", "")
    if s_text and len(re.sub(r"\s+", "", s_text)) < 8:
        add("short_subjective", "S>", "주관적 소견이 너무 짧습니다.")

    deduction = sum(25 if issue["severity"] == "needs_edit" else 12 for issue in issues)
    score = max(0, 100 - deduction)
    level = "good" if score >= 85 and not issues else "review" if score >= 60 else "needs_edit"
    return {"score": score, "level": level, "issues": issues[:8]}


def _clip_chart_text(text: str, max_chars: int = 700) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _extract_marker_value(text: str, marker: str) -> str:
    for line in (text or "").splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return ""


def _extract_pose_summary(text: str) -> str:
    lines = (text or "").splitlines()
    pose_lines: list[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        if "🦴 자세 분석" in stripped:
            collecting = True
            continue
        if collecting:
            if stripped.startswith(("위 이미지", "[마스킹", "[face_", "환자:", "📝", "🏷")):
                break
            if stripped:
                pose_lines.append(stripped)
        elif "관절 각도 측정" in stripped or stripped.startswith("•"):
            pose_lines.append(stripped)

    return "\n".join(pose_lines[:12]).strip()


def _extract_voice_memo(text: str) -> str:
    marker = "[치료사 음성 메모"
    if marker not in (text or ""):
        return ""

    tail = text.split(marker, 1)[1]
    if "\n" in tail:
        tail = tail.split("\n", 1)[1]
    stop_markers = ["📝 인식된 텍스트:", "🏷 장면:", "🦴 자세 분석:", "위 이미지"]
    for stop in stop_markers:
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    return _clip_chart_text(tail, 500)


def _extract_video_transcript_capture_text(text: str) -> str:
    """Keep therapist speech separate from operational video analysis notes."""

    marker = "[치료사 음성 기록 — S> 섹션 참고]"
    if marker not in (text or ""):
        return ""
    tail = text.split(marker, 1)[1]
    for stop in ("\n\n[영상 분석", "\n[영상 분석", "\n\n[Ray-Ban 영상"):
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    return tail.strip()


def _looks_nonclinical_image(text: str, scene: str) -> bool:
    haystack = f"{scene}\n{text}".lower()
    return any(
        token in haystack
        for token in [
            "screenshot",
            "screen shot",
            "document",
            "keyboard",
            "computer",
            "xcode",
            "127.0.0.1",
            "localhost",
            "rayban local bridge",
            "codex",
            "파일 변경",
        ]
    )


def _image_chart_inputs(text: str, image_notes: str) -> tuple[str, str, bool]:
    clean_text = _strip_operational_image_notes(text)
    clean_image_notes = _strip_operational_image_notes(image_notes)
    scene = _extract_marker_value(clean_text, "🏷 장면:")
    voice_memo = _extract_voice_memo(clean_text)
    pose_summary = _extract_pose_summary(clean_text)
    nonclinical = _looks_nonclinical_image(clean_text, scene)

    subjective = voice_memo or "환자 주관적 호소 미입력"
    observations: list[str] = []

    if nonclinical:
        observations.append("비임상 스크린/문서 캡처로 판단되어 임상 관찰 제한")
        observations.append("OCR 텍스트는 원본 기록에만 보관하고 SOAP 본문에는 반영하지 않음")

    if scene:
        observations.append(f"장면 분류: {scene}")
    if pose_summary:
        observations.append(pose_summary)
    if clean_image_notes:
        observations.append(clean_image_notes)
    if not observations:
        observations.append("이미지 기반 임상 소견 미입력")

    return subjective, "\n".join(observations), nonclinical


def build_soap(text: str, event_id: str = "", event_type: str = "text",
               image_notes: str = ""):
    """auto-chart generate_chart()로 11.txt 생성 + S/O/A/P dict 반환."""
    import datetime as _dt
    date_str = _dt.date.today().isoformat()
    chart_text = _strip_operational_image_notes(_normalize_clinical_terms(text))

    # 11.txt 차트 생성
    if event_type == "image":
        transcript, img_note, nonclinical_image = _image_chart_inputs(chart_text, image_notes)
        extraction_basis = f"{transcript}\n{img_note}"
    else:
        transcript = chart_text
        img_note = ""
        extraction_basis = chart_text
        nonclinical_image = False

    # O/A/P 먼저 추출
    o_val = _extract_measurements(extraction_basis)
    a_val = _extract_risk_flags(extraction_basis)
    if nonclinical_image:
        p_val = "· 임상 사진/음성 기록 재수집 후 SOAP 보완\n· 치료사 검수 후 최종 차트 확정"
    else:
        p_val = _build_plan(extraction_basis)

    chart_content = generate_chart(
        template_name="11",
        uuid=event_id or "unknown",
        date=date_str,
        transcript_text=transcript,
        image_notes=img_note,
        objective=o_val,
        assessment=a_val,
        plan=p_val,
    )

    # 파일 저장
    if event_id:
        chart_path = CHART_DIR / f"{event_id}_11.txt"
        save_chart(chart_path, chart_content)

    # S/O/A/P dict (iOS 앱 호환)
    return transcript, o_val, f"임상 해석: {a_val}", p_val


def _extract_image_note_line(text: str) -> str:
    markers = ("[마스킹", "[face_", "[영상 분석", "[Ray-Ban 영상]")
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(markers) and not _is_operational_image_note(stripped):
            lines.append(stripped)
    return "\n".join(lines)


def _extract_patient_from_text(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        for marker in ("환자:", "[환자]"):
            if stripped.startswith(marker):
                return stripped.split(marker, 1)[1].strip()
    return ""


def _get_event_for_merge(conn: sqlite3.Connection, event_id: str) -> dict:
    row = conn.execute(
        "SELECT id, source, event_type, raw_text, patient_name, created_at, owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id "
        "FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        _error(404, "EVENT_NOT_FOUND", f"event not found: {event_id}")
    return {
        "id": row[0],
        "source": row[1],
        "event_type": row[2],
        "raw_text": row[3] or "",
        "patient_name": row[4] or "",
        "created_at": row[5],
        "owner_org_id": row[6] or "",
        "owner_provider_person_id": row[7] or "",
        "subject_person_id": row[8] or "",
        "physio_client_id": row[9] or "",
        "physio_session_id": row[10] or "",
    }


def _resolve_merged_scope(image_event: dict, audio_event: dict) -> tuple[Optional[str], Optional[str]]:
    scopes = []
    for key in ("owner_org_id", "owner_provider_person_id"):
        image_value = _clean_scope_value(image_event.get(key))
        audio_value = _clean_scope_value(audio_event.get(key))
        if image_value and audio_value and image_value != audio_value:
            _error(400, "SCOPE_MISMATCH", "서로 다른 조직/전문가의 이벤트는 통합할 수 없습니다.")
        scopes.append(image_value or audio_value)
    return scopes[0], scopes[1]


def _resolve_merged_physio_context(image_event: dict, audio_event: dict) -> tuple[Optional[str], Optional[str]]:
    values = []
    for key in ("physio_client_id", "physio_session_id"):
        image_value = _clean_scope_value(image_event.get(key))
        audio_value = _clean_scope_value(audio_event.get(key))
        if image_value and audio_value and image_value != audio_value:
            _error(400, "PHYSIO_CONTEXT_MISMATCH", "서로 다른 physio client/session 이벤트는 통합할 수 없습니다.")
        values.append(image_value or audio_value)
    return values[0], values[1]


def _resolve_merged_subject_person_id(image_event: dict, audio_event: dict) -> Optional[str]:
    image_value = _clean_scope_value(image_event.get("subject_person_id"))
    audio_value = _clean_scope_value(audio_event.get("subject_person_id"))
    if image_value and audio_value and image_value != audio_value:
        _error(400, "SUBJECT_PERSON_MISMATCH", "서로 다른 subject_person_id 이벤트는 통합할 수 없습니다.")
    return image_value or audio_value


def _create_merged_event(image_event: dict, audio_event: dict, patient_name: str = "") -> dict:
    if image_event["event_type"] not in {"image", "video"}:
        _error(400, "INVALID_IMAGE_EVENT", "image_event_id는 image 또는 video 이벤트여야 합니다.")
    if audio_event["event_type"] not in {"audio", "text"}:
        _error(400, "INVALID_AUDIO_EVENT", "audio_event_id는 audio 또는 text 이벤트여야 합니다.")

    event_id = str(uuid.uuid4())
    image_text = image_event["raw_text"]
    audio_text = _normalize_clinical_terms(audio_event["raw_text"])
    image_notes = _extract_image_note_line(image_text)
    _, pe_note, nonclinical_image = _image_chart_inputs(image_text, image_notes)

    patient = (
        (patient_name or "").strip()
        or image_event.get("patient_name")
        or audio_event.get("patient_name")
        or _extract_patient_from_text(image_text)
        or None
    )
    owner_org_id, owner_provider_person_id = _resolve_merged_scope(image_event, audio_event)
    subject_person_id = _resolve_merged_subject_person_id(image_event, audio_event)
    physio_client_id, physio_session_id = _resolve_merged_physio_context(image_event, audio_event)
    consent_id = _require_patient_consent(
        "text",
        patient or "",
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
        subject_person_id=subject_person_id,
    )

    subjective = audio_text.strip() or "환자 주관적 호소 미입력"
    extraction_basis = "\n".join([subjective, pe_note])
    o_val = _extract_measurements(extraction_basis)
    a_val = _extract_risk_flags(extraction_basis)
    if nonclinical_image and not audio_text.strip():
        p_val = "· 임상 사진/음성 기록 재수집 후 SOAP 보완\n· 치료사 검수 후 최종 차트 확정"
    else:
        p_val = _build_plan(extraction_basis)

    chart_content = generate_chart(
        template_name="11",
        uuid=event_id,
        date=datetime.utcnow().date().isoformat(),
        transcript_text=subjective,
        image_notes=pe_note,
        objective=o_val,
        assessment=a_val,
        plan=p_val,
    )
    save_chart(CHART_DIR / f"{event_id}_11.txt", chart_content)

    raw_text = "\n\n".join(
        [
            "[통합 차트]",
            f"환자: {patient or '미지정'}",
            f"이미지 이벤트: {image_event['id']}",
            f"음성 이벤트: {audio_event['id']}",
            "[S: 음성/텍스트]",
            subjective,
            "[P/E: 이미지]",
            pe_note,
        ]
    )
    soap = {
        "s": subjective,
        "o": o_val,
        "a": f"임상 해석: {a_val}",
        "p": p_val,
    }

    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (id, source, event_type, raw_text, intent, status, patient_name, owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                "merged",
                "combined",
                raw_text,
                "note",
                "processed",
                patient,
                owner_org_id,
                owner_provider_person_id,
                subject_person_id,
                physio_client_id,
                physio_session_id,
            ),
        )
        conn.execute(
            "INSERT INTO soap_notes (id, event_id, s, o, a, p) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, soap["s"], soap["o"], soap["a"], soap["p"]),
        )
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, "info", f"merged image={image_event['id']} audio={audio_event['id']} consent_id={consent_id or '-'}"),
        )
        conn.commit()

    return {"event_id": event_id, "soap": soap, "patient_name": patient}


@lru_cache(maxsize=1)
def _get_whisper_model():
    from faster_whisper import WhisperModel  # type: ignore

    model_name = os.getenv("WHISPER_MODEL", "small")
    device = os.getenv("WHISPER_DEVICE", "auto")
    compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def stt_whisper_local(audio_path: Optional[str]) -> str:
    if not audio_path:
        return ""
    try:
        model = _get_whisper_model()
        segments, _ = model.transcribe(audio_path, language="ko")
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text or f"[STT_EMPTY] {audio_path}"
    except Exception:
        return f"[STT_STUB] {audio_path}"


def _process_event(
    source: str,
    event_type: str,
    text: Optional[str] = None,
    audio_path: Optional[str] = None,
    image_base64: Optional[str] = None,
    image_notes: str = "",
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    physio_client_id: Optional[str] = None,
    physio_session_id: Optional[str] = None,
):
    audio_store = AUDIO_STORE
    image_store = os.getenv("IMAGE_STORE", "false").lower() == "true"
    phi_redact = os.getenv("PHI_REDACT", "true").lower() == "true"
    soap_enabled = os.getenv("SOAP_ENABLED", "true").lower() == "true"
    masking_meta = None
    masking_audit = ""
    owner_org_id = _clean_scope_value(owner_org_id)
    owner_provider_person_id = _clean_scope_value(owner_provider_person_id)
    subject_person_id = _clean_scope_value(subject_person_id)
    physio_client_id = _clean_scope_value(physio_client_id)
    physio_session_id = _clean_scope_value(physio_session_id)

    if event_type not in {"audio", "text", "command", "image", "video"}:
        raise HTTPException(status_code=400, detail="event_type must be audio/text/command/image/video")
    _require_pilot_capture_metadata(
        event_type=event_type,
        patient_name=patient_name,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
        subject_person_id=subject_person_id,
        physio_client_id=physio_client_id,
        physio_session_id=physio_session_id,
    )

    event_id = str(uuid.uuid4())
    consent_id = _require_patient_consent(
        event_type,
        patient_name,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
        subject_person_id=subject_person_id,
    )

    if event_type == "audio":
        parsed_text = stt_whisper_local(audio_path)
    elif event_type == "image" and image_base64:
        import base64
        img_bytes = base64.b64decode(image_base64)
        raw_path = UPLOAD_DIR / f"{uuid.uuid4()}.jpg"
        raw_path.write_bytes(img_bytes)
        clinical_image_notes = _strip_operational_image_notes(image_notes)

        # ── 2단계: 얼굴 마스킹 (YuNet → MediaPipe → Haar fallback) ──
        masked_path = MASKED_DIR / f"{event_id}_masked.jpg"
        try:
            mask_result = _mask_faces(
                raw_path,
                masked_path,
                method=os.getenv("FACE_MASK_METHOD", "solid"),
                blur_kernel=91,
            )
            face_count = mask_result.get("face_count", 0)
            detector = mask_result.get("detector", "unknown")
            mask_shape = mask_result.get("shape", "box")
            segment_sources = mask_result.get("segment_sources") or []
            segment_note = f", shape={mask_shape}"
            if segment_sources:
                segment_note += f", segmenter={'+'.join(segment_sources)}"
            if face_count == 0:
                masked_path.unlink(missing_ok=True)
                if not ALLOW_UNMASKED_IMAGE:
                    raw_path.unlink(missing_ok=True)
                    _error(
                        422,
                        "FACE_NOT_DETECTED",
                        f"얼굴을 감지하지 못해 이미지 처리를 중단했습니다. detector={detector}{segment_note}",
                    )
                import shutil
                shutil.copy(raw_path, masked_path)
                masking_meta = {
                    "status": "face_not_detected",
                    "face_count": 0,
                    "detector": detector,
                    "shape": mask_shape,
                    "segmenters": segment_sources,
                    "masked_file": masked_path.name,
                    "unmasked_allowed": True,
                }
                masking_audit = f"masking face_not_detected detector={detector}{segment_note} file={masked_path.name} allow_unmasked=1"
            else:
                masking_meta = {
                    "status": "completed",
                    "face_count": face_count,
                    "detector": detector,
                    "shape": mask_shape,
                    "segmenters": segment_sources,
                    "masked_file": masked_path.name,
                }
                masking_audit = f"masking completed face_count={face_count} detector={detector}{segment_note} file={masked_path.name}"
        except HTTPException:
            raw_path.unlink(missing_ok=True)
            masked_path.unlink(missing_ok=True)
            raise
        except Exception as e:
            masked_path.unlink(missing_ok=True)
            if not ALLOW_UNMASKED_IMAGE:
                raw_path.unlink(missing_ok=True)
                _error(422, "MASKING_FAILED", f"얼굴 마스킹 실패로 이미지 처리를 중단했습니다: {e}")
            masking_meta = {
                "status": "failed_unmasked_allowed",
                "error": str(e),
                "unmasked_allowed": True,
            }
            masking_audit = f"masking failed allow_unmasked=1 error={e}"

        if not image_store:
            raw_path.unlink(missing_ok=True)

        parsed_text = "\n".join(
            part.strip()
            for part in [text or "", clinical_image_notes]
            if part and part.strip()
        )
        image_notes = clinical_image_notes
    else:
        parsed_text = text or ""

    if phi_redact:
        parsed_text = redact_phi(parsed_text)

    intent = classify_intent(parsed_text, event_type)

    soap_id = None
    soap = None
    transcript_capture_events: list[dict] = []
    should_make_soap = intent == "note" or event_type in {"audio", "image", "video"}
    if soap_enabled and should_make_soap:
        _img_notes = image_notes if event_type == "image" else ""
        s, o, a, p = build_soap(parsed_text, event_id=event_id,
                                 event_type=event_type, image_notes=_img_notes)
        soap_id = str(uuid.uuid4())
        soap = {"s": s, "o": o, "a": a, "p": p}

    visit_auto_attach = None
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (id, source, event_type, raw_text, intent, status, patient_name, owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                source,
                event_type,
                parsed_text,
                intent,
                "processed",
                patient_name or None,
                owner_org_id,
                owner_provider_person_id,
                subject_person_id,
                physio_client_id,
                physio_session_id,
            ),
        )
        if soap_id and soap:
            conn.execute(
                "INSERT INTO soap_notes (id, event_id, s, o, a, p) VALUES (?, ?, ?, ?, ?, ?)",
                (soap_id, event_id, soap["s"], soap["o"], soap["a"], soap["p"]),
            )
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, "info", f"ingest processed consent_id={consent_id or '-'}"),
        )
        if masking_audit:
            conn.execute(
                "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), event_id, "info", masking_audit),
            )
        visit_auto_attach = _auto_attach_event_to_active_visit(conn, event_id)
        capture_text = parsed_text
        if event_type == "video":
            capture_text = _extract_video_transcript_capture_text(parsed_text)
        if capture_text and event_type in {"audio", "text", "image", "video"}:
            attached_session = (visit_auto_attach or {}).get("session") or {}
            transcript_capture_events = _create_transcript_capture_events(
                conn,
                text=capture_text,
                visit_session_id=attached_session.get("id"),
                encounter_id=attached_session.get("encounter_id") or physio_session_id,
                organization_id=attached_session.get("organization_id") or owner_org_id,
                provider_person_id=attached_session.get("provider_person_id") or owner_provider_person_id,
                provider_role=attached_session.get("provider_role"),
                subject_person_id=attached_session.get("subject_person_id") or subject_person_id,
                source_event_id=event_id,
                start_ms=None,
                end_ms=None,
                capture_origin=_capture_origin_from_source(source),
                derived_from=event_type,
            )
        conn.commit()

    ack = {"note": "기록 완료", "question": "질문 접수 완료", "command": "명령 접수 완료"}[intent]

    if event_type == "audio" and audio_path and not audio_store:
        try:
            p = Path(audio_path)
            if p.exists() and str(p).startswith(str(UPLOAD_DIR)):
                p.unlink(missing_ok=True)
        except Exception:
            pass

    return {
        "event_id": event_id,
        "intent": intent,
        "ack": ack,
        "soap": soap,
        "policy": {
            "audio_store": audio_store,
            "image_store": image_store,
            "phi_redact": phi_redact,
            "soap_enabled": soap_enabled,
            "allow_unmasked_image": ALLOW_UNMASKED_IMAGE,
            "patient_consent_required": REQUIRE_PATIENT_CONSENT,
            "consent_id": consent_id,
        },
        "media": {
            "masking": masking_meta,
        } if masking_meta else {},
        "scope": {
            "owner_org_id": owner_org_id,
            "owner_provider_person_id": owner_provider_person_id,
            "subject_person_id": subject_person_id,
            "physio_client_id": physio_client_id,
            "physio_session_id": physio_session_id,
        },
        "visit_auto_attach": visit_auto_attach,
        "capture_events": transcript_capture_events,
    }


def _event_status_result(processed: dict) -> dict:
    """Normalize _process_event output to the /events/{id} response shape used by iOS."""
    event_id = processed.get("event_id", "")
    event_obj = None
    if event_id:
        with _conn() as conn:
            row = conn.execute(
                "SELECT id, source, event_type, raw_text, intent, status, created_at, owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id "
                "FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
        if row:
            event_obj = {
                "id": row[0],
                "source": row[1],
                "event_type": row[2],
                "raw_text": row[3],
                "intent": row[4],
                "status": row[5],
                "created_at": row[6],
                "owner_org_id": row[7],
                "owner_provider_person_id": row[8],
                "subject_person_id": row[9],
                "physio_client_id": row[10],
                "physio_session_id": row[11],
            }
    result = {"event": event_obj, "soap": processed.get("soap")}
    if processed.get("media"):
        result["media"] = processed["media"]
    return result


def _process_upload_job(
    event_id: str,
    source: str,
    saved_path: Path,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    physio_client_id: Optional[str] = None,
    physio_session_id: Optional[str] = None,
):
    attempts = 2
    last_error = None
    for i in range(attempts):
        try:
            _touch_async_result(event_id, {
                "status": "processing",
                "message": f"audio processing attempt={i+1}",
            })
            started = datetime.utcnow()
            result = _run_with_timeout(
                _process_event,
                PROCESS_TIMEOUT_SECONDS,
                source=source,
                event_type="audio",
                audio_path=str(saved_path),
                patient_name=patient_name,
                owner_org_id=owner_org_id,
                owner_provider_person_id=owner_provider_person_id,
                subject_person_id=subject_person_id,
                physio_client_id=physio_client_id,
                physio_session_id=physio_session_id,
            )
            inner_id = result.get("event_id", "")
            if inner_id and inner_id != event_id:
                import shutil as _shutil
                inner_chart = CHART_DIR / f"{inner_id}_11.txt"
                outer_chart = CHART_DIR / f"{event_id}_11.txt"
                if inner_chart.exists() and not outer_chart.exists():
                    _shutil.copy(inner_chart, outer_chart)
            if AUDIO_STORE and inner_id and saved_path.exists():
                with _conn() as conn:
                    transcript_row = conn.execute(
                        "SELECT raw_text FROM events WHERE id = ?",
                        (inner_id,),
                    ).fetchone()
                transcript_text = transcript_row[0] if transcript_row and transcript_row[0] else ""
                _stage_raw_media_if_consent_active(
                    saved_path,
                    RawMediaStage(
                        event_id=inner_id,
                        kind="raw_audio",
                        transcript_text=transcript_text,
                        consent_id=str((result.get("policy") or {}).get("consent_id") or ""),
                    ),
                    owner_org_id=owner_org_id,
                    owner_provider_person_id=owner_provider_person_id,
                    subject_person_id=subject_person_id,
                )

            saved_path.unlink(missing_ok=True)
            _touch_async_result(event_id, {"status": "done", "result": _event_status_result(result)})
            took_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
            _audit_log(inner_id or None, "info", f"upload processed attempt={i+1} took_ms={took_ms}")
            return
        except Exception as e:
            last_error = e
            code, msg, retryable = _normalize_error(e)
            logger.exception("upload job failed event_id=%s attempt=%s code=%s", event_id, i + 1, code)
            _audit_log(None, "error", f"upload failed outer_event_id={event_id} attempt={i+1} code={code} msg={msg}")
            if i == attempts - 1:
                _touch_async_result(event_id, {
                    "status": "error",
                    "error": msg,
                    "error_code": code,
                    "retryable": retryable,
                })
    saved_path.unlink(missing_ok=True)


def _delete_event_artifacts(event_id: str) -> list[str]:
    deleted: list[str] = []
    candidates = [CHART_DIR / f"{event_id}_11.txt"]
    candidates.extend(
        path for path in MASKED_DIR.iterdir()
        if path.name.startswith(f"{event_id}_")
    )
    candidates.extend(
        path for path in RAW_MEDIA_DIR.iterdir()
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


def _delete_raw_event_artifacts(event_id: str) -> int:
    deleted = 0
    failures: list[str] = []
    for artifact in list_raw_media_artifacts(RAW_MEDIA_DIR, event_id):
        try:
            if delete_raw_media(RAW_MEDIA_DIR, artifact["filename"]):
                deleted += 1
        except OSError as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError("staged raw media purge failed")
    return deleted


def _process_image_job(
    event_id: str,
    source: str,
    saved_path,
    description: str,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    physio_client_id: Optional[str] = None,
    physio_session_id: Optional[str] = None,
):
    image_store = os.getenv("IMAGE_STORE", "false").lower() == "true"
    try:
        _touch_async_result(event_id, {"status": "processing", "message": "image processing"})
        text = description if description else f"[이미지 캡처] 파일: {saved_path.name}"
        import base64
        image_base64 = base64.b64encode(Path(saved_path).read_bytes()).decode("ascii")
        started = datetime.utcnow()
        result = _run_with_timeout(
            _process_event,
            PROCESS_TIMEOUT_SECONDS,
            source=source,
            event_type="image",
            text=text,
            image_base64=image_base64,
            patient_name=patient_name,
            owner_org_id=owner_org_id,
            owner_provider_person_id=owner_provider_person_id,
            subject_person_id=subject_person_id,
            physio_client_id=physio_client_id,
            physio_session_id=physio_session_id,
        )
        inner_id = result.get("event_id", "")
        if inner_id and inner_id != event_id:
            import shutil as _shutil
            inner_chart = CHART_DIR / f"{inner_id}_11.txt"
            outer_chart = CHART_DIR / f"{event_id}_11.txt"
            if inner_chart.exists() and not outer_chart.exists():
                _shutil.copy(inner_chart, outer_chart)

        if image_store:
            result["image_path"] = str(saved_path)
        _touch_async_result(event_id, {"status": "done", "result": _event_status_result(result)})
        took_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
        _audit_log(event_id, "info", f"image processed took_ms={took_ms}")
    except Exception as e:
        code, msg, retryable = _normalize_error(e)
        logger.exception("image job failed event_id=%s code=%s", event_id, code)
        _audit_log(event_id, "error", f"image failed code={code} msg={msg}")
        _touch_async_result(event_id, {
            "status": "error",
            "error": msg,
            "error_code": code,
            "retryable": retryable,
        })
    finally:
        if not image_store:
            Path(saved_path).unlink(missing_ok=True)


def _process_video_job(
    event_id: str,
    source: str,
    saved_path: Path,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    physio_client_id: Optional[str] = None,
    physio_session_id: Optional[str] = None,
):
    import subprocess
    import tempfile
    import shutil as _shutil

    tmp_dir = Path(tempfile.mkdtemp(prefix="video_"))
    try:
        _touch_async_result(event_id, {"status": "processing", "message": "video processing"})
        # ── 1. 오디오 추출 ──────────────────────────────────────────
        audio_path = tmp_dir / "audio.m4a"
        audio_ok = False
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(saved_path), "-vn", "-acodec", "copy", str(audio_path)],
                capture_output=True, timeout=120,
            )
            audio_ok = r.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0
        except Exception:
            pass

        # ── 2. Whisper STT ──────────────────────────────────────────
        stt_text = ""
        if audio_ok:
            stt_text = stt_whisper_local(str(audio_path))

        # ── 3. 키프레임 추출 (1fps, 최대 10장) ─────────────────────
        frames_dir = tmp_dir / "frames"
        frames_dir.mkdir()
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(saved_path),
                 "-vf", "fps=1", "-frames:v", "10",
                 str(frames_dir / "frame_%04d.jpg")],
                capture_output=True, timeout=60,
            )
        except Exception:
            pass

        # ── 4. 프레임 마스킹 ────────────────────────────────────────
        frames = sorted(frames_dir.glob("*.jpg"))
        frame_notes = []
        for i, frame_path in enumerate(frames):
            try:
                masked_path = MASKED_DIR / f"{event_id}_f{i:04d}.jpg"
                res = _mask_faces(
                    frame_path,
                    masked_path,
                    method=os.getenv("FACE_MASK_METHOD", "solid"),
                    blur_kernel=91,
                )
                face_count = res.get("face_count", 0)
                detector = res.get("detector", "?")
                shape = res.get("shape", "box")
                sources = res.get("segment_sources") or []
                source_note = f", {'+'.join(sources)}" if sources else ""
                frame_notes.append(f"t+{i}s: {face_count}명 감지 ({detector}, {shape}{source_note})")
            except Exception:
                frame_notes.append(f"t+{i}s: 분석 오류")

        # ── 5. MediaPipe Pose 측정 (저장하지 않는 로컬 evidence) ─────
        pose_analysis = analyze_pose_frames(
            frames,
            frame_interval_ms=1_000,
            duration_sec=max(1.0, len(frames)),
        )
        pose_summary = pose_analysis.get("summary")
        pose_capture_candidates = pose_analysis.get("candidates") or []

        # ── 6. 통합 텍스트 ──────────────────────────────────────────
        parts = []
        if patient_name:
            parts.append("[환자] " + patient_name)
        parts.append(
            "[Ray-Ban 영상] 파일=" + saved_path.name +
            " 크기=" + str(saved_path.stat().st_size // 1024) + "KB"
        )
        if stt_text:
            parts.append("[치료사 음성 기록 — S> 섹션 참고]" + chr(10) + stt_text)
        else:
            parts.append("[치료사 음성] 음성 없음 또는 추출 실패")
        if frame_notes:
            parts.append(
                "[영상 분석 " + str(len(frames)) + "프레임]" + chr(10) +
                chr(10).join(frame_notes)
            )

        combined = (chr(10) + chr(10)).join(parts)

        # ── 7. SOAP 차트 생성 ────────────────────────────────────────
        result = _process_event(
            source=source,
            event_type="video",
            text=combined,
            patient_name=patient_name,
            owner_org_id=owner_org_id,
            owner_provider_person_id=owner_provider_person_id,
            subject_person_id=subject_person_id,
            physio_client_id=physio_client_id,
            physio_session_id=physio_session_id,
        )
        inner_id = result.get("event_id", "")

        pose_capture_events: list[dict] = []
        if inner_id and pose_capture_candidates:
            attached_session = (result.get("visit_auto_attach") or {}).get("session") or {}
            with _conn() as pose_conn:
                pose_capture_events = _create_pose_capture_events(
                    pose_conn,
                    candidates=pose_capture_candidates,
                    visit_session_id=attached_session.get("id"),
                    encounter_id=attached_session.get("encounter_id") or physio_session_id,
                    organization_id=attached_session.get("organization_id") or owner_org_id,
                    provider_person_id=attached_session.get("provider_person_id") or owner_provider_person_id,
                    provider_role=attached_session.get("provider_role"),
                    subject_person_id=attached_session.get("subject_person_id") or subject_person_id,
                    source_event_id=inner_id,
                    source_media_id=event_id,
                    start_ms=0,
                    end_ms=max(1_000, len(frames) * 1_000) if frames else None,
                    capture_origin=_capture_origin_from_source(source),
                )
                pose_conn.commit()
        result["pose_summary"] = pose_summary
        result["pose_capture_events"] = pose_capture_events

        # outer_event_id 로도 차트 조회 가능하도록 복사
        if inner_id and inner_id != event_id:
            inner_chart = CHART_DIR / f"{inner_id}_11.txt"
            outer_chart = CHART_DIR / f"{event_id}_11.txt"
            if inner_chart.exists() and not outer_chart.exists():
                _shutil.copy(inner_chart, outer_chart)
            for outer_masked in MASKED_DIR.glob(f"{event_id}_f*.jpg"):
                inner_masked = MASKED_DIR / outer_masked.name.replace(event_id, inner_id, 1)
                if not inner_masked.exists():
                    _shutil.copy(outer_masked, inner_masked)
        if VIDEO_STORE and inner_id and saved_path.exists():
            _stage_raw_media_if_consent_active(
                saved_path,
                RawMediaStage(
                    event_id=inner_id,
                    kind="raw_video",
                    consent_id=str((result.get("policy") or {}).get("consent_id") or ""),
                ),
                owner_org_id=owner_org_id,
                owner_provider_person_id=owner_provider_person_id,
                subject_person_id=subject_person_id,
            )

        # iOS EventStatusResponse 구조에 맞게 래핑
        with _conn() as _c:
            ev_row = _c.execute(
                "SELECT id, source, event_type, raw_text, intent, status, created_at, owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id "
                "FROM events WHERE id = ?",
                (inner_id,),
            ).fetchone()
        event_obj = None
        if ev_row:
            event_obj = {
                "id": ev_row[0], "source": ev_row[1], "event_type": ev_row[2],
                "raw_text": ev_row[3], "intent": ev_row[4],
                "status": ev_row[5], "created_at": ev_row[6],
                "owner_org_id": ev_row[7],
                "owner_provider_person_id": ev_row[8],
                "subject_person_id": ev_row[9],
                "physio_client_id": ev_row[10],
                "physio_session_id": ev_row[11],
            }

        _touch_async_result(event_id, {
            "status": "done",
            "result": {
                "event": event_obj,
                "soap": result.get("soap"),
            },
        })
        _audit_log(inner_id or None, "info", "video processed")

    except Exception as e:
        code, msg, retryable = _normalize_error(e)
        logger.exception("video job failed event_id=%s code=%s", event_id, code)
        _audit_log(None, "error", f"video failed outer_event_id={event_id} code={code} msg={msg}")
        _touch_async_result(event_id, {
            "status": "error",
            "error": msg,
            "error_code": code,
            "retryable": retryable,
        })
    finally:
        _shutil.rmtree(tmp_dir, ignore_errors=True)
        saved_path.unlink(missing_ok=True)


def _authorize_raw_media_request(filename: str, request: Request) -> tuple[Path, str]:
    file_path = resolve_raw_media(RAW_MEDIA_DIR, filename)
    if file_path is None:
        raise HTTPException(status_code=404, detail="file not found")
    event_id = file_path.stem.rsplit("_", 1)[0]
    requested_org_id = request.headers.get("x-glasspt-org-id", "").strip()
    requested_provider_id = request.headers.get("x-glasspt-provider-person-id", "").strip()
    if not requested_org_id or not requested_provider_id:
        raise HTTPException(status_code=403, detail="scoped artifact access headers are required")
    with _conn() as conn:
        row = conn.execute(
            "SELECT owner_org_id, owner_provider_person_id FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="event not found")
    if row[0] != requested_org_id or row[1] != requested_provider_id:
        raise HTTPException(status_code=403, detail="artifact scope mismatch")
    return file_path, event_id


def _label_performance_value(payload: RehabLabelPayload) -> str:
    performance = (payload.performance_level or payload.performance or "").strip()
    if not performance:
        _error(422, "LABEL_PERFORMANCE_REQUIRED", "performance 또는 performance_level이 필요합니다.")
    return performance


# ── Glass Relay (phone-free PT HUD) ─────────────────────────────────────────
import threading as _threading

_glass_lock = _threading.Lock()
_glass_state: dict = {
    "visit_session_id": None,
    "patient": None,
    "mode": "standby",
    "message": "라이브 연결을 기다리는 중",
    "is_recording": False,
    "recording_start": None,
    "session_count": 0,
    "event_role_counts": {},
    "capture_role": "observation",
    "active_hud_candidate": None,
    "last_insight": None,
    "updated_at": None,
}
# Kept separate from the authenticated HUD state so the public Web App test
# can never disclose or modify a live clinician session.
_hud_test_state: dict = {
    "visit_session_id": None,
    "patient": None,
    "mode": "standby",
    "message": "HUD 테스트 연결 대기",
    "is_recording": False,
    "recording_start": None,
    "session_count": 0,
    "event_role_counts": {},
    "capture_role": "observation",
    "active_hud_candidate": None,
    "phase": "pre_review",
    "readiness": "ready",
    "error_state": None,
    "last_insight": None,
    "updated_at": None,
}
_glass_pending_command: list[dict] = []
_glass_pending_device_command: list[dict] = []


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


class AgentCueDryRunRequest(BaseModel):
    requested_tool: str = "generate_session_cue"
    event_id: Optional[str] = None
    session_id: Optional[str] = None
    mode: str = "ready"
    patient_alias: Optional[str] = None
    observed_phase: Optional[str] = Field(default=None, max_length=80)
    context_summary: Optional[str] = Field(default=None, max_length=240)
    risk_flags: list[str] = Field(default_factory=list, max_length=5)
    update_glass: bool = False

    class Config:
        extra = "forbid"


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


class GlassVisitStartRequest(BaseModel):
    candidate_id: Optional[str] = None
    update_glass: bool = True


def _apply_visit_session_hud(session: dict, insight: Optional[dict] = None) -> dict:
    hud = visit_hud_state(session)
    if insight:
        hud["last_insight"] = insight
    with _glass_lock:
        hud["capture_role"] = _glass_state.get("capture_role") or _role_for_visit_phase(hud.get("phase"))
        _glass_state.update(hud)
        _glass_state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    return hud


def _apply_hud_test_visit_state(session: dict) -> dict:
    """Store only the synthetic fixture's state for the public device check."""
    hud = visit_hud_state(session)
    hud.update(
        {
            "message": "HUD 테스트 방문 진행 중",
            "is_recording": False,
            "recording_start": None,
            "last_insight": None,
            "active_hud_candidate": None,
        }
    )
    with _glass_lock:
        _hud_test_state.update(hud)
        _hud_test_state["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return dict(_hud_test_state)


def _build_visit_session_write_plan(session: dict) -> dict:
    draft = session.get("draft_progress_note") or {}
    if not draft:
        draft = {
            "subjective": session.get("history_summary") or "방문 재활 세션 진행.",
            "objective": f"phase={session.get('phase')}, linked_events={len(session.get('event_ids') or [])}",
            "assessment": "AI 추출 결과는 clinician review 전 draft 상태.",
            "plan": "진행 노트 초안을 검토 후 확정.",
        }
    bundle = build_moai_export_bundle(
        event={
            "id": session["id"],
            "event_type": "text",
            "created_at": session.get("started_at"),
            "owner_org_id": session["organization_id"],
            "owner_provider_person_id": session["provider_person_id"],
            "subject_person_id": session["subject_person_id"],
            "physio_session_id": session["encounter_id"],
            "raw_text": session.get("history_summary") or "",
        },
        soap={
            "s": draft.get("subjective"),
            "o": draft.get("objective"),
            "a": draft.get("assessment"),
            "p": draft.get("plan"),
        },
        subject_person_id=session["subject_person_id"],
        provider_person_id=session["provider_person_id"],
        encounter_id=session["encounter_id"],
        capture_device="rayban_visit_session",
    )
    if bundle.get("notes"):
        bundle["notes"][0]["payload"]["note_format"] = "progress"
        bundle["notes"][0]["payload"]["source_type"] = "visit_session_orchestrator"
        bundle["notes"][0]["payload"]["note_content"] = "\n".join(
            str(draft.get(key) or "") for key in ["subjective", "objective", "assessment", "plan"]
        ).strip()
        bundle["notes"][0]["payload"]["ai_draft_snapshot"] = {
            "source_system": "rayban_pt",
            "source_visit_session_id": session["id"],
            "linked_event_ids": draft.get("evidence_ids") or session.get("event_ids") or [],
        }
    return build_moai_write_plan(bundle)


def _visit_sync_marker_event_id(session: dict) -> str:
    return f"visit-sync-{session['id']}"


def _enqueue_visit_session_sync_job(conn: sqlite3.Connection, session: dict, plan: dict) -> dict:
    marker_event_id = _visit_sync_marker_event_id(session)
    conn.execute(
        """
        INSERT INTO events (
            id, source, event_type, raw_text, intent, status, patient_name,
            owner_org_id, owner_provider_person_id, subject_person_id,
            physio_client_id, physio_session_id
        )
        VALUES (?, 'visit_session_orchestrator', 'text', ?, 'note', 'processed', NULL, ?, ?, ?, NULL, ?)
        ON CONFLICT(id) DO UPDATE SET
          raw_text=excluded.raw_text,
          status='processed',
          physio_session_id=excluded.physio_session_id
        """,
        (
            marker_event_id,
            f"Visit session ended; source_visit_session_id={session['id']}",
            session["organization_id"],
            session["provider_person_id"],
            session["subject_person_id"],
            session["encounter_id"],
        ),
    )
    _enqueue_moai_sync_job(conn, marker_event_id, "visit_session_ended")
    conn.execute(
        """
        UPDATE moai_sync_jobs
        SET operation_count = ?,
            skipped_count = ?,
            last_plan_summary = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE event_id = ?
        """,
        (
            int(plan.get("summary", {}).get("operation_count") or 0),
            int(plan.get("summary", {}).get("skipped_count") or 0),
            json.dumps(_summarize_moai_plan_for_job(plan), ensure_ascii=False),
            marker_event_id,
        ),
    )
    row = conn.execute(
        """
        SELECT id, event_id, status, trigger_reason, operation_count, skipped_count, attempts,
               last_error, last_plan_summary, last_result_summary, last_attempted_at,
               synced_at, created_at, updated_at
        FROM moai_sync_jobs
        WHERE event_id = ?
        """,
        (marker_event_id,),
    ).fetchone()
    return _moai_sync_job_from_row(row)


def _apply_visit_sync_pending_hud(session: dict, sync_job: dict) -> dict:
    hud = visit_hud_state(session)
    hud.update(
        {
            "mode": "summary",
            "message": f"노트 초안 준비 · 전송 대기 {sync_job.get('operation_count', 0)}건",
            "readiness": "sync_pending",
            "error_state": None,
            "last_insight": {
                "id": f"sync-pending:{sync_job['event_id']}",
                "title": "전송 대기",
                "body": "노트 초안 준비 · 서버 큐 대기",
                "severity": "info",
                "lens_safe": True,
                "source": "moai_sync_queue",
            },
        }
    )
    with _glass_lock:
        _glass_state.update(hud)
        _glass_state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    return hud


def _trim_event_text(value: Optional[str], limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _visit_linked_events(conn: sqlite3.Connection, session: dict) -> list[dict]:
    event_ids = [str(event_id) for event_id in session.get("event_ids") or [] if str(event_id).strip()]
    refs_by_id = {
        str(ref.get("event_id")): ref
        for ref in session.get("event_refs") or []
        if str(ref.get("event_id") or "").strip()
    }
    rows = []
    if event_ids:
        placeholders = ",".join("?" for _ in event_ids)
        rows = conn.execute(
            f"""
            SELECT id, event_type, raw_text, intent, created_at
            FROM events
            WHERE id IN ({placeholders})
            """,
            event_ids,
        ).fetchall()
    by_id = {
        row[0]: {
            "id": row[0],
            "event_type": row[1],
            "raw_text": row[2] or "",
            "intent": row[3] or "",
            "created_at": row[4],
            "role": refs_by_id.get(row[0], {}).get("role"),
            "phase": refs_by_id.get(row[0], {}).get("phase"),
            "payload": {},
            "candidate_type": "",
        }
        for row in rows
    }

    capture_rows = conn.execute(
        f"{_capture_event_select()} WHERE visit_session_id = ? ORDER BY created_at ASC",
        (session["id"],),
    ).fetchall()
    capture_events: list[dict] = []
    for row in capture_rows:
        event = _capture_event_from_row(row)
        if event["id"] in by_id:
            continue
        payload = event.get("payload") or {}
        action_type = str(payload.get("action_type") or "observation")
        role = action_type if action_type in {"assessment", "intervention", "home_program", "observation"} else "observation"
        capture_events.append(
            {
                "id": event["id"],
                "event_type": event["event_type"],
                "candidate_type": event["candidate_type"],
                "raw_text": _capture_event_note_text(event),
                "intent": action_type,
                "created_at": event["created_at"],
                "role": refs_by_id.get(event["id"], {}).get("role") or role,
                "phase": refs_by_id.get(event["id"], {}).get("phase") or session.get("phase"),
                "payload": payload,
                "source_type": event["source_type"],
            }
        )

    return [by_id[event_id] for event_id in event_ids if event_id in by_id] + capture_events


def _capture_event_note_text(event: dict) -> str:
    """Render a compact, clinician-reviewable line from one capture candidate."""

    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    nested = payload.get("semantic") if isinstance(payload.get("semantic"), dict) else {}

    def read(key: str):
        return nested.get(key) if key in nested else payload.get(key)

    value_labels = {
        "assessment_type": "평가",
        "assessment_name": "평가 도구",
        "intervention_type": "중재",
        "instruction_type": "교육",
        "instruction_detail": "지시 상세",
        "activity_name": "동작명",
        "core_task": "과제",
        "body_position": "체위",
        "assist_level": "보조",
        "performance_level": "수행",
        "tolerance": "내약성",
        "fatigue_level": "피로",
        "repetition_count": "반복",
        "set_count": "세트",
        "hold_duration_seconds": "유지(초)",
        "rest_duration_seconds": "휴식(초)",
        "pain_score": "통증 점수",
        "rpe_score": "RPE",
        "equipment": "도구",
        "compensations": "보상",
        "safety_flags": "안전",
    }
    details: list[str] = []
    for key in (
        "assessment_type",
        "assessment_name",
        "intervention_type",
        "instruction_type",
        "instruction_detail",
        "activity_name",
        "core_task",
        "body_position",
        "assist_level",
        "performance_level",
        "tolerance",
        "fatigue_level",
        "repetition_count",
        "hold_duration_seconds",
        "compensations",
        "safety_flags",
    ):
        value = read(key)
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(item).replace("_", " ") for item in value)
        else:
            rendered = str(value).replace("_", " ")
        details.append(f"{value_labels[key]}={rendered}")

    base = str(payload.get("source_text") or payload.get("label") or event.get("candidate_type") or "capture event").strip()
    if details:
        return _trim_event_text(f"{base} ({'; '.join(details)})", limit=220)
    return _trim_event_text(base, limit=220)


def _linked_event_bucket(event: dict) -> str:
    role = str(event.get("role") or "").strip()
    if role in {"assessment", "intervention", "home_program", "observation"}:
        return role
    text = f"{event.get('intent') or ''} {event.get('raw_text') or ''}".lower()
    if any(token in text for token in ["home program", "home exercise", "과제", "homework", "assigned"]):
        return "home_program"
    if any(token in text for token in ["assessment", "평가", "test", "tug", "measure"]):
        return "assessment"
    if any(token in text for token in ["intervention", "중재", "training", "practice", "completed"]):
        return "intervention"
    return "observation"


def _build_linked_event_progress_note(session: dict, linked_events: list[dict]) -> dict:
    lines_by_bucket = {"assessment": [], "intervention": [], "home_program": [], "observation": []}
    for event in linked_events:
        text = _trim_event_text(event.get("raw_text"))
        if not text:
            continue
        lines_by_bucket[_linked_event_bucket(event)].append(text)

    objective_lines = []
    if lines_by_bucket["assessment"]:
        objective_lines.append("평가: " + "; ".join(lines_by_bucket["assessment"][:3]))
    if lines_by_bucket["observation"]:
        objective_lines.append("관찰: " + "; ".join(lines_by_bucket["observation"][:3]))
    if lines_by_bucket["intervention"]:
        objective_lines.append("중재: " + "; ".join(lines_by_bucket["intervention"][:3]))

    plan_lines = []
    if lines_by_bucket["home_program"]:
        plan_lines.append("과제: " + "; ".join(lines_by_bucket["home_program"][:3]))
    plan_lines.append("다음 방문 시 반응과 수행 안전성을 재확인.")

    assessment_basis = lines_by_bucket["assessment"] or lines_by_bucket["observation"] or lines_by_bucket["intervention"]
    return {
        "note_format": "progress",
        "status": "draft",
        "requires_approval": True,
        "subjective": session.get("history_summary") or "방문 재활 세션 진행.",
        "objective": "\n".join(objective_lines) or f"linked_events={len(linked_events)}.",
        "assessment": (
            "방문 중 기록된 평가/관찰/중재 내용을 바탕으로 clinician review 필요: "
            + "; ".join(assessment_basis[:3])
            if assessment_basis
            else "AI 추출 결과는 clinician review 전 draft 상태."
        ),
        "plan": "\n".join(plan_lines),
        "evidence_ids": [str(event["id"]) for event in linked_events if str(event.get("id") or "").strip()],
    }


def _refresh_visit_progress_note_from_events(conn: sqlite3.Connection, session: dict) -> dict:
    linked_events = _visit_linked_events(conn, session)
    if not linked_events:
        return session
    draft = _build_linked_event_progress_note(session, linked_events)
    conn.execute(
        "UPDATE visit_sessions SET draft_progress_note = ?, updated_at = ? WHERE id = ?",
        (json.dumps(draft, ensure_ascii=False, separators=(",", ":")), datetime.utcnow().isoformat() + "Z", session["id"]),
    )
    refreshed = get_visit_session(conn, session["id"])
    return refreshed or session


VISIT_EVENT_ROLE_ORDER = ["observation", "assessment", "intervention", "home_program"]


def _role_for_visit_phase(phase: Optional[str]) -> str:
    clean = (phase or "").strip()
    if clean in {"assessment", "intervention", "home_program"}:
        return clean
    return "observation"


def _next_visit_event_role(current: Optional[str]) -> str:
    clean = (current or "").strip()
    try:
        index = VISIT_EVENT_ROLE_ORDER.index(clean)
    except ValueError:
        return VISIT_EVENT_ROLE_ORDER[0]
    return VISIT_EVENT_ROLE_ORDER[(index + 1) % len(VISIT_EVENT_ROLE_ORDER)]


def _set_hud_capture_role(role: str) -> None:
    with _glass_lock:
        _glass_state["capture_role"] = role
        _glass_state["updated_at"] = datetime.utcnow().isoformat() + "Z"


def _active_capture_role_from_hud(default_phase: Optional[str] = None) -> str:
    with _glass_lock:
        role = str(_glass_state.get("capture_role") or "").strip()
    return role if role in VISIT_EVENT_ROLE_ORDER else _role_for_visit_phase(default_phase)


def _auto_attach_event_to_active_visit(conn: sqlite3.Connection, event_id: str) -> Optional[dict]:
    session_id = _active_visit_session_id_from_hud()
    if not session_id:
        return None
    session = get_visit_session(conn, session_id)
    if not session or session.get("status") != "active":
        return None
    role = _active_capture_role_from_hud(str(session.get("phase") or ""))
    attached = attach_visit_event(conn, session_id, event_id, role=role, phase=session.get("phase"))
    hud = _apply_visit_session_hud(attached)
    return {"session": attached, "role": role, "glass_state": hud}


GLASS_COMMANDS = {
    "start_visit",
    "toggle_recording",
    "next_phase",
    "next_role",
    "end_visit_session",
    "cycle_record_preview",
    "nav_up",
    "nav_down",
    "nav_left",
    "nav_right",
    "select_focused",
    "start_live",
    "open_capture_history",
    "primary_action",
    "select_patient",
    "show_recommendations",
    "approve_candidate",
    "discard_candidate",
    "capture_photo",
    "start_audio",
    "stop_audio",
}

# Commands that must reach the paired iPhone. The Web App can request them,
# but it cannot perform camera or microphone work itself.
GLASS_DEVICE_COMMANDS = {
    "toggle_recording",
    "start_recording",
    "stop_recording",
    "start_live",
    "capture_photo",
    "start_audio",
    "stop_audio",
    "open_capture_history",
    "select_patient",
    "show_recommendations",
    "primary_action",
}
NEURAL_BAND_GESTURE_MAP = {
    "toggle_recording": "toggle_recording",
    "tap": "toggle_recording",
    "single_tap": "toggle_recording",
    "double_tap": "toggle_recording",
    "press": "toggle_recording",
    "squeeze": "toggle_recording",
    "photo": "capture_photo",
    "capture_photo": "capture_photo",
    "camera": "capture_photo",
    "snapshot": "capture_photo",
    "voice": "start_audio",
    "audio": "start_audio",
    "stt": "start_audio",
    "start_audio": "start_audio",
    "stop_audio": "stop_audio",
    "long_press": "end_visit_session",
    "hold": "end_visit_session",
    "pinch_hold": "end_visit_session",
    "down": "nav_down",
    "swipe_down": "nav_down",
    "downward": "nav_down",
    "up": "nav_up",
    "swipe_up": "nav_up",
    "upward": "nav_up",
    "raise": "nav_up",
    "lift": "nav_up",
    "select": "select_focused",
    "enter": "select_focused",
    "confirm": "select_focused",
    "pinch": "approve_candidate",
    "approve": "approve_candidate",
    "accept": "approve_candidate",
    "yes": "approve_candidate",
    "reject": "discard_candidate",
    "discard": "discard_candidate",
    "delete": "discard_candidate",
    "no": "discard_candidate",
    "open": "start_visit",
    "start": "start_visit",
    "start_visit": "start_visit",
    "primary_action": "select_focused",
    "next": "nav_right",
    "swipe_right": "nav_right",
    "right": "nav_right",
    "forward": "nav_right",
    "phase": "next_phase",
    "role": "next_role",
    "next_role": "next_role",
    "swipe_left": "nav_left",
    "left": "nav_left",
    "patient": "select_patient",
    "select_patient": "select_patient",
    "patient_select": "select_patient",
    "history": "cycle_record_preview",
    "records": "cycle_record_preview",
    "open_history": "cycle_record_preview",
    "recommend": "show_recommendations",
    "recommendations": "show_recommendations",
    "assessment": "show_recommendations",
    "evaluation": "show_recommendations",
    "show_recommendations": "show_recommendations",
    "end": "end_visit_session",
    "finish": "end_visit_session",
    "complete": "end_visit_session",
    "end_visit_session": "end_visit_session",
}
AGENT_ALLOWED_TOOLS = {"generate_session_cue"}
AGENT_BLOCKED_ACTIONS = [
    "production_supabase_write",
    "patient_message",
    "billing",
    "delete_data",
    "model_training",
    "model_promotion",
]


def _short_lens_text(text: str, limit: int = 80) -> str:
    clean = re.sub(r"\s+", " ", redact_phi(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _build_dry_run_session_cue(payload: AgentCueDryRunRequest) -> dict:
    mode = (payload.mode or "ready").strip().lower()
    flags = [str(flag).strip().lower() for flag in payload.risk_flags if str(flag).strip()]
    phase = _short_lens_text(payload.observed_phase or "", limit=36)
    summary = _short_lens_text(payload.context_summary or "", limit=54)

    severity = "info"
    title = "세션 준비"
    body = "환자 선택과 동의를 확인하세요."

    if mode == "recording":
        title = "관찰 유지"
        body = "자세와 피로 신호를 짧게 확인하세요."
    elif mode in {"uploading", "analyzing"}:
        title = "처리 중"
        body = "완료 후 iPhone에서 초안을 검토하세요."
    elif mode == "success":
        title = "검토 필요"
        body = "차트와 라벨을 승인 전 확인하세요."
    elif mode == "error":
        title = "앱 확인"
        body = "상세 오류는 iPhone에서 확인하세요."
        severity = "warning"

    if "fall" in flags or "pain" in flags or "safety" in flags:
        title = "안전 확인"
        body = "중단 여부를 확인하고 도움을 요청하세요."
        severity = "warning"
    elif "fatigue" in flags:
        title = "피로 관찰"
        body = "휴식 필요 여부를 확인하세요."
        severity = "info"

    if phase and mode == "recording":
        body = _short_lens_text(f"{phase}: {body}", limit=80)
    elif summary and mode == "ready":
        body = _short_lens_text(summary, limit=80)

    return {
        "id": str(uuid.uuid4()),
        "title": _short_lens_text(title, limit=32),
        "body": _short_lens_text(body, limit=80),
        "severity": severity,
        "lens_safe": True,
        "source": "agent_gateway_dry_run",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


def _existing_event_id_for_audit(event_id: Optional[str]) -> Optional[str]:
    if not event_id:
        return None
    try:
        with _conn() as conn:
            row = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        return event_id if row else None
    except Exception:
        return None


def _lens_safe_patient_alias(value: Optional[str]) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    if not clean:
        return "Patient"
    if re.match(r"^[A-Za-z][A-Za-z\s.'-]{1,}$", clean):
        return " ".join(
            f"{part[0].upper()}." if index == 0 else part[0].upper()
            for index, part in enumerate(clean.split())
            if part
        ) or "Patient"
    if len(clean) <= 2:
        return clean[0] + "*"
    return clean[0] + "*" + clean[-1]


def _visit_candidate_from_event_row(row) -> dict:
    return {
        "id": row[0],
        "patient_alias": _lens_safe_patient_alias(row[1]),
        "organization_id": row[2],
        "provider_person_id": row[3],
        "subject_person_id": row[4],
        "physio_client_id": row[5],
        "encounter_id": row[6] or row[0],
        "source_event_id": row[0],
        "session_label": row[7] or "방문 재활",
        "created_at": row[8],
        "readiness": "ready" if row[2] and row[3] and row[4] else "missing_identity",
        "source": "local_events",
    }


def _glass_remote_scope() -> tuple[Optional[str], Optional[str]]:
    provider_person_id = (
        os.getenv("RAYBAN_HUD_PROVIDER_PERSON_ID")
        or os.getenv("GLASS_PROVIDER_PERSON_ID")
        or os.getenv("MOAI_WEB_PROVIDER_PERSON_ID")
        or ""
    ).strip()
    organization_id = (
        os.getenv("RAYBAN_HUD_ORGANIZATION_ID")
        or os.getenv("GLASS_ORGANIZATION_ID")
        or os.getenv("MOAI_WEB_ORGANIZATION_ID")
        or ""
    ).strip()
    return provider_person_id or None, organization_id or None


def _moai_fetch_rows(table: str, params) -> list[dict]:
    config = load_moai_writer_config()
    if config is None:
        return []
    headers = {
        "apikey": config.api_key,
        "Accept": "application/json",
    }
    if config.auth_header:
        headers["Authorization"] = config.auth_header
    response = requests.get(
        f"{config.base_url}/rest/v1/{table}",
        headers=headers,
        params=params,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _hud_scope_signing_secret() -> str:
    return HUD_SCOPE_SECRET or BRIDGE_API_KEY


def build_hud_scope_token(
    organization_id: str,
    provider_person_id: str,
    expires_at: Optional[datetime] = None,
) -> str:
    secret = _hud_scope_signing_secret()
    if not secret:
        raise RuntimeError("RAYBAN_HUD_SCOPE_SECRET or BRIDGE_API_KEY is required")
    exp = expires_at or (datetime.now(timezone.utc) + timedelta(hours=12))
    payload = {
        "organization_id": organization_id,
        "provider_person_id": provider_person_id,
        "exp": int(exp.timestamp()),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"h1.{body}.{_b64url_encode(sig)}"


def _decode_hud_scope_token(raw_token: str) -> dict:
    secret = _hud_scope_signing_secret()
    if not secret:
        raise ValueError("HUD scope secret is not configured")
    parts = raw_token.strip().split(".")
    if len(parts) != 3 or parts[0] != "h1":
        raise ValueError("invalid HUD scope token format")
    expected = hmac.new(secret.encode("utf-8"), parts[1].encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _b64url_decode(parts[2])
    except Exception as exc:
        raise ValueError("invalid HUD scope token signature") from exc
    if not hmac.compare_digest(expected, actual):
        raise ValueError("invalid HUD scope token signature")
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid HUD scope token payload") from exc
    exp = int(payload.get("exp") or 0)
    if exp and exp < int(datetime.now(timezone.utc).timestamp()):
        raise ValueError("expired HUD scope token")
    organization_id = str(payload.get("organization_id") or "").strip()
    provider_person_id = str(payload.get("provider_person_id") or "").strip()
    if not organization_id or not provider_person_id:
        raise ValueError("HUD scope token requires organization_id and provider_person_id")
    return {
        "organization_id": organization_id,
        "provider_person_id": provider_person_id,
        "exp": exp,
    }


def _hud_scope_from_request(request: Optional[Request] = None) -> dict:
    if request is not None and _is_hud_test_request(request):
        return dict(HUD_TEST_SCOPE)
    raw_token = ""
    if request is not None:
        raw_token = request.headers.get("x-hud-token", "") or request.query_params.get("hud_token", "")
    if raw_token:
        try:
            return _decode_hud_scope_token(raw_token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail={"code": "INVALID_HUD_SCOPE_TOKEN", "message": str(exc)})
    provider_person_id, organization_id = _glass_remote_scope()
    scope: dict[str, str] = {}
    if provider_person_id:
        scope["provider_person_id"] = provider_person_id
    if organization_id:
        scope["organization_id"] = organization_id
    return scope


def _candidate_matches_hud_scope(candidate: dict, scope: Optional[dict]) -> bool:
    if not scope:
        return True
    provider_person_id = str(scope.get("provider_person_id") or "").strip()
    organization_id = str(scope.get("organization_id") or "").strip()
    if provider_person_id and candidate.get("provider_person_id") != provider_person_id:
        return False
    if organization_id and candidate.get("organization_id") != organization_id:
        return False
    return True


def _today_visit_window_utc() -> tuple[str, str, datetime]:
    now_local = datetime.now().astimezone()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    now_utc = now_local.astimezone(timezone.utc)
    start_utc = start_local.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    end_utc = end_local.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return start_utc, end_utc, now_utc


def _parse_moai_datetime(value: Optional[str]) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.max.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)


def _looks_like_uuid(value: Optional[str]) -> bool:
    try:
        uuid.UUID(str(value or ""))
        return True
    except ValueError:
        return False


def _visit_candidate_from_encounter_row(row: dict) -> Optional[dict]:
    organization_id = str(row.get("organization_id") or "").strip()
    provider_person_id = str(row.get("provider_person_id") or "").strip()
    subject_person_id = str(row.get("subject_person_id") or "").strip()
    encounter_id = str(row.get("id") or "").strip()
    if not (organization_id and provider_person_id and subject_person_id and encounter_id):
        return None
    short_subject = subject_person_id.split("-", 1)[0][:6].upper()
    return {
        "id": f"moai:{encounter_id}",
        "patient_alias": f"P-{short_subject}" if short_subject else "Patient",
        "organization_id": organization_id,
        "provider_person_id": provider_person_id,
        "subject_person_id": subject_person_id,
        "physio_client_id": None,
        "encounter_id": encounter_id,
        "source_event_id": None,
        "session_label": str(row.get("session_type") or "방문 재활"),
        "created_at": row.get("period_start"),
        "readiness": "ready",
        "source": "moai_web.encounters",
        "status": row.get("status"),
        "care_setting": row.get("care_setting"),
    }


def _list_moai_glass_visit_candidates(limit: int = 10, scope: Optional[dict] = None) -> list[dict]:
    provider_person_id, organization_id = _glass_remote_scope()
    if scope:
        provider_person_id = str(scope.get("provider_person_id") or provider_person_id or "").strip()
        organization_id = str(scope.get("organization_id") or organization_id or "").strip()
    if not provider_person_id:
        return []
    if not _looks_like_uuid(provider_person_id) or (organization_id and not _looks_like_uuid(organization_id)):
        return []
    window_start, window_end, now_utc = _today_visit_window_utc()
    params = [
        ("select", "id,organization_id,provider_person_id,subject_person_id,period_start,session_type,status,care_setting"),
        ("provider_person_id", f"eq.{provider_person_id}"),
        ("subject_person_id", "not.is.null"),
        ("period_start", f"gte.{window_start}"),
        ("period_start", f"lt.{window_end}"),
        ("order", "period_start.asc.nullslast"),
        ("limit", str(max(1, min(limit, 50)))),
    ]
    if organization_id:
        params.append(("organization_id", f"eq.{organization_id}"))
    try:
        rows = _moai_fetch_rows("encounters", params)
    except Exception as exc:
        logger.warning("moai glass visit candidate lookup failed: %s", exc)
        return []
    candidates: list[dict] = []
    for row in rows:
        candidate = _visit_candidate_from_encounter_row(row)
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda candidate: abs((_parse_moai_datetime(candidate.get("created_at")) - now_utc).total_seconds()))
    return candidates


def _list_glass_visit_candidates(conn: sqlite3.Connection, limit: int = 10, scope: Optional[dict] = None) -> list[dict]:
    remote_candidates = _list_moai_glass_visit_candidates(limit=limit, scope=scope)
    if remote_candidates:
        return remote_candidates

    rows = conn.execute(
        """
        SELECT id, patient_name, owner_org_id, owner_provider_person_id, subject_person_id,
               physio_client_id, physio_session_id, intent, created_at
        FROM events
        WHERE COALESCE(owner_org_id, '') != ''
          AND COALESCE(owner_provider_person_id, '') != ''
          AND COALESCE(subject_person_id, '') != ''
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (max(1, min(limit, 50)),),
    ).fetchall()
    seen: set[tuple[str, str, str]] = set()
    candidates: list[dict] = []
    for row in rows:
        candidate = _visit_candidate_from_event_row(row)
        if not _candidate_matches_hud_scope(candidate, scope):
            continue
        key = (
            candidate["organization_id"],
            candidate["subject_person_id"],
            candidate["encounter_id"],
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _get_glass_visit_candidate(
    conn: sqlite3.Connection,
    candidate_id: Optional[str] = None,
    offset: int = 0,
    scope: Optional[dict] = None,
) -> Optional[dict]:
    if candidate_id:
        for candidate in _list_moai_glass_visit_candidates(limit=50, scope=scope):
            if candidate["id"] == candidate_id or candidate["encounter_id"] == candidate_id:
                return candidate
        row = conn.execute(
            """
            SELECT id, patient_name, owner_org_id, owner_provider_person_id, subject_person_id,
                   physio_client_id, physio_session_id, intent, created_at
            FROM events
            WHERE id = ? OR physio_session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (candidate_id, candidate_id),
        ).fetchone()
        candidate = _visit_candidate_from_event_row(row) if row else None
        if candidate and _candidate_matches_hud_scope(candidate, scope):
            return candidate
        return None
    candidates = _list_glass_visit_candidates(conn, limit=max(10, offset + 1), scope=scope)
    if not candidates:
        return None
    return candidates[offset % len(candidates)]


def _visit_candidate_history_summary(candidate: dict) -> str:
    if candidate.get("source_event_id"):
        return f"HUD visit started from event {candidate['source_event_id']}."
    return f"HUD visit started from {candidate.get('source') or 'visit candidate'} {candidate['encounter_id']}."


def _build_subject_record_preview(subject_context: dict) -> dict:
    notes = _safe_moai_fetch_rows(
        "encounter_notes",
        _subject_history_params(
            subject_context,
            select="id,encounter_id,note_format,status,approval_status,objective,assessment,plan,note_content,created_at",
            order="created_at.desc.nullslast",
        ),
    )
    observations = _safe_moai_fetch_rows(
        "observations",
        _subject_history_params(
            subject_context,
            select="id,code,code_display,status,interpretation,value_string,note,effective_datetime,created_at",
            order="effective_datetime.desc.nullslast",
        ),
    )
    activity_sessions = _safe_moai_fetch_rows(
        "activity_sessions",
        _subject_history_params(
            subject_context,
            select="id,activity_type,status,notes,metrics,created_at",
            order="created_at.desc.nullslast",
        ),
    )
    media_summaries = _safe_moai_fetch_rows(
        "client_media_summaries",
        _subject_history_params(
            subject_context,
            select="id,title,summary_text,media_kind,body_region,observed_at,created_at",
            order="observed_at.desc.nullslast",
        ),
    )

    pending_notes = [
        row
        for row in notes
        if str(row.get("approval_status") or "").lower() in {"pending", "none", ""}
        or str(row.get("status") or "").lower() == "draft"
    ]
    signals: list[str] = []
    if notes:
        signals.append(f"노트 {len(notes)}")
    if pending_notes:
        signals.append("미승인 확인")
    if observations:
        signals.append(f"평가 {len(observations)}")
    if activity_sessions:
        signals.append(f"중재/과제 {len(activity_sessions)}")
    if media_summaries:
        signals.append(f"미디어요약 {len(media_summaries)}")
    cue = " · ".join(signals[:3]) if signals else "최근 기록 없음"
    if len(cue) > 44:
        cue = cue[:41].rstrip() + "..."
    detail_lines = _record_preview_detail_lines(notes, observations, activity_sessions, media_summaries)
    count_lines = [
        f"노트 {len(notes)}" + (f" · 미승인 {len(pending_notes)}" if pending_notes else ""),
        f"평가 {len(observations)} · 중재/과제 {len(activity_sessions)}",
        f"미디어 요약 {len(media_summaries)}",
    ]
    lines = detail_lines or count_lines
    return {
        "title": "기록 요약",
        "cue": cue,
        "lines": lines,
        "lens_safe": True,
        "source": "moai_web.record_preview",
        "signals": {
            "notes_count": len(notes),
            "pending_notes_count": len(pending_notes),
            "observations_count": len(observations),
            "activity_sessions_count": len(activity_sessions),
            "media_summaries_count": len(media_summaries),
        },
    }


def _record_preview_detail_lines(
    notes: list[dict],
    observations: list[dict],
    activity_sessions: list[dict],
    media_summaries: list[dict],
) -> list[str]:
    lines: list[str] = []
    for row in notes[:1]:
        text = row.get("assessment") or row.get("objective") or row.get("plan") or row.get("note_content")
        if text:
            lines.append("노트: " + _short_lens_text(str(text), limit=58))
    for row in observations[:1]:
        label = str(row.get("code_display") or row.get("code") or "평가").strip()
        value = row.get("value_string") or row.get("interpretation") or row.get("note")
        if value:
            lines.append(_short_lens_text(f"평가: {label} {value}", limit=58))
        elif label:
            lines.append(_short_lens_text(f"평가: {label}", limit=58))
    for row in activity_sessions[:1]:
        activity = str(row.get("activity_type") or "중재").strip()
        note = row.get("notes")
        if not note and isinstance(row.get("metrics"), dict):
            metric_parts = [str(value) for value in row["metrics"].values() if value]
            note = " ".join(metric_parts[:2])
        line = f"중재: {activity}" + (f" · {note}" if note else "")
        lines.append(_short_lens_text(line, limit=58))
    for row in media_summaries[:1]:
        summary = row.get("summary_text") or row.get("title") or row.get("body_region") or row.get("media_kind")
        if summary:
            lines.append("미디어: " + _short_lens_text(str(summary), limit=54))
    return lines[:6]


def _build_local_candidate_record_preview(conn: sqlite3.Connection, candidate: dict) -> dict:
    source_event_id = str(candidate.get("source_event_id") or "").strip()
    encounter_id = str(candidate.get("encounter_id") or "").strip()
    rows = conn.execute(
        """
        SELECT id, event_type, raw_text, intent, created_at
        FROM events
        WHERE id = ? OR physio_session_id = ?
        ORDER BY created_at DESC
        LIMIT 3
        """,
        (source_event_id, encounter_id),
    ).fetchall()
    lines: list[str] = []
    marker_count = 0
    for row in rows:
        raw_text = str(row[2] or "")
        if raw_text.startswith("Visit session ended; source_visit_session_id="):
            marker_count += 1
            text = "세션 종료 · 노트 초안 생성"
        else:
            text = _short_lens_text(raw_text or row[3] or row[1] or "방문 기록", limit=58)
        if text:
            label = "기록" if str(row[1] or "").lower() == "text" else (row[1] or "기록")
            line = f"{label}: {text}"
            if line not in lines:
                lines.append(line)
    demo_only = bool(rows) and marker_count == len(rows)
    if demo_only or not lines:
        lines = [
            "데모 노트: 최근 방문 후 기립 균형 훈련 지속",
            "데모 평가: min-mod assist, 피로 시 체간 흔들림",
            "데모 관찰: 보행 시작 시 좌우 체중 이동 지연",
            "데모 중재: sit-to-stand 5회, 휴식 2회",
            "데모 cue: 발 전체 접지 후 일어나기",
            "데모 과제: 보호자 도움 하 서기 3회",
        ]
    return {
        "title": "기록 요약",
        "cue": "데모 기록" if demo_only or not rows else "로컬 기록 " + str(len(rows)),
        "lines": lines[:6],
        "lens_safe": True,
        "source": "demo.record_preview" if demo_only or not rows else "local.record_preview",
        "signals": {
            "notes_count": len(rows),
            "pending_notes_count": 0,
            "observations_count": 0,
            "activity_sessions_count": 0,
            "media_summaries_count": 0,
        },
    }


def _attach_record_preview_to_candidate(conn: sqlite3.Connection, candidate: Optional[dict]) -> Optional[dict]:
    if not candidate:
        return None
    hydrated = dict(candidate)
    subject_person_id = str(hydrated.get("subject_person_id") or "").strip()
    organization_id = str(hydrated.get("organization_id") or "").strip()
    if hydrated.get("source") != "local_events" and subject_person_id and _looks_like_uuid(subject_person_id) and (not organization_id or _looks_like_uuid(organization_id)):
        hydrated["record_preview"] = _build_subject_record_preview(hydrated)
    else:
        hydrated["record_preview"] = _build_local_candidate_record_preview(conn, hydrated)
    return hydrated


def _safe_moai_fetch_rows(table: str, params: dict[str, str]) -> list[dict]:
    try:
        return _moai_fetch_rows(table, params)
    except Exception as exc:
        logger.warning("moai pre-review lookup failed table=%s: %s", table, exc)
        return []


def _subject_history_params(session: dict, *, select: str, order: str, limit: int = 3) -> dict[str, str]:
    params = {
        "select": select,
        "subject_person_id": f"eq.{session['subject_person_id']}",
        "order": order,
        "limit": str(max(1, min(limit, 10))),
    }
    organization_id = str(session.get("organization_id") or "").strip()
    if organization_id:
        params["organization_id"] = f"eq.{organization_id}"
    return params


def _build_visit_pre_review(session: dict) -> dict:
    preview = _build_subject_record_preview(session)
    cue = preview["cue"] if preview["cue"] != "최근 기록 없음" else "기록 확인 후 평가로 진행"
    severity = "warning" if preview["signals"]["pending_notes_count"] else "info"
    return {
        "id": f"pre-review:{session['id']}",
        "title": "Pre-review",
        "body": cue,
        "severity": severity,
        "lens_safe": True,
        "source": "moai_web.pre_review",
        "signals": preview["signals"],
        "lines": preview["lines"],
    }


def _attach_pre_review_to_session(conn: sqlite3.Connection, session: dict) -> tuple[dict, dict]:
    pre_review = _build_visit_pre_review(session)
    updated = update_visit_phase(conn, session["id"], "pre_review", pre_review["body"])
    return updated, pre_review


def _build_visit_end_checkpoint_cue(session: dict) -> str:
    event_count = len(session.get("event_ids") or [])
    role_counts = {"assessment": 0, "intervention": 0, "home_program": 0, "observation": 0}
    for ref in session.get("event_refs") or []:
        role = str(ref.get("role") or "observation")
        role_counts[role] = role_counts.get(role, 0) + 1
    role_parts = []
    if role_counts.get("assessment"):
        role_parts.append(f"평가 {role_counts['assessment']}")
    if role_counts.get("intervention"):
        role_parts.append(f"중재 {role_counts['intervention']}")
    if role_counts.get("home_program"):
        role_parts.append(f"과제 {role_counts['home_program']}")
    status_parts = [
        " · ".join(role_parts) if role_parts else f"기록 {event_count}",
        f"phase {session.get('phase') or 'unknown'}",
    ]
    if session.get("recording_status") == "recording":
        status_parts.append("녹화중")
    return " · ".join(status_parts + ["확인=종료"])


def _generate_command_cue_if_needed(command: str, source: str) -> Optional[dict]:
    if command != "show_recommendations":
        return None
    with _glass_lock:
        mode = str(_glass_state.get("mode") or ("recording" if _glass_state.get("is_recording") else "ready"))
        session_count = int(_glass_state.get("session_count") or 0)
    payload = AgentCueDryRunRequest(
        mode=mode,
        observed_phase="session cue",
        context_summary=f"HUD request from {source}; session {session_count}",
        risk_flags=["fatigue"] if mode == "recording" else [],
    )
    cue = _build_dry_run_session_cue(payload)
    with _glass_lock:
        _glass_state["last_insight"] = cue
        _glass_state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _audit_log(None, "info", f"agent command cue generated source={source} mode={mode}")
    return cue


VISIT_PHASE_ORDER = ["pre_review", "assessment", "intervention", "home_program", "summary"]
SERVER_EXECUTED_GLASS_COMMANDS = {
    "start_visit",
    "toggle_recording",
    "next_phase",
    "next_role",
    "end_visit_session",
    "approve_candidate",
    "discard_candidate",
}


def _active_visit_session_id_from_hud() -> Optional[str]:
    with _glass_lock:
        session_id = str(_glass_state.get("visit_session_id") or "").strip()
    return session_id or None


def _glass_state_snapshot() -> dict:
    with _glass_lock:
        return dict(_glass_state)


def _next_visit_phase(current: str) -> str:
    try:
        index = VISIT_PHASE_ORDER.index(current)
    except ValueError:
        return "assessment"
    return VISIT_PHASE_ORDER[min(index + 1, len(VISIT_PHASE_ORDER) - 1)]


def _execute_visit_hud_command(
    command: str,
    source: str,
    metadata: Optional[dict] = None,
    scope: Optional[dict] = None,
) -> Optional[dict]:
    if command not in SERVER_EXECUTED_GLASS_COMMANDS:
        return None

    if command in {"approve_candidate", "discard_candidate"}:
        with _conn() as conn:
            candidate = _active_hud_candidate(conn, status="candidate")
            if not candidate:
                with _glass_lock:
                    _glass_state.update(
                        {
                            "mode": "error",
                            "message": "승인할 후보 없음",
                            "readiness": "error",
                            "error_state": "NO_ACTIVE_HUD_CANDIDATE",
                            "updated_at": datetime.utcnow().isoformat() + "Z",
                        }
                    )
                _audit_log(None, "warning", f"HUD candidate command blocked no candidate command={command} source={source}")
                return {
                    "ok": False,
                    "executed": False,
                    "command": command,
                    "error_code": "NO_ACTIVE_HUD_CANDIDATE",
                    "message": "active HUD candidate is required",
                }
            if command == "approve_candidate":
                reviewer = str(candidate.get("provider_person_id") or "").strip()
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
                    (reviewer, candidate["id"]),
                )
            else:
                reviewer = str(candidate.get("provider_person_id") or "").strip()
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
                    (reviewer, f"discarded via {source} command", candidate["id"]),
                )
            conn.commit()
            updated = _get_hud_candidate(conn, candidate["id"])
            plan = _hud_candidate_plan(updated)
            next_candidate = _latest_hud_candidate_for_encounter(conn, updated.get("encounter_id") or "", status="candidate")
        _set_active_hud_candidate(next_candidate)
        message = "후보 승인됨" if command == "approve_candidate" else "후보 폐기됨"
        with _glass_lock:
            _glass_state.update(
                {
                    "mode": "candidate_approval" if next_candidate else "ready",
                    "message": message,
                    "readiness": "ready",
                    "error_state": None,
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                }
            )
        _audit_log(None, "info", f"HUD candidate command executed command={command} source={source} id={updated['id']}")
        return {
            "ok": True,
            "executed": True,
            "command": command,
            "source": source,
            "candidate": updated,
            "next_candidate": next_candidate,
            "moai_write_plan": plan,
            "glass_state": _glass_state_snapshot(),
        }

    session_id = _active_visit_session_id_from_hud()
    if command == "start_visit":
        try:
            with _conn() as conn:
                pre_review = None
                plan = None
                sync_job = None
                if session_id:
                    existing = get_visit_session(conn, session_id)
                    if existing and existing.get("status") == "active":
                        candidate = None
                        if existing.get("phase") == "summary":
                            session = end_visit_session(conn, session_id)
                            session = _refresh_visit_progress_note_from_events(conn, session)
                            plan = _build_visit_session_write_plan(session)
                            sync_job = _enqueue_visit_session_sync_job(conn, session, plan)
                        else:
                            session = existing
                    else:
                        session_id = None
                if not session_id:
                    candidate = _get_glass_visit_candidate(conn, scope=scope)
                    if not candidate:
                        raise LookupError("NO_GLASS_VISIT_CANDIDATE")
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
        except LookupError:
            with _glass_lock:
                _glass_state.update(
                    {
                        "mode": "error",
                        "message": "시작할 방문 후보 없음",
                        "readiness": "error",
                        "error_state": "NO_GLASS_VISIT_CANDIDATE",
                        "updated_at": datetime.utcnow().isoformat() + "Z",
                    }
                )
            _audit_log(None, "warning", f"HUD start blocked no visit candidate source={source}")
            return {
                "ok": False,
                "executed": False,
                "command": command,
                "error_code": "NO_GLASS_VISIT_CANDIDATE",
                "message": "No visit candidate with canonical identity is available.",
            }
        hud = _apply_visit_sync_pending_hud(session, sync_job) if sync_job else _apply_visit_session_hud(session, insight=pre_review)
        _audit_log(None, "info", f"HUD command executed command={command} source={source} id={session['id']}")
        result = {
            "ok": True,
            "executed": True,
            "command": command,
            "source": source,
            "session": session,
            "glass_state": hud,
        }
        if metadata:
            result["metadata"] = metadata
        if candidate:
            result["candidate"] = candidate
        if plan:
            result["moai_write_plan"] = plan
        if sync_job:
            result["moai_sync_job"] = sync_job
        return result

    if not session_id:
        if command == "toggle_recording":
            return None
        with _glass_lock:
            _glass_state.update(
                {
                    "mode": "error",
                    "message": "활성 방문 세션 없음",
                    "readiness": "error",
                    "error_state": "NO_ACTIVE_VISIT_SESSION",
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                }
            )
        _audit_log(None, "warning", f"HUD command blocked no active visit session command={command} source={source}")
        return {
            "ok": False,
            "executed": False,
            "command": command,
            "error_code": "NO_ACTIVE_VISIT_SESSION",
            "message": "active visit session is required",
        }

    try:
        with _conn() as conn:
            existing = get_visit_session(conn, session_id)
            if not existing:
                raise KeyError(session_id)
            sync_job = None
            if command == "toggle_recording":
                session = set_visit_recording(
                    conn,
                    session_id,
                    is_recording=existing.get("recording_status") != "recording",
                )
                plan = None
            elif command == "next_phase":
                next_phase = _next_visit_phase(str(existing.get("phase") or "pre_review"))
                session = update_visit_phase(
                    conn,
                    session_id,
                    next_phase,
                )
                _set_hud_capture_role(_role_for_visit_phase(next_phase))
                plan = None
            elif command == "next_role":
                next_role = _next_visit_event_role(_active_capture_role_from_hud(str(existing.get("phase") or "")))
                _set_hud_capture_role(next_role)
                session = update_visit_phase(
                    conn,
                    session_id,
                    str(existing.get("phase") or "pre_review"),
                    f"기록 모드 {next_role}",
                )
                plan = None
            else:
                if existing.get("phase") == "summary":
                    session = end_visit_session(conn, session_id)
                    session = _refresh_visit_progress_note_from_events(conn, session)
                    plan = _build_visit_session_write_plan(session)
                    sync_job = _enqueue_visit_session_sync_job(conn, session, plan)
                else:
                    session = update_visit_phase(conn, session_id, "summary", _build_visit_end_checkpoint_cue(existing))
                    plan = None
            conn.commit()
    except KeyError:
        with _glass_lock:
            _glass_state.update(
                {
                    "mode": "error",
                    "message": "방문 세션을 찾을 수 없음",
                    "readiness": "error",
                    "error_state": "VISIT_SESSION_NOT_FOUND",
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                }
            )
        _audit_log(None, "warning", f"HUD command blocked missing visit session id={session_id} command={command}")
        return {
            "ok": False,
            "executed": False,
            "command": command,
            "error_code": "VISIT_SESSION_NOT_FOUND",
            "message": "visit session not found",
        }

    hud = _apply_visit_sync_pending_hud(session, sync_job) if sync_job else _apply_visit_session_hud(session)
    _audit_log(None, "info", f"HUD command executed command={command} source={source} id={session_id}")
    result = {
        "ok": True,
        "executed": True,
        "command": command,
        "source": source,
        "session": session,
        "glass_state": hud,
    }
    if metadata:
        result["metadata"] = metadata
    if plan:
        result["moai_write_plan"] = plan
    if sync_job:
        result["moai_sync_job"] = sync_job
    return result


def _queue_glass_command(
    command: str,
    source: str = "glass",
    metadata: Optional[dict] = None,
    scope: Optional[dict] = None,
    delivery: str = "web",
) -> dict:
    global _glass_pending_command, _glass_pending_device_command
    if command not in GLASS_COMMANDS:
        allowed = ", ".join(sorted(GLASS_COMMANDS))
        _error(400, "INVALID_COMMAND", f"command must be one of: {allowed}")

    # Camera/microphone actions are executed by the paired iPhone, never by
    # the Web App. Keep them off the shared Web App queue so two consumers do
    # not race to consume the same command.
    if command in {"capture_photo", "start_audio", "stop_audio"}:
        queued = {
            "command": command,
            "id": str(uuid.uuid4()),
            "created_at": datetime.utcnow().isoformat() + "Z",
            "source": source,
        }
        if metadata:
            queued["metadata"] = metadata
        with _glass_lock:
            _glass_pending_device_command.append(queued)
        return queued

    executed = _execute_visit_hud_command(command, source=source, metadata=metadata, scope=scope)
    if executed is not None:
        # The server owns the visit-state transition, while the iPhone owns
        # the physical recorder. Queue an explicit idempotent action for the
        # native app instead of asking it to toggle a second time.
        if command == "toggle_recording":
            session = executed.get("session") or {}
            device_command = (
                "start_recording"
                if session.get("recording_status") == "recording"
                else "stop_recording"
            )
            device_queued = {
                "command": device_command,
                "id": str(uuid.uuid4()),
                "created_at": datetime.utcnow().isoformat() + "Z",
                "source": source,
            }
            if metadata:
                device_queued["metadata"] = metadata
            with _glass_lock:
                _glass_pending_device_command.append(device_queued)
        return {
            "command": command,
            "id": str(uuid.uuid4()),
            "created_at": datetime.utcnow().isoformat() + "Z",
            "source": source,
            "executed": executed,
        }

    queued = {
        "command": command,
        "id": str(uuid.uuid4()),
        "created_at": datetime.utcnow().isoformat() + "Z",
        "source": source,
    }
    if metadata:
        queued["metadata"] = metadata

    with _glass_lock:
        if delivery == "device" and command in GLASS_DEVICE_COMMANDS:
            _glass_pending_device_command.append(queued)
        else:
            _glass_pending_command.append(queued)
    cue = _generate_command_cue_if_needed(command, source)
    if cue:
        queued["cue_id"] = cue["id"]
    return queued


__all__ = [
    "os",
    "ipaddress",
    "re",
    "sqlite3",
    "uuid",
    "logging",
    "concurrent",
    "threading",
    "base64",
    "hashlib",
    "hmac",
    "requests",
    "datetime",
    "timedelta",
    "timezone",
    "lru_cache",
    "Path",
    "Optional",
    "Union",
    "urlencode",
    "json",
    "load_dotenv",
    "BackgroundTasks",
    "FastAPI",
    "File",
    "Form",
    "HTTPException",
    "Request",
    "UploadFile",
    "FileResponse",
    "HTMLResponse",
    "JSONResponse",
    "RedirectResponse",
    "StaticFiles",
    "BaseModel",
    "Field",
    "generate_chart",
    "_mask_faces",
    "save_chart",
    "build_hud_moai_bundle_from_candidate",
    "resolve_moai_identity",
    "build_moai_export_bundle",
    "build_moai_write_plan",
    "execute_moai_write_plan",
    "load_moai_writer_config",
    "RawMediaStage",
    "delete_raw_media",
    "list_raw_media_artifacts",
    "resolve_raw_media",
    "stage_raw_media",
    "POSE_EXTRACTOR_VERSION",
    "analyze_pose_frames",
    "TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION",
    "capture_action_type",
    "extract_transcript_capture_candidates",
    "PROVIDER_ROLES",
    "attach_visit_event",
    "create_visit_session",
    "end_visit_session",
    "ensure_visit_session_schema",
    "get_visit_session",
    "set_visit_recording",
    "update_visit_phase",
    "visit_hud_state",
    "ROOT",
    "DB_PATH",
    "UPLOAD_DIR",
    "CHART_DIR",
    "MASKED_DIR",
    "RAW_MEDIA_DIR",
    "_env_bool",
    "BRIDGE_API_KEY",
    "HUD_SCOPE_SECRET",
    "REQUIRE_API_KEY",
    "ALLOW_INSECURE_LAN",
    "ALLOW_DOCS_WITHOUT_AUTH",
    "ENABLE_FILE_DOWNLOADS",
    "ALLOW_UNMASKED_IMAGE",
    "REQUIRE_PATIENT_CONSENT",
    "AUDIO_STORE",
    "VIDEO_STORE",
    "PILOT_CAPTURE_MODE",
    "PUBLIC_PATHS",
    "PUBLIC_PATH_PREFIXES",
    "HUD_TOKEN_AUTH_PATH_PREFIXES",
    "HUD_TOKEN_ISSUE_PATH",
    "HUD_TEST_HEADER",
    "HUD_TEST_SCOPE",
    "HUD_TEST_ALLOWED_REQUESTS",
    "DOC_PATHS",
    "_is_hud_test_request",
    "ASYNC_RESULTS",
    "ASYNC_RESULT_TTL_MINUTES",
    "ASYNC_RESULT_MAX_ITEMS",
    "UPLOAD_MAX_MB",
    "PROCESS_TIMEOUT_SECONDS",
    "logger",
    "EXECUTOR",
    "CONSENT_MEDIA_LOCK",
    "_client_host",
    "_is_loopback_host",
    "IngestPayload",
    "RehabLabelPayload",
    "LABEL_TAXONOMY_V0",
    "MergeEventsPayload",
    "ChartUpdatePayload",
    "ChartReviewPayload",
    "HudCandidatePayload",
    "HudCandidateExtractPayload",
    "HudCandidateDecisionPayload",
    "HudTokenIssuePayload",
    "CaptureEventPayload",
    "CaptureEventUpdatePayload",
    "CaptureEventExtractPayload",
    "_error",
    "_clean_scope_value",
    "_scope_from_request",
    "_validate_upload_size",
    "_touch_async_result",
    "_normalize_error",
    "_audit_log",
    "_run_with_timeout",
    "_prune_async_results",
    "_ensure_runtime_schema",
    "_conn",
    "CAPTURE_EVENT_STATUSES",
    "CAPTURE_EVENT_SOURCE_TYPES",
    "CAPTURE_ORIGIN_ALIASES",
    "_capture_origin_from_source",
    "_backfill_capture_origin",
    "_capture_event_from_row",
    "_capture_event_select",
    "_create_transcript_capture_events",
    "_create_pose_capture_events",
    "DEFAULT_CONSENT_TEXT",
    "_latest_patient_consent",
    "_require_patient_consent",
    "_stage_raw_media_if_consent_active",
    "_pilot_metadata_gaps",
    "_require_pilot_capture_metadata",
    "_json_list",
    "_optional_bool_from_db",
    "_get_label_by_event_id",
    "_get_chart_review_by_event_id",
    "_get_latest_soap_by_event_id",
    "_read_chart_export",
    "_list_event_artifacts",
    "_get_event_snapshot",
    "_build_moai_bundle_for_event",
    "_hud_candidate_from_row",
    "_get_hud_candidate",
    "_hud_candidate_plan",
    "_extract_side_from_transcript",
    "_extract_test_from_transcript",
    "_extract_value_from_transcript",
    "_extract_symptom_from_transcript",
    "_extract_hud_candidate_from_transcript",
    "_hud_candidate_micro_card",
    "_latest_hud_candidate_for_encounter",
    "_active_hud_candidate",
    "_set_active_hud_candidate",
    "_safe_json_loads",
    "_summarize_moai_plan_for_job",
    "_summarize_moai_write_result_for_job",
    "_moai_sync_job_from_row",
    "_enqueue_moai_sync_job",
    "_record_moai_sync_job_attempt",
    "_list_moai_sync_jobs",
    "_event_consent_status",
    "_build_pilot_manifest_for_event",
    "_pilot_readiness_from_manifest",
    "_first_non_empty",
    "_build_physio_session_export_item",
    "redact_phi",
    "classify_intent",
    "_extract_measurements",
    "_extract_risk_flags",
    "_build_plan",
    "_normalize_clinical_terms",
    "_is_operational_image_note",
    "_strip_operational_image_notes",
    "CHART_SECTION_KEYS",
    "_parse_chart_sections",
    "_chart_quality",
    "_clip_chart_text",
    "_extract_marker_value",
    "_extract_pose_summary",
    "_extract_voice_memo",
    "_extract_video_transcript_capture_text",
    "_looks_nonclinical_image",
    "_image_chart_inputs",
    "build_soap",
    "_extract_image_note_line",
    "_extract_patient_from_text",
    "_get_event_for_merge",
    "_resolve_merged_scope",
    "_resolve_merged_physio_context",
    "_resolve_merged_subject_person_id",
    "_create_merged_event",
    "_get_whisper_model",
    "stt_whisper_local",
    "_process_event",
    "_event_status_result",
    "_process_upload_job",
    "_delete_event_artifacts",
    "_delete_raw_event_artifacts",
    "_process_image_job",
    "_process_video_job",
    "_authorize_raw_media_request",
    "_label_performance_value",
    "_threading",
    "_glass_lock",
    "_glass_state",
    "_hud_test_state",
    "_glass_pending_command",
    "_glass_pending_device_command",
    "GlassStateUpdate",
    "GlassCommandRequest",
    "NeuralBandEventRequest",
    "AgentCueDryRunRequest",
    "VisitSessionStartRequest",
    "VisitSessionPhaseRequest",
    "VisitSessionRecordingRequest",
    "VisitSessionEventRequest",
    "GlassVisitStartRequest",
    "_apply_visit_session_hud",
    "_apply_hud_test_visit_state",
    "_build_visit_session_write_plan",
    "_visit_sync_marker_event_id",
    "_enqueue_visit_session_sync_job",
    "_apply_visit_sync_pending_hud",
    "_trim_event_text",
    "_visit_linked_events",
    "_capture_event_note_text",
    "_linked_event_bucket",
    "_build_linked_event_progress_note",
    "_refresh_visit_progress_note_from_events",
    "VISIT_EVENT_ROLE_ORDER",
    "_role_for_visit_phase",
    "_next_visit_event_role",
    "_set_hud_capture_role",
    "_active_capture_role_from_hud",
    "_auto_attach_event_to_active_visit",
    "GLASS_COMMANDS",
    "GLASS_DEVICE_COMMANDS",
    "NEURAL_BAND_GESTURE_MAP",
    "AGENT_ALLOWED_TOOLS",
    "AGENT_BLOCKED_ACTIONS",
    "_short_lens_text",
    "_build_dry_run_session_cue",
    "_existing_event_id_for_audit",
    "_lens_safe_patient_alias",
    "_visit_candidate_from_event_row",
    "_glass_remote_scope",
    "_moai_fetch_rows",
    "_b64url_encode",
    "_b64url_decode",
    "_hud_scope_signing_secret",
    "build_hud_scope_token",
    "_decode_hud_scope_token",
    "_hud_scope_from_request",
    "_candidate_matches_hud_scope",
    "_today_visit_window_utc",
    "_parse_moai_datetime",
    "_looks_like_uuid",
    "_visit_candidate_from_encounter_row",
    "_list_moai_glass_visit_candidates",
    "_list_glass_visit_candidates",
    "_get_glass_visit_candidate",
    "_visit_candidate_history_summary",
    "_build_subject_record_preview",
    "_record_preview_detail_lines",
    "_build_local_candidate_record_preview",
    "_attach_record_preview_to_candidate",
    "_safe_moai_fetch_rows",
    "_subject_history_params",
    "_build_visit_pre_review",
    "_attach_pre_review_to_session",
    "_build_visit_end_checkpoint_cue",
    "_generate_command_cue_if_needed",
    "VISIT_PHASE_ORDER",
    "SERVER_EXECUTED_GLASS_COMMANDS",
    "_active_visit_session_id_from_hud",
    "_glass_state_snapshot",
    "_next_visit_phase",
    "_execute_visit_hud_command",
    "_queue_glass_command",
]
