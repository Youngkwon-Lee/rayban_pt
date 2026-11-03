# Real Meta Ray-Ban IMU probe (research only)

**2026-11-03 status: code compiled; PHYSICAL SENSOR TEST BLOCKED.** iPhone 12 Pro is currently reported as `unavailable` by Xcode/CoreDevice on the connected Mac. This source has not been installed or executed on a physical phone, and it does not prove the motion signal exists on Meta Ray-Ban Display or can detect silent speech.

## Purpose

A separate iOS QA app reads **real glasses** IMU motion through Meta Wearables DAT `1.0.1` only after a person manually selects a registered device and taps Start. It does not import `MWDATMockDevice`, use the existing `rayban_pt` camera/audio/HUD, or change its pin to DAT `0.9.0`. The QA bundle ID is `com.kinelo.realmotionqa20261103`, distinct from `yk.RaybanPT` and the synthetic `com.kinelo.motionmockqa20261102`.

## Implementation and privacy

- `Wearables.configure()` on launch. The separate URL scheme `kinelomotionqa://` accepts only Meta AI registration callbacks carrying the `metaWearablesAction` query item.
- Device discovery lists link state, compatibility and donned state, without printing or saving device identifiers.
- Registration opens the Meta AI companion only after an explicit button press. Session creation is guarded on `.registered`, a selected device and `.connected`. Do not create a real session from synthetic mocks.
- After the session reaches `.started`, add experimental `MWDATMotion` at 10 Hz and read up to 30 frames (9-second watchdog). Failure and absence of samples are explicit non-PASS results.
- PASS requires at least 10 received frames, every source `.glasses`, at least 10 acceleration and gyro fields, and strictly increasing device timestamps.
- Operator chooses one benign trial label: `정지`, `입만 움직임`, `머리 움직임`. Labels are **not** training data or evidence of recognition accuracy.
- Stores **only** these summary counts, clock interval, generic trial label and status in app-local `Documents/real-motion-probe-summary.json`. No raw IMU series, device IDs, speech, images, video, network upload, patient data, or clinical actions. Nothing happens until the user presses the start button.

## Setup and prerequisites

1. Use a **real, compatible** Ray-Ban device paired with the iPhone through Meta AI. Prior project notes mention Meta Ray-Ban Display, but the current physical pairing and firmware have not been verified in this run.
2. Enable Developer Mode in Meta AI (Settings > Your glasses > Developer Mode).
3. Meta's Motion skill says Motion is **experimental** and requires enablement for the app in Wearables Developer Center; there is no runtime Motion permission to request. Developer Mode with `MetaAppID: "0"` bypasses attestation but is **not** proof that experimental Motion is entitled. A `capabilityClosed` error should be reported, not worked around.
4. Keep device on, worn, and connected. If the SDK requires an on-glasses DAT or firmware update, follow Meta's approved flow separately; this repo does not perform updates.
5. Xcode 27.2+ and XcodeGen. From this directory, run `xcodegen generate -s project.yml` to generate an isolated project with SDK **exact 1.0.1**. Sign the QA bundle with the user's authorized developer team. Confirm the ID doesn't overwrite an existing app before installing.
6. On the unlocked iPhone, open the QA app, register with Meta AI using its button if needed, select the correct device, and run **one trial**.
7. Check the on-screen PASS/FAIL and the QA app's own JSON. Compare fresh timestamps to avoid recycling old results. Run the three conditions separately only after data transport is stable.

## Build evidence (2026-11-03)

- Local temporary SDK tag 1.0.1 checkout: commit `0bd369298eef6a980ad0bdb6c8a5502f2c02a08c`.
- Command: `xcodebuild -project RealMotionProbe.xcodeproj -scheme RealMotionProbe -configuration Debug -destination 'generic/platform=iOS' -sdk iphoneos CODE_SIGNING_ALLOWED=NO build`.
- Generic iPhone Debug compile result: **PASS, exit 0**. It used an isolated `project-local.yml` that substitutes the local checkout of the same 1.0.1 SDK tag; the committed `project.yml` retains a portable SPM exact version.
- Source Git blob: `e9c5c946170d3ad7902661bea2d2102bd4f01305`; XcodeGen spec blob: `ad3d149a295fd826b216a7845136bf6ed1e581fe`.
- **Not run**: package download/generation from the portable remote spec, real-device signing, installation, Meta AI registration, physical Motion frames, actual conditions, CI or production deployment. The iPhone was `unavailable` in `devicectl list devices` at the time of this test.

## Decision gate

A PASS only establishes real inertial data transport. Whether silent mouthing generates a distinguishable motion signal is a later **held-out, within-person feasibility study** against still/head-only baseline. Do not label gestures as speech recognition or connect to Kinelo clinical actions without this evidence and explicit review.

Official API: https://github.com/meta/meta-wearables-dat-ios/blob/main/plugins/mwdat-ios/skills/motion/SKILL.md
Official prerequisites: https://github.com/meta/meta-wearables-dat-ios/blob/main/plugins/mwdat-ios/skills/getting-started/SKILL.md
