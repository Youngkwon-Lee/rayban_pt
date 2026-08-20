"""Import pediatric_home_v1 clip labels as human_label capture_events.

Implements the bridge-side half of docs/pediatric-label-schema-mapping.md
SSC.2: clip records from home-rehab-labeling are linked to rayban_pt via an
explicit link table (clip_id -> visit/session identifiers), evidence spans
are converted from float seconds to integer milliseconds, and rows are
inserted idempotently keyed by a deterministic extraction_key.

Human labels enter as status='edited' only. Approval stays a separate,
human-driven transition (PATCH /capture-events/{id}) so no import path can
mark anything approved.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

HUMAN_LABEL_SOURCE_TYPE = "human_label"
LABEL_SCHEMA_NAME = "pediatric_home_v1"

# Ordinal -> continuous mapping fixed by pediatric-label-schema-mapping.md SSB.6
# (interval midpoints; the intervals are low=[0,0.6), medium=[0.6,0.8),
# high=[0.8,1.0]).
LABEL_CONFIDENCE_TO_SCORE = {"low": 0.3, "medium": 0.7, "high": 0.9}


def human_label_extraction_key(
    source_event_id: Optional[str], clip_id: str, annotator: str
) -> str:
    raw = f"{source_event_id or ''}:human_label:{clip_id}:{annotator}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def span_bounds_ms(clip: dict) -> tuple[Optional[int], Optional[int]]:
    """Overall [start, end] in ms across evidence_spans (sec floats).

    Open-ended spans (end_sec null/empty) leave end_ms as None. Clip-level
    labels without spans return (None, None).
    """
    spans = clip.get("evidence_spans") or []
    starts: list[int] = []
    ends: list[int] = []
    open_ended = False
    for span in spans:
        start_sec = span.get("start_sec")
        if start_sec is not None and start_sec != "":
            starts.append(round(float(start_sec) * 1000))
        end_sec = span.get("end_sec")
        if end_sec is None or end_sec == "":
            open_ended = True
        else:
            ends.append(round(float(end_sec) * 1000))
    start_ms = min(starts) if starts else None
    end_ms = None if open_ended or not ends else max(ends)
    return start_ms, end_ms


def clip_to_capture_event(clip: dict, link: dict) -> Optional[dict]:
    """Build one capture_events row dict from a clip record and its link.

    Returns None for clips that cannot be imported (missing clip_id,
    clip_type, or annotator) so callers can report skips explicitly.
    """
    clip_id = str(clip.get("clip_id") or "").strip()
    clip_type = str(clip.get("clip_type") or "").strip()
    annotator = str(clip.get("annotator") or "").strip()
    if not clip_id or not clip_type or not annotator:
        return None

    source_event_id = (link.get("source_event_id") or "").strip() or None
    start_ms, end_ms = span_bounds_ms(clip)
    confidence = LABEL_CONFIDENCE_TO_SCORE.get(
        str(clip.get("label_confidence") or "").strip().lower()
    )

    payload = {key: value for key, value in clip.items() if value is not None}
    payload.update(
        {
            "label_schema": LABEL_SCHEMA_NAME,
            "schema_version": clip.get("schema_version") or "rehab-v1.0",
            "clip_id": clip_id,
            "extraction_key": human_label_extraction_key(
                source_event_id, clip_id, annotator
            ),
            "derived_from": "human_label",
        }
    )

    return {
        "visit_session_id": (link.get("visit_session_id") or "").strip() or None,
        "encounter_id": (link.get("encounter_id") or "").strip() or None,
        "organization_id": (link.get("organization_id") or "").strip() or None,
        "provider_person_id": (link.get("provider_person_id") or "").strip() or None,
        "subject_person_id": (link.get("subject_person_id") or "").strip() or None,
        "source_media_id": (link.get("source_media_id") or "").strip() or None,
        "source_event_id": source_event_id,
        "source_type": HUMAN_LABEL_SOURCE_TYPE,
        "event_type": clip_type,
        "candidate_type": clip_type,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "confidence": confidence,
        "status": "edited",
        "payload": payload,
    }


def import_label_clips(
    conn: sqlite3.Connection, clips: list[dict], links: dict[str, dict]
) -> dict:
    """Idempotently insert human_label capture_events. Returns a summary."""

    existing_keys = {
        row[0]
        for row in conn.execute(
            "SELECT json_extract(payload_json, '$.extraction_key') FROM capture_events "
            "WHERE source_type = ?",
            (HUMAN_LABEL_SOURCE_TYPE,),
        ).fetchall()
        if row[0]
    }

    created, skipped_existing, skipped_unlinked, skipped_invalid = 0, 0, 0, 0
    for clip in clips:
        clip_id = str(clip.get("clip_id") or "").strip()
        link = links.get(clip_id)
        if not link:
            skipped_unlinked += 1
            continue
        row = clip_to_capture_event(clip, link)
        if row is None:
            skipped_invalid += 1
            continue
        key = row["payload"]["extraction_key"]
        if key in existing_keys:
            skipped_existing += 1
            continue
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO capture_events (
              id, visit_session_id, encounter_id, organization_id, provider_person_id,
              subject_person_id, source_media_id, source_event_id, source_type, event_type,
              candidate_type, start_ms, end_ms, confidence, status, payload_json,
              reviewed_by, reviewed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                row["visit_session_id"],
                row["encounter_id"],
                row["organization_id"],
                row["provider_person_id"],
                row["subject_person_id"],
                row["source_media_id"],
                row["source_event_id"],
                row["source_type"],
                row["event_type"],
                row["candidate_type"],
                row["start_ms"],
                row["end_ms"],
                row["confidence"],
                row["status"],
                json.dumps(row["payload"], ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )
        existing_keys.add(key)
        created += 1

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_unlinked": skipped_unlinked,
        "skipped_invalid": skipped_invalid,
    }
