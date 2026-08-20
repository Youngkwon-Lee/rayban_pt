#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import wave
from pathlib import Path

import app as bridge


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write_silent_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 16_000)


def main() -> None:
    original_raw_media_dir = bridge.RAW_MEDIA_DIR
    with tempfile.TemporaryDirectory(prefix="rayban-raw-media-") as temp_dir:
        raw_media_dir = Path(temp_dir) / "raw-media"
        raw_media_dir.mkdir()
        bridge.RAW_MEDIA_DIR = raw_media_dir
        try:
            source_audio = Path(temp_dir) / "capture.wav"
            _write_silent_wav(source_audio)
            staged_audio = bridge.stage_raw_media(
                source_audio,
                raw_media_dir,
                bridge.RawMediaStage(
                    event_id="event-audio",
                    kind="raw_audio",
                    transcript_text="테스트 음성",
                    consent_id="consent-test",
                ),
            )

            require(staged_audio.exists(), "staged audio should exist")
            require(not source_audio.exists(), "source audio should move into staging")

            artifacts = bridge._list_event_artifacts("event-audio")
            require(len(artifacts) == 1, "audio event should expose one raw artifact")
            artifact = artifacts[0]
            require(artifact["kind"] == "raw_audio", "audio artifact kind mismatch")
            require(artifact["content_type"] == "audio/wav", "audio content type mismatch")
            require(artifact["duration_seconds"] == 1.0, "audio duration mismatch")
            require(artifact["transcript_text"] == "테스트 음성", "audio transcript mismatch")
            require(artifact["consent_id"] == "consent-test", "audio consent provenance mismatch")

            metadata_path = staged_audio.with_suffix(staged_audio.suffix + ".json")
            require(metadata_path.exists(), "raw media metadata should exist")
            json.loads(metadata_path.read_text(encoding="utf-8"))

            require(bridge.delete_raw_media(raw_media_dir, artifact["filename"]), "raw audio should delete")
            require(not staged_audio.exists(), "consumed raw audio should be removed")
            require(not metadata_path.exists(), "consumed metadata should be removed")
        finally:
            bridge.RAW_MEDIA_DIR = original_raw_media_dir

    print("OK: raw media artifact smoke test passed")


if __name__ == "__main__":
    main()
