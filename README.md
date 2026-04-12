# rayban_pt

Ray-Ban Meta → Local Bridge iOS adapter prototype.

`rayban_pt`는 Ray-Ban/iPhone 입력을 로컬 브리지로 보내고, STT/마스킹/SOAP 처리 결과를 조회하는 iOS 측 어댑터 코드입니다.

## What this repo is

- iOS client-side adapter (SwiftUI)
- Bridge API client for:
  - `POST /ingest` (text)
  - `POST /ingest-upload` (audio/image/video multipart)
  - `GET /events/{event_id}` (async result polling)
- Minimal test UI for end-to-end verification

## What this repo is NOT

- Full production app
- Direct complete replacement for Meta official SDK sample apps
- Backend server implementation (that lives in `rayban-local-bridge`)

## Architecture (high-level)

Ray-Ban / iPhone capture
→ `rayban_pt` (BridgeClient / ViewModel)
→ Tailscale endpoint (`:8791`)
→ local bridge server (`:8790`)
→ STT + redaction + intent + SOAP

## Included files

- `BridgeClient.swift`
  - HTTP client for text upload / media upload / event polling
- `StatusModel.swift`
  - Adapter state enum + user-facing error mapping
- `AdapterViewModel.swift`
  - Async orchestration (`accepted → processing → done/error`)
- `M2_TestView.swift`
  - SwiftUI test screen (text send + file pick + upload)

## Quick start (Xcode)

1. Create/open an iOS SwiftUI app project.
2. Add the 4 Swift files from this repo to your target.
3. Set app entry view to `M2_TestView()`.
4. Configure `baseURL` in `M2_TestView`.
5. Build & run on simulator/device.

## Example endpoint

- Public via Tailscale Serve: `http://YOUR_SERVER_HOST:8791`
- Internal mapping: `8791 -> 127.0.0.1:8790`

## Required iOS notes

- ATS/HTTP policy may block plain HTTP. Use either:
  - HTTPS endpoint, or
  - ATS exception for trusted development endpoint.
- For local network prompts, ensure plist permissions are correctly set when needed.

## Validation checklist

- [ ] Text send returns `ack=기록완료`
- [ ] Media upload returns `status=accepted`
- [ ] Polling returns `status=done`
- [ ] Event is visible in backend `events` + `soap_notes`

## Privacy stance

- Prefer temporary local media handling on device
- Keep raw storage minimal on server
- Use server-side PHI masking/redaction

## Roadmap (short)

- [ ] Show full SOAP (S/O/A/P) directly in app UI
- [ ] Expose `event_id` and retry UX
- [ ] Add recent-events quick lookup
- [ ] Integrate official Meta Wearables SDK session manager module

## License

TBD (set before wider public distribution)
