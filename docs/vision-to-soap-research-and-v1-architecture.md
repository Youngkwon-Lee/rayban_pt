# Vision → SOAP: Research Survey and v1 Architecture Decision

Surveyed: 2026-08-20. Companion to `physio-app-agentic-mlops-runbook.md` and
`multimodal-data-contract-and-readiness.md`. This document records why the
note-generation layer is transcript-primary with structured intermediates, and
when fine-tuning becomes worth doing.

## Question 1: Can vision produce structured SOAP notes directly?

No shipped product and no validated research does end-to-end video → SOAP.

### Commercial landscape (2025–2026)

- Ambient scribes are audio-only across the board: Abridge, Microsoft Dragon
  Copilot (ex-Nuance DAX), Nabla, Suki, DeepScribe, Ambience, Heidi, Freed.
  Pipeline: audio → ASR → LLM SOAP draft → coding → EHR.
- PT-specific scribes (closest analogs): SPRY Agentic Scribe (structured SOAP
  fields, goal grids, billing), WebPT × Comprehend Health (2025-08), ScribePT.
  All audio-only. APTA published a practice advisory on ambient AI scribes in
  PT documentation (2025-09).
- Korea: Puzzle AI VoiceEMR / Puzzle Gen (medical ASR + generative summary,
  Daewoong partnership), Naver CLOVA Voice EMR + Smart Survey. Both audio-only.
  Kakao Healthcare pivoted to CGM/hospital-data, not scribes.
- Vision products in rehab are metric products, not note generators: Exer AI
  (FDA Class II SaMD motion assessment), Kemtai (111 keypoints, form feedback),
  Sword Health.

Market conclusion: the proven decomposition is audio→note and video→pose→
metrics as separate systems. Glasses-based video+audio+pose capture for PT is
unoccupied ground, domestically and globally.

### Research boundary

- Markerless mocap → clinical metrics is mature but bounded: OpenCap sagittal
  RMSE 7.0–13.4° with systematic 5–15° flexion overestimation; MediaPipe joint
  angles mean error ~8.6° (better than inter-rater ~12.8°, comparable to
  short-arm goniometry in favorable setups; degrades with camera-angle
  deviation). Good for progress tracking and screening, not goniometer
  replacement in the record.
- Video-based clinical scoring exists at small scale: PD4T (30 PD patients,
  ~2,900 clips, UPDRS 0–4 per task), KIMORE (78 subjects, 0–50 clinician
  scores), UI-PRMD. Successful models are skeleton-input classifiers or
  regressors, not raw-pixel models.
- Video → note text: essentially no validated research. SOAP-generation work
  is transcript→note; a 2025 pediatric-rehab study found ~23.6 errors per
  AI-generated case, dominated by omissions — omission rate is the right
  primary eval metric.
- Multimodal LLM capability on clinical video: good at gross/postural/
  contextual description (a seizure-semiology pilot beat fine-tuned CNN/ViT on
  13/18 features zero-shot) but weak on subtle high-frequency movement; staged
  decomposition (action understanding → conclusion) beat direct video→diagnosis
  by +79.6% (HMVDx). No VLM reliably outputs joint angles, rep counts, tremor
  frequency, or symmetry from raw video — every validated metric routes
  through keypoints + deterministic geometry.

### Egocentric caveat

Nearly all validation above is third-person static camera. Clinician-worn
glasses mean a moving head-mounted camera, partial framing, and motion blur.
No published validation of ROM from egocentric clinical video exists. Treat
egocentric pose accuracy as an open pilot question: collect goniometer
reference measurements on a handful of movements and compare before trusting
any lens-derived angle in documentation.

## Question 2: When does labeling → fine-tuning pay off?

Not at pilot scale. Reference points:

- Rehab-ML papers train narrow skeleton scorers on 10–78 subjects and
  hundreds–thousands of clips. PD4T needed 30 patients / ~2,900 scored clips
  for 4 tasks.
- End-to-end video→SOAP fine-tuning has no published success at any scale.
- Practical fine-tuning routes today: GPT-4o tuning is image-only (no video);
  Gemini public API tuning is unavailable; open-VLM LoRA (Qwen-VL family) is
  feasible but useful floors start around 1–2k labeled clips.
- First model actually worth training: a skeleton-based exercise-quality
  scorer (0–4 rubric), at roughly 30+ patients / a few hundred scored clips.
  This matches the existing readiness plan: Gate 3 (50+ encounters, 100+ per
  label family) in `multimodal-data-contract-and-readiness.md`. The gate
  design stands; no changes needed.

## v1 architecture decision (pilot, 5–10 sessions, solo developer)

Three lanes converging in one LLM synthesis call. No fine-tuning.

1. Audio lane (primary; carries S, A, P): Korean medical STT → diarized
   transcript → frontier LLM with a strict PT-SOAP template and therapist-
   written few-shot exemplars.
2. Pose lane (carries Objective numbers): existing MediaPipe pipeline →
   deterministic metrics only (angles at labeled events, reps, tempo, L/R
   symmetry) → injected into the prompt as a structured JSON block the model
   may quote but not extend.
3. Vision lane (evidence, not measurement): VLM on keyframes/short clips for
   exercise identification, compensation description, and timestamps, attached
   as citations to Objective claims. Zero-shot with decomposed prompting.

This keeps the current review-first posture: the LLM replaces the rule-based
`build_soap` assembly step, not the therapist review gates, and drafts remain
`requires_approval: true`.

## Labeling plan that compounds at n=5–10

Self-hosted Label Studio (free, video timeline segmentation, PHI stays local).
Per session (~1–2h):

- temporal segments with exercise/activity IDs (aligns with `capture_events`
  and `rehab_labels.core_task`),
- a 0–4 quality rubric per exercise instance (future scorer training target),
- one gold SOAP note written by the therapist (eval reference + few-shot pool).

These three assets serve immediately as an eval set (score drafts by omission
rate against the gold note) and later as the exact PD4T-style schema for the
Gate 3 scorer. De-identification: keep raw video on-prem; Meta EgoBlur is
purpose-built for egocentric face blurring, but blurring is weak against
re-identification, so storage stays local regardless.

## Sources (primary)

- Ambient scribe comparisons: medequipdirectory.com 2026 guide; ehrsource.com;
  iatrox.com buyer's guide; heidihealth.com.
- PT scribes: sprypt.com/blog/ai-scribe-spry-vs-webpt; deepcura.com roundup.
- Korea: puzzle-ai.com; CLOVA tech blog (Healthcare AI); kakaocorp.com.
- OpenCap validation: PubMed 38905926; Frontiers Digital Health 2026 scoping
  review. MediaPipe goniometry: PMC10712662, PMC10416158.
- PD4T / PECoP: arXiv 2311.07603, github.com/Plrbear/PECoP. Reviews:
  PMC12568243; arXiv 2602.13507.
- SOAP quality: arXiv 2503.15526; arXiv 2509.04340.
- VLM boundary: arXiv 2605.03352 (seizure semiology); arXiv 2510.09230
  (HMVDx); arXiv 2505.18412 (skeleton features into LLM).
- Fine-tuning: OpenAI vision fine-tuning announcement; ai.google.dev
  model-tuning docs; Datature Qwen2.5-VL guide.
- Egocentric: arXiv 2511.09894 (EgoEMS); arXiv 2601.06750.
- Labeling/de-ID: encord.com healthcare annotation roundup; intuitionlabs.ai
  de-ID review; NCI MIDI best practices (PMC11810855).
