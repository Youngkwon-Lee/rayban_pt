# MRBD Device Testing

This checklist is for testing `server/static/glass-webapp/` on Meta Ray-Ban Display glasses.

## Preferred Test Topology

Use a same-origin HTTPS bridge when possible:

```text
Meta Ray-Ban Display
  -> https://<bridge-host>/glass-app/
  -> same origin /glass/state and /glass/command
  -> local Rayban PT bridge
  -> iOS DAT app
```

This avoids putting bridge credentials or patient context into a public static hosting URL.

If you need a standalone public static preview, the web app supports:

```text
https://glass-webapp.vercel.app/?bridge_url=https%3A%2F%2F<bridge-host>&api_key=<short-lived-test-key>
```

Use this only with non-PHI test data and a short-lived bridge API key.

The current persistent home bridge is available at:

```text
https://desktop-t43sn5m-1.tailde3b80.ts.net/glasspt
```

As of 2026-08-17, the Vercel `glass-webapp` project was resumed from
`DEPLOYMENT_PAUSED`, a fresh preview was deployed, and the stable alias below
was verified over HTTPS. The `/glass/*` rewrite reached the home bridge with a
short-lived HUD scope token and returned HTTP 200 state without exposing the
bridge API key.

Stable HUD URL:

```text
https://glass-webapp.vercel.app
```

### Bridge runtime sync — 2026-08-17

- The persistent home bridge had been running an older copy without
  `/capture-events`; the latest `server/app.py`, `schema.sql`, and `server/lib`
  were deployed to `/home/yk/services/rayban_pt/server` after a dated backup.
- Remote `py_compile`, systemd restart, and authenticated OpenAPI readback now
  show `/capture-events` and `/capture-events/extract`.
- A synthetic canonical QA encounter event was created and read by the
  authenticated `physio_app` Preview surface. It remains draft and is not a
  clinical write.

Operator-only token bootstrap (run on the home bridge host; never print or put
the returned token in a repository):

```bash
set -a
source /home/yk/.config/rayban-bridge/env
set +a
curl -X POST http://127.0.0.1:8791/glass/hud-token \
  -H "x-api-key:$BRIDGE_API_KEY" \
  -H 'content-type: application/json' \
  --data '{"organization_id":"<org-id>","provider_person_id":"<provider-person-id>","ttl_seconds":300}'
```

Use the returned short-lived token in:

```text
https://glass-webapp.vercel.app/connect/<short-lived-hud-token>
```

The token authorizes only the HUD relay paths. It does not authorize raw media,
clinical writes, or the general bridge API.

The `bridge_url` may include a path prefix such as `/glasspt`; the Web App preserves that prefix when calling `/glass/state` and `/glass/command`.

## Local Proof

From the bridge directory:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python smoke_test.py
```

For a browser preview:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8792
open "http://127.0.0.1:8792/glass-app/"
```

Expected:

- viewport is 600 x 600
- `favicon.png` is a real 128 x 128 PNG
- `manifest.webmanifest` references `favicon.png`
- no browser console errors
- arrow keys move focus across four commands
- Enter sends the focused command to `/glass/command`
- patient identity appears as an alias, not full PHI

## Standalone HTTPS Preview

The web app directory now includes a Vercel-compatible static wrapper:

- `server/static/glass-webapp/package.json`
- `server/static/glass-webapp/server.js`
- `server/static/glass-webapp/vercel.json`

Check Vercel login:

```bash
vercel whoami
```

Deploy the production static Web App:

```bash
cd /Users/youngkwon/projects/rayban_pt/server/static/glass-webapp
vercel --prod --yes
```

Current stable URL:

```text
https://glass-webapp.vercel.app
```

The former `stage-rayban-pt-mrbd-hud.vercel.app` address is stale and should not be used for device testing.

If using a remote bridge:

```text
https://glass-webapp.vercel.app/?bridge_url=https%3A%2F%2F<bridge-host>&api_key=<short-lived-test-key>
```

Prefer a same-origin bridge URL or `hud_token` over `api_key` query strings for real testing. Use `api_key` in a public static URL only with non-PHI fixtures and a short-lived test key.

Some Meta AI builds remove query parameters when they save a Web App. In that
case use the path-based connection form instead:

```text
https://glass-webapp.vercel.app/connect/<short-lived-hud-token>
```

The production app maps `/connect/<token>` to the HUD and uses the configured
home bridge as the bridge origin. The token is scoped, short-lived, and must be
issued by an authenticated bridge operator; never put `BRIDGE_API_KEY` in this
path or in a QR code.

## Meta AI App Deep Link

QR/deep-link format:

```text
fb-viewapp://web_app_deep_link?appName=Kinelo%20AR&appUrl=<url-encoded-production-url>
```

Generate the encoded URL:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python mrbd_device_link.py \
  "https://glass-webapp.vercel.app"
```

Then generate a QR code using the installed Meta Wearables QR skill script or any local QR utility.

## Clinical Privacy Preflight

The Rayban PT capture path does not invoke Meta AI. Camera frames and HFP microphone audio go to the paired iPhone app and the configured Rayban PT bridge.

Meta AI on the glasses is a separate path. Meta states that Meta AI voice conversations are stored by default and the option to disable voice-recording storage is no longer available. Before any clinical capture:

1. In the Meta AI mobile app, open **Glasses → Device settings → Meta AI → “Hey Meta” preferences** and disable **Hey Meta**.
2. Disable **Respond without “Hey Meta”**.
3. Do not activate Meta AI with the touchpad or Neural Band during the session.
4. Open **Glasses → Device settings → Glasses privacy → Voice activity log** and delete prior test interactions.
5. Confirm **Cloud media** is disabled in the glasses privacy settings.
6. Confirm the Rayban PT bridge URL is the only configured upload destination used by the iPhone app.

Disabling **Hey Meta** deactivates the camera-based Meta AI feature. If Meta AI is intentionally used to ask about what the wearer sees, the glasses send an image to Meta's cloud for processing.

This app also opts out of the separate DAT SDK analytics collection through `MWDAT.Analytics.OptOut=true` in `Info.plist`. That setting does not disable Meta AI voice-history storage; the Meta AI controls above are still required.

- [Meta AI mobile app settings and default voice storage](https://www.meta.com/help/ai-glasses/1964061290737893/)
- [Meta AI glasses voice privacy notice](https://www.meta.com/legal/ai-glasses/voice-controls-privacy-notice/)
- [How visual Meta AI requests use a cloud image](https://www.meta.com/help/smart-glasses/articles/voice-controls/ask-meta-ai-about-what-you-see-ray-ban-meta-smart-glasses/)
- [Meta cloud media settings](https://www.meta.com/help/ai-glasses/734190441863923/)
- [Official DAT iOS analytics opt-out](https://github.com/facebook/meta-wearables-dat-ios#opting-out-of-data-collection)

## On-Device Verification

### DAT 연결 사전조건 — 2026-08-17

실제 iPhone 12 Pro에서 최신 Debug 빌드를 실행한 결과:

```text
initialConnectedAccessories count 0
registrationState rawValue 0
prepareStandbyDisplay failed: noEligibleDevice
```

이 상태는 자동기록 코드나 bridge 업로드 실패가 아니라 DAT가 사용할 수 있는
안경이 아직 등록되지 않은 상태다. 앱의 `Meta AI 연결` 버튼은 이제 Meta AI를
직접 열어 다음 순서를 수행하도록 안내한다.

1. Meta AI 앱에서 Ray-Ban 안경을 같은 iPhone에 페어링한다.
2. Meta AI 설정에서 Developer Mode를 켠다.
3. Kinelo AR의 DAT 등록과 카메라 권한을 승인한다.
4. 안경을 켜고 펼친 상태에서 앱의 `재연결`을 누른다.
5. 앱 로그에서 `registrationState=registered`, 장치 목록 1개,
   `linkState=connected`를 확인한 뒤 라이브 카메라를 시작한다.

Meta 공식 DAT 문서도 Developer Mode, Meta AI companion app, 등록 callback,
권한 승인을 실제 장치 연결의 전제조건으로 설명한다.

- [Meta Wearables DAT iOS setup and registration](https://github.com/facebook/meta-wearables-dat-ios/blob/main/AGENTS.md)
- [Meta Wearables DAT iOS device availability](https://github.com/facebook/meta-wearables-dat-ios/blob/main/AGENTS.md)

### iPhone companion build check — 2026-08-17

The native `RaybanPT` Debug target compiled, signed, installed, and launched on
the paired iPhone 12 Pro as `yk.RaybanPT`. The shared `RaybanPT` scheme now
explicitly includes the `RaybanPTUITests` testable reference. A real-device UI
test reached the XCTest runner but timed out while enabling automation mode;
the phone was unlocked/in use, so this remains a device-state gate. No physical
Ray-Ban media was claimed from this run.

The same scheme's `TextSendUITest/testServerSetupSheet` passed on the available
iOS 26.5 Simulator. This validates the settings-sheet UI contract separately
from the real-device automation gate.

The device inventory also contains a legacy `com.youngkwon.raybanpt` install in
addition to the current `yk.RaybanPT` build. The current `physio_app` launch
link uses the current-only `kineloarhud://` scheme; `carelive://` remains a
backward-compatible scheme for older builds. The physical proof script still
launches `yk.RaybanPT` by bundle ID and reports
`preflight_legacy_bundle=detected`; it does not uninstall anything automatically.

### Official DAT MockDevice transport check — 2026-08-17

The Debug Simulator target now links Meta's official `MWDATMockDevice` and
exercises the real app path: `Wearables` registration → virtual glasses
pairing/power/unfold/don → `DeviceSession` → `Camera.Stream`. The UI test
reaches the app's `프레임 수신 대기 중` state, so the session state transition is
verified without claiming physical hardware.

The available iOS 26.5 Simulator does not deliver the next
`videoFramePublisher` callback. This reproduces Meta's public upstream issue
[#197](https://github.com/facebook/meta-wearables-dat-ios/issues/197), which
also reports failure in Meta's own CameraAccess integration test. The test
therefore records the live-state proof and skips only the frame assertion on
iOS 26.5; it will fail loudly on another runtime if frames still do not arrive.
This does not replace the physical Ray-Ban proof below.

The current Debug build also logs the selected HFP input/current route, the
first received video-frame dimensions, and final audio/video artifact sizes.
These diagnostics are intentionally local-console evidence; they do not log
raw audio or video content.

The transport UI test launches with `-rayban_dat_mock_qa_patient`, which creates
one local `DAT Mock QA` subject only inside the Debug Simulator path. This keeps
the test deterministic when the simulator already contains or lacks persisted
patients; it does not bypass patient selection on a physical device.

The `-glass_hud_autotest` path now exercises the same `VideoRecorder` used by
the live stream. In Demo mode, simulated camera frames are encoded to an MP4
during the HUD context → recording → stop sequence, and the test logs frame
count and artifact size. The latest diagnostic run logged a 2160×4695 source
frame, four frames, and an 18,659-byte MP4. This is repeatable iOS
media-pipeline QA; it must not be labeled as physical Ray-Ban evidence. Audio
still requires the paired glasses' Bluetooth HFP input and is not synthesized
by Demo mode.

For an explicit end-to-end upload check, add
`-glass_media_upload_autotest`. The app uploads the generated MP4 only when a
patient is selected, the bridge API key is configured, and active capture
consent is present. It waits for the completed clinical event, then attaches
that event—not the transport upload-job ID—to the active visit session. The
flag is opt-in and should be used only with a non-PHI QA subject.

When a device launcher consumes arguments beginning with `-`, the same test
flags can be supplied as environment variables:

- `GLASS_DEMO_CONNECTED=1`
- `GLASS_HUD_AUTOTEST=1`
- `GLASS_MEDIA_UPLOAD_AUTOTEST=1`
- `SESSION_AUTO_CAPTURE_AUTOTEST=1`

### Session automatic capture — 2026-08-17

The native companion exposes an opt-in `세션 자동기록` setting. A physio_app
Care Live launch requests this setting after RaybanPT selects the patient;
a manual RaybanPT launch leaves it off until the provider enables it. When a
selected patient has an active visit session and active capture consent, the
setting starts the existing `VideoRecorder` as soon as the glass live stream
starts. It stops and submits the video when the stream/session ends, preserving
the existing draft capture-event and visit-session attachment path. If the
Bluetooth HFP route is available, the same session also starts the guarded
glass-microphone recorder; if HFP is absent or drops, the video continues but
the audio artifact is discarded rather than silently falling back to the
iPhone microphone.

If the Care Live request reaches RaybanPT before the bridge has an active
capture consent, RaybanPT shows the existing patient-consent confirmation
dialog. Only the provider's explicit confirmation records `/consents` on the
bridge and retries automatic capture; cancelling or failing that step leaves
the session unrecorded.

The setting is disabled by default and can be turned off while live. This is
a consent-gated capture convenience, not autonomous clinical recognition or an
automatic clinical write.

Every uploaded capture now carries an explicit provenance source: physical
camera video is `rayban_dat_camera`, physical glasses audio is
`rayban_hfp_microphone`, and Demo/autotest media uses an `ios_demo_*` source.
This prevents synthetic frames from being mistaken for physical Ray-Ban
evidence in downstream review.

The bridge also copies this provenance into transcript candidates created from
audio/video uploads and MediaPipe pose candidates. Idempotent replays backfill
older draft candidates when the trusted source is known.

If the setting was persisted from an earlier launch and the live stream starts
before the patient is selected, selecting the patient now retries the same
consent/visit-session gate and starts automatic capture without requiring a
second stream restart.

If the DAT session stops because the glasses disconnect or the stream reports
an error, the same lifecycle finalizer runs from the stream state transition.
It closes the video/audio artifacts and submits the partial session instead of
leaving an automatic recording open indefinitely. A dropped HFP route still
discards only the audio artifact; it never falls back to the iPhone microphone.

Ending the visit also waits for any hands-free video processing task and active
transcription before closing the visit session, so final media events can still
attach to the correct encounter.

The bridge semantic layer is role-aware for physical therapists, occupational
therapists, Pilates instructors, personal trainers, caregivers, and other
providers. It preserves `provider_role` plus `provider_role_domain` and stages
explicit assessment, instruction, intervention, and safety evidence as drafts;
specific ADL/TUG/fine-motor assessment labels take precedence over broad
movement-screen matches, and it does not infer a diagnosis or finalize a
clinical note.

`SESSION_AUTO_CAPTURE_AUTOTEST=1` exercises this lifecycle with Demo frames and
is not a physical camera or HFP microphone test. Use it only with the same
non-PHI QA subject and explicit consent gate as the media upload autotest.

Latest physical iPhone automatic-capture E2E (2026-08-17) passed with the QA
subject: the app logged `streaming=true recording=true`, captured four Demo
frames, produced an 18,659-byte MP4, and the bridge returned completed/processed
clinical event `24902ba4-92ff-4b11-acea-aa6d38fdf512` plus a `video_evidence`
draft for the encounter. The same run logged `audio=false`; no Ray-Ban
accessory was connected, so this is automatic video lifecycle proof only and
not HFP microphone proof.

### Current physical accessory gate — 2026-08-17

The Debug target previously overrode `META_APP_ID` and `META_CLIENT_TOKEN`
with `0`, even though `Secrets.xcconfig` was attached as the base
configuration. That override is removed now; the built app receives the
non-empty xcconfig values without logging them.

The latest non-Demo launch on the paired iPhone still reports
`ExternalAccessory initialConnectedAccessories count 0`,
`MWDAT ... noEligibleDevice`, and `hasActiveDevice=false`. The installed-app
inventory now confirms Meta AI is installed (`com.facebook.stellaapp`), but the
glasses are not paired/eligible in DAT. Therefore the remaining hardware proof
requires completing the Ray-Ban Meta pairing and DAT permission flow in Meta AI;
no code or bridge change can manufacture this accessory state.

The iOS Simulator separately confirmed the real `kineloarhud://launch` boundary:
after the OS opened the Kinelo AR HUD app, the app displayed the supplied
`DAT DeepLink QA` patient, `automatic capture` session label, and live guided
mode. It then correctly held automatic capture with the visible
`방문 세션을 시작할 수 없어 자동기록을 대기합니다` gate because the simulator
had no configured bridge/active consent. This proves context delivery, not
physical camera or HFP capture.

The latest short proof run (`physical-capture-20260817-061707.log`) recorded
`preflight_meta_ai=detected`, `preflight_target_bundle=detected`, and
`preflight_legacy_bundle=detected`, but still `criteria=0/7`; it did not
produce camera frames or microphone artifacts.

After pairing, rerun the installed Debug build without `GLASS_DEMO_CONNECTED`
and without `SESSION_AUTO_CAPTURE_AUTOTEST`, then start a Care Live session
from physio_app (or use the manual `세션 자동기록` toggle) with a non-PHI QA
subject. Capture the iPhone console,
the HFP route log, the resulting audio/video event IDs, and the physio_app
readback together as one hardware proof bundle.

When Care Live launches RaybanPT, the link now carries the canonical subject
and normalized provider role. `physiotherapist`, `pilates_instructor`, and
`athletic_trainer`/`crossfit_coach` are mapped to the bridge roles
`physical_therapist`, `pilates_instructor`, and `personal_trainer`; unknown
profiles use `other`. This prevents semantic extraction from silently using a
physical-therapy role for every provider.

The repeatable console runner is:

```bash
RAYBAN_DEVICE_UDID=<paired-iPhone-UDID> \
  RAYBAN_PROOF_TIMEOUT_SECONDS=180 \
./server/run_physical_capture_proof.sh
```

Before launching the app, the runner reports `preflight_meta_ai=detected` or
`preflight_meta_ai=not-detected` from the paired iPhone app list. The latter is
an actionable pairing prerequisite, not a capture result.

It intentionally launches the non-Demo app and returns `PHYSICAL_CAPTURE_PROOF=PASS`
only when DAT has advanced beyond unavailable, a real video frame and non-empty
video artifact exist, the selected input route is explicitly a Ray-Ban/Meta
`bluetoothHFP` input, and the HFP recorder has started and produced a non-empty
audio artifact. A generic Bluetooth headset is rejected so it cannot be
misreported as `rayban_hfp_microphone`. It returns `INCOMPLETE` for the current
no-accessory state; Demo/autotest output cannot satisfy these checks.

1. Add the web app through the Meta AI app using the QR code or manual URL.
2. Open the app on the display.
3. Confirm the HUD loads within a few seconds.
4. Use Neural Band / D-pad navigation to move focus:
   - record
   - patient
   - cue
   - history
5. Press Enter on each command and verify the iOS app receives the command through `/glass/command`.
6. Start and stop recording from the HUD.
7. Confirm lens text stays minimal:
   - no full patient identity
   - no raw note text
   - no model output longer than a short cue
8. Confirm errors appear as short “확인 필요” state, with details only in iPhone/bridge logs.
9. Record one short display video from the Meta AI app or MRBD display recording control for regression review.

## Stop Conditions

Stop the device test if any of these occur:

- full patient name or note text appears on the lens
- public URL requires a long-lived production API key
- Vercel preview prompts for login on glasses
- command focus cannot be reached with D-pad
- Enter triggers the wrong command
- iOS DAT recording state diverges from `/glass/state`

## Latest Tooling

See `docs/meta-wearables-tooling-status.md` for the current DAT SDK, Web App plugin, MCP, and upgrade notes.

## Official DAT diagnosis — 2026-08-17

The physical run must distinguish a typed on-glasses update error from a generic
session-start failure. Meta DAT exposes a separate
`datAppOnTheGlassesUpdateRequired` error, but the current RaybanPT console shows
`DeviceSession.start()` → `starting` → `stopped` with
`unexpectedError(description: "Device unavailable")`. The device is registered,
compatible, link-connected, and camera permission is granted. Therefore the
current log does not prove that an update is required, and the absence of an
Update button in Meta AI is expected evidence against that specific diagnosis.

This is consistent with the official `facebook/meta-wearables-dat-ios` reports:
the same healthy registration/compatibility/permission sequence can still fail
before the session reaches `.started`, including in Meta's CameraAccess sample
([discussion #116](https://github.com/facebook/meta-wearables-dat-ios/discussions/116)).
The repository also has Ray-Ban reports where Meta AI says the glasses are up to
date but the on-glasses DAT manifest is not staged, and App Connections has no
Install/Update action ([issue #252](https://github.com/facebook/meta-wearables-dat-ios/issues/252),
[issue #248](https://github.com/facebook/meta-wearables-dat-ios/issues/248)).

The next hardware recovery step is a cold glasses reset: end any broadcast in
Meta AI if shown, close the temples/charging case for approximately 30–60
seconds, reopen, and rerun the capture without force-killing the app mid-session.
The upstream stale-broadcast report identifies this as the reliable recovery
when a previous stream was suspended or killed before `stream.stop()` and
`session.stop()` completed ([issue #231](https://github.com/facebook/meta-wearables-dat-ios/issues/231)).
No manual update action should be assumed unless the app receives the typed
`datAppOnTheGlassesUpdateRequired` error.

## Physical capture proof — PASS — 2026-08-17

Final proof log: `/tmp/rayban-qa-final-proof-2.log`. After correcting two
false-negative HFP patterns in `server/run_physical_capture_proof.sh`, the log
evaluates to `criteria=7/7`.

- Real Ray-Ban DAT session reached `.started`; camera delivered 56 frames at
  `360x640` and produced an MP4 of 825,103 bytes.
- The active input was `BluetoothHFP:Meta RB Display 002G`; the HFP recorder
  produced a WAV of 133,248 bytes.
- Bridge upload completed with outer event
  `63b16ad3-52fd-4326-b1c6-ed17d76b24b2` and clinical event
  `6bba736c-1fdf-453b-865a-8830e27829cd`; the video source is
  `rayban_dat_camera`.
- Encounter-scoped capture readback contains video with
  `capture_origin=rayban_dat_camera`, audio with
  `capture_origin=rayban_hfp_microphone`, and review-gated pose/ROM drafts.

The physical capture edge and bridge contract are proven. Authenticated
physio_app Encounter Room review, approval, SOAP insertion, save, and reload
lineage remain separate clinical UI gates.
