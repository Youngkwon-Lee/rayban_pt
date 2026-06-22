#!/usr/bin/env python3
"""Smoke test for guided home-visit rehab session orchestration."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

os.environ["BRIDGE_API_KEY"] = "visit-smoke-key"
os.environ["REQUIRE_API_KEY"] = "true"
os.environ["REQUIRE_PATIENT_CONSENT"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import app as bridge  # noqa: E402


API_KEY = os.environ["BRIDGE_API_KEY"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def headers() -> dict[str, str]:
    return {"x-api-key": API_KEY}


def configure_isolated_storage(root: Path) -> None:
    bridge.DB_PATH = root / "bridge.db"
    bridge.UPLOAD_DIR = root / "uploads"
    bridge.CHART_DIR = root / "charts"
    bridge.MASKED_DIR = root / "masked"
    bridge.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    bridge.CHART_DIR.mkdir(parents=True, exist_ok=True)
    bridge.MASKED_DIR.mkdir(parents=True, exist_ok=True)
    bridge.ASYNC_RESULTS.clear()

    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(bridge.DB_PATH) as conn:
        conn.executescript(schema)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rayban_visit_session_smoke_") as tmp:
        configure_isolated_storage(Path(tmp))
        client = TestClient(bridge.app)

        unauth = client.post("/visit-sessions/start", json={})
        require(unauth.status_code == 401, "visit session API should require auth")

        start = client.post(
            "/visit-sessions/start",
            headers=headers(),
            json={
                "organization_id": "11111111-1111-4111-8111-111111111111",
                "provider_person_id": "22222222-2222-4222-8222-222222222222",
                "subject_person_id": "33333333-3333-4333-8333-333333333333",
                "encounter_id": "44444444-4444-4444-8444-444444444444",
                "patient_alias": "P7",
                "history_summary": "Recent gait instability; avoid lens PHI.",
            },
        )
        require(start.status_code == 200, f"visit session start should succeed: {start.text}")
        body = start.json()
        session = body["session"]
        session_id = session["id"]
        require(body["status"] == "started", "start status mismatch")
        require(session["phase"] == "pre_review", "initial phase should be pre_review")
        require(session["encounter_id"] == "44444444-4444-4444-8444-444444444444", "encounter should persist")
        require(body["glass_state"]["patient"] == "P7", "HUD should receive patient alias")
        require("Recent gait" not in body["glass_state"]["message"], "HUD should not show history text by default")

        phase = client.post(
            f"/visit-sessions/{session_id}/phase",
            headers=headers(),
            json={"phase": "assessment", "cue": "TUG 준비"},
        )
        require(phase.status_code == 200, "phase update should succeed")
        require(phase.json()["session"]["phase"] == "assessment", "phase should update")
        require(phase.json()["glass_state"]["message"] == "TUG 준비", "HUD cue should update")

        invalid_phase = client.post(
            f"/visit-sessions/{session_id}/phase",
            headers=headers(),
            json={"phase": "billing"},
        )
        require(invalid_phase.status_code == 400, "invalid phase should be rejected")

        recording = client.post(
            f"/visit-sessions/{session_id}/recording",
            headers=headers(),
            json={"is_recording": True},
        )
        require(recording.status_code == 200, "recording update should succeed")
        require(recording.json()["session"]["recording_status"] == "recording", "recording state should persist")
        require(recording.json()["glass_state"]["is_recording"] is True, "HUD should reflect recording")
        require(recording.json()["glass_state"]["recording_start"], "HUD should include recording start time")

        ingest = client.post(
            "/ingest",
            headers=headers(),
            json={
                "source": "visit-smoke",
                "event_type": "text",
                "patient_name": "P7",
                "owner_org_id": "11111111-1111-4111-8111-111111111111",
                "owner_provider_person_id": "22222222-2222-4222-8222-222222222222",
                "subject_person_id": "33333333-3333-4333-8333-333333333333",
                "physio_session_id": "44444444-4444-4444-8444-444444444444",
                "text": "standing balance intervention completed",
            },
        )
        require(ingest.status_code == 200, f"ingest should succeed: {ingest.text}")
        event_id = ingest.json()["event_id"]

        attach = client.post(
            f"/visit-sessions/{session_id}/events",
            headers=headers(),
            json={"event_id": event_id},
        )
        require(attach.status_code == 200, "event attach should succeed")
        require(attach.json()["session"]["event_ids"] == [event_id], "attached event should persist")
        require(attach.json()["glass_state"]["session_count"] == 1, "HUD count should reflect event count")

        end = client.post(f"/visit-sessions/{session_id}/end", headers=headers())
        require(end.status_code == 200, f"visit end should succeed: {end.text}")
        ended = end.json()
        require(ended["session"]["status"] == "ended", "session should end")
        require(ended["session"]["phase"] == "summary", "ended phase should be summary")
        require(ended["glass_state"]["is_recording"] is False, "HUD should stop recording on end")
        plan = ended["moai_write_plan"]
        require(plan["summary"]["skipped_count"] == 0, f"visit plan should not skip: {plan['skipped']}")
        targets = [op["target_table"] for op in plan["operations"]]
        require("encounters" in targets, "visit end should plan encounter upsert")
        require("encounter_notes" in targets, "visit end should plan progress note upsert")
        note_op = next(op for op in plan["operations"] if op["target_table"] == "encounter_notes")
        require(note_op["payload"]["note_format"] == "progress", "visit note should be progress format")
        require(note_op["payload"]["requires_approval"] is True, "visit note should require approval")
        require(event_id in note_op["payload"]["ai_draft_snapshot"]["linked_event_ids"], "note should link event ids")

    print("OK: visit session orchestration smoke test passed")


if __name__ == "__main__":
    main()
