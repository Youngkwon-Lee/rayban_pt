from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from lib.moai_constraints import validate_physio_payload_constraints


@dataclass
class MoaiWriterConfig:
    base_url: str
    api_key: str
    auth_header: str | None = None
    timeout_seconds: int = 20


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def load_moai_writer_config() -> MoaiWriterConfig | None:
    base_url = (os.getenv("MOAI_WEB_SUPABASE_URL") or os.getenv("SUPABASE_URL") or "").strip()
    secret_key = (os.getenv("MOAI_WEB_SUPABASE_SECRET_KEY") or "").strip()
    service_role_key = (os.getenv("MOAI_WEB_SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    timeout_seconds = int(os.getenv("MOAI_WEB_SUPABASE_TIMEOUT_SECONDS", "20"))

    if not base_url:
        project_ref = (os.getenv("MOAI_WEB_SUPABASE_PROJECT_REF") or "").strip()
        if project_ref:
            base_url = f"https://{project_ref}.supabase.co"

    if not base_url:
        return None
    if secret_key:
        return MoaiWriterConfig(
            base_url=_normalize_base_url(base_url),
            api_key=secret_key,
            auth_header=None,
            timeout_seconds=timeout_seconds,
        )
    if service_role_key:
        return MoaiWriterConfig(
            base_url=_normalize_base_url(base_url),
            api_key=service_role_key,
            auth_header=f"Bearer {service_role_key}",
            timeout_seconds=timeout_seconds,
        )
    return None


def _iter_bundle_items(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    ordered_sections = [
        "encounter",
        "media",
        "voice_memos",
        "ai_inference_logs",
        "media_summaries",
        "notes",
        "observations",
        "activity_sessions",
        "reviews",
        "audit_events",
    ]
    items: list[dict[str, Any]] = []
    for section in ordered_sections:
        section_value = bundle.get(section)
        if isinstance(section_value, list):
            items.extend(section_value)
        elif isinstance(section_value, dict) and section_value.get("target_table"):
            items.append(section_value)
    return items


def build_moai_write_plan(bundle: dict[str, Any]) -> dict[str, Any]:
    items = _iter_bundle_items(bundle)
    operations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in items:
        target_table = item.get("target_table")
        payload = item.get("payload") or {}
        valid = bool(item.get("valid_for_upsert"))
        missing_fields = list(item.get("missing_required_fields") or [])
        warnings = list(item.get("warnings") or [])
        if not target_table:
            continue
        if not valid:
            skipped.append(
                {
                    "target_table": target_table,
                    "reason": "missing_required_fields",
                    "missing_required_fields": missing_fields,
                    "warnings": warnings,
                }
            )
            continue
        constraint_violations = validate_physio_payload_constraints(str(target_table), payload)
        if constraint_violations:
            skipped.append(
                {
                    "target_table": target_table,
                    "reason": "constraint_violation",
                    "constraint_violations": constraint_violations,
                    "warnings": warnings,
                }
            )
            continue

        has_id = isinstance(payload, dict) and bool(payload.get("id"))
        operations.append(
            {
                "target_table": target_table,
                "action": "upsert" if has_id else "insert",
                "on_conflict": "id" if has_id else None,
                "payload": payload,
                "warnings": warnings,
            }
        )

    return {
        "context": bundle.get("context") or {},
        "validation": bundle.get("validation") or {},
        "summary": {
            "operation_count": len(operations),
            "skipped_count": len(skipped),
        },
        "operations": operations,
        "skipped": skipped,
    }


def _build_headers(config: MoaiWriterConfig) -> dict[str, str]:
    headers = {
        "apikey": config.api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if config.auth_header:
        headers["Authorization"] = config.auth_header
    return headers


def execute_moai_write_plan(plan: dict[str, Any], *, config: MoaiWriterConfig | None = None) -> dict[str, Any]:
    config = config or load_moai_writer_config()
    if config is None:
        raise RuntimeError(
            "moai writer is not configured; set MOAI_WEB_SUPABASE_URL and "
            "MOAI_WEB_SUPABASE_SECRET_KEY or MOAI_WEB_SUPABASE_SERVICE_ROLE_KEY"
        )

    headers = _build_headers(config)
    results: list[dict[str, Any]] = []
    for op in plan.get("operations") or []:
        target_table = op["target_table"]
        action = op["action"]
        payload = op["payload"]
        url = f"{config.base_url}/rest/v1/{target_table}"
        params: dict[str, str] = {}
        request_headers = dict(headers)
        if action == "upsert":
            params["on_conflict"] = str(op.get("on_conflict") or "id")
            request_headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        else:
            request_headers["Prefer"] = "return=representation"

        response = requests.post(
            url,
            headers=request_headers,
            params=params,
            json=payload,
            timeout=config.timeout_seconds,
        )
        results.append(
            {
                "target_table": target_table,
                "action": action,
                "status_code": response.status_code,
                "ok": response.ok,
                "response_text": response.text[:2000],
            }
        )
        response.raise_for_status()

    return {
        "context": plan.get("context") or {},
        "summary": {
            "attempted": len(plan.get("operations") or []),
            "succeeded": len(results),
        },
        "results": results,
        "skipped": plan.get("skipped") or [],
    }
