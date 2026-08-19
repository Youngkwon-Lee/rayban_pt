# Multimodal Data Contract and Readiness Gates

## Why This Exists

`rayban_pt` should become a multimodal capture edge for `physio_app`, not a separate AI database.

The shared `moai_web` Supabase project is the system of record. The local bridge can stage, inspect, retry, and dry-run, but final clinical data, review status, and training lineage should converge into `physio_app`.

## Should We Use Real Data Now?

Use both, but for different jobs.

### Right now: design with synthetic and fixture data

This is best for:

- schema mapping
- agent harness behavior
- write gating
- PHI-safe logging
- taxonomy design
- dry-run validation

Reason: real data is still scarce, and early automation can amplify bad assumptions. Synthetic fixtures let us break and repair the frame cheaply.

### Soon: add a tiny real pilot set

This is best for:

- checking camera angles
- testing actual therapist language
- seeing how often identity resolution fails
- discovering missing label values
- validating whether generic pose models behave acceptably

Recommended size:

- 5 to 10 internal/non-production sessions first
- 20 to 30 clinician-reviewed pilot sessions next
- 50+ reviewed sessions before any serious dataset snapshot
- 200+ reviewed examples per label family before thinking about label-specific model training

### Not yet: automatic training or production sync

Do not enable scheduled sync, model training, or automatic promotion yet.

The current correct state is:

- dry-run by default
- explicit write gate for Supabase
- reviewed-data contract defined
- readiness thresholds visible

## Canonical Multimodal Object

Every captured session should be normalized into one canonical object before writing to `moai_web` or exporting a dataset.

```json
{
  "capture": {
    "source_system": "rayban_pt",
    "source_event_id": "local-event-id",
    "capture_device": "rayban",
    "captured_at": "timestamp",
    "modality": "image | video | audio | text | combined"
  },
  "identity": {
    "organization_id": "uuid",
    "subject_person_id": "uuid",
    "provider_person_id": "uuid",
    "encounter_id": "uuid",
    "physio_client_id": "uuid"
  },
  "consent": {
    "status": "granted | missing | revoked | unknown",
    "scope": "capture_analysis_storage"
  },
  "media": [
    {
      "kind": "raw_image | raw_video | audio | masked_image | sampled_frame",
      "storage_bucket": "bucket",
      "storage_path": "path",
      "content_type": "mime/type",
      "derived_from": "source media id"
    }
  ],
  "signals": {
    "transcript": "text",
    "pose_features": {},
    "visual_findings": {},
    "temporal_features": {}
  },
  "ai_outputs": [
    {
      "type": "soap_draft | label_draft | pose_analysis | summary",
      "model_key": "model-or-rule-name",
      "model_version": "version",
      "prompt_version": "version",
      "output": {},
      "confidence": null
    }
  ],
  "human_review": {
    "status": "unreviewed | reviewed | corrected | approved | rejected",
    "reviewer_person_id": "uuid",
    "reviewed_at": "timestamp",
    "corrections": {}
  },
  "gold": {
    "eligible": false,
    "reason": "needs clinician review"
  }
}
```

## moai_web Table Contract

| Canonical object area | moai_web destination | Notes |
| --- | --- | --- |
| `identity.encounter_id` | `encounters.id` | Session anchor |
| `identity.organization_id` | all clinical target rows | Required for production writes |
| `identity.subject_person_id` | `subject_person_id` columns | Must resolve from `org_clients.person_id` where possible |
| `identity.provider_person_id` | `provider_person_id`, `created_by`, `performer_person_id` | Must be a canonical person id |
| `media.raw_image/video` | `encounter_media` | Store metadata; object bytes live in Storage |
| `media.audio` | `voice_memos` | Store audio path and transcript |
| `signals.transcript` | `voice_memos.transcript_text` / `ai_inference_log.output_snapshot` | Keep source modality trace |
| `signals.pose_features` | `client_media_summaries.structured_findings` / `observations` | Use observations for clinically meaningful facts |
| `ai_outputs` | `ai_inference_log` | All AI draft lineage goes here |
| `human_review` | `clinical_extraction_reviews` / `ai_feedback` | Store corrections and review decision |
| final SOAP | `encounter_notes` | Draft until signed/approved |
| final labels | `observations` / `activity_sessions.metrics` / `encounters.session_type` | Do not create duplicate local-style label tables in Supabase |
| audit | `clinical_events` | Keep source event id in resource metadata |

## Label Taxonomy v0

Therapist quick tags cover the session arc rather than only two exercises:
`safety_check`, `assessment_started`, `assessment_finding`,
`positioning_alignment`, `rom_measurement`, `functional_task`,
`intervention_started`, `movement_correction`, `orthosis_assistive_device`,
`exercise_instruction`, `response_tolerance`, `caregiver_education`,
`reassessment_outcome`, and `home_program`. These tags are timestamped draft
evidence; they do not assert a diagnosis or replace the provider's structured
assessment.

## Local staging implementation

The first bridge slice now materializes the shared time-axis contract in the
local SQLite staging database as `capture_events`. It is intentionally separate
from `events`: `events` remains the media/transcript processing record, while
`capture_events` records evidence references and therapist annotations that can
later be reviewed and projected into `physio_app`.

Each candidate also carries a normalized `action_type`: `observation`,
`assessment`, `instruction`, `intervention`, `reassessment`, `home_program`,
or `safety_check`. A visit session carries `provider_role`, allowing the same
evidence contract to be evaluated across physical therapists, occupational
therapists, Pilates instructors, personal trainers, caregivers, and other
providers without inferring profession from video.

Current endpoints:

- `POST /capture-events` — create a draft media, transcript, pose, or therapist-tag event
- `POST /capture-events/extract` — extract conservative, review-first semantic
  candidates from therapist transcript/text and create idempotent draft events
- `GET /visit-sessions/{session_id}/capture-events` — read the session timeline
- `GET /capture-events?encounter_id=...` — scoped readback for the `physio_app` review surface
- `PATCH /capture-events/{event_id}` — scoped provider review update (`edited`, `approved`, `rejected`)

The iPhone companion automatically creates a draft `capture_events` row after a
completed audio or video event, referencing the existing `source_event_id`.
The bridge now also runs `rayban_transcript_rules_v2` after audio/text/image/video
processing. It creates idempotent draft candidates for safety, assessment,
pose/positioning/ROM, function, tolerance, reassessment, intervention,
movement correction, devices, exercise instruction, caregiver education, and
home-program language. These candidates remain provider-review-only and are
never promoted automatically.
The companion `rayban_capture_semantics_v2` pass preserves explicit structured
fields when the transcript states them: `assessment_type`, `assessment_name`,
`intervention_type`, `instruction_type`, `activity_name`, `core_task`,
`body_position`, `assist_level`, `performance_level`, `tolerance`,
`fatigue_level`, `repetition_count`, `set_count`, `hold_duration_seconds`,
`rest_duration_seconds`, `pain_score`, `rpe_score`, `equipment`,
`instruction_detail`, `compensations`, and `safety_flags`. A visit session's
`provider_role` is retained in the semantic snapshot. These fields are stored
both in the candidate payload and in a versioned `semantic` snapshot; unknown
values are omitted rather than guessed. The vocabulary covers common PT/OT
functional tests, Pilates mat/reformer work, trainer strength/resistance tasks,
and explicit dosage/response cues. It remains transcript/rule evidence, not
autonomous vision-based exercise recognition.
For uploaded video, the bridge now samples the extracted frames through a local
MediaPipe Pose Landmarker and emits additional `source_type=pose` draft
evidence: ROM measurement ranges, left/right angle differences, movement-range
labels, and a pose-quality summary. The output follows the existing
`video_pose_summary` vocabulary in `physio_app`, uses the official
[MediaPipe Pose Landmarker model](https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task),
and caches the model outside the repository. Set `RAYBAN_POSE_MODEL_PATH` for a
managed/offline model asset. A single camera remains a screening measurement;
the bridge never turns an angle or asymmetry into a diagnosis automatically.
During live streaming, the therapist can add timestamped draft tags for
assessment start/finding, intervention, movement correction, exercise
instruction, and home-program instruction. `physio_app` can now read these
events, edit/approve/reject them through the authenticated bridge, and append
approved items to the existing SOAP draft with source lineage. The bridge
projection remains local staging; there is still no direct Supabase projection
from `rayban_pt`. The provider-triggered `physio_app` promotion action can
write approved assessment/measurement evidence to preliminary Observations,
intervention/exercise/education evidence to Procedures, and home-program
evidence to a draft CarePlan proposal, all keyed to the capture event.

### Verification snapshot (2026-08-17)

- Passed: local bridge capture-event smoke test, pose-capture smoke test, bridge
  safety/glass relay/visit-session/MRBD smoke tests, and Swift parse check.
- Passed in `physio_app`: typecheck, production webpack build, 4 focused suites /
  43 tests, and action/API/domain guards.
- Browser proof: the guarded local dev-auth flow entered active fixture
  `9d300000-0000-4000-8000-0000000000ff`; a synthetic event was read, approved,
  inserted into the provider-reviewed A SOAP draft, persisted through the save
  action, and displayed again after refresh in session wrap-up review.
- Pose browser proof: a reviewed `R Knee Flexion 42-128°` pose candidate was
  read in the same Encounter Room timeline, approved, inserted into SOAP O as
  `[Ray-Ban pose] ...`, saved, and found again after a full page reload.
- Real bridge media proof: an MP4 uploaded to `/ingest-video` was processed by
  the local MediaPipe model and produced four `draft` pose events (quality, two
  ROM measurements, and movement evidence) linked to the generated source
  event. Operational frame notes were excluded from transcript semanticization.
- Authenticated staging proof: the fixed physio_app staging alias read a
  provider-role-aware semantic v2 QA session with assessment, intervention,
  activity, dosage, pain/RPE, equipment, and compensation fields. The fixture
  remained draft-only and was not promoted to a clinical record.
- iOS media path: the companion app now keeps the upload-job ID separate from
  the completed clinical event ID when attaching media to a visit session.
  Its HUD auto-test also drives the real `VideoRecorder` over simulated live
  frames and reports the resulting MP4 artifact; this remains Demo proof, not
  a physical Ray-Ban capture.
- Remaining: a real Ray-Ban capture and production Encounter Room promotion
  against a real database encounter.
  Full exercise-name/repetition/compensation classification from vision still
  requires a labeled pilot set and provider-reviewed evaluation; the current
  pose layer deliberately emits measurements/evidence rather than clinical
  conclusions.
- Current worktree gate outside this slice: the repository boundary guard
  reports an explicit-client fallback in
  `external-exercise-adherence.repo.ts`, and `git diff --check` reports one
  unrelated trailing-space line in an existing E2E file.

### Session Labels

| Label | Type | Draft method | Gold condition |
| --- | --- | --- | --- |
| `provider_role` | enum | session context or therapist form | provider confirmed |
| `action_type` | enum | capture candidate or therapist form | provider confirmed |
| `session_type` | enum | therapist form or LLM draft | clinician confirmed |
| `service_domain` | enum | rule default: `physical_therapy` | system confirmed |
| `care_setting` | enum | app context | clinician or workflow confirmed |
| `session_goal` | free text or enum | LLM draft | clinician confirmed |

Suggested `session_type` values:

- `assessment`
- `therapeutic_exercise`
- `neuromotor_training`
- `gait_training`
- `balance_training`
- `caregiver_training`
- `home_exercise_review`
- `other`

`provider_role` separates the practice context for a physical therapist,
occupational therapist, Pilates instructor, personal trainer, caregiver, or
other provider. `action_type` separates what happened in the segment:
`observation`, `assessment`, `instruction`, `intervention`, `reassessment`,
`home_program`, or `safety_check`. These fields are additive pilot metadata;
they do not authorize automatic clinical conclusions.

### Task Labels

| Label | Type | Draft method | Gold condition |
| --- | --- | --- | --- |
| `core_task` | enum | therapist form, LLM, pose/rule | clinician confirmed |
| `custom_task` | string | therapist form when `core_task=other` | taxonomy review or clinician confirmed |
| `body_position` | enum | VLM or pose/rule | clinician confirmed for training |
| `repetition_count` | integer | pose/rule or manual | clinician confirmed or sensor confidence high |
| `hold_duration_seconds` | number | pose/rule or manual | clinician confirmed or sensor confidence high |

Suggested `core_task` values:

- `prone_head_control`
- `sitting_balance`
- `standing_balance`
- `gait_practice`
- `sit_to_stand`
- `reaching`
- `range_of_motion`
- `caregiver_handling`
- `positioning`
- `other`

Suggested `body_position` values:

- `supine`
- `prone`
- `side_lying`
- `sitting`
- `quadruped`
- `kneeling`
- `standing`
- `walking`
- `unknown`

### Assist and Performance Labels

| Label | Type | Draft method | Gold condition |
| --- | --- | --- | --- |
| `assist_level` | enum | therapist form or LLM draft | clinician confirmed |
| `performance_level` | enum | therapist form or LLM draft | clinician confirmed |
| `tolerance` | enum | LLM draft from transcript + therapist form | clinician confirmed |
| `fatigue_level` | enum | LLM draft or therapist form | clinician confirmed |

Suggested `assist_level` values:

- `independent`
- `supervision`
- `standby_assist`
- `contact_guard`
- `minimal_assist`
- `moderate_assist`
- `maximal_assist`
- `dependent`
- `not_tested`

Suggested `performance_level` values:

- `improved`
- `stable`
- `declined`
- `variable`
- `unable`
- `not_observed`

### Movement Quality Labels

These are important but should not become gold without review early on.

### Training Eligibility

Reviewed labels are not automatically training data. A sample should only enter a training/eval dataset when:

- `review_status` is `reviewed`, `corrected`, or `approved`
- `subject_person_id`, `provider_person_id`, `organization_id`, and `encounter_id` are resolved
- `usable_for_training=true` is explicitly set by the reviewer or pilot operator
- any custom task such as `supported_kneeling` is stored as `core_task=other`, `custom_task=supported_kneeling`, and `body_position=kneeling` until the taxonomy is promoted

| Label | Type | Draft method | Gold condition |
| --- | --- | --- | --- |
| `midline_control` | enum | pose/rule + clinician review | clinician confirmed |
| `head_control` | enum | pose/rule + clinician review | clinician confirmed |
| `trunk_control` | enum | pose/rule + clinician review | clinician confirmed |
| `weight_shift_pattern` | enum | pose/rule + clinician review | clinician confirmed |
| `compensations` | multi-select | VLM/LLM draft | clinician confirmed |

Suggested `compensations` values:

- `right_weight_shift`
- `left_weight_shift`
- `trunk_lateral_flexion`
- `excessive_extension`
- `excessive_flexion`
- `shoulder_elevation`
- `pelvic_rotation`
- `caregiver_overassist`
- `none_observed`
- `unknown`

### Safety and Context Labels

| Label | Type | Draft method | Gold condition |
| --- | --- | --- | --- |
| `safety_flags` | multi-select | LLM/VLM/rules | clinician confirmed |
| `pain_observed` | enum | transcript + visual draft | clinician confirmed |
| `caregiver_present` | boolean | transcript/VLM draft | clinician confirmed if clinically used |
| `environment_constraints` | multi-select | VLM/LLM draft | clinician confirmed |

Suggested `safety_flags` values:

- `fall_risk`
- `fatigue`
- `pain`
- `poor_tolerance`
- `unsafe_environment`
- `skin_integrity_risk`
- `respiratory_concern`
- `seizure_precaution`
- `none`
- `unknown`

## Draft vs Gold Rules

### Draft

A label is draft if it comes from:

- LLM or VLM output
- pose/rule inference
- unreviewed transcript
- unreviewed local bridge label

Draft data can be used for:

- UI suggestions
- review queue prioritization
- weak supervision experiments
- error analysis

Draft data should not be used as gold training labels.

### Gold

A label can become gold only when:

- canonical identity is resolved
- consent is valid
- raw or derived media reference is traceable
- clinician review status is explicit
- final value is stored in `observations`, `activity_sessions`, `encounter_notes`, or review tables
- PHI/masking policy is satisfied

## Dataset Readiness Gates

### Gate 0: Frame readiness

Use synthetic or fixture data only.

Requirements:

- canonical object defined
- table mapping implemented
- dry-run works
- logs are PHI-safe by default
- write gate exists

Status: this is where the project is now.

### Gate 1: Pilot readiness

Use a tiny real internal dataset.

Requirements:

- 5 to 10 internal/non-production sessions
- consent path tested
- identity resolution tested
- at least image/video + note/transcript examples
- clinician can correct labels
- no automatic training
- no automatic model deployment

### Gate 2: Review dataset readiness

Use reviewed data for analytics and evaluation.

Requirements:

- 20 to 30 reviewed sessions
- at least 80% identity resolution success
- label completeness report
- clinician correction rate measured
- PHI/masking failures tracked
- no production model promotion

### Gate 3: Training dataset readiness

Use data for early model experiments.

Requirements:

- 50+ reviewed encounters for broad workflow experiments
- 100+ examples for a single stable label family
- 200+ examples per label family for serious classifier training
- stable taxonomy version
- train/eval split fixed
- baseline rules evaluated

### Gate 4: Production candidate readiness

Use trained models or prompts as release candidates.

Requirements:

- offline eval beats baseline
- clinician acceptance rate improves or stays stable
- no safety regression
- latency and failure rate acceptable
- rollback path exists in `ml_model_registry`

## Agent Operating Modes

| Mode | Allowed actions | Blocked actions |
| --- | --- | --- |
| `design` | edit docs, define taxonomy, inspect schema | write to Supabase, train models |
| `dry_run` | build payloads, plan sync, inspect jobs | execute writes |
| `review_required` | enqueue candidates, summarize gaps | mark gold automatically |
| `execute_allowed` | write with explicit env gate and operator intent | automatic promotion |
| `training_allowed` | run eval/training jobs after readiness gates | train on unreviewed data |

Current default mode should be `design` or `dry_run`.

## Pilot Data Collection Template

See `docs/pilot-capture-checklist.md` for the field workflow and `docs/pilot-session-manifest.template.json` for a machine-readable session template.

For each pilot session, capture:

- one canonical `organization_id`
- one canonical `provider_person_id`
- one canonical `subject_person_id`
- one `encounter_id`
- consent status
- at least one media artifact
- transcript or therapist note
- therapist label form
- chart review outcome
- whether AI draft was accepted, corrected, or rejected

## What To Avoid For Now

- recurring sync automation
- automatic model training
- training on unreviewed labels
- creating duplicate Supabase tables for local bridge concepts
- storing raw media bytes in Postgres
- treating `patient_name` as a durable identifier
- evaluating only on AI-generated labels

## Recommended Next Step

Collect a tiny internal pilot set, but keep every agent action in dry-run mode until:

- identity resolution works on real rows
- reviewed labels exist
- the taxonomy has survived a few real sessions
- the team agrees which labels are worth training first
