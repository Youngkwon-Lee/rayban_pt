#!/usr/bin/env python3
import argparse
import json
import os
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
import urllib.error
import urllib.request


DEFAULT_BRIDGE_BASE_URL = os.getenv("BRIDGE_BASE_URL", "http://127.0.0.1:8791")
DEFAULT_ADAPTER_HOST = os.getenv("NEURAL_BAND_BRIDGE_HOST", "127.0.0.1")
DEFAULT_ADAPTER_PORT = int(os.getenv("NEURAL_BAND_BRIDGE_PORT", "8793"))


@dataclass
class ForwardConfig:
    bridge_base_url: str
    api_key: str
    source: str = "neural_band"
    timeout_sec: float = 10.0


def forward_gesture(
    *,
    gesture: str,
    config: ForwardConfig,
    device_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = {
        "gesture": gesture,
        "device_id": device_id,
        "source": config.source,
        "metadata": metadata or {},
    }

    request = urllib.request.Request(
        config.bridge_base_url.rstrip("/") + "/neural-band/event",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": config.api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=config.timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise RuntimeError(f"bridge returned {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"bridge unreachable: {exc.reason}") from exc


def parse_stdin_line(line: str) -> tuple[str, Optional[str], dict[str, Any]]:
    raw = line.strip()
    if not raw:
        raise ValueError("empty line")

    if raw.startswith("{"):
        payload = json.loads(raw)
        gesture = str(payload["gesture"])
        device_id = payload.get("device_id")
        metadata = payload.get("metadata") or {}
        return gesture, device_id, metadata

    return raw, None, {}


def run_stdin_mode(config: ForwardConfig) -> int:
    print("stdin mode ready: enter a gesture per line, or JSON with gesture/device_id/metadata", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            gesture, device_id, metadata = parse_stdin_line(line)
            response = forward_gesture(
                gesture=gesture,
                config=config,
                device_id=device_id,
                metadata=metadata,
            )
            print(json.dumps(response, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    return 0


def make_handler(config: ForwardConfig):
    class NeuralBandHandler(BaseHTTPRequestHandler):
        server_version = "NeuralBandBridge/0.1"

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return

            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "neural-band-bridge",
                    "bridge_base_url": config.bridge_base_url,
                    "source": config.source,
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/gesture":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                payload = json.loads(raw_body or "{}")
                gesture = str(payload["gesture"])
                device_id = payload.get("device_id")
                metadata = payload.get("metadata") or {}
                response = forward_gesture(
                    gesture=gesture,
                    config=config,
                    device_id=device_id,
                    metadata=metadata,
                )
                self._send_json(200, response)
            except KeyError:
                self._send_json(400, {"ok": False, "error": "gesture is required"})
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "invalid_json"})
            except Exception as exc:
                self._send_json(502, {"ok": False, "error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    return NeuralBandHandler


def run_http_server(config: ForwardConfig, host: str, port: int) -> int:
    handler = make_handler(config)
    server = ThreadingHTTPServer((host, port), handler)
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "serve",
                "listen": f"http://{host}:{port}",
                "bridge_base_url": config.bridge_base_url,
                "source": config.source,
            },
            ensure_ascii=False,
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def add_common_forward_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bridge-base-url", default=DEFAULT_BRIDGE_BASE_URL)
    parser.add_argument("--api-key", default=os.getenv("BRIDGE_API_KEY", ""))
    parser.add_argument("--source", default=os.getenv("NEURAL_BAND_SOURCE", "neural_band"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Companion bridge for external neural-band gesture inputs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    send_parser = subparsers.add_parser("send", help="Send a single gesture to Kinelo AR.")
    add_common_forward_args(send_parser)
    send_parser.add_argument("gesture")
    send_parser.add_argument("--device-id", default=os.getenv("NEURAL_BAND_DEVICE_ID", ""))
    send_parser.add_argument("--metadata", default="")

    serve_parser = subparsers.add_parser("serve", help="Run a local HTTP adapter that forwards gestures.")
    add_common_forward_args(serve_parser)
    serve_parser.add_argument("--host", default=DEFAULT_ADAPTER_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_ADAPTER_PORT)

    stdin_parser = subparsers.add_parser("stdin", help="Read gestures from stdin and forward them.")
    add_common_forward_args(stdin_parser)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.api_key:
        print("BRIDGE_API_KEY or --api-key is required", file=sys.stderr)
        return 1

    config = ForwardConfig(
        bridge_base_url=args.bridge_base_url,
        api_key=args.api_key,
        source=args.source,
    )

    if args.command == "send":
        metadata = json.loads(args.metadata) if args.metadata else {}
        response = forward_gesture(
            gesture=args.gesture,
            config=config,
            device_id=args.device_id or None,
            metadata=metadata,
        )
        print(json.dumps(response, ensure_ascii=False))
        return 0

    if args.command == "serve":
        return run_http_server(config, args.host, args.port)

    if args.command == "stdin":
        return run_stdin_mode(config)

    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
