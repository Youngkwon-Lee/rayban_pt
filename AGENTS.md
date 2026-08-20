# Rayban PT Agent Instructions

## Project intent

`rayban_pt` is the multimodal capture edge for `physio_app`.

The canonical operating database is the shared Supabase `moai_web` project. The local SQLite bridge is a staging and resilience layer, not the long-term source of truth.

Current operating mode is `design/dry_run`. Do not enable recurring sync, training, or production write automation until the readiness gates in `docs/multimodal-data-contract-and-readiness.md` are met.

For first real-data collection, follow `docs/pilot-capture-checklist.md` and use `docs/pilot-session-manifest.template.json`.

## Agent workflow

Before changing Supabase, MLOps, or shared DB behavior:

1. Use the `supabase` skill for Supabase-specific work.
2. Use `supabase-postgres-best-practices` when touching schema, RLS, indexes, or query shape.
3. Use Hugging Face Jobs/Trackio skills for remote eval, training, and experiment tracking.
4. Keep writes dry-run by default.
5. Never expose service role or secret keys in frontend code or logs.

## MLOps harness

Run commands from:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
```

Check local readiness:

```bash
./.venv/bin/python mlops_harness.py doctor
```

Plan one event sync:

```bash
./.venv/bin/python mlops_harness.py event-plan <event_id> \
  --subject-person-id <person_id> \
  --provider-person-id <provider_person_id> \
  --encounter-id <encounter_id>
```

Dry-run recent sync:

```bash
./.venv/bin/python mlops_harness.py sync-recent --limit 20
```

Summarize local pilot/data readiness:

```bash
./.venv/bin/python mlops_harness.py readiness-report --status processed --limit 50
```

Run a synthetic non-PHI pilot rehearsal in a temporary local DB:

```bash
./.venv/bin/python mlops_harness.py pilot-fixture
```

Inspect queued sync jobs:

```bash
./.venv/bin/python mlops_harness.py inspect-sync-jobs --status pending
```

Process queued sync jobs:

```bash
./.venv/bin/python mlops_harness.py sync-pending --limit 20
```

The harness tries to resolve identity from `moai_web` by default, especially `physio_client_id -> org_clients.person_id`. Use `--no-resolve-identity` only when you intentionally want a local-only plan.

Dry-run output is summarized by default to avoid writing note text or identity hints into agent logs. Use `--full` only when payload inspection is necessary.

Execute a write only when the agent environment explicitly opens the gate:

```bash
export MOAI_HARNESS_ALLOW_WRITES=true
./.venv/bin/python mlops_harness.py sync-event <event_id> \
  --subject-person-id <person_id> \
  --provider-person-id <provider_person_id> \
  --encounter-id <encounter_id> \
  --execute
```

Build a dataset snapshot manifest:

```bash
./.venv/bin/python mlops_harness.py dataset-manifest \
  --purpose multimodal_rehab_labeling \
  --minimum-reviewed-encounters 50
```

## Shared DB mapping

Read `docs/multimodal-data-contract-and-readiness.md` before changing label taxonomy, dataset export, or training automation.
Read `docs/pilot-capture-checklist.md` before changing pilot capture requirements.

Use existing `moai_web` tables before adding new permanent tables:

- `encounters`
- `encounter_media`
- `voice_memos`
- `ai_inference_log`
- `client_media_summaries`
- `encounter_notes`
- `observations`
- `activity_sessions`
- `clinical_extraction_reviews`
- `ai_feedback`
- `ml_model_registry`
- `ml_predictions`
- `prompt_evaluation_runs`
- `prompt_evaluation_results`
- `clinical_events`

## Safety rules

- Resolve `organization_id`, `subject_person_id`, `provider_person_id`, and `encounter_id` before production sync.
- Store raw media in object storage and metadata in Postgres.
- Treat AI output as draft until clinician review.
- Treat clinician-corrected data as gold only after review status is explicit.
- Register dataset/model/eval lineage before using it for training or deployment.
- Run `./.venv/bin/python smoke_test.py` after bridge changes.
