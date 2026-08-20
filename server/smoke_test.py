#!/usr/bin/env python3
"""Local safety smoke test for the Rayban bridge API.

Runs against an isolated temporary database and storage directory. It does not
modify the real server/storage database used by the dev bridge.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any


os.environ["BRIDGE_API_KEY"] = "smoke-test-key"
os.environ["REQUIRE_API_KEY"] = "true"
os.environ["ALLOW_INSECURE_LAN"] = "false"
os.environ["ENABLE_FILE_DOWNLOADS"] = "false"
os.environ["IMAGE_STORE"] = "false"
os.environ["AUDIO_STORE"] = "false"
os.environ["VIDEO_STORE"] = "false"
os.environ["PHI_REDACT"] = "true"
os.environ["REQUIRE_PATIENT_CONSENT"] = "true"
os.environ["ALLOW_UNMASKED_IMAGE"] = "false"
os.environ["SOAP_ENABLED"] = "true"
os.environ["PROCESS_TIMEOUT_SECONDS"] = "30"
os.environ["PILOT_CAPTURE_MODE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app as bridge  # noqa: E402
import bridge_core  # noqa: E402  (mutable config/state lives here)
import mlops_harness  # noqa: E402
from lib.hud_state_machine import build_hud_fixture, build_hud_moai_bundle, summarize_hud_fixture  # noqa: E402
from lib.moai_identity import resolve_moai_identity  # noqa: E402
from lib.moai_mapper import build_moai_export_bundle  # noqa: E402
from lib.moai_writer import build_moai_write_plan  # noqa: E402


API_KEY = os.environ["BRIDGE_API_KEY"]
PATIENT_NAME = "SmokePatient"
SMOKE_ORG_ID = "org-smoke"
SMOKE_PROVIDER_PERSON_ID = "provider-smoke"
SMOKE_SUBJECT_PERSON_ID = "person-smoke-patient"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def error_code(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code", ""))
    return str(payload.get("code", ""))


def auth_headers(scoped: bool = False) -> dict[str, str]:
    headers = {"x-api-key": API_KEY}
    if scoped:
        headers["x-glasspt-org-id"] = SMOKE_ORG_ID
        headers["x-glasspt-provider-person-id"] = SMOKE_PROVIDER_PERSON_ID
    return headers


def blank_jpeg_base64() -> str:
    image = Image.new("RGB", (240, 240), color=(255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def configure_isolated_storage(root: Path) -> None:
    bridge_core.DB_PATH = root / "bridge.db"
    bridge_core.UPLOAD_DIR = root / "uploads"
    bridge_core.CHART_DIR = root / "charts"
    bridge_core.MASKED_DIR = root / "masked"

    bridge_core.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    bridge_core.CHART_DIR.mkdir(parents=True, exist_ok=True)
    bridge_core.MASKED_DIR.mkdir(parents=True, exist_ok=True)
    bridge.ASYNC_RESULTS.clear()

    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(bridge_core.DB_PATH) as conn:
        conn.executescript(schema)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rayban_bridge_smoke_") as tmp:
        configure_isolated_storage(Path(tmp))
        client = TestClient(bridge.app)

        health = client.get("/health")
        require(health.status_code == 200, "health should be public")
        health_json = health.json()
        require(health_json["ok"] is True, "health db should be ok")
        require(health_json["security"]["api_key_configured"] is True, "api key should be configured")
        require(health_json["security"]["file_downloads_enabled"] is False, "file downloads should be off")
        require(health_json["security"]["patient_consent_required"] is True, "consent should be required")
        require(health_json["security"]["pilot_capture_mode"] is False, "pilot capture mode should default off in smoke")

        taxonomy = client.get("/label-taxonomy", headers=auth_headers())
        require(taxonomy.status_code == 200, "label taxonomy should load")
        require(
            taxonomy.json()["taxonomy"]["schema_version"] == "rayban_pt_label_taxonomy/v0",
            "label taxonomy schema mismatch",
        )

        bridge_ui = client.get("/")
        require(bridge_ui.status_code == 200, "bridge UI should load")
        require('fetch("/consents/status"' in bridge_ui.text, "bridge UI should use body-based consent status")
        require('method: "DELETE"' in bridge_ui.text, "bridge UI should support body-based consent revocation")
        require("/consents/${" not in bridge_ui.text, "bridge UI must not put patient names in consent URLs")

        no_auth = client.get("/recent-events")
        require(no_auth.status_code == 401, "protected routes should reject missing api key")
        require(no_auth.json().get("code") == "UNAUTHORIZED", "missing key should return UNAUTHORIZED")

        glass_no_auth = client.get("/glass/state")
        require(glass_no_auth.status_code == 401, "glass routes should reject missing api key")
        require(glass_no_auth.json().get("code") == "UNAUTHORIZED", "glass missing key should return UNAUTHORIZED")

        no_consent = client.post(
            "/ingest",
            headers=auth_headers(scoped=True),
            json={
                "source": "smoke",
                "event_type": "text",
                "patient_name": PATIENT_NAME,
                "subject_person_id": SMOKE_SUBJECT_PERSON_ID,
                "text": "patient note before consent",
            },
        )
        require(no_consent.status_code == 428, "ingest should require patient consent")
        require(error_code(no_consent.json()) == "PATIENT_CONSENT_REQUIRED", "missing consent code mismatch")

        consent = client.post(
            "/consents",
            headers=auth_headers(scoped=True),
            json={
                "patient_name": PATIENT_NAME,
                "subject_person_id": SMOKE_SUBJECT_PERSON_ID,
                "granted_by": "smoke-test",
            },
        )
        require(consent.status_code == 200, "record consent should succeed")
        require(consent.json()["ok"] is True, "record consent ok mismatch")

        active = client.post(
            "/consents/status",
            headers=auth_headers(scoped=True),
            json={"patient_name": PATIENT_NAME, "subject_person_id": SMOKE_SUBJECT_PERSON_ID},
        )
        require(active.status_code == 200, "consent lookup should succeed")
        require(active.json()["active"] is True, "consent should be active")

        bridge_core.PILOT_CAPTURE_MODE = True
        pilot_missing = client.post(
            "/ingest",
            headers=auth_headers(scoped=True),
            json={
                "source": "smoke",
                "event_type": "text",
                "patient_name": PATIENT_NAME,
                "subject_person_id": SMOKE_SUBJECT_PERSON_ID,
                "text": "pilot note missing canonical metadata",
            },
        )
        require(pilot_missing.status_code == 422, "pilot mode should require canonical metadata")
        require(error_code(pilot_missing.json()) == "PILOT_METADATA_REQUIRED", "pilot metadata error code mismatch")
        bridge_core.PILOT_CAPTURE_MODE = False

        text = (
            "환자 김민수 010-1234-5678 test@example.com 900101-1234567 "
            "MRN:ABCD1234 보행 불안정, 통증 6점"
        )
        ingest = client.post(
            "/ingest",
            headers=auth_headers(scoped=True),
            json={
                "source": "smoke",
                "event_type": "text",
                "patient_name": PATIENT_NAME,
                "subject_person_id": SMOKE_SUBJECT_PERSON_ID,
                "physio_client_id": "client-smoke-1",
                "physio_session_id": "enc-smoke-1",
                "text": text,
            },
        )
        require(ingest.status_code == 200, f"text ingest failed: {ingest.text}")
        event_id = ingest.json()["event_id"]

        event = client.get(f"/events/{event_id}", headers=auth_headers())
        require(event.status_code == 200, "event lookup should succeed")
        event_detail = event.json()["result"]["event"]
        raw_text = event_detail["raw_text"]
        require(event_detail["owner_org_id"] == SMOKE_ORG_ID, "event org scope should persist")
        require(
            event_detail["owner_provider_person_id"] == SMOKE_PROVIDER_PERSON_ID,
            "event provider scope should persist",
        )
        for token in ["010-1234-5678", "test@example.com", "900101-1234567", "ABCD1234", "김민수"]:
            require(token not in raw_text, f"PHI token was not redacted: {token}")
        for token in ["[REDACTED_PHONE]", "[REDACTED_EMAIL]", "[REDACTED_RRN]", "[REDACTED_ID]"]:
            require(token in raw_text, f"missing redaction marker: {token}")

        chart = client.get(f"/charts/{event_id}", headers=auth_headers())
        require(chart.status_code == 200, "chart lookup should succeed")
        require("010-1234-5678" not in chart.json()["chart"], "chart should not contain raw phone")

        review_chart_text = """F/U>
2026-05-10

S>
환자 주관적 호소 미입력

O>
관찰/측정 수치 미입력

P/E>
장면 분류 미입력

A>
특이 위험징후 미확인, 전반적 안정

PTx.>
· 다음 방문 시 기능 재평가
"""
        chart_update = client.put(
            f"/charts/{event_id}",
            headers=auth_headers(scoped=True),
            json={"chart": review_chart_text},
        )
        require(chart_update.status_code == 200, "chart update should succeed")
        require(chart_update.json()["review"] is None, "chart update should clear review state")

        review_queue = client.get("/chart-review?event_type=all&limit=20", headers=auth_headers())
        require(review_queue.status_code == 200, "chart review queue should load")
        require(
            any(item["event_id"] == event_id for item in review_queue.json()["items"]),
            "review queue should include unreviewed low-quality chart",
        )

        reviewed = client.post(
            f"/charts/{event_id}/review",
            headers=auth_headers(),
            json={"reviewer": "smoke-therapist", "notes": "reviewed by smoke test"},
        )
        require(reviewed.status_code == 200, "chart review mark should succeed")
        require(reviewed.json()["review"]["reviewer"] == "smoke-therapist", "reviewer should round-trip")

        chart_after_review = client.get(f"/charts/{event_id}", headers=auth_headers())
        require(chart_after_review.status_code == 200, "reviewed chart lookup should succeed")
        require(chart_after_review.json()["review"]["notes"] == "reviewed by smoke test", "review notes should persist")

        review_queue_after = client.get("/chart-review?event_type=all&limit=20", headers=auth_headers())
        require(review_queue_after.status_code == 200, "post-review queue should load")
        require(
            all(item["event_id"] != event_id for item in review_queue_after.json()["items"]),
            "reviewed chart should leave default review queue",
        )

        review_queue_with_good = client.get(
            "/chart-review?event_type=all&include_good=true&limit=20",
            headers=auth_headers(),
        )
        require(review_queue_with_good.status_code == 200, "include_good review queue should load")
        require(
            any(item["event_id"] == event_id and item["review"] is not None for item in review_queue_with_good.json()["items"]),
            "include_good queue should show reviewed chart state",
        )

        cleared_review = client.delete(f"/charts/{event_id}/review", headers=auth_headers())
        require(cleared_review.status_code == 200, "chart review clear should succeed")
        require(cleared_review.json()["review"] is None, "chart review clear should return null review")

        label = client.post(
            f"/labels/{event_id}",
            headers=auth_headers(),
            json={
                "provider_role": "pilates_instructor",
                "action_type": "intervention",
                "session_type": "balance_training",
                "core_task": "other",
                "custom_task": "supported_kneeling",
                "body_position": "kneeling",
                "assist_level": "minimal_assist",
                "performance_level": "stable",
                "review_status": "reviewed",
                "reviewer_person_id": SMOKE_PROVIDER_PERSON_ID,
                "usable_for_training": True,
                "label_confidence": 0.95,
                "repetition_count": 3,
                "hold_duration_seconds": 12.5,
                "tolerance": "fair",
                "fatigue_level": "mild",
                "compensations": ["right_weight_shift"],
                "caregiver_present": True,
                "safety_flags": ["fatigue"],
                "notes": "smoke label",
            },
        )
        require(label.status_code == 200, "label upsert should succeed")
        require(label.json()["label"]["provider_role"] == "pilates_instructor", "provider role should round-trip")
        require(label.json()["label"]["action_type"] == "intervention", "action type should round-trip")
        require(label.json()["label"]["flags"] == ["fatigue"], "label flags should round-trip")
        require(label.json()["label"]["custom_task"] == "supported_kneeling", "custom task should round-trip")
        require(label.json()["label"]["body_position"] == "kneeling", "body position should round-trip")
        require(label.json()["label"]["performance_level"] == "stable", "performance_level should round-trip")
        require(label.json()["label"]["review_status"] == "reviewed", "review status should round-trip")
        require(label.json()["label"]["usable_for_training"] is True, "training usability should round-trip")
        require(label.json()["label"]["compensations"] == ["right_weight_shift"], "compensations should round-trip")
        require(label.json()["label"]["caregiver_present"] is True, "caregiver_present should round-trip")
        require(label.json()["readiness"]["usable_for_schema_eval"] is True, "label response should include readiness")
        sync_jobs = client.get("/moai-sync/jobs?status=pending&limit=10", headers=auth_headers())
        require(sync_jobs.status_code == 200, "moai sync job list should load")
        sync_job_items = sync_jobs.json()["items"]
        sync_job = next((item for item in sync_job_items if item["event_id"] == event_id), None)
        require(sync_job is not None, "label upsert should enqueue moai sync job")
        require(sync_job["trigger_reason"] == "label_upserted", "moai sync trigger reason mismatch")

        recent_after_label = client.get("/recent-events?limit=5", headers=auth_headers())
        require(recent_after_label.status_code == 200, "recent events should load after label")
        recent_item = next((item for item in recent_after_label.json()["items"] if item["id"] == event_id), None)
        require(recent_item is not None, "recent events should include current event")
        require(recent_item["identity_completeness"]["complete"] is True, "recent event identity should be complete")
        require(recent_item["owner_org_id"] == SMOKE_ORG_ID, "recent event should expose org id")

        masked_file = bridge_core.MASKED_DIR / f"{event_id}_masked.jpg"
        masked_file.write_bytes(base64.b64decode(blank_jpeg_base64()))
        masked_response = client.get(f"/masked-files/{masked_file.name}", headers=auth_headers())
        require(masked_response.status_code == 200, "masked artifact should be downloadable with api key")
        require(masked_response.headers["content-type"].startswith("image/jpeg"), "masked artifact content type mismatch")

        physio_feed = client.get("/physio/sessions?limit=5", headers=auth_headers())
        require(physio_feed.status_code == 200, "physio session feed should load")
        feed_items = physio_feed.json()["items"]
        exported = next((item for item in feed_items if item["event_id"] == event_id), None)
        require(exported is not None, "physio session feed should include the saved event")
        require(exported["persisted"] is True, "physio session should be marked persisted")
        require(exported["label"]["core_task"] == "other", "physio session label should round-trip")
        require(exported["label"]["custom_task"] == "supported_kneeling", "physio session custom task should round-trip")
        require(exported["soap"]["a"], "physio session should include SOAP summary")
        require(exported["quality"]["level"] in {"good", "review", "needs_edit"}, "physio session quality should be present")
        require(exported["artifacts"][0]["download_path"] == f"/masked-files/{masked_file.name}", "physio session artifact path mismatch")
        require("010-1234-5678" not in exported["chart_excerpt"], "physio chart excerpt should stay redacted")
        require(exported["owner_org_id"] == SMOKE_ORG_ID, "physio export should include org scope")
        require(
            exported["owner_provider_person_id"] == SMOKE_PROVIDER_PERSON_ID,
            "physio export should include provider scope",
        )

        moai_export = client.get(
            f"/events/{event_id}/moai-export?subject_person_id=person-smoke-patient&encounter_id=enc-smoke-1",
            headers=auth_headers(),
        )
        require(moai_export.status_code == 200, "moai export should load")
        moai_payload = moai_export.json()["result"]
        require(moai_payload["context"]["source_system"] == "rayban_pt", "moai export source system mismatch")
        require(moai_payload["context"]["subject_person_id"] == "person-smoke-patient", "moai export subject override mismatch")
        require(moai_payload["encounter"]["target_table"] == "encounters", "moai encounter target mismatch")
        require(moai_payload["encounter"]["payload"]["id"] == "enc-smoke-1", "moai encounter id override mismatch")
        require(moai_payload["encounter"]["payload"]["status"] == "in-progress", "moai encounter status should match physio_app enum")
        require(moai_payload["encounter"]["payload"]["service_domain"] == "clinical", "moai encounter service domain should match physio_app enum")
        require(moai_payload["encounter"]["payload"]["flow_mode"] == "simple", "moai encounter flow mode should match physio_app enum")
        require(moai_payload["encounter"]["payload"]["care_setting"] == "home_visit", "moai encounter should mark home visit care setting")
        require(moai_payload["notes"][0]["target_table"] == "encounter_notes", "moai note target mismatch")
        require(moai_payload["notes"][0]["payload"]["note_format"] == "soap", "moai note format should match physio_app enum")
        require(
            all(item["payload"]["source_type"] == "ai" for item in moai_payload["observations"]),
            "moai observations should use a physio_app source_type",
        )
        require(any(item["target_table"] == "observations" for item in moai_payload["observations"]), "moai observations should be present")
        require(
            moai_payload["activity_sessions"][0]["payload"]["activity_type"] == "clinic_exercise",
            "moai activity type should match physio_app enum",
        )
        require(
            moai_payload["activity_sessions"][0]["payload"]["source"] == "camera",
            "moai activity source should match physio_app enum",
        )
        require(moai_payload["audit_events"][0]["target_table"] == "clinical_events", "moai audit target mismatch")

        reviewed_image_bundle = build_moai_export_bundle(
            event={
                "id": event_id,
                "event_type": "image",
                "created_at": event_detail["created_at"],
                "owner_org_id": SMOKE_ORG_ID,
                "owner_provider_person_id": SMOKE_PROVIDER_PERSON_ID,
                "subject_person_id": "person-smoke-patient",
            },
            soap={"s": "subjective", "o": "objective", "a": "assessment", "p": "plan"},
            label=label.json()["label"],
            review={"reviewer": SMOKE_PROVIDER_PERSON_ID, "notes": "accepted", "reviewed_at": event_detail["created_at"]},
            subject_person_id="person-smoke-patient",
            provider_person_id=SMOKE_PROVIDER_PERSON_ID,
            encounter_id="enc-smoke-1",
        )
        require(
            reviewed_image_bundle["ai_inference_logs"][0]["payload"]["review_status"] == "accepted",
            "moai AI review status should match physio_app enum",
        )
        require(
            reviewed_image_bundle["media_summaries"][0]["payload"]["media_ref_type"] == "image_upload",
            "moai media summary ref type should match physio_app enum",
        )
        require(
            reviewed_image_bundle["reviews"][0]["payload"]["source_table"] == "other",
            "moai review source table should match physio_app enum",
        )
        require(
            reviewed_image_bundle["reviews"][0]["payload"]["review_status"] == "clinician_accepted",
            "moai review status should match physio_app enum",
        )

        hud_fixture = build_hud_fixture(
            encounter_id="enc-smoke-hud",
            organization_id=SMOKE_ORG_ID,
            subject_person_id="person-smoke-patient",
            provider_person_id=SMOKE_PROVIDER_PERSON_ID,
        )
        hud_summary = summarize_hud_fixture(hud_fixture)
        require(hud_summary["page_count"] == 4, "HUD fixture should define four encounter states")
        require(hud_summary["micro_card_only"] is True, "HUD fixture should stay micro-card scoped")
        require(
            hud_summary["candidate"]["status"] == "confirmed_by_provider",
            "HUD fixture should approve the synthetic candidate by default",
        )
        hud_plan = build_moai_write_plan(build_hud_moai_bundle(hud_fixture))
        hud_targets = {op["target_table"] for op in hud_plan["operations"]}
        require(hud_plan["summary"]["skipped_count"] == 0, "HUD write plan should not skip synthetic fixture rows")
        require("observations" in hud_targets, "HUD write plan should include observations")
        require("clinical_extraction_reviews" in hud_targets, "HUD write plan should include extraction review")
        require("clinical_events" in hud_targets, "HUD write plan should include audit event")

        hud_rehearsal = mlops_harness._build_hud_e2e_rehearsal(
            SimpleNamespace(
                transcript="좌측 SLR 45도에서 posterior thigh pain",
                org_id=SMOKE_ORG_ID,
                provider_person_id=SMOKE_PROVIDER_PERSON_ID,
                subject_person_id="person-smoke-patient",
                encounter_id="enc-smoke-hud-e2e",
                candidate_id="hud-smoke-candidate",
                source="ios_audio_transcript",
                confidence=None,
                synthetic_non_phi=True,
                full=False,
            )
        )
        require(hud_rehearsal["status"] == "done", "HUD E2E rehearsal should parse fixture transcript")
        require(
            hud_rehearsal["checks"]["approved_emits_observation"] is True,
            "HUD E2E approval path should emit observation plan",
        )
        require(
            hud_rehearsal["checks"]["discarded_skips_observation"] is True,
            "HUD E2E discard path should skip observation plan",
        )

        hud_create = client.post(
            "/hud/candidates",
            headers=auth_headers(scoped=True),
            json={
                "encounter_id": "enc-smoke-1",
                "subject_person_id": "person-smoke-patient",
                "event_type": "test_result",
                "test": "SLR",
                "side": "left",
                "value": "45 degrees",
                "symptom": "posterior thigh pain",
                "source_text": "Synthetic note: left SLR 45 degrees with posterior thigh pain.",
                "confidence": 0.8,
            },
        )
        require(hud_create.status_code == 200, f"HUD candidate create should succeed: {hud_create.text}")
        hud_candidate = hud_create.json()["candidate"]
        require(hud_candidate["status"] == "candidate", "new HUD candidate should start as candidate")
        require(hud_candidate["review_status"] == "auto_extracted", "new HUD candidate should be auto_extracted")

        hud_list = client.get("/hud/candidates?encounter_id=enc-smoke-1", headers=auth_headers())
        require(hud_list.status_code == 200, "HUD candidate list should load")
        require(
            any(item["id"] == hud_candidate["id"] for item in hud_list.json()["items"]),
            "HUD candidate list should include staged candidate",
        )

        hud_approve = client.post(
            f"/hud/candidates/{hud_candidate['id']}/approve",
            headers=auth_headers(),
            json={"reviewer_person_id": SMOKE_PROVIDER_PERSON_ID},
        )
        require(hud_approve.status_code == 200, "HUD candidate approve should succeed")
        approved_candidate = hud_approve.json()["candidate"]
        require(approved_candidate["status"] == "confirmed_by_provider", "approved HUD candidate status mismatch")
        require(approved_candidate["review_status"] == "clinician_accepted", "approved HUD review status mismatch")
        approved_plan = hud_approve.json()["plan"]
        approved_targets = {op["target_table"] for op in approved_plan["operations"]}
        require(approved_plan["summary"]["skipped_count"] == 0, "approved HUD plan should not skip rows")
        require("observations" in approved_targets, "approved HUD plan should include observation")
        observation_op = next(op for op in approved_plan["operations"] if op["target_table"] == "observations")
        require(observation_op["payload"]["status"] == "final", "approved HUD observation should be final")
        require(observation_op["payload"]["source_type"] == "manual", "approved HUD observation should be manual")
        review_op = next(op for op in approved_plan["operations"] if op["target_table"] == "clinical_extraction_reviews")
        require(
            review_op["payload"].get("resolved_observation_id") == observation_op["payload"]["id"],
            "approved HUD review should point to the resolved observation",
        )

        hud_value_create = client.post(
            "/hud/candidates",
            headers=auth_headers(scoped=True),
            json={
                "encounter_id": "enc-smoke-1",
                "subject_person_id": "person-smoke-patient",
                "event_type": "test_result",
                "test": "SLR",
                "side": "right",
                "value": "40 degrees",
                "symptom": "posterior thigh pain",
            },
        )
        require(hud_value_create.status_code == 200, "HUD value candidate create should succeed")
        hud_value_id = hud_value_create.json()["candidate"]["id"]
        hud_value_approve = client.post(
            f"/hud/candidates/{hud_value_id}/approve",
            headers=auth_headers(),
            json={"reviewer_person_id": SMOKE_PROVIDER_PERSON_ID},
        )
        require(hud_value_approve.status_code == 200, "HUD value candidate approve should succeed")
        value_plan = hud_value_approve.json()["plan"]
        value_observation = next(op for op in value_plan["operations"] if op["target_table"] == "observations")
        require(
            value_observation["payload"].get("value_quantity") == 40.0
            and value_observation["payload"].get("value_unit") == "degrees",
            "HUD observation value should come from the candidate and use numeric value_quantity",
        )

        hud_plan_api = client.get(f"/hud/candidates/{hud_candidate['id']}/moai-write-plan", headers=auth_headers())
        require(hud_plan_api.status_code == 200, "HUD candidate write plan should load")
        require(hud_plan_api.json()["result"]["summary"]["operation_count"] == 3, "approved HUD API plan should have 3 operations")

        hud_discard_create = client.post(
            "/hud/candidates",
            headers=auth_headers(scoped=True),
            json={
                "encounter_id": "enc-smoke-1",
                "subject_person_id": "person-smoke-patient",
                "event_type": "test_result",
                "test": "Slump",
                "side": "left",
                "value": "positive",
                "symptom": "unclear",
            },
        )
        require(hud_discard_create.status_code == 200, "discard HUD candidate create should succeed")
        discard_id = hud_discard_create.json()["candidate"]["id"]
        hud_discard = client.post(
            f"/hud/candidates/{discard_id}/discard",
            headers=auth_headers(),
            json={"reviewer_person_id": SMOKE_PROVIDER_PERSON_ID, "reason": "duplicate candidate"},
        )
        require(hud_discard.status_code == 200, "HUD candidate discard should succeed")
        discard_body = hud_discard.json()
        require(discard_body["candidate"]["status"] == "discarded", "discarded HUD candidate status mismatch")
        discard_targets = {op["target_table"] for op in discard_body["plan"]["operations"]}
        require("observations" not in discard_targets, "discarded HUD candidate should not create observation")
        require("clinical_extraction_reviews" in discard_targets, "discarded HUD candidate should retain review trace")
        require("clinical_events" in discard_targets, "discarded HUD candidate should retain audit trace")

        extract_preview = client.post(
            "/hud/candidates/extract",
            headers=auth_headers(scoped=True),
            json={
                "encounter_id": "enc-smoke-1",
                "subject_person_id": "person-smoke-patient",
                "text": "좌측 SLR 45도에서 posterior thigh pain",
                "create_candidate": False,
            },
        )
        require(extract_preview.status_code == 200, "HUD extract preview should succeed")
        preview_candidate = extract_preview.json()["candidate"]
        require(preview_candidate["test"] == "SLR", "HUD extract should parse SLR")
        require(preview_candidate["side"] == "left", "HUD extract should parse Korean left")
        require(preview_candidate["value"] == "45 degrees", "HUD extract should parse degree value")
        require(preview_candidate["symptom"] == "posterior thigh pain", "HUD extract should parse symptom")

        extract_create = client.post(
            "/hud/candidates/extract",
            headers=auth_headers(scoped=True),
            json={
                "encounter_id": "enc-smoke-1",
                "subject_person_id": "person-smoke-patient",
                "text": "Right slump positive with radiating pain",
                "confidence": 0.76,
            },
        )
        require(extract_create.status_code == 200, "HUD extract create should succeed")
        extracted_candidate = extract_create.json()["candidate"]
        require(extracted_candidate["test"] == "Slump", "HUD extract should parse Slump")
        require(extracted_candidate["side"] == "right", "HUD extract should parse right")
        require(extracted_candidate["value"] == "positive", "HUD extract should parse positive")
        require(extracted_candidate["status"] == "candidate", "HUD extract should stage candidate")
        extract_state = client.get("/glass/state", headers=auth_headers()).json()
        require(
            extract_state["active_hud_candidate"]["id"] == extracted_candidate["id"],
            "HUD extract should set active candidate micro-card",
        )
        extract_approve = client.post(
            "/neural-band/event",
            headers=auth_headers(),
            json={"gesture": "pinch", "device_id": "band-extract-1"},
        )
        require(extract_approve.status_code == 200, "pinch should approve extracted HUD candidate")
        require(
            extract_approve.json()["executed"]["candidate"]["status"] == "confirmed_by_provider",
            "pinch should confirm extracted candidate",
        )

        no_extract = client.post(
            "/hud/candidates/extract",
            headers=auth_headers(scoped=True),
            json={
                "encounter_id": "enc-smoke-1",
                "subject_person_id": "person-smoke-patient",
                "text": "환자가 오늘은 조금 피곤하다고 말함",
            },
        )
        require(no_extract.status_code == 200, "unsupported transcript should return no_candidate")
        require(no_extract.json()["status"] == "no_candidate", "unsupported transcript should not create candidate")

        hud_gesture_create = client.post(
            "/hud/candidates",
            headers=auth_headers(scoped=True),
            json={
                "encounter_id": "enc-smoke-1",
                "subject_person_id": "person-smoke-patient",
                "event_type": "test_result",
                "test": "SLR",
                "side": "right",
                "value": "50 degrees",
                "symptom": "hamstring tightness",
            },
        )
        require(hud_gesture_create.status_code == 200, "gesture HUD candidate create should succeed")
        neural_approve = client.post(
            "/neural-band/event",
            headers=auth_headers(),
            json={"gesture": "pinch", "device_id": "band-candidate-1"},
        )
        require(neural_approve.status_code == 200, "pinch gesture should approve active HUD candidate")
        neural_approve_body = neural_approve.json()
        require(neural_approve_body["mapped_command"] == "approve_candidate", "pinch should map to approve_candidate")
        require(neural_approve_body["executed"]["candidate"]["status"] == "confirmed_by_provider", "pinch should approve candidate")
        neural_targets = {op["target_table"] for op in neural_approve_body["executed"]["moai_write_plan"]["operations"]}
        require("observations" in neural_targets, "pinch-approved candidate should produce observation plan")

        hud_command_create = client.post(
            "/hud/candidates",
            headers=auth_headers(scoped=True),
            json={
                "encounter_id": "enc-smoke-1",
                "subject_person_id": "person-smoke-patient",
                "event_type": "test_result",
                "test": "ODI",
                "value": "duplicate",
                "symptom": "duplicate candidate",
            },
        )
        require(hud_command_create.status_code == 200, "command HUD candidate create should succeed")
        command_discard = client.post(
            "/glass/command",
            headers=auth_headers(),
            json={"command": "discard_candidate"},
        )
        require(command_discard.status_code == 200, "glass discard command should execute")
        command_discard_body = command_discard.json()
        require(command_discard_body["executed"]["candidate"]["status"] == "discarded", "glass command should discard candidate")
        command_discard_targets = {
            op["target_table"]
            for op in command_discard_body["executed"]["moai_write_plan"]["operations"]
        }
        require("observations" not in command_discard_targets, "command-discarded candidate should not produce observation")

        pilot_manifest = client.get(f"/events/{event_id}/pilot-manifest?resolve_identity=false", headers=auth_headers())
        require(pilot_manifest.status_code == 200, "pilot manifest should load")
        manifest_payload = pilot_manifest.json()["manifest"]
        require(manifest_payload["schema_version"] == "rayban_pt_pilot_session_manifest/v0", "pilot manifest schema mismatch")
        require(manifest_payload["identity"]["organization_id"] == SMOKE_ORG_ID, "pilot manifest org mismatch")
        require(manifest_payload["readiness"]["usable_for_schema_eval"] is True, "pilot manifest should be usable for schema eval")

        pilot_readiness = client.get(f"/events/{event_id}/pilot-readiness?resolve_identity=false", headers=auth_headers())
        require(pilot_readiness.status_code == 200, "pilot readiness should load")
        require(pilot_readiness.json()["readiness"]["gate"] == "gate_1_pilot", "pilot readiness gate mismatch")

        readiness_report = mlops_harness._build_readiness_report(
            limit=10,
            status="processed",
            resolve_identity=False,
        )
        require(readiness_report["summary"]["scanned_count"] >= 1, "readiness report should scan local events")
        require(
            readiness_report["summary"]["usable_for_schema_eval_count"] >= 1,
            "readiness report should count schema-ready events",
        )

        moai_plan = client.get(
            f"/events/{event_id}/moai-write-plan?subject_person_id=person-smoke-patient&encounter_id=enc-smoke-1",
            headers=auth_headers(),
        )
        require(moai_plan.status_code == 200, "moai write plan should load")
        plan_payload = moai_plan.json()["result"]
        require(plan_payload["summary"]["operation_count"] >= 4, "moai write plan should include operations")
        summarized_plan = mlops_harness._summarize_plan(plan_payload)
        require(PATIENT_NAME not in json.dumps(summarized_plan, ensure_ascii=False), "moai plan summary should omit patient identity hints")

        local_pilot_plan = client.get(
            f"/events/{event_id}/moai-write-plan?resolve_identity=false",
            headers=auth_headers(),
        )
        require(local_pilot_plan.status_code == 200, "local pilot write plan should load")
        require(
            local_pilot_plan.json()["result"]["summary"]["operation_count"] >= 1,
            "local pilot dry-run should build at least one operation",
        )

        def fake_identity_fetch(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
            if table == "org_clients" and params.get("id") == "eq.client-smoke-1":
                return [{"id": "client-smoke-1", "person_id": "person-smoke-patient", "organization_id": SMOKE_ORG_ID}]
            return []

        resolved_identity = resolve_moai_identity(
            event={
                "owner_org_id": SMOKE_ORG_ID,
                "owner_provider_person_id": SMOKE_PROVIDER_PERSON_ID,
                "physio_client_id": "client-smoke-1",
                "patient_name": PATIENT_NAME,
                "subject_person_id": SMOKE_SUBJECT_PERSON_ID,
            },
            fetch_rows=fake_identity_fetch,
        )
        require(resolved_identity.status == "resolved", "identity resolver should resolve org client id")
        require(resolved_identity.subject_person_id == "person-smoke-patient", "identity resolver subject mismatch")
        require(resolved_identity.organization_id == SMOKE_ORG_ID, "identity resolver org mismatch")

        moai_dry_run = client.post(
            f"/events/{event_id}/moai-write?subject_person_id=person-smoke-patient&encounter_id=enc-smoke-1&dry_run=true",
            headers=auth_headers(),
        )
        require(moai_dry_run.status_code == 200, "moai dry-run write should load")
        require(moai_dry_run.json()["status"] == "dry_run", "moai dry-run status mismatch")

        scoped_feed = client.get(
            f"/physio/sessions?limit=5&org_id={SMOKE_ORG_ID}&provider_person_id={SMOKE_PROVIDER_PERSON_ID}",
            headers=auth_headers(),
        )
        require(scoped_feed.status_code == 200, "scoped physio feed should load")
        require(
            any(item["event_id"] == event_id for item in scoped_feed.json()["items"]),
            "scoped physio feed should include matching provider event",
        )

        other_scope_feed = client.get(
            "/physio/sessions?limit=5&org_id=other-org&provider_person_id=other-provider",
            headers=auth_headers(),
        )
        require(other_scope_feed.status_code == 200, "other scoped physio feed should load")
        require(
            all(item["event_id"] != event_id for item in other_scope_feed.json()["items"]),
            "other provider should not see scoped event",
        )

        image_fail = client.post(
            "/ingest",
            headers=auth_headers(scoped=True),
            json={
                "source": "smoke",
                "event_type": "image",
                "patient_name": PATIENT_NAME,
                "subject_person_id": SMOKE_SUBJECT_PERSON_ID,
                "text": "blank image masking gate",
                "image_base64": blank_jpeg_base64(),
            },
        )
        require(image_fail.status_code == 422, f"blank image should fail closed: {image_fail.text}")
        require(error_code(image_fail.json()) == "FACE_NOT_DETECTED", "blank image should return FACE_NOT_DETECTED")

        file_download = client.get("/files/missing.mp4", headers=auth_headers())
        require(file_download.status_code == 404, "file downloads should be disabled")
        require(error_code(file_download.json()) == "FILE_DOWNLOAD_DISABLED", "file download code mismatch")

        bad_audit = client.get("/audit-logs?level=debug", headers=auth_headers())
        require(bad_audit.status_code == 400, "invalid audit level should fail")
        require(error_code(bad_audit.json()) == "INVALID_AUDIT_LEVEL", "audit error code mismatch")

        audit = client.get("/audit-logs?limit=20", headers=auth_headers())
        require(audit.status_code == 200, "audit log lookup should succeed")
        require(len(audit.json()["items"]) >= 1, "audit logs should not be empty")

        revoke = client.request(
            "DELETE",
            "/consents",
            headers=auth_headers(scoped=True),
            json={"patient_name": PATIENT_NAME, "subject_person_id": SMOKE_SUBJECT_PERSON_ID},
        )
        require(revoke.status_code == 200, "revoke consent should succeed")
        require(revoke.json()["revoked"] >= 1, "revoke should affect at least one consent")

        inactive = client.post(
            "/consents/status",
            headers=auth_headers(scoped=True),
            json={"patient_name": PATIENT_NAME, "subject_person_id": SMOKE_SUBJECT_PERSON_ID},
        )
        require(inactive.status_code == 200, "post-revoke consent lookup should succeed")
        require(inactive.json()["active"] is False, "consent should be inactive after revoke")

        after_revoke = client.post(
            "/ingest",
            headers=auth_headers(scoped=True),
            json={
                "source": "smoke",
                "event_type": "text",
                "patient_name": PATIENT_NAME,
                "subject_person_id": SMOKE_SUBJECT_PERSON_ID,
                "text": "post revoke note",
            },
        )
        require(after_revoke.status_code == 428, "post-revoke ingest should require new consent")
        require(error_code(after_revoke.json()) == "PATIENT_CONSENT_REQUIRED", "post-revoke error code mismatch")

        deleted = client.delete(f"/events/{event_id}", headers=auth_headers())
        require(deleted.status_code == 200, "event deletion should succeed")

        gone = client.get(f"/events/{event_id}", headers=auth_headers())
        require(gone.status_code == 404, "deleted event should no longer be readable")

    print("OK: bridge safety smoke test passed")

    # ── Glass relay ───────────────────────────────────────────────────────────
    reset_glass = client.post(
        "/glass/state",
        headers=auth_headers(),
        json={
            "patient": None,
            "mode": "standby",
            "message": "라이브 연결을 기다리는 중",
            "is_recording": False,
            "recording_start": None,
            "session_count": 0,
            "event_role_counts": {},
            "capture_role": "observation",
            "active_hud_candidate": None,
            "visit_session_id": None,
            "phase": "pre_review",
            "readiness": "ready",
            "error_state": None,
            "last_insight": None,
        },
    )
    require(reset_glass.status_code == 200, "glass state reset should succeed")

    initial = client.get("/glass/state", headers=auth_headers())
    require(initial.status_code == 200, "glass state should return 200")
    require(initial.json()["is_recording"] is False, "initial state should not be recording")
    require(initial.json()["mode"] == "standby", "initial mode should be standby")

    pushed = client.post(
        "/glass/state",
        headers=auth_headers(),
        json={
            "patient": "TestPT",
            "mode": "recording",
            "message": "TestPT · 세션 1 저장 준비",
            "is_recording": True,
            "session_count": 1,
        },
    )
    require(pushed.status_code == 200, "glass state push should succeed")

    state = client.get("/glass/state", headers=auth_headers()).json()
    require(state["patient"] == "TestPT", "patient should be stored")
    require(state["mode"] == "recording", "mode should be stored")
    require(state["message"] == "TestPT · 세션 1 저장 준비", "message should be stored")
    require(state["is_recording"] is True, "recording flag should be stored")
    require(state["session_count"] == 1, "session count should be stored")

    cleared = client.post(
        "/glass/state",
        headers=auth_headers(),
        json={
            "mode": "ready",
            "message": "하단 버튼으로 바로 시작",
            "is_recording": False,
            "recording_start": None,
            "last_insight": None,
        },
    )
    require(cleared.status_code == 200, "glass state clear should succeed")

    cleared_state = client.get("/glass/state", headers=auth_headers()).json()
    require(cleared_state["mode"] == "ready", "mode should update to ready")
    require(cleared_state["is_recording"] is False, "recording flag should reset")
    require(cleared_state["recording_start"] is None, "recording_start should clear")
    require(cleared_state["last_insight"] is None, "last_insight should clear")

    cmd_post = client.post(
        "/glass/command",
        headers=auth_headers(),
        json={"command": "toggle_recording"},
    )
    require(cmd_post.status_code == 200, "glass command post should succeed")

    cmd_poll = client.get("/glass/command", headers=auth_headers()).json()
    require(cmd_poll["command"] == "toggle_recording", "command should be returned once")

    cmd_empty = client.get("/glass/command", headers=auth_headers()).json()
    require(cmd_empty["command"] is None, "command queue should be empty after poll")

    bad_cmd = client.post(
        "/glass/command",
        headers=auth_headers(),
        json={"command": "bad_command"},
    )
    require(bad_cmd.status_code == 400, "invalid command should return 400")

    neural_cmd = client.post(
        "/neural-band/event",
        headers=auth_headers(),
        json={"gesture": "double_tap", "device_id": "band-test-1"},
    )
    require(neural_cmd.status_code == 200, "neural band event should succeed")
    neural_body = neural_cmd.json()
    require(neural_body["mapped_command"] == "toggle_recording", "neural band gesture should map to toggle_recording")

    neural_poll = client.get("/glass/device-command", headers=auth_headers()).json()
    require(neural_poll["command"] == "toggle_recording", "mapped neural band command should be queued")
    require(neural_poll["source"] == "neural_band", "queued command should preserve source")
    require(neural_poll["metadata"]["gesture"] == "double_tap", "queued command should preserve gesture metadata")
    require(neural_poll["metadata"]["device_id"] == "band-test-1", "queued command should preserve device metadata")

    photo_cmd = client.post(
        "/neural-band/event",
        headers=auth_headers(),
        json={"gesture": "capture_photo", "device_id": "band-test-1"},
    )
    require(photo_cmd.status_code == 200, "photo gesture should succeed")
    photo_poll = client.get("/glass/device-command", headers=auth_headers()).json()
    require(photo_poll["command"] == "capture_photo", "photo command should target native device queue")

    audio_cmd = client.post(
        "/neural-band/event",
        headers=auth_headers(),
        json={"gesture": "stt", "device_id": "band-test-1"},
    )
    require(audio_cmd.status_code == 200, "STT gesture should succeed")
    audio_poll = client.get("/glass/device-command", headers=auth_headers()).json()
    require(audio_poll["command"] == "start_audio", "STT gesture should start native audio capture")

    bad_neural = client.post(
        "/neural-band/event",
        headers=auth_headers(),
        json={"gesture": "unknown_wave"},
    )
    require(bad_neural.status_code == 400, "invalid neural band gesture should return 400")

    print("OK: glass relay smoke test passed")


if __name__ == "__main__":
    main()
