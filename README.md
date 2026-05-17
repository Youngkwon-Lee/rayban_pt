# rayban_pt

## Where to develop (important)

- **Main iOS app code**: `RaybanPT/RaybanPT/`
  - Example: `RaybanPT/RaybanPT/DeviceSessionManager.swift`
- **Xcode project**: `RaybanPT/RaybanPT.xcodeproj`
- **Bridge server (local test)**: `server/`
- Top-level `*.swift` files are lightweight adapter/test files kept for compatibility.

A general-purpose Ray-Ban/iPhone capture-to-note adapter for a local bridge pipeline.

`rayban_pt` focuses on reliable client-side ingestion (text/audio/image/video) and async result polling,
so backend processors (STT, redaction, summarization, SOAP or other formats) can be swapped per domain.

## What this repo is

- iOS adapter prototype (SwiftUI)
- Domain-agnostic upload + polling client
- Minimal test UI for end-to-end verification

## Core capabilities

- `POST /ingest` (text)
- `POST /ingest-upload` (audio/image/video multipart)
- `GET /events/{event_id}` (async processing status/result)

## Included files

- `BridgeClient.swift` — network client for ingest/upload/poll
- `StatusModel.swift` — adapter state + error mapping
- `AdapterViewModel.swift` — async flow orchestration
- `M2_TestView.swift` — SwiftUI test screen

## Quick start (Xcode)

1. Create/open an iOS SwiftUI app.
2. Add the 4 Swift files from this repo to your target.
3. Set app entry view to `M2_TestView()`.
4. Configure `baseURL` in `M2_TestView`.
5. Build & run on simulator/device.

## Example endpoint

- Public (via Tailscale Serve): `http://YOUR_SERVER_HOST:8791`
- Internal mapping example: `8791 -> 127.0.0.1:8790`

## Notes

- Backend output format is pluggable (SOAP optional).
- ATS/network policy on iOS may require HTTPS or explicit development exceptions.
- Keep secrets/tokens out of source control.

## Validation checklist

- [ ] text upload returns 2xx + ack
- [ ] media upload returns `accepted`
- [ ] polling returns `done` or `error`
- [ ] event is present in backend storage

## Device E2E checklist

Use the iOS app server settings sheet before a field test:

- [ ] `/health` succeeds and DB is ok
- [ ] API key is entered in the app
- [ ] patient consent is required by the bridge
- [ ] original file downloads are disabled
- [ ] unmasked image storage is blocked
- [ ] select a patient and record consent
- [ ] send one text note and confirm chart creation
- [ ] send one camera image or video and confirm result
- [ ] confirm face-not-detected or masking-failed errors are understandable
- [ ] review chart list, label, delete, and audit log flows

## Bridge safety smoke test

Run the local API safety checks without touching the real bridge database:

```bash
server/.venv/bin/python server/smoke_test.py
```

This verifies API key enforcement, patient consent gating, PHI redaction, chart access/update, chart review queue mark/clear, label round-trip, fail-closed image masking, disabled file downloads, audit validation, consent revocation, and event deletion.

## License

MIT
