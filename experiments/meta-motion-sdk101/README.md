# Meta Ray-Ban Motion 1.0.1 — isolated API spike

**Status (2026-11-02): SDK API typecheck PASS; no simulated session was executed.**

This experimental source is intentionally OUTSIDE the iOS app target. The production-facing \`RaybanPT/RaybanPT.xcodeproj\` still pins Meta DAT **0.9.0**. Nothing here upgrades the app, collects patient data, changes camera/audio/display behavior, or enables clinical actions.

## Evidence / scope

- Official Meta DAT iOS SDK version: **1.0.1**, tag commit \`0bd369298eef6a980ad0bdb6c8a5502f2c02a08c\`.
- SDK modules used: \`MWDATCore\`, \`MWDATMotion\` (experimental), \`MWDATMockDevice\`.
- \`MetaMotionMockSpike.swift\`: prepare mock Ray-Ban Meta, configure a deterministic local feed of 30 \`MWDATMockDevice.MotionSample\` frames, attach Motion to an externally-started session, read up to 30 real SDK \`MWDATMotion.MotionSample\` frames, and provide cleanup.
- Each mapped sample preserves monotonic nanosecond timestamp and **optional** sensor values. Acceleration is interpreted as m/s², angular velocity as rad/s and orientation as a quaternion. It is NOT equivalent to AirPods' userAcceleration / relative Euler angles; do not feed it straight into the AirPods nine-feature classifier without a conversion/validation layer.
- SDK Motion is experimental. SDK availability does not imply a physical Ray-Ban exposes usable lip/jaw signals or can decode silent speech.

## Reproduce the actual compile-only check

On a Mac with Xcode 27.2 and Swift 6.4.2:

\`\`\`bash
ROOT="$(pwd)"
SDK_ROOT="/tmp/meta-wearables-motion101-20261102"
git clone --depth 1 --single-branch --branch 1.0.1 \
  https://github.com/meta/meta-wearables-dat-ios.git "$SDK_ROOT"
git -C "$SDK_ROOT" rev-parse HEAD
# The SDK tag checked on 2026-11-02: 0bd369298eef6a980ad0bdb6c8a5502f2c02a08c

xcrun swiftc -typecheck -parse-as-library -swift-version 6 \
  -target arm64-apple-ios17.6 \
  -sdk "$(xcrun --sdk iphoneos --show-sdk-path)" \
  -F "$SDK_ROOT/MWDATCore.xcframework/ios-arm64" \
  -F "$SDK_ROOT/MWDATMockDevice.xcframework/ios-arm64" \
  -F "$SDK_ROOT/MWDATMotion.xcframework/ios-arm64" \
  "$ROOT/experiments/meta-motion-sdk101/MetaMotionMockSpike.swift"
\`\`\`

**Observed result:** exit code 0, no typecheck errors. This only validates Swift API compatibility with the official **1.0.1** frameworks. It does **not** prove that motion samples are delivered by MockDeviceKit or on real glasses.

## Real MockDevice runtime validation (not done)

1. In a SEPARATE test app pinned to SDK 1.0.1, call \`Wearables.configure()\` once, enable \`MockDeviceKit\`, and pair \`.rayBanMeta\`.
2. Create a specific-device \`DeviceSession\`; call \`start()\` and wait for \`.started\`. The spike's \`startMotion(on:)\` rejects an unstarted session.
3. Register stream/state/error listeners, start Motion, verify mock sample count, timestamp monotonicity, optional fields, units, lifecycle transitions and stop/restart. Time out / cancel sample consumers when no frames arrive.
4. Run existing camera/audio/display compatibility checks **before** considering SDK upgrade; preserve test evidence by exact version/commit.
5. After explicit device access and physical hardware proof, run a non-patient gesture pilot. Compare silent mouthing against head-only motions; measure false acceptance and rejection in held-out sessions. Do not connect recognition to clinical writes.

### Environment constraints observed

- iOS app \`rayban_pt\` at PR #5 can be built for a generic iPhone target using a placeholder local \`Secrets.xcconfig\` and \`CODE_SIGNING_ALLOWED=NO\`.
- A physical iPhone was detected, **but it already has \`yk.RaybanPT\` installed**. No install or overwrite was attempted.
- No runnable iOS simulator runtime was installed on the connected Mac; the SDK MockDevice session was not executed.
- The normal user's repo checkout was not changed; isolated temporary directories were used.
- There was no merge, deployment, real camera/audio/PHI collection or app SDK upgrade.

Official references:
- https://github.com/meta/meta-wearables-dat-ios/blob/1.0.1/CHANGELOG.md
- https://github.com/meta/meta-wearables-dat-ios/blob/1.0.1/AGENTS.md
- https://github.com/meta/meta-wearables-dat-ios
