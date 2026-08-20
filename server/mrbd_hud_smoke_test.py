#!/usr/bin/env python3
"""Static contract smoke test for the Meta Ray-Ban Display HUD."""

from __future__ import annotations

import json
import struct
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
    shortlink = client.get("/g/demo", follow_redirects=False)
    require(shortlink.status_code == 302, "demo QR shortlink should redirect")
    require(
        shortlink.headers.get("location") == "/glass-app/?candidate_id=enc-demo-a1f607c7"
        or shortlink.headers.get("location") == "/glass-app/?api_key=mrbd-temp-20260622&candidate_id=enc-demo-a1f607c7",
        "demo QR shortlink redirect target mismatch",
    )
    previous_bridge_key = bridge.BRIDGE_API_KEY
    bridge.BRIDGE_API_KEY = "hud-shortlink-test-key"
    try:
        hud_test_shortlink = client.get("/g/hud-test", follow_redirects=False)
        public_hud_state = client.get("/glass/state", headers={"x-hud-test": "1"})
        public_hud_command = client.get("/glass/command", headers={"x-hud-test": "1"})
    finally:
        bridge.BRIDGE_API_KEY = previous_bridge_key
    hud_test_location = hud_test_shortlink.headers.get("location") or ""
    require(hud_test_shortlink.status_code == 302, "HUD test shortlink should redirect")
    require(
        hud_test_location.startswith("/glass-app/?hud_token=h1."),
        "HUD test shortlink must issue a scoped token",
    )
    require("api_key=" not in hud_test_location, "HUD test shortlink must not expose the bridge API key")
    require(public_hud_state.status_code == 200, "public HUD test state should be reachable")
    require(
        public_hud_state.json().get("message") in {"HUD 테스트 연결 대기", "HUD 테스트 방문 진행 중"},
        "public HUD test must return isolated state",
    )
    require(public_hud_command.status_code == 401, "public HUD test must not access capture commands")
    glass_html = glass_app.text
    require('name="viewport" content="width=600, height=600' in glass_html, "MRBD viewport meta missing")
    require('name="mrbd-web-app-capable" content="yes"' in glass_html, "MRBD capability meta missing")
    require(
        'rel="icon" href="favicon.png" type="image/png"' in glass_html
        or 'rel="icon" href="/favicon.png" type="image/png"' in glass_html,
        "MRBD PNG favicon link missing",
    )
    require(
        'rel="manifest" href="manifest.webmanifest"' in glass_html
        or 'rel="manifest" href="/manifest.webmanifest"' in glass_html,
        "MRBD manifest link missing",
    )
    require('aria-label="HUD 명령"' in glass_html, "HUD command rail missing")
    require('aria-label="Visit phase"' not in glass_html, "HUD should not duplicate encounter workflow phases")
    require('id="patient-context-label"' in glass_html, "HUD patient context label missing")
    require('id="readiness-label"' in glass_html, "HUD readiness indicator missing")
    require('id="end-label"' in glass_html, "HUD dynamic end label missing")
    require('id="record-list"' in glass_html, "HUD record preview list missing")
    require('id="status-card" tabindex="0"' in glass_html, "HUD record card should support contextual focus")
    require('data-action="cycle_record_preview"' in glass_html, "HUD record card should cycle record preview")
    require('id="capture-role-label"' not in glass_html, "HUD should not expose capture role controls")
    require('id="role-counts-label"' not in glass_html, "HUD should not expose role counters")
    require("phase-chip" not in glass_html, "HUD should use capture labels instead of phase chips")
    for command in ["toggle_recording", "next_phase", "end_visit_session"]:
        require(f'data-action="{command}"' in glass_html, f"HUD command missing: {command}")
    for command in ["next_role", "show_recommendations"]:
        require(f'data-action="{command}"' not in glass_html, f"HUD should not expose optional command: {command}")
    require('data-action="open_capture_history"' not in glass_html, "HUD should not expose full history navigation")
    require(glass_html.count("focusable command-button") == 3, "HUD should expose three focusable commands")

    glass_css = client.get("/glass-app/styles.css")
    require(glass_css.status_code == 200, "glass webapp CSS should load")
    css_text = glass_css.text
    require("width: 600px;" in css_text and "height: 600px;" in css_text, "HUD CSS should fix 600x600 canvas")
    require("background: #000000" in css_text, "HUD page background should be additive-display transparent black")
    require("height: 88px;" in css_text, "HUD commands should use MRBD button height")
    require("grid-template-areas:" in css_text and '"commands"' in css_text, "HUD commands should own an explicit grid row")
    require("grid-template-rows: 64px 64px minmax(0, 1fr) 0 88px;" in css_text, "HUD should reserve no blank bottom row")
    require(".record-list" in css_text, "HUD CSS should include record preview list")
    require(".record-position" in css_text, "HUD record preview should expose pager position")
    require("white-space: nowrap;" in css_text, "HUD text should avoid uncontrolled wrapping")
    require("transform: scale(0.94)" not in css_text, "HUD focus should not resize command buttons")
    require("transform: scale(0.90)" not in css_text, "HUD press should not resize command buttons")

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
        "TARGET_CANDIDATE_ID",
        "commandResultLabel",
        "safePatientAlias",
        "renderMiddleButton",
        "renderEndButton",
        "renderReadiness",
        "refreshVisibleStatus",
        "completeVisitHudSession",
        "complete_visit_hud",
        "closeHud",
        "close_hud",
        "window.close",
        "pollPendingCommand",
        "/glass/command",
        "recordPreview",
        "sessionRecordPreview",
        "recordPreviewOpen",
        "recordPreviewOpen = false",
        "renderRecordList",
        "document.createElement('p')",
        "scrollRecordCard",
        "focusRecordCard",
        "commandButtons",
        "handleNavigationCommand",
        "selectFocused",
        "nav_up",
        "nav_down",
        "nav_left",
        "nav_right",
        "select_focused",
        "record-preview-mode",
        "previewMeta",
        "cycleRecordPreview",
        "cycle_record_preview",
        "glassState.visit_session_id && recordPreview()",
        "previewCaption",
        "record_preview",
        "기록 확인",
        "기록 보기",
        "다음 기록",
        "기록 대기",
        "영상 시작으로 평가와 중재를 캡처하세요.",
        "statusToastLabel",
        "pollStateQuiet",
        "환자 확인",
        "방문 시작",
        "다른 환자",
        "환자 고정",
        "세션 종료",
        "종료 확인",
        "종료 확정",
        "완료",
        "다음 방문 대기",
        "HUD 정리됨",
        "sync_pending",
        "bridge_url",
        "normalizeBaseUrl",
        "BRIDGE_BASE_URL",
        "IS_HUD_TEST",
        "x-hud-test",
        "transportConnected",
        "statusCardInteractive",
        "lensSafeMessage",
        "연결 확인 필요",
        "HUD 닫기",
        "!el.disabled",
    ]:
        require(token in js_text, f"HUD JS should include {token}")
    require("params.push('api_key=" not in js_text, "HUD requests should not copy API keys into query strings")
    require("params.push('hud_token=" not in js_text, "HUD requests should not copy scope tokens into query strings")
    require("window.history.replaceState" in js_text, "HUD should remove bootstrap auth params from the address bar")
    require("/hud-test" in js_text, "HUD should recognize the public device-test route")
    for token in ["renderCaptureRole", "normalizedCaptureRole", "roleCountsLabel", "next_role", "show_recommendations"]:
        require(token not in js_text, f"HUD JS should not include optional workflow UI token: {token}")
    require("상태 확인 요청됨" not in js_text, "HUD status button should not enqueue a server request")
    require("상태 새로고침" not in js_text, "HUD should not expose a confusing status refresh control")
    require("offsetParent !== null" in js_text, "HUD focus navigation should skip hidden controls")
    require("querySelectorAll('.focusable')" in js_text, "HUD JS should drive focusable D-pad navigation")

    webapp_root = Path(__file__).parent / "static" / "glass-webapp"
    manifest = json.loads((webapp_root / "manifest.webmanifest").read_text(encoding="utf-8"))
    require(manifest["name"] == "Kinelo AR", "HUD manifest name mismatch")
    require(manifest["display"] == "standalone", "HUD manifest display mode mismatch")
    require(manifest["icons"][0]["src"] == "favicon.png", "HUD manifest icon src mismatch")
    require(manifest["icons"][0]["sizes"] == "128x128", "HUD manifest icon size mismatch")
    favicon_bytes = (webapp_root / "favicon.png").read_bytes()
    require(favicon_bytes.startswith(b"\x89PNG\r\n\x1a\n"), "HUD favicon should be a PNG")
    width, height = struct.unpack(">II", favicon_bytes[16:24])
    require(width == 128 and height == 128, "HUD favicon should be 128x128")
    package_json = json.loads((webapp_root / "package.json").read_text(encoding="utf-8"))
    require(package_json["scripts"]["start"] == "node server.js", "HUD package should expose Vercel start script")
    server_js = (webapp_root / "server.js").read_text(encoding="utf-8")
    require("process.env.PORT || 3000" in server_js, "HUD static server should honor Vercel PORT")
    require("path.resolve" in server_js and "Forbidden" in server_js, "HUD static server should block path traversal")
    require("application/manifest+json" in server_js, "HUD static server should serve manifest MIME type")
    vercel_json = json.loads((webapp_root / "vercel.json").read_text(encoding="utf-8"))
    require(
        vercel_json["rewrites"][0]["source"] == "/hud-test"
        and vercel_json["rewrites"][0]["destination"] == "/index.html",
        "HUD Vercel public test rewrite mismatch",
    )
    require(
        vercel_json["rewrites"][1]["source"] == "/connect/(.*)"
        and vercel_json["rewrites"][1]["destination"] == "/index.html",
        "HUD Vercel connect rewrite mismatch",
    )
    require(
        any(
            rule.get("source") == "/glass/(.*)"
            and rule.get("destination") == "https://desktop-t43sn5m-1.tailde3b80.ts.net/glasspt/glass/$1"
            for rule in vercel_json["rewrites"]
        ),
        "HUD Vercel same-origin bridge proxy rewrite missing",
    )
    require(
        any(rule.get("source") == "/(.*)" for rule in vercel_json["rewrites"]),
        "HUD Vercel catch-all rewrite missing",
    )

    console_js = (Path(__file__).parent / "static" / "neural-band-console" / "app.js").read_text(encoding="utf-8")
    require("apiKey: elements.apiKey.value.trim()" not in console_js, "Neural Band console must not persist API keys")
    require("apiKey: \"\"" in console_js, "Neural Band console should clear legacy persisted API keys")

    print("OK: MRBD HUD static contract passed")


if __name__ == "__main__":
    main()
