#!/usr/bin/env python3
"""Privacy-bounded benchmark for Ray-Ban Korean rehabilitation STT.

The manifest may contain sensitive transcript text. Console output and the
default result file therefore contain metrics and identifiers only. Use
--include-text-output only for an access-controlled local labeling workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ALLOWED_SPLITS = {"train", "dev", "test"}
PUNCTUATION_RE = re.compile(r"[^0-9A-Za-z가-힣\s]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
LATERALITY_TERMS = {
    "left": ("왼쪽", "좌측"),
    "right": ("오른쪽", "우측"),
    "bilateral": ("양측", "양쪽"),
}


def _normalized_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text).lower()
    normalized = PUNCTUATION_RE.sub(" ", normalized)
    return normalized.split()


def _normalized_chars(text: str) -> list[str]:
    return list("".join(_normalized_words(text)))


def edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for ref_index, ref_value in enumerate(reference, start=1):
        current = [ref_index]
        for hyp_index, hyp_value in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hyp_index] + 1,
                    previous[hyp_index - 1] + (ref_value != hyp_value),
                )
            )
        previous = current
    return previous[-1]


def _rate(errors: int, reference_units: int, hypothesis_units: int) -> float:
    if reference_units:
        return errors / reference_units
    return 0.0 if hypothesis_units == 0 else 1.0


def _term_counts(reference: str, hypothesis: str, terms: Iterable[str]) -> tuple[int, int]:
    ref = "".join(_normalized_words(reference))
    hyp = "".join(_normalized_words(hypothesis))
    total = 0
    matched = 0
    for term in terms:
        normalized = "".join(_normalized_words(str(term)))
        if normalized and normalized in ref:
            total += 1
            matched += int(normalized in hyp)
    return matched, total


def _token_recall(reference_tokens: Iterable[str], hypothesis_tokens: Iterable[str]) -> tuple[int, int]:
    remaining = list(hypothesis_tokens)
    matched = 0
    total = 0
    for token in reference_tokens:
        total += 1
        if token in remaining:
            matched += 1
            remaining.remove(token)
    return matched, total


def _laterality_tokens(text: str) -> list[str]:
    compact = "".join(_normalized_words(text))
    found: list[str] = []
    for canonical, aliases in LATERALITY_TERMS.items():
        if any(alias in compact for alias in aliases):
            found.append(canonical)
    return found


def score_transcript(reference: str, hypothesis: str, domain_terms: Iterable[str] = ()) -> dict[str, Any]:
    ref_chars = _normalized_chars(reference)
    hyp_chars = _normalized_chars(hypothesis)
    ref_words = _normalized_words(reference)
    hyp_words = _normalized_words(hypothesis)
    char_errors = edit_distance(ref_chars, hyp_chars)
    word_errors = edit_distance(ref_words, hyp_words)
    term_matches, term_total = _term_counts(reference, hypothesis, domain_terms)
    number_matches, number_total = _token_recall(NUMBER_RE.findall(reference), NUMBER_RE.findall(hypothesis))
    laterality_matches, laterality_total = _token_recall(
        _laterality_tokens(reference),
        _laterality_tokens(hypothesis),
    )
    return {
        "char_errors": char_errors,
        "reference_chars": len(ref_chars),
        "cer": round(_rate(char_errors, len(ref_chars), len(hyp_chars)), 6),
        "word_errors": word_errors,
        "reference_words": len(ref_words),
        "wer": round(_rate(word_errors, len(ref_words), len(hyp_words)), 6),
        "domain_term_matches": term_matches,
        "domain_term_total": term_total,
        "domain_term_recall": _safe_ratio(term_matches, term_total),
        "number_matches": number_matches,
        "number_total": number_total,
        "number_recall": _safe_ratio(number_matches, number_total),
        "laterality_matches": laterality_matches,
        "laterality_total": laterality_total,
        "laterality_recall": _safe_ratio(laterality_matches, laterality_total),
    }


def _audio_duration(path: Path) -> float:
    if path.suffix.lower() != ".wav":
        return 0.0
    try:
        with wave.open(str(path), "rb") as audio:
            rate = audio.getframerate()
            return round(audio.getnframes() / rate, 3) if rate else 0.0
    except (OSError, wave.Error):
        return 0.0


def load_manifest(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    sample_ids: set[str] = set()
    session_splits: dict[str, str] = {}
    speaker_splits: dict[str, set[str]] = {}

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"manifest line {line_number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError(f"manifest line {line_number}: object required")
        required = ("sample_id", "audio_path", "reference_text", "split", "session_id", "speaker_id", "capture_origin")
        missing = [field for field in required if not str(record.get(field) or "").strip()]
        if missing:
            raise ValueError(f"manifest line {line_number}: missing {','.join(missing)}")
        sample_id = str(record["sample_id"])
        if sample_id in sample_ids:
            raise ValueError(f"manifest line {line_number}: duplicate sample_id={sample_id}")
        sample_ids.add(sample_id)
        split = str(record["split"])
        if split not in ALLOWED_SPLITS:
            raise ValueError(f"manifest line {line_number}: split must be train/dev/test")
        audio_path = Path(str(record["audio_path"]))
        if not audio_path.is_absolute():
            audio_path = (path.parent / audio_path).resolve()
        if not audio_path.is_file():
            raise ValueError(f"manifest line {line_number}: audio file not found for sample_id={sample_id}")
        session_id = str(record["session_id"])
        previous_split = session_splits.setdefault(session_id, split)
        if previous_split != split:
            raise ValueError(f"session leakage: session_id={session_id} appears in {previous_split} and {split}")
        speaker_splits.setdefault(str(record["speaker_id"]), set()).add(split)
        record["audio_path"] = str(audio_path)
        record["domain_terms"] = [str(term) for term in record.get("domain_terms") or []]
        records.append(record)

    for speaker_id, splits in sorted(speaker_splits.items()):
        if len(splits) > 1:
            warnings.append(f"speaker_overlap:{speaker_id}:{','.join(sorted(splits))}")
    if not records:
        raise ValueError("manifest has no records")
    return records, warnings


class FasterWhisperTranscriber:
    def __init__(self, model_name: str, device: str, compute_type: str):
        from faster_whisper import WhisperModel
        import ctranslate2
        import faster_whisper

        started = time.perf_counter()
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self.model_load_seconds = round(time.perf_counter() - started, 3)
        self.runtime_versions = {
            "faster_whisper": faster_whisper.__version__,
            "ctranslate2": ctranslate2.__version__,
        }

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str,
        initial_prompt: str,
        hotwords: str,
        beam_size: int,
    ) -> tuple[str, dict[str, Any]]:
        options: dict[str, Any] = {"language": language, "beam_size": beam_size}
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        if hotwords:
            options["hotwords"] = hotwords
        segments, info = self.model.transcribe(audio_path, **options)
        segment_list = list(segments)
        text = " ".join(segment.text.strip() for segment in segment_list).strip()
        return text, {
            "detected_language": getattr(info, "language", None),
            "segment_count": len(segment_list),
        }


def _safe_ratio(matches: int, total: int) -> float | None:
    return round(matches / total, 6) if total else None


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [item for item in items if item["status"] == "ok"]
    sums = {
        key: sum(int(item["metrics"][key]) for item in successful)
        for key in (
            "char_errors", "reference_chars", "word_errors", "reference_words",
            "domain_term_matches", "domain_term_total", "number_matches", "number_total",
            "laterality_matches", "laterality_total",
        )
    }
    has_success = bool(successful)
    return {
        "sample_count": len(items),
        "successful_count": len(successful),
        "failed_count": len(items) - len(successful),
        "audio_duration_seconds": round(sum(float(item["audio_duration_seconds"]) for item in items), 3),
        "inference_seconds": round(sum(float(item["inference_seconds"]) for item in items), 3),
        "cer_micro": round(_rate(sums["char_errors"], sums["reference_chars"], 0), 6) if has_success else None,
        "wer_micro": round(_rate(sums["word_errors"], sums["reference_words"], 0), 6) if has_success else None,
        "domain_term_recall": _safe_ratio(sums["domain_term_matches"], sums["domain_term_total"]),
        "number_recall": _safe_ratio(sums["number_matches"], sums["number_total"]),
        "laterality_recall": _safe_ratio(sums["laterality_matches"], sums["laterality_total"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Korean rehabilitation STT without printing transcripts")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="ko")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--initial-prompt", default="")
    parser.add_argument("--hotwords", default="")
    parser.add_argument("--include-text-output", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.beam_size < 1:
        print(json.dumps({"status": "invalid_arguments", "error": "--beam-size must be at least 1"}))
        return 2
    try:
        records, warnings = load_manifest(args.manifest.resolve())
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "invalid_manifest", "error": str(exc)}, ensure_ascii=False))
        return 2

    if args.validate_only:
        print(json.dumps({"status": "valid", "sample_count": len(records), "warnings": warnings}, ensure_ascii=False))
        return 0
    if args.output is None:
        print(json.dumps({"status": "invalid_arguments", "error": "--output is required unless --validate-only is used"}))
        return 2

    transcriber = FasterWhisperTranscriber(args.model, args.device, args.compute_type)
    items: list[dict[str, Any]] = []
    for record in records:
        started = time.perf_counter()
        try:
            hypothesis, metadata = transcriber.transcribe(
                record["audio_path"],
                language=args.language,
                initial_prompt=args.initial_prompt,
                hotwords=args.hotwords,
                beam_size=args.beam_size,
            )
            if not hypothesis:
                raise RuntimeError("empty_transcript")
            item: dict[str, Any] = {
                "sample_id": record["sample_id"],
                "split": record["split"],
                "session_id": record["session_id"],
                "speaker_id": record["speaker_id"],
                "capture_origin": record["capture_origin"],
                "status": "ok",
                "audio_duration_seconds": _audio_duration(Path(record["audio_path"])),
                "inference_seconds": round(time.perf_counter() - started, 3),
                "metrics": score_transcript(record["reference_text"], hypothesis, record["domain_terms"]),
                **metadata,
            }
            if args.include_text_output:
                item["reference_text"] = record["reference_text"]
                item["hypothesis_text"] = hypothesis
            items.append(item)
        except Exception as exc:
            items.append({
                "sample_id": record["sample_id"],
                "split": record["split"],
                "session_id": record["session_id"],
                "speaker_id": record["speaker_id"],
                "capture_origin": record["capture_origin"],
                "status": "error",
                "error_type": type(exc).__name__,
                "audio_duration_seconds": _audio_duration(Path(record["audio_path"])),
                "inference_seconds": round(time.perf_counter() - started, 3),
            })

    result = {
        "schema_version": "rayban-stt-benchmark-v1",
        "run_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": hashlib.sha256(args.manifest.resolve().read_bytes()).hexdigest(),
        "model": {
            "name": args.model,
            "device": args.device,
            "compute_type": args.compute_type,
            "language": args.language,
            "beam_size": args.beam_size,
            "condition_on_previous_text": True,
            "model_load_seconds": transcriber.model_load_seconds,
            "initial_prompt_used": bool(args.initial_prompt),
            "hotwords_used": bool(args.hotwords),
            "runtime_versions": transcriber.runtime_versions,
        },
        "manifest_warnings": warnings,
        "text_included": bool(args.include_text_output),
        "summary": aggregate(items),
        "items": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "complete", "output": str(args.output), "summary": result["summary"]}, ensure_ascii=False))
    return 1 if result["summary"]["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
