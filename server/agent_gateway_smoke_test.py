#!/usr/bin/env python3
"""Smoke test for the dry-run clinical agent gateway."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

os.environ["BRIDGE_API_KEY"] = "agent-smoke-key"
os.environ["REQUIRE_API_KEY"] = "true"
os.environ["ALLOW_INSECURE_LAN"] = "false"

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
    with tempfile.TemporaryDirectory(prefix="rayban_agent_smoke_") as tmp:
        configure_isolated_storage(Path(tmp))
        client = TestClient(bridge.app)

        unauth = client.post("/agent/cue-dry-run", json={"mode": "ready"})
        require(unauth.status_code == 401, "agent gateway should require bridge auth")

        dry_run = client.post(
            "/agent/cue-dry-run",
            headers=headers(),
            json={
                "event_id": "event-agent-smoke",
                "mode": "recording",
                "patient_alias": "P7",
                "observed_phase": "standing balance",
                "context_summary": "mild fatigue, stable posture, no fall",
                "risk_flags": ["fatigue"],
            },
        )
        require(dry_run.status_code == 200, f"cue dry-run should succeed: {dry_run.text}")
        body = dry_run.json()
        require(body["status"] == "dry_run", "agent gateway should stay dry-run")
        require(body["tool"] == "generate_session_cue", "cue tool name mismatch")
        require(body["cue"]["lens_safe"] is True, "cue should be lens-safe")
        require(body["cue"]["title"], "cue title should be present")
        require(len(body["cue"]["body"]) <= 80, "cue body should be lens-short")
        require(body["blocked_actions"], "agent response should list blocked actions")
        require(body["glass_state_updated"] is False, "dry-run should not update HUD by default")

        state_before = client.get("/glass/state", headers=headers()).json()
        require(state_before["last_insight"] is None, "HUD insight should stay empty without update_glass")

        update = client.post(
            "/agent/cue-dry-run",
            headers=headers(),
            json={
                "event_id": "event-agent-smoke",
                "mode": "recording",
                "patient_alias": "P7",
                "context_summary": "환자 김민수 010-1234-5678 fatigue appears",
                "risk_flags": ["fatigue"],
                "update_glass": True,
            },
        )
        require(update.status_code == 200, "cue update should succeed")
        updated = update.json()
        require(updated["glass_state_updated"] is True, "update_glass should update HUD")
        require("김민수" not in updated["cue"]["body"], "cue body should redact Korean patient name")
        require("010-1234-5678" not in updated["cue"]["body"], "cue body should redact phone")

        state_after = client.get("/glass/state", headers=headers()).json()
        require(state_after["last_insight"]["id"] == updated["cue"]["id"], "HUD should receive cue id")
        require(state_after["last_insight"]["lens_safe"] is True, "HUD insight should be lens-safe")

        blocked_tool = client.post(
            "/agent/cue-dry-run",
            headers=headers(),
            json={"requested_tool": "send_patient_message", "mode": "ready"},
        )
        require(blocked_tool.status_code == 403, "non-allowlisted tools should be blocked")

        extra_field = client.post(
            "/agent/cue-dry-run",
            headers=headers(),
            json={"mode": "ready", "raw_text": "raw clinical transcript"},
        )
        require(extra_field.status_code == 422, "raw transcript field should be rejected by schema")

    print("OK: agent cue dry-run smoke test passed")


if __name__ == "__main__":
    main()
