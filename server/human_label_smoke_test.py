"""Smoke checks for human_label capture events and the label importer.

Proves the bridge-side half of docs/pediatric-label-schema-mapping.md §C.2:
- POST /capture-events accepts source_type=human_label with guard rails
  (no draft status, approved requires reviewer, clip_id+label_schema required)
- lib.label_import converts pediatric_home_v1 clips (sec spans, ordinal
  confidence) into idempotent capture_events rows
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

os.environ["BRIDGE_API_KEY"] = "human-label-smoke-key"
os.environ["REQUIRE_API_KEY"] = "true"
os.environ["ALLOW_INSECURE_LAN"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import bridge_core  # noqa: E402
from app import app  # noqa: E402
from lib.label_import import (  # noqa: E402
    LABEL_CONFIDENCE_TO_SCORE,
    human_label_extraction_key,
    import_label_clips,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


SAMPLE_CLIPS = [
    {
        "clip_id": "PB9_0001",
        "asset_uid": "asset_smoke_0001",
        "patient_uid": "patient_smoke",
        "visit_id": "2026-08-20",
        "schema_version": "rehab-v1.0",
        "annotator": "pt_smoke",
        "clip_type": "supported_sitting",
        "label_confidence": "high",
        "evidence_spans": [
            {"start_sec": 12.5, "end_sec": 34.0, "label": "supported sitting hold"}
        ],
        "assist_level": "min_assist",
        "exercise_tolerance": "good",
    },
    {
        # clip-level label without spans
        "clip_id": "PB9_0002",
        "annotator": "pt_smoke",
        "clip_type": "device_peg",
        "label_confidence": "medium",
        "site_redness": "none",
    },
    {
        # no links entry -> must be skipped
        "clip_id": "PB9_9999",
        "annotator": "pt_smoke",
        "clip_type": "rom_clip",
    },
]

SAMPLE_LINKS = {
    "PB9_0001": {
        "visit_session_id": "vs-smoke-1",
        "source_event_id": "ev-smoke-1",
        "encounter_id": "enc-smoke-1",
        "organization_id": "org-smoke",
        "provider_person_id": "provider-smoke",
        "subject_person_id": "subject-smoke",
    },
    "PB9_0002": {"source_event_id": "ev-smoke-1", "organization_id": "org-smoke"},
}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rayban_human_label_") as tmp:
        bridge_core.DB_PATH = Path(tmp) / "bridge.db"
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(bridge_core.DB_PATH) as conn:
            conn.executescript(schema)

        client = TestClient(app)
        headers = {
            "x-api-key": os.environ["BRIDGE_API_KEY"],
            "x-glasspt-org-id": "org-smoke",
            "x-glasspt-provider-person-id": "provider-smoke",
        }

        base = {
            "source_type": "human_label",
            "event_type": "supported_sitting",
            "payload": {"clip_id": "PB9_0100", "label_schema": "pediatric_home_v1"},
        }

        # draft status rejected
        response = client.post("/capture-events", json={**base, "status": "draft"}, headers=headers)
        require(response.status_code == 400, f"draft human_label should 400, got {response.status_code}")
        require(response.json()["detail"]["code"] == "INVALID_HUMAN_LABEL_STATUS", "wrong code for draft guard")

        # approved without reviewer rejected
        response = client.post("/capture-events", json={**base, "status": "approved"}, headers=headers)
        require(response.status_code == 400, "approved human_label without reviewer should 400")
        require(response.json()["detail"]["code"] == "HUMAN_LABEL_REVIEWER_REQUIRED", "wrong code for reviewer guard")

        # missing clip_id/label_schema rejected
        response = client.post(
            "/capture-events",
            json={**base, "status": "edited", "payload": {"note": "no ids"}},
            headers=headers,
        )
        require(response.status_code == 400, "human_label without clip identity should 400")
        require(response.json()["detail"]["code"] == "INVALID_HUMAN_LABEL_PAYLOAD", "wrong code for payload guard")

        # valid edited human_label accepted
        response = client.post("/capture-events", json={**base, "status": "edited"}, headers=headers)
        require(response.status_code == 200, f"edited human_label should succeed, got {response.status_code}: {response.text}")
        event = response.json()["event"]
        require(event["source_type"] == "human_label", "source_type not persisted")
        require(event["status"] == "edited", "status not persisted")

        # importer: first run creates 2, skips 1 unlinked
        with sqlite3.connect(bridge_core.DB_PATH) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            summary = import_label_clips(conn, SAMPLE_CLIPS, SAMPLE_LINKS)
            conn.commit()
        require(summary == {"created": 2, "skipped_existing": 0, "skipped_unlinked": 1, "skipped_invalid": 0}, f"unexpected first import summary: {summary}")

        # importer: second run is fully idempotent
        with sqlite3.connect(bridge_core.DB_PATH) as conn:
            summary = import_label_clips(conn, SAMPLE_CLIPS, SAMPLE_LINKS)
            conn.commit()
        require(summary["created"] == 0 and summary["skipped_existing"] == 2, f"import not idempotent: {summary}")

        # conversion checks: spanned clip via the scoped list API
        listing = client.get(
            "/capture-events",
            params={"encounter_id": "enc-smoke-1"},
            headers=headers,
        )
        require(listing.status_code == 200, f"capture-events listing failed: {listing.status_code}")
        events = {
            event["payload"].get("clip_id"): event
            for event in listing.json()["items"]
            if event["source_type"] == "human_label"
        }
        spanned = events.get("PB9_0001")
        require(spanned is not None, "spanned clip not imported")
        require(spanned["start_ms"] == 12500 and spanned["end_ms"] == 34000, f"sec->ms conversion wrong: {spanned['start_ms']}..{spanned['end_ms']}")
        require(spanned["confidence"] == LABEL_CONFIDENCE_TO_SCORE["high"], "confidence crosswalk wrong")
        require(spanned["status"] == "edited", "imported labels must enter as edited")
        require(
            spanned["payload"]["extraction_key"]
            == human_label_extraction_key("ev-smoke-1", "PB9_0001", "pt_smoke"),
            "extraction key mismatch",
        )
        # clip-level label (no visit/encounter scope) verified directly in DB
        with sqlite3.connect(bridge_core.DB_PATH) as conn:
            row = conn.execute(
                "SELECT start_ms, end_ms, status, confidence FROM capture_events "
                "WHERE source_type = 'human_label' "
                "AND json_extract(payload_json, '$.clip_id') = 'PB9_0002'"
            ).fetchone()
        require(row is not None, "clip-level label not imported")
        require(row[0] is None and row[1] is None, "clip-level label must have null spans")
        require(row[2] == "edited", "clip-level label must enter as edited")
        require(row[3] == LABEL_CONFIDENCE_TO_SCORE["medium"], "medium confidence crosswalk wrong")

    print("human_label_smoke_test: PASS")


if __name__ == "__main__":
    main()
