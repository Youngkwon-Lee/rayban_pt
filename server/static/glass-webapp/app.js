(function () {
  'use strict';

  var params = new URLSearchParams(window.location.search);
  var API_KEY = params.get('api_key') || '';
  var BRIDGE_BASE_URL = normalizeBaseUrl(params.get('bridge_url')) || window.location.origin;
  var POLL_MS = 2000;
  var INSIGHT_DURATION_MS = 8000;
  var VISIT_PHASES = ['pre_review', 'assessment', 'intervention', 'home_program', 'summary'];

  var glassState = {
    patient: null,
    mode: 'standby',
    message: '라이브 연결을 기다리는 중',
    is_recording: false,
    recording_start: null,
    session_count: 0,
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

  function apiHeaders() {
    var h = { 'Content-Type': 'application/json' };
    if (API_KEY) h['x-api-key'] = API_KEY;
    return h;
  }

  function apiUrl(path) {
    var qp = API_KEY ? '?api_key=' + encodeURIComponent(API_KEY) : '';
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
      })
      .catch(function () {
        setConnected(false);
      });
  }

  function sendCommand(command) {
    fetch(apiUrl('/glass/command'), {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ command: command }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function () {
        showToast(commandLabel(command) + ' 요청됨', 'success');
      })
      .catch(function (e) {
        showToast('명령 실패: ' + e.message, 'error');
      });
  }

  function render() {
    renderPatient();
    renderReadiness();
    renderPhase();
    renderStatus();
    renderInsight();
    renderToggleButton();
  }

  function renderPatient() {
    var alias = safePatientAlias(glassState.patient);
    var count = glassState.session_count || 0;
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

  function renderPhase() {
    var mode = normalizedMode();
    var phase = phaseFromMode(mode);
    var activeIndex = VISIT_PHASES.indexOf(phase);
    var chips = document.querySelectorAll('.phase-chip');
    Array.prototype.forEach.call(chips, function (chip) {
      var chipIndex = VISIT_PHASES.indexOf(chip.dataset.phase);
      chip.classList.toggle('active', chip.dataset.phase === phase);
      chip.classList.toggle('done', activeIndex > 0 && chipIndex >= 0 && chipIndex < activeIndex);
    });
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

  function phaseFromMode(mode) {
    if (VISIT_PHASES.indexOf(mode) !== -1) return mode;
    if (mode === 'ready' || mode === 'standby') return 'pre_review';
    if (mode === 'recording') return phaseFromMessage(glassState.message) || 'assessment';
    if (mode === 'success') return 'summary';
    return 'pre_review';
  }

  function phaseFromMessage(message) {
    var text = String(message || '').toLowerCase();
    if (text.indexOf('home') !== -1 || text.indexOf('과제') !== -1) return 'home_program';
    if (text.indexOf('중재') !== -1 || text.indexOf('intervention') !== -1) return 'intervention';
    if (text.indexOf('평가') !== -1 || text.indexOf('assessment') !== -1) return 'assessment';
    return '';
  }

  function statusCopy(mode, message) {
    var patientReady = Boolean(glassState.patient);
    var m = message || '';
    if (mode === 'pre_review') {
      return { kicker: 'REVIEW', title: '기록 확인', meta: m || '이전 기록과 금기 사항을 확인하세요.', caption: '환자 식별 정보는 최소 alias만 표시합니다.' };
    }
    if (mode === 'assessment') {
      return { kicker: 'ASSESS', title: '평가', meta: m || '관찰, 신체검사, 기능평가를 진행하세요.', caption: '결과는 자동 라벨 후보로 저장됩니다.' };
    }
    if (mode === 'intervention') {
      return { kicker: 'TREAT', title: '중재', meta: m || '수행한 중재와 반응을 짧게 캡처하세요.', caption: '세부 기록은 서버에서 초안으로 정리합니다.' };
    }
    if (mode === 'home_program') {
      return { kicker: 'HOME', title: '과제', meta: m || '가정 과제와 보호자 cue를 확인하세요.', caption: '렌즈에는 짧은 cue만 표시합니다.' };
    }
    if (mode === 'summary') {
      return { kicker: 'NOTE', title: '노트 초안', meta: m || '진행 노트 초안 검토가 필요합니다.', caption: '전송 전 iPhone/physio 앱에서 승인하세요.' };
    }
    if (mode === 'recording') {
      return { kicker: 'REC', title: '녹화 중', meta: m || '세션 캡처를 저장 중입니다.', caption: '민감 정보는 앱과 서버에서만 처리합니다.' };
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
        title: '녹화 준비',
        meta: m || (patientReady ? '선택 환자 세션을 시작할 수 있습니다.' : '환자를 먼저 선택하세요.'),
        caption: patientReady ? 'Neural Band Enter로 시작합니다.' : '환자명은 렌즈에 축약 표시됩니다.',
      };
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
      select_patient: '환자 선택',
      show_recommendations: '큐 표시',
      open_capture_history: '기록 열기',
      primary_action: '기본 동작',
    };
    return labels[command] || command;
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
    startTimers();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
