import os
import ipaddress
import re
import sqlite3
import uuid
import logging
import concurrent.futures
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional
import json

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

load_dotenv()

# ── auto-chart 통합 ──────────────────────────────────────────────────────────
from lib.auto_chart import generate_chart, mask_faces as _mask_faces, save_chart

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "storage" / "bridge.db"
UPLOAD_DIR = ROOT / "storage" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CHART_DIR = ROOT / "storage" / "charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)
MASKED_DIR = ROOT / "storage" / "masked"
MASKED_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="rayban-local-bridge", version="0.4.0")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "").strip()
REQUIRE_API_KEY = _env_bool("REQUIRE_API_KEY", True)
ALLOW_INSECURE_LAN = _env_bool("ALLOW_INSECURE_LAN", False)
ALLOW_DOCS_WITHOUT_AUTH = _env_bool("ALLOW_DOCS_WITHOUT_AUTH", False)
ENABLE_FILE_DOWNLOADS = _env_bool("ENABLE_FILE_DOWNLOADS", False)
ALLOW_UNMASKED_IMAGE = _env_bool("ALLOW_UNMASKED_IMAGE", False)
REQUIRE_PATIENT_CONSENT = _env_bool("REQUIRE_PATIENT_CONSENT", False)
VIDEO_STORE = _env_bool("VIDEO_STORE", False)

PUBLIC_PATHS = {"/", "/health"}
DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}

ASYNC_RESULTS: dict[str, dict] = {}
ASYNC_RESULT_TTL_MINUTES = int(os.getenv("ASYNC_RESULT_TTL_MINUTES", "60"))
ASYNC_RESULT_MAX_ITEMS = int(os.getenv("ASYNC_RESULT_MAX_ITEMS", "1000"))
UPLOAD_MAX_MB = int(os.getenv("UPLOAD_MAX_MB", "25"))
PROCESS_TIMEOUT_SECONDS = int(os.getenv("PROCESS_TIMEOUT_SECONDS", "180"))

logger = logging.getLogger("rayban-local-bridge")
if not logger.handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _client_host(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    path = request.url.path

    if path in PUBLIC_PATHS or (ALLOW_DOCS_WITHOUT_AUTH and path in DOC_PATHS):
        return await call_next(request)

    incoming_key = request.headers.get("x-api-key", "") or request.query_params.get("api_key", "")
    if BRIDGE_API_KEY:
        if incoming_key != BRIDGE_API_KEY:
            return JSONResponse(
                status_code=401,
                content={
                    "code": "UNAUTHORIZED",
                    "message": "유효한 x-api-key 헤더가 필요합니다.",
                },
            )
        return await call_next(request)

    if not REQUIRE_API_KEY or ALLOW_INSECURE_LAN or _is_loopback_host(_client_host(request)):
        return await call_next(request)

    if path in DOC_PATHS:
        message = "LAN에서 API 문서를 보려면 BRIDGE_API_KEY를 설정하거나 ALLOW_DOCS_WITHOUT_AUTH=true를 명시하세요."
    else:
        message = "LAN 요청에는 BRIDGE_API_KEY 설정이 필요합니다. server/run_lan_bridge.sh를 다시 실행해 생성된 키를 앱에 입력하세요."
    return JSONResponse(
        status_code=503,
        content={
            "code": "BRIDGE_API_KEY_REQUIRED",
            "message": message,
        },
    )

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


class RehabLabelPayload(BaseModel):
    session_type: str
    core_task: str
    assist_level: str
    performance: str
    flags: list[str] = []
    notes: str = ""


class ConsentPayload(BaseModel):
    patient_name: str
    scope: str = "capture_analysis_storage"
    consent_text: Optional[str] = None
    granted_by: Optional[str] = None


class MergeEventsPayload(BaseModel):
    image_event_id: str
    audio_event_id: str
    patient_name: Optional[str] = None


class ChartUpdatePayload(BaseModel):
    chart: str


class ChartReviewPayload(BaseModel):
    reviewer: Optional[str] = "therapist"
    notes: Optional[str] = ""


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
    org_id = (
        _clean_scope_value(owner_org_id)
        or _clean_scope_value(request.headers.get("x-glasspt-org-id"))
        or _clean_scope_value(request.headers.get("x-org-id"))
    )
    provider_person_id = (
        _clean_scope_value(owner_provider_person_id)
        or _clean_scope_value(request.headers.get("x-glasspt-provider-person-id"))
        or _clean_scope_value(request.headers.get("x-provider-person-id"))
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
        """
    )
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "owner_org_id" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN owner_org_id TEXT")
    if "owner_provider_person_id" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN owner_provider_person_id TEXT")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_events_owner_org_created_at ON events(owner_org_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_owner_provider_created_at ON events(owner_provider_person_id, created_at);
        """
    )


def _conn():
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail="DB not initialized. Run: python init_db.py")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_runtime_schema(conn)
    return conn


DEFAULT_CONSENT_TEXT = (
    "환자 또는 보호자가 치료 기록을 위해 사진/영상/음성/텍스트를 캡처하고, "
    "로컬 서버에서 분석 및 차트 생성을 수행하며, 필요한 기간 동안 저장하는 것에 동의했습니다."
)


def _latest_patient_consent(conn: sqlite3.Connection, patient_name: str, scope: str = "capture_analysis_storage"):
    return conn.execute(
        """
        SELECT id, patient_name, scope, consent_text, granted_by, created_at
        FROM patient_consents
        WHERE patient_name = ? AND scope = ? AND revoked_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (patient_name.strip(), scope.strip() or "capture_analysis_storage"),
    ).fetchone()


def _require_patient_consent(event_type: str, patient_name: str) -> Optional[str]:
    if not REQUIRE_PATIENT_CONSENT or event_type not in {"audio", "image", "video", "text"}:
        return None

    name = (patient_name or "").strip()
    if not name:
        _error(428, "PATIENT_CONSENT_REQUIRED", "환자 동의 확인을 위해 patient_name이 필요합니다.")

    with _conn() as conn:
        row = _latest_patient_consent(conn, name)
    if not row:
        _error(428, "PATIENT_CONSENT_REQUIRED", f"{name} 환자의 활성 동의 기록이 필요합니다.")
    return row[0]


def _get_label_by_event_id(conn: sqlite3.Connection, event_id: str):
    row = conn.execute(
        "SELECT event_id, session_type, core_task, assist_level, performance, flags, notes, updated_at FROM rehab_labels WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    flags = []
    try:
        flags = json.loads(row[5] or "[]")
        if not isinstance(flags, list):
            flags = []
    except Exception:
        flags = []
    return {
        "event_id": row[0],
        "session_type": row[1],
        "core_task": row[2],
        "assist_level": row[3],
        "performance": row[4],
        "flags": flags,
        "notes": row[6] or "",
        "updated_at": row[7],
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
    return artifacts


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
        "title": title,
        "summary": summary,
        "label": label,
        "soap": soap,
        "chart_excerpt": _clip_chart_text(chart_text, 900) if chart_text else "",
        "quality": quality,
        "review": review,
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
        "SELECT id, source, event_type, raw_text, patient_name, created_at, owner_org_id, owner_provider_person_id "
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
    consent_id = _require_patient_consent("text", patient or "")

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
            "INSERT INTO events (id, source, event_type, raw_text, intent, status, patient_name, owner_org_id, owner_provider_person_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, "merged", "combined", raw_text, "note", "processed", patient, owner_org_id, owner_provider_person_id),
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
):
    audio_store = os.getenv("AUDIO_STORE", "false").lower() == "true"
    image_store = os.getenv("IMAGE_STORE", "false").lower() == "true"
    phi_redact = os.getenv("PHI_REDACT", "true").lower() == "true"
    soap_enabled = os.getenv("SOAP_ENABLED", "true").lower() == "true"
    masking_meta = None
    masking_audit = ""
    owner_org_id = _clean_scope_value(owner_org_id)
    owner_provider_person_id = _clean_scope_value(owner_provider_person_id)

    if event_type not in {"audio", "text", "command", "image", "video"}:
        raise HTTPException(status_code=400, detail="event_type must be audio/text/command/image/video")

    event_id = str(uuid.uuid4())
    consent_id = _require_patient_consent(event_type, patient_name)

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
    should_make_soap = intent == "note" or event_type in {"audio", "image", "video"}
    if soap_enabled and should_make_soap:
        _img_notes = image_notes if event_type == "image" else ""
        s, o, a, p = build_soap(parsed_text, event_id=event_id,
                                 event_type=event_type, image_notes=_img_notes)
        soap_id = str(uuid.uuid4())
        soap = {"s": s, "o": o, "a": a, "p": p}

    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (id, source, event_type, raw_text, intent, status, patient_name, owner_org_id, owner_provider_person_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        },
    }


def _event_status_result(processed: dict) -> dict:
    """Normalize _process_event output to the /events/{id} response shape used by iOS."""
    event_id = processed.get("event_id", "")
    event_obj = None
    if event_id:
        with _conn() as conn:
            row = conn.execute(
                "SELECT id, source, event_type, raw_text, intent, status, created_at, owner_org_id, owner_provider_person_id "
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
            }
    result = {"event": event_obj, "soap": processed.get("soap")}
    if processed.get("media"):
        result["media"] = processed["media"]
    return result


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/legacy", response_class=HTMLResponse)
def legacy_index():
    return """
<!doctype html>
<html lang='ko'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Rayban Local Bridge UI</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 20px; line-height: 1.4; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 14px; margin-bottom: 14px; }
    textarea, input[type=text] { width: 100%; padding: 10px; margin: 6px 0; box-sizing: border-box; }
    button { padding: 10px 14px; border: 0; border-radius: 10px; background: #111; color: #fff; }
    pre { background: #f7f7f7; padding: 10px; border-radius: 10px; overflow: auto; }
    .muted { color: #666; font-size: 13px; }
  </style>
</head>
<body>
  <h2>Rayban Local Bridge</h2>
  <div class='muted'>텍스트 전송(/ingest) 및 오디오 업로드(/ingest-upload) 테스트 UI</div>

  <div class='card'>
    <h3>API Key (x-api-key)</h3>
    <input id='apiKey' type='text' placeholder='BRIDGE_API_KEY 입력' />
    <div class='muted'>인증이 켜진 경우 필수입니다. 브라우저 localStorage에만 저장됩니다.</div>
  </div>

  <div class='card'>
    <h3>Ray-Ban 페어링 상태 (운영 체크)</h3>
    <div class='muted'>실제 페어링 제어는 Meta View 앱에서 수행하고, 여기서는 상태 체크 후 업로드로 진행합니다.</div>
    <label><input id='pairingReady' type='checkbox' /> Ray-Ban 연결/촬영 준비 완료</label>
    <div style='margin-top:8px;'>
      <button onclick='openMetaGuide()'>Meta View 열기 안내</button>
      <button onclick='showWorkflow()'>워크플로우 보기</button>
    </div>
    <pre id='pairingOut' class='muted' style='margin-top:10px;'>체크 후 업로드를 진행하세요.</pre>
  </div>

  <div class='card'>
    <h3>텍스트 전송</h3>
    <input id='source' type='text' value='iphone' />
    <textarea id='text' rows='4'>환아 김민수 MRN:12345678 보행 불안정, 통증 6점</textarea>
    <button onclick='sendText()'>POST /ingest</button>
  </div>

  <div class='card'>
    <h3>오디오 업로드 (JS)</h3>
    <input id='audioSource' type='text' value='iphone' />
    <input id='audioFile' type='file' />
    <button onclick='uploadAudio()'>POST /ingest-upload</button>
    <div class='muted'>iPhone Safari에서 Load failed가 나면 아래 "폼 업로드"를 사용하세요. (단, API Key 보호가 켜져 있으면 폼 업로드는 인증 헤더를 보낼 수 없습니다)</div>
  </div>

  <div class='card'>
    <h3>오디오 업로드 (폼 업로드: Safari 안정 모드)</h3>
    <form action='/ingest-upload' method='post' enctype='multipart/form-data' target='_blank'>
      <input type='hidden' name='event_type' value='audio' />
      <label>source</label>
      <input type='text' name='source' value='iphone' />
      <label>audio file</label>
      <input type='file' name='audio' />
      <button type='submit'>폼으로 업로드</button>
    </form>
  </div>

  <div class='card'>
    <h3>결과 조회</h3>
    <input id='eventId' type='text' placeholder='event_id 입력 (예: ed76837d-...)' />
    <button onclick='checkEvent()'>GET /events/{id}</button>
    <button onclick='listRecent()'>GET /recent-events</button>
  </div>

  <div class='card'>
    <h3>라벨링 (MVP)</h3>
    <input id='labelEventId' type='text' placeholder='라벨링할 event_id' />
    <input id='sessionType' type='text' value='기립훈련' placeholder='session_type' />
    <input id='coreTask' type='text' value='경부 회전+중립 유지' placeholder='core_task' />
    <input id='assistLevel' type='text' value='mod' placeholder='assist_level (max/mod/min/CGA/ind)' />
    <input id='performance' type='text' value='보통' placeholder='performance (좋음/보통/저하)' />
    <input id='flags' type='text' value='피로,자세흔들림' placeholder='flags (쉼표로 구분)' />
    <textarea id='labelNotes' rows='2' placeholder='notes'>후반부 집중도 저하</textarea>
    <button onclick='saveLabel()'>POST /labels/{id}</button>
    <button onclick='getLabel()'>GET /labels/{id}</button>
  </div>

  <div class='card'>
    <h3>응답</h3>
    <pre id='out'>여기에 결과가 표시됩니다.</pre>
  </div>

<script>
const apiKeyEl = document.getElementById('apiKey');
apiKeyEl.value = localStorage.getItem('bridge_api_key') || '';
apiKeyEl.addEventListener('input', () => {
  localStorage.setItem('bridge_api_key', apiKeyEl.value || '');
});

const pairingReadyEl = document.getElementById('pairingReady');
const pairingOutEl = document.getElementById('pairingOut');
pairingReadyEl.checked = (localStorage.getItem('rayban_pairing_ready') || '') === '1';
pairingReadyEl.addEventListener('change', () => {
  localStorage.setItem('rayban_pairing_ready', pairingReadyEl.checked ? '1' : '0');
  pairingOutEl.textContent = pairingReadyEl.checked
    ? '연결 준비 완료: 이제 오디오/영상 업로드 → 라벨링 → SOAP 확인 순서로 진행하세요.'
    : '연결 미확인: Meta View 앱에서 안경 연결 상태를 먼저 확인하세요.';
});
pairingOutEl.textContent = pairingReadyEl.checked
  ? '연결 준비 완료: 이제 오디오/영상 업로드 → 라벨링 → SOAP 확인 순서로 진행하세요.'
  : '연결 미확인: Meta View 앱에서 안경 연결 상태를 먼저 확인하세요.';

function openMetaGuide() {
  pairingOutEl.textContent = 'iPhone에서 Meta View 앱 실행 → Ray-Ban 선택 → 연결 상태 확인 후 돌아와 체크하세요.';
}

function showWorkflow() {
  pairingOutEl.textContent = '권장 순서: 1) 페어링 확인 2) 촬영/파일준비 3) 업로드 4) event 조회 5) 라벨 저장 6) 차트 확인';
}

function authHeaders(isJson = false) {
  const h = {};
  const k = (apiKeyEl.value || '').trim();
  if (isJson) h['Content-Type'] = 'application/json';
  if (k) h['x-api-key'] = k;
  return h;
}

async function sendText() {
  const out = document.getElementById('out');
  try {
    out.textContent = '전송 중...';
    const payload = {
      source: document.getElementById('source').value || 'iphone',
      event_type: 'text',
      text: document.getElementById('text').value || ''
    };
    const res = await fetch(window.location.origin + '/ingest', {
      method: 'POST',
      headers: authHeaders(true),
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    out.textContent = JSON.stringify({ status: res.status, data }, null, 2);
  } catch (e) {
    out.textContent = '오류: ' + String(e);
  }
}

async function uploadAudio() {
  const out = document.getElementById('out');
  try {
    const f = document.getElementById('audioFile').files[0];
    if (!f) {
      alert('오디오 파일을 먼저 선택하세요.');
      return;
    }
    out.textContent = '업로드 중...';
    const fd = new FormData();
    fd.append('source', document.getElementById('audioSource').value || 'iphone');
    fd.append('event_type', 'audio');
    fd.append('audio', f);

    const res = await fetch(window.location.origin + '/ingest-upload', {
      method: 'POST',
      headers: authHeaders(false),
      body: fd
    });
    const data = await res.json();

    if (!data.event_id) {
      out.textContent = JSON.stringify({ status: res.status, data }, null, 2);
      return;
    }

    document.getElementById('eventId').value = data.event_id;
    out.textContent = JSON.stringify({ status: res.status, data, poll: 'processing...' }, null, 2);

    for (let i = 0; i < 20; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const p = await fetch(window.location.origin + '/events/' + data.event_id, {
        headers: authHeaders(false)
      });
      const d = await p.json();
      out.textContent = JSON.stringify({ upload: data, event: d }, null, 2);
      if (d.status === 'done' || d.status === 'error') return;
    }
  } catch (e) {
    out.textContent = '오류: ' + String(e);
  }
}

async function checkEvent() {
  const out = document.getElementById('out');
  const id = (document.getElementById('eventId').value || '').trim();
  if (!id) {
    out.textContent = 'event_id를 입력하세요.';
    return;
  }
  const res = await fetch(window.location.origin + '/events/' + id, {
    headers: authHeaders(false)
  });
  const data = await res.json();
  const labelIdEl = document.getElementById('labelEventId');
  if (labelIdEl && !labelIdEl.value) labelIdEl.value = id;
  out.textContent = JSON.stringify({ status: res.status, data }, null, 2);
}

async function listRecent() {
  const out = document.getElementById('out');
  const res = await fetch(window.location.origin + '/recent-events', {
    headers: authHeaders(false)
  });
  const data = await res.json();
  out.textContent = JSON.stringify({ status: res.status, data }, null, 2);
}

async function saveLabel() {
  const out = document.getElementById('out');
  const id = (document.getElementById('labelEventId').value || document.getElementById('eventId').value || '').trim();
  if (!id) {
    out.textContent = 'label용 event_id를 입력하세요.';
    return;
  }
  const flagsRaw = (document.getElementById('flags').value || '').trim();
  const payload = {
    session_type: document.getElementById('sessionType').value || '',
    core_task: document.getElementById('coreTask').value || '',
    assist_level: document.getElementById('assistLevel').value || '',
    performance: document.getElementById('performance').value || '',
    flags: flagsRaw ? flagsRaw.split(',').map(x => x.trim()).filter(Boolean) : [],
    notes: document.getElementById('labelNotes').value || ''
  };

  const res = await fetch(window.location.origin + '/labels/' + id, {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  out.textContent = JSON.stringify({ status: res.status, data }, null, 2);
}

async function getLabel() {
  const out = document.getElementById('out');
  const id = (document.getElementById('labelEventId').value || document.getElementById('eventId').value || '').trim();
  if (!id) {
    out.textContent = 'label용 event_id를 입력하세요.';
    return;
  }

  const res = await fetch(window.location.origin + '/labels/' + id, {
    headers: authHeaders(false)
  });
  const data = await res.json();
  out.textContent = JSON.stringify({ status: res.status, data }, null, 2);
}
</script>
</body>
</html>
    """


@app.get("/health")
def health():
    _prune_async_results()
    db_ok = True
    db_error = None
    recent_error_logs = 0
    try:
        with _conn() as conn:
            conn.execute("SELECT 1").fetchone()
            r = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE level='error' AND created_at >= datetime('now', '-60 minutes')"
            ).fetchone()
            recent_error_logs = int(r[0] if r else 0)
    except Exception as e:
        db_ok = False
        db_error = str(e)

    return {
        "ok": db_ok,
        "service": "rayban-local-bridge",
        "version": "0.4.0",
        "time": datetime.utcnow().isoformat(),
        "db": {"ok": db_ok, "error": db_error},
        "async_cache": {
            "items": len(ASYNC_RESULTS),
            "ttl_minutes": ASYNC_RESULT_TTL_MINUTES,
            "max_items": ASYNC_RESULT_MAX_ITEMS,
        },
        "processing": {"timeout_seconds": PROCESS_TIMEOUT_SECONDS},
        "security": {
            "api_key_configured": bool(BRIDGE_API_KEY),
            "require_api_key": REQUIRE_API_KEY,
            "allow_insecure_lan": ALLOW_INSECURE_LAN,
            "docs_public_without_auth": ALLOW_DOCS_WITHOUT_AUTH,
            "file_downloads_enabled": ENABLE_FILE_DOWNLOADS,
            "allow_unmasked_image": ALLOW_UNMASKED_IMAGE,
            "patient_consent_required": REQUIRE_PATIENT_CONSENT,
            "video_store": VIDEO_STORE,
        },
        "recent_error_logs_60m": recent_error_logs,
    }


@app.post("/consents")
def record_consent(payload: ConsentPayload):
    patient_name = payload.patient_name.strip()
    scope = payload.scope.strip() or "capture_analysis_storage"
    if not patient_name:
        _error(400, "INVALID_PATIENT_NAME", "patient_name은 비워둘 수 없습니다.")

    consent_id = str(uuid.uuid4())
    consent_text = (payload.consent_text or DEFAULT_CONSENT_TEXT).strip()
    granted_by = (payload.granted_by or "").strip() or None

    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO patient_consents (id, patient_name, scope, consent_text, granted_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (consent_id, patient_name, scope, consent_text, granted_by),
        )
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), None, "info", f"consent recorded patient={patient_name} scope={scope}"),
        )
        conn.commit()

    return {
        "ok": True,
        "consent": {
            "id": consent_id,
            "patient_name": patient_name,
            "scope": scope,
            "granted_by": granted_by,
        },
    }


@app.get("/consents/{patient_name}")
def get_patient_consent(patient_name: str, scope: str = "capture_analysis_storage"):
    name = patient_name.strip()
    if not name:
        _error(400, "INVALID_PATIENT_NAME", "patient_name은 비워둘 수 없습니다.")
    with _conn() as conn:
        row = _latest_patient_consent(conn, name, scope)
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


@app.delete("/consents/{patient_name}")
def revoke_patient_consent(patient_name: str, scope: str = "capture_analysis_storage"):
    name = patient_name.strip()
    if not name:
        _error(400, "INVALID_PATIENT_NAME", "patient_name은 비워둘 수 없습니다.")

    with _conn() as conn:
        cur = conn.execute(
            """
            UPDATE patient_consents
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE patient_name = ? AND scope = ? AND revoked_at IS NULL
            """,
            (name, scope.strip() or "capture_analysis_storage"),
        )
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), None, "info", f"consent revoked patient={name} scope={scope} count={cur.rowcount}"),
        )
        conn.commit()

    return {"ok": True, "patient_name": name, "scope": scope, "revoked": cur.rowcount}


@app.post("/ingest")
def ingest(payload: IngestPayload, request: Request):
    owner_org_id, owner_provider_person_id = _scope_from_request(
        request,
        owner_org_id=payload.owner_org_id or payload.org_id,
        owner_provider_person_id=payload.owner_provider_person_id or payload.provider_person_id,
    )
    return _process_event(
        source=payload.source,
        event_type=payload.event_type,
        text=payload.text,
        audio_path=payload.audio_path,
        image_base64=payload.image_base64,
        patient_name=payload.patient_name or "",
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
    )


@app.post("/events/merge")
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


def _process_upload_job(
    event_id: str,
    source: str,
    saved_path: Path,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
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
            )
            inner_id = result.get("event_id", "")
            if inner_id and inner_id != event_id:
                import shutil as _shutil
                inner_chart = CHART_DIR / f"{inner_id}_11.txt"
                outer_chart = CHART_DIR / f"{event_id}_11.txt"
                if inner_chart.exists() and not outer_chart.exists():
                    _shutil.copy(inner_chart, outer_chart)

            _touch_async_result(event_id, {"status": "done", "result": _event_status_result(result)})
            took_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
            _audit_log(event_id, "info", f"upload processed attempt={i+1} took_ms={took_ms}")
            return
        except Exception as e:
            last_error = e
            code, msg, retryable = _normalize_error(e)
            logger.exception("upload job failed event_id=%s attempt=%s code=%s", event_id, i + 1, code)
            _audit_log(event_id, "error", f"upload failed attempt={i+1} code={code} msg={msg}")
            if i == attempts - 1:
                _touch_async_result(event_id, {
                    "status": "error",
                    "error": msg,
                    "error_code": code,
                    "retryable": retryable,
                })


@app.post("/ingest-upload")
async def ingest_upload(
    background_tasks: BackgroundTasks,
    request: Request,
    source: str = Form("iphone"),
    event_type: str = Form("audio"),
    patient_name: str = Form(""),
    owner_org_id: str = Form(""),
    owner_provider_person_id: str = Form(""),
    audio: UploadFile = File(...),
):
    if event_type != "audio":
        _error(400, "INVALID_EVENT_TYPE", "ingest-upload only supports event_type=audio")

    ext = (Path(audio.filename or "").suffix or "").lower()
    allowed_ext = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm"}
    content_type = (audio.content_type or "").lower()

    is_audio_type = content_type.startswith("audio/")
    is_audio_ext = ext in allowed_ext

    if not (is_audio_type or is_audio_ext):
        _error(400, "INVALID_AUDIO_FILE", f"audio 파일만 업로드 가능합니다. 현재: content_type={content_type or 'unknown'}, ext={ext or 'none'}")

    safe_ext = ext if ext else ".bin"
    saved_path = UPLOAD_DIR / f"{uuid.uuid4()}{safe_ext}"

    content = await audio.read()
    _validate_upload_size(content, "audio")
    saved_path.write_bytes(content)

    event_id = str(uuid.uuid4())
    scoped_org_id, scoped_provider_person_id = _scope_from_request(
        request,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
    )
    _touch_async_result(event_id, {"status": "accepted", "message": "uploaded"})
    background_tasks.add_task(
        _process_upload_job,
        event_id,
        source,
        saved_path,
        patient_name,
        scoped_org_id,
        scoped_provider_person_id,
    )

    return {
        "event_id": event_id,
        "status": "accepted",
        "message": "업로드 접수 완료. /events/{event_id} 로 결과를 조회하세요.",
    }


@app.get("/events/{event_id}")
def get_event(event_id: str):
    _prune_async_results()
    row = ASYNC_RESULTS.get(event_id)
    if row:
        return row

    # fallback: DB에서 처리 완료 이벤트 조회
    with _conn() as conn:
        ev = conn.execute(
            "SELECT id, source, event_type, raw_text, intent, status, created_at, owner_org_id, owner_provider_person_id FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")
        soap = conn.execute(
            "SELECT s, o, a, p, created_at FROM soap_notes WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        label = _get_label_by_event_id(conn, event_id)

    event_obj = {
        "id": ev[0],
        "source": ev[1],
        "event_type": ev[2],
        "raw_text": ev[3],
        "intent": ev[4],
        "status": ev[5],
        "created_at": ev[6],
        "owner_org_id": ev[7],
        "owner_provider_person_id": ev[8],
    }
    soap_obj = None
    if soap:
        soap_obj = {"s": soap[0], "o": soap[1], "a": soap[2], "p": soap[3], "created_at": soap[4]}

    _audit_log(event_id, "info", "event viewed")
    return {"status": "done", "result": {"event": event_obj, "soap": soap_obj, "label": label}}


def _delete_event_artifacts(event_id: str) -> list[str]:
    deleted: list[str] = []
    candidates = [CHART_DIR / f"{event_id}_11.txt"]
    candidates.extend(MASKED_DIR.glob(f"{event_id}*"))
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                path.unlink()
                deleted.append(path.name)
        except Exception as e:
            logger.warning("artifact delete failed event_id=%s path=%s err=%s", event_id, path, e)
    return deleted


@app.delete("/events/{event_id}")
def delete_event(event_id: str):
    deleted_files = _delete_event_artifacts(event_id)
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev and not deleted_files:
            raise HTTPException(status_code=404, detail="event not found")
        if ev:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), None, "info", f"event deleted id={event_id} files={len(deleted_files)}"),
        )
        conn.commit()
    ASYNC_RESULTS.pop(event_id, None)
    return {"ok": True, "event_id": event_id, "deleted_files": deleted_files}


@app.delete("/retention/events")
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




def _process_image_job(
    event_id: str,
    source: str,
    saved_path,
    description: str,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
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


@app.post("/ingest-image")
async def ingest_image(
    background_tasks: BackgroundTasks,
    request: Request,
    source: str = Form("rayban"),
    description: str = Form(""),
    patient_name: str = Form(""),
    owner_org_id: str = Form(""),
    owner_provider_person_id: str = Form(""),
    image: UploadFile = File(...),
):
    ext = (Path(image.filename or "").suffix or "").lower()
    allowed_ext = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
    content_type = (image.content_type or "").lower()

    is_image_type = content_type.startswith("image/")
    is_image_ext = ext in allowed_ext

    if not (is_image_type or is_image_ext):
        _error(400, "INVALID_IMAGE_FILE", f"이미지 파일만 업로드 가능합니다. content_type={content_type or 'unknown'}, ext={ext or 'none'}")

    safe_ext = ext if ext else ".jpg"
    saved_path = UPLOAD_DIR / f"{__import__('uuid').uuid4()}{safe_ext}"

    content = await image.read()
    _validate_upload_size(content, "image")
    saved_path.write_bytes(content)

    event_id = str(__import__('uuid').uuid4())
    scoped_org_id, scoped_provider_person_id = _scope_from_request(
        request,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
    )
    _touch_async_result(event_id, {"status": "accepted", "message": "image uploaded"})
    background_tasks.add_task(
        _process_image_job,
        event_id,
        source,
        saved_path,
        description,
        patient_name,
        scoped_org_id,
        scoped_provider_person_id,
    )

    return {
        "event_id": event_id,
        "status": "accepted",
        "image_saved": saved_path.name,
        "message": "이미지 접수 완료. /events/{event_id} 로 결과를 조회하세요.",
    }



def _process_video_job(
    event_id: str,
    source: str,
    saved_path: Path,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
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

        # ── 5. 통합 텍스트 ──────────────────────────────────────────
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

        # ── 6. SOAP 차트 생성 ────────────────────────────────────────
        result = _process_event(
            source=source,
            event_type="video",
            text=combined,
            patient_name=patient_name,
            owner_org_id=owner_org_id,
            owner_provider_person_id=owner_provider_person_id,
        )
        inner_id = result.get("event_id", "")

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

        # iOS EventStatusResponse 구조에 맞게 래핑
        with _conn() as _c:
            ev_row = _c.execute(
                "SELECT id, source, event_type, raw_text, intent, status, created_at, owner_org_id, owner_provider_person_id "
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
            }

        _touch_async_result(event_id, {
            "status": "done",
            "result": {
                "event": event_obj,
                "soap": result.get("soap"),
            },
        })
        _audit_log(event_id, "info", "video processed")

    except Exception as e:
        code, msg, retryable = _normalize_error(e)
        logger.exception("video job failed event_id=%s code=%s", event_id, code)
        _audit_log(event_id, "error", f"video failed code={code} msg={msg}")
        _touch_async_result(event_id, {
            "status": "error",
            "error": msg,
            "error_code": code,
            "retryable": retryable,
        })
    finally:
        _shutil.rmtree(tmp_dir, ignore_errors=True)
        if not VIDEO_STORE:
            saved_path.unlink(missing_ok=True)


@app.post("/ingest-video")
async def ingest_video(
    background_tasks: BackgroundTasks,
    request: Request,
    source: str = Form("rayban-camera"),
    patient_name: str = Form(""),
    owner_org_id: str = Form(""),
    owner_provider_person_id: str = Form(""),
    video: UploadFile = File(...),
):
    ext = (Path(video.filename or "").suffix or "").lower()
    allowed_ext = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
    content_type = (video.content_type or "").lower()

    is_video_type = content_type.startswith("video/")
    is_video_ext = ext in allowed_ext

    if not (is_video_type or is_video_ext):
        _error(400, "INVALID_VIDEO_FILE", f"영상 파일만 업로드 가능합니다. content_type={content_type or 'unknown'}, ext={ext or 'none'}")

    safe_ext = ext if ext else ".mp4"
    saved_path = UPLOAD_DIR / f"{uuid.uuid4()}{safe_ext}"

    content = await video.read()
    _validate_upload_size(content, "video")
    saved_path.write_bytes(content)

    event_id = str(uuid.uuid4())
    scoped_org_id, scoped_provider_person_id = _scope_from_request(
        request,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
    )
    _touch_async_result(event_id, {"status": "accepted", "message": "video uploaded"})
    background_tasks.add_task(
        _process_video_job,
        event_id,
        source,
        saved_path,
        patient_name,
        scoped_org_id,
        scoped_provider_person_id,
    )

    return {
        "event_id": event_id,
        "status": "accepted",
        "video_saved": saved_path.name,
        "size_kb": len(content) // 1024,
        "message": "영상 접수 완료. /events/{event_id} 로 결과를 조회하세요.",
    }



@app.get("/charts/{event_id}")
def get_chart(event_id: str):
    """생성된 11.txt 차트 내용 반환."""
    chart_path = CHART_DIR / f"{event_id}_11.txt"
    if not chart_path.exists():
        raise HTTPException(status_code=404, detail="차트 없음")
    _audit_log(event_id, "info", "chart viewed")
    chart = chart_path.read_text(encoding="utf-8")
    with _conn() as conn:
        review = _get_chart_review_by_event_id(conn, event_id)
    return {"event_id": event_id, "chart": chart, "quality": _chart_quality(chart), "review": review}


@app.put("/charts/{event_id}")
def update_chart(event_id: str, payload: ChartUpdatePayload):
    """치료사가 검수한 차트 본문을 저장하고 SOAP 요약을 동기화."""
    chart = (payload.chart or "").strip()
    if len(chart) < 20:
        _error(400, "CHART_TOO_SHORT", "저장할 차트 본문이 너무 짧습니다.")
    if len(chart) > 20000:
        _error(413, "CHART_TOO_LARGE", "저장할 차트 본문이 너무 깁니다.")

    sections = _parse_chart_sections(chart)
    s_val = sections.get("S>", "")
    o_val = sections.get("O>", "")
    a_val = sections.get("A>", "")
    p_val = sections.get("PTx.>", "")
    chart_path = CHART_DIR / f"{event_id}_11.txt"

    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        save_chart(chart_path, chart + "\n")
        conn.execute("DELETE FROM chart_reviews WHERE event_id = ?", (event_id,))

        row = conn.execute(
            "SELECT id FROM soap_notes WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE soap_notes SET s = ?, o = ?, a = ?, p = ? WHERE id = ?",
                (s_val, o_val, a_val, p_val, row[0]),
            )
        else:
            conn.execute(
                "INSERT INTO soap_notes (id, event_id, s, o, a, p) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), event_id, s_val, o_val, a_val, p_val),
            )

        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, "info", "chart updated manually"),
        )
        conn.commit()

    saved = chart_path.read_text(encoding="utf-8")
    return {"ok": True, "event_id": event_id, "chart": saved, "quality": _chart_quality(saved), "review": None}


@app.post("/charts/{event_id}/review")
def mark_chart_reviewed(event_id: str, payload: ChartReviewPayload):
    """치료사가 차트 초안을 검수 완료로 표시."""
    chart_path = CHART_DIR / f"{event_id}_11.txt"
    if not chart_path.exists():
        raise HTTPException(status_code=404, detail="차트 없음")

    chart = chart_path.read_text(encoding="utf-8")
    quality = _chart_quality(chart)
    reviewer = (payload.reviewer or "therapist").strip() or "therapist"
    notes = (payload.notes or "").strip()

    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        conn.execute(
            """
            INSERT INTO chart_reviews (event_id, reviewer, notes, quality_score, quality_level, reviewed_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET
              reviewer=excluded.reviewer,
              notes=excluded.notes,
              quality_score=excluded.quality_score,
              quality_level=excluded.quality_level,
              reviewed_at=CURRENT_TIMESTAMP
            """,
            (event_id, reviewer, notes, int(quality.get("score") or 0), str(quality.get("level") or "")),
        )
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, "info", f"chart reviewed reviewer={reviewer} quality={quality.get('level')} score={quality.get('score')}"),
        )
        conn.commit()
        review = _get_chart_review_by_event_id(conn, event_id)

    return {"ok": True, "event_id": event_id, "quality": quality, "review": review}


@app.delete("/charts/{event_id}/review")
def clear_chart_review(event_id: str):
    """차트 검수 완료 표시를 해제."""
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        deleted = conn.execute("DELETE FROM chart_reviews WHERE event_id = ?", (event_id,)).rowcount
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, "info", f"chart review cleared deleted={deleted}"),
        )
        conn.commit()

    quality = None
    chart_path = CHART_DIR / f"{event_id}_11.txt"
    if chart_path.exists():
        quality = _chart_quality(chart_path.read_text(encoding="utf-8"))
    return {"ok": True, "event_id": event_id, "quality": quality, "review": None}


@app.get("/chart-review")
def chart_review(limit: int = 50, include_good: bool = False, event_type: str = "combined"):
    """차트 품질 검수가 필요한 최근 기록 목록."""
    n = max(1, min(limit, 100))
    clean_event_type = event_type.strip().lower()
    if clean_event_type not in {"combined", "all"}:
        _error(400, "INVALID_EVENT_TYPE_FILTER", "event_type은 combined 또는 all이어야 합니다.")

    where_sql = "" if clean_event_type == "all" else "WHERE event_type = ?"
    params: list[object] = []
    if clean_event_type != "all":
        params.append(clean_event_type)
    params.append(n)

    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, source, event_type, intent, status, created_at, patient_name
            FROM events
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        items = []
        for r in rows:
            chart_path = CHART_DIR / f"{r[0]}_11.txt"
            if not chart_path.exists():
                continue

            chart = chart_path.read_text(encoding="utf-8")
            quality = _chart_quality(chart)
            review = _get_chart_review_by_event_id(conn, r[0])
            if review and not include_good:
                continue
            if not include_good and quality.get("level") == "good":
                continue

            label = _get_label_by_event_id(conn, r[0])
            first_issue = (quality.get("issues") or [{}])[0]
            items.append(
                {
                    "event_id": r[0],
                    "source": r[1],
                    "event_type": r[2],
                    "intent": r[3],
                    "status": r[4],
                    "created_at": r[5],
                    "patient_name": r[6] or None,
                    "has_label": label is not None,
                    "quality": quality,
                    "review": review,
                    "primary_issue": first_issue.get("message") or "",
                }
            )

    return {"items": items}


@app.get("/files/{filename}")
def get_uploaded_file(filename: str):
    if not ENABLE_FILE_DOWNLOADS:
        _error(404, "FILE_DOWNLOAD_DISABLED", "원본 업로드 파일 다운로드는 기본 비활성화되어 있습니다.")

    safe_name = Path(filename).name
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    media_type = None
    ext = file_path.suffix.lower()
    if ext in {".mp4", ".m4v"}:
        media_type = "video/mp4"
    elif ext == ".mov":
        media_type = "video/quicktime"
    elif ext == ".avi":
        media_type = "video/x-msvideo"
    elif ext == ".mkv":
        media_type = "video/x-matroska"

    return FileResponse(str(file_path), media_type=media_type, filename=safe_name)


@app.get("/masked-files/{filename}")
def get_masked_file(filename: str):
    """마스킹이 끝난 산출물만 보호된 경로로 내려준다."""
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=404, detail="file not found")

    file_path = MASKED_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(str(file_path), media_type="image/jpeg", filename=safe_name)


@app.get("/recent-events")
def recent_events(limit: int = 10):
    n = max(1, min(limit, 50))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, source, event_type, intent, status, created_at, patient_name FROM events ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()
    items = []
    with _conn() as conn:
        for r in rows:
            label = _get_label_by_event_id(conn, r[0])
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
                }
            )
    return {"items": items}


@app.get("/physio/sessions")
def physio_sessions(
    limit: int = 20,
    patient_name: str = "",
    org_id: str = "",
    provider_person_id: str = "",
    include_unscoped: bool = False,
):
    """physio_app에서 바로 읽을 수 있는 현장 세션 피드."""
    n = max(1, min(limit, 100))
    clean_patient_name = patient_name.strip()
    clean_org_id = (org_id or "").strip()
    clean_provider_person_id = (provider_person_id or "").strip()
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
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(n)

    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, source, event_type, intent, status, created_at, patient_name, owner_org_id, owner_provider_person_id
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
        "schema_version": "physio-session-feed/v1",
        "scope": {
            "org_id": clean_org_id or None,
            "provider_person_id": clean_provider_person_id or None,
            "include_unscoped": include_unscoped,
        },
    }


@app.post("/labels/{event_id}")
def upsert_label(event_id: str, payload: RehabLabelPayload):
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        conn.execute(
            """
            INSERT INTO rehab_labels (event_id, session_type, core_task, assist_level, performance, flags, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET
              session_type=excluded.session_type,
              core_task=excluded.core_task,
              assist_level=excluded.assist_level,
              performance=excluded.performance,
              flags=excluded.flags,
              notes=excluded.notes,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                event_id,
                payload.session_type,
                payload.core_task,
                payload.assist_level,
                payload.performance,
                json.dumps(payload.flags, ensure_ascii=False),
                payload.notes,
            ),
        )
        conn.commit()
        label = _get_label_by_event_id(conn, event_id)

    return {"ok": True, "label": label}


@app.get("/labels/{event_id}")
def get_label(event_id: str):
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")
        label = _get_label_by_event_id(conn, event_id)
    return {"event_id": event_id, "label": label}


@app.get("/recent-failures")
def recent_failures(limit: int = 20):
    n = max(1, min(limit, 100))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_id, level, message, created_at FROM audit_logs WHERE level='error' ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()

    return {
        "items": [
            {
                "event_id": r[0],
                "level": r[1],
                "message": r[2],
                "created_at": r[3],
            }
            for r in rows
        ]
    }


@app.get("/audit-logs")
def audit_logs(limit: int = 50, level: str = "", event_id: str = ""):
    n = max(1, min(limit, 200))
    filters = []
    params: list[object] = []

    clean_level = level.strip().lower()
    if clean_level:
        if clean_level not in {"info", "warning", "error"}:
            _error(400, "INVALID_AUDIT_LEVEL", "level은 info/warning/error 중 하나여야 합니다.")
        filters.append("level = ?")
        params.append(clean_level)

    clean_event_id = event_id.strip()
    if clean_event_id:
        filters.append("event_id = ?")
        params.append(clean_event_id)

    where_sql = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(n)

    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, event_id, level, message, created_at
            FROM audit_logs
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return {
        "items": [
            {
                "id": r[0],
                "event_id": r[1],
                "level": r[2],
                "message": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]
    }
