# Ray-Ban Korean STT: tool survey and implementation audit

Date: 2026-08-20

## Decision

Do not start fine-tuning yet. First create a repeatable benchmark from real
Meta Ray-Ban HFP WAV files, human-corrected Korean transcripts, and immutable
session-level train/dev/test splits. Compare base models and vocabulary
prompting with the same decoder settings. Fine-tuning opens only if the
benchmark shows persistent domain errors that prompting or a larger base
model does not fix.

## Current implementation

Implemented:

- The iOS app selects a Ray-Ban/Meta Bluetooth HFP input and rejects a dropped
  route. It records mono PCM WAV at 8 kHz.
- The app uploads WAV to `/ingest-upload`, polls the asynchronous result, and
  attaches the resulting event to the active visit.
- The bridge runs `faster-whisper==1.1.1`, currently defaulting to model
  `small`, language `ko`, device `auto`, compute type `int8`.
- With active scoped consent and `AUDIO_STORE=true`, the bridge stages the raw
  WAV plus a JSON sidecar containing duration, transcript, and consent ID.
- Raw-media access is organization-scoped and deletion/revocation behavior is
  covered by the round-trip smoke test.
- Transcript-derived clinical capture events and human clinical label import
  already have smoke coverage.

Verified on 2026-08-20:

- `raw_media_roundtrip_smoke_test.py`: PASS
- `capture_event_smoke_test.py`: PASS
- `human_label_smoke_test.py`: PASS
- `smoke_test.py`: PASS
- A synthetic Korean sentence converted to Ray-Ban-like 8 kHz mono WAV ran
  through the real `faster-whisper small` path. The transcript contained the
  clinically important errors `무릎 굽힘 -> 무릎 9핀` and
  `지지 앉기 -> 지지 않기`, demonstrating why a domain benchmark is needed.
- On that single synthetic sample, the baseline measured CER 6.52%, WER
  14.29%, and domain-term recall 33.33%. Adding an initial prompt and hotwords
  measured CER 2.17%, WER 7.14%, and domain-term recall 66.67%. This is a
  pipeline smoke result, not evidence of clinical accuracy; the same comparison
  must be repeated on real Ray-Ban HFP recordings.

Gaps found:

- Before this audit, STT exceptions and empty results were stored as
  `[STT_STUB]` or `[STT_EMPTY]` text, while iOS treated any non-empty string as
  success. The bridge now emits explicit `STT_FAILED` / `STT_EMPTY` errors.
- No model version, inference latency, decoder settings, CER/WER, domain-term
  accuracy, numeric accuracy, or laterality accuracy was recorded.
- `server/storage/raw-media` is currently empty. The earlier physical capture
  proof logs remain, but the corresponding WAV is not retained for repeatable
  STT evaluation.
- When an upload fails during STT, the current ingestion job removes the
  temporary upload rather than creating a failed-but-reviewable raw-media event.
  Preserve-on-failure needs a separately scoped event/provenance design before
  using failure audio as training data.
- The current `pediatric_home_v1` workbench schema is video/clinical-label
  oriented. Transcript, corrected transcript, STT model, and error category do
  not reach its UI.
- No automated export from the labeling workbench into a Whisper/Hugging Face
  audio-text dataset exists.

## Tool and service survey

### Recommended open-source stack

1. **Existing capture + faster-whisper**: keep for local inference and the
   production-shaped baseline. It supports `initial_prompt` and `hotwords` and
   can load a CTranslate2-converted fine-tuned Whisper model.
2. **Label Studio OSS**: best fit for listening to the WAV, correcting the full
   transcript, and optionally labeling audio regions. Its advanced multi-channel
   transcript editor is an Enterprise feature, so the first pilot should use a
   simple OSS Audio + required TextArea configuration.
3. **Hugging Face Transformers/Datasets/Evaluate**: preferred first fine-tuning
   path because the current runtime is already Whisper-compatible and the
   checkpoint can be converted back to CTranslate2.
4. **WhisperX + pyannote.audio**: add only when therapist/caregiver/child speaker
   separation and word-level timestamps become necessary. They solve alignment
   and diarization, not base transcript accuracy.
5. **Silero VAD**: optional later optimization for silence/noise segmentation.
   Do not add it before the raw Ray-Ban baseline establishes whether silence is
   a material error source.

Alternatives not selected for the first pilot:

- **Audino** is audio-focused, but its current repository describes a
  non-commercial license and a heavier CVAT-derived deployment. It adds little
  over Label Studio for this pilot.
- **Argilla** is strong for text feedback and dataset iteration but is not as
  direct a waveform transcription editor for this workflow.
- Building STT transcription into the existing pediatric video workbench would
  preserve one UI, but it would couple two different gold-label tasks and delay
  the first benchmark. Integrate exports later instead.

### Managed comparison candidates

- **Azure Custom Speech**: Korean supports audio plus human-labeled transcripts,
  text customization, structured text, output formatting, and phrase lists. It
  is the strongest managed comparator if institutional data-processing terms
  permit it.
- **Google Cloud Speech-to-Text**: use Korean recognition plus phrase sets as a
  benchmark comparator; full model adaptation availability is more limited than
  simple phrase biasing.
- **AWS Transcribe**: Korean batch/streaming and custom vocabulary can be a
  comparator, but Amazon Transcribe Medical is US-English only, so it is not a
  Korean medical-model solution.

No identifiable patient audio should be sent to a managed API until the
institution approves the processor, region, retention, and contractual terms.

## Benchmark contract

One JSON object per audio clip:

- `sample_id`: non-identifying stable identifier
- `audio_path`: local WAV path
- `reference_text`: human-corrected verbatim Korean transcript
- `split`: `train`, `dev`, or `test`
- `session_id`: pseudonymous capture session; a session may not cross splits
- `speaker_id`: pseudonymous speaker identifier; cross-split overlap is reported
- `capture_origin`: normally `rayban_hfp_microphone`
- `noise_condition`: optional controlled condition
- `domain_terms`: terms that must be preserved for clinical usefulness

Primary metrics:

- Korean CER without spacing/punctuation
- WER with normalized spacing/punctuation
- domain-term recall
- Arabic numeric-token recall
- laterality recall
- failure rate, audio duration, and inference latency

## Commands

Validate a manifest without loading a model:

```bash
cd /Users/youngkwon/projects/rayban_pt/server
./.venv/bin/python stt_benchmark.py \
  --manifest /path/to/rayban-stt-test.jsonl \
  --validate-only
```

Run a privacy-bounded benchmark. The result omits transcript text by default:

```bash
./.venv/bin/python stt_benchmark.py \
  --manifest /path/to/rayban-stt-test.jsonl \
  --output storage/stt-benchmark/small-baseline.json \
  --model small
```

Compare vocabulary prompting without training:

```bash
./.venv/bin/python stt_benchmark.py \
  --manifest /path/to/rayban-stt-test.jsonl \
  --output storage/stt-benchmark/small-prompted.json \
  --model small \
  --initial-prompt "중증소아 방문재활 기록입니다." \
  --hotwords "무릎 굽힘 지지 앉기 중등도 보조 기관절개관 위루관"
```

Only an access-controlled labeling export should use `--include-text-output`.

## Fine-tuning gate

Before training:

1. Collect at least 20-30 controlled, non-PHI Ray-Ban utterances for pipeline
   and label-protocol validation.
2. Then collect representative consented pilot audio with session-level splits
   and dual review for clinically important corrections.
3. Compare `small`, a larger/turbo Whisper baseline, and prompt/hotword variants
   with identical decoder settings.
4. Define the held-out test set before training. Never split clips from the same
   session across train and test.
5. Fine-tune only if the held-out baseline misses the agreed CER and clinical
   term/number/laterality targets.
6. Register dataset, checkpoint, decoder settings, and evaluation lineage before
   converting the winning Transformers checkpoint to CTranslate2 and setting
   `WHISPER_MODEL` to that immutable model path/version.

The first real-device exit condition is not "a transcript appeared." It is:
raw WAV retained with consent provenance, explicit STT status, human reference
available, benchmark result written, and no clinically important number or
laterality error hidden by a successful HTTP response.

## Primary sources

- Label Studio open-source overview and audio transcription support:
  <https://labelstud.io/guide/get_started>
- Label Studio transcription TextArea configuration:
  <https://labelstud.io/tags/textarea>
- Label Studio ML backend integration:
  <https://labelstud.io/guide/ml_create>
- Hugging Face automatic speech recognition training and WER evaluation:
  <https://huggingface.co/docs/transformers/tasks/asr>
- Hugging Face Whisper model documentation:
  <https://huggingface.co/docs/transformers/model_doc/whisper>
- faster-whisper model conversion and fair-comparison notes:
  <https://github.com/SYSTRAN/faster-whisper/blob/master/README.md>
- WhisperX alignment/diarization:
  <https://github.com/m-bain/whisperX>
- pyannote.audio speaker diarization:
  <https://github.com/pyannote/pyannote-audio>
- Silero VAD:
  <https://github.com/snakers4/silero-vad>
- Azure Speech Korean and Custom Speech feature support:
  <https://learn.microsoft.com/azure/ai-services/speech-service/language-support>
- Google Cloud Speech-to-Text model adaptation and phrase sets:
  <https://cloud.google.com/speech-to-text/docs/adaptation-model>
- Amazon Transcribe language support and Medical limitation:
  <https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html>
  and <https://docs.aws.amazon.com/transcribe/latest/APIReference/API_CreateMedicalVocabulary.html>
- AI-Hub medical-domain speech dataset:
  <https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=566>
- Korean telemedicine Whisper domain fine-tuning study:
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC13091973/>
