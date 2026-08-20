#!/usr/bin/env python3
"""Smoke test for the timestamped capture-event staging contract."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

os.environ["BRIDGE_API_KEY"] = "capture-event-smoke-key"
os.environ["REQUIRE_API_KEY"] = "true"
os.environ["ALLOW_INSECURE_LAN"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import app as bridge  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rayban_capture_events_") as tmp:
        root = Path(tmp)
        bridge.DB_PATH = root / "bridge.db"
        bridge.ASYNC_RESULTS.clear()
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(bridge.DB_PATH) as conn:
            conn.executescript(schema)

        # This test proves the local capture-event contract. Keep the optional
        # moai_web pre-review enrichment deterministic and avoid sending
        # synthetic non-UUID fixture IDs to the live Supabase REST endpoint.
        bridge._moai_fetch_rows = lambda table, params: []

        client = TestClient(bridge.app)
        headers = {
            "x-api-key": os.environ["BRIDGE_API_KEY"],
            "x-glasspt-org-id": "org-capture-smoke",
            "x-glasspt-provider-person-id": "provider-capture-smoke",
        }

        started = client.post(
            "/visit-sessions/start",
            headers=headers,
            json={
                "organization_id": "org-capture-smoke",
                "provider_person_id": "provider-capture-smoke",
                "provider_role": "pilates_instructor",
                "subject_person_id": "person-capture-smoke",
                "encounter_id": "encounter-capture-smoke",
                "patient_alias": "Smoke Alias",
                "update_glass": False,
            },
        )
        require(started.status_code == 200, f"visit session start failed: {started.text}")
        session_id = started.json()["session"]["id"]
        require(
            started.json()["session"]["provider_role"] == "pilates_instructor",
            "provider role should be retained on visit session",
        )

        created = client.post(
            "/capture-events",
            headers=headers,
            json={
                "visit_session_id": session_id,
                "source_event_id": "media-event-smoke",
                "source_type": "video",
                "event_type": "media",
                "candidate_type": "video_evidence",
                "start_ms": 1250,
                "confidence": 0.91,
                "payload": {
                    "media_event_type": "video_evidence",
                    "file_name": "clip.mp4",
                    "capture_origin": "rayban_dat_camera",
                },
            },
        )
        require(created.status_code == 200, f"capture event create failed: {created.text}")
        event = created.json()["event"]
        require(event["visit_session_id"] == session_id, "session link missing")
        require(event["encounter_id"] == "encounter-capture-smoke", "encounter link missing")
        require(event["start_ms"] == 1250, "timestamp missing")
        require(event["payload"]["file_name"] == "clip.mp4", "payload missing")
        require(
            event["payload"]["capture_origin"] == "rayban_dat_camera",
            "capture origin must survive bridge persistence",
        )
        require(event["payload"]["action_type"] == "observation", "video evidence action type missing")
        require(event["payload"]["provider_role"] == "pilates_instructor", "provider role should flow to capture event")
        require(event["status"] == "draft", "capture events must start as draft")

        extracted = client.post(
            "/capture-events/extract",
            headers=headers,
            json={
                "visit_session_id": session_id,
                "source_event_id": "audio-event-semantic-smoke",
                "source_type": "audio",
                "capture_origin": "rayban_hfp_microphone",
                "text": (
                    "왼쪽 무릎 정렬을 확인했습니다. 앉았다 일어나기에서 균형이 흔들립니다. "
                    "이제 앉았다 일어나기 운동을 10회 반복하고 집에서 매일 하세요."
                ),
            },
        )
        require(extracted.status_code == 200, f"transcript extraction failed: {extracted.text}")
        extracted_events = extracted.json()["events"]
        extracted_types = {item["candidate_type"] for item in extracted_events}
        require(
            {"positioning_alignment", "functional_task", "exercise_instruction", "home_program"}
            <= extracted_types,
            f"semantic candidates missing: {extracted_types}",
        )
        require(all(item["status"] == "draft" for item in extracted_events), "semantic candidates must start as draft")
        require(
            all(item["payload"].get("capture_origin") == "rayban_hfp_microphone" for item in extracted_events),
            "explicit transcript extraction must retain HFP provenance",
        )
        action_types = {item["candidate_type"]: item["payload"]["action_type"] for item in extracted_events}
        require(action_types["positioning_alignment"] == "assessment", "positioning should be assessment")
        require(action_types["functional_task"] == "observation", "functional task should be observation")
        require(action_types["exercise_instruction"] == "instruction", "exercise instruction should be instruction")
        require(action_types["home_program"] == "home_program", "home program action type missing")
        functional_event = next(item for item in extracted_events if item["candidate_type"] == "functional_task")
        require(
            functional_event["payload"]["core_task"] == "sit_to_stand",
            "functional task should preserve the explicit core task",
        )
        require(
            functional_event["payload"]["assessment_type"] == "functional_task",
            "functional task should preserve its assessment type",
        )
        require(
            functional_event["payload"]["activity_name"] == "sit_to_stand",
            "functional task should preserve the explicit movement name",
        )
        correction_event = next(item for item in extracted_events if item["candidate_type"] == "movement_correction")
        require(
            correction_event["payload"]["intervention_type"] == "movement_correction",
            "movement correction should preserve its intervention type",
        )
        instruction_event = next(item for item in extracted_events if item["candidate_type"] == "exercise_instruction")
        require(
            instruction_event["payload"]["repetition_count"] == 10,
            "exercise instruction should preserve explicit repetition count",
        )
        require(
            instruction_event["payload"]["instruction_type"] == "exercise_instruction",
            "exercise instruction should preserve its instruction type",
        )
        require(
            instruction_event["payload"]["semantic"]["domain"] == "instruction",
            "semantic domain should be retained",
        )
        require(
            functional_event["payload"]["semantic"]["provider_role"] == "pilates_instructor",
            "provider role should be retained in the semantic snapshot",
        )

        advanced = client.post(
            "/capture-events/extract",
            headers=headers,
            json={
                "visit_session_id": session_id,
                "source_event_id": "advanced-semantic-smoke",
                "source_type": "audio",
                "text": (
                    "오버헤드 스쿼트 동작 평가에서 무릎 안쪽 모임을 관찰했습니다. "
                    "필라테스 리포머 풋워크 중립 척추 중재를 하고, 3세트 8회 반복, RPE 6, "
                    "통증 VAS 2/10, 휴식 30초로 진행합니다. 골반이 떨어지고 숨을 참습니다."
                ),
            },
        )
        require(advanced.status_code == 200, f"advanced semantic extraction failed: {advanced.text}")
        advanced_events = advanced.json()["events"]
        advanced_by_type = {item["candidate_type"]: item for item in advanced_events}
        require(
            advanced_by_type["assessment_finding"]["payload"]["assessment_name"] == "movement_screen",
            "movement screen assessment should be identified",
        )
        intervention = advanced_by_type["intervention_started"]
        require(
            intervention["payload"]["intervention_type"] == "pilates_reformer",
            "Pilates reformer intervention should be identified",
        )
        require(
            intervention["payload"]["activity_name"] == "reformer_footwork",
            "reformer footwork activity should be identified",
        )
        instruction = advanced_by_type["exercise_instruction"]
        require(instruction["payload"]["set_count"] == 3, "set count should be extracted")
        require(instruction["payload"]["repetition_count"] == 8, "repetition count should be extracted")
        require(instruction["payload"]["pain_score"] == 2.0, "pain score should be extracted")
        require(instruction["payload"]["rpe_score"] == 6.0, "RPE should be extracted")
        require(instruction["payload"]["rest_duration_seconds"] == 30, "rest duration should be extracted")
        require(
            "knee_valgus" in advanced_by_type["assessment_finding"]["payload"]["compensations"],
            "knee valgus compensation should be extracted",
        )
        require(
            "breath_holding" in advanced_by_type["movement_correction"]["payload"]["compensations"],
            "breath holding compensation should be extracted",
        )

        custom = client.post(
            "/capture-events/extract",
            headers=headers,
            json={
                "visit_session_id": session_id,
                "source_event_id": "custom-semantic-smoke",
                "source_type": "audio",
                "capture_origin": "rayban_hfp_microphone",
                "text": (
                    "Y-balance 평가를 시작합니다. 몬스터 워크 운동을 가르쳐 주세요. "
                    "발목 안정화 중재를 설명합니다. 세션 후 어지럼 반응을 기록합니다."
                ),
            },
        )
        require(custom.status_code == 200, f"custom semantic extraction failed: {custom.text}")
        custom_events = custom.json()["events"]
        custom_by_type = {item["candidate_type"]: item for item in custom_events}
        require("assessment_started" in custom_by_type, "custom assessment candidate missing")
        require("exercise_instruction" in custom_by_type, "custom instruction candidate missing")
        require("intervention_started" in custom_by_type, "custom intervention candidate missing")
        require("response_tolerance" in custom_by_type, "custom response candidate missing")
        require(
            custom_by_type["assessment_started"]["payload"]["semantic"]["assessment_tool_detail"]
            == "Y-balance 평가를 시작합니다",
            "unclassified assessment text must survive persistence",
        )
        require(
            custom_by_type["exercise_instruction"]["payload"]["semantic"]["activity_detail"]
            == "몬스터 워크 운동을 가르쳐 주세요",
            "unclassified activity text must survive persistence",
        )
        require(
            custom_by_type["intervention_started"]["payload"]["semantic"]["intervention_detail"]
            == "발목 안정화 중재를 설명합니다",
            "intervention detail must survive persistence",
        )
        require(
            custom_by_type["exercise_instruction"]["payload"]["semantic"]["instruction_text"]
            == "몬스터 워크 운동을 가르쳐 주세요",
            "instruction source text must survive persistence",
        )
        require(
            custom_by_type["response_tolerance"]["payload"]["semantic"]["response_note"]
            == "세션 후 어지럼 반응을 기록합니다",
            "response note must survive persistence",
        )

        extracted_again = client.post(
            "/capture-events/extract",
            headers=headers,
            json={
                "visit_session_id": session_id,
                "source_event_id": "audio-event-semantic-smoke",
                "source_type": "audio",
                "text": (
                    "왼쪽 무릎 정렬을 확인했습니다. 앉았다 일어나기에서 균형이 흔들립니다. "
                    "이제 앉았다 일어나기 운동을 10회 반복하고 집에서 매일 하세요."
                ),
            },
        )
        require(extracted_again.status_code == 200, f"semantic idempotency retry failed: {extracted_again.text}")
        require(
            [item["id"] for item in extracted_again.json()["events"]]
            == [item["id"] for item in extracted_events],
            "repeated transcript extraction must be idempotent",
        )

        automatic = client.post(
            "/ingest",
            headers=headers,
            json={
                "source": "rayban_hfp_microphone",
                "event_type": "text",
                "text": "평가를 시작하고 오른쪽 어깨 가동범위는 90도입니다. 집에서 매일 운동하세요.",
                "patient_name": "Smoke Alias",
                "owner_org_id": "org-capture-smoke",
                "owner_provider_person_id": "provider-capture-smoke",
                "subject_person_id": "person-capture-smoke",
                "physio_session_id": "encounter-auto-semantic-smoke",
            },
        )
        require(automatic.status_code == 200, f"automatic ingest failed: {automatic.text}")
        automatic_events = automatic.json().get("capture_events") or []
        require(len(automatic_events) >= 3, "automatic ingest should create transcript capture events")
        require(all(item["status"] == "draft" for item in automatic_events), "automatic events must remain draft")
        require(
            all(item["payload"].get("capture_origin") == "rayban_hfp_microphone" for item in automatic_events),
            "media-derived transcript candidates must retain HFP provenance",
        )

        approved = client.patch(
            f"/capture-events/{event['id']}",
            headers=headers,
            json={
                "status": "approved",
                "reviewed_by": "provider-capture-smoke",
                "payload": {
                    "media_event_type": "video_evidence",
                    "review_note": "verified",
                    "capture_origin": "rayban_dat_camera",
                },
            },
        )
        require(approved.status_code == 200, f"capture event update failed: {approved.text}")
        require(approved.json()["event"]["status"] == "approved", "approval status missing")
        require(approved.json()["event"]["reviewed_by"] == "provider-capture-smoke", "reviewer missing")

        listed = client.get(f"/visit-sessions/{session_id}/capture-events", headers=headers)
        require(listed.status_code == 200, f"capture event list failed: {listed.text}")
        require(
            len(listed.json()["items"]) == 1 + len(extracted_events) + len(advanced_events) + len(custom_events),
            "capture event list should contain media plus all extracted semantic events",
        )

        encounter_listed = client.get(
            "/capture-events?encounter_id=encounter-capture-smoke",
            headers=headers,
        )
        require(encounter_listed.status_code == 200, f"encounter capture list failed: {encounter_listed.text}")
        require(encounter_listed.json()["items"][0]["status"] == "approved", "updated event not readable")
        require(
            encounter_listed.json()["items"][0]["payload"]["capture_origin"] == "rayban_dat_camera",
            "capture origin must survive bridge readback",
        )

        invalid = client.post(
            "/capture-events",
            headers=headers,
            json={
                "visit_session_id": session_id,
                "source_type": "therapist_tag",
                "event_type": "assessment_finding",
                "start_ms": 5000,
                "end_ms": 4000,
            },
        )
        require(invalid.status_code == 400, "invalid timestamp should be rejected")

    print("capture_event_smoke_test: PASS")


if __name__ == "__main__":
    main()
