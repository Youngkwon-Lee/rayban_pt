# RaybanPT -> moai_web Mapping

This document maps the local `rayban_pt` bridge schema to the existing `moai_web` Supabase schema.

## Goal

Avoid copying the local SQLite schema into Supabase as-is.
Use existing `moai_web` tables wherever possible, and keep local bridge tables only as transient staging.

## Local Source Tables

`rayban_pt` local bridge tables:

- `events`
- `soap_notes`
- `rehab_labels`
- `chart_reviews`
- `patient_consents`
- `audit_logs`

## Recommended Target Tables in moai_web

Primary target tables:

- `encounters`
- `encounter_media`
- `voice_memos`
- `client_media_summaries`
- `encounter_notes`
- `observations`
- `activity_sessions`
- `clinical_extraction_reviews`
- `patient_consent_records`
- `clinical_events`
- `ai_inference_log`

## Mapping Principles

1. `encounters` is the session/visit anchor.
2. Raw media should go to `encounter_media` and `voice_memos`, not into a generic event table.
3. Final clinical narrative belongs in `encounter_notes`.
4. Structured clinical facts belong in `observations`.
5. AI draft, evaluation, and review metadata belong in `ai_inference_log`, `client_media_summaries`, and `clinical_extraction_reviews`.
6. Local bridge tables should remain staging-only unless there is a clear gap in `moai_web`.

## Entity Mapping

### 1. `events`

Local meaning:

- Mixed staging record for text/audio/image/video ingest
- Includes source, raw text, patient name, org/provider scope, and optional local physio ids

Target:

- `encounters`
- `encounter_media`
- `voice_memos`
- `clinical_events`

Recommended split:

- Session-level metadata -> `encounters`
- Image/video file record -> `encounter_media`
- Audio file + transcript -> `voice_memos`
- Request/process/audit trail -> `clinical_events`

Field mapping:

| Local `events` field | moai_web target | Notes |
| --- | --- | --- |
| `id` | staging only or external reference in metadata | Do not force as primary key in `moai_web`; keep in `metadata.source_event_id` |
| `source` | `encounters.source_system` / `clinical_events.event_subtype` | Example: `iphone`, `rayban`, `merged` |
| `event_type` | `encounter_media.media_type`, `voice_memos`, or metadata | Split by modality |
| `raw_text` | `voice_memos.transcript_text`, `encounter_notes.ai_draft_snapshot`, `client_media_summaries.summary_text` | Depends on modality |
| `intent` | metadata only | Can be logged in `clinical_events` or `ai_inference_log.output_type` |
| `status` | `encounters.status`, `encounter_media.analysis_status`, `voice_memos.processing_status` | Modality-specific |
| `patient_name` | resolve to `persons` / `org_clients` / `subject_person_id` | Do not store as free text long-term if person id can be resolved |
| `owner_org_id` | `organization_id` | Direct mapping |
| `owner_provider_person_id` | `provider_person_id` in `encounters`, `voice_memos.provider_person_id` | Direct mapping if already resolved |
| `physio_client_id` | resolve to `org_clients.id` or `subject_person_id` | Prefer durable `person_id` / `subject_person_id` |
| `physio_session_id` | `encounter_id` | Best mapped as the canonical encounter id |
| `created_at` | `encounters.period_start`, `encounter_media.captured_at`, `voice_memos.recorded_at`, `clinical_events.occurred_at` | Use modality-appropriate time field |

### 2. `soap_notes`

Local meaning:

- Draft or generated SOAP note tied to one event

Target:

- `encounter_notes`

Field mapping:

| Local `soap_notes` field | moai_web target | Notes |
| --- | --- | --- |
| `id` | `encounter_notes.id` or metadata | Can reuse if UUID-compatible |
| `event_id` | `encounter_notes.encounter_id` or metadata link | Prefer canonical encounter id |
| `s` | `encounter_notes.subjective` | Direct |
| `o` | `encounter_notes.objective` | Direct |
| `a` | `encounter_notes.assessment` | Direct |
| `p` | `encounter_notes.plan` | Direct |
| `created_at` | `encounter_notes.created_at` | Direct |

Recommended additional fields in `encounter_notes`:

- `note_format = 'SOAP'`
- `source_type = 'rayban_pt_bridge'`
- `source_system = 'rayban_pt'`
- `status = 'draft'` before therapist approval
- `ai_draft_snapshot` for original generated payload

### 3. `rehab_labels`

Local meaning:

- Therapist-facing lightweight labeling for session type, task, assist level, performance, flags, notes
- Custom task and body-position capture for early taxonomy gaps such as `supported_kneeling`
- Explicit review/training gates so reviewed clinical labels do not automatically become model-training gold

Target:

- `encounters.session_type`
- `observations`
- `activity_sessions.metrics`
- optional `client_media_summaries.structured_findings`

Recommended mapping:

| Local `rehab_labels` field | moai_web target | Notes |
| --- | --- | --- |
| `event_id` | `encounter_id` / `activity_session_id` / metadata | Depends on whether label is session-level or task-level |
| `session_type` | `encounters.session_type` | Direct |
| `core_task` | `activity_sessions.activity_type` or `observations.code/value_string` | Better as structured observation if taxonomy exists |
| `custom_task` | `activity_sessions.activity_type` / `activity_sessions.metrics.custom_task` | Used when `core_task=other`; do not promote to enum until taxonomy review |
| `body_position` | `observations` / `activity_sessions.metrics.body_position` | Required for kneeling/sitting/prone interpretation |
| `assist_level` | `observations` | Store as coded observation or `value_string` |
| `performance_level` / `performance` | `observations` or `activity_sessions.metrics` | API accepts both; `performance_level` is preferred |
| `review_status` | `ai_inference_log.review_status` / review metadata | `reviewed`, `corrected`, or `approved` can satisfy schema readiness |
| `usable_for_training` | dataset filter metadata | Must be true before an example enters a training/eval dataset |
| `flags` | `observations.value_json` or `client_media_summaries.structured_findings` | Use coded flags where possible |
| `notes` | `activity_sessions.notes` or `observations.note` | Direct |
| `updated_at` | `updated_at` fields on target tables | Direct |

Recommended observation coding approach:

- One observation for assist level
- One observation for performance
- One observation for safety/risk flags
- One observation for task outcome

Why not create `rehab_labels` in `moai_web`?

- `session_type` already exists in `encounters`
- `observations` is flexible enough for coded clinical facts
- `activity_sessions.metrics` is better for structured task metrics
- A new table would duplicate existing semantics

### 4. `chart_reviews`

Local meaning:

- Human review state for generated chart quality

Target:

- `clinical_extraction_reviews`
- `ai_feedback`
- `encounter_notes.approval_status`
- `encounter_notes.review_action`

Field mapping:

| Local `chart_reviews` field | moai_web target | Notes |
| --- | --- | --- |
| `event_id` | `clinical_extraction_reviews.encounter_id` or `ai_inference_id` | Depends on whether review targets a note or extraction |
| `reviewer` | `clinical_extraction_reviews.reviewer_person_id` / `ai_feedback.reviewer_person_id` | Direct after identity resolution |
| `notes` | `clinical_extraction_reviews.review_note` / `ai_feedback.rejection_note` | Direct |
| `quality_score` | `ai_feedback.modified_content` or metadata | No exact direct column; store in review payload/metadata |
| `quality_level` | `clinical_extraction_reviews.review_status` or metadata | Map to review status taxonomy |
| `reviewed_at` | `clinical_extraction_reviews.reviewed_at` / `encounter_notes.approved_at` | Direct |

Recommended rule:

- Use `clinical_extraction_reviews` for structured extraction correction
- Use `ai_feedback` for narrative AI output correction/rejection
- Use `encounter_notes.approval_status` for final note sign-off

### 5. `patient_consents`

Local meaning:

- Consent record for capture/analysis/storage

Target:

- `patient_consent_records`

Field mapping:

| Local `patient_consents` field | moai_web target | Notes |
| --- | --- | --- |
| `id` | `patient_consent_records.id` | Direct |
| `patient_name` | resolve to `person_id` | Resolve, do not keep name-only identity as primary linkage |
| `scope` | `content_code` | Needs controlled mapping |
| `consent_text` | `signed_content_snapshot` | Store exact signed text snapshot |
| `granted_by` | `created_by` | Resolve to person id if possible |
| `revoked_at` | no obvious direct field in current table | May need extension if revocation is required |
| `created_at` | `created_at` / `signed_at` | Use both if signing time is known |

Gap:

- Revocation is not obvious in the current `patient_consent_records` shape.
- If revocation matters operationally, this may be a justified schema extension.

### 6. `audit_logs`

Local meaning:

- Operational audit log

Target:

- `clinical_events`

Field mapping:

| Local `audit_logs` field | moai_web target | Notes |
| --- | --- | --- |
| `id` | `clinical_events.id` | Direct |
| `event_id` | `clinical_events.resource_id` or `event_id` | Use as external source id if not canonical encounter id |
| `level` | `clinical_events.severity` | Map `info/warn/error` into platform severity |
| `message` | `clinical_events.action_description` | Direct |
| `created_at` | `clinical_events.occurred_at` | Direct |

Suggested defaults for bridge-originated audit rows:

- `event_type = 'bridge_event'`
- `resource_type = 'rayban_pt_event'`
- `actor_type = 'system'`
- `api_endpoint` set when generated from an API request

## Recommended 1st-Pass Ingestion Flow

1. Resolve identity and scope.
   - `organization_id`
   - `provider_person_id`
   - `subject_person_id`
   - optional existing `encounter_id`

2. Create or reuse `encounters`.
   - Use one encounter as the canonical session container.

3. Store raw modality records.
   - image/video -> `encounter_media`
   - audio -> `voice_memos`
   - free text note -> stage in `encounter_notes.ai_draft_snapshot` or review queue

4. Record AI output.
   - `ai_inference_log`
   - `client_media_summaries`

5. Record structured review.
   - `clinical_extraction_reviews`

6. Write final therapist-approved note.
   - `encounter_notes`

7. Write structured label outcomes.
   - `encounters.session_type`
   - `observations`
   - `activity_sessions.metrics`

8. Write audit trail.
   - `clinical_events`

## What Is Duplicative and Should Not Be Recreated

Do not create direct clones of these local tables in `moai_web`:

- `soap_notes`
- `audit_logs`
- `patient_consents`
- `chart_reviews`

These are already covered by:

- `encounter_notes`
- `clinical_events`
- `patient_consent_records`
- `clinical_extraction_reviews` / `ai_feedback`

## What May Still Need Extension

Potential real gaps:

1. Consent revocation
   - `patient_consent_records` does not obviously expose a revocation field in the current shape.

2. Lightweight therapist session labels
   - If product wants a single compact review form with `core_task`, `assist_level`, `performance`, and `flags`,
     a dedicated materialized view or helper table may still improve UX even if storage remains in `observations`.

3. Bridge source identity
   - It may help to standardize metadata fields such as:
     - `source_event_id`
     - `source_system = 'rayban_pt'`
     - `capture_device = 'rayban' | 'iphone'`

## Recommendation

Use `moai_web` as the system of record.

Keep `rayban_pt` local SQLite only as:

- temporary ingest queue
- fail-safe offline buffer
- local smoke-test fixture

Do not port the local schema 1:1 into Supabase.
Instead, build a mapper from local bridge payloads into the existing `moai_web` domain model.
