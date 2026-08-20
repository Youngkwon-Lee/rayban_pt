#!/usr/bin/env python3
"""Build Meta Ray-Ban Display web app deep links for device testing."""

from __future__ import annotations

import argparse
import json
from urllib.parse import quote, urlencode, urlparse


def _valid_https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise argparse.ArgumentTypeError("app URL must be an absolute https:// URL")
    return value


def build_deep_link(*, app_name: str, app_url: str) -> str:
    query = urlencode({"appName": app_name, "appUrl": app_url}, quote_via=quote)
    return f"fb-viewapp://web_app_deep_link?{query}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Meta AI app deep link for an MRBD web app.")
    parser.add_argument("app_url", type=_valid_https_url, help="Public HTTPS web app URL")
    parser.add_argument("--app-name", default="Kinelo AR")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the raw deep link")
    args = parser.parse_args()

    deep_link = build_deep_link(app_name=args.app_name, app_url=args.app_url)
    if args.json:
        print(json.dumps({"app_name": args.app_name, "app_url": args.app_url, "deep_link": deep_link}, indent=2))
    else:
        print(deep_link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
