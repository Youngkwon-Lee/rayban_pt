#!/usr/bin/env bash
set -u

# Run the native companion on a real iPhone after Ray-Ban Meta pairing.
# This deliberately does not enable any Demo/autotest flag.

if [[ -z "${RAYBAN_DEVICE_UDID:-}" ]]; then
  echo "RAYBAN_DEVICE_UDID is required" >&2
  exit 2
fi

APP_BUNDLE_ID="${RAYBAN_APP_BUNDLE_ID:-yk.RaybanPT}"
TIMEOUT_SECONDS="${RAYBAN_PROOF_TIMEOUT_SECONDS:-180}"
OUTPUT_DIR="${RAYBAN_PROOF_OUTPUT_DIR:-$(cd "$(dirname "$0")/.." && pwd)/output/hardware-proof}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_PATH="$OUTPUT_DIR/physical-capture-$STAMP.log"

mkdir -p "$OUTPUT_DIR"
echo "log=$LOG_PATH"
echo "device=$RAYBAN_DEVICE_UDID"
echo "app=$APP_BUNDLE_ID"
echo "Before continuing, select a non-PHI QA patient, confirm active capture consent,"
echo "enable 세션 자동기록, start the live stream, and stop it after a short session."

if xcrun devicectl device info apps --include-all-apps --device "$RAYBAN_DEVICE_UDID" 2>/dev/null | rg -qi 'Meta AI|com\.facebook\.stellaapp|com\.facebook|com\.meta'; then
  echo "preflight_meta_ai=detected"
else
  echo "preflight_meta_ai=not-detected"
  echo "Install/open Meta AI, pair the Ray-Ban glasses, enable Developer Mode, and grant DAT access before retrying."
fi

installed_apps="$(xcrun devicectl device info apps --include-all-apps --device "$RAYBAN_DEVICE_UDID" 2>/dev/null || true)"
if printf '%s\n' "$installed_apps" | rg -q 'com\.youngkwon\.raybanpt'; then
  echo "preflight_legacy_bundle=detected"
  echo "Warning: legacy com.youngkwon.raybanpt is also installed. Keep the current bundle explicit for launch; remove the legacy app before relying on carelive:// deep-link routing."
else
  echo "preflight_legacy_bundle=not-detected"
fi

if printf '%s\n' "$installed_apps" | rg -q "[[:space:]]${APP_BUNDLE_ID}[[:space:]]"; then
  echo "preflight_target_bundle=detected"
else
  echo "preflight_target_bundle=not-detected"
  echo "Install the current RaybanPT build before retrying this proof." >&2
fi

set +e
xcrun devicectl device process launch \
  --device "$RAYBAN_DEVICE_UDID" \
  --terminate-existing \
  --console \
  --timeout "$TIMEOUT_SECONDS" \
  "$APP_BUNDLE_ID" 2>&1 | tee "$LOG_PATH"
LAUNCH_STATUS=${PIPESTATUS[0]}
set -e

echo
echo "--- physical proof summary ---"

criteria=0
passed=0

check() {
  local label="$1"
  local pattern="$2"
  criteria=$((criteria + 1))
  if rg -q -- "$pattern" "$LOG_PATH"; then
    echo "PASS $label"
    passed=$((passed + 1))
  else
    echo "MISS $label"
  fi
}

check "DAT registration advanced" 'registrationState.*rawValue: [1-9]'
check "connected accessory" 'initialConnectedAccessories count [1-9][0-9]*|device compatibility:'
check "real video frame" '\[VideoRecorder\] first-frame source='
check "non-empty video artifact" '\[VideoRecorder\] recording-stopped frames=[1-9][0-9]* bytes=[1-9][0-9]*'
check "Ray-Ban HFP route selected" '\[AudioRecorder\] route=recording-started.*BluetoothHFP:.*(Ray-Ban|Rayban|Meta)'
check "HFP recording started" '\[AudioRecorder\] route=recording-started'
check "non-empty HFP artifact" '\[AudioRecorder\] recording-stopped bytes=[1-9][0-9]*'

echo "criteria=$passed/$criteria"
echo "launch_status=$LAUNCH_STATUS"

if [[ "$passed" -eq "$criteria" ]]; then
  echo "PHYSICAL_CAPTURE_PROOF=PASS"
  exit 0
fi

echo "PHYSICAL_CAPTURE_PROOF=INCOMPLETE"
echo "Review $LOG_PATH, then read the bridge capture event and physio_app timeline." >&2
exit 1
