"""FastAPI application wiring for the rayban local bridge.

Application setup only: the app object, static mounts, the API-key/HUD-token
middleware, and router registration.  Configuration, mutable state, and domain
helpers live in :mod:`bridge_core`; endpoints live in :mod:`routers`.

``bridge_core`` is re-exported here so that existing tooling which does
``import app`` keeps seeing the helper names it used to.  Values that are
patched at runtime (``DB_PATH``, ``BRIDGE_API_KEY``, ...) must be set on
``bridge_core`` itself, because that is where every reader looks them up.
"""

import bridge_core as core
from bridge_core import *  # noqa: F401,F403  (backwards-compatible re-export)
from bridge_core import (  # noqa: F401  (names the app layer uses directly)
    ROOT,
    FastAPI,
    JSONResponse,
    Request,
    StaticFiles,
)

app = FastAPI(title="rayban-local-bridge", version="0.4.0")

app.mount(
    "/glass-app",
    StaticFiles(directory=str(ROOT / "static" / "glass-webapp"), html=True),
    name="glass-webapp",
)

app.mount(
    "/neural-band-console",
    StaticFiles(directory=str(ROOT / "static" / "neural-band-console"), html=True),
    name="neural-band-console",
)

@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    path = request.url.path
    is_public_prefix = any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in PUBLIC_PATH_PREFIXES
    )

    if (
        path in PUBLIC_PATHS
        or is_public_prefix
        or (core.ALLOW_DOCS_WITHOUT_AUTH and path in DOC_PATHS)
    ):
        return await call_next(request)

    if _is_hud_test_request(request):
        return await call_next(request)

    incoming_key = request.headers.get("x-api-key", "") or request.query_params.get("api_key", "")
    incoming_hud_token = request.headers.get("x-hud-token", "") or request.query_params.get("hud_token", "")
    hud_token_allowed = (
        path != HUD_TOKEN_ISSUE_PATH
        and any(path.startswith(prefix) for prefix in HUD_TOKEN_AUTH_PATH_PREFIXES)
    )
    if core.BRIDGE_API_KEY:
        if incoming_key == core.BRIDGE_API_KEY:
            return await call_next(request)
        if incoming_hud_token and hud_token_allowed:
            try:
                _decode_hud_scope_token(incoming_hud_token)
            except Exception as exc:
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "INVALID_HUD_SCOPE_TOKEN",
                        "message": str(exc),
                    },
                )
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={
                "code": "UNAUTHORIZED",
                "message": "유효한 x-api-key 헤더 또는 HUD scope token이 필요합니다.",
            },
        )

    if incoming_hud_token and hud_token_allowed:
        try:
            _decode_hud_scope_token(incoming_hud_token)
        except Exception as exc:
            return JSONResponse(
                status_code=401,
                content={
                    "code": "INVALID_HUD_SCOPE_TOKEN",
                    "message": str(exc),
                },
            )
        return await call_next(request)

    if not core.REQUIRE_API_KEY or core.ALLOW_INSECURE_LAN or _is_loopback_host(_client_host(request)):
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

@app.get("/g/demo")
def glass_demo_shortlink():
    params = []
    if core.BRIDGE_API_KEY:
        params.append(f"api_key={BRIDGE_API_KEY}")
    params.append("candidate_id=enc-demo-a1f607c7")
    return RedirectResponse(url="/glass-app/?" + "&".join(params), status_code=302)

@app.get("/g/hud-test")
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


@app.get("/events/{event_id}")
def get_event(event_id: str):
    _prune_async_results()
    row = ASYNC_RESULTS.get(event_id)
    if row:
        return row

    event_obj, soap_obj, label, _review, _artifacts = _get_event_snapshot(event_id)
    _audit_log(event_id, "info", "event viewed")
    return {"status": "done", "result": {"event": event_obj, "soap": soap_obj, "label": label}}

@app.get("/events/{event_id}/moai-export")
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

@app.get("/events/{event_id}/moai-write-plan")
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

@app.post("/events/{event_id}/moai-write")
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

@app.post("/hud/candidates")
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

@app.post("/hud/candidates/extract")
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

@app.get("/hud/candidates")
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

@app.get("/hud/candidates/{candidate_id}")
def get_hud_candidate(candidate_id: str):
    with _conn() as conn:
        candidate = _get_hud_candidate(conn, candidate_id)
    return {"status": "done", "candidate": candidate}

@app.post("/hud/candidates/{candidate_id}/approve")
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

@app.post("/hud/candidates/{candidate_id}/discard")
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

@app.get("/hud/candidates/{candidate_id}/moai-write-plan")
def get_hud_candidate_moai_write_plan(candidate_id: str):
    with _conn() as conn:
        candidate = _get_hud_candidate(conn, candidate_id)
    plan = _hud_candidate_plan(candidate)
    _audit_log(None, "info", f"HUD candidate moai write plan viewed id={candidate_id}")
    return {"status": "done", "result": plan}

@app.get("/moai-sync/jobs")
def get_moai_sync_jobs(status: str = "pending", limit: int = 20):
    clean_status = (status or "").strip().lower()
    if clean_status not in {"pending", "planned", "blocked", "synced", "error", "all"}:
        _error(400, "INVALID_SYNC_STATUS", "status must be pending, planned, blocked, synced, error, or all")
    return {"status": "done", "items": _list_moai_sync_jobs(status=clean_status, limit=limit)}

@app.get("/moai-sync/jobs/{event_id}")
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

@app.get("/events/{event_id}/pilot-manifest")
def get_event_pilot_manifest(event_id: str, resolve_identity: bool = True):
    return {"status": "done", "manifest": _build_pilot_manifest_for_event(event_id, resolve_identity=resolve_identity)}

@app.get("/events/{event_id}/pilot-readiness")
def get_event_pilot_readiness(event_id: str, resolve_identity: bool = True):
    manifest = _build_pilot_manifest_for_event(event_id, resolve_identity=resolve_identity)
    return {
        "status": "done",
        "event_id": event_id,
        "readiness": manifest["readiness"],
        "identity": manifest["identity"],
        "agent_dry_run": manifest["agent_dry_run"],
    }

@app.delete("/events/{event_id}")
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


@app.get("/charts/{event_id}")
def get_chart(event_id: str):
    """생성된 11.txt 차트 내용 반환."""
    chart_path = core.CHART_DIR / f"{event_id}_11.txt"
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
    chart_path = core.CHART_DIR / f"{event_id}_11.txt"

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
        _enqueue_moai_sync_job(conn, event_id, "chart_updated")
        conn.commit()

    saved = chart_path.read_text(encoding="utf-8")
    return {"ok": True, "event_id": event_id, "chart": saved, "quality": _chart_quality(saved), "review": None}

@app.post("/charts/{event_id}/review")
def mark_chart_reviewed(event_id: str, payload: ChartReviewPayload):
    """치료사가 차트 초안을 검수 완료로 표시."""
    chart_path = core.CHART_DIR / f"{event_id}_11.txt"
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
        _enqueue_moai_sync_job(conn, event_id, "chart_reviewed")
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
        _enqueue_moai_sync_job(conn, event_id, "chart_review_cleared")
        conn.commit()

    quality = None
    chart_path = core.CHART_DIR / f"{event_id}_11.txt"
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
            chart_path = core.CHART_DIR / f"{r[0]}_11.txt"
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
    if not core.ENABLE_FILE_DOWNLOADS:
        _error(404, "FILE_DOWNLOAD_DISABLED", "원본 업로드 파일 다운로드는 기본 비활성화되어 있습니다.")

    safe_name = Path(filename).name
    file_path = core.UPLOAD_DIR / safe_name
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

    file_path = core.MASKED_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(str(file_path), media_type="image/jpeg", filename=safe_name)

@app.get("/raw-media/{filename}")
def get_raw_media(filename: str, request: Request):
    file_path, event_id = _authorize_raw_media_request(filename, request)
    artifacts = list_raw_media_artifacts(core.RAW_MEDIA_DIR, event_id)
    content_type = next(
        (item["content_type"] for item in artifacts if item["filename"] == file_path.name),
        "application/octet-stream",
    )
    _audit_log(event_id, "info", "raw media accessed for scoped import")
    return FileResponse(str(file_path), media_type=content_type, filename=file_path.name)

@app.delete("/raw-media/{filename}")
def consume_raw_media(filename: str, request: Request):
    _, event_id = _authorize_raw_media_request(filename, request)
    if not delete_raw_media(core.RAW_MEDIA_DIR, filename):
        raise HTTPException(status_code=404, detail="file not found")
    _audit_log(event_id, "info", "raw media consumed after durable import")
    return {"ok": True, "filename": filename}

@app.get("/recent-events")
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

@app.get("/physio/sessions")
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

@app.get("/label-taxonomy")
def get_label_taxonomy():
    return {"status": "done", "taxonomy": LABEL_TAXONOMY_V0}

@app.post("/labels/{event_id}")
def upsert_label(event_id: str, payload: RehabLabelPayload):
    performance = _label_performance_value(payload)
    safety_flags = payload.safety_flags if payload.safety_flags is not None else payload.flags
    label_confidence = payload.label_confidence
    if label_confidence is not None and not (0 <= label_confidence <= 1):
        _error(422, "INVALID_LABEL_CONFIDENCE", "label_confidence는 0과 1 사이여야 합니다.")
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        conn.execute(
            """
            INSERT INTO rehab_labels (
              event_id, provider_role, action_type, session_type, core_task, custom_task, body_position,
              assist_level, performance, review_status, reviewer_person_id,
              usable_for_training, label_confidence, repetition_count,
              hold_duration_seconds, tolerance, fatigue_level, compensations,
              caregiver_present, flags, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET
              provider_role=excluded.provider_role,
              action_type=excluded.action_type,
              session_type=excluded.session_type,
              core_task=excluded.core_task,
              custom_task=excluded.custom_task,
              body_position=excluded.body_position,
              assist_level=excluded.assist_level,
              performance=excluded.performance,
              review_status=excluded.review_status,
              reviewer_person_id=excluded.reviewer_person_id,
              usable_for_training=excluded.usable_for_training,
              label_confidence=excluded.label_confidence,
              repetition_count=excluded.repetition_count,
              hold_duration_seconds=excluded.hold_duration_seconds,
              tolerance=excluded.tolerance,
              fatigue_level=excluded.fatigue_level,
              compensations=excluded.compensations,
              caregiver_present=excluded.caregiver_present,
              flags=excluded.flags,
              notes=excluded.notes,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                event_id,
                payload.provider_role.strip() or "unspecified",
                payload.action_type.strip() or "observation",
                payload.session_type,
                payload.core_task,
                payload.custom_task.strip(),
                payload.body_position.strip(),
                payload.assist_level,
                performance,
                payload.review_status.strip() or "reviewed",
                payload.reviewer_person_id.strip(),
                1 if payload.usable_for_training else 0,
                label_confidence,
                payload.repetition_count,
                payload.hold_duration_seconds,
                payload.tolerance.strip(),
                payload.fatigue_level.strip(),
                json.dumps(payload.compensations, ensure_ascii=False),
                None if payload.caregiver_present is None else (1 if payload.caregiver_present else 0),
                json.dumps(safety_flags, ensure_ascii=False),
                payload.notes,
            ),
        )
        _enqueue_moai_sync_job(conn, event_id, "label_upserted")
        conn.commit()
        label = _get_label_by_event_id(conn, event_id)

    manifest = _build_pilot_manifest_for_event(event_id, resolve_identity=False)
    return {"ok": True, "label": label, "readiness": manifest["readiness"]}

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

@app.post("/glass/hud-token")
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

@app.post("/visit-sessions/start")
def visit_session_start(payload: VisitSessionStartRequest):
    provider_role = payload.provider_role.strip()
    if provider_role not in PROVIDER_ROLES:
        _error(400, "INVALID_PROVIDER_ROLE", f"provider_role must be one of: {', '.join(sorted(PROVIDER_ROLES))}")
    with _conn() as conn:
        session = create_visit_session(
            conn,
            organization_id=payload.organization_id.strip(),
            provider_person_id=payload.provider_person_id.strip(),
            provider_role=provider_role,
            subject_person_id=payload.subject_person_id.strip(),
            encounter_id=(payload.encounter_id or "").strip() or None,
            patient_alias=payload.patient_alias,
            history_summary=payload.history_summary,
        )
        session, pre_review = _attach_pre_review_to_session(conn, session)
        conn.commit()
    hud = _apply_visit_session_hud(session, insight=pre_review) if payload.update_glass else None
    _audit_log(None, "info", f"visit session started id={session['id']}")
    return {"status": "started", "session": session, "glass_state": hud}

@app.get("/visit-sessions/{session_id}")
def visit_session_get(session_id: str):
    with _conn() as conn:
        session = get_visit_session(conn, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="visit session not found")
    return {"status": "done", "session": session}

@app.post("/visit-sessions/{session_id}/phase")
def visit_session_set_phase(session_id: str, payload: VisitSessionPhaseRequest):
    try:
        with _conn() as conn:
            session = update_visit_phase(conn, session_id, payload.phase, payload.cue)
            conn.commit()
    except ValueError as exc:
        _error(400, "INVALID_VISIT_PHASE", str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="visit session not found")
    hud = _apply_visit_session_hud(session) if payload.update_glass else None
    _audit_log(None, "info", f"visit session phase id={session_id} phase={session['phase']}")
    return {"status": "updated", "session": session, "glass_state": hud}

@app.post("/visit-sessions/{session_id}/recording")
def visit_session_set_recording(session_id: str, payload: VisitSessionRecordingRequest):
    try:
        with _conn() as conn:
            session = set_visit_recording(conn, session_id, payload.is_recording)
            conn.commit()
    except KeyError:
        raise HTTPException(status_code=404, detail="visit session not found")
    hud = _apply_visit_session_hud(session) if payload.update_glass else None
    _audit_log(None, "info", f"visit session recording id={session_id} recording={payload.is_recording}")
    return {"status": "updated", "session": session, "glass_state": hud}

@app.post("/visit-sessions/{session_id}/events")
def visit_session_attach_event(session_id: str, payload: VisitSessionEventRequest):
    with _conn() as conn:
        event_exists = conn.execute("SELECT id FROM events WHERE id = ?", (payload.event_id,)).fetchone()
        if not event_exists:
            raise HTTPException(status_code=404, detail="event not found")
        try:
            session = attach_visit_event(conn, session_id, payload.event_id, role=payload.role, phase=payload.phase)
        except KeyError:
            raise HTTPException(status_code=404, detail="visit session not found")
        conn.commit()
    hud = _apply_visit_session_hud(session) if payload.update_glass else None
    _audit_log(payload.event_id, "info", f"event attached to visit session id={session_id}")
    return {"status": "attached", "session": session, "glass_state": hud}

@app.post("/capture-events")
def capture_event_create(payload: CaptureEventPayload, request: Request):
    source_type = payload.source_type.strip().lower()
    event_type = payload.event_type.strip()
    candidate_type = (payload.candidate_type or event_type).strip()
    status = payload.status.strip().lower()
    if source_type not in CAPTURE_EVENT_SOURCE_TYPES:
        _error(400, "INVALID_CAPTURE_SOURCE", f"source_type must be one of: {', '.join(sorted(CAPTURE_EVENT_SOURCE_TYPES))}")
    if not event_type or not candidate_type:
        _error(400, "INVALID_CAPTURE_EVENT", "event_type and candidate_type are required")
    if status not in CAPTURE_EVENT_STATUSES:
        _error(400, "INVALID_CAPTURE_STATUS", f"status must be one of: {', '.join(sorted(CAPTURE_EVENT_STATUSES))}")
    if payload.start_ms is not None and payload.start_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "start_ms must be non-negative")
    if payload.end_ms is not None and payload.end_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be non-negative")
    if payload.start_ms is not None and payload.end_ms is not None and payload.end_ms < payload.start_ms:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be greater than or equal to start_ms")
    if payload.confidence is not None and not 0 <= payload.confidence <= 1:
        _error(400, "INVALID_CAPTURE_CONFIDENCE", "confidence must be between 0 and 1")

    with _conn() as conn:
        session = None
        if payload.visit_session_id:
            session = get_visit_session(conn, payload.visit_session_id.strip())
            if not session:
                raise HTTPException(status_code=404, detail="visit session not found")

        organization_id, provider_person_id = _scope_from_request(
            request,
            owner_org_id=payload.organization_id or (session or {}).get("organization_id"),
            owner_provider_person_id=payload.provider_person_id or (session or {}).get("provider_person_id"),
        )
        organization_id = organization_id or _clean_scope_value(payload.organization_id)
        provider_person_id = provider_person_id or _clean_scope_value(payload.provider_person_id)
        encounter_id = _clean_scope_value(payload.encounter_id) or (session or {}).get("encounter_id")
        subject_person_id = _clean_scope_value(payload.subject_person_id) or (session or {}).get("subject_person_id")
        visit_session_id = _clean_scope_value(payload.visit_session_id)
        source_media_id = _clean_scope_value(payload.source_media_id)
        source_event_id = _clean_scope_value(payload.source_event_id)
        reviewed_by = _clean_scope_value(payload.reviewed_by)
        reviewed_at = datetime.utcnow().isoformat() if reviewed_by and status != "draft" else None
        event_payload = dict(payload.payload or {})
        event_payload.setdefault("action_type", capture_action_type(candidate_type))
        if session and session.get("provider_role"):
            event_payload.setdefault("provider_role", session["provider_role"])
        event_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO capture_events (
                id, visit_session_id, encounter_id, organization_id, provider_person_id,
                subject_person_id, source_media_id, source_event_id, source_type, event_type,
                candidate_type, start_ms, end_ms, confidence, status, payload_json,
                reviewed_by, reviewed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                visit_session_id,
                encounter_id,
                organization_id,
                provider_person_id,
                subject_person_id,
                source_media_id,
                source_event_id,
                source_type,
                event_type,
                candidate_type,
                payload.start_ms,
                payload.end_ms,
                payload.confidence,
                status,
                json.dumps(event_payload, ensure_ascii=False, separators=(",", ":")),
                reviewed_by,
                reviewed_at,
                now,
                now,
            ),
        )
        row = conn.execute(f"{_capture_event_select()} WHERE id = ?", (event_id,)).fetchone()
        conn.commit()
    event = _capture_event_from_row(row)
    _audit_log(None, "info", f"capture event created id={event_id} type={event_type} source_event={source_event_id or '-'}")
    return {"status": "created", "event": event}

@app.post("/capture-events/extract")
def capture_event_extract(payload: CaptureEventExtractPayload, request: Request):
    """Create review-first capture evidence from explicit therapist language."""

    text = payload.text.strip()
    if not text:
        _error(422, "CAPTURE_TRANSCRIPT_REQUIRED", "text는 비어 있을 수 없습니다.")
    if payload.start_ms is not None and payload.start_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "start_ms must be non-negative")
    if payload.end_ms is not None and payload.end_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be non-negative")
    if payload.start_ms is not None and payload.end_ms is not None and payload.end_ms < payload.start_ms:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be greater than or equal to start_ms")
    if payload.confidence is not None and not 0 <= payload.confidence <= 1:
        _error(400, "INVALID_CAPTURE_CONFIDENCE", "confidence must be between 0 and 1")

    with _conn() as conn:
        session = None
        if payload.visit_session_id:
            session = get_visit_session(conn, payload.visit_session_id.strip())
            if not session:
                raise HTTPException(status_code=404, detail="visit session not found")

        organization_id, provider_person_id = _scope_from_request(
            request,
            owner_org_id=payload.organization_id or (session or {}).get("organization_id"),
            owner_provider_person_id=payload.provider_person_id or (session or {}).get("provider_person_id"),
        )
        organization_id = organization_id or _clean_scope_value(payload.organization_id)
        provider_person_id = provider_person_id or _clean_scope_value(payload.provider_person_id)
        encounter_id = _clean_scope_value(payload.encounter_id) or (session or {}).get("encounter_id")
        subject_person_id = _clean_scope_value(payload.subject_person_id) or (session or {}).get("subject_person_id")
        visit_session_id = _clean_scope_value(payload.visit_session_id)

        if not encounter_id and not visit_session_id:
            _error(422, "CAPTURE_SCOPE_REQUIRED", "encounter_id or visit_session_id is required")

        candidates = extract_transcript_capture_candidates(
            text,
            provider_role=(session or {}).get("provider_role"),
        )
        if not payload.create_events:
            return {
                "status": "preview" if candidates else "no_candidates",
                "extractor_version": TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION,
                "source_text": _short_lens_text(text, limit=2_000),
                "candidates": candidates,
            }

        events = _create_transcript_capture_events(
            conn,
            text=text,
            visit_session_id=visit_session_id,
            encounter_id=encounter_id,
            organization_id=organization_id,
            provider_person_id=provider_person_id,
            provider_role=(session or {}).get("provider_role"),
            subject_person_id=subject_person_id,
            source_event_id=payload.source_event_id,
            source_media_id=payload.source_media_id,
            start_ms=payload.start_ms,
            end_ms=payload.end_ms,
            confidence=payload.confidence,
            capture_origin=_capture_origin_from_source(payload.capture_origin),
            derived_from=payload.source_type.strip().lower() or "transcript",
        )
        conn.commit()

    # source_event_id may refer to an upstream media event that is not present
    # in this bridge's local `events` table, so it must not be used as the
    # audit_logs foreign key without a local existence check.
    audit_event_id = None
    if payload.source_event_id:
        with _conn() as audit_conn:
            if audit_conn.execute("SELECT 1 FROM events WHERE id = ?", (payload.source_event_id,)).fetchone():
                audit_event_id = payload.source_event_id
    _audit_log(
        audit_event_id,
        "info",
        f"transcript capture extraction candidates={len(events)} source_event={payload.source_event_id or '-'}",
    )
    return {
        "status": "created" if events else "no_candidates",
        "extractor_version": TRANSCRIPT_CAPTURE_EXTRACTOR_VERSION,
        "source_event_id": payload.source_event_id,
        "events": events,
    }

@app.get("/visit-sessions/{session_id}/capture-events")
def capture_event_list_by_session(session_id: str, limit: int = 100):
    if limit < 1 or limit > 500:
        _error(400, "INVALID_CAPTURE_LIMIT", "limit must be between 1 and 500")
    with _conn() as conn:
        session = get_visit_session(conn, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="visit session not found")
        rows = conn.execute(
            f"{_capture_event_select()} WHERE visit_session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return {"items": [_capture_event_from_row(row) for row in rows]}

@app.get("/capture-events")
def capture_event_list(
    request: Request,
    encounter_id: Optional[str] = None,
    visit_session_id: Optional[str] = None,
    limit: int = 100,
):
    if not _clean_scope_value(encounter_id) and not _clean_scope_value(visit_session_id):
        _error(400, "CAPTURE_SCOPE_REQUIRED", "encounter_id or visit_session_id is required")
    if limit < 1 or limit > 500:
        _error(400, "INVALID_CAPTURE_LIMIT", "limit must be between 1 and 500")
    filters: list[str] = []
    values: list[object] = []
    scoped_org_id, scoped_provider_person_id = _scope_from_request(request)
    if scoped_org_id:
        filters.append("organization_id = ?")
        values.append(scoped_org_id)
    if scoped_provider_person_id:
        filters.append("provider_person_id = ?")
        values.append(scoped_provider_person_id)
    if encounter_id and encounter_id.strip():
        filters.append("encounter_id = ?")
        values.append(encounter_id.strip())
    if visit_session_id and visit_session_id.strip():
        filters.append("visit_session_id = ?")
        values.append(visit_session_id.strip())
    with _conn() as conn:
        rows = conn.execute(
            f"{_capture_event_select()} WHERE {' AND '.join(filters)} ORDER BY created_at ASC LIMIT ?",
            (*values, limit),
        ).fetchall()
    return {"items": [_capture_event_from_row(row) for row in rows]}

@app.patch("/capture-events/{event_id}")
def capture_event_update(event_id: str, payload: CaptureEventUpdatePayload, request: Request):
    status = payload.status.strip().lower() if payload.status is not None else None
    if status is not None and status not in CAPTURE_EVENT_STATUSES:
        _error(400, "INVALID_CAPTURE_STATUS", f"status must be one of: {', '.join(sorted(CAPTURE_EVENT_STATUSES))}")
    if payload.start_ms is not None and payload.start_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "start_ms must be non-negative")
    if payload.end_ms is not None and payload.end_ms < 0:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be non-negative")
    if payload.start_ms is not None and payload.end_ms is not None and payload.end_ms < payload.start_ms:
        _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be greater than or equal to start_ms")
    if payload.confidence is not None and not 0 <= payload.confidence <= 1:
        _error(400, "INVALID_CAPTURE_CONFIDENCE", "confidence must be between 0 and 1")

    with _conn() as conn:
        existing = conn.execute(f"{_capture_event_select()} WHERE id = ?", (event_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="capture event not found")
        existing_event = _capture_event_from_row(existing)
        scoped_org_id, scoped_provider_person_id = _scope_from_request(request)
        if scoped_org_id and existing_event["organization_id"] and scoped_org_id != existing_event["organization_id"]:
            raise HTTPException(status_code=403, detail="capture event organization scope mismatch")
        if (
            scoped_provider_person_id
            and existing_event["provider_person_id"]
            and scoped_provider_person_id != existing_event["provider_person_id"]
        ):
            raise HTTPException(status_code=403, detail="capture event provider scope mismatch")
        next_start_ms = payload.start_ms if payload.start_ms is not None else existing_event["start_ms"]
        next_end_ms = payload.end_ms if payload.end_ms is not None else existing_event["end_ms"]
        if next_start_ms is not None and next_end_ms is not None and next_end_ms < next_start_ms:
            _error(400, "INVALID_CAPTURE_TIMESTAMP", "end_ms must be greater than or equal to start_ms")
        next_status = status or existing_event["status"]
        reviewed_by = _clean_scope_value(payload.reviewed_by) or existing_event["reviewed_by"]
        reviewed_at = existing_event["reviewed_at"]
        if next_status != "draft" and reviewed_by:
            reviewed_at = reviewed_at or datetime.utcnow().isoformat()
        next_payload = payload.payload if payload.payload is not None else existing_event["payload"]
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE capture_events
            SET start_ms = ?, end_ms = ?, confidence = ?, status = ?, payload_json = ?,
                reviewed_by = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_start_ms,
                next_end_ms,
                payload.confidence if payload.confidence is not None else existing_event["confidence"],
                next_status,
                json.dumps(next_payload or {}, ensure_ascii=False, separators=(",", ":")),
                reviewed_by,
                reviewed_at,
                now,
                event_id,
            ),
        )
        row = conn.execute(f"{_capture_event_select()} WHERE id = ?", (event_id,)).fetchone()
        conn.commit()
    event = _capture_event_from_row(row)
    _audit_log(None, "info", f"capture event updated id={event_id} status={event['status']}")
    return {"status": "updated", "event": event}

@app.post("/visit-sessions/{session_id}/end")
def visit_session_end(session_id: str, update_glass: bool = True):
    try:
        with _conn() as conn:
            session = end_visit_session(conn, session_id)
            session = _refresh_visit_progress_note_from_events(conn, session)
            plan = _build_visit_session_write_plan(session)
            sync_job = _enqueue_visit_session_sync_job(conn, session, plan)
            conn.commit()
    except KeyError:
        raise HTTPException(status_code=404, detail="visit session not found")
    hud = _apply_visit_sync_pending_hud(session, sync_job) if update_glass else None
    _audit_log(None, "info", f"visit session ended id={session_id}")
    return {"status": "ended", "session": session, "glass_state": hud, "moai_write_plan": plan, "moai_sync_job": sync_job}

@app.get("/glass/visits/next")
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

@app.post("/glass/visits/start")
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

@app.get("/glass/state")
def glass_state_get(request: Request):
    with _glass_lock:
        if _is_hud_test_request(request):
            return dict(_hud_test_state)
        return dict(_glass_state)

@app.post("/glass/state")
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

@app.post("/glass/command")
def glass_command_post(cmd: GlassCommandRequest, request: Request):
    scope = _hud_scope_from_request(request)
    queued = _queue_glass_command(cmd.command, scope=scope)
    response = {"ok": True, "command": queued["command"], "id": queued["id"]}
    if "executed" in queued:
        response["executed"] = queued["executed"]
    return response

@app.post("/neural-band/event")
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

@app.post("/agent/cue-dry-run")
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

@app.get("/glass/command")
def glass_command_get():
    with _glass_lock:
        cmd = core._glass_pending_command.pop(0) if core._glass_pending_command else None
    if cmd is None:
        return {"command": None}
    return cmd

@app.get("/glass/device-command")
def glass_device_command_get():
    """Consume one command intended for the paired native iOS app."""
    with _glass_lock:
        cmd = core._glass_pending_device_command.pop(0) if core._glass_pending_device_command else None
    if cmd is None:
        return {"command": None}
    return cmd

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


# ── routers ─────────────────────────────────────────────────────────────────

from routers.consents import router as consents_router
from routers.ingest import router as ingest_router

app.include_router(consents_router)
app.include_router(ingest_router)
