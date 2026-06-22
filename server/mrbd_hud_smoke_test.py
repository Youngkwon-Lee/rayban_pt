#!/usr/bin/env python3
"""Static contract smoke test for the Meta Ray-Ban Display HUD."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app as bridge


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    client = TestClient(bridge.app)

    glass_app = client.get("/glass-app/")
    require(glass_app.status_code == 200, "glass webapp should be public")
    glass_html = glass_app.text
    require('name="viewport" content="width=600, height=600' in glass_html, "MRBD viewport meta missing")
    require('name="mrbd-web-app-capable" content="yes"' in glass_html, "MRBD capability meta missing")
    require('aria-label="HUD commands"' in glass_html, "HUD command rail missing")
    require('aria-label="Visit phase"' in glass_html, "HUD visit phase strip missing")
    require('id="readiness-label"' in glass_html, "HUD readiness indicator missing")
    require('id="capture-role-label"' in glass_html, "HUD capture role indicator missing")
    require('id="role-counts-label"' in glass_html, "HUD role counts indicator missing")
    for phase in ["pre_review", "assessment", "intervention", "home_program", "summary"]:
        require(f'data-phase="{phase}"' in glass_html, f"HUD phase chip missing: {phase}")
    for command in ["toggle_recording", "next_phase", "next_role", "show_recommendations", "open_capture_history", "end_visit_session"]:
        require(f'data-action="{command}"' in glass_html, f"HUD command missing: {command}")
    require(glass_html.count("focusable command-button") == 6, "HUD should expose six focusable commands")

    glass_css = client.get("/glass-app/styles.css")
    require(glass_css.status_code == 200, "glass webapp CSS should load")
    css_text = glass_css.text
    require("width: 600px;" in css_text and "height: 600px;" in css_text, "HUD CSS should fix 600x600 canvas")
    require("background: #000000" in css_text, "HUD page background should be additive-display transparent black")
    require("min-height: 88px;" in css_text, "HUD commands should use MRBD button height")

    glass_js = client.get("/glass-app/app.js")
    require(glass_js.status_code == 200, "glass webapp JS should load")
    js_text = glass_js.text
    for token in [
        "ArrowRight",
        "ArrowDown",
        "ArrowLeft",
        "ArrowUp",
        "sendCommand",
        "startVisit",
        "/glass/visits/start",
        "/glass/visits/next",
        "commandResultLabel",
        "safePatientAlias",
        "renderPhase",
        "renderCaptureRole",
        "normalizedCaptureRole",
        "roleCountsLabel",
        "capture_role",
        "event_role_counts",
        "next_role",
        "renderReadiness",
        "bridge_url",
        "normalizeBaseUrl",
        "BRIDGE_BASE_URL",
    ]:
        require(token in js_text, f"HUD JS should include {token}")
    require("querySelectorAll('.focusable')" in js_text, "HUD JS should drive focusable D-pad navigation")

    webapp_root = Path(__file__).parent / "static" / "glass-webapp"
    package_json = json.loads((webapp_root / "package.json").read_text(encoding="utf-8"))
    require(package_json["scripts"]["start"] == "node server.js", "HUD package should expose Vercel start script")
    server_js = (webapp_root / "server.js").read_text(encoding="utf-8")
    require("process.env.PORT || 3000" in server_js, "HUD static server should honor Vercel PORT")
    require("path.resolve" in server_js and "Forbidden" in server_js, "HUD static server should block path traversal")
    vercel_json = json.loads((webapp_root / "vercel.json").read_text(encoding="utf-8"))
    require(vercel_json["rewrites"][0]["source"] == "/(.*)", "HUD Vercel rewrite source mismatch")

    print("OK: MRBD HUD static contract passed")


if __name__ == "__main__":
    main()
