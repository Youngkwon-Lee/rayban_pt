# Rayban PT Agent Instructions

## Scope and operating mode

`rayban_pt` is the multimodal capture edge for `physio_app`. Shared Supabase
`moai_web` is the system of record; local SQLite is staging/resilience only.

The default is `design/dry_run`: do not enable recurring sync, training,
automatic promotion, or production writes until
`docs/multimodal-data-contract-and-readiness.md` gates allow it. Read that
contract for canonical mapping, table selection, taxonomy, data lineage, and
readiness. Read `docs/pilot-capture-checklist.md` and use
`docs/pilot-session-manifest.template.json` for real pilot capture.

## Data and security contract

- Reuse existing `moai_web` tables; never create permanent duplicates for local
  bridge concepts. Raw media belongs in object storage; Postgres stores metadata
  and references.
- Resolve `organization_id`, `subject_person_id`, `provider_person_id`, and
  `encounter_id` before a production sync. By default, resolve
  `physio_client_id -> org_clients.person_id`; use `--no-resolve-identity`
  only for an intentional local-only plan.
- AI/rule outputs are draft evidence, not clinical conclusions. Gold,
  training, or evaluation data requires valid consent, resolved identity,
  traceable media, explicit clinician review, and registered
  dataset/model/evaluation lineage.
- For Supabase work, use the `supabase` skill; also use
  `supabase-postgres-best-practices` for schema, RLS, indexes, and query shape.
  Preserve RLS and access boundaries. Never expose service-role/secret
  credentials in client code, `NEXT_PUBLIC_*`, source control, or logs.
- Default output is summarized to avoid PHI, note text, and identity hints.
  Use `--full` only for necessary, authorized private inspection.
- Use Hugging Face Jobs/Trackio skills for remote processing, training,
  evaluation, and experiment tracking; do not start them in `design/dry_run`.

## MLOps harness

From `/Users/youngkwon/projects/rayban_pt/server`, run
`./.venv/bin/python mlops_harness.py <command>`; inspect `--help` before
unfamiliar flags.

```bash
./.venv/bin/python mlops_harness.py doctor
./.venv/bin/python mlops_harness.py event-plan <event_id> [identity flags]
./.venv/bin/python mlops_harness.py sync-recent --limit 20
./.venv/bin/python mlops_harness.py inspect-sync-jobs --status pending
./.venv/bin/python mlops_harness.py sync-pending --limit 20
./.venv/bin/python mlops_harness.py readiness-report --status processed --limit 50
./.venv/bin/python mlops_harness.py pilot-fixture
./.venv/bin/python mlops_harness.py dataset-manifest --purpose multimodal_rehab_labeling --minimum-reviewed-encounters 50
```

`sync-event`, `sync-recent`, and `sync-pending` are dry-run unless the
operator passes `--execute` and explicitly sets
`MOAI_HARNESS_ALLOW_WRITES=true`. This is a production-write gate: first
verify target, identity, consent, review status, and plan; never set it
implicitly or commit it.

## Validation and handoff

After bridge changes, run `./.venv/bin/python smoke_test.py` from `server/`.
It validates local bridge safety, not remote or clinical production. Keep
edits narrow, avoid PHI in fixtures/logs, and hand off mode, commands, target,
and unmet readiness/review gates.
