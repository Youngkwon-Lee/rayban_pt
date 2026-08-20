# Pilot Capture -> Pediatric Home Labeling Runbook

Status: draft, verified against code on 2026-08-20. Manual/glue steps only — no
new integration code was written for this runbook, per design intent in
`docs/pediatric-labeling-and-soap-display-design.md` ("Do not build a new
labeling tool").

Scope: how to take `rayban_pt` pilot capture sessions (see
`docs/pilot-capture-checklist.md`) and label them in the existing
`home-rehab-labeling` workbench using the `pediatric_home_v1` schema. This is
a **manual, per-session** workflow. There is no automated transfer between the
two systems yet (see Known Gaps at the end).

---

## 0. What was actually verified

- `home-rehab-labeling` has **no** `scripts/serve_workbench.py`. The real
  script is `scripts/rehab_v1/serve_rehab_labeling_workbench.py`, default
  port **8877** (confirmed via `--help` and the systemd unit). The path in
  `docs/pediatric-labeling-and-soap-display-design.md` (`python3
  scripts/serve_workbench.py --port 8877`) is wrong — use the path below.
- The script is pure Python 3 standard library (`argparse`, `http.server`,
  `sqlite3`, `hashlib`, …). No `requirements.txt` or `pyproject.toml` exists
  in that repo and none is needed. Ran cleanly under the system's Python
  3.12.2 with no venv.
- `pediatric_home_v1` already exists at
  `home-rehab-labeling/review_apps/label_schemas/pediatric_home_v1.json` and
  is already used by three registered projects in
  `review_apps/labeling_projects.json` (`pediatric_home`,
  `pediatric_home_batch2`, `pediatric_home_batch3_1000`) — each with its own
  testset JSON but the same shared schema. This is the pattern to copy for a
  rayban_pt batch, not `create_labeling_project.py` (that command always
  generates a **new** schema file per `--project-id`, which would fork away
  from the taxonomy the design doc wants reused).
- The workbench requires login; there is no anonymous/file:// mode
  (`review_apps/rehab_home_labeling_v1.html` explicitly detects `file://` and
  refuses to save). A project-scoped account with `project_ids` membership is
  required (see `docs/rehab-v1/14_labeling_platform_registry.md`).
- Media is **not** served as plain static files for arbitrary paths. Every
  video/image request goes through `/media-proxy/<project_id>/<asset_uid>`,
  which forwards to a resolver's `{base}/media/{asset_uid}` endpoint. The
  only resolver script that implements that endpoint,
  `scripts/rehab_v1/serve_d_pediatric_media_resolver.py`, is **WSL/Windows-only**
  — it shells out to `powershell.exe` at a hardcoded path
  (`/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`) and its
  `--root` default is `D:\pediatric`. `/media-url-proxy/` is additionally
  hardcoded to only accept `100.125.26.99:8766` / `:8767` as a target host.
  **There is no macOS-local media resolver in this repo.** This is the
  central gap this runbook works around (see Step 2 and Known Gaps).
- The production instance already runs on the home desktop
  (`yk@100.125.26.99`) under systemd: `rehab-label-workbench.service` (port
  8877, `WorkingDirectory=/home/yk/rehab_labeling_server`) plus resolver
  services on 8767 (`D:\pediatric` default) and 8768/8769 for other projects.
  This matches this user's own runtime-priority rule (always-on services live
  on the home desktop, not the MacBook).
- The running server loads `review_apps/labeling_projects.json` once at
  startup into `self.server.projects`. A script that edits that file on disk
  while the server is already running (e.g. adding a batch entry by hand,
  or via `create_labeling_project.py`) does **not** hot-reload — confirmed by
  reading the in-server "create project" handler, which explicitly does
  `self.server.projects = projects_loaded` after writing, something an
  external script cannot do to another process. **You must restart the
  workbench service** after registering a new project/testset.
- Export: authenticated admin-only GET endpoints
  `/api/admin/export.jsonl` and `/api/admin/export.clean.jsonl`
  (`?project_id=...&team_id=...&annotator=...`), plus CSV equivalents. Labels
  are also mirrored continually to
  `<export-dir>/<project_id>/<project_id>_labels.jsonl` and
  `..._labels.latest.json` as annotators save, and to a SQLite file
  (`<export-dir>/rehab_labeling.sqlite3` by default).
- `rayban_pt` pilot capture media (audio/video) is staged by
  `server/lib/raw_media.py` into `server/storage/raw-media/` (created at
  import time), one file per artifact named
  `{event_id}_{audio|video}{ext}` (`.wav`/`.mov`/`.mp4`) plus a sidecar
  `{same_name}.json` with `{kind, content_type, duration_seconds,
  transcript_text, consent_id}`. `RawMediaKind` is currently only
  `raw_audio` / `raw_video` — still-image capture does not go through this
  module; if a pilot session includes standalone photos, confirm where those
  land separately before staging (**[미확인]** — not found in
  `raw_media.py`/`bridge_core.py`).
- `server/storage/` is already covered by `.gitignore` (line 41), so nothing
  under it — raw media, or the proposed label-output folder in Step 5 — will
  be committed by accident.

---

## 1. Prerequisites

**home-rehab-labeling side**
- SSH access to the home desktop (`yk@100.125.26.99`), where the shared
  workbench + resolvers already run. Prefer using that live instance over
  starting a second one, so exports land in the one place people already
  check.
- A workbench account with `project_ids` membership for whatever project you
  register (invite-link bootstrap, per
  `docs/rehab-v1/14_labeling_platform_registry.md`). If none of the pediatric
  team's testers should see rayban_pt clips yet, use a fresh project id
  scoped to just the people who need it — membership is enforced server-side
  per project, not just by UI.
- `HOME_REHAB_LABEL_SALT` — same salt used elsewhere in that repo — only
  needed if you generate `asset_uid`s via the PHI-safe manifest tooling
  (`build_remote_d_pediatric_phi_manifest.py`) or `create_labeling_project.py
  --media-root`. Get it from whoever manages that repo; do not invent a new
  one (asset_uids must stay stable across sessions).

**PHI / privacy rules** (from
`docs/rehab-v1/12_d_pediatric_phi_safe_labeling_workflow.md`, which is the
current, authoritative workflow doc — doc `07_dual_rater_pilot_workflow.md`
describes an older CSV-based flow, superseded by the workbench):
- Never export patient names, raw folder names, file names, or full paths
  into review packets or testset JSON. Only hashed `asset_uid`s leave the
  secure machine.
- Label first with human raters; do not fine-tune or auto-conclude from
  labels before they exist.
- Do not auto-sync raw media into Supabase.
- Do not export raw file paths into app payloads unless the app itself stays
  on the secure machine.
- Face/identity masking: the workbench supports
  `--media-privacy-mode {original,blur}` server-wide and a per-project
  `media_privacy_mode` override, and blur-cache build scripts exist
  (`build_blurred_video_cache.py`, `build_blurred_image_cache.py`,
  `build_blurred_media_cache.py`). Whether blur is actually turned on for any
  given project is a per-registry-entry setting, not automatic —
  **confirm the `media_privacy_mode` value for whatever project you register
  before treating unmasked rayban_pt faces as safe to view outside the
  clinical team** (**[미확인]** whether blur is default-on for new entries;
  it reads as opt-in based on the code).
- rayban_pt-side prerequisites are unchanged from
  `docs/pilot-capture-checklist.md`: `organization_id`,
  `provider_person_id`, `subject_person_id`/`physio_client_id`,
  `encounter_id`, consent checked, capture device recorded, and
  `PILOT_CAPTURE_MODE=false` while still designing schemas.

---

## 2. Step-by-step

### Step 0 — Capture the pilot session in rayban_pt

Follow `docs/pilot-capture-checklist.md` as-is: confirm identity/consent
fields before capture, use the recommended spoken-note format, and after
capture run the dry-run readiness checks from `server/`:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python mlops_harness.py doctor
./.venv/bin/python mlops_harness.py readiness-report --status processed --limit 50
```

Confirm the session's raw media exists:

```bash
ls server/storage/raw-media/ | grep <event_id>
```

You should see `{event_id}_audio.wav` and/or `{event_id}_video.mov` (or
`.mp4`) plus matching `.json` sidecars.

### Step 1 — Stage media for labeling (the actual gap)

Because the only working media resolver in `home-rehab-labeling` is
WSL/Windows-only and defaults to `D:\pediatric`, and there is no macOS-local
equivalent, the pragmatic path for a pilot is to copy the staged files to the
home desktop and reuse the **existing** resolver script with different
arguments — not to write a new one.

Recommended: keep rayban_pt clips out of the real `D:\pediatric` PHI dataset
folder, in their own root, served by a second instance of the same resolver
script on a spare port:

```bash
# from the Mac, copy this session's staged media to the home desktop
rsync -av \
  --include="<event_id>_*" --exclude="*" \
  /Users/youngkwon/projects/rayban_pt/server/storage/raw-media/ \
  yk@100.125.26.99:/mnt/d/rayban_pt_pilot/<session_id>/
```

```bash
# on the home desktop (WSL), start a second resolver pointed at that folder
export HOME_REHAB_LABEL_SALT="<same salt as the rest of the repo>"
cd ~/rehab_labeling_server   # or wherever that repo is checked out there
python3 scripts/rehab_v1/serve_d_pediatric_media_resolver.py \
  --root 'D:\rayban_pt_pilot' \
  --host 127.0.0.1 \
  --port 8770 \
  --public-base-url http://127.0.0.1:8770
```

Ports 8767–8769 are already used by the pediatric/hawkeye/gait resolvers per
the systemd units, so 8770 (or any free port) avoids collisions — pick a port
and confirm it is not already bound (`ss -tlnp | grep 8770` on the desktop)
before wiring it in.

**Note the `.json` sidecar metadata (`transcript_text`, `consent_id`,
`duration_seconds`) does not travel through this resolver.** It is not part
of the `pediatric_home_v1` clip schema. If a transcript needs to be visible
to the labeler, it currently has to be pasted manually into the `comments`
field, or handled outside the tool.

### Step 2 — Build a PHI-safe manifest and testset for this batch

From the home desktop, against the new root:

```bash
export HOME_REHAB_LABEL_SALT="<same salt>"
python3 scripts/rehab_v1/build_remote_d_pediatric_phi_manifest.py \
  --host 127.0.0.1 \
  --root 'D:\rayban_pt_pilot\<session_id>' \
  --output-jsonl examples/rehab-v1/rayban_pt_pilot_assets.jsonl \
  --summary-json examples/rehab-v1/rayban_pt_pilot_summary.json
```

(This script is documented as SSH-to-a-remote-Windows-host; run it locally on
the desktop where the folder actually lives — **[미확인]** whether it also
works pointed at `--host 127.0.0.1`/localhost rather than a genuinely remote
host; confirm with a `--limit 5` smoke run first.)

Then build the testset JSON the labeling UI reads:

```bash
python3 scripts/rehab_v1/build_rehab_home_testset.py \
  --shortlist-csv examples/rehab-v1/rayban_pt_pilot_assets.jsonl \
  --output-json review_apps/rayban_pt_pilot_testset.json
```

(`build_rehab_home_testset.py --help` expects a shortlist CSV, produced
normally by `select_phi_safe_pilot_shortlist.py`; for a single pilot session
with a handful of clips it may be simpler to hand-write the small testset
JSON directly, matching the shape already used by `pediatric_home_batch2` —
inspect `review_apps/pediatric_home_batch2_keywords_100_testset.json` as a
template before doing this by hand.)

### Step 3 — Register the batch against the existing `pediatric_home_v1` schema

Do **not** run `create_labeling_project.py` for this — it always generates a
brand-new schema file per project id, which would fork away from the shared
pediatric taxonomy the design doc wants reused. Instead, add an entry to
`review_apps/labeling_projects.json` following the exact pattern already used
by `pediatric_home_batch2`/`pediatric_home_batch3_1000`:

```json
{
  "id": "pediatric_home_rayban_pilot",
  "name": "Pediatric Home Rehab — Rayban PT Pilot",
  "subtitle": "rayban_pt capture pilot batch",
  "description": "Pilot sessions captured via rayban_pt, labeled with the shared pediatric_home_v1 taxonomy.",
  "href": "/review_apps/rehab_home_labeling_v1.html?project_id=pediatric_home_rayban_pilot&testset=rayban_pt_pilot_testset.json&schema=label_schemas/pediatric_home_v1.json&team_id=pediatric_team",
  "testset": "review_apps/rayban_pt_pilot_testset.json",
  "schema": "review_apps/label_schemas/pediatric_home_v1.json",
  "status": "pilot"
}
```

Add project membership for whoever will label it (per the access-control
rules in `docs/rehab-v1/14_labeling_platform_registry.md`), then **restart
the workbench service** so it re-reads the registry file — confirmed in code
that `self.server.projects` is only loaded once at process start:

```bash
systemctl --user restart rehab-label-workbench.service
```

Confirm it came back up:

```bash
curl -fsS http://127.0.0.1:8877/rehab-labeling/login >/dev/null && echo OK
```

### Step 4 — Label with `pediatric_home_v1`

Open `http://127.0.0.1:8877/rehab-labeling/login` (or the desktop's public
URL if remote), log in, and open the `pediatric_home_rayban_pilot` project.

Per the labeling plan in
`docs/pediatric-labeling-and-soap-display-design.md`, prioritize in this
order within the 1–2h budget:

1. **구간 (segments)** — set `clip_type` correctly first for every clip
   (`supported_sitting`, `supine_posture`, `device_tcan`/`device_peg`/
   `device_vent`, `scoliosis_screen`, `feeding_swallow`, `rom_clip`, etc. —
   the full enum is in `pediatric_home_v1.json`'s `common_fields.clip_type`).
   Everything else in the schema is keyed off this.
2. **인스턴스 (per-clip task fields)** — fill the `task_fields` block that
   matches the chosen `clip_type` (e.g. for `supported_sitting`:
   `head_control_supported_sitting`, `trunk_control_supported_sitting`,
   `sitting_support_level`, `exercise_tolerance`, …), plus the common fields
   `assessable`, `distress_red_flag`, `video_quality`, `occlusion`,
   `laterality`.
3. **기기 (device state)** — for `device_tcan`/`device_peg`/`device_vent`/
   `device_airway`/`device_afo` clips, fill the device-site fields
   (`stoma_redness`, `peg_position_issue`, `urgent_device_red_flag`, etc.) —
   these already exist in the schema exactly as described in the design doc.
4. **세션 gold note** — **this does not exist as a schema field today.**
   `pediatric_home_v1.json` has no session-level note distinct from the
   per-clip `comments` field (confirmed — grepped the schema and the
   workbench server for `session_note`/`gold_note`/`session_gold`: none
   exist). The design doc's "세션 gold note = Zone A + B + C, 치료사 직접
   작성" is a target, not a shipped feature. For this pilot, capture it
   outside the tool: have the therapist write the gold note as free text in
   `rayban_pt`'s own session notes (or in a plain text file alongside the
   exported labels — see Step 5), and cross-reference it by `event_id`/
   `session_id`. Do not try to force it into the per-clip `comments` field.

Use `label_confidence` (`low`/`medium`/`high`) on every clip — it is a real
schema field and is what the tool's own docs treat as the confidence signal;
there is no separate numeric confidence score.

### Step 5 — Export labels

As an admin user:

```bash
curl -H "Cookie: <session cookie>" \
  "http://127.0.0.1:8877/api/admin/export.clean.jsonl?project_id=pediatric_home_rayban_pilot" \
  -o pediatric_home_rayban_pilot_labels.jsonl
```

(Easier in practice: log in via the browser and use the admin export link in
the UI, which sets the auth cookie automatically — `send_admin_export_jsonl`
requires the same session-cookie auth as every other page.) `.clean.jsonl`
strips internal bookkeeping fields; use the plain `.jsonl` if you need the
raw record shape for debugging.

Labels are also continuously mirrored (no export step needed to see them) at:
- `<export-dir>/pediatric_home_rayban_pilot/pediatric_home_rayban_pilot_labels.jsonl`
- `<export-dir>/pediatric_home_rayban_pilot/pediatric_home_rayban_pilot_labels.latest.json`
- the SQLite mirror at `<export-dir>/rehab_labeling.sqlite3`

where `<export-dir>` is whatever `--export-dir` the running service was
started with (`review_apps/exports` under the production
`WorkingDirectory=/home/yk/rehab_labeling_server`, per the systemd unit).

### Step 6 — Where to store the exported labels (proposed, not built)

Copy the exported JSONL back to a rayban_pt-side location that already
travels with the rest of that pilot session's raw evidence, and that is
already excluded from git (`server/storage/` is in `.gitignore`):

```
server/storage/pilot-labels/<session_id>/pediatric_home_rayban_pilot_labels.jsonl
server/storage/pilot-labels/<session_id>/gold_note.txt   # the Step 4.4 workaround
```

This is a proposal only — no code was written to automate the copy or to map
these labels onto rayban_pt's own `rehab_labels` facets
(`core_task`/`assist_level`/`performance`/`usable_for_training`). The design
doc explicitly defers that mapping to a not-yet-written
`pediatric-label-schema-mapping.md`.

---

## 3. Time budget and quality checklist

Target from the design doc: **1–2 hours per pilot session.** Rough split for
a session with a handful of clips: 10–15 min staging/registration (mostly
one-time setup per batch, not per clip), 45–75 min labeling (구간 →
인스턴스 → 기기, in that priority order — stop at 인스턴스 if time runs out,
기기 fields only apply to device clips anyway), 15–20 min gold note +
export.

Quality checklist, built only from what the tool's own docs and schema
actually support (not aspirational):
- **Dual-rater**: supported by the tool's pattern (two accounts label the
  same testset independently), and is the documented pilot gate in
  `docs/rehab-v1/07_dual_rater_pilot_workflow.md` (kappa ≥ 0.60 target for
  retained labels) — but that doc's tooling
  (`build_adjudication_sheet.py`, `apply_adjudication.py`) is CSV-based and
  predates the workbench UI. For a single rayban_pt pilot session with very
  few clips, a lightweight substitute is acceptable: export both raters'
  JSONL via `?annotator=<name>` and diff by hand; do not skip this if the
  labels are meant to seed a gold set later.
- **`label_confidence`**: mark every clip; treat `low` as "needs a second
  rater or exclusion," not as a number to average.
- **`assessable` / `distress_red_flag` / `video_quality` / `occlusion`**: set
  on every clip regardless of `clip_type` — these are the tool's own common
  quality gate fields, and match rayban_pt's own readiness-report intent
  from `docs/pilot-capture-checklist.md`.
- **`review_status`**: leave `pending` clips out of any export you treat as
  final; only `labeled` (and reviewed) clips should count toward a gold set.
- Cross-check against `docs/pilot-capture-checklist.md`'s own review
  checklist (AI draft accepted/corrected/rejected, masking/PHI policy
  passed, identity resolution passed, consent valid) before treating any
  exported label as usable for training.

---

## 4. Known gaps between the two systems

- **No automated transfer.** Everything above is manual file copies and hand
  edits to a JSON registry. Nothing watches rayban_pt's `raw-media/` folder
  and nothing pushes exported labels back automatically. Fine for a pilot;
  not fine at scale.
- **No macOS-local media resolver.** The only resolver implementation in
  `home-rehab-labeling` shells out to Windows PowerShell and defaults to
  `D:\pediatric`. Labeling rayban_pt clips today requires copying them to
  the WSL/Windows home desktop first. A generic resolver that serves an
  arbitrary local folder (no PowerShell, no Windows path assumptions) would
  remove this step, but was intentionally not built here.
- **No session-level gold note field.** `pediatric_home_v1.json` only has
  per-clip fields plus a free-text `comments`. The "Zone A/B/C session gold
  note" concept from `docs/pediatric-labeling-and-soap-display-design.md` is
  not implemented in the labeling tool; it has to be captured outside it for
  now (Step 4.4 / Step 6).
- **No label-schema mapping.** `pediatric_home_v1` facets and rayban_pt's own
  `rehab_labels` facets (`core_task`, `assist_level`, `performance_level`,
  `usable_for_training`) overlap conceptually but use different vocabularies
  and are not mapped anywhere yet — flagged in the design doc as future work
  (`pediatric-label-schema-mapping.md`, not yet written).
- **Transcript/consent metadata does not reach the labeling UI.** rayban_pt's
  `raw_media.py` sidecar (`transcript_text`, `consent_id`,
  `duration_seconds`) has no corresponding field in `pediatric_home_v1` or in
  the resolver's `/media/<asset_uid>` response; it either has to be pasted
  into `comments` by hand or ignored during labeling.
- **Still-image capture staging is unverified.** `raw_media.py`'s
  `RawMediaKind` only covers `raw_audio`/`raw_video`; if a pilot session
  captures standalone photos, confirm separately where those land before
  trying to stage them into this workflow.
