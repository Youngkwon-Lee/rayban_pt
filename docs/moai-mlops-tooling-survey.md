# `rayban_pt` -> `moai_web` MLOps Tooling Survey

## Current bridge status

The bridge now supports three manual integration steps:

1. `GET /events/{event_id}/moai-export`
   - Normalizes a local event into a `moai_web` payload bundle.
2. `GET /events/{event_id}/moai-write-plan`
   - Converts the bundle into ordered REST upsert operations.
3. `POST /events/{event_id}/moai-write`
   - Executes the write plan against Supabase REST when backend credentials are configured.

Today this is still a controlled export path, not a fully automatic sync pipeline.

Agent-facing harness:

- `server/mlops_harness.py doctor`
- `server/mlops_harness.py event-plan <event_id>`
- `server/mlops_harness.py sync-event <event_id>`
- `server/mlops_harness.py sync-recent`
- `server/mlops_harness.py inspect-sync-jobs`
- `server/mlops_harness.py sync-pending`
- `server/mlops_harness.py dataset-manifest`

See `AGENTS.md` and `docs/physio-app-agentic-mlops-runbook.md` for the operating contract.
See `docs/multimodal-data-contract-and-readiness.md` for the canonical data contract and the reason automation remains in `design/dry_run` mode for now.

Identity resolution:

- the harness resolves `physio_client_id` through `moai_web.org_clients.id`
- the resolved `org_clients.person_id` becomes `subject_person_id`
- `patient_name` is only a fallback hint and is accepted only when the match is unambiguous inside an organization

## What `moai_web` already has for MLOps

Based on the live `public` schema metadata from project `iwtyzcwiovuvmsodtusx` (`moai_web`), the app DB already includes several MLOps-relevant tables:

| Area | Existing tables | Why it matters |
| --- | --- | --- |
| Inference logging | `ai_inference_log`, `agent_runs` | Keeps per-run metadata, model/prompt lineage, latency, and review state |
| Human review | `clinical_extraction_reviews` | Supports AI draft -> clinician correction workflows |
| Clinical outputs | `encounter_notes`, `client_media_summaries`, `observations`, `activity_sessions` | Stores final note, structured findings, and measurable session facts |
| Model registry | `ml_model_registry` | Stores model versions and deployment metadata |
| Predictions | `ml_predictions` | Stores prediction outputs separately from raw inference calls |
| Offline evaluation | `prompt_evaluation_runs`, `prompt_evaluation_results` | Supports prompt/model benchmarking and regression checks |

That means `moai_web` is already more than an app database. It has the shape of an early MLOps control plane.

## What is still missing in `rayban_pt`

The current bridge code is useful, but it is not yet full MLOps:

- No automatic post-ingest sync trigger
- No dataset snapshot export for training sets
- No scheduled evaluation job runner
- No training run logger wired into `ml_model_registry`
- No drift or label-correction dashboard
- No identity resolver for reliably mapping local patient/provider references to canonical `moai_web` IDs

So the right mental model is:

- `moai_web` already has many of the destination tables
- `rayban_pt` currently has an export/write bridge
- the missing layer is orchestration

## Skills and plugins available in this environment

### Supabase

Two local skills are directly relevant:

1. `supabase`
   - Use for any DB, auth, RLS, Storage, or CLI/MCP workflow touching Supabase
   - Emphasizes current docs, verification, and security checks
2. `supabase-postgres-best-practices`
   - Use for schema design, indexes, query tuning, and RLS/performance reviews

These are most useful when we:

- harden `moai_web` write paths
- review RLS on newly used tables
- add indexes for `encounter_id`, `subject_person_id`, and review queues

### Hugging Face plugin

The Hugging Face plugin is available and authenticated in this session.

Relevant capabilities:

1. `hf_jobs`
   - Run CPU/GPU jobs remotely
   - Supports ad hoc jobs and scheduled jobs
2. `huggingface-trackio` skill
   - Experiment tracking for training metrics and alerts
   - Useful as a lightweight stand-in for W&B-style tracking
3. `huggingface-community-evals` skill
   - Local/offline model evaluation using `inspect-ai` or `lighteval`
4. `huggingface-vision-trainer` skill
   - Fine-tune vision models such as detectors/classifiers/segmentation models on HF Jobs
5. `huggingface-llm-trainer` skill
   - Fine-tune language models if structured note generation or coding models ever become trainable targets

Callable plugin tools discovered in-session:

- Hugging Face Jobs
- Hugging Face model search
- Hugging Face dataset search
- Hugging Face paper search
- Hugging Face repo details
- Hugging Face docs search/fetch

Notably absent in this Codex environment:

- no dedicated MLflow plugin
- no dedicated Weights & Biases plugin

That makes Trackio the closest built-in experiment tracking option here.

## Best-fit MLOps stack for this project

For `rayban_pt`, the most realistic stack is not MLflow-first. It is:

1. `moai_web` as the operational system of record
   - inference logs
   - review states
   - model registry rows
   - prediction outputs
2. Hugging Face Jobs for remote training/eval compute
   - especially useful for vision workloads when local GPU setup is inconvenient
3. Trackio for run-time experiment metrics
   - loss, accuracy, alerts, run comparisons
   - can be adopted with a `wandb`-like logging pattern when needed
4. `prompt_evaluation_*` tables in `moai_web` for app-level regression checks
   - especially for SOAP draft quality or extraction prompt changes

This means we can avoid introducing another heavy platform immediately unless scale demands it.

## Practical recommendation by phase

### Phase 1: Operationalize the bridge

- Keep using `moai_web` tables already identified in the mapper
- Add automatic sync after event finalization or therapist review
- Resolve subject/provider identity before write
- Log every write outcome into `clinical_events` or a dedicated sync audit event

### Phase 2: Build a review-backed dataset loop

- Export clinician-corrected labels from `observations`, `activity_sessions`, and `clinical_extraction_reviews`
- Snapshot approved examples into a reproducible training dataset
- Register the dataset version in `moai_web`

### Phase 3: Train and evaluate

- Use Hugging Face Jobs for remote fine-tuning
- Use Trackio for metrics
- Write summary artifacts and model lineage back into `ml_model_registry`
- Save benchmark results into `prompt_evaluation_runs` / `prompt_evaluation_results`

### Phase 4: Production gating

- Promote only models that beat baseline metrics
- Compare auto label vs clinician-corrected label disagreement rates
- Gate rollout on both offline metrics and real review acceptance rate

## Immediate next steps

Recommended order:

1. Wire a guarded auto-sync path from `rayban_pt` into `moai_web`
2. Add identity resolution for patient/provider/org mapping
3. Define one approved training export query from `moai_web`
4. Run one small Hugging Face eval or training smoke test with Trackio enabled

## Bottom line

The good news is that we do not need to invent a full MLOps platform from scratch.

- `moai_web` already contains much of the app-side MLOps schema
- this Codex environment already has Supabase and Hugging Face skills/plugins that fit the job
- the main missing work is orchestration, dataset curation, and safe rollout rules
