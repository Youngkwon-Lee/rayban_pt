"""Review-first pose evidence extraction for Ray-Ban video captures.

This module intentionally produces measurements and capture evidence, not a
diagnosis or an automatic clinical conclusion.  The output mirrors the
physio_app ``video_pose_summary`` contract closely enough for the bridge to
share the same downstream vocabulary while keeping the bridge independent.

MediaPipe's model asset is cached outside the repository.  Set
``RAYBAN_POSE_MODEL_PATH`` to an organization-managed local asset when the
bridge runs in a restricted environment.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .transcript_capture import SEMANTIC_EXTRACTOR_VERSION, capture_action_type

logger = logging.getLogger(__name__)

POSE_EXTRACTOR_VERSION = "mediapipe_pose_rules_v1"
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/1/pose_landmarker_full.task"
)

# MediaPipe Pose Landmarker indices.  These are the same 33-point indices used
# by physio_app/src/features/encounter-room/utils/pose-angle-calculator.ts.
LANDMARK = {
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_ear": 7,
    "right_ear": 8,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
    "left_foot_index": 31,
    "right_foot_index": 32,
}

# Keep this registry intentionally limited to the angles already supported by
# the physio_app capture contract.  A single-camera estimate is a screening
# measurement and must remain provider-reviewed.
METRICS: dict[str, dict[str, Any]] = {
    "MET.ROM.SHOULDER.FLEX": {
        "label": "Shoulder Flexion",
        "normal": (0.0, 180.0),
        "triple": ("shoulder", "hip", "elbow"),
    },
    "MET.ROM.SHOULDER.ABD": {
        "label": "Shoulder Abduction",
        "normal": (0.0, 180.0),
        "triple": ("opposite_shoulder", "shoulder", "elbow"),
    },
    "MET.ROM.SHOULDER.EXT": {
        "label": "Shoulder Extension proxy",
        "normal": (0.0, 60.0),
        "triple": ("shoulder", "elbow", "wrist"),
    },
    "MET.ROM.HIP.FLEX": {
        "label": "Hip Flexion",
        "normal": (0.0, 120.0),
        "triple": ("shoulder", "hip", "knee"),
    },
    "MET.ROM.HIP.ABD": {
        "label": "Hip Abduction",
        "normal": (0.0, 45.0),
        "triple": ("opposite_hip", "hip", "knee"),
    },
    "MET.ROM.KNEE.FLEX": {
        "label": "Knee Flexion",
        "normal": (0.0, 135.0),
        "triple": ("hip", "knee", "ankle"),
    },
    "MET.ROM.ANKLE.DORSI": {
        "label": "Ankle Dorsiflexion proxy",
        "normal": (0.0, 20.0),
        "triple": ("knee", "ankle", "foot_index"),
    },
    "MET.ROM.TRUNK.FLEX": {
        "label": "Trunk Flexion proxy",
        "normal": (0.0, 120.0),
        "triple": ("ear", "hip", "knee"),
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(value: float) -> float:
    return round(float(value), 1)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _default_model_path() -> Path:
    configured = os.getenv("RAYBAN_POSE_MODEL_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    cache_root = os.getenv("RAYBAN_POSE_MODEL_DIR", "").strip()
    if cache_root:
        return Path(cache_root).expanduser() / "pose_landmarker_full.task"
    return Path.home() / ".cache" / "rayban_pt" / "pose_landmarker_full.task"


def _ensure_model_path() -> Optional[Path]:
    configured = _default_model_path()
    if configured.is_file() and configured.stat().st_size > 0:
        return configured

    if os.getenv("RAYBAN_POSE_AUTO_DOWNLOAD", "true").lower() in {"0", "false", "no"}:
        return None

    try:
        configured.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix="pose_landmarker_", suffix=".part", dir=str(configured.parent)
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        try:
            with urllib.request.urlopen(POSE_MODEL_URL, timeout=45) as response, temporary_path.open("wb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
            if temporary_path.stat().st_size < 100_000:
                raise RuntimeError("downloaded pose model is unexpectedly small")
            os.replace(temporary_path, configured)
            return configured
        finally:
            temporary_path.unlink(missing_ok=True)
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        logger.warning("pose model unavailable: %s", exc)
        return None


def _angle(first: dict[str, float], vertex: dict[str, float], second: dict[str, float]) -> Optional[float]:
    v1x = first["x"] - vertex["x"]
    v1y = first["y"] - vertex["y"]
    v2x = second["x"] - vertex["x"]
    v2y = second["y"] - vertex["y"]
    magnitude_1 = math.hypot(v1x, v1y)
    magnitude_2 = math.hypot(v2x, v2y)
    if magnitude_1 == 0 or magnitude_2 == 0:
        return None
    cosine = _clamp((v1x * v2x + v1y * v2y) / (magnitude_1 * magnitude_2), -1.0, 1.0)
    return _round(math.degrees(math.acos(cosine)))


def _point(landmarks: list[Any], name: str) -> Optional[dict[str, float]]:
    index = LANDMARK.get(name)
    if index is None or index >= len(landmarks):
        return None
    landmark = landmarks[index]
    visibility = float(getattr(landmark, "visibility", 1.0) or 0.0)
    presence = float(getattr(landmark, "presence", 1.0) or 0.0)
    if min(visibility, presence) < 0.5:
        return None
    return {"x": float(landmark.x), "y": float(landmark.y)}


def _resolve_triple(landmarks: list[Any], side: str, triple: tuple[str, str, str]) -> Optional[tuple[dict[str, float], ...]]:
    resolved: list[dict[str, float]] = []
    for name in triple:
        if name == "opposite_shoulder":
            resolved_name = "right_shoulder" if side == "left" else "left_shoulder"
        elif name == "opposite_hip":
            resolved_name = "right_hip" if side == "left" else "left_hip"
        else:
            resolved_name = f"{side}_{name}"
        point = _point(landmarks, resolved_name)
        if point is None:
            return None
        resolved.append(point)
    return tuple(resolved)


def _measure_landmarks(landmarks: list[Any]) -> list[dict[str, Any]]:
    measurements: list[dict[str, Any]] = []
    for metric_id, definition in METRICS.items():
        for side in ("left", "right"):
            triple = _resolve_triple(landmarks, side, definition["triple"])
            if triple is None:
                continue
            value = _angle(*triple)
            if value is None:
                continue
            normal_min, normal_max = definition["normal"]
            span = normal_max - normal_min
            measurements.append(
                {
                    "angle": value,
                    "is_abnormal": bool(value < normal_min or value > normal_max),
                    "metric_id": metric_id,
                    "metric_label": definition["label"],
                    "percent_of_normal": _round(((value - normal_min) / span) * 100) if span else 100.0,
                    "side": side,
                }
            )
    return measurements


def _count_repetitions(angles: list[float], minimum_excursion: float = 15.0) -> int:
    """Count conservative full cycles in a one-dimensional angle series."""

    if len(angles) < 3:
        return 0
    direction = 0
    turns = 0
    anchor = angles[0]
    for value in angles[1:]:
        delta = value - anchor
        if direction == 0:
            if abs(delta) >= minimum_excursion:
                direction = 1 if delta > 0 else -1
                anchor = value
            continue
        next_direction = 1 if delta > 0 else -1 if delta < 0 else direction
        if next_direction != direction and abs(delta) >= minimum_excursion:
            turns += 1
            direction = next_direction
            anchor = value
    return turns // 2


def summarize_pose_samples(samples: list[dict[str, Any]], duration_sec: float) -> Optional[dict[str, Any]]:
    """Aggregate frame measurements into the physio_app video pose contract."""

    if not samples:
        return None

    measurements_by_key: dict[str, dict[str, Any]] = {}
    asymmetries_by_metric: dict[str, dict[str, Any]] = {}
    for sample in samples:
        per_metric: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for measurement in sample.get("measurements", []):
            key = f"{measurement['metric_id']}:{measurement['side']}"
            current = measurements_by_key.setdefault(
                key,
                {"angles": [], "metric_id": measurement["metric_id"], "metric_label": measurement["metric_label"], "side": measurement["side"]},
            )
            current["angles"].append(float(measurement["angle"]))
            per_metric[measurement["metric_id"]][measurement["side"]] = measurement

        for metric_id, sides in per_metric.items():
            left = sides.get("left")
            right = sides.get("right")
            if left and right:
                current = asymmetries_by_metric.setdefault(
                    metric_id,
                    {"diffs": [], "metric_id": metric_id, "metric_label": left["metric_label"]},
                )
                diff = abs(float(left["angle"]) - float(right["angle"]))
                if diff >= 10:
                    current["diffs"].append(diff)

    measurement_entries = []
    for current in measurements_by_key.values():
        angles = current["angles"]
        if not angles:
            continue
        measurement_entries.append(
            {
                "max_angle": _round(max(angles)),
                "mean_angle": _round(sum(angles) / len(angles)),
                "metric_id": current["metric_id"],
                "metric_label": current["metric_label"],
                "min_angle": _round(min(angles)),
                "sample_count": len(angles),
                "side": current["side"],
            }
        )
    measurement_entries.sort(key=lambda entry: (entry["metric_label"], entry["side"]))

    asymmetry_entries = []
    for current in asymmetries_by_metric.values():
        if not current["diffs"]:
            continue
        asymmetry_entries.append(
            {
                "max_diff": _round(max(current["diffs"])),
                "metric_id": current["metric_id"],
                "metric_label": current["metric_label"],
                "sample_count": len(current["diffs"]),
            }
        )
    asymmetry_entries.sort(key=lambda entry: entry["max_diff"], reverse=True)

    repetition_entries = []
    for current in measurements_by_key.values():
        count = _count_repetitions(current["angles"])
        if count <= 0:
            continue
        repetition_entries.append(
            {
                "metric_id": current["metric_id"],
                "metric_label": current["metric_label"],
                "repetition_count": count,
                "sample_count": len(current["angles"]),
                "side": current["side"],
            }
        )

    movement_labels = [
        f"{'L' if entry['side'] == 'left' else 'R'} {entry['metric_label']} {entry['min_angle']}-{entry['max_angle']} deg"
        for entry in sorted(
            measurement_entries,
            key=lambda entry: entry["max_angle"] - entry["min_angle"],
            reverse=True,
        )[:6]
    ]
    timestamps = [int(sample.get("timestamp_ms", 0)) for sample in samples]
    return {
        "asymmetry_entries": asymmetry_entries,
        "asymmetry_labels": [
            f"{entry['metric_label']} max L/R difference {entry['max_diff']} deg"
            for entry in asymmetry_entries[:6]
        ],
        "capture_duration_seconds": max(0.0, float(duration_sec)),
        "measured_at_end": samples[-1].get("measured_at") or _now_iso(),
        "measured_at_start": samples[0].get("measured_at") or _now_iso(),
        "measurement_entries": measurement_entries,
        "movement_labels": movement_labels,
        "repetition_entries": repetition_entries,
        "processing_mode": "on_device_mediapipe",
        "review_status": "provider_review_required",
        "sample_count": len(samples),
        "frame_timestamps_ms": timestamps,
        "detected_frame_count": len(samples),
        "extractor_version": POSE_EXTRACTOR_VERSION,
    }


def build_pose_capture_candidates(summary: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map pose summary data to review-first capture event candidates."""

    if not summary or not summary.get("measurement_entries"):
        return []

    encoded_summary = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
    candidates: list[dict[str, Any]] = []
    candidates.append(
        {
            "event_type": "pose_quality",
            "candidate_type": "video_evidence",
            "confidence": 0.75,
            "payload": {
                "label": f"MediaPipe pose: {summary['sample_count']}개 프레임 측정",
                "pose_summary": encoded_summary,
                "action_type": "observation",
                "semantic": {
                    "version": SEMANTIC_EXTRACTOR_VERSION,
                    "domain": "observation",
                },
                "extractor_version": POSE_EXTRACTOR_VERSION,
                "review_required": "true",
            },
        }
    )
    for entry in summary["measurement_entries"]:
        side_label = "L" if entry["side"] == "left" else "R"
        candidates.append(
            {
                "event_type": "pose_measurement",
                "candidate_type": "rom_measurement",
                "confidence": 0.65,
                "payload": {
                    "label": f"{side_label} {entry['metric_label']} {entry['min_angle']}-{entry['max_angle']}°",
                    "metric_id": entry["metric_id"],
                    "metric_label": entry["metric_label"],
                    "action_type": capture_action_type("rom_measurement"),
                    "semantic": {
                        "version": SEMANTIC_EXTRACTOR_VERSION,
                        "domain": "assessment",
                        "assessment_type": "range_of_motion",
                        "core_task": "range_of_motion",
                    },
                    "side": entry["side"],
                    "min_angle": str(entry["min_angle"]),
                    "max_angle": str(entry["max_angle"]),
                    "mean_angle": str(entry["mean_angle"]),
                    "sample_count": str(entry["sample_count"]),
                    "unit": "deg",
                    "processing_mode": "on_device_mediapipe",
                    "review_required": "true",
                },
            }
        )
    for entry in summary.get("asymmetry_entries", [])[:6]:
        candidates.append(
            {
                "event_type": "pose_asymmetry",
                "candidate_type": "positioning_alignment",
                "confidence": 0.55,
                "payload": {
                    "label": f"{entry['metric_label']} 좌우 차이 {entry['max_diff']}°",
                    "metric_id": entry["metric_id"],
                    "metric_label": entry["metric_label"],
                    "action_type": capture_action_type("positioning_alignment"),
                    "semantic": {
                        "version": SEMANTIC_EXTRACTOR_VERSION,
                        "domain": "assessment",
                        "assessment_type": "positioning_alignment",
                        "core_task": "positioning",
                    },
                    "left_right_difference": str(entry["max_diff"]),
                    "sample_count": str(entry["sample_count"]),
                    "review_required": "true",
                },
            }
        )
    if summary.get("movement_labels"):
        candidates.append(
            {
                "event_type": "movement_range",
                "candidate_type": "video_evidence",
                "confidence": 0.6,
                "payload": {
                    "label": " · ".join(summary["movement_labels"]),
                    "movement_labels": json.dumps(summary["movement_labels"], ensure_ascii=False),
                    "pose_summary": encoded_summary,
                    "action_type": "observation",
                    "semantic": {
                        "version": SEMANTIC_EXTRACTOR_VERSION,
                        "domain": "observation",
                    },
                    "review_required": "true",
                },
            }
        )
    for entry in summary.get("repetition_entries", [])[:6]:
        side_label = "L" if entry["side"] == "left" else "R"
        candidates.append(
            {
                "event_type": "repetition_observation",
                "candidate_type": "functional_task",
                "confidence": 0.5,
                "payload": {
                    "label": f"{side_label} {entry['metric_label']} 반복 관찰 {entry['repetition_count']}회",
                    "metric_id": entry["metric_id"],
                    "metric_label": entry["metric_label"],
                    "side": entry["side"],
                    "action_type": "observation",
                    "semantic": {
                        "version": SEMANTIC_EXTRACTOR_VERSION,
                        "domain": "observation",
                        "core_task": "functional_task",
                        "repetition_count": entry["repetition_count"],
                    },
                    "repetition_count": str(entry["repetition_count"]),
                    "sample_count": str(entry["sample_count"]),
                    "processing_mode": "on_device_mediapipe",
                    "review_required": "true",
                },
            }
        )
    return candidates


def analyze_pose_frames(
    frame_paths: Iterable[Path],
    *,
    frame_interval_ms: int = 1_000,
    duration_sec: Optional[float] = None,
) -> dict[str, Any]:
    """Run local MediaPipe inference over sampled frame files.

    A missing model or optional runtime dependency returns a non-fatal
    ``unavailable`` result so video upload and transcript capture still work.
    """

    paths = list(frame_paths)
    model_path = _ensure_model_path()
    if model_path is None:
        return {"status": "unavailable", "reason": "pose_model_unavailable", "summary": None, "candidates": []}

    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
        from mediapipe.tasks import python as mp_python  # type: ignore
        from mediapipe.tasks.python import vision  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("pose runtime unavailable: %s", exc)
        return {"status": "unavailable", "reason": "pose_runtime_unavailable", "summary": None, "candidates": []}

    try:
        options = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        landmarker = vision.PoseLandmarker.create_from_options(options)
    except Exception as exc:  # pragma: no cover - model/runtime dependent
        logger.warning("pose landmarker initialization failed: %s", exc)
        return {"status": "unavailable", "reason": "pose_landmarker_init_failed", "summary": None, "candidates": []}

    samples: list[dict[str, Any]] = []
    try:
        for index, frame_path in enumerate(paths):
            frame = cv2.imread(str(frame_path))
            if frame is None:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            if not result.pose_landmarks:
                continue
            landmarks = result.pose_landmarks[0]
            measurements = _measure_landmarks(landmarks)
            if not measurements:
                continue
            visible = [float(getattr(item, "visibility", 0.0) or 0.0) for item in landmarks]
            samples.append(
                {
                    "timestamp_ms": index * frame_interval_ms,
                    "measured_at": _now_iso(),
                    "measurements": measurements,
                    "visibility_mean": _round(sum(visible) / len(visible)) if visible else 0.0,
                }
            )
    finally:
        landmarker.close()

    summary_samples = samples
    summary = summarize_pose_samples(summary_samples, duration_sec or (len(paths) * frame_interval_ms / 1000))
    if summary is None:
        return {"status": "no_pose", "reason": "no_pose_detected", "summary": None, "candidates": []}
    summary["input_frame_count"] = len(paths)
    summary["mean_visibility"] = _round(
        sum(float(sample.get("visibility_mean", 0.0)) for sample in summary_samples) / len(summary_samples)
    )
    return {
        "status": "done",
        "reason": "pose_detected",
        "summary": summary,
        "candidates": build_pose_capture_candidates(summary),
    }
