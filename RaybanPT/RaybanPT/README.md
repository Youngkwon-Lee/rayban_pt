# rayban_pt

Ray-Ban Meta -> Local Bridge iOS adapter prototype.

## Included
- `BridgeClient.swift`: bridge API client (`/ingest`, `/ingest-upload`, `/events/{id}`)
- `StatusModel.swift`: adapter state + user-facing error mapping
- `AdapterViewModel.swift`: async flow orchestration
- `M2_TestView.swift`: SwiftUI test screen (text + audio upload)

## Quick use
1. Add files to your iOS target in Xcode.
2. Render `M2_TestView()` from app entry.
3. Set base URL in `M2_TestView` init if needed.
4. Open server settings from the floating `서버` button and run the bridge/device E2E checklist before field testing.

## Server URL 설정
앱 최초 실행 후 Settings에서 Bridge URL을 입력하거나, `UserDefaults`의 `bridge_base_url` 키에 직접 설정.

```
예: http://YOUR_SERVER_HOST:8791
```

## Secrets 설정 (빌드 전 필수)
`RaybanPT/Secrets.xcconfig.example`을 `Secrets.xcconfig`로 복사 후 값 채우기:
```
META_APP_ID = YOUR_META_APP_ID
META_CLIENT_TOKEN = YOUR_META_CLIENT_TOKEN
BRIDGE_BASE_URL = http://YOUR_SERVER_HOST:8791
```
