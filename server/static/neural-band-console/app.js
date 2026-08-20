const STORAGE_KEY = "kinelo.neuralBandConsole.config.v1";
const DEFAULT_BASE_URL = window.location.origin;
const POLL_INTERVAL_MS = 3000;
const MAX_LOG_LINES = 120;

const elements = {
  baseUrl: document.getElementById("baseUrl"),
  apiKey: document.getElementById("apiKey"),
  deviceId: document.getElementById("deviceId"),
  sessionTag: document.getElementById("sessionTag"),
  metadataJson: document.getElementById("metadataJson"),
  customGesture: document.getElementById("customGesture"),
  healthChip: document.getElementById("healthChip"),
  glassChip: document.getElementById("glassChip"),
  statePatient: document.getElementById("statePatient"),
  stateMode: document.getElementById("stateMode"),
  stateRecording: document.getElementById("stateRecording"),
  stateSessionCount: document.getElementById("stateSessionCount"),
  stateDump: document.getElementById("stateDump"),
  eventLog: document.getElementById("eventLog"),
  saveConfigBtn: document.getElementById("saveConfigBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  clearLogBtn: document.getElementById("clearLogBtn"),
  sendCustomBtn: document.getElementById("sendCustomBtn"),
};

let logLines = [];
let pollHandle = null;

function normalizeBaseUrl(value) {
  const trimmed = (value || "").trim();
  return (trimmed || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

function readConfig() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    if (Object.prototype.hasOwnProperty.call(parsed, "apiKey")) {
      delete parsed.apiKey;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(parsed));
    }
    return {
      baseUrl: normalizeBaseUrl(parsed.baseUrl),
      apiKey: "",
      deviceId: parsed.deviceId || "band-01",
      sessionTag: parsed.sessionTag || "pilot",
      metadataJson: parsed.metadataJson || "",
    };
  } catch {
    return {
      baseUrl: DEFAULT_BASE_URL,
      apiKey: "",
      deviceId: "band-01",
      sessionTag: "pilot",
      metadataJson: "",
    };
  }
}

function writeConfig() {
  const next = {
    baseUrl: normalizeBaseUrl(elements.baseUrl.value),
    deviceId: elements.deviceId.value.trim(),
    sessionTag: elements.sessionTag.value.trim(),
    metadataJson: elements.metadataJson.value.trim(),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  appendLog("config.saved", next);
  setChip(elements.healthChip, "설정 저장됨", "warn");
}

function hydrateConfig() {
  const config = readConfig();
  elements.baseUrl.value = config.baseUrl;
  elements.apiKey.value = config.apiKey;
  elements.deviceId.value = config.deviceId;
  elements.sessionTag.value = config.sessionTag;
  elements.metadataJson.value = config.metadataJson;
}

function setChip(node, text, tone) {
  node.textContent = text;
  node.classList.remove("ok", "warn", "bad");
  if (tone) {
    node.classList.add(tone);
  }
}

function appendLog(type, payload) {
  const timestamp = new Date().toLocaleTimeString("ko-KR", { hour12: false });
  const line = `[${timestamp}] ${type}\n${JSON.stringify(payload, null, 2)}`;
  logLines = [line, ...logLines].slice(0, MAX_LOG_LINES);
  elements.eventLog.textContent = logLines.join("\n\n");
}

function parseMetadata() {
  const raw = elements.metadataJson.value.trim();
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed;
    }
  } catch (error) {
    throw new Error(`metadata JSON parse failed: ${error.message}`);
  }
  throw new Error("metadata JSON must be an object");
}

function requestHeaders(includeJson = false) {
  const headers = {};
  const apiKey = elements.apiKey.value.trim();
  if (apiKey) {
    headers["x-api-key"] = apiKey;
  }
  if (includeJson) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

async function fetchJson(path, options = {}) {
  const baseUrl = normalizeBaseUrl(elements.baseUrl.value);
  const response = await fetch(`${baseUrl}${path}`, options);
  const rawText = await response.text();
  let body = null;
  try {
    body = rawText ? JSON.parse(rawText) : {};
  } catch {
    body = { raw: rawText };
  }
  if (!response.ok) {
    const detail = body?.detail || body?.message || response.statusText || "request failed";
    throw new Error(detail);
  }
  return body;
}

function renderGlassState(state) {
  elements.statePatient.textContent = state.patient || "-";
  elements.stateMode.textContent = state.mode || "-";
  elements.stateRecording.textContent = state.is_recording ? "recording" : "idle";
  elements.stateSessionCount.textContent = String(state.session_count ?? "-");
  elements.stateDump.textContent = JSON.stringify(state, null, 2);

  if (state.is_recording) {
    setChip(elements.glassChip, "Glass recording 중", "ok");
  } else if (state.mode) {
    setChip(elements.glassChip, `Glass ${state.mode}`, "warn");
  } else {
    setChip(elements.glassChip, "Glass 상태 대기", "warn");
  }
}

async function refreshStatus() {
  try {
    const [health, state] = await Promise.all([
      fetchJson("/health", { headers: requestHeaders(false) }),
      fetchJson("/glass/state", { headers: requestHeaders(false) }),
    ]);
    setChip(elements.healthChip, `Bridge OK · ${health?.status || "ready"}`, "ok");
    renderGlassState(state);
  } catch (error) {
    setChip(elements.healthChip, `Bridge 오류 · ${error.message}`, "bad");
    setChip(elements.glassChip, "Glass 상태 확인 실패", "bad");
    elements.stateDump.textContent = String(error.message || error);
  }
}

async function sendGesture(gesture) {
  let metadata;
  try {
    metadata = parseMetadata();
  } catch (error) {
    appendLog("gesture.invalid_metadata", { error: error.message });
    setChip(elements.healthChip, "Metadata JSON 확인 필요", "bad");
    return;
  }

  const payload = {
    gesture,
    device_id: elements.deviceId.value.trim() || undefined,
    metadata: {
      ...metadata,
      session_tag: elements.sessionTag.value.trim() || undefined,
    },
  };

  try {
    const result = await fetchJson("/neural-band/event", {
      method: "POST",
      headers: requestHeaders(true),
      body: JSON.stringify(payload),
    });
    appendLog("gesture.sent", { payload, result });
    setChip(elements.healthChip, `Sent ${gesture}`, "ok");
    refreshStatus();
  } catch (error) {
    appendLog("gesture.failed", { payload, error: error.message });
    setChip(elements.healthChip, `전송 실패 · ${error.message}`, "bad");
  }
}

function bindEvents() {
  elements.saveConfigBtn.addEventListener("click", () => {
    writeConfig();
    restartPolling();
  });

  elements.refreshBtn.addEventListener("click", refreshStatus);
  elements.clearLogBtn.addEventListener("click", () => {
    logLines = [];
    elements.eventLog.textContent = "";
  });

  elements.sendCustomBtn.addEventListener("click", () => {
    const gesture = elements.customGesture.value.trim();
    if (!gesture) {
      setChip(elements.healthChip, "Custom gesture 이름이 필요합니다", "bad");
      return;
    }
    sendGesture(gesture);
  });

  document.querySelectorAll("[data-gesture]").forEach((button) => {
    button.addEventListener("click", () => sendGesture(button.dataset.gesture));
  });
}

function restartPolling() {
  if (pollHandle) {
    window.clearInterval(pollHandle);
  }
  refreshStatus();
  pollHandle = window.setInterval(refreshStatus, POLL_INTERVAL_MS);
}

hydrateConfig();
bindEvents();
restartPolling();
