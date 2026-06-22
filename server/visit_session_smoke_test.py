#!/usr/bin/env python3
"""Smoke test for guided home-visit rehab session orchestration."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["BRIDGE_API_KEY"] = "visit-smoke-key"
os.environ["REQUIRE_API_KEY"] = "true"
os.environ["REQUIRE_PATIENT_CONSENT"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import app as bridge  # noqa: E402
import mlops_harness  # noqa: E402


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


def reset_hud_state() -> None:
    with bridge._glass_lock:
        bridge._glass_state.update(
            {
                "visit_session_id": None,
                "patient": "대기",
                "mode": "ready",
                "message": "준비됨",
                "is_recording": False,
                "recording_start": None,
                "session_count": 0,
                "event_role_counts": {},
                "readiness": "ready",
                "error_state": None,
            }
        )


def exercise_hud_scope_token_candidate_filter() -> None:
    with tempfile.TemporaryDirectory(prefix="rayban_hud_scope_smoke_") as tmp:
        configure_isolated_storage(Path(tmp))
        reset_hud_state()
        client = TestClient(bridge.app)
        bridge._moai_fetch_rows = lambda table, params: []
        token = bridge.build_hud_scope_token(
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        allowed = client.post(
            "/ingest",
            headers=headers(),
            json={
                "source": "visit-scope-allowed",
                "event_type": "text",
                "patient_name": "Allowed Patient",
                "owner_org_id": "11111111-1111-4111-8111-111111111111",
                "owner_provider_person_id": "22222222-2222-4222-8222-222222222222",
                "subject_person_id": "33333333-3333-4333-8333-333333333333",
                "physio_session_id": "44444444-4444-4444-8444-444444444444",
                "text": "allowed visit candidate",
            },
        )
        require(allowed.status_code == 200, f"allowed seed should succeed: {allowed.text}")

        blocked = client.post(
            "/ingest",
            headers=headers(),
            json={
                "source": "visit-scope-blocked",
                "event_type": "text",
                "patient_name": "Blocked Patient",
                "owner_org_id": "aaaaaaaa-1111-4111-8111-111111111111",
                "owner_provider_person_id": "bbbbbbbb-2222-4222-8222-222222222222",
                "subject_person_id": "cccccccc-3333-4333-8333-333333333333",
                "physio_session_id": "dddddddd-4444-4444-8444-444444444444",
                "text": "blocked visit candidate",
            },
        )
        require(blocked.status_code == 200, f"blocked seed should succeed: {blocked.text}")

        next_visit = client.get(
            f"/glass/visits/next?hud_token={token}",
            headers=headers(),
        )
        require(next_visit.status_code == 200, "scoped HUD next visit should succeed")
        candidate = next_visit.json()["candidate"]
        require(candidate["encounter_id"] == "44444444-4444-4444-8444-444444444444", "scoped HUD should show only provider candidate")
        require(candidate["patient_alias"] == "A. P", "scoped HUD should keep only lens-safe alias")

        exact = client.get(
            f"/glass/visits/next?hud_token={token}&candidate_id=44444444-4444-4444-8444-444444444444",
            headers=headers(),
        )
        require(exact.status_code == 200, "exact encounter HUD candidate should resolve")
        require(
            exact.json()["candidate"]["encounter_id"] == "44444444-4444-4444-8444-444444444444",
            "candidate_id should accept encounter id for physio_app deep links",
        )

        blocked_exact = client.get(
            f"/glass/visits/next?hud_token={token}&candidate_id=dddddddd-4444-4444-8444-444444444444",
            headers=headers(),
        )
        require(blocked_exact.status_code == 200, "out-of-scope exact candidate lookup should not fail")
        require(blocked_exact.json()["candidate"] is None, "out-of-scope encounter should stay hidden")

        invalid = client.get("/glass/visits/next?hud_token=bad-token", headers=headers())
        require(invalid.status_code == 401, "invalid HUD scope token should be rejected")


def exercise_remote_today_candidate_ranking() -> None:
    now = datetime.now(timezone.utc)
    nearer = (now + timedelta(minutes=20)).isoformat(timespec="seconds").replace("+00:00", "Z")
    later = (now + timedelta(hours=3)).isoformat(timespec="seconds").replace("+00:00", "Z")
    captured_params: list[tuple[str, str]] = []
    original_fetch = bridge._moai_fetch_rows

    def fake_moai_fetch(table: str, params) -> list[dict]:
        captured_params.extend(list(params))
        return [
            {
                "id": "later-encounter",
                "organization_id": "11111111-1111-4111-8111-111111111111",
                "provider_person_id": "22222222-2222-4222-8222-222222222222",
                "subject_person_id": "99999999-3333-4333-8333-333333333333",
                "period_start": later,
                "session_type": "home_visit_pt",
                "status": "scheduled",
                "care_setting": "home_visit",
            },
            {
                "id": "nearer-encounter",
                "organization_id": "11111111-1111-4111-8111-111111111111",
                "provider_person_id": "22222222-2222-4222-8222-222222222222",
                "subject_person_id": "33333333-3333-4333-8333-333333333333",
                "period_start": nearer,
                "session_type": "home_visit_pt",
                "status": "scheduled",
                "care_setting": "home_visit",
            },
        ]

    bridge._moai_fetch_rows = fake_moai_fetch
    try:
        candidates = bridge._list_moai_glass_visit_candidates(
            limit=10,
            scope={
                "organization_id": "11111111-1111-4111-8111-111111111111",
                "provider_person_id": "22222222-2222-4222-8222-222222222222",
            },
        )
        require(candidates[0]["encounter_id"] == "nearer-encounter", "today candidates should rank nearest appointment first")
        period_filters = [value for key, value in captured_params if key == "period_start"]
        require(any(value.startswith("gte.") for value in period_filters), "remote lookup should constrain start of today window")
        require(any(value.startswith("lt.") for value in period_filters), "remote lookup should constrain end of today window")
        require(
            ("provider_person_id", "eq.22222222-2222-4222-8222-222222222222") in captured_params,
            "remote lookup should be provider scoped",
        )
        require(
            ("organization_id", "eq.11111111-1111-4111-8111-111111111111") in captured_params,
            "remote lookup should be organization scoped",
        )
    finally:
        bridge._moai_fetch_rows = original_fetch


def exercise_remote_visit_candidate() -> None:
    with tempfile.TemporaryDirectory(prefix="rayban_remote_visit_candidate_smoke_") as tmp:
        configure_isolated_storage(Path(tmp))
        reset_hud_state()
        client = TestClient(bridge.app)
        remote_candidate = {
            "id": "moai:55555555-5555-4555-8555-555555555555",
            "patient_alias": "P-333333",
            "organization_id": "11111111-1111-4111-8111-111111111111",
            "provider_person_id": "22222222-2222-4222-8222-222222222222",
            "subject_person_id": "33333333-3333-4333-8333-333333333333",
            "physio_client_id": None,
            "encounter_id": "55555555-5555-4555-8555-555555555555",
            "source_event_id": None,
            "session_label": "home_visit_pt",
            "created_at": "2026-06-22T09:00:00Z",
            "readiness": "ready",
            "source": "moai_web.encounters",
            "status": "scheduled",
            "care_setting": "home_visit",
        }
        original_lookup = bridge._list_moai_glass_visit_candidates
        original_fetch = bridge._moai_fetch_rows
        bridge._list_moai_glass_visit_candidates = lambda limit=10, scope=None: [remote_candidate]

        def fake_moai_fetch(table: str, params: dict[str, str]) -> list[dict]:
            if table == "encounter_notes":
                return [
                    {
                        "id": "note-1",
                        "encounter_id": "prev-enc-1",
                        "note_format": "progress",
                        "status": "draft",
                        "approval_status": "pending",
                        "objective": "환자 김민수 010-1234-5678 기립 유지 20초, 후반부 체간 흔들림 관찰",
                        "assessment": "체간 안정성 저하와 피로 누적 소견",
                        "plan": "기립 유지 훈련",
                        "note_content": "S/O/A/P draft",
                        "created_at": "2026-06-20T09:00:00Z",
                    }
                ]
            if table == "observations":
                return [
                    {
                        "id": "obs-1",
                        "code": "assist_level",
                        "code_display": "Assist Level",
                        "status": "final",
                        "interpretation": "unchanged",
                        "value_string": "mod assist",
                        "note": "Therapist-reviewed label",
                        "effective_datetime": "2026-06-20T09:15:00Z",
                    }
                ]
            if table == "activity_sessions":
                return [
                    {
                        "id": "act-1",
                        "activity_type": "home_exercise",
                        "status": "completed",
                        "notes": "standing balance practice completed",
                        "metrics": {"assist_level": "mod", "performance": "fair"},
                    }
                ]
            if table == "client_media_summaries":
                return [
                    {
                        "id": "media-summary-1",
                        "media_kind": "video",
                        "body_region": "hip",
                        "summary_text": "hip strategy observed",
                    }
                ]
            return []

        bridge._moai_fetch_rows = fake_moai_fetch
        try:
            next_visit = client.get("/glass/visits/next", headers=headers())
            require(next_visit.status_code == 200, "remote HUD next visit should succeed")
            candidate = next_visit.json()["candidate"]
            require(candidate["source"] == "moai_web.encounters", "HUD should prefer moai encounter candidates")
            require(candidate["patient_alias"] == "P-333333", "remote candidate should keep lens-safe alias")
            preview = candidate["record_preview"]
            require(preview["lens_safe"] is True, "HUD candidate record preview should be lens-safe")
            require(preview["signals"]["notes_count"] == 1, "candidate preview should count notes")
            require(preview["signals"]["pending_notes_count"] == 1, "candidate preview should flag pending notes")
            require(preview["signals"]["observations_count"] == 1, "candidate preview should count observations")
            require(preview["signals"]["activity_sessions_count"] == 1, "candidate preview should count activities")
            require("노트 1" in preview["cue"], "candidate preview should include note count")
            require("미승인 확인" in preview["cue"], "candidate preview should include pending-note flag")
            require(any("체간 안정성 저하" in line for line in preview["lines"]), "candidate preview should include note snippet")
            require(any("Assist Level mod assist" in line for line in preview["lines"]), "candidate preview should include observation snippet")
            require("김민수" not in json.dumps(preview, ensure_ascii=False), "candidate preview should redact patient names")
            require("010-1234-5678" not in json.dumps(preview, ensure_ascii=False), "candidate preview should redact phones")

            start = client.post(
                "/glass/visits/start",
                headers=headers(),
                json={"candidate_id": candidate["id"]},
            )
            require(start.status_code == 200, f"remote HUD visit start should succeed: {start.text}")
            session = start.json()["session"]
            require(
                session["encounter_id"] == "55555555-5555-4555-8555-555555555555",
                "remote encounter id should anchor visit session",
            )
            require(session["patient_alias"] == "P-333333", "remote alias should persist to visit session")
            require(
                "moai_web.encounters" in session["history_summary"],
                "remote start summary should record candidate source",
            )
            require("노트 1" in session["cue"], "pre-review cue should include note count")
            require("미승인 확인" in session["cue"], "pre-review cue should flag pending notes")
            insight = start.json()["glass_state"]["last_insight"]
            require(insight["source"] == "moai_web.pre_review", "HUD insight should come from pre-review")
            require(insight["lens_safe"] is True, "pre-review insight should be lens-safe")
            require(insight["signals"]["observations_count"] == 1, "pre-review should count observations")
            require(insight["signals"]["activity_sessions_count"] == 1, "pre-review should count activities")
            require(insight["signals"]["media_summaries_count"] == 1, "pre-review should count media summaries")
            require(any("체간 안정성 저하" in line for line in insight["lines"]), "pre-review insight should include note snippet")
        finally:
            bridge._list_moai_glass_visit_candidates = original_lookup
            bridge._moai_fetch_rows = original_fetch
            reset_hud_state()


def main() -> None:
    exercise_hud_scope_token_candidate_filter()
    exercise_remote_today_candidate_ranking()
    exercise_remote_visit_candidate()

    with tempfile.TemporaryDirectory(prefix="rayban_visit_session_smoke_") as tmp:
        configure_isolated_storage(Path(tmp))
        reset_hud_state()
        client = TestClient(bridge.app)
        bridge._moai_fetch_rows = lambda table, params: []

        unauth = client.post("/visit-sessions/start", json={})
        require(unauth.status_code == 401, "visit session API should require auth")

        seed = client.post(
            "/ingest",
            headers=headers(),
            json={
                "source": "visit-seed",
                "event_type": "text",
                "patient_name": "P7",
                "owner_org_id": "11111111-1111-4111-8111-111111111111",
                "owner_provider_person_id": "22222222-2222-4222-8222-222222222222",
                "subject_person_id": "33333333-3333-4333-8333-333333333333",
                "physio_session_id": "44444444-4444-4444-8444-444444444444",
                "text": "visit candidate seed",
            },
        )
        require(seed.status_code == 200, f"visit seed should succeed: {seed.text}")

        next_visit = client.get("/glass/visits/next", headers=headers())
        require(next_visit.status_code == 200, "HUD next visit should succeed")
        candidate = next_visit.json()["candidate"]
        require(candidate["patient_alias"] == "P*", "HUD should expose only lens-safe patient alias")
        require(candidate["encounter_id"] == "44444444-4444-4444-8444-444444444444", "HUD candidate should keep encounter")
        local_preview = candidate["record_preview"]
        require(local_preview["source"] == "local.record_preview", "local HUD candidate should use local record preview")
        require(any("visit candidate seed" in line for line in local_preview["lines"]), "local preview should include local event text")
        require("원격 기록 연결 전" not in json.dumps(local_preview, ensure_ascii=False), "local preview should not show remote placeholder")
        require("source_visit_session_id" not in json.dumps(local_preview, ensure_ascii=False), "local preview should hide internal visit marker ids")

        start = client.post(
            "/neural-band/event",
            headers=headers(),
            json={"gesture": "start_visit", "device_id": "band-visit-start"},
        )
        require(start.status_code == 200, f"visit session start should succeed: {start.text}")
        body = start.json()["executed"]
        session = body["session"]
        session_id = session["id"]
        require(body["executed"] is True, "neural band explicit start should execute visit start")
        require(session["phase"] == "pre_review", "initial phase should be pre_review")
        require(session["encounter_id"] == "44444444-4444-4444-8444-444444444444", "encounter should persist")
        require(body["glass_state"]["patient"] == "P*", "HUD should receive lens-safe patient alias")
        require(body["glass_state"]["visit_session_id"] == session_id, "HUD should receive active visit session id")

        record_gesture = client.post(
            "/neural-band/event",
            headers=headers(),
            json={"gesture": "swipe_up", "device_id": "band-record-preview"},
        )
        require(record_gesture.status_code == 200, f"record preview gesture should succeed: {record_gesture.text}")
        require(
            record_gesture.json()["mapped_command"] == "nav_up",
            "neural band up gesture should map to HUD up navigation",
        )
        queued_record = client.get("/glass/command", headers=headers()).json()
        require(queued_record["command"] == "nav_up", "up navigation command should be queued for HUD")
        require(queued_record["metadata"]["gesture"] == "swipe_up", "up navigation command should preserve gesture")

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
        require(recording.json()["glass_state"]["visit_session_id"] == session_id, "HUD should expose active visit session")

        hud_toggle = client.post(
            "/glass/command",
            headers=headers(),
            json={"command": "toggle_recording"},
        )
        require(hud_toggle.status_code == 200, "HUD toggle command should succeed")
        require(hud_toggle.json()["executed"]["executed"] is True, "HUD toggle should execute server-side")
        require(hud_toggle.json()["executed"]["session"]["recording_status"] == "idle", "HUD toggle should stop recording")

        hud_next = client.post(
            "/glass/command",
            headers=headers(),
            json={"command": "next_phase"},
        )
        require(hud_next.status_code == 200, "HUD next phase command should succeed")
        require(hud_next.json()["executed"]["session"]["phase"] == "intervention", "HUD next phase should advance workflow")
        require(
            hud_next.json()["executed"]["glass_state"]["capture_role"] == "intervention",
            "intervention phase should set active capture role",
        )

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
        require(
            ingest.json()["visit_auto_attach"]["role"] == "intervention",
            "active visit should auto-attach ingest with intervention role",
        )
        require(
            ingest.json()["visit_auto_attach"]["session"]["event_ids"] == [event_id],
            "auto-attached event should persist in active visit",
        )

        next_role = client.post(
            "/glass/command",
            headers=headers(),
            json={"command": "next_role"},
        )
        require(next_role.status_code == 200, "HUD next role command should succeed")
        require(
            next_role.json()["executed"]["glass_state"]["capture_role"] == "home_program",
            "next_role should cycle intervention to home program",
        )

        home_program = client.post(
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
                "text": "home program assigned: supported standing practice",
            },
        )
        require(home_program.status_code == 200, f"home program ingest should succeed: {home_program.text}")
        home_program_event_id = home_program.json()["event_id"]
        require(
            home_program.json()["visit_auto_attach"]["role"] == "home_program",
            "active visit should auto-attach ingest with home program role",
        )
        require(
            home_program.json()["visit_auto_attach"]["session"]["event_ids"] == [event_id, home_program_event_id],
            "auto-attached home program event should persist in active visit",
        )

        attach = client.post(
            f"/visit-sessions/{session_id}/events",
            headers=headers(),
            json={"event_id": event_id, "role": "intervention"},
        )
        require(attach.status_code == 200, "event attach should succeed")
        require(
            attach.json()["session"]["event_ids"] == [event_id, home_program_event_id],
            "reattached event should not duplicate or drop existing events",
        )
        require(
            attach.json()["session"]["event_refs"][0]["role"] == "intervention",
            "attached event should persist explicit role",
        )
        require(attach.json()["glass_state"]["session_count"] == 2, "HUD count should reflect event count")
        require(
            attach.json()["glass_state"]["event_role_counts"]["intervention"] == 1,
            "HUD should count intervention events",
        )

        attach_home_program = client.post(
            f"/visit-sessions/{session_id}/events",
            headers=headers(),
            json={"event_id": home_program_event_id, "role": "home_program"},
        )
        require(attach_home_program.status_code == 200, "home program event attach should succeed")
        require(
            attach_home_program.json()["session"]["event_ids"] == [event_id, home_program_event_id],
            "multiple attached events should persist",
        )
        require(
            attach_home_program.json()["session"]["event_refs"][1]["role"] == "home_program",
            "home program role should persist",
        )
        require(attach_home_program.json()["glass_state"]["session_count"] == 2, "HUD count should reflect all events")
        require(
            attach_home_program.json()["glass_state"]["event_role_counts"]["home_program"] == 1,
            "HUD should count home program events",
        )

        checkpoint = client.post(
            "/neural-band/event",
            headers=headers(),
            json={"gesture": "long_press", "device_id": "band-visit-smoke"},
        )
        require(checkpoint.status_code == 200, f"visit checkpoint should succeed: {checkpoint.text}")
        checkpointed = checkpoint.json()["executed"]
        require(checkpointed["executed"] is True, "neural band long press should execute server-side")
        require(checkpointed["session"]["status"] == "active", "long press should not end immediately")
        require(checkpointed["session"]["phase"] == "summary", "long press should move to summary checkpoint")
        require("확인=종료" in checkpointed["session"]["cue"], "checkpoint cue should ask for confirm")
        require("중재 1" in checkpointed["session"]["cue"], "checkpoint cue should summarize intervention count")
        require("과제 1" in checkpointed["session"]["cue"], "checkpoint cue should summarize home program count")
        require("moai_write_plan" not in checkpointed, "checkpoint should not build write plan yet")

        end = client.post(
            "/neural-band/event",
            headers=headers(),
            json={"gesture": "end_visit_session", "device_id": "band-visit-smoke"},
        )
        require(end.status_code == 200, f"visit end should succeed: {end.text}")
        ended = end.json()["executed"]
        require(ended["executed"] is True, "neural band explicit end should execute final end")
        require(ended["session"]["status"] == "ended", "session should end")
        require(ended["session"]["phase"] == "summary", "ended phase should be summary")
        require(ended["glass_state"]["is_recording"] is False, "HUD should stop recording on end")
        require(ended["glass_state"]["readiness"] == "sync_pending", "HUD should show pending sync readiness")
        require("전송 대기" in ended["glass_state"]["message"], "HUD should show PHI-safe pending sync cue")
        sync_job = ended["moai_sync_job"]
        require(sync_job["status"] == "pending", "visit end should enqueue pending moai sync job")
        require(sync_job["trigger_reason"] == "visit_session_ended", "sync job should record visit trigger")
        require(sync_job["operation_count"] >= 2, "sync job should include planned operations")
        plan = ended["moai_write_plan"]
        require(plan["summary"]["skipped_count"] == 0, f"visit plan should not skip: {plan['skipped']}")
        targets = [op["target_table"] for op in plan["operations"]]
        require("encounters" in targets, "visit end should plan encounter upsert")
        require("encounter_notes" in targets, "visit end should plan progress note upsert")
        note_op = next(op for op in plan["operations"] if op["target_table"] == "encounter_notes")
        require(note_op["payload"]["note_format"] == "progress", "visit note should be progress format")
        require(note_op["payload"]["requires_approval"] is True, "visit note should require approval")
        require(event_id in note_op["payload"]["ai_draft_snapshot"]["linked_event_ids"], "note should link event ids")
        note_content = note_op["payload"]["note_content"]
        require(
            "standing balance intervention completed" in note_content,
            "progress note should summarize linked intervention event",
        )
        require(
            "home program assigned" in note_content,
            "progress note should summarize linked home program event",
        )
        jobs = client.get("/moai-sync/jobs?status=pending&limit=10", headers=headers())
        require(jobs.status_code == 200, "pending moai sync jobs should load")
        queued = [job for job in jobs.json()["items"] if job["event_id"] == sync_job["event_id"]]
        require(queued, "visit session sync job should be visible in pending queue")

        harness_args = type(
            "HarnessArgs",
            (),
            {
                "subject_person_id": None,
                "provider_person_id": None,
                "encounter_id": None,
                "capture_device": "rayban_visit_session",
                "no_resolve_identity": True,
            },
        )()
        bundle, harness_plan = mlops_harness._build_bundle_and_plan(harness_args, sync_job["event_id"])
        require(bundle["context"]["source_type"] == "visit_session_sync_marker", "harness should detect visit sync marker")
        require(
            harness_plan["summary"]["operation_count"] == sync_job["operation_count"],
            "harness visit plan should match queued operation count",
        )
        dry_run = mlops_harness._execute_plan_if_allowed(harness_plan, execute=False)
        require(dry_run["status"] == "dry_run", "harness should dry-run visit sync plan")

        sync_pending_args = type(
            "SyncPendingArgs",
            (),
            {
                "subject_person_id": None,
                "provider_person_id": None,
                "encounter_id": None,
                "capture_device": "rayban_visit_session",
                "no_resolve_identity": True,
                "status": "pending",
                "limit": 10,
                "execute": False,
                "continue_on_error": False,
                "full": False,
            },
        )()
        with contextlib.redirect_stdout(io.StringIO()):
            sync_pending_exit = mlops_harness.cmd_sync_pending(sync_pending_args)
        require(sync_pending_exit == 0, "harness sync-pending should dry-run visit job")
        planned_jobs = client.get("/moai-sync/jobs?status=planned&limit=10", headers=headers())
        require(planned_jobs.status_code == 200, "planned moai sync jobs should load")
        planned = [job for job in planned_jobs.json()["items"] if job["event_id"] == sync_job["event_id"]]
        require(planned, "visit session sync job should become planned after dry-run sync-pending")

    print("OK: visit session orchestration smoke test passed")


if __name__ == "__main__":
    main()
