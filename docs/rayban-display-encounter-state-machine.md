# Ray-Ban Display Encounter State Machine

## Purpose

Ray-Ban Meta Display should act as a low-friction PT encounter control surface, not as a full patient chart viewer.

The HUD shows only encounter micro-cards that help the provider move through a visit:

```text
observe -> hypothesize -> test -> intervene -> record
```

The canonical record remains the shared `moai_web` Supabase project. The local bridge and HUD flow stay in `design/dry_run` until the readiness gates in `multimodal-data-contract-and-readiness.md` are met.

## HUD Boundary

Do show:

- short patient context needed for the current encounter
- current session counts
- next-action suggestions
- one candidate extraction at a time
- end-of-encounter summary

Do not show:

- full patient chart pages
- long SOAP notes
- raw transcripts with PHI
- training/evaluation controls
- production write status as if it were complete

## Four States

### 1. Patient Context

Shown at encounter start.

```text
Fixture / Lumbar pain

Last SLR 40 deg
NPRS 7/10

Today:
[ ] Slump
[ ] SLR
[ ] ODI
```

Primary transition:

- gesture: thumb/index tap
- next: `session_mode`

### 2. Session Mode

Main state during treatment.

```text
Observation 2
Test 1
Intervention 0

Recommended:
[ ] Slump
[ ] Neuro screen
[ ] ODI
```

Primary actions:

- voice capture creates a candidate event
- quick add creates an observation, test, or intervention candidate

Primary transition:

- event: candidate generated
- next: `candidate_approval`

### 3. Candidate Approval

Every AI/STT extraction must be treated as a candidate until the provider approves, edits, or discards it.

Example candidate:

```json
{
  "event_type": "test_result",
  "test": "SLR",
  "side": "left",
  "value": "45 degrees",
  "symptom": "posterior thigh pain",
  "source": "rayban_meta_display",
  "status": "candidate"
}
```

Gestures:

- pinch: approve
- swipe right: edit on phone/web
- swipe left: discard

Approved candidates become reviewed clinical facts. Unapproved candidates remain draft and are not gold training labels.

### 4. End Encounter

Shown before completing the visit.

```text
Observation 6
Tests 4
Interventions 3

SOAP Draft Ready
```

Actions:

- complete encounter
- generate SOAP draft
- return to phone/web for detailed edits

## Normalized Event Contract

The HUD adapter should normalize vendor-specific inputs into this internal shape:

```json
{
  "id": "hud-candidate-slr-left-45",
  "encounter_id": "enc-hud-fixture",
  "event_type": "test_result",
  "test": "SLR",
  "side": "left",
  "value": "45 degrees",
  "symptom": "posterior thigh pain",
  "source": "rayban_meta_display",
  "status": "candidate | confirmed_by_provider | discarded",
  "review_status": "auto_extracted | clinician_accepted | clinician_corrected | rejected",
  "captured_at": "timestamp"
}
```

## moai_web Mapping

First-pass mapping:

| HUD concept | moai_web target | Rule |
| --- | --- | --- |
| approved test result | `observations` | `status=final`, `source_type=manual` after provider approval |
| unapproved test result | `observations` | `status=preliminary`, `source_type=ai` only for draft/review queues |
| candidate approval | `clinical_extraction_reviews` | Store proposed and final payloads |
| HUD state/audit | `clinical_events` | Operational trace only |
| SOAP draft | `encounter_notes` | Draft, approval required |

No new permanent Supabase table is required for the first pass.

## Local Proof Command

Run from `server`:

```bash
./.venv/bin/python mlops_harness.py hud-fixture-plan
```

Expected:

- `mode=design/dry_run`
- `safety.writes_supabase=false`
- four HUD states are present
- one provider-approved SLR candidate is normalized
- dry-run plan includes `observations`, `clinical_extraction_reviews`, and `clinical_events`
- no production write occurs

Use `--no-approved` to rehearse an unapproved candidate:

```bash
./.venv/bin/python mlops_harness.py hud-fixture-plan --no-approved
```

The unapproved plan should remain preliminary/draft and must not be treated as gold.
