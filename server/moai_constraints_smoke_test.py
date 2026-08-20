#!/usr/bin/env python3
"""Smoke test for physio_app/moai_web constraint-aware export planning."""

from __future__ import annotations

from copy import deepcopy

from lib.moai_mapper import build_moai_export_bundle
from lib.moai_writer import build_moai_write_plan


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _operation(plan: dict, table: str) -> dict:
    for op in plan["operations"]:
        if op["target_table"] == table:
            return op
    raise AssertionError(f"missing operation for {table}")


def main() -> None:
    event = {
        "id": "event-constraint-smoke",
        "event_type": "image",
        "created_at": "2026-06-22T09:00:00Z",
        "owner_org_id": "11111111-1111-4111-8111-111111111111",
        "owner_provider_person_id": "22222222-2222-4222-8222-222222222222",
        "subject_person_id": "33333333-3333-4333-8333-333333333333",
        "status": "processed",
    }
    label = {
        "session_type": "balance_training",
        "core_task": "standing_balance",
        "custom_task": "supported_kneeling",
        "body_position": "kneeling",
        "assist_level": "minimal_assist",
        "performance_level": "stable",
        "review_status": "reviewed",
        "usable_for_training": True,
        "label_confidence": 0.94,
        "flags": ["fatigue"],
        "notes": "constraint smoke label",
    }
    soap = {"s": "subjective", "o": "objective", "a": "assessment", "p": "plan"}
    review = {
        "reviewer": "22222222-2222-4222-8222-222222222222",
        "reviewed_at": "2026-06-22T09:10:00Z",
        "notes": "accepted",
    }

    bundle = build_moai_export_bundle(
        event=event,
        soap=soap,
        label=label,
        review=review,
        artifacts=[
            {
                "filename": "frame.jpg",
                "download_path": "/masked-files/frame.jpg",
                "content_type": "image/jpeg",
                "kind": "masked_image",
                "file_size_bytes": 2048,
            }
        ],
        subject_person_id="33333333-3333-4333-8333-333333333333",
        provider_person_id="22222222-2222-4222-8222-222222222222",
        encounter_id="44444444-4444-4444-8444-444444444444",
    )
    plan = build_moai_write_plan(bundle)
    require(plan["summary"]["skipped_count"] == 0, f"valid plan should not skip operations: {plan['skipped']}")

    require(_operation(plan, "encounters")["payload"]["status"] == "in-progress", "encounter status enum mismatch")
    require(_operation(plan, "encounters")["payload"]["care_setting"] == "home_visit", "care setting enum mismatch")
    require(_operation(plan, "encounter_media")["payload"]["media_type"] == "photo", "media type enum mismatch")
    require(_operation(plan, "encounter_media")["payload"]["analysis_status"] == "completed", "analysis status enum mismatch")
    require(_operation(plan, "encounter_notes")["payload"]["note_format"] == "soap", "note format enum mismatch")
    require(_operation(plan, "activity_sessions")["payload"]["source"] == "camera", "activity source enum mismatch")
    require(_operation(plan, "client_media_summaries")["payload"]["media_ref_type"] == "image_upload", "media ref type enum mismatch")
    require(_operation(plan, "clinical_extraction_reviews")["payload"]["source_table"] == "other", "review source table enum mismatch")

    broken = deepcopy(bundle)
    broken["encounter"]["payload"]["status"] = "draft"
    broken["notes"][0]["payload"]["note_format"] = "SOAP"
    broken_plan = build_moai_write_plan(broken)
    skipped_tables = {item["target_table"]: item for item in broken_plan["skipped"]}
    require(skipped_tables["encounters"]["reason"] == "constraint_violation", "invalid encounter should be blocked")
    require(skipped_tables["encounter_notes"]["reason"] == "constraint_violation", "invalid note should be blocked")
    require(broken_plan["summary"]["skipped_count"] == 2, "broken plan skipped count mismatch")

    print("OK: moai constraint smoke test passed")


if __name__ == "__main__":
    main()
