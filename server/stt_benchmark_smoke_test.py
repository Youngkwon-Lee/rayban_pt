#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import wave
from pathlib import Path

from fastapi import HTTPException

import bridge_core
from stt_benchmark import aggregate, load_manifest, score_transcript


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _silent_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\x00\x00" * 8_000)


def main() -> None:
    metrics = score_transcript(
        "오른쪽 무릎 굽힘 95도 통증 없음",
        "오른쪽 무릎 9핀 95도 통증 없음",
        ["무릎 굽힘", "통증 없음"],
    )
    require(metrics["cer"] > 0, "CER should detect the clinical term error")
    require(metrics["number_recall"] == 1, "number token should match")
    require(metrics["laterality_recall"] == 1, "laterality should match")
    require(metrics["domain_term_matches"] == 1, "one of two domain terms should match")

    with tempfile.TemporaryDirectory(prefix="rayban-stt-benchmark-") as temp_dir:
        root = Path(temp_dir)
        wav_path = root / "sample.wav"
        manifest_path = root / "manifest.jsonl"
        _silent_wav(wav_path)
        manifest_path.write_text(
            json.dumps({
                "sample_id": "synthetic-001",
                "audio_path": "sample.wav",
                "reference_text": "오른쪽 무릎 굽힘 95도",
                "split": "test",
                "session_id": "synthetic-session-001",
                "speaker_id": "synthetic-speaker-001",
                "capture_origin": "synthetic_tts_8khz",
                "domain_terms": ["무릎 굽힘"],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        records, warnings = load_manifest(manifest_path)
        require(len(records) == 1 and not warnings, "valid manifest should load")
        require(Path(records[0]["audio_path"]).is_absolute(), "audio path should be resolved")

    summary = aggregate([{
        "status": "ok",
        "audio_duration_seconds": 1.0,
        "inference_seconds": 0.5,
        "metrics": metrics,
    }])
    require(summary["successful_count"] == 1, "aggregate should count success")

    original_model_factory = bridge_core._get_whisper_model
    try:
        class EmptyModel:
            def transcribe(self, _path, **_options):
                return iter([type("Segment", (), {"text": ""})()]), None

        bridge_core._get_whisper_model = lambda: EmptyModel()
        try:
            bridge_core.stt_whisper_local("synthetic.wav")
            raise AssertionError("empty STT must not be treated as success")
        except HTTPException as exc:
            require(exc.detail["code"] == "STT_EMPTY", "empty STT should have an explicit error code")

        class BrokenModel:
            def transcribe(self, _path, **_options):
                raise RuntimeError("synthetic failure")

        bridge_core._get_whisper_model = lambda: BrokenModel()
        try:
            bridge_core.stt_whisper_local("synthetic.wav")
            raise AssertionError("failed STT must not be treated as success")
        except HTTPException as exc:
            require(exc.detail["code"] == "STT_FAILED", "failed STT should have an explicit error code")
    finally:
        bridge_core._get_whisper_model = original_model_factory

    print("stt_benchmark_smoke_test: PASS")


if __name__ == "__main__":
    main()
