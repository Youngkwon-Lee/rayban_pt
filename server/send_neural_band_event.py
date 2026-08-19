#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a neural-band gesture event to the Kinelo AR bridge.")
    parser.add_argument("gesture", help="Gesture name such as tap, double_tap, down, select, or toggle_recording")
    parser.add_argument("--base-url", default=os.getenv("BRIDGE_BASE_URL", "http://127.0.0.1:8791"))
    parser.add_argument("--api-key", default=os.getenv("BRIDGE_API_KEY", ""))
    parser.add_argument("--device-id", default=os.getenv("NEURAL_BAND_DEVICE_ID", ""))
    parser.add_argument("--source", default=os.getenv("NEURAL_BAND_SOURCE", "neural_band"))
    args = parser.parse_args()

    if not args.api_key:
        print("BRIDGE_API_KEY or --api-key is required", file=sys.stderr)
        return 1

    payload = {
        "gesture": args.gesture,
        "device_id": args.device_id or None,
        "source": args.source,
    }

    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/neural-band/event",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": args.api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(response.read().decode("utf-8"))
            return 0
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
