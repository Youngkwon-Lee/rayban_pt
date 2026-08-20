#!/usr/bin/env bash
# Local proof that the iOS app still compiles.
# Usage: ./scripts/build_ios.sh ["scheme"]   (default: RaybanPT)
set -euo pipefail
cd "$(dirname "$0")/.."

SCHEME="${1:-RaybanPT}"

xcodebuild \
  -project RaybanPT/RaybanPT.xcodeproj \
  -scheme "$SCHEME" \
  -destination "generic/platform=iOS Simulator" \
  -derivedDataPath .deriveddata-device \
  CODE_SIGNING_ALLOWED=NO \
  build | tail -20

echo "RESULT: iOS build succeeded for scheme '$SCHEME'"
