# Neural Band Integration

`Kinelo AR` already supports the primary on-glass HUD interaction path.

Use the neural-band path only when a separate external gesture source must trigger the same recording flow.

The current fallback operating pattern is:

`neural-band gesture -> companion bridge -> /neural-band/event -> /glass/command -> Kinelo AR HUD action`

Device actions are now separated from the Web App command queue. The native
iPhone app consumes `/glass/device-command`, while the Web App consumes
`/glass/command`. Both queues are FIFO, so a Web App poll cannot swallow a
camera or microphone command and rapid inputs do not overwrite one another.

## Current command path

- The local bridge accepts neural-band gestures at `POST /neural-band/event`.
- Supported gestures currently map to recording, the current HUD `primary_action`, or a specific HUD workflow action.
- Recording toggle gestures:
  - `tap`
  - `single_tap`
  - `double_tap`
  - `press`
  - `squeeze`
  - `photo`
  - `capture_photo`
  - `camera`
  - `snapshot`
  - `voice`
  - `audio`
  - `stt`
  - `start_audio`
  - `stop_audio`
  - `stop_voice`
  - `toggle_recording`
- Native capture gestures:
  - `photo`, `capture_photo`, `camera`, `snapshot`
  - `voice`, `audio`, `stt`, `start_audio`, `stop_audio`, `stop_voice`
- Primary action gestures:
  - `down`
  - `swipe_down`
  - `downward`
  - `select`
  - `enter`
  - `confirm`
  - `open`
  - `primary_action`
- Patient selection gestures:
  - `patient`
  - `select_patient`
  - `patient_select`
- History gestures:
  - `history`
  - `records`
  - `open_history`
- Recommended assessment gestures:
  - `recommend`
  - `recommendations`
  - `assessment`
  - `evaluation`
  - `show_recommendations`
- The Web App consumes `/glass/command` and resolves its own navigation/state actions.
- The iPhone app polls `/glass/device-command` for device-local actions. Recording state transitions are sent as explicit `start_recording` or `stop_recording` commands; photo and audio commands are not executed by the Web App.
- Direct HUD workflow actions open patient selection, capture history, or the recommended assessment checklist.

## Quick test

Use the built-in helper:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
python3 send_neural_band_event.py double_tap \
  --base-url http://127.0.0.1:8791 \
  --api-key "$(cat .bridge_api_key)"
```

Or call the bridge directly:

```bash
curl -X POST http://127.0.0.1:8791/neural-band/event \
  -H "x-api-key: $(cat /Users/youngkwon/projects/rayban_pt/server/.bridge_api_key)" \
  -H "Content-Type: application/json" \
  -d '{"gesture":"double_tap","device_id":"band-01"}'
```

## Companion bridge

When the neural-band vendor gives us BLE events, webhook callbacks, OSC, or a desktop/mobile SDK, connect it to:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
python3 neural_band_bridge.py serve \
  --bridge-base-url http://127.0.0.1:8791 \
  --api-key "$(cat .bridge_api_key)"
```

This starts a local adapter at `http://127.0.0.1:8793`.

Forward a gesture into the adapter:

```bash
curl -X POST http://127.0.0.1:8793/gesture \
  -H "Content-Type: application/json" \
  -d '{"gesture":"double_tap","device_id":"band-01","metadata":{"session":"pilot"}}'
```

## Other input modes

One-shot send:

```bash
python3 neural_band_bridge.py send double_tap \
  --bridge-base-url http://127.0.0.1:8791 \
  --api-key "$(cat .bridge_api_key)"
```

STDIN relay:

```bash
python3 neural_band_bridge.py stdin \
  --bridge-base-url http://127.0.0.1:8791 \
  --api-key "$(cat .bridge_api_key)"
```

Then pipe gesture lines such as:

```text
double_tap
down
{"gesture":"press","device_id":"band-01","metadata":{"strength":"high"}}
```

## Direct iPhone deep link mode

`Kinelo AR` now accepts neural-band commands directly through the app URL scheme.

This is useful when a companion app, iPhone Shortcut, or vendor mobile SDK can open URLs but does not want to depend on the bridge polling path.

Example:

```text
kineloar://neural-band?gesture=double_tap&device_id=band-01&patient_name=PilotDemo&session_type=neuromotor_training&subject_person_id=person-demo-9cc23e42&physio_client_id=client-demo-9cc23e42&physio_session_id=enc-demo-9cc23e42
```

Current behavior:

- Supported gestures map to the local recording toggle, the current HUD primary action, or a direct HUD workflow action:
  - `tap`
  - `single_tap`
  - `double_tap`
  - `press`
  - `squeeze`
  - `toggle_recording`
  - `down`
  - `swipe_down`
  - `select`
  - `enter`
  - `primary_action`
  - `patient`
  - `select_patient`
  - `history`
  - `records`
  - `recommend`
  - `assessment`
  - `evaluation`
- If `subject_person_id`, `physio_client_id`, or `physio_session_id` are present, the app stores them into the same pilot identity context used by capture.
- If `patient_name` or `session_type` are present, the guided session context is primed before recording.
- The app posts the same local recording, primary-action, patient-selection, history, or recommendation event used by the glass HUD and bridge command queue.

Quick simulator test:

```bash
xcrun simctl openurl booted \
  'kineloar://neural-band?gesture=press&subject_person_id=person-neural-xyz&physio_client_id=client-neural-xyz&physio_session_id=session-neural-xyz&patient_name=NeuralPilot&session_type=band_test'
```

Practical iPhone Shortcut setup:

1. Create a new Shortcut.
2. Add `Open URLs`.
3. Paste a `kineloar://neural-band?...` URL.
4. Trigger it from an automation, widget, Back Tap, or a companion app.

## Recommended next integration

Pick one real neural-band source and wire it into `neural_band_bridge.py`:

1. iPhone SDK callback
2. macOS companion app callback
3. BLE event reader
4. Vendor webhook or local websocket

The safest first real integration is a companion process that converts vendor gesture events into `POST /gesture` on port `8793`.
