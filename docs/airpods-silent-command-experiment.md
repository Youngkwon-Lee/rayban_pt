# AirPods silent-command experiment

Research-only, opt-in iOS SwiftUI experiment. This does not use Ray-Ban sensors, and does not change existing capture/bridge/clinical workflows.

## Without AirPods: executable synthetic demo

The existing iOS Text tab now offers **AirPods 없는 합성 데이터 데모** as an alternative to **AirPods 센서 시작**. No Bluetooth headphones or glasses are needed for this demonstration.

1. Open Text > AirPods 무음 명령 실험 and tap "AirPods 없는 합성 데이터 데모".
2. Pick one of the five labels; tap 학습 구간 시작 3 times. Repeat for a second label.
3. Pick a trained label and tap 인식 구간 시작. The classifier is fed synthetic 9-channel motion data and shows a candidate, or a rejection.
4. Confirm or reject; confirmation displays the candidate only in Text tab. Training samples remain memory-only.
5. Repeat with other labels. This demonstration proves **only the UI/algorithm path**, NOT whether mouth movements can be sensed without AirPods.

The deterministic training/replay signals are generated from the selected label; they are deliberately distinguishable and are NOT an independent accuracy dataset. The result must not be reported as measured human speech performance.

## Meta Ray-Ban as a future sensor (not yet implemented)

The current `RaybanPT/RaybanPT.xcodeproj/project.pbxproj` pins Meta Wearables Device Access Toolkit `0.9.0`. Meta added **experimental** `MWDATMotion`, `MotionConfiguration`, and `MotionSample` in `1.0.0` (changelog dated 2026-09-24). This PR does NOT upgrade SDK or attach glasses-motion capability; doing so requires explicit compatibility evaluation for existing capture, camera, audio and display features. Availability varies by glasses and firmware; published SDK motion capability does not establish the presence of measurable silent-mouth speech signals. Existing public Android reports include Motion stuck at STARTING with zero samples in 1.0.0.

References:
- https://developers.meta.com/wearables/faq/
- https://github.com/meta/meta-wearables-dat-ios/blob/main/CHANGELOG.md
- https://github.com/meta/meta-wearables-dat-ios/blob/main/AGENTS.md
- https://github.com/facebook/meta-wearables-dat-android/issues/200

## Operation (real headphone-motion path; requires supported hardware)
1. Connect head-tracking AirPods to a real iPhone and select them as the iPhone audio output. Open Text > AirPods silent-command experiment.
2. Tap Sensor start. Grant Motion & Fitness permission if asked.
3. Choose a command (다음, 이전, 기록, 확인, 취소), tap Training segment start, silently mouth the command, and tap End segment after about 1-3 seconds. Train at least two labels, three takes each.
4. Look at leave-one-out training score. This is not a held-out or clinical accuracy estimate.
5. Tap Recognition segment start, mouth a trained command, end the segment, then explicitly confirm or reject the candidate. Confirmation only displays the label in the Text tab; it does NOT execute anything or fill/upload patient notes.

## Implementation
- CoreMotion CMHeadphoneMotionManager, on-device, 9 features (user acceleration, rotation rate and relative attitude).
- Per-take centering, per-training-set scaling, banded dynamic time warping, 1-nearest-template matching.
- A candidate is rejected unless its distance is within 1.25x the worst within-label nearest-neighbor training distance and its margin over the second label is at least 25%.
- Training templates are never sent over the network or written to disk. They reside in view memory only, have an 8-take-per-label cap and are destroyed on dismissal. No audio or media is captured. No backend/DB/PHI writes.
- Sensor stream runs only while the user explicitly starts it and stops when the sheet closes. No background processing.
- Motion availability fails closed on simulator or unsupported headphones.

## Limitations and field check
AirPods expose processed ~25 Hz head motion, not the private speech-detecting accelerometer or an inward mic. Jaw/head motion is an indirect proxy. Silence classification is unvalidated; other movements or repositioning can create false positives. Never use these predictions to perform a clinical action without a separate consent and review workflow.

Next field validation: with synthetic command labels and no patients, compare 5 command labels across repeated sessions, reseat AirPods between sessions, record held-out accuracy, rejection rate, false accepts, and time per command. Compare to tap, whisper STT and normal STT. Do not infer overall product accuracy from in-sample leave-one-out scoring.

Independent implementation inspired by the public MIT-license experiment https://github.com/joeblau/subvox (no source code copied).
Apple interface: https://developer.apple.com/documentation/coremotion/cmheadphonemotionmanager

Device build/run and hardware evaluation are unverified. No merge or production release intended.
