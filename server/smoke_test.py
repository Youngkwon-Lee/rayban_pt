#!/usr/bin/env python3
"""Local safety smoke test for the Rayban bridge API.

Runs against an isolated temporary database and storage directory. It does not
modify the real server/storage database used by the dev bridge.
"""

from __future__ import annotations

import base64
import io
import os
import sqlite3
import tempfile
from pathlib import Path
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

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app as bridge  # noqa: E402


API_KEY = os.environ["BRIDGE_API_KEY"]
PATIENT_NAME = "SmokePatient"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def error_code(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code", ""))
    return str(payload.get("code", ""))


def auth_headers() -> dict[str, str]:
    return {"x-api-key": API_KEY}


def blank_jpeg_base64() -> str:
    image = Image.new("RGB", (240, 240), color=(255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


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

        no_auth = client.get("/recent-events")
        require(no_auth.status_code == 401, "protected routes should reject missing api key")
        require(no_auth.json().get("code") == "UNAUTHORIZED", "missing key should return UNAUTHORIZED")

        no_consent = client.post(
            "/ingest",
            headers=auth_headers(),
            json={
                "source": "smoke",
                "event_type": "text",
                "patient_name": PATIENT_NAME,
                "text": "patient note before consent",
            },
        )
        require(no_consent.status_code == 428, "ingest should require patient consent")
        require(error_code(no_consent.json()) == "PATIENT_CONSENT_REQUIRED", "missing consent code mismatch")

        consent = client.post(
            "/consents",
            headers=auth_headers(),
            json={"patient_name": PATIENT_NAME, "granted_by": "smoke-test"},
        )
        require(consent.status_code == 200, "record consent should succeed")
        require(consent.json()["ok"] is True, "record consent ok mismatch")

        active = client.get(f"/consents/{PATIENT_NAME}", headers=auth_headers())
        require(active.status_code == 200, "consent lookup should succeed")
        require(active.json()["active"] is True, "consent should be active")

        text = (
            "환자 김민수 010-1234-5678 test@example.com 900101-1234567 "
            "MRN:ABCD1234 보행 불안정, 통증 6점"
        )
        ingest = client.post(
            "/ingest",
            headers=auth_headers(),
            json={
                "source": "smoke",
                "event_type": "text",
                "patient_name": PATIENT_NAME,
                "text": text,
            },
        )
        require(ingest.status_code == 200, f"text ingest failed: {ingest.text}")
        event_id = ingest.json()["event_id"]

        event = client.get(f"/events/{event_id}", headers=auth_headers())
        require(event.status_code == 200, "event lookup should succeed")
        raw_text = event.json()["result"]["event"]["raw_text"]
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
            headers=auth_headers(),
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
                "session_type": "standing",
                "core_task": "balance",
                "assist_level": "min",
                "performance": "stable",
                "flags": ["fatigue"],
                "notes": "smoke label",
            },
        )
        require(label.status_code == 200, "label upsert should succeed")
        require(label.json()["label"]["flags"] == ["fatigue"], "label flags should round-trip")

        image_fail = client.post(
            "/ingest",
            headers=auth_headers(),
            json={
                "source": "smoke",
                "event_type": "image",
                "patient_name": PATIENT_NAME,
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

        revoke = client.delete(f"/consents/{PATIENT_NAME}", headers=auth_headers())
        require(revoke.status_code == 200, "revoke consent should succeed")
        require(revoke.json()["revoked"] >= 1, "revoke should affect at least one consent")

        inactive = client.get(f"/consents/{PATIENT_NAME}", headers=auth_headers())
        require(inactive.status_code == 200, "post-revoke consent lookup should succeed")
        require(inactive.json()["active"] is False, "consent should be inactive after revoke")

        after_revoke = client.post(
            "/ingest",
            headers=auth_headers(),
            json={
                "source": "smoke",
                "event_type": "text",
                "patient_name": PATIENT_NAME,
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


if __name__ == "__main__":
    main()
