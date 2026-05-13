#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8791}"
HOST="${HOST:-0.0.0.0}"
KEY_FILE="${BRIDGE_API_KEY_FILE:-$PWD/.bridge_api_key}"

if [[ -z "${BRIDGE_API_KEY:-}" ]]; then
  if [[ ! -f "$KEY_FILE" ]]; then
    umask 077
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex 24 > "$KEY_FILE"
    else
      .venv/bin/python - <<'PY' > "$KEY_FILE"
import secrets
print(secrets.token_hex(24))
PY
    fi
  fi
  export BRIDGE_API_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"
fi

export REQUIRE_API_KEY="${REQUIRE_API_KEY:-true}"
export ALLOW_INSECURE_LAN="${ALLOW_INSECURE_LAN:-false}"
export ENABLE_FILE_DOWNLOADS="${ENABLE_FILE_DOWNLOADS:-false}"
export IMAGE_STORE="${IMAGE_STORE:-false}"
export AUDIO_STORE="${AUDIO_STORE:-false}"
export VIDEO_STORE="${VIDEO_STORE:-false}"
export PHI_REDACT="${PHI_REDACT:-true}"
export REQUIRE_PATIENT_CONSENT="${REQUIRE_PATIENT_CONSENT:-true}"

echo "Rayban local bridge: http://$HOST:$PORT"
echo "API key file: $KEY_FILE"
echo "API key: $BRIDGE_API_KEY"
echo "Set ALLOW_INSECURE_LAN=true only for temporary testing."

exec .venv/bin/python -m uvicorn app:app --host "$HOST" --port "$PORT"
