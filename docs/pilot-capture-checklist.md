# Pilot Capture Checklist

## Purpose

Use this checklist for the first 5 to 10 internal pilot sessions.

The goal is not to train a model yet. The goal is to verify that `rayban_pt` captures multimodal rehab data in a shape that can later become reviewed `physio_app` data.

Current operating mode:

- `design`
- `dry_run`
- no scheduled sync
- no automatic training
- no production write automation

## Pilot Session Mix

Collect a small but intentionally varied set.

Recommended first 10 sessions:

| Count | Scenario | Why |
| --- | --- | --- |
| 2 | clean text-only therapist notes | baseline SOAP and label extraction |
| 2 | audio + therapist note | transcript quality and note grounding |
| 2 | image/video + therapist note | visual artifact mapping and masking |
| 2 | combined image/video + audio/text | multimodal object shape |
| 1 | low-quality capture | failure mode documentation |
| 1 | identity or consent edge case | safety and blocking behavior |

Do not optimize the pilot set to look good. A slightly messy set is more useful.

## Before Capture

Required:

- `organization_id` is known
- `provider_person_id` is known
- `subject_person_id` or `physio_client_id` is known
- `encounter_id` is known or intentionally created for the session
- consent path is checked
- capture device is recorded as `rayban`, `iphone`, or `web`
- when using Ray-Ban audio, the selected input route is named Ray-Ban/Meta;
  generic Bluetooth HFP inputs are not accepted as Ray-Ban microphone evidence
- planned task is selected

Block capture if:

- consent is missing for real patient data
- subject identity cannot be resolved for production data
- unmasked face/video storage policy is unclear
- session is not appropriate for internal pilot use

## During Capture

Capture at least one of:

- text note
- audio note
- image
- short video

For multimodal sessions, prefer:

- one image or short video showing setup/body position
- one short spoken note from the therapist
- one final therapist label/review form

### Goniometer reference measurement (egocentric ROM validation)

Egocentric (glasses-worn, moving camera) pose accuracy has no published
validation, so lens-derived angles stay research candidates until checked
against goniometry (see `vision-to-soap-research-and-v1-architecture.md`).
In at least 3 of the 10 pilot sessions:

- pick 1-2 movements that the video captures (e.g. knee flexion, shoulder
  flexion) and measure the same motion with a goniometer at the same moment
- speak the goniometer value into the audio note ("goniometer right knee
  flexion 95 degrees") so it lands in the transcript with a timestamp
- record camera framing (subject fully in frame? sagittal or oblique?) —
  out-of-plane error is the known failure mode
- later compare `MET.ROM.*` pose values against the goniometer reference;
  a per-metric error summary decides whether lens angles ever leave
  research-candidate status

### Labeling link capture

For every captured media event, note the `visit_session_id` and event id
shown by the bridge (or keep the session manifest) — the label importer
needs them to fill `docs/pilot-clip-links.template.json` when clips come
back from the labeling workbench.

Recommended spoken note format:

```text
Session type: neuromotor training.
Task: prone head control.
Position: prone.
Assist: minimal assist.
Performance: improved.
Observed: maintained head lift for 20 seconds across 7 attempts.
Compensation: right weight shift when fatigued.
Safety: fatigue, no pain reported.
Plan: continue head control and caregiver cueing next visit.
```

## Therapist Label Form v0

Use these labels during the pilot. Avoid adding new values casually; put missing values in `notes` and review taxonomy later.

The bridge exposes the current pilot options at:

```bash
curl -H "X-API-Key: $BRIDGE_API_KEY" \
  "http://127.0.0.1:8000/label-taxonomy"
```

### Required Fields

| Field | Example |
| --- | --- |
| `provider_role` | `physical_therapist` or `pilates_instructor` |
| `action_type` | `assessment`, `instruction`, or `intervention` |
| `session_type` | `neuromotor_training` |
| `core_task` | `prone_head_control` |
| `custom_task` | `supported_kneeling` when `core_task=other` |
| `body_position` | `prone` |
| `assist_level` | `minimal_assist` |
| `performance_level` | `improved` |
| `review_status` | `reviewed` |

### Optional But Useful Fields

| Field | Example |
| --- | --- |
| `repetition_count` | `7` |
| `hold_duration_seconds` | `20` |
| `tolerance` | `fair` |
| `fatigue_level` | `mild` |
| `compensations` | `right_weight_shift` |
| `safety_flags` | `fatigue` |
| `caregiver_present` | `true` |
| `label_confidence` | `0.95` |
| `usable_for_training` | `false` until explicitly approved |
| `notes` | free text |

## Review Checklist

After capture, a clinician or operator should mark:

- AI draft accepted, corrected, rejected, or not generated
- SOAP sections complete enough for review
- labels are correct or corrected
- media is usable
- masking/PHI policy passed
- identity resolution passed
- consent status is valid

## Agent Dry-Run Checklist

Run from:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
```

Inspect readiness:

```bash
./.venv/bin/python mlops_harness.py doctor
./.venv/bin/python mlops_harness.py readiness-report --status processed --limit 50
```

Inspect queued candidates:

```bash
./.venv/bin/python mlops_harness.py inspect-sync-jobs --status pending
```

Build a dry-run plan:

```bash
./.venv/bin/python mlops_harness.py sync-pending --limit 5
```

For a specific event:

```bash
./.venv/bin/python mlops_harness.py event-plan <event_id>
```

Expected result:

- `operations` contains the intended target tables
- `skipped` explains missing identity/review/media fields
- no note text or patient name appears in default agent logs
- `identity_resolution.status` is visible
- no Supabase write happens

## Bridge Readiness Checks

Keep `PILOT_CAPTURE_MODE=false` while designing schemas and running dry-runs. Turn it on only for controlled internal pilot capture; when enabled, `/ingest` rejects non-command events unless canonical pilot metadata is present.

Required pilot ingest metadata:

- `patient_name`
- `owner_org_id`
- `owner_provider_person_id`
- `subject_person_id` or `physio_client_id`
- `physio_session_id`

The local bridge UI has a `Pilot Identity Context` block. Fill it before capture; new text/audio events automatically include those values, and the recent-events table shows identity completeness.

The same UI also has a `Consent` block. Before pilot capture, check or record consent for the patient name, then use `Pilot Dry-run` on the captured event to review readiness and the moai write plan without writing to Supabase.

For a no-data rehearsal, run:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python mlops_harness.py pilot-fixture
```

Expected: `usable_for_schema_eval=true`, `writes_supabase=false`, and gold remains blocked unless canonical subject identity, reviewed labels, and `usable_for_training=true` are all present.

Inspect the machine-readable pilot state:

```bash
curl -H "X-API-Key: $BRIDGE_API_KEY" \
  "http://127.0.0.1:8000/events/<event_id>/pilot-manifest?resolve_identity=true"
```

Inspect just the gate outcome:

```bash
curl -H "X-API-Key: $BRIDGE_API_KEY" \
  "http://127.0.0.1:8000/events/<event_id>/pilot-readiness?resolve_identity=true"
```

## What Makes A Pilot Session Usable

A pilot session is usable for schema/readiness evaluation when:

- local event exists
- canonical identity is present or the reason for missing identity is known
- at least one modality artifact exists
- therapist label form exists
- review outcome exists
- dry-run plan can be generated
- skipped fields are understandable

It is usable for future gold dataset only when:

- consent is valid
- identity is resolved
- clinician review is explicit
- labels are corrected or approved
- PHI/masking policy is satisfied
- media/transcript/note references are traceable

## Pilot Retrospective Questions

After every 5 sessions, answer:

- Which labels were hard to choose?
- Which labels needed new values?
- Which values were often `unknown`?
- Did identity resolution fail?
- Did media capture produce usable pose/body context?
- Did the therapist have to rewrite the SOAP note heavily?
- Which modality gave the strongest signal?
- Which fields should be required before sync?

## Stop Conditions

Pause the pilot and revise the frame if:

- more than 30% of sessions cannot resolve identity
- consent state is ambiguous
- masking/PHI policy is unclear
- therapists frequently need label values not in taxonomy
- generated notes routinely invent unsupported details
- media is often not usable for the intended clinical task

## Pilot Exit Criteria

Move from Gate 1 to Gate 2 only when:

- 20 to 30 reviewed sessions exist
- identity resolution succeeds on at least 80%
- label completeness report is available
- clinician correction rate is measured
- PHI/masking failures are tracked
- the team agrees which label families are stable enough for evaluation
