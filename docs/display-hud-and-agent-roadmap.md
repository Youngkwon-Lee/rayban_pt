# Meta Ray-Ban Display HUD and Agent Roadmap

`rayban_pt` should treat Meta Ray-Ban Display as a small clinical HUD, not as the clinical system of record. The native iOS DAT path remains responsible for camera, audio, session lifecycle, consent context, upload, and local bridge coordination.

## Current Target: MRBD HUD

The display web app at `server/static/glass-webapp/` is the lightweight display layer.

Design constraints:

- 600 x 600 viewport
- black page background for additive transparency
- dark-gray UI surfaces for readable panels
- D-pad / Neural Band focus navigation
- no touch or free cursor assumptions
- minimal PHI on lens
- no heavy frameworks, external fonts, or continuous animations

Safe lens content:

- patient alias or initials, not full identity
- recording state
- session timer
- bridge readiness state
- upload/analyze/success/error status
- short therapist cue or recommendation

Commands should stay narrow:

- `toggle_recording`
- `select_patient`
- `show_recommendations`
- `open_capture_history`
- `primary_action`

The HUD talks to the bridge through:

- `GET /glass/state`
- `POST /glass/command`

## Native DAT Boundary

Keep these in the iOS app:

- Meta DAT session attach/start/stop
- camera stream and capture
- microphone/audio capture
- patient/session context
- consent checks
- upload to bridge
- polling and review UI

The Display Web App should not own raw media, clinical note text, long summaries, service keys, or model credentials.

## Future Agent Layer

VisionClaw-style systems show a useful pattern:

```text
glasses camera/audio
  -> native DAT app
  -> low-rate vision/audio stream
  -> realtime AI session
  -> guarded tool/action router
  -> explicit confirmation for high-risk actions
```

For `rayban_pt`, an agent layer should be added only after pilot capture and review gates are stable.

The first gateway is intentionally dry-run only:

- `POST /agent/cue-dry-run`
- allowlisted tool: `generate_session_cue`
- optional HUD update: `update_glass=true`
- rejects raw transcript/note fields through the request schema
- redacts PHI-like text before creating lens output
- never writes clinical records, messages patients, trains models, or promotes models

Candidate architecture:

```text
Ray-Ban Meta DAT iOS app
  -> local bridge realtime session
  -> clinical agent gateway
  -> allowed tools:
       - generate session cue
       - summarize recent capture state
       - suggest label draft
       - prepare chart draft
       - ask therapist for confirmation
  -> blocked tools by default:
       - production Supabase writes
       - patient messaging
       - billing
       - deletion
       - model training or promotion
```

Agent safety requirements:

- clinician review remains mandatory for clinical conclusions
- raw PHI media does not leave approved storage paths
- tool calls are allowlisted by operation and context
- writes remain dry-run until explicit gates open
- high-risk actions require human confirmation outside the lens
- every agent action records audit metadata
- display output is short, non-diagnostic, and PHI-minimized

## Milestones

1. MRBD HUD compliance: 600x600, D-pad focus, PHI-minimized display.
2. Device testing: HTTPS hosted preview, QR/deep link setup, Neural Band command verification.
3. Pilot capture: consent, capture, label, readiness, dry-run write plan.
4. Realtime cue prototype: native DAT sends low-rate non-PHI/session-safe context to a local agent gateway.
5. Guarded agent tools: cue generation and label draft only.
6. Clinical review integration: therapist approves/corrects before any gold dataset or moai write path.
