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

## Current endpoint (example)
`http://YOUR_SERVER_HOST:8791`
