#!/usr/bin/env python3
"""Loopback-only HUD preview. Synthetic in-memory data; no bridge/device access."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1] / "server/static/glass-webapp"
PREVIEW = {"cue": "오늘 확인할 항목", "lines": ["동작 전 불편감 확인", "선택한 동작 기록", "종료 후 반응 확인"], "lens_safe": True}
CANDIDATE = {"id": "qa-candidate-1", "encounter_id": "qa-encounter", "title": "좌측 SLR / 45도", "body": "치료사 확인 전 초안", "lines": ["좌측 SLR / 45도", "후면 당김 보고", "치료사 확인 전 초안"], "status": "candidate", "lens_safe": True}


def fixture(scenario):
    state = {"patient": "Q*A", "mode": "pre_review", "phase": "pre_review", "visit_session_id": "qa-session", "session_count": 2, "readiness": "ready", "is_recording": False, "last_insight": None, "active_hud_candidate": None}
    if scenario == "before":
        state.update(patient=None, visit_session_id=None, mode="standby")
    elif scenario in ("recording", "candidate"):
        state.update(mode="recording", is_recording=True, recording_start=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(), active_hud_candidate=dict(CANDIDATE))
    elif scenario == "insight":
        state["last_insight"] = {"id": "qa-cue-1", "title": "종료 전 확인", "body": "선택한 확인 항목을 다시 살펴보세요.", "lens_safe": True}
    elif scenario == "summary":
        state.update(mode="summary", phase="summary", readiness="sync_pending", message="진행 노트 초안 준비됨")
    elif scenario in ("uploading", "analyzing", "error"):
        state.update(mode=scenario)
    return state


STATE = fixture("before")
REQUESTS = []
OFFLINE = False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def reply(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/glass/state":
            return self.reply(STATE, 503 if OFFLINE else 200)
        if path == "/glass/command":
            return self.reply({"command": None})
        if path == "/glass/visits/next":
            return self.reply({"candidate": {"id": "qa-encounter", "patient_alias": "Q*A", "record_preview": PREVIEW}})
        if path == "/__fixture/requests":
            return self.reply(REQUESTS)
        return super().do_GET()

    def do_POST(self):
        global STATE, OFFLINE
        path = urlparse(self.path).path
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if path == "/__fixture":
            scenario = data.get("scenario", "before")
            STATE = fixture(scenario)
            STATE.update(data.get("state", {}))
            OFFLINE = scenario == "offline"
            REQUESTS.clear()
            return self.reply(STATE)
        REQUESTS.append({"path": path, "body": data})
        if path == "/glass/visits/start":
            STATE = fixture("session")
            return self.reply({"glass_state": STATE})
        if path == "/glass/state":
            STATE.update(data)
            return self.reply({"ok": True})
        if path == "/glass/command":
            command = data.get("command")
            if command in ("approve_candidate", "discard_candidate"):
                current = STATE.get("active_hud_candidate") or {}
                if data.get("candidate_id") and data["candidate_id"] != current.get("id"):
                    return self.reply({"executed": {"ok": False, "error_code": "HUD_CANDIDATE_CHANGED", "message": "후보가 변경되었습니다."}})
                STATE["active_hud_candidate"] = None
            elif command == "toggle_recording":
                STATE["is_recording"] = not STATE.get("is_recording")
                STATE["mode"] = "recording" if STATE["is_recording"] else "pre_review"
            elif command == "end_visit_session":
                STATE = fixture("summary") if STATE["mode"] == "summary" else dict(STATE, mode="summary", phase="summary")
            return self.reply({"executed": {"ok": True, "glass_state": STATE}})
        return self.reply({"error": "fixture endpoint not implemented"}, 404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8793)
    args = parser.parse_args()
    print(f"Synthetic HUD preview: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
