# Meta Wearables Tooling Status

Last checked: 2026-08-17 UTC.

This project uses two Meta Ray-Ban Display integration paths:

- Native iOS DAT app for camera, audio, session lifecycle, display rendering, consent context, and bridge coordination.
- MRBD Web App HUD at `server/static/glass-webapp/` for lightweight HTTPS-hosted lens UI.

Keep this boundary. Web Apps are good for glanceable UI, D-pad/Neural Band input, IMU/location web APIs, lightweight local storage, on-glasses composer text input, offline app-shell caching, and the glasses back gesture. They still do not own the raw camera/microphone capture or the clinical write path, so capture and clinical workflows stay in the native DAT app and local bridge. The project intentionally does not use lens text input or offline clinical state yet because the clinical context remains phone-mediated and review-gated.

## Phone-free boundary

The current third-party capture path is not phone-free. DAT is a mobile-app SDK: the glasses camera/microphone stream into the paired iOS app, and that app saves and uploads the media. The display Web App can render the HUD and accept Neural Band/D-pad input, but it cannot directly capture camera/microphone media or write into `physio_app` by itself. Meta's official developer guidance also says third-party apps run on a paired mobile device and captured media is delivered to that app in real time rather than stored on the glasses for a later custom import.

- [Official DAT iOS repository](https://github.com/facebook/meta-wearables-dat-ios)
- [Meta developer discussion on paired-mobile and real-time media delivery](https://github.com/facebook/meta-wearables-dat-android/discussions/20)

## Current Local Baseline

- `RaybanPT` pins `meta-wearables-dat-ios` to `0.9.0`.
- DAT analytics collection is explicitly opted out through `MWDAT.Analytics.OptOut=true` in the app `Info.plist`.
- `server/static/glass-webapp/` follows the MRBD Web App model:
  - fixed 600 x 600 viewport
  - black page background for additive-display transparency
  - dark-gray visible UI surfaces
  - D-pad/Enter/Escape key handling
  - PNG favicon and Web App Manifest
  - Vercel-compatible static server
- Web App and native-device command delivery are separated:
  - `/glass/command` is the Web App/server-state queue.
  - `/glass/device-command` is the paired iPhone camera/audio queue.
  - Both queues are FIFO to avoid cross-consumer command loss.
- Local Codex plugin cache remains `meta-wearables-webapp` `125.0.0`; upstream toolkit is
  `v127`. The local app does not depend on the new text-input/offline skills, so refresh
  the plugin separately when those capabilities are intentionally adopted.

## Latest Official Tooling

- Latest iOS DAT tag found: `0.9.0` (`2026-08-03`).
- Official DAT repo: `https://github.com/facebook/meta-wearables-dat-ios`.
- Official Web App toolkit repo: `https://github.com/facebookincubator/meta-wearables-webapp`.
- Official Web App toolkit currently publishes plugin update `v127`, including on-glasses text composer, offline app-shell, back gesture, and optional pinch-drag guidance.
- Public docs context: `https://wearables.developer.meta.com/llms.txt?full=true`.
- Public remote MCP server: `https://mcp.developer.meta.com/wearables`.

The MCP server does not require authentication and exposes:

- `search_dat_docs`
- `search_webapps_docs`

Example JSON-RPC probe:

```bash
curl -sS https://mcp.developer.meta.com/wearables \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## DAT 0.9.0 Delta

New support worth tracking:

- Consolidated camera lifecycle: `DeviceSession.addCamera(config:)` returns a `Camera`, with the child `camera.stream` and `camera.stop()`.
- Display `ButtonGroup` component.
- `DeviceType.supportsDisplay` capability predicate.
- Camera Access background recording improvements.

Breaking changes handled in this repo:

- `StreamViewModel` now uses `DeviceSession.addCamera(config:)`, stores the owning `Camera`, and calls `camera.stop()` so the camera lifecycle owns its stream.
- `Stream.start()` and `Stream.stop()` remain synchronous.
- `Display.start()` and `Display.stop()` remain synchronous.
- `DeviceSession.addDisplay` remains the native HUD attachment point.

Local code updates applied for `0.9.0`:

- Updated `RaybanPT/RaybanPT/StreamViewModel.swift` camera ownership and stop path.
- Updated `RaybanPT.xcodeproj` package requirement and `Package.resolved` from `0.8.0` to `0.9.0`.

### MockDeviceKit feasibility probe — 2026-08-17

The official `MWDATMockDevice` product is present in the DAT 0.9.0 package,
and is now linked only in the Debug Simulator target. The app can pair,
power on, unfold, don, register, select, and start a virtual Ray-Ban through
the real `Wearables`/`DeviceSession`/`Camera.Stream` path. The UI proof reaches
`프레임 수신 대기 중`, which confirms the session state transition.

The remaining frame assertion is currently blocked by Meta's upstream
`videoFramePublisher` issue on the installed iOS 26.5 Simulator: the callback
does not fire even though the stream reaches `.streaming`, and Meta's own
CameraAccess integration test reports the same behavior. Duplicate internal
media-class warnings are emitted by the SDK composition but do not crash the
app. This is an SDK/simulator integration gate, not evidence that physical
camera capture is implemented by the mock.

The official MockDeviceKit path remains useful for session/lifecycle tests and
for future frame tests when Meta ships a compatible simulator binary. See the upstream
[MockDeviceKit documentation](https://github.com/facebook/meta-wearables-dat-ios/blob/main/AGENTS.md)
and the reported [iOS 26.5 MockDevice frame-delivery issue](https://github.com/facebook/meta-wearables-dat-ios/issues/197).

## Web App v127 delta

The official Web App toolkit now documents these capabilities in addition to the original 600×600 additive-display contract:

- standard HTML text controls can open the on-glasses handwriting/voice composer after focus + tap;
- Service Worker + Cache API can provide an offline app shell;
- thumb + middle-finger back gesture is the device back action, with Escape as the desktop test equivalent;
- EMG pinch activates the focused element, while continuous drag is opt-in through `touch-action: none`.

`server/static/glass-webapp/` intentionally remains a short, online bridge-command HUD. It has no text field or service worker, and its offline state means bridge transport unavailable rather than a local clinical cache.

Use these proof commands after DAT-related changes:

```bash
cd /Users/youngkwon/projects/rayban_pt/RaybanPT
xcodebuild -project RaybanPT.xcodeproj \
  -scheme 'RaybanPT (Demo)' \
  -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' \
  build
```

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python mrbd_hud_smoke_test.py
./.venv/bin/python visit_session_smoke_test.py
./.venv/bin/python smoke_test.py
```

## AI/CLI Setup Notes

DAT iOS Codex plugin install path from the official repo:

```bash
git clone https://github.com/facebook/meta-wearables-dat-ios.git
cd meta-wearables-dat-ios
codex plugin install ./plugins/mwdat-ios
```

Web App plugin marketplace path:

```bash
codex plugin marketplace add https://github.com/facebookincubator/meta-wearables-webapp
codex plugin marketplace upgrade meta-wearables
```

Current local caveat: `/opt/homebrew/bin/codex` is an npm wrapper for `@openai/codex@0.118.0`, but it currently fails with an `ENOENT` for its vendored binary. The active Codex app session still has the Meta Web App plugin loaded, but terminal-based `codex plugin ...` commands need the local CLI install repaired before use.
