(function () {
  'use strict';

  // ── Config ────────────────────────────────────────────────────────────────
  var params = new URLSearchParams(window.location.search);
  var API_KEY = params.get('api_key') || '';
  var POLL_MS = 2000;

  // ── State ─────────────────────────────────────────────────────────────────
  var glassState = {
    patient: null,
    is_recording: false,
    recording_start: null,
    session_count: 0,
    last_insight: null,
    updated_at: null,
  };
  var lastInsightId = null;
  var insightDismissTimer = null;
  var insightProgressTimer = null;
  var insightStartTime = null;
  var INSIGHT_DURATION_MS = 8000;

  // ── API helpers ───────────────────────────────────────────────────────────
  function apiHeaders() {
    var h = { 'Content-Type': 'application/json' };
    if (API_KEY) h['x-api-key'] = API_KEY;
    return h;
  }

  function apiUrl(path) {
    var base = window.location.origin;
    var qp = API_KEY ? '?api_key=' + encodeURIComponent(API_KEY) : '';
    return base + path + qp;
  }

  // ── Poll glass state ──────────────────────────────────────────────────────
  function pollState() {
    fetch(apiUrl('/glass/state'), { headers: apiHeaders() })
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

  // ── Toggle recording ──────────────────────────────────────────────────────
  function toggleRecording() {
    fetch(apiUrl('/glass/command'), {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ command: 'toggle_recording' }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function () {
        showToast(glassState.is_recording ? '녹화 중지 요청됨' : '녹화 시작 요청됨', 'success');
      })
      .catch(function (e) {
        showToast('명령 전송 실패: ' + e.message, 'error');
      });
  }

  // ── Render ────────────────────────────────────────────────────────────────
  function render() {
    renderPatient();
    renderStatus();
    renderInsight();
    renderToggleButton();
  }

  function renderPatient() {
    var name = glassState.patient || '환자 미선택';
    var count = glassState.session_count || 0;
    var sessionLabel = count > 0 ? '세션 ' + count + '회 완료' : '녹화 대기';

    setText('patient-name', name);
    setText('session-label', sessionLabel);
  }

  function renderStatus() {
    var card = document.getElementById('status-card');
    var recDot = document.getElementById('rec-dot');
    var title = document.getElementById('status-title');
    var meta = document.getElementById('status-meta');
    var timer = document.getElementById('rec-timer');

    if (glassState.is_recording) {
      card.classList.add('recording');
      recDot.classList.remove('hidden');
      title.classList.add('recording');
      setText('status-title', 'REC');

      var patient = glassState.patient;
      var count = glassState.session_count || 0;
      var metaText = count > 0 ? '세션 ' + count : '';
      if (patient) metaText = (metaText ? metaText + ' · ' : '') + patient;
      setText('status-meta', metaText);

      timer.textContent = elapsedString(glassState.recording_start);
    } else {
      card.classList.remove('recording');
      recDot.classList.add('hidden');
      title.classList.remove('recording');
      setText('status-title', '대기 중');
      setText('status-meta', '');
      timer.textContent = '';
    }
  }

  function renderInsight() {
    var insight = glassState.last_insight;
    if (!insight) return;

    var insightId = insight.id || (insight.title + ':' + insight.body);
    if (insightId === lastInsightId) return;

    lastInsightId = insightId;
    showInsightCard(insight.title || '', insight.body || '');
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
    }, 100);

    insightDismissTimer = setTimeout(function () {
      clearInterval(insightProgressTimer);
      card.classList.add('hidden');
    }, INSIGHT_DURATION_MS);
  }

  function renderToggleButton() {
    var btn = document.getElementById('toggle-btn');
    if (glassState.is_recording) {
      btn.textContent = '■ 녹화 중지';
      btn.classList.remove('primary');
      btn.classList.add('stop');
    } else {
      btn.textContent = '● 녹화 시작';
      btn.classList.remove('stop');
      btn.classList.add('primary');
    }
  }

  // ── Timer update (1 s tick while recording) ───────────────────────────────
  function elapsedString(isoStart) {
    if (!isoStart) return '00:00';
    var start = new Date(isoStart).getTime();
    var secs = Math.max(0, Math.floor((Date.now() - start) / 1000));
    var mm = Math.floor(secs / 60);
    var ss = secs % 60;
    return (mm < 10 ? '0' : '') + mm + ':' + (ss < 10 ? '0' : '') + ss;
  }

  // ── Connection indicator ──────────────────────────────────────────────────
  function setConnected(ok) {
    var dot = document.getElementById('conn-dot');
    var label = document.getElementById('conn-label');
    if (ok) {
      dot.className = 'conn-dot connected';
      label.textContent = '연결됨';
    } else {
      dot.className = 'conn-dot error';
      label.textContent = '오류';
    }
  }

  // ── Toast ─────────────────────────────────────────────────────────────────
  function showToast(msg, type) {
    var toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = 'toast' + (type ? ' ' + type : '');
    toast.offsetHeight;
    toast.classList.add('visible');
    setTimeout(function () { toast.classList.remove('visible'); }, 2500);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  // ── Events ────────────────────────────────────────────────────────────────
  function setupEvents() {
    document.addEventListener('click', function (e) {
      var el = e.target.closest('[data-action]');
      if (!el) return;
      var action = el.dataset.action;
      if (action === 'toggle-recording') toggleRecording();
    });

    document.addEventListener('keydown', function (e) {
      switch (e.key) {
        case 'Enter':
          var focused = document.activeElement;
          if (focused && focused.classList.contains('focusable')) {
            focused.click();
          } else {
            toggleRecording();
          }
          e.preventDefault();
          break;
        case ' ':
          toggleRecording();
          e.preventDefault();
          break;
      }
    });
  }

  // ── REC timer tick ────────────────────────────────────────────────────────
  function timerTick() {
    if (glassState.is_recording) {
      var timer = document.getElementById('rec-timer');
      if (timer) timer.textContent = elapsedString(glassState.recording_start);
    }
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  function init() {
    setupEvents();

    var btn = document.getElementById('toggle-btn');
    if (btn) btn.focus();

    pollState();
    setInterval(pollState, POLL_MS);
    setInterval(timerTick, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
