# Meta Motion Mock QA app — isolated iOS runtime probe

This QA app is **research-only** and isolated from the installed \`rayban_pt\` app. It must not be used for clinical input or any physical action. It does not access patient data, photos, microphone, Meta glasses, or any backend. It uses generated 10 Hz simulated motion frames from the sibling \`../MetaMotionMockSpike.swift\`.

## Reproduction

Prerequisites: macOS with Xcode 27.2+ and XcodeGen; SDK 1.0.1 Swift package downloaded through Xcode/SPM.

1. From this \`QAApp\` directory: \`xcodegen generate --spec project.yml\`.
2. Compile without signing: \`xcodebuild -project MetaMotionMockQA.xcodeproj -scheme MetaMotionMockQA -configuration Debug -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build\`.
3. For a **test** iPhone, sign using your own team, choose a unique bundle ID and verify no installed app with that ID would be overwritten. The default ID \`com.kinelo.motionmockqa20261102\` was verified absent before first install, then used for subsequent QA updates.
4. Unlock your iPhone and open **Meta Motion Mock QA**. The app automatically runs the mock-only experiment, logs receive counts and writes \`Documents/meta-motion-mock-qa-result.json\` inside the QA app container.
5. Interpret results strictly: PASS requires ≥1 accelerometer sample, ≥1 gyroscope sample and strictly increasing timestamps. A zero sample count is NOT a successful mock test. Avoid unvalidated real motion/clinical actions.

The app generates deterministic 30-frame fake signals in RAM, sets \`MockGlasses.services.motion.setMotionFeed\`, watches for simulated device discovery and selector availability, starts a \`DeviceSession\`, adds Motion, reads up to 30 \`Motion.samples\` (12 second timeout), and cleans up. Errors are written as explicit failures rather than silently treated as passes.

## Verified environment / observed results — 2026-11-02

- Official SDK: \`meta/meta-wearables-dat-ios\` **1.0.1**, tag SHA \`0bd369298eef6a980ad0bdb6c8a5502f2c02a08c\`.
- Separate QA app was built and codesigned using locally checked-out SDK 1.0.1, installed and launched on iPhone 12 Pro without overwriting either installed Kinelo AR HUD bundle.
- Initial app: **0 frames**, session creation returned \`noEligibleDevice\`.
- Waiting until \`Wearables.shared.devices\` contains the fake identifier and \`SpecificDeviceSelector.activeDevice\` is non-nil did not by itself resolve \`noEligibleDevice\`.
- Official SDK 1.0.0 changelog notes MockDeviceKit uses the same Bluetooth/Wi-Fi Info.plist connectivity gating as physical glasses. Relevant Info.plist declarations were added to this QA target only, not to the production app.
- Later reopen: **0 frames**, \`Wearables.configure()\` returned \`alreadyConfigured\` in the existing SDK singleton. A guarded handling path was added.
- Latest code was built, signed and installed on the same independent QA app ID. **Final execution was blocked** because the iPhone was locked (\`FBSOpenApplicationErrorDomain\` Locked). This build's stream receiving behavior has **not been tested**.
- The portable **exactVersion 1.0.1** XcodeGen project spec was generated successfully, but its additional build using remote-package resolution was stopped while Xcode downloaded the full SDK mirror. Prior successful signed builds used a local SDK checkout of the *same tag*, not this portable remote package layout.
- No usable iOS Simulator runtime was installed on the connected Mac. **No Motion.samples stream has been confirmed at runtime.**

## Safety and boundaries

- Distinct app bundle ID; no existing app installs or files were replaced.
- No real Meta or AirPods motion, audio, photos, PHI, patients, server access, writes, merges, or deployments.
- The default \`MetaAppID: "0"\` and \`ClientToken: "mock-only"\` are placeholders for a mock-only development environment, not production credentials. Do not use a patient-linked or production Meta app ID for this exercise.
- The QA bundle was left on the phone, so the owner can unlock it and trigger a local retry without rebuilding. Only this QA app's own data file is read for evidence.
- If \`noEligibleDevice\` persists after unlocking, stop and diagnose eligibility/SDK compatibility; do not claim that physical glasses or silent speech will work.

Official SDK references:
- https://github.com/meta/meta-wearables-dat-ios/blob/1.0.1/CHANGELOG.md
- https://github.com/meta/meta-wearables-dat-ios/blob/1.0.1/AGENTS.md
