"""Generated chart text, clinician review state, and rehab label endpoints."""

import json
import uuid

from fastapi import APIRouter, HTTPException
from lib.auto_chart import save_chart
from pydantic import BaseModel, Field
from typing import Optional

import bridge_core as core
from bridge_core import (
    _audit_log,
    _build_pilot_manifest_for_event,
    _chart_quality,
    _conn,
    _enqueue_moai_sync_job,
    _error,
    _get_chart_review_by_event_id,
    _get_label_by_event_id,
    _parse_chart_sections,
)

router = APIRouter()


class ChartUpdatePayload(BaseModel):
    chart: str


class ChartReviewPayload(BaseModel):
    reviewer: Optional[str] = "therapist"
    notes: Optional[str] = ""


class RehabLabelPayload(BaseModel):
    provider_role: str = "unspecified"
    action_type: str = "observation"
    session_type: str
    core_task: str
    custom_task: str = ""
    body_position: str = ""
    assist_level: str
    performance: Optional[str] = None
    performance_level: Optional[str] = None
    review_status: str = "reviewed"
    reviewer_person_id: str = ""
    usable_for_training: bool = False
    label_confidence: Optional[float] = None
    repetition_count: Optional[int] = None
    hold_duration_seconds: Optional[float] = None
    tolerance: str = ""
    fatigue_level: str = ""
    compensations: list[str] = Field(default_factory=list)
    caregiver_present: Optional[bool] = None
    flags: list[str] = Field(default_factory=list)
    safety_flags: Optional[list[str]] = None
    notes: str = ""


LABEL_TAXONOMY_V0 = {
    "schema_version": "rayban_pt_label_taxonomy/v0",
    "provider_role": [
        {"value": "physical_therapist", "label": "Physical therapist"},
        {"value": "occupational_therapist", "label": "Occupational therapist"},
        {"value": "pilates_instructor", "label": "Pilates instructor"},
        {"value": "personal_trainer", "label": "Personal trainer"},
        {"value": "caregiver", "label": "Caregiver"},
        {"value": "unspecified", "label": "Unspecified"},
        {"value": "other", "label": "Other"},
    ],
    "action_type": [
        {"value": "observation", "label": "Observation"},
        {"value": "assessment", "label": "Assessment"},
        {"value": "instruction", "label": "Instruction"},
        {"value": "intervention", "label": "Intervention"},
        {"value": "reassessment", "label": "Reassessment"},
        {"value": "home_program", "label": "Home program"},
        {"value": "safety_check", "label": "Safety check"},
    ],
    "assessment_name": [
        {"value": "pain_assessment", "label": "Pain assessment"},
        {"value": "range_of_motion", "label": "Range of motion"},
        {"value": "manual_muscle_test", "label": "Manual muscle test"},
        {"value": "gait_assessment", "label": "Gait assessment"},
        {"value": "balance_assessment", "label": "Balance assessment"},
        {"value": "movement_screen", "label": "Movement screen"},
        {"value": "posture_assessment", "label": "Posture assessment"},
        {"value": "breathing_assessment", "label": "Breathing assessment"},
        {"value": "strength_assessment", "label": "Strength assessment"},
        {"value": "endurance_assessment", "label": "Endurance assessment"},
        {"value": "special_test", "label": "Special test"},
    ],
    "activity_name": [
        {"value": "sit_to_stand", "label": "Sit to stand"},
        {"value": "bridge", "label": "Bridge"},
        {"value": "dead_bug", "label": "Dead bug"},
        {"value": "bird_dog", "label": "Bird dog"},
        {"value": "plank", "label": "Plank"},
        {"value": "side_plank", "label": "Side plank"},
        {"value": "push_up", "label": "Push-up"},
        {"value": "deadlift", "label": "Deadlift"},
        {"value": "row", "label": "Row"},
        {"value": "overhead_press", "label": "Overhead press"},
        {"value": "squat", "label": "Squat"},
        {"value": "lunge", "label": "Lunge"},
        {"value": "step_up", "label": "Step-up"},
        {"value": "clamshell", "label": "Clamshell"},
        {"value": "heel_raise", "label": "Heel raise"},
        {"value": "shoulder_flexion", "label": "Shoulder flexion"},
        {"value": "pelvic_tilt", "label": "Pelvic tilt"},
        {"value": "cat_cow", "label": "Cat-cow"},
        {"value": "roll_down", "label": "Roll down"},
        {"value": "hundred", "label": "The hundred"},
        {"value": "teaser", "label": "Teaser"},
        {"value": "reformer_footwork", "label": "Reformer footwork"},
        {"value": "single_leg_stance", "label": "Single-leg stance"},
        {"value": "tandem_stance", "label": "Tandem stance"},
        {"value": "breathing", "label": "Breathing"},
        {"value": "gait_training", "label": "Gait training"},
        {"value": "balance_training", "label": "Balance training"},
    ],
    "intervention_type": [
        {"value": "movement_correction", "label": "Movement correction"},
        {"value": "joint_mobilization", "label": "Joint mobilization"},
        {"value": "manual_therapy", "label": "Manual therapy"},
        {"value": "massage", "label": "Massage"},
        {"value": "soft_tissue_mobilization", "label": "Soft-tissue mobilization"},
        {"value": "stretching", "label": "Stretching"},
        {"value": "relaxation", "label": "Relaxation"},
        {"value": "neuromuscular_reeducation", "label": "Neuromuscular re-education"},
        {"value": "therapeutic_exercise", "label": "Therapeutic exercise"},
        {"value": "breathing_training", "label": "Breathing training"},
        {"value": "pilates_mat", "label": "Pilates mat"},
        {"value": "pilates_reformer", "label": "Pilates reformer"},
        {"value": "resistance_training", "label": "Resistance training"},
        {"value": "gait_training", "label": "Gait training"},
        {"value": "balance_training", "label": "Balance training"},
        {"value": "taping", "label": "Taping"},
        {"value": "cueing", "label": "Cueing/facilitation"},
        {"value": "orthosis_assistive_device", "label": "Orthosis/assistive device"},
        {"value": "other_intervention", "label": "Other intervention"},
    ],
    "session_type": [
        {"value": "assessment", "label": "Assessment"},
        {"value": "therapeutic_exercise", "label": "Therapeutic exercise"},
        {"value": "neuromotor_training", "label": "Neuromotor training"},
        {"value": "gait_training", "label": "Gait training"},
        {"value": "balance_training", "label": "Balance training"},
        {"value": "caregiver_training", "label": "Caregiver training"},
        {"value": "home_exercise_review", "label": "Home exercise review"},
        {"value": "other", "label": "Other"},
    ],
    "core_task": [
        {"value": "prone_head_control", "label": "Prone head control"},
        {"value": "sitting_balance", "label": "Sitting balance"},
        {"value": "standing_balance", "label": "Standing balance"},
        {"value": "gait_practice", "label": "Gait practice"},
        {"value": "sit_to_stand", "label": "Sit to stand"},
        {"value": "reaching", "label": "Reaching"},
        {"value": "balance_test", "label": "Balance test"},
        {"value": "strength_test", "label": "Strength test"},
        {"value": "movement_screen", "label": "Movement screen"},
        {"value": "breathing_control", "label": "Breathing control"},
        {"value": "strength_training", "label": "Strength training"},
        {"value": "motor_control", "label": "Motor control"},
        {"value": "pilates_control", "label": "Pilates control"},
        {"value": "range_of_motion", "label": "Range of motion"},
        {"value": "caregiver_handling", "label": "Caregiver handling"},
        {"value": "positioning", "label": "Positioning"},
        {"value": "other", "label": "Other"},
    ],
    "body_position": [
        {"value": "supine", "label": "Supine"},
        {"value": "prone", "label": "Prone"},
        {"value": "side_lying", "label": "Side lying"},
        {"value": "sitting", "label": "Sitting"},
        {"value": "quadruped", "label": "Quadruped"},
        {"value": "kneeling", "label": "Kneeling"},
        {"value": "standing", "label": "Standing"},
        {"value": "walking", "label": "Walking"},
        {"value": "unknown", "label": "Unknown"},
    ],
    "assist_level": [
        {"value": "independent", "label": "Independent"},
        {"value": "supervision", "label": "Supervision"},
        {"value": "standby_assist", "label": "Standby assist"},
        {"value": "contact_guard", "label": "Contact guard"},
        {"value": "minimal_assist", "label": "Minimal assist"},
        {"value": "moderate_assist", "label": "Moderate assist"},
        {"value": "maximal_assist", "label": "Maximal assist"},
        {"value": "dependent", "label": "Dependent"},
        {"value": "not_tested", "label": "Not tested"},
    ],
    "performance": [
        {"value": "improved", "label": "Improved"},
        {"value": "stable", "label": "Stable"},
        {"value": "declined", "label": "Declined"},
        {"value": "variable", "label": "Variable"},
        {"value": "unable", "label": "Unable"},
        {"value": "not_observed", "label": "Not observed"},
    ],
    "review_status": [
        {"value": "unreviewed", "label": "Unreviewed"},
        {"value": "reviewed", "label": "Reviewed"},
        {"value": "corrected", "label": "Corrected"},
        {"value": "approved", "label": "Approved"},
        {"value": "rejected", "label": "Rejected"},
    ],
    "tolerance": [
        {"value": "good", "label": "Good"},
        {"value": "fair", "label": "Fair"},
        {"value": "poor", "label": "Poor"},
        {"value": "tolerated", "label": "Tolerated"},
        {"value": "not_observed", "label": "Not observed"},
    ],
    "fatigue_level": [
        {"value": "none", "label": "None"},
        {"value": "mild", "label": "Mild"},
        {"value": "moderate", "label": "Moderate"},
        {"value": "severe", "label": "Severe"},
        {"value": "uncertain", "label": "Uncertain"},
    ],
    "compensations": [
        "right_weight_shift",
        "left_weight_shift",
        "trunk_lateral_flexion",
        "excessive_extension",
        "excessive_flexion",
        "shoulder_elevation",
        "pelvic_rotation",
        "knee_valgus",
        "pelvic_drop",
        "forward_trunk_lean",
        "lumbar_extension",
        "breath_holding",
        "scapular_winging",
        "foot_pronation",
        "cervical_extension",
    ],
    "flags": [
        "fatigue",
        "postural_sway",
        "pain",
        "caregiver_assist",
        "safety_risk",
        "low_attention",
        "equipment_used",
        "needs_review",
    ],
    "presets": [
        {
            "name": "Head Control",
            "session_type": "neuromotor_training",
            "core_task": "prone_head_control",
            "custom_task": "",
            "body_position": "prone",
            "assist_level": "minimal_assist",
            "performance_level": "stable",
            "review_status": "reviewed",
            "flags": ["fatigue"],
            "notes": "Head/trunk control task reviewed by therapist.",
        },
        {
            "name": "Sitting Balance",
            "session_type": "balance_training",
            "core_task": "sitting_balance",
            "custom_task": "",
            "body_position": "sitting",
            "assist_level": "contact_guard",
            "performance_level": "variable",
            "review_status": "reviewed",
            "flags": ["postural_sway"],
            "notes": "Sitting balance quality and safety reviewed.",
        },
        {
            "name": "Gait Practice",
            "session_type": "gait_training",
            "core_task": "gait_practice",
            "custom_task": "",
            "body_position": "walking",
            "assist_level": "moderate_assist",
            "performance_level": "stable",
            "review_status": "reviewed",
            "flags": ["caregiver_assist"],
            "notes": "Gait practice reviewed; assist level confirmed.",
        },
        {
            "name": "Supported Kneeling",
            "session_type": "neuromotor_training",
            "core_task": "other",
            "custom_task": "supported_kneeling",
            "body_position": "kneeling",
            "assist_level": "minimal_assist",
            "performance_level": "stable",
            "review_status": "reviewed",
            "flags": ["caregiver_assist", "needs_review"],
            "notes": "Custom task candidate kept outside core_task until taxonomy review.",
        },
    ],
}


def _label_performance_value(payload: RehabLabelPayload) -> str:
    performance = (payload.performance_level or payload.performance or "").strip()
    if not performance:
        _error(422, "LABEL_PERFORMANCE_REQUIRED", "performance 또는 performance_level이 필요합니다.")
    return performance


@router.get("/charts/{event_id}")
def get_chart(event_id: str):
    """생성된 11.txt 차트 내용 반환."""
    chart_path = core.CHART_DIR / f"{event_id}_11.txt"
    if not chart_path.exists():
        raise HTTPException(status_code=404, detail="차트 없음")
    _audit_log(event_id, "info", "chart viewed")
    chart = chart_path.read_text(encoding="utf-8")
    with _conn() as conn:
        review = _get_chart_review_by_event_id(conn, event_id)
    return {"event_id": event_id, "chart": chart, "quality": _chart_quality(chart), "review": review}


@router.put("/charts/{event_id}")
def update_chart(event_id: str, payload: ChartUpdatePayload):
    """치료사가 검수한 차트 본문을 저장하고 SOAP 요약을 동기화."""
    chart = (payload.chart or "").strip()
    if len(chart) < 20:
        _error(400, "CHART_TOO_SHORT", "저장할 차트 본문이 너무 짧습니다.")
    if len(chart) > 20000:
        _error(413, "CHART_TOO_LARGE", "저장할 차트 본문이 너무 깁니다.")

    sections = _parse_chart_sections(chart)
    s_val = sections.get("S>", "")
    o_val = sections.get("O>", "")
    a_val = sections.get("A>", "")
    p_val = sections.get("PTx.>", "")
    chart_path = core.CHART_DIR / f"{event_id}_11.txt"

    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        save_chart(chart_path, chart + "\n")
        conn.execute("DELETE FROM chart_reviews WHERE event_id = ?", (event_id,))

        row = conn.execute(
            "SELECT id FROM soap_notes WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE soap_notes SET s = ?, o = ?, a = ?, p = ? WHERE id = ?",
                (s_val, o_val, a_val, p_val, row[0]),
            )
        else:
            conn.execute(
                "INSERT INTO soap_notes (id, event_id, s, o, a, p) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), event_id, s_val, o_val, a_val, p_val),
            )

        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, "info", "chart updated manually"),
        )
        _enqueue_moai_sync_job(conn, event_id, "chart_updated")
        conn.commit()

    saved = chart_path.read_text(encoding="utf-8")
    return {"ok": True, "event_id": event_id, "chart": saved, "quality": _chart_quality(saved), "review": None}


@router.post("/charts/{event_id}/review")
def mark_chart_reviewed(event_id: str, payload: ChartReviewPayload):
    """치료사가 차트 초안을 검수 완료로 표시."""
    chart_path = core.CHART_DIR / f"{event_id}_11.txt"
    if not chart_path.exists():
        raise HTTPException(status_code=404, detail="차트 없음")

    chart = chart_path.read_text(encoding="utf-8")
    quality = _chart_quality(chart)
    reviewer = (payload.reviewer or "therapist").strip() or "therapist"
    notes = (payload.notes or "").strip()

    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        conn.execute(
            """
            INSERT INTO chart_reviews (event_id, reviewer, notes, quality_score, quality_level, reviewed_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET
              reviewer=excluded.reviewer,
              notes=excluded.notes,
              quality_score=excluded.quality_score,
              quality_level=excluded.quality_level,
              reviewed_at=CURRENT_TIMESTAMP
            """,
            (event_id, reviewer, notes, int(quality.get("score") or 0), str(quality.get("level") or "")),
        )
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, "info", f"chart reviewed reviewer={reviewer} quality={quality.get('level')} score={quality.get('score')}"),
        )
        _enqueue_moai_sync_job(conn, event_id, "chart_reviewed")
        conn.commit()
        review = _get_chart_review_by_event_id(conn, event_id)

    return {"ok": True, "event_id": event_id, "quality": quality, "review": review}


@router.delete("/charts/{event_id}/review")
def clear_chart_review(event_id: str):
    """차트 검수 완료 표시를 해제."""
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        deleted = conn.execute("DELETE FROM chart_reviews WHERE event_id = ?", (event_id,)).rowcount
        conn.execute(
            "INSERT INTO audit_logs (id, event_id, level, message) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_id, "info", f"chart review cleared deleted={deleted}"),
        )
        _enqueue_moai_sync_job(conn, event_id, "chart_review_cleared")
        conn.commit()

    quality = None
    chart_path = core.CHART_DIR / f"{event_id}_11.txt"
    if chart_path.exists():
        quality = _chart_quality(chart_path.read_text(encoding="utf-8"))
    return {"ok": True, "event_id": event_id, "quality": quality, "review": None}


@router.get("/chart-review")
def chart_review(limit: int = 50, include_good: bool = False, event_type: str = "combined"):
    """차트 품질 검수가 필요한 최근 기록 목록."""
    n = max(1, min(limit, 100))
    clean_event_type = event_type.strip().lower()
    if clean_event_type not in {"combined", "all"}:
        _error(400, "INVALID_EVENT_TYPE_FILTER", "event_type은 combined 또는 all이어야 합니다.")

    where_sql = "" if clean_event_type == "all" else "WHERE event_type = ?"
    params: list[object] = []
    if clean_event_type != "all":
        params.append(clean_event_type)
    params.append(n)

    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, source, event_type, intent, status, created_at, patient_name
            FROM events
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        items = []
        for r in rows:
            chart_path = core.CHART_DIR / f"{r[0]}_11.txt"
            if not chart_path.exists():
                continue

            chart = chart_path.read_text(encoding="utf-8")
            quality = _chart_quality(chart)
            review = _get_chart_review_by_event_id(conn, r[0])
            if review and not include_good:
                continue
            if not include_good and quality.get("level") == "good":
                continue

            label = _get_label_by_event_id(conn, r[0])
            first_issue = (quality.get("issues") or [{}])[0]
            items.append(
                {
                    "event_id": r[0],
                    "source": r[1],
                    "event_type": r[2],
                    "intent": r[3],
                    "status": r[4],
                    "created_at": r[5],
                    "patient_name": r[6] or None,
                    "has_label": label is not None,
                    "quality": quality,
                    "review": review,
                    "primary_issue": first_issue.get("message") or "",
                }
            )

    return {"items": items}


@router.get("/label-taxonomy")
def get_label_taxonomy():
    return {"status": "done", "taxonomy": LABEL_TAXONOMY_V0}


@router.post("/labels/{event_id}")
def upsert_label(event_id: str, payload: RehabLabelPayload):
    performance = _label_performance_value(payload)
    safety_flags = payload.safety_flags if payload.safety_flags is not None else payload.flags
    label_confidence = payload.label_confidence
    if label_confidence is not None and not (0 <= label_confidence <= 1):
        _error(422, "INVALID_LABEL_CONFIDENCE", "label_confidence는 0과 1 사이여야 합니다.")
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")

        conn.execute(
            """
            INSERT INTO rehab_labels (
              event_id, provider_role, action_type, session_type, core_task, custom_task, body_position,
              assist_level, performance, review_status, reviewer_person_id,
              usable_for_training, label_confidence, repetition_count,
              hold_duration_seconds, tolerance, fatigue_level, compensations,
              caregiver_present, flags, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET
              provider_role=excluded.provider_role,
              action_type=excluded.action_type,
              session_type=excluded.session_type,
              core_task=excluded.core_task,
              custom_task=excluded.custom_task,
              body_position=excluded.body_position,
              assist_level=excluded.assist_level,
              performance=excluded.performance,
              review_status=excluded.review_status,
              reviewer_person_id=excluded.reviewer_person_id,
              usable_for_training=excluded.usable_for_training,
              label_confidence=excluded.label_confidence,
              repetition_count=excluded.repetition_count,
              hold_duration_seconds=excluded.hold_duration_seconds,
              tolerance=excluded.tolerance,
              fatigue_level=excluded.fatigue_level,
              compensations=excluded.compensations,
              caregiver_present=excluded.caregiver_present,
              flags=excluded.flags,
              notes=excluded.notes,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                event_id,
                payload.provider_role.strip() or "unspecified",
                payload.action_type.strip() or "observation",
                payload.session_type,
                payload.core_task,
                payload.custom_task.strip(),
                payload.body_position.strip(),
                payload.assist_level,
                performance,
                payload.review_status.strip() or "reviewed",
                payload.reviewer_person_id.strip(),
                1 if payload.usable_for_training else 0,
                label_confidence,
                payload.repetition_count,
                payload.hold_duration_seconds,
                payload.tolerance.strip(),
                payload.fatigue_level.strip(),
                json.dumps(payload.compensations, ensure_ascii=False),
                None if payload.caregiver_present is None else (1 if payload.caregiver_present else 0),
                json.dumps(safety_flags, ensure_ascii=False),
                payload.notes,
            ),
        )
        _enqueue_moai_sync_job(conn, event_id, "label_upserted")
        conn.commit()
        label = _get_label_by_event_id(conn, event_id)

    manifest = _build_pilot_manifest_for_event(event_id, resolve_identity=False)
    return {"ok": True, "label": label, "readiness": manifest["readiness"]}


@router.get("/labels/{event_id}")
def get_label(event_id: str):
    with _conn() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="event not found")
        label = _get_label_by_event_id(conn, event_id)
    return {"event_id": event_id, "label": label}
