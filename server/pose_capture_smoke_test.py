"""Deterministic smoke checks for bridge-side MediaPipe pose evidence."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

os.environ.setdefault("REQUIRE_API_KEY", "false")
os.environ.setdefault("REQUIRE_PATIENT_CONSENT", "false")

import bridge_core  # noqa: E402
from app import (  # noqa: E402
    _conn,
    _create_pose_capture_events,
    _extract_video_transcript_capture_text,
)
from lib.pose_capture import (  # noqa: E402
    POSE_EXTRACTOR_VERSION,
    build_pose_capture_candidates,
    summarize_pose_samples,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    # Use an isolated throwaway DB so this test does not depend on a
    # previously initialized local storage/bridge.db (e.g. in CI).
    tmp_dir = Path(tempfile.mkdtemp(prefix="rayban_pose_smoke_"))
    bridge_core.DB_PATH = tmp_dir / "bridge.db"
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(bridge_core.DB_PATH) as schema_conn:
        schema_conn.executescript(schema)

    samples = []
    for index, (left_angle, right_angle) in enumerate(((82, 106), (95, 118), (110, 125))):
        samples.append(
            {
                "timestamp_ms": index * 1_000,
                "measured_at": f"2026-08-16T00:00:0{index}+00:00",
                "visibility_mean": 0.91,
                "measurements": [
                    {
                        "angle": left_angle,
                        "metric_id": "MET.ROM.KNEE.FLEX",
                        "metric_label": "Knee Flexion",
                        "side": "left",
                    },
                    {
                        "angle": right_angle,
                        "metric_id": "MET.ROM.KNEE.FLEX",
                        "metric_label": "Knee Flexion",
                        "side": "right",
                    },
                ],
            }
        )

    summary = summarize_pose_samples(samples, 3)
    require(summary is not None, "pose summary should be generated")
    require(summary["processing_mode"] == "on_device_mediapipe", "pose mode contract missing")
    require(summary["review_status"] == "provider_review_required", "pose review gate missing")
    require(summary["sample_count"] == 3, "sample count mismatch")
    require(summary["asymmetry_entries"], "left/right asymmetry should be retained as evidence")

    candidates = build_pose_capture_candidates(summary)
    candidate_types = {candidate["candidate_type"] for candidate in candidates}
    require("rom_measurement" in candidate_types, "ROM candidate missing")
    require("positioning_alignment" in candidate_types, "alignment candidate missing")
    require("video_evidence" in candidate_types, "video evidence candidate missing")
    candidate_action_types = {
        candidate["candidate_type"]: candidate["payload"].get("action_type")
        for candidate in candidates
    }
    require(candidate_action_types["rom_measurement"] == "assessment", "ROM should be assessment")
    require(candidate_action_types["positioning_alignment"] == "assessment", "alignment should be assessment")
    require(candidate_action_types["video_evidence"] == "observation", "pose evidence should be observation")
    rom_candidate = next(candidate for candidate in candidates if candidate["candidate_type"] == "rom_measurement")
    require(
        rom_candidate["payload"]["semantic"]["assessment_type"] == "range_of_motion",
        "ROM semantic assessment type missing",
    )
    alignment_candidate = next(
        candidate for candidate in candidates if candidate["candidate_type"] == "positioning_alignment"
    )
    require(
        alignment_candidate["payload"]["semantic"]["core_task"] == "positioning",
        "alignment semantic core task missing",
    )
    require(
        all(candidate["payload"].get("review_required") == "true" for candidate in candidates),
        "pose candidates must remain review-first",
    )

    source_event_id = "pose-smoke-source"
    encounter_id = "pose-smoke-encounter"
    with _conn() as conn:
        first = _create_pose_capture_events(
            conn,
            candidates=candidates,
            encounter_id=encounter_id,
            organization_id="pose-smoke-org",
            provider_person_id="pose-smoke-provider",
            subject_person_id="pose-smoke-subject",
            source_event_id=source_event_id,
            source_media_id="pose-smoke-media",
            start_ms=0,
            end_ms=3_000,
            capture_origin="rayban_dat_camera",
        )
        conn.commit()
        second = _create_pose_capture_events(
            conn,
            candidates=candidates,
            encounter_id=encounter_id,
            organization_id="pose-smoke-org",
            provider_person_id="pose-smoke-provider",
            subject_person_id="pose-smoke-subject",
            source_event_id=source_event_id,
            source_media_id="pose-smoke-media",
            start_ms=0,
            end_ms=3_000,
        )
        conn.commit()

    require(first, "pose capture events should be created")
    require(len(first) == len(second), "idempotent pose extraction count mismatch")
    require({event["id"] for event in first} == {event["id"] for event in second}, "pose extraction duplicated events")
    require(all(event["source_type"] == "pose" for event in first), "pose source type missing")
    require(all(event["status"] == "draft" for event in first), "pose events must start as draft")
    require(
        all(event["payload"].get("capture_origin") == "rayban_dat_camera" for event in first),
        "pose events must retain camera provenance",
    )
    require(all(event["payload"].get("extractor_version") == POSE_EXTRACTOR_VERSION for event in first), "pose extractor version missing")
    transcript = _extract_video_transcript_capture_text(
        "[Ray-Ban 영상] 파일=clip.mp4\n\n"
        "[치료사 음성 기록 — S> 섹션 참고]\n오른쪽 무릎 정렬을 확인했습니다.\n\n"
        "[영상 분석 3프레임]\nt+0s: 1명 감지"
    )
    require(transcript == "오른쪽 무릎 정렬을 확인했습니다.", "video operational notes leaked into transcript extraction")

    cycle_summary = summarize_pose_samples(
        [
            {
                "timestamp_ms": index * 1_000,
                "measured_at": f"2026-08-16T00:01:0{index}+00:00",
                "measurements": [{
                    "angle": angle,
                    "metric_id": "MET.ROM.KNEE.FLEX",
                    "metric_label": "Knee Flexion",
                    "side": "left",
                }],
            }
            for index, angle in enumerate((30, 80, 30, 80, 30))
        ],
        5,
    )
    cycle_candidates = build_pose_capture_candidates(cycle_summary)
    require(
        any(candidate["candidate_type"] == "functional_task" for candidate in cycle_candidates),
        "cyclic pose motion should produce a review-first repetition candidate",
    )
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("pose_capture_smoke_test: PASS")


if __name__ == "__main__":
    main()
