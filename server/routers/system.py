"""Service-level endpoints: landing pages, HUD shortlinks, health, and audit/failure logs."""

from fastapi import APIRouter
from datetime import datetime, timedelta, timezone
from fastapi.responses import HTMLResponse, RedirectResponse
from urllib.parse import urlencode

import bridge_core as core
from bridge_core import (
    ASYNC_RESULTS,
    ASYNC_RESULT_MAX_ITEMS,
    ASYNC_RESULT_TTL_MINUTES,
    PROCESS_TIMEOUT_SECONDS,
    ROOT,
    _conn,
    _error,
    _prune_async_results,
    build_hud_scope_token,
)

router = APIRouter()


@router.get("/g/demo")
def glass_demo_shortlink():
    params = []
    if core.BRIDGE_API_KEY:
        params.append(f"api_key={core.BRIDGE_API_KEY}")
    params.append("candidate_id=enc-demo-a1f607c7")
    return RedirectResponse(url="/glass-app/?" + "&".join(params), status_code=302)


@router.get("/g/hud-test")
def glass_hud_test_shortlink():
    """Open the non-PHI HUD fixture with a freshly scoped, short-lived token."""
    token = build_hud_scope_token(
        organization_id="t1",
        provider_person_id="p1",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    return RedirectResponse(
        url="/glass-app/?" + urlencode({"hud_token": token}),
        status_code=302,
    )


@router.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@router.get("/legacy", response_class=HTMLResponse)
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
    <input id='providerRole' type='text' value='physical_therapist' placeholder='provider_role (physical_therapist/pilates_instructor/personal_trainer)' />
    <input id='actionType' type='text' value='intervention' placeholder='action_type (assessment/instruction/intervention/reassessment)' />
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
    provider_role: document.getElementById('providerRole').value || 'unspecified',
    action_type: document.getElementById('actionType').value || 'observation',
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


@router.get("/health")
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
            "api_key_configured": bool(core.BRIDGE_API_KEY),
            "require_api_key": core.REQUIRE_API_KEY,
            "allow_insecure_lan": core.ALLOW_INSECURE_LAN,
            "docs_public_without_auth": core.ALLOW_DOCS_WITHOUT_AUTH,
            "file_downloads_enabled": core.ENABLE_FILE_DOWNLOADS,
            "allow_unmasked_image": core.ALLOW_UNMASKED_IMAGE,
            "patient_consent_required": core.REQUIRE_PATIENT_CONSENT,
            "audio_store": core.AUDIO_STORE,
            "video_store": core.VIDEO_STORE,
            "pilot_capture_mode": core.PILOT_CAPTURE_MODE,
        },
        "recent_error_logs_60m": recent_error_logs,
    }


@router.get("/recent-failures")
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


@router.get("/audit-logs")
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
