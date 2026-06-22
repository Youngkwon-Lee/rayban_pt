#!/usr/bin/env python3
"""Agent-friendly MLOps harness for the Rayban PT bridge.

This script gives Codex/MCP agents a small, predictable command surface for
sync planning, guarded Supabase writes, and dataset snapshot planning.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import app as bridge
from lib.visit_session import get_visit_session
from lib.moai_writer import build_moai_write_plan, execute_moai_write_plan, load_moai_writer_config


WRITE_GATE_ENV = "MOAI_HARNESS_ALLOW_WRITES"


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_command(args: list[str], timeout_seconds: int = 10) -> dict[str, Any]:
    executable = shutil.which(args[0])
    if not executable:
        return {"available": False, "ok": False, "error": f"{args[0]} not found"}
    try:
        completed = subprocess.run(
            [executable, *args[1:]],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {"available": True, "ok": False, "error": "timed out"}
    return {
        "available": True,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip()[-2000:],
        "stderr": completed.stderr.strip()[-2000:],
    }


def _writer_status() -> dict[str, Any]:
    config = load_moai_writer_config()
    if config is None:
        return {
            "configured": False,
            "base_url": None,
            "has_api_key": False,
            "has_authorization_header": False,
            "timeout_seconds": None,
        }
    return {
        "configured": True,
        "base_url": config.base_url,
        "has_api_key": bool(config.api_key),
        "has_authorization_header": bool(config.auth_header),
        "timeout_seconds": config.timeout_seconds,
    }


def _list_candidate_event_ids(*, limit: int, status: str | None = None) -> list[str]:
    query = "SELECT id FROM events"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with bridge._conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [str(row[0]) for row in rows]


def _build_bundle_and_plan(args: argparse.Namespace, event_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if event_id.startswith("visit-sync-"):
        session_id = event_id.removeprefix("visit-sync-")
        with bridge._conn() as conn:
            session = get_visit_session(conn, session_id)
        if not session:
            raise KeyError(f"visit session not found for sync marker: {session_id}")
        bundle = {
            "context": {
                "source_system": "rayban_pt",
                "source_type": "visit_session_sync_marker",
                "source_visit_session_id": session_id,
                "encounter_id": session.get("encounter_id"),
                "subject_person_id": session.get("subject_person_id"),
                "provider_person_id": session.get("provider_person_id"),
            },
            "visit_session": session,
        }
        plan = bridge._build_visit_session_write_plan(session)
        return bundle, plan

    bundle = bridge._build_moai_bundle_for_event(
        event_id,
        subject_person_id=args.subject_person_id or None,
        provider_person_id=args.provider_person_id or None,
        encounter_id=args.encounter_id or None,
        capture_device=args.capture_device,
        resolve_identity=not args.no_resolve_identity,
    )
    plan = build_moai_write_plan(bundle)
    return bundle, plan


def _summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    context = dict(plan.get("context") or {})
    if context.get("identity_hints"):
        context["identity_hints_present"] = True
        context.pop("identity_hints", None)
    return {
        "context": context,
        "summary": plan.get("summary") or {},
        "operations": [
            {
                "target_table": op.get("target_table"),
                "action": op.get("action"),
                "on_conflict": op.get("on_conflict"),
                "warnings": op.get("warnings") or [],
            }
            for op in plan.get("operations") or []
        ],
        "skipped": plan.get("skipped") or [],
    }


def _execute_plan_if_allowed(plan: dict[str, Any], *, execute: bool, full: bool = False) -> dict[str, Any]:
    dry_run_result = plan if full else _summarize_plan(plan)
    if not execute:
        return {"status": "dry_run", "result": dry_run_result}
    if os.getenv(WRITE_GATE_ENV, "").strip().lower() not in {"1", "true", "yes", "y", "on"}:
        return {
            "status": "blocked",
            "code": "WRITE_GATE_CLOSED",
            "message": f"Set {WRITE_GATE_ENV}=true in the agent environment to allow Supabase writes.",
            "result": dry_run_result,
        }
    config = load_moai_writer_config()
    if config is None:
        return {
            "status": "blocked",
            "code": "MOAI_WRITER_NOT_CONFIGURED",
            "message": "Set MOAI_WEB_SUPABASE_URL and MOAI_WEB_SUPABASE_SECRET_KEY or MOAI_WEB_SUPABASE_SERVICE_ROLE_KEY.",
            "result": dry_run_result,
        }
    return {"status": "done", "result": execute_moai_write_plan(plan, config=config)}


def _sync_job_status_from_attempt(plan: dict[str, Any], attempt_result: dict[str, Any]) -> str:
    if attempt_result["status"] == "done":
        return "synced"
    if attempt_result["status"] == "blocked":
        return "blocked"
    if attempt_result["status"] == "dry_run":
        skipped_count = int((plan.get("summary") or {}).get("skipped_count") or 0)
        return "blocked" if skipped_count else "planned"
    return "error"


def _record_attempt(event_id: str, plan: dict[str, Any], attempt_result: dict[str, Any]) -> dict[str, Any]:
    job_status = _sync_job_status_from_attempt(plan, attempt_result)
    error = attempt_result.get("message") if attempt_result["status"] == "blocked" else None
    result = attempt_result.get("result") if attempt_result["status"] == "done" else None
    return bridge._record_moai_sync_job_attempt(
        event_id,
        status=job_status,
        plan=plan,
        result=result,
        error=error,
    )


def cmd_doctor(_args: argparse.Namespace) -> int:
    payload = {
        "status": "done",
        "checked_at": _now_iso(),
        "workspace": str(Path(__file__).resolve().parents[1]),
        "local_bridge": {
            "db_path": str(bridge.DB_PATH),
            "db_exists": bridge.DB_PATH.exists(),
            "write_gate_env": WRITE_GATE_ENV,
            "write_gate_open": os.getenv(WRITE_GATE_ENV, "").strip().lower() in {"1", "true", "yes", "y", "on"},
        },
        "moai_writer": _writer_status(),
        "supabase_cli": _run_command(["supabase", "projects", "list"], timeout_seconds=15),
        "huggingface_cli": _run_command(["hf", "auth", "whoami"], timeout_seconds=15),
        "agent_capabilities": {
            "expected_skills": [
                "supabase",
                "supabase-postgres-best-practices",
                "huggingface-trackio",
                "huggingface-community-evals",
                "huggingface-jobs",
                "huggingface-vision-trainer",
            ],
            "mcp_notes": [
                "Use Supabase tooling for schema/RLS/query verification.",
                "Use Hugging Face Jobs for remote dataset processing, evaluation, and training.",
                "Use Trackio inside training/eval jobs for metrics and alerts.",
            ],
        },
    }
    _print_json(payload)
    return 0


def cmd_event_plan(args: argparse.Namespace) -> int:
    bundle, plan = _build_bundle_and_plan(args, args.event_id)
    payload = {
        "status": "done",
        "event_id": args.event_id,
        "bundle": bundle if args.full else None,
        "plan": plan if args.full else _summarize_plan(plan),
    }
    if not args.full:
        payload.pop("bundle")
    _print_json(payload)
    return 0


def cmd_sync_event(args: argparse.Namespace) -> int:
    _bundle, plan = _build_bundle_and_plan(args, args.event_id)
    result = _execute_plan_if_allowed(plan, execute=args.execute, full=args.full)
    job = _record_attempt(args.event_id, plan, result)
    _print_json({"event_id": args.event_id, "sync_job": job, **result})
    return 0 if result["status"] in {"dry_run", "done"} else 2


def cmd_sync_recent(args: argparse.Namespace) -> int:
    event_ids = _list_candidate_event_ids(limit=args.limit, status=args.status)
    results: list[dict[str, Any]] = []
    exit_code = 0
    for event_id in event_ids:
        try:
            _bundle, plan = _build_bundle_and_plan(args, event_id)
            result = _execute_plan_if_allowed(plan, execute=args.execute, full=args.full)
            results.append({"event_id": event_id, **result})
            if result["status"] not in {"dry_run", "done"}:
                exit_code = 2
        except Exception as exc:
            results.append({"event_id": event_id, "status": "error", "message": str(exc)})
            exit_code = 1
            if not args.continue_on_error:
                break
    _print_json(
        {
            "status": "done" if exit_code == 0 else "partial",
            "execute": args.execute,
            "candidate_count": len(event_ids),
            "results": results,
        }
    )
    return exit_code


def cmd_inspect_sync_jobs(args: argparse.Namespace) -> int:
    jobs = bridge._list_moai_sync_jobs(status=args.status, limit=args.limit)
    _print_json({"status": "done", "count": len(jobs), "items": jobs})
    return 0


def cmd_sync_pending(args: argparse.Namespace) -> int:
    jobs = bridge._list_moai_sync_jobs(status=args.status, limit=args.limit)
    results: list[dict[str, Any]] = []
    exit_code = 0
    for job in jobs:
        event_id = str(job["event_id"])
        try:
            _bundle, plan = _build_bundle_and_plan(args, event_id)
            result = _execute_plan_if_allowed(plan, execute=args.execute, full=args.full)
            updated_job = _record_attempt(event_id, plan, result)
            results.append({"event_id": event_id, "sync_job": updated_job, **result})
            if result["status"] not in {"dry_run", "done"}:
                exit_code = 2
        except Exception as exc:
            error_job = bridge._record_moai_sync_job_attempt(event_id, status="error", error=str(exc))
            results.append({"event_id": event_id, "status": "error", "sync_job": error_job, "message": str(exc)})
            exit_code = 1
            if not args.continue_on_error:
                break
    _print_json(
        {
            "status": "done" if exit_code == 0 else "partial",
            "execute": args.execute,
            "candidate_count": len(jobs),
            "results": results,
        }
    )
    return exit_code


def _summarize_pilot_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    identity = manifest.get("identity") or {}
    session = manifest.get("session") or {}
    modalities = manifest.get("modalities") or {}
    readiness = manifest.get("readiness") or {}
    review = manifest.get("review") or {}
    labels = manifest.get("therapist_labels_v0") or {}
    agent_dry_run = manifest.get("agent_dry_run") or {}
    captured_modalities = [
        name
        for name, value in modalities.items()
        if isinstance(value, dict) and value.get("captured")
    ]
    return {
        "event_id": session.get("pilot_session_id"),
        "captured_at": session.get("captured_at"),
        "event_type": session.get("event_type"),
        "organization_id_present": bool(identity.get("organization_id")),
        "provider_person_id_present": bool(identity.get("provider_person_id")),
        "subject_person_id_present": bool(identity.get("subject_person_id")),
        "physio_client_id_present": bool(identity.get("physio_client_id")),
        "encounter_id_present": bool(identity.get("encounter_id")),
        "identity_resolution_status": identity.get("identity_resolution_status"),
        "captured_modalities": captured_modalities,
        "session_type_present": bool(labels.get("session_type")),
        "core_task_present": bool(labels.get("core_task")),
        "assist_level_present": bool(labels.get("assist_level")),
        "performance_level_present": bool(labels.get("performance_level")),
        "chart_review_status": review.get("chart_review_status"),
        "label_review_status": review.get("label_review_status"),
        "sync_job_enqueued": bool(agent_dry_run.get("sync_job_enqueued")),
        "usable_for_schema_eval": bool(readiness.get("usable_for_schema_eval")),
        "eligible_for_gold_dataset": bool(readiness.get("eligible_for_gold_dataset")),
        "missing_requirements": readiness.get("missing_requirements") or [],
        "gold_missing_requirements": readiness.get("gold_missing_requirements") or [],
    }


def _increment_counts(counts: dict[str, int], values: list[str]) -> None:
    for value in values:
        counts[value] = counts.get(value, 0) + 1


def _sorted_counts(counts: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"requirement": key, "count": value}
        for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _build_readiness_report(*, limit: int, status: str | None, resolve_identity: bool) -> dict[str, Any]:
    event_ids = _list_candidate_event_ids(limit=limit, status=status)
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    missing_schema_counts: dict[str, int] = {}
    missing_gold_counts: dict[str, int] = {}

    for event_id in event_ids:
        try:
            manifest = bridge._build_pilot_manifest_for_event(event_id, resolve_identity=resolve_identity)
            item = _summarize_pilot_manifest(manifest)
            items.append(item)
            _increment_counts(missing_schema_counts, item["missing_requirements"])
            _increment_counts(missing_gold_counts, item["gold_missing_requirements"])
        except Exception as exc:
            errors.append({"event_id": event_id, "message": str(exc)})

    usable_count = sum(1 for item in items if item["usable_for_schema_eval"])
    gold_count = sum(1 for item in items if item["eligible_for_gold_dataset"])
    return {
        "status": "done" if not errors else "partial",
        "generated_at": _now_iso(),
        "mode": "design/dry_run",
        "resolve_identity": resolve_identity,
        "filters": {
            "status": status or "all",
            "limit": limit,
        },
        "summary": {
            "candidate_count": len(event_ids),
            "scanned_count": len(items),
            "error_count": len(errors),
            "usable_for_schema_eval_count": usable_count,
            "eligible_for_gold_dataset_count": gold_count,
            "schema_eval_rate": round(usable_count / len(items), 4) if items else 0,
            "gold_eligibility_rate": round(gold_count / len(items), 4) if items else 0,
        },
        "missing_requirement_counts": _sorted_counts(missing_schema_counts),
        "gold_missing_requirement_counts": _sorted_counts(missing_gold_counts),
        "items": items,
        "errors": errors,
    }


def cmd_readiness_report(args: argparse.Namespace) -> int:
    status = None if args.status == "all" else args.status
    report = _build_readiness_report(
        limit=args.limit,
        status=status,
        resolve_identity=args.resolve_identity,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _print_json(
            {
                "status": report["status"],
                "output": str(output),
                "summary": report["summary"],
            }
        )
        return 0 if report["status"] == "done" else 1
    _print_json(report)
    return 0 if report["status"] == "done" else 1


@contextmanager
def _isolated_bridge_runtime():
    saved_paths = {
        "DB_PATH": bridge.DB_PATH,
        "UPLOAD_DIR": bridge.UPLOAD_DIR,
        "CHART_DIR": bridge.CHART_DIR,
        "MASKED_DIR": bridge.MASKED_DIR,
    }
    saved_flags = {
        "REQUIRE_API_KEY": bridge.REQUIRE_API_KEY,
        "BRIDGE_API_KEY": bridge.BRIDGE_API_KEY,
        "REQUIRE_PATIENT_CONSENT": bridge.REQUIRE_PATIENT_CONSENT,
        "PILOT_CAPTURE_MODE": bridge.PILOT_CAPTURE_MODE,
    }
    saved_async_results = dict(bridge.ASYNC_RESULTS)
    env_keys = ["PHI_REDACT", "SOAP_ENABLED", "IMAGE_STORE", "AUDIO_STORE", "VIDEO_STORE"]
    saved_env = {key: os.environ.get(key) for key in env_keys}

    with tempfile.TemporaryDirectory(prefix="rayban_pt_pilot_fixture_") as tmp:
        root = Path(tmp)
        try:
            bridge.DB_PATH = root / "bridge.db"
            bridge.UPLOAD_DIR = root / "uploads"
            bridge.CHART_DIR = root / "charts"
            bridge.MASKED_DIR = root / "masked"
            bridge.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            bridge.CHART_DIR.mkdir(parents=True, exist_ok=True)
            bridge.MASKED_DIR.mkdir(parents=True, exist_ok=True)
            bridge.ASYNC_RESULTS.clear()

            bridge.REQUIRE_API_KEY = True
            bridge.BRIDGE_API_KEY = "pilot-fixture-key"
            bridge.REQUIRE_PATIENT_CONSENT = True
            bridge.PILOT_CAPTURE_MODE = True
            os.environ.update(
                {
                    "PHI_REDACT": "true",
                    "SOAP_ENABLED": "true",
                    "IMAGE_STORE": "false",
                    "AUDIO_STORE": "false",
                    "VIDEO_STORE": "false",
                }
            )

            schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
            with sqlite3.connect(bridge.DB_PATH) as conn:
                conn.executescript(schema)

            yield {"root": root, "api_key": bridge.BRIDGE_API_KEY}
        finally:
            bridge.DB_PATH = saved_paths["DB_PATH"]
            bridge.UPLOAD_DIR = saved_paths["UPLOAD_DIR"]
            bridge.CHART_DIR = saved_paths["CHART_DIR"]
            bridge.MASKED_DIR = saved_paths["MASKED_DIR"]
            bridge.REQUIRE_API_KEY = saved_flags["REQUIRE_API_KEY"]
            bridge.BRIDGE_API_KEY = saved_flags["BRIDGE_API_KEY"]
            bridge.REQUIRE_PATIENT_CONSENT = saved_flags["REQUIRE_PATIENT_CONSENT"]
            bridge.PILOT_CAPTURE_MODE = saved_flags["PILOT_CAPTURE_MODE"]
            bridge.ASYNC_RESULTS.clear()
            bridge.ASYNC_RESULTS.update(saved_async_results)
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _fixture_json_response(response, step: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(f"{step} failed status={response.status_code} payload={payload}")
    return payload


def _pilot_fixture_label_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "session_type": args.session_type,
        "core_task": args.core_task,
        "custom_task": args.custom_task,
        "body_position": args.body_position,
        "assist_level": args.assist_level,
        "performance_level": args.performance,
        "review_status": args.review_status,
        "reviewer_person_id": args.provider_person_id,
        "usable_for_training": args.usable_for_training,
        "label_confidence": args.label_confidence,
        "repetition_count": args.repetition_count,
        "hold_duration_seconds": args.hold_duration_seconds,
        "tolerance": args.tolerance,
        "fatigue_level": args.fatigue_level,
        "compensations": args.compensation,
        "caregiver_present": args.caregiver_present,
        "flags": args.flag,
        "notes": "Synthetic non-PHI pilot fixture label reviewed for schema flow.",
    }


def _build_pilot_fixture(args: argparse.Namespace) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    with _isolated_bridge_runtime() as runtime:
        client = TestClient(bridge.app)
        headers = {
            "x-api-key": str(runtime["api_key"]),
            "x-glasspt-org-id": args.org_id,
            "x-glasspt-provider-person-id": args.provider_person_id,
        }

        consent = _fixture_json_response(
            client.post(
                "/consents",
                headers=headers,
                json={"patient_name": args.patient_name, "granted_by": args.granted_by},
            ),
            "consent",
        )
        ingest = _fixture_json_response(
            client.post(
                "/ingest",
                headers=headers,
                json={
                    "source": "pilot-fixture",
                    "event_type": "text",
                    "patient_name": args.patient_name,
                    "subject_person_id": args.subject_person_id,
                    "physio_client_id": args.physio_client_id,
                    "physio_session_id": args.encounter_id,
                    "text": args.text,
                },
            ),
            "ingest",
        )
        event_id = str(ingest["event_id"])
        label = _fixture_json_response(
            client.post(f"/labels/{event_id}", headers=headers, json=_pilot_fixture_label_payload(args)),
            "label",
        )
        readiness = _fixture_json_response(
            client.get(f"/events/{event_id}/pilot-readiness?resolve_identity=false", headers=headers),
            "readiness",
        )
        write_plan = _fixture_json_response(
            client.get(
                f"/events/{event_id}/moai-write-plan"
                f"?subject_person_id={args.subject_person_id}"
                f"&provider_person_id={args.provider_person_id}"
                f"&encounter_id={args.encounter_id}"
                "&resolve_identity=false",
                headers=headers,
            ),
            "write_plan",
        )
        report = _build_readiness_report(limit=5, status="processed", resolve_identity=False)

        return {
            "status": "done",
            "mode": "isolated_pilot_fixture",
            "generated_at": _now_iso(),
            "safety": {
                "uses_temporary_local_db": True,
                "writes_real_local_db": False,
                "writes_supabase": False,
                "training_started": False,
                "temp_root_removed_after_run": True,
            },
            "fixture": {
                "event_id": event_id,
                "patient_name": args.patient_name,
                "organization_id": args.org_id,
                "provider_person_id": args.provider_person_id,
                "subject_person_id": args.subject_person_id,
                "physio_client_id": args.physio_client_id,
                "encounter_id": args.encounter_id,
            },
            "steps": {
                "consent": {"ok": bool(consent.get("ok")), "active": True},
                "ingest": {"status": ingest.get("status"), "event_id": event_id},
                "label": {
                    "ok": bool(label.get("ok")),
                    "session_type": label.get("label", {}).get("session_type"),
                    "core_task": label.get("label", {}).get("core_task"),
                },
                "readiness": readiness.get("readiness"),
                "write_plan": _summarize_plan(write_plan.get("result") or {}),
                "readiness_report_summary": report.get("summary"),
            },
        }


def cmd_pilot_fixture(args: argparse.Namespace) -> int:
    payload = _build_pilot_fixture(args)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _print_json({"status": "done", "output": str(output), "event_id": payload["fixture"]["event_id"]})
        return 0
    _print_json(payload)
    return 0


def _dataset_snapshot_spec(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "snapshot_id": args.snapshot_id,
        "created_at": _now_iso(),
        "source_project": "moai_web",
        "source_system": "physio_app",
        "purpose": args.purpose,
        "storage_recommendation": {
            "format": "jsonl or parquet",
            "location": "Supabase Storage or Hugging Face Dataset repository",
            "immutability": "write once per snapshot_id",
        },
        "canonical_sources": [
            {
                "name": "reviewed_multimodal_encounters",
                "tables": [
                    "encounters",
                    "encounter_media",
                    "voice_memos",
                    "client_media_summaries",
                    "encounter_notes",
                    "observations",
                    "activity_sessions",
                    "clinical_extraction_reviews",
                ],
                "gold_filter": "review_status in ('approved', 'accepted', 'reviewed') or encounter_notes.approval_status in ('approved', 'signed')",
            },
            {
                "name": "ai_feedback_pairs",
                "tables": ["ai_inference_log", "ai_feedback", "clinical_extraction_reviews"],
                "gold_filter": "final_payload is not null or feedback_status in ('accepted', 'corrected', 'rejected')",
            },
            {
                "name": "model_lineage",
                "tables": ["ml_model_registry", "ml_predictions", "prompt_evaluation_runs", "prompt_evaluation_results"],
                "gold_filter": "status in ('production', 'candidate', 'evaluated')",
            },
        ],
        "agent_steps": [
            "Export reviewed rows from moai_web with Supabase service credentials.",
            "Join only by canonical IDs: organization_id, subject_person_id, encounter_id, ai_inference_id.",
            "Write immutable snapshot artifacts.",
            "Register snapshot metadata in model/evaluation tracking tables before training.",
            "Run evaluation first; train only if the snapshot passes minimum volume and quality checks.",
        ],
        "quality_gates": {
            "minimum_reviewed_encounters": args.minimum_reviewed_encounters,
            "required_modalities": args.required_modality,
            "require_clinician_review": True,
            "block_unresolved_identity": True,
            "block_unmasked_phi_media": True,
        },
    }


def cmd_dataset_manifest(args: argparse.Namespace) -> int:
    spec = _dataset_snapshot_spec(args)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _print_json({"status": "done", "output": str(output), "snapshot_id": args.snapshot_id})
        return 0
    _print_json({"status": "done", "manifest": spec})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rayban PT -> physio_app MLOps agent harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local agent, Supabase, and HF tooling readiness")
    doctor.set_defaults(func=cmd_doctor)

    event_plan = subparsers.add_parser("event-plan", help="Build a moai_web write plan for one local event")
    _add_event_args(event_plan)
    event_plan.add_argument("--full", action="store_true", help="Include full bundle and payloads")
    event_plan.set_defaults(func=cmd_event_plan)

    sync_event = subparsers.add_parser("sync-event", help="Dry-run or execute a moai_web write for one event")
    _add_event_args(sync_event)
    sync_event.add_argument("--execute", action="store_true", help="Execute Supabase writes; still requires MOAI_HARNESS_ALLOW_WRITES=true")
    sync_event.add_argument("--full", action="store_true", help="Include full dry-run payloads; may include PHI")
    sync_event.set_defaults(func=cmd_sync_event)

    sync_recent = subparsers.add_parser("sync-recent", help="Dry-run or execute writes for recent local events")
    _add_common_identity_args(sync_recent)
    sync_recent.add_argument("--limit", type=int, default=10)
    sync_recent.add_argument("--status", default="processed")
    sync_recent.add_argument("--execute", action="store_true", help="Execute Supabase writes; still requires MOAI_HARNESS_ALLOW_WRITES=true")
    sync_recent.add_argument("--continue-on-error", action="store_true")
    sync_recent.add_argument("--full", action="store_true", help="Include full dry-run payloads; may include PHI")
    sync_recent.set_defaults(func=cmd_sync_recent)

    inspect_jobs = subparsers.add_parser("inspect-sync-jobs", help="Inspect local moai_web sync job queue")
    inspect_jobs.add_argument("--status", default="pending", choices=["pending", "planned", "blocked", "synced", "error", "all"])
    inspect_jobs.add_argument("--limit", type=int, default=20)
    inspect_jobs.set_defaults(func=cmd_inspect_sync_jobs)

    sync_pending = subparsers.add_parser("sync-pending", help="Dry-run or execute queued moai_web sync jobs")
    _add_common_identity_args(sync_pending)
    sync_pending.add_argument("--status", default="pending", choices=["pending", "planned", "blocked", "error"])
    sync_pending.add_argument("--limit", type=int, default=20)
    sync_pending.add_argument("--execute", action="store_true", help="Execute Supabase writes; still requires MOAI_HARNESS_ALLOW_WRITES=true")
    sync_pending.add_argument("--continue-on-error", action="store_true")
    sync_pending.add_argument("--full", action="store_true", help="Include full dry-run payloads; may include PHI")
    sync_pending.set_defaults(func=cmd_sync_pending)

    dataset = subparsers.add_parser("dataset-manifest", help="Emit a reviewed-dataset snapshot manifest for agent execution")
    dataset.add_argument("--snapshot-id", default=f"rayban_pt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    dataset.add_argument("--purpose", default="multimodal_rehab_labeling")
    dataset.add_argument("--minimum-reviewed-encounters", type=int, default=50)
    dataset.add_argument("--required-modality", action="append", default=["image_or_video", "text_or_audio"])
    dataset.add_argument("--output", default="")
    dataset.set_defaults(func=cmd_dataset_manifest)

    readiness = subparsers.add_parser(
        "readiness-report",
        help="Summarize local pilot events for schema-eval and gold-dataset readiness",
    )
    readiness.add_argument("--limit", type=int, default=50)
    readiness.add_argument("--status", default="processed", help="Local event status to scan, or 'all'")
    readiness.add_argument(
        "--resolve-identity",
        action="store_true",
        help="Resolve identity against moai_web; default is local-only to avoid remote calls",
    )
    readiness.add_argument("--output", default="")
    readiness.set_defaults(func=cmd_readiness_report)

    pilot_fixture = subparsers.add_parser(
        "pilot-fixture",
        help="Run a synthetic non-PHI pilot flow in an isolated temporary local DB",
    )
    pilot_fixture.add_argument("--patient-name", default="FixturePatient")
    pilot_fixture.add_argument("--org-id", default="org-fixture")
    pilot_fixture.add_argument("--provider-person-id", default="provider-fixture")
    pilot_fixture.add_argument("--physio-client-id", default="client-fixture")
    pilot_fixture.add_argument("--subject-person-id", default="person-fixture-subject")
    pilot_fixture.add_argument("--encounter-id", default="enc-fixture")
    pilot_fixture.add_argument("--granted-by", default="fixture-therapist")
    pilot_fixture.add_argument(
        "--text",
        default="Synthetic non-PHI pilot note: standing balance practice, stable tolerance, no personal identifiers.",
    )
    pilot_fixture.add_argument("--session-type", default="balance_training")
    pilot_fixture.add_argument("--core-task", default="standing_balance")
    pilot_fixture.add_argument("--custom-task", default="")
    pilot_fixture.add_argument("--body-position", default="standing")
    pilot_fixture.add_argument("--assist-level", default="minimal_assist")
    pilot_fixture.add_argument("--performance", default="stable")
    pilot_fixture.add_argument("--review-status", default="reviewed")
    pilot_fixture.add_argument("--usable-for-training", action=argparse.BooleanOptionalAction, default=True)
    pilot_fixture.add_argument("--label-confidence", type=float, default=1.0)
    pilot_fixture.add_argument("--repetition-count", type=int, default=None)
    pilot_fixture.add_argument("--hold-duration-seconds", type=float, default=None)
    pilot_fixture.add_argument("--tolerance", default="fair")
    pilot_fixture.add_argument("--fatigue-level", default="mild")
    pilot_fixture.add_argument("--compensation", action="append", default=[])
    pilot_fixture.add_argument("--caregiver-present", action=argparse.BooleanOptionalAction, default=None)
    pilot_fixture.add_argument("--flag", action="append", default=["fatigue"])
    pilot_fixture.add_argument("--output", default="")
    pilot_fixture.set_defaults(func=cmd_pilot_fixture)

    return parser


def _add_common_identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--subject-person-id", default="", help="Canonical physio_app subject person id")
    parser.add_argument("--provider-person-id", default="", help="Canonical physio_app provider person id")
    parser.add_argument("--encounter-id", default="", help="Canonical physio_app encounter id")
    parser.add_argument("--capture-device", default="rayban")
    parser.add_argument("--no-resolve-identity", action="store_true", help="Skip moai_web identity lookup and use only local/provided IDs")


def _add_event_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("event_id")
    _add_common_identity_args(parser)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        _print_json({"status": "error", "message": str(exc), "command": args.command})
        return 1


if __name__ == "__main__":
    sys.exit(main())
