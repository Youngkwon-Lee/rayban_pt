"""CLI: import home-rehab-labeling pediatric_home_v1 clips into capture_events.

Usage (dry-run by default, matching the project's design/dry_run posture):

    .venv/bin/python import_pediatric_labels.py \
        --labels /path/to/rehab_home_labels.jsonl \
        --links /path/to/clip_links.json [--apply]

--labels: JSONL, one clip record per line (the labeling tool's export).
--links:  JSON object mapping clip_id -> {visit_session_id, source_event_id,
          encounter_id, organization_id, provider_person_id,
          subject_person_id, source_media_id} (all optional per clip, but a
          clip without a links entry is skipped and reported).
--apply:  actually write; without it the plan is printed and nothing changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bridge_core
from lib.label_import import clip_to_capture_event, import_label_clips


def _load_clips(path: Path) -> list[dict]:
    clips = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            clips.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSONL at {path}:{line_number}: {exc}")
    return clips


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--links", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="write to the bridge DB (default: dry-run)")
    args = parser.parse_args()

    clips = _load_clips(args.labels)
    links = json.loads(args.links.read_text(encoding="utf-8"))
    if not isinstance(links, dict):
        raise SystemExit("--links must be a JSON object keyed by clip_id")

    if not args.apply:
        planned, unlinked, invalid = 0, 0, 0
        for clip in clips:
            clip_id = str(clip.get("clip_id") or "").strip()
            link = links.get(clip_id)
            if not link:
                unlinked += 1
                continue
            row = clip_to_capture_event(clip, link)
            if row is None:
                invalid += 1
                continue
            planned += 1
            print(
                f"PLAN clip_id={clip_id} type={row['candidate_type']} "
                f"span_ms={row['start_ms']}..{row['end_ms']} confidence={row['confidence']}"
            )
        print(f"DRY-RUN: planned={planned} unlinked={unlinked} invalid={invalid} (use --apply to write)")
        return 0

    with bridge_core._conn() as conn:
        summary = import_label_clips(conn, clips, links)
        conn.commit()
    print(
        "APPLIED: created={created} skipped_existing={skipped_existing} "
        "skipped_unlinked={skipped_unlinked} skipped_invalid={skipped_invalid}".format(**summary)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
