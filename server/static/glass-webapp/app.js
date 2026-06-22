(function () {
  'use strict';

  var params = new URLSearchParams(window.location.search);
  var API_KEY = params.get('api_key') || '';
  var HUD_TOKEN = params.get('hud_token') || '';
  var TARGET_CANDIDATE_ID = params.get('candidate_id') || params.get('encounter_id') || '';
  var BRIDGE_BASE_URL = normalizeBaseUrl(params.get('bridge_url')) || window.location.origin;
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
  var visitCandidate = null;
  var visitCandidateOffset = 0;
  var lastCandidateLoadAt = 0;

  function apiHeaders() {
    var h = { 'Content-Type': 'application/json' };
    if (API_KEY) h['x-api-key'] = API_KEY;
    if (HUD_TOKEN) h['x-hud-token'] = HUD_TOKEN;
    return h;
  }

  function apiUrl(path) {
    var joiner = path.indexOf('?') === -1 ? '?' : '&';
    var params = [];
    if (API_KEY) params.push('api_key=' + encodeURIComponent(API_KEY));
    if (HUD_TOKEN) params.push('hud_token=' + encodeURIComponent(HUD_TOKEN));
    var qp = params.length ? joiner + params.join('&') : '';
    return BRIDGE_BASE_URL + path + qp;
  }

  function normalizeBaseUrl(raw) {
    var value = String(raw || '').trim();
    if (!value) return '';
    try {
      var url = new URL(value);
      if (url.protocol !== 'https:' && url.protocol !== 'http:') return '';
      return url.origin;
    } catch (e) {
      return '';
    }
  }

  function pollState() {
    fetch(apiUrl('/glass/state'), { headers: apiHeaders(), cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        setConnected(true);
        glassState = data;
        render();
        maybeLoadVisitCandidate();
      })
      .catch(function () {
        setConnected(false);
      });
  }

  function sendCommand(command) {
    if (command === 'start_visit') {
      startVisit();
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
      .catch(function (e) {
        showToast('명령 실패: ' + e.message, 'error');
      });
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
        lastCandidateLoadAt = Date.now();
        render();
      })
      .catch(function () {
        visitCandidate = null;
        lastCandidateLoadAt = Date.now();
        render();
      });
  }

  function maybeLoadVisitCandidate() {
    if (glassState.visit_session_id) return;
    if (Date.now() - lastCandidateLoadAt < 10000) return;
    loadNextVisitCandidate(visitCandidateOffset);
  }

  function startVisit() {
    var body = {
      candidate_id: visitCandidate ? visitCandidate.id : (TARGET_CANDIDATE_ID || null),
      update_glass: true,
    };
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
          visitCandidate = null;
          render();
        }
        showToast('세션 시작됨', 'success');
      })
      .catch(function (e) {
        showToast('시작 실패: ' + e.message, 'error');
      });
  }

  function render() {
    renderPatient();
    renderReadiness();
    renderStatus();
    renderInsight();
    renderToggleButton();
    renderNextButton();
    renderEndButton();
  }

  function renderPatient() {
    var candidateAlias = visitCandidate && visitCandidate.patient_alias;
    var alias = safePatientAlias(glassState.patient || (!glassState.visit_session_id ? candidateAlias : ''));
    var count = glassState.session_count || 0;
    if (glassState.visit_session_id) {
      setText('patient-context-label', 'ACTIVE SESSION');
    } else if (alias) {
      setText('patient-context-label', TARGET_CANDIDATE_ID ? 'SELECTED PATIENT' : 'NEXT PATIENT');
    } else {
      setText('patient-context-label', 'PATIENT ALIAS');
    }
    setText('patient-name', alias || '미선택');
    setText('session-label', 'E' + count);
  }

  function renderReadiness() {
    var label = document.getElementById('readiness-label');
    if (!label) return;
    var hasError = Boolean(glassState.error_state) || glassState.mode === 'error';
    var ready = String(glassState.readiness || '').toLowerCase() || (hasError ? 'error' : 'ready');
    label.classList.toggle('ready', !hasError && ready === 'ready');
    label.classList.toggle('error', hasError || ready === 'error');
    label.textContent = hasError ? '오류' : (ready === 'ready' ? '준비' : '확인');
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
  }

  function normalizedMode() {
    if (glassState.is_recording) return 'recording';
    return glassState.mode || 'standby';
  }

  function statusCopy(mode, message) {
    var selectedAlias = safePatientAlias(glassState.patient || (visitCandidate && visitCandidate.patient_alias) || '');
    var hasSession = Boolean(glassState.visit_session_id);
    var m = message || '';
    if (!hasSession && selectedAlias) {
      return {
        kicker: TARGET_CANDIDATE_ID ? 'SELECTED' : 'CONFIRM',
        title: '환자 확인',
        meta: selectedAlias + ' 방문을 시작할까요?',
        caption: TARGET_CANDIDATE_ID ? '선택된 오늘 방문입니다. 시작을 눌러 세션을 엽니다.' : '다른 환자면 다음 환자를 누르세요.',
      };
    }
    if (!hasSession && !selectedAlias && (mode === 'standby' || mode === 'ready')) {
      return {
        kicker: 'WAIT',
        title: '환자 대기',
        meta: m || '오늘 방문 환자를 불러오는 중입니다.',
        caption: '환자가 보이면 시작으로 세션을 확인합니다.',
      };
    }
    if (mode === 'pre_review') {
      return { kicker: 'SESSION', title: '세션 준비', meta: m || '녹화를 시작할 수 있습니다.', caption: '상세 기록은 physio_app encounter에서 검토합니다.' };
    }
    if (mode === 'assessment') {
      return { kicker: 'SESSION', title: '세션 진행 중', meta: m || '필요한 장면에서 녹화를 시작하세요.', caption: '기록 분류는 서버와 physio_app에서 정리합니다.' };
    }
    if (mode === 'intervention') {
      return { kicker: 'SESSION', title: '세션 진행 중', meta: m || '필요한 장면에서 녹화를 시작하세요.', caption: 'HUD는 세션 제어만 담당합니다.' };
    }
    if (mode === 'home_program') {
      return { kicker: 'SESSION', title: '세션 진행 중', meta: m || '필요한 장면에서 녹화를 시작하세요.', caption: '과제 내용은 encounter 초안에서 확인합니다.' };
    }
    if (mode === 'summary') {
      return { kicker: 'DONE', title: '초안 준비', meta: m || '진행 노트 초안이 준비되었습니다.', caption: 'physio_app에서 검토하고 서명하세요.' };
    }
    if (mode === 'recording') {
      return { kicker: 'REC', title: '녹화 중', meta: m || '세션 캡처를 저장 중입니다.', caption: '내용 분류와 노트 초안은 서버에서 처리합니다.' };
    }
    if (mode === 'uploading') {
      return { kicker: 'SEND', title: '업로드', meta: m || '브리지로 전송 중입니다.', caption: '렌즈에는 업로드 상태만 표시합니다.' };
    }
    if (mode === 'analyzing') {
      return { kicker: 'AI', title: '분석 중', meta: m || '요약과 차트 초안을 준비 중입니다.', caption: '결과는 clinician review 전까지 초안입니다.' };
    }
    if (mode === 'success') {
      return { kicker: 'DONE', title: '저장 완료', meta: m || '기록 생성이 완료되었습니다.', caption: '필요하면 기록 화면에서 검토하세요.' };
    }
    if (mode === 'error') {
      return { kicker: 'ISSUE', title: '확인 필요', meta: m || '다시 시도하거나 앱 상태를 확인하세요.', caption: '오류 세부 정보는 iPhone/bridge에서 확인합니다.' };
    }
    if (mode === 'ready') {
      return {
        kicker: 'READY',
        title: '환자 대기',
        meta: m || '환자를 먼저 선택하세요.',
        caption: '렌즈에는 alias만 표시합니다.',
      };
    }
    if (hasSession) {
      return { kicker: 'SESSION', title: '세션 진행 중', meta: m || '필요한 장면에서 녹화를 시작하세요.', caption: '종료는 세션을 마칠 때만 누르세요.' };
    }
    return { kicker: 'STANDBY', title: '화면 준비', meta: m || '라이브 연결을 기다리는 중입니다.', caption: 'iPhone 앱 또는 브리지 연결 후 시작하세요.' };
  }

  function renderInsight() {
    var card = document.getElementById('insight-card');
    var insight = glassState.last_insight;
    var mode = normalizedMode();
    var hideInModes = { recording: true, uploading: true, analyzing: true };

    if (!insight || hideInModes[mode]) {
      card.classList.add('hidden');
      return;
    }

    var insightId = insight.id || (String(insight.title || '') + ':' + String(insight.body || ''));
    if (insightId === lastInsightId) return;
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
    insightStartTime = Date.now();
    progress.style.transform = 'scaleY(1)';

    insightProgressTimer = setInterval(function () {
      var elapsed = Date.now() - insightStartTime;
      var remaining = Math.max(0, 1 - elapsed / INSIGHT_DURATION_MS);
      progress.style.transform = 'scaleY(' + remaining + ')';
    }, 250);

    insightDismissTimer = setTimeout(function () {
      clearInterval(insightProgressTimer);
      card.classList.add('hidden');
    }, INSIGHT_DURATION_MS);
  }

  function renderToggleButton() {
    var btn = document.getElementById('toggle-btn');
    var label = document.getElementById('toggle-label');
    if (!btn || !label) return;
    if (!glassState.visit_session_id) {
      label.textContent = visitCandidate || TARGET_CANDIDATE_ID ? '시작 확인' : '시작 대기';
      btn.dataset.action = 'start_visit';
      btn.classList.remove('stop');
      btn.classList.add('primary');
      return;
    }
    btn.dataset.action = 'toggle_recording';
    if (glassState.is_recording || glassState.mode === 'recording') {
      label.textContent = '녹화 중지';
      btn.classList.remove('primary');
      btn.classList.add('stop');
    } else {
      label.textContent = '녹화 시작';
      btn.classList.remove('stop');
      btn.classList.add('primary');
    }
  }

  function renderNextButton() {
    var btn = document.getElementById('next-btn');
    var label = document.getElementById('next-label');
    if (!btn || !label) return;
    if (!glassState.visit_session_id) {
      btn.dataset.action = 'next_phase';
      label.textContent = TARGET_CANDIDATE_ID ? '환자 고정' : '다른 환자';
      return;
    }
    btn.dataset.action = 'primary_action';
    label.textContent = '상태 확인';
  }

  function renderEndButton() {
    var label = document.getElementById('end-label');
    var btn = document.getElementById('end-btn');
    if (!label || !btn) return;
    label.textContent = glassState.visit_session_id ? '세션 종료' : '닫기';
    btn.classList.toggle('disabled-look', !glassState.visit_session_id);
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

  function setConnected(ok) {
    var dot = document.getElementById('conn-dot');
    var label = document.getElementById('conn-label');
    dot.className = 'conn-dot ' + (ok ? 'connected' : 'error');
    label.textContent = ok ? '연결됨' : '오류';
  }

  function showToast(msg, type) {
    var toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = 'toast' + (type ? ' ' + type : '');
    toast.offsetHeight;
    toast.classList.add('visible');
    window.setTimeout(function () { toast.classList.remove('visible'); }, 2500);
  }

  function commandLabel(command) {
    var labels = {
      toggle_recording: glassState.is_recording ? '녹화 중지' : '녹화 시작',
      start_visit: '시작 확인',
      select_patient: '환자 선택',
      next_phase: TARGET_CANDIDATE_ID ? '환자 고정' : '다른 환자',
      end_visit_session: '세션 종료',
      primary_action: '상태 확인',
    };
    return labels[command] || command;
  }

  function commandResultLabel(command, result) {
    if (result && result.ok === false) return result.message || '명령 실패';
    var labels = {
      toggle_recording: (glassState.is_recording || glassState.mode === 'recording') ? '녹화 시작됨' : '녹화 중지됨',
      start_visit: '세션 시작됨',
      next_phase: TARGET_CANDIDATE_ID ? '환자 고정됨' : '다른 환자',
      end_visit_session: '세션 종료됨',
    };
    return labels[command] || commandLabel(command) + ' 완료';
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function focusables() {
    return Array.prototype.slice.call(document.querySelectorAll('.focusable'));
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
      if (['ArrowRight', 'ArrowDown'].indexOf(e.key) !== -1) {
        moveFocus(1);
        e.preventDefault();
        return;
      }
      if (['ArrowLeft', 'ArrowUp'].indexOf(e.key) !== -1) {
        moveFocus(-1);
        e.preventDefault();
        return;
      }
      if (e.key === 'Enter' || e.key === ' ') {
        var focused = document.activeElement;
        if (focused && focused.classList.contains('focusable')) focused.click();
        e.preventDefault();
        return;
      }
      if (e.key === 'Escape') {
        sendCommand('primary_action');
        e.preventDefault();
      }
    });

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        clearInterval(statePollTimer);
        clearInterval(timerPollTimer);
      } else {
        startTimers();
        pollState();
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
    statePollTimer = setInterval(pollState, POLL_MS);
    timerPollTimer = setInterval(timerTick, 1000);
  }

  function init() {
    setupEvents();
    var btn = document.getElementById('toggle-btn');
    if (btn) btn.focus();
    pollState();
    loadNextVisitCandidate(0);
    startTimers();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
