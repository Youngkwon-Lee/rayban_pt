(function () {
  'use strict';

  var params = new URLSearchParams(window.location.search);
  var DEFAULT_PRODUCTION_BRIDGE = 'https://desktop-t43sn5m-1.tailde3b80.ts.net/glasspt';
  var PATH_CONFIG = parsePathConfig(window.location.pathname);
  var IS_HUD_TEST = String(window.location.pathname || '').replace(/\/+$/, '') === '/hud-test';
  var API_KEY = params.get('api_key') || '';
  var HUD_TOKEN = params.get('hud_token') || PATH_CONFIG.hudToken || '';
  var TARGET_CANDIDATE_ID = params.get('candidate_id') || params.get('encounter_id') || '';
  // A path-scoped HUD token is served through Vercel's same-origin proxy.
  // This prevents the Display browser from needing cross-origin CORS access
  // to the private bridge while keeping the token out of query parameters.
  var BRIDGE_BASE_URL = (PATH_CONFIG.hudToken || IS_HUD_TEST)
    ? window.location.origin
    : (normalizeBaseUrl(params.get('bridge_url')) || PATH_CONFIG.bridgeUrl || window.location.origin);
  var POLL_MS = 2000;
  var INSIGHT_DURATION_MS = 8000;

  var glassState = {
    patient: null,
    mode: 'standby',
    message: '라이브 연결을 기다리는 중',
    is_recording: false,
    recording_start: null,
    session_count: 0,
    event_role_counts: {},
    capture_role: 'observation',
    active_hud_candidate: null,
    visit_session_id: null,
    phase: 'pre_review',
    readiness: 'ready',
    error_state: null,
    last_insight: null,
    updated_at: null,
  };
  var lastInsightId = null;
  var insightDismissTimer = null;
  var insightProgressTimer = null;
  var insightStartTime = null;
  var statePollTimer = null;
  var timerPollTimer = null;
  var commandPollTimer = null;
  var visitCandidate = null;
  var visitCandidateOffset = 0;
  var lastCandidateLoadAt = 0;
  var sessionRecordPreview = null;
  var recordPreviewLineIndex = 0;
  var recordPreviewOpen = false;
  var transportConnected = false;
  var commandPending = false;

  function parsePathConfig(pathname) {
    var match = String(pathname || '').match(/^\/connect\/([^/]+)\/?$/);
    if (!match) return {};
    try {
      return {
        hudToken: decodeURIComponent(match[1]),
        bridgeUrl: DEFAULT_PRODUCTION_BRIDGE,
      };
    } catch (e) {
      return {};
    }
  }

  function apiHeaders() {
    var h = { 'Content-Type': 'application/json' };
    if (API_KEY) h['x-api-key'] = API_KEY;
    if (HUD_TOKEN) h['x-hud-token'] = HUD_TOKEN;
    if (IS_HUD_TEST) h['x-hud-test'] = '1';
    return h;
  }

  function apiUrl(path) {
    return BRIDGE_BASE_URL + path;
  }

  function clearAuthParamsFromAddressBar() {
    if (!window.history || !window.history.replaceState) return;
    if (!params.has('api_key') && !params.has('hud_token')) return;
    var cleanUrl = new URL(window.location.href);
    cleanUrl.searchParams.delete('api_key');
    cleanUrl.searchParams.delete('hud_token');
    window.history.replaceState({}, document.title, cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
  }

  function normalizeBaseUrl(raw) {
    var value = String(raw || '').trim();
    if (!value) return '';
    try {
      var url = new URL(value);
      if (url.protocol !== 'https:' && url.protocol !== 'http:') return '';
      var pathname = url.pathname.replace(/\/+$/, '');
      return url.origin + (pathname === '/' ? '' : pathname);
    } catch (e) {
      return '';
    }
  }

  function pollState() {
    return fetch(apiUrl('/glass/state'), { headers: apiHeaders(), cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        glassState = data;
        setConnected(true);
        maybeLoadVisitCandidate();
      })
      .catch(function () {
        setConnected(false);
        throw new Error('state fetch failed');
      });
  }

  function pollStateQuiet() {
    pollState().catch(function () {});
  }

  function sendCommand(command) {
    if (command === 'close_hud') {
      closeHud();
      return;
    }
    if (!transportConnected) {
      showToast('연결 확인 필요', 'error');
      return;
    }
    if (commandPending) {
      showToast('처리 중', 'error');
      return;
    }
    if (command === 'start_visit') {
      startVisit();
      return;
    }
    if (command === 'primary_action') {
      refreshVisibleStatus();
      return;
    }
    if (command === 'complete_visit_hud') {
      completeVisitHudSession();
      return;
    }
    if (command === 'cycle_record_preview') {
      cycleRecordPreview();
      return;
    }
    if (command === 'next_phase' && !glassState.visit_session_id) {
      if (TARGET_CANDIDATE_ID) {
        showToast('환자 고정됨', 'success');
        loadNextVisitCandidate(0);
        return;
      }
      loadNextVisitCandidate(visitCandidateOffset + 1);
      return;
    }
    setCommandPending(true);
    fetch(apiUrl('/glass/command'), {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ command: command }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (data.executed && data.executed.glass_state) {
          glassState = data.executed.glass_state;
          render();
          showToast(commandResultLabel(command, data.executed), data.executed.ok === false ? 'error' : 'success');
        } else {
          showToast(commandLabel(command) + ' 요청됨', 'success');
        }
      })
      .catch(function () {
        showToast('명령 실패 · 연결 확인', 'error');
      })
      .finally(function () {
        setCommandPending(false);
      });
  }

  function pollPendingCommand() {
    return fetch(apiUrl('/glass/command'), { headers: apiHeaders(), cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.command) return;
        if (handleNavigationCommand(data.command)) return;
        if (data.command === 'cycle_record_preview' || data.command === 'open_capture_history') {
          cycleRecordPreview();
          return;
        }
        if (data.executed && data.executed.glass_state) {
          glassState = data.executed.glass_state;
          render();
          showToast(commandResultLabel(data.command, data.executed), data.executed.ok === false ? 'error' : 'success');
          return;
        }
        showToast(commandLabel(data.command) + ' 수신', 'success');
      });
  }

  function pollPendingCommandQuiet() {
    pollPendingCommand().catch(function () {});
  }

  function loadNextVisitCandidate(offset) {
    visitCandidateOffset = Math.max(0, offset || 0);
    var path = TARGET_CANDIDATE_ID
      ? '/glass/visits/next?candidate_id=' + encodeURIComponent(TARGET_CANDIDATE_ID)
      : '/glass/visits/next?offset=' + encodeURIComponent(visitCandidateOffset);
    fetch(apiUrl(path), {
      headers: apiHeaders(),
      cache: 'no-store',
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        visitCandidate = data.candidate || null;
        if (!glassState.visit_session_id) sessionRecordPreview = null;
        recordPreviewLineIndex = 0;
        recordPreviewOpen = Boolean(visitCandidate && visitCandidate.record_preview);
        lastCandidateLoadAt = Date.now();
        render();
      })
      .catch(function () {
        lastCandidateLoadAt = Date.now();
        render();
      });
  }

  function maybeLoadVisitCandidate() {
    if (glassState.visit_session_id) return;
    if (Date.now() - lastCandidateLoadAt < 10000) return;
    loadNextVisitCandidate(visitCandidateOffset);
  }

  function refreshVisibleStatus() {
    pollState()
      .then(function () {
        showToast(statusToastLabel(), 'success');
      })
      .catch(function () {
        showToast('연결 확인 필요', 'error');
      });
  }

  function completeVisitHudSession() {
    setCommandPending(true);
    fetch(apiUrl('/glass/state'), {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({
        patient: null,
        mode: 'standby',
        message: '다음 방문 대기',
        is_recording: false,
        recording_start: null,
        session_count: 0,
        event_role_counts: {},
        capture_role: 'observation',
        active_hud_candidate: null,
        visit_session_id: null,
        phase: 'pre_review',
        readiness: 'ready',
        error_state: null,
        last_insight: null,
      }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        visitCandidate = null;
        visitCandidateOffset = 0;
        recordPreviewLineIndex = 0;
        lastCandidateLoadAt = 0;
        sessionRecordPreview = null;
        glassState = {
          patient: null,
          mode: 'standby',
          message: '다음 방문 대기',
          is_recording: false,
          recording_start: null,
          session_count: 0,
          event_role_counts: {},
          capture_role: 'observation',
          active_hud_candidate: null,
          visit_session_id: null,
          phase: 'pre_review',
          readiness: 'ready',
          error_state: null,
          last_insight: null,
          updated_at: null,
        };
        render();
        loadNextVisitCandidate(0);
        showToast('HUD 정리됨', 'success');
      })
      .catch(function () {
        showToast('완료 실패 · 연결 확인', 'error');
      })
      .finally(function () {
        setCommandPending(false);
      });
  }

  function closeHud() {
    clearInterval(statePollTimer);
    clearInterval(timerPollTimer);
    clearInterval(commandPollTimer);
    document.body.classList.add('hud-closed');
    showToast('HUD 닫힘', 'success');
    window.setTimeout(function () {
      window.close();
      if (!window.closed) {
        try {
          window.location.replace('about:blank');
        } catch (e) {
          document.body.innerHTML = '';
        }
      }
    }, 120);
  }

  function startVisit() {
    var previewBeforeStart = visitCandidate && visitCandidate.record_preview;
    var body = {
      candidate_id: visitCandidate ? visitCandidate.id : (TARGET_CANDIDATE_ID || null),
      update_glass: true,
    };
    setCommandPending(true);
    fetch(apiUrl('/glass/visits/start'), {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify(body),
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (data.glass_state) {
          glassState = data.glass_state;
          sessionRecordPreview = previewBeforeStart || sessionRecordPreview;
          visitCandidate = null;
          recordPreviewLineIndex = 0;
          recordPreviewOpen = false;
          render();
        }
        showToast('세션 시작됨', 'success');
      })
      .catch(function () {
        showToast('시작 실패 · 연결 확인', 'error');
      })
      .finally(function () {
        setCommandPending(false);
      });
  }

  function render() {
    var home = document.getElementById('home');
    if (home) home.dataset.connected = transportConnected ? 'true' : 'false';
    renderPatient();
    renderReadiness();
    renderStatus();
    renderInsight();
    renderToggleButton();
    renderMiddleButton();
    renderEndButton();
    updateCommandRailLayout();
  }

  function renderPatient() {
    var candidateAlias = visitCandidate && visitCandidate.patient_alias;
    var alias = safePatientAlias(glassState.patient || (!glassState.visit_session_id ? candidateAlias : ''));
    var count = glassState.session_count || 0;
    if (!transportConnected) {
      setText('patient-context-label', alias ? '최근 환자' : '환자');
    } else if (glassState.visit_session_id) {
      setText('patient-context-label', '진행 중인 세션');
    } else if (alias) {
      setText('patient-context-label', TARGET_CANDIDATE_ID ? '선택한 환자' : '다음 환자');
    } else {
      setText('patient-context-label', '환자');
    }
    setText('patient-name', alias || '미선택');
    setText('session-label', 'E' + count);
  }

  function renderReadiness() {
    var label = document.getElementById('readiness-label');
    if (!label) return;
    var hasError = !transportConnected || Boolean(glassState.error_state) || glassState.mode === 'error';
    var ready = String(glassState.readiness || '').toLowerCase() || (hasError ? 'error' : 'ready');
    label.classList.toggle('ready', !hasError && ready === 'ready');
    label.classList.toggle('error', hasError || ready === 'error');
    label.textContent = !transportConnected ? '오프라인' : (hasError ? '오류' : (ready === 'ready' ? '준비' : '확인'));
  }

  function renderStatus() {
    var card = document.getElementById('status-card');
    var recDot = document.getElementById('rec-dot');
    var timer = document.getElementById('rec-timer');
    var mode = normalizedMode();
    var copy = statusCopy(mode, glassState.message);

    card.dataset.mode = mode;
    recDot.classList.toggle('hidden', mode !== 'recording');
    timer.classList.toggle('hidden', mode !== 'recording');
    timer.textContent = mode === 'recording' ? elapsedString(glassState.recording_start) : '';

    setText('status-kicker', copy.kicker);
    setText('status-title', copy.title);
    setText('status-meta', copy.meta);
    setText('status-caption', copy.caption);
    renderRecordList(copy.preview || null);

    var interactive = statusCardInteractive();
    card.classList.toggle('focusable', interactive);
    card.tabIndex = interactive ? 0 : -1;
    if (interactive) {
      card.dataset.action = 'cycle_record_preview';
    } else {
      card.removeAttribute('data-action');
      if (document.activeElement === card) focusCommand(0);
    }
  }

  function cycleRecordPreview() {
    var preview = recordPreview();
    if (!preview || !preview.lines || !preview.lines.length) {
      showToast('표시할 기록 없음', 'error');
      return;
    }
    var wasOpen = recordPreviewOpen;
    recordPreviewOpen = true;
    if (wasOpen) {
      recordPreviewLineIndex = (recordPreviewLineIndex + 1) % preview.lines.length;
    } else {
      recordPreviewLineIndex = normalizedPreviewIndex(preview);
    }
    renderStatus();
    renderMiddleButton();
    showToast('기록 ' + (recordPreviewLineIndex + 1) + '/' + preview.lines.length, 'success');
  }

  function normalizedMode() {
    if (!transportConnected) return 'offline';
    if (activeCandidate()) return 'candidate_approval';
    if (glassState.is_recording) return 'recording';
    return glassState.mode || 'standby';
  }

  function activeCandidate() {
    var candidate = glassState.active_hud_candidate;
    if (!candidate || candidate.status !== 'candidate') return null;
    return candidate;
  }

  function statusCopy(mode, message) {
    var selectedAlias = safePatientAlias(glassState.patient || (visitCandidate && visitCandidate.patient_alias) || '');
    var hasSession = Boolean(glassState.visit_session_id);
    var readiness = String(glassState.readiness || '').toLowerCase();
    var preview = recordPreview();
    var m = message || '';
    var hudCandidate = activeCandidate();
    if (mode === 'offline') {
      return {
        kicker: '연결',
        title: '연결 확인 필요',
        meta: '브리지 또는 휴대폰 연결을 확인하세요.',
        caption: '연결되면 현재 세션 상태가 자동으로 복원됩니다.',
      };
    }
    if (hudCandidate) {
      return {
        kicker: '후보',
        title: '기록 후보 확인',
        meta: hudCandidate.title || '기록 후보',
        caption: '승인하거나 폐기하세요.',
        preview: {
          lines: hudCandidate.lines && hudCandidate.lines.length
            ? hudCandidate.lines
            : [hudCandidate.title || '기록 후보', hudCandidate.body || '승인 대기'],
          lens_safe: hudCandidate.lens_safe !== false,
        },
      };
    }
    if (!hasSession && selectedAlias) {
      return {
        kicker: TARGET_CANDIDATE_ID ? '선택' : '확인',
        title: preview ? '기록 확인' : '환자 확인',
        meta: preview ? previewMeta(preview) : selectedAlias + ' 방문을 시작할까요?',
        caption: preview ? previewCaption(preview) : (TARGET_CANDIDATE_ID ? '선택된 오늘 방문입니다. 시작을 눌러 세션을 엽니다.' : '다른 환자면 다음 환자를 누르세요.'),
        preview: preview || null,
      };
    }
    if (!hasSession && !selectedAlias && (mode === 'standby' || mode === 'ready')) {
      return {
        kicker: '대기',
        title: '환자 대기',
        meta: m || '오늘 방문 환자를 불러오는 중입니다.',
        caption: '환자가 보이면 시작으로 세션을 확인합니다.',
      };
    }
    if (mode === 'recording') {
      return { kicker: '영상', title: '영상 기록 중', meta: '글래스 영상을 휴대폰에 기록 중입니다.', caption: '중지를 누르면 저장과 업로드를 시작합니다.' };
    }
    if (preview && recordPreviewOpen && hasSession) {
      return {
        kicker: mode === 'recording' ? '영상 · 기록' : '기록',
        title: '기록 확인',
        meta: previewMeta(preview),
        caption: previewCaption(preview),
        preview: preview,
      };
    }
    if (mode === 'pre_review') {
      return {
        kicker: '캡처',
        title: '기록 대기',
        meta: '영상 시작으로 평가와 중재를 캡처하세요.',
        caption: preview ? '이전 기록은 기록 확인 탭에서 봅니다.' : '필요한 장면에서 영상을 시작하세요.',
      };
    }
    if (mode === 'assessment') {
      return { kicker: '세션', title: '세션 진행 중', meta: m || '필요한 장면에서 영상을 시작하세요.', caption: '기록 분류는 휴대폰과 서버에서 정리합니다.' };
    }
    if (mode === 'intervention') {
      return { kicker: '세션', title: '세션 진행 중', meta: m || '필요한 장면에서 영상을 시작하세요.', caption: 'HUD는 세션 제어만 담당합니다.' };
    }
    if (mode === 'home_program') {
      return { kicker: '세션', title: '세션 진행 중', meta: m || '필요한 장면에서 영상을 시작하세요.', caption: '과제 내용은 진행 노트 초안에서 확인합니다.' };
    }
    if (mode === 'summary' && readiness === 'sync_pending') {
      return { kicker: '대기열', title: '전송 대기', meta: m || '진행 노트 초안이 준비되었습니다.', caption: '휴대폰에서 검토하고 서명하세요.' };
    }
    if (mode === 'summary') {
      return { kicker: '확인', title: '종료 확인', meta: m || '세션을 종료하려면 한 번 더 누르세요.', caption: '종료 후 진행 노트 초안을 준비합니다.' };
    }
    if (mode === 'uploading') {
      return { kicker: '전송', title: '업로드 중', meta: m || '브리지로 전송 중입니다.', caption: '완료될 때까지 연결을 유지하세요.' };
    }
    if (mode === 'analyzing') {
      return { kicker: '분석', title: '분석 중', meta: m || '요약과 차트 초안을 준비 중입니다.', caption: '검토 전까지 임시 초안입니다.' };
    }
    if (mode === 'success') {
      return { kicker: '완료', title: '저장 완료', meta: m || '기록 생성이 완료되었습니다.', caption: '필요하면 휴대폰에서 검토하세요.' };
    }
    if (mode === 'error') {
      return { kicker: '오류', title: '확인 필요', meta: '휴대폰 앱 상태를 확인하세요.', caption: '상세 오류는 휴대폰 로그에만 표시됩니다.' };
    }
    if (mode === 'ready') {
      return {
        kicker: '준비',
        title: '환자 대기',
        meta: m || '환자를 먼저 선택하세요.',
        caption: '렌즈에는 환자 별칭만 표시합니다.',
      };
    }
    if (hasSession) {
      return { kicker: '세션', title: '세션 진행 중', meta: m || '필요한 장면에서 영상을 시작하세요.', caption: '종료는 세션을 마칠 때만 누르세요.' };
    }
    return { kicker: '대기', title: '화면 준비', meta: m || '라이브 연결을 기다리는 중입니다.', caption: '휴대폰 앱과 브리지가 연결되면 시작할 수 있습니다.' };
  }

  function recordPreview() {
    var candidatePreview = visitCandidate && visitCandidate.record_preview;
    if (candidatePreview && candidatePreview.lens_safe !== false) return candidatePreview;
    if (sessionRecordPreview && sessionRecordPreview.lens_safe !== false) return sessionRecordPreview;
    var insight = glassState.last_insight;
    if (insight && insight.source === 'moai_web.pre_review' && insight.lens_safe !== false) {
      return {
        cue: insight.body || insight.title || '',
        lines: insight.lines || [],
        signals: insight.signals || {},
        lens_safe: true,
      };
    }
    return null;
  }

  function previewCaption(preview) {
    var lines = preview && preview.lines;
    if (lines && lines.length) return '기록 ' + (normalizedPreviewIndex(preview) + 1) + '/' + lines.length;
    var signals = (preview && preview.signals) || {};
    var parts = [];
    if (signals.notes_count) parts.push('노트 ' + signals.notes_count);
    if (signals.observations_count) parts.push('평가 ' + signals.observations_count);
    if (signals.activity_sessions_count) parts.push('중재/과제 ' + signals.activity_sessions_count);
    return parts.length ? parts.join(' · ') : '요약만 렌즈에 표시합니다.';
  }

  function previewMeta(preview) {
    return (preview && preview.cue) || '이전 기록';
  }

  function activePreviewLine(preview) {
    var lines = preview && preview.lines;
    if (lines && lines.length) return lines[normalizedPreviewIndex(preview)];
    return (preview && preview.cue) || '기록 요약 없음';
  }

  function renderRecordList(preview) {
    var list = document.getElementById('record-list');
    if (!list) return;
    var panel = list.closest('.status-panel');
    var lines = preview && preview.lines;
    if (!lines || !lines.length) {
      list.classList.add('hidden');
      list.innerHTML = '';
      if (panel) panel.classList.remove('record-preview-mode');
      return;
    }
    list.classList.remove('hidden');
    if (panel) panel.classList.add('record-preview-mode');
    var active = normalizedPreviewIndex(preview);
    if (list.children.length !== 2) {
      list.innerHTML = '';
      var row = document.createElement('p');
      row.id = 'record-line-active';
      list.appendChild(row);
      var position = document.createElement('span');
      position.className = 'record-position';
      position.setAttribute('aria-hidden', 'true');
      list.appendChild(position);
    }
    list.children[0].textContent = lines[active] || '';
    list.children[1].textContent = (active + 1) + '/' + lines.length;
  }

  function normalizedPreviewIndex(preview) {
    var lines = preview && preview.lines;
    if (!lines || !lines.length) return 0;
    if (recordPreviewLineIndex >= lines.length) recordPreviewLineIndex = 0;
    return Math.max(0, recordPreviewLineIndex);
  }

  function renderInsight() {
    var card = document.getElementById('insight-card');
    var insight = glassState.last_insight;
    var mode = normalizedMode();
    var hideInModes = { recording: true, uploading: true, analyzing: true, offline: true };

    if (!insight || hideInModes[mode]) {
      card.classList.add('hidden');
      setInsightLayoutVisible(false);
      return;
    }

    var insightId = insight.id || (String(insight.title || '') + ':' + String(insight.body || ''));
    if (insightId === lastInsightId) {
      setInsightLayoutVisible(!card.classList.contains('hidden'));
      return;
    }
    lastInsightId = insightId;
    showInsightCard(insight.title || '확인 필요', insight.body || '');
  }

  function showInsightCard(title, body) {
    clearTimeout(insightDismissTimer);
    clearInterval(insightProgressTimer);

    setText('insight-title', title);
    setText('insight-body', body);

    var card = document.getElementById('insight-card');
    var progress = document.getElementById('insight-progress');
    card.classList.remove('hidden');
    setInsightLayoutVisible(true);
    insightStartTime = Date.now();
    progress.style.transform = 'scaleY(1)';

    if (glassState.last_insight && glassState.last_insight.source === 'moai_web.pre_review') {
      progress.style.transform = 'scaleY(0)';
      return;
    }

    insightProgressTimer = setInterval(function () {
      var elapsed = Date.now() - insightStartTime;
      var remaining = Math.max(0, 1 - elapsed / INSIGHT_DURATION_MS);
      progress.style.transform = 'scaleY(' + remaining + ')';
    }, 250);

    insightDismissTimer = setTimeout(function () {
      clearInterval(insightProgressTimer);
      card.classList.add('hidden');
      setInsightLayoutVisible(false);
    }, INSIGHT_DURATION_MS);
  }

  function setInsightLayoutVisible(visible) {
    var home = document.getElementById('home');
    if (home) home.classList.toggle('has-insight', Boolean(visible));
  }

  function renderToggleButton() {
    var btn = document.getElementById('toggle-btn');
    var label = document.getElementById('toggle-label');
    var icon = document.getElementById('toggle-icon');
    if (!btn || !label) return;
    btn.classList.remove('hidden', 'disabled-look', 'stop');
    btn.classList.add('primary');
    btn.disabled = false;
    if (!transportConnected) {
      btn.removeAttribute('data-action');
      btn.disabled = true;
      btn.classList.add('hidden', 'disabled-look');
      label.textContent = '연결 대기';
      if (icon) icon.textContent = '·';
      return;
    }
    if (commandPending) {
      btn.removeAttribute('data-action');
      btn.disabled = true;
      btn.classList.add('disabled-look');
      label.textContent = '처리 중';
      if (icon) icon.textContent = '·';
      return;
    }
    if (activeCandidate()) {
      btn.dataset.action = 'approve_candidate';
      label.textContent = '후보 승인';
      if (icon) icon.textContent = '✓';
      return;
    }
    if (!glassState.visit_session_id) {
      label.textContent = visitCandidate || TARGET_CANDIDATE_ID ? '방문 시작' : '시작 대기';
      btn.dataset.action = 'start_visit';
      if (icon) icon.textContent = '●';
      return;
    }
    btn.dataset.action = 'toggle_recording';
    if (glassState.is_recording || glassState.mode === 'recording') {
      label.textContent = '영상 중지';
      btn.classList.remove('primary');
      btn.classList.add('stop');
      if (icon) icon.textContent = '■';
    } else {
      label.textContent = '영상 시작';
      if (icon) icon.textContent = '●';
    }
  }

  function renderMiddleButton() {
    var btn = document.getElementById('next-btn');
    var label = document.getElementById('next-label');
    var icon = document.getElementById('next-icon');
    if (!btn || !label) return;
    btn.disabled = false;
    btn.classList.remove('disabled-look');
    if (!transportConnected || commandPending) {
      btn.removeAttribute('data-action');
      btn.classList.add('hidden');
      return;
    }
    if (activeCandidate()) {
      btn.dataset.action = 'primary_action';
      label.textContent = '상태';
      if (icon) icon.textContent = 'i';
      btn.classList.remove('hidden');
      return;
    }
    if (!glassState.visit_session_id) {
      btn.dataset.action = 'next_phase';
      label.textContent = TARGET_CANDIDATE_ID ? '환자 고정' : '다른 환자';
      if (icon) icon.textContent = '›';
      btn.classList.remove('hidden');
      return;
    }
    if (glassState.visit_session_id && recordPreview()) {
      btn.dataset.action = 'cycle_record_preview';
      label.textContent = recordPreviewOpen ? '다음 기록' : '기록 보기';
      if (icon) icon.textContent = '›';
      btn.classList.remove('hidden');
      return;
    }
    btn.removeAttribute('data-action');
    btn.classList.add('hidden');
  }

  function renderEndButton() {
    var label = document.getElementById('end-label');
    var btn = document.getElementById('end-btn');
    var icon = document.getElementById('end-icon');
    if (!label || !btn) return;
    btn.disabled = false;
    btn.classList.remove('hidden', 'disabled-look');
    var mode = normalizedMode();
    var readiness = String(glassState.readiness || '').toLowerCase();
    var hasSession = Boolean(glassState.visit_session_id);
    if (!transportConnected) {
      label.textContent = 'HUD 닫기';
      btn.dataset.action = 'close_hud';
      if (icon) icon.textContent = '×';
      return;
    }
    if (commandPending) {
      btn.removeAttribute('data-action');
      btn.disabled = true;
      btn.classList.add('disabled-look');
      label.textContent = '처리 중';
      if (icon) icon.textContent = '·';
      return;
    }
    if (activeCandidate()) {
      label.textContent = '후보 폐기';
      btn.dataset.action = 'discard_candidate';
      if (icon) icon.textContent = '×';
      return;
    }
    if (!hasSession) {
      label.textContent = '닫기';
      btn.dataset.action = 'close_hud';
      if (icon) icon.textContent = '×';
      return;
    }
    if (readiness === 'sync_pending') {
      label.textContent = '완료';
      btn.dataset.action = 'complete_visit_hud';
      if (icon) icon.textContent = '✓';
      return;
    }
    btn.dataset.action = 'end_visit_session';
    label.textContent = mode === 'summary' ? '종료 확정' : '세션 종료';
    if (icon) icon.textContent = mode === 'summary' ? '✓' : '×';
  }

  function setCommandPending(pending) {
    var wasPending = commandPending;
    commandPending = Boolean(pending);
    renderToggleButton();
    renderMiddleButton();
    renderEndButton();
    updateCommandRailLayout();
    if (wasPending && !commandPending) {
      var buttons = commandButtons();
      if (buttons.length) buttons[0].focus();
    }
  }

  function updateCommandRailLayout() {
    var rail = document.getElementById('command-rail');
    if (!rail) return;
    var count = commandButtons().length;
    rail.dataset.count = String(Math.max(1, count));
    var focused = document.activeElement;
    if (focused && (focused.disabled || focused.classList.contains('hidden'))) {
      focusCommand(0);
    }
  }

  function safePatientAlias(name) {
    var clean = String(name || '').trim();
    if (!clean) return '';
    if (/^[A-Za-z][A-Za-z\s.'-]{1,}$/.test(clean)) {
      return clean.split(/\s+/).map(function (part, index) {
        return index === 0 ? part.charAt(0).toUpperCase() + '.' : part.charAt(0).toUpperCase();
      }).join(' ');
    }
    if (clean.length <= 2) return clean.charAt(0) + '*';
    return clean.charAt(0) + '*' + clean.charAt(clean.length - 1);
  }

  function elapsedString(isoStart) {
    if (!isoStart) return '00:00';
    var start = new Date(isoStart).getTime();
    var secs = Math.max(0, Math.floor((Date.now() - start) / 1000));
    var mm = Math.floor(secs / 60);
    var ss = secs % 60;
    return (mm < 10 ? '0' : '') + mm + ':' + (ss < 10 ? '0' : '') + ss;
  }

  function statusToastLabel() {
    var mode = normalizedMode();
    if (mode === 'offline') return '연결 확인 필요';
    if (mode === 'recording') return '영상 기록 중 ' + elapsedString(glassState.recording_start);
    if (mode === 'uploading') return '업로드 중';
    if (mode === 'analyzing') return '분석 중';
    if (mode === 'success') return '저장 완료';
    if (mode === 'error' || glassState.error_state) return '확인 필요';
    if (glassState.visit_session_id) return '세션 진행 중';
    if (visitCandidate || glassState.patient) return '환자 확인 대기';
    return '환자 대기 중';
  }

  function setConnected(ok) {
    var dot = document.getElementById('conn-dot');
    var label = document.getElementById('conn-label');
    var wasConnected = transportConnected;
    transportConnected = Boolean(ok);
    dot.className = 'conn-dot ' + (ok ? 'connected' : 'error');
    label.textContent = ok ? '연결됨' : '오프라인';
    render();
    if (ok !== wasConnected) {
      var buttons = commandButtons();
      if (buttons.length) buttons[0].focus();
    }
  }

  function showToast(msg, type) {
    var toast = document.getElementById('toast');
    toast.textContent = lensSafeMessage(msg);
    toast.className = 'toast' + (type ? ' ' + type : '');
    toast.offsetHeight;
    toast.classList.add('visible');
    window.setTimeout(function () { toast.classList.remove('visible'); }, 2500);
  }

  function commandLabel(command) {
    var labels = {
      toggle_recording: glassState.is_recording ? '영상 중지' : '영상 시작',
      start_visit: '방문 시작',
      approve_candidate: '후보 승인',
      discard_candidate: '후보 폐기',
      select_patient: '환자 선택',
      next_phase: TARGET_CANDIDATE_ID ? '환자 고정' : '다른 환자',
      cycle_record_preview: recordPreviewOpen ? '다음 기록' : '기록 보기',
      end_visit_session: '세션 종료',
      complete_visit_hud: '완료',
      primary_action: '상태',
      close_hud: '닫기',
    };
    return labels[command] || command;
  }

  function commandResultLabel(command, result) {
    if (result && result.ok === false) return lensSafeMessage(result.message || '명령 실패');
    var labels = {
      toggle_recording: (glassState.is_recording || glassState.mode === 'recording') ? '영상 시작됨' : '영상 중지됨',
      start_visit: '세션 시작됨',
      approve_candidate: '후보 승인됨',
      discard_candidate: '후보 폐기됨',
      next_phase: TARGET_CANDIDATE_ID ? '환자 고정됨' : '다른 환자',
      end_visit_session: result && result.session && result.session.status === 'ended' ? '세션 종료됨' : '종료 확인 필요',
    };
    return labels[command] || commandLabel(command) + ' 완료';
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function lensSafeMessage(message) {
    var clean = String(message || '')
      .replace(/https?:\/\/\S+/gi, '')
      .replace(/Failed to fetch|state fetch failed|HTTP\s+\d+/gi, '연결 확인 필요')
      .replace(/\s+/g, ' ')
      .trim();
    if (!clean) return '상태 확인 필요';
    return clean.length > 54 ? clean.slice(0, 53) + '…' : clean;
  }

  function focusables() {
    return Array.prototype.slice.call(document.querySelectorAll('.focusable')).filter(function (el) {
      return !el.classList.contains('hidden') && el.offsetParent !== null;
    });
  }

  function commandButtons() {
    return Array.prototype.slice.call(document.querySelectorAll('.command-button.focusable')).filter(function (el) {
      return !el.disabled && !el.classList.contains('hidden') && el.offsetParent !== null;
    });
  }

  function focusCommand(delta) {
    var items = commandButtons();
    if (!items.length) return;
    var currentIndex = items.indexOf(document.activeElement);
    var nextIndex = currentIndex < 0 ? 0 : (currentIndex + delta + items.length) % items.length;
    items[nextIndex].focus();
  }

  function focusRecordCard() {
    var card = document.getElementById('status-card');
    if (card && statusCardInteractive()) card.focus();
  }

  function statusCardInteractive() {
    var preview = recordPreview();
    return transportConnected && Boolean(activeCandidate() || (preview && preview.lines && preview.lines.length));
  }

  function scrollRecordCard(delta) {
    var preview = recordPreview();
    if (!preview || !preview.lines || !preview.lines.length) {
      showToast('표시할 기록 없음', 'error');
      return;
    }
    recordPreviewOpen = true;
    recordPreviewLineIndex = (normalizedPreviewIndex(preview) + delta + preview.lines.length) % preview.lines.length;
    renderStatus();
    showToast('기록 ' + (recordPreviewLineIndex + 1) + '/' + preview.lines.length, 'success');
  }

  function selectFocused() {
    var focused = document.activeElement;
    if (!focused || !focused.classList.contains('focusable')) {
      focusCommand(0);
      focused = document.activeElement;
    }
    if (focused && focused.classList.contains('focusable')) focused.click();
  }

  function handleNavigationCommand(command) {
    if (command === 'nav_right') {
      if (document.activeElement && document.activeElement.id === 'status-card') scrollRecordCard(1);
      else focusCommand(1);
      return true;
    }
    if (command === 'nav_left') {
      if (document.activeElement && document.activeElement.id === 'status-card') scrollRecordCard(-1);
      else focusCommand(-1);
      return true;
    }
    if (command === 'nav_up') {
      if (document.activeElement && document.activeElement.classList.contains('command-button')) {
        focusRecordCard();
      } else {
        focusCommand(0);
      }
      return true;
    }
    if (command === 'nav_down') {
      focusCommand(0);
      return true;
    }
    if (command === 'select_focused') {
      selectFocused();
      return true;
    }
    return false;
  }

  function moveFocus(delta) {
    var items = focusables();
    if (!items.length) return;
    var currentIndex = items.indexOf(document.activeElement);
    var nextIndex = currentIndex < 0 ? 0 : (currentIndex + delta + items.length) % items.length;
    items[nextIndex].focus();
    items[nextIndex].scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function setupEvents() {
    document.addEventListener('click', function (e) {
      var el = e.target.closest('[data-action]');
      if (!el) return;
      sendCommand(el.dataset.action);
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') {
        if (document.activeElement && document.activeElement.id === 'status-card') scrollRecordCard(1);
        else focusCommand(1);
        e.preventDefault();
        return;
      }
      if (e.key === 'ArrowLeft') {
        if (document.activeElement && document.activeElement.id === 'status-card') scrollRecordCard(-1);
        else focusCommand(-1);
        e.preventDefault();
        return;
      }
      if (e.key === 'ArrowUp') {
        if (document.activeElement && document.activeElement.classList.contains('command-button')) {
          focusRecordCard();
        } else {
          focusCommand(0);
        }
        e.preventDefault();
        return;
      }
      if (e.key === 'ArrowDown') {
        focusCommand(0);
        e.preventDefault();
        return;
      }
      if (e.key === 'Enter' || e.key === ' ') {
        selectFocused();
        e.preventDefault();
        return;
      }
      if (e.key === 'Escape') {
        sendCommand(glassState.visit_session_id ? 'primary_action' : 'close_hud');
        e.preventDefault();
      }
    });

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        clearInterval(statePollTimer);
        clearInterval(timerPollTimer);
        clearInterval(commandPollTimer);
      } else {
        startTimers();
        pollStateQuiet();
        pollPendingCommandQuiet();
      }
    });
  }

  function timerTick() {
    if (glassState.is_recording || glassState.mode === 'recording') {
      var timer = document.getElementById('rec-timer');
      if (timer) timer.textContent = elapsedString(glassState.recording_start);
    }
  }

  function startTimers() {
    clearInterval(statePollTimer);
    clearInterval(timerPollTimer);
    clearInterval(commandPollTimer);
    statePollTimer = setInterval(pollStateQuiet, POLL_MS);
    timerPollTimer = setInterval(timerTick, 1000);
    commandPollTimer = setInterval(pollPendingCommandQuiet, POLL_MS);
  }

  function init() {
    clearAuthParamsFromAddressBar();
    setupEvents();
    render();
    focusCommand(0);
    pollStateQuiet();
    loadNextVisitCandidate(0);
    startTimers();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
