from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict


RawMediaKind = Literal["raw_audio", "raw_video"]


class RawMediaStage(NamedTuple):
    event_id: str
    kind: RawMediaKind
    transcript_text: str = ""
    consent_id: str = ""


class RawMediaArtifact(TypedDict):
    kind: RawMediaKind
    filename: str
    content_type: str
    file_size_bytes: int
    duration_seconds: float
    transcript_text: str
    consent_id: str
    download_path: str


_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
}


def _duration_seconds(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                frame_rate = audio.getframerate()
                return round(audio.getnframes() / frame_rate, 3) if frame_rate else 0.0
        except (OSError, wave.Error):
            return 0.0

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
        return round(float(result.stdout.strip()), 3) if result.returncode == 0 else 0.0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def stage_raw_media(source_path: Path, raw_media_dir: Path, stage: RawMediaStage) -> Path:
    raw_media_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix.lower() or (".wav" if stage.kind == "raw_audio" else ".mp4")
    staged_path = raw_media_dir / f"{stage.event_id}_{stage.kind.removeprefix('raw_')}{suffix}"
    shutil.move(str(source_path), staged_path)
    metadata = {
        "kind": stage.kind,
        "content_type": _CONTENT_TYPES.get(suffix, "application/octet-stream"),
        "duration_seconds": _duration_seconds(staged_path),
        "transcript_text": stage.transcript_text,
        "consent_id": stage.consent_id,
    }
    staged_path.with_suffix(staged_path.suffix + ".json").write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )
    return staged_path


def list_raw_media_artifacts(raw_media_dir: Path, event_id: str) -> list[RawMediaArtifact]:
    artifacts: list[RawMediaArtifact] = []
    for path in sorted(raw_media_dir.glob(f"{event_id}_*")):
        if not path.is_file() or path.suffix == ".json":
            continue
        metadata_path = path.with_suffix(path.suffix + ".json")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
        kind: RawMediaKind = "raw_audio" if "_audio" in path.stem else "raw_video"
        artifacts.append(
            {
                "kind": kind,
                "filename": path.name,
                "content_type": str(metadata.get("content_type") or _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")),
                "file_size_bytes": path.stat().st_size,
                "duration_seconds": float(metadata.get("duration_seconds") or 0),
                "transcript_text": str(metadata.get("transcript_text") or ""),
                "consent_id": str(metadata.get("consent_id") or ""),
                "download_path": f"/raw-media/{path.name}",
            }
        )
    return artifacts


def resolve_raw_media(raw_media_dir: Path, filename: str) -> Path | None:
    safe_name = Path(filename).name
    if safe_name != filename:
        return None
    path = raw_media_dir / safe_name
    try:
        resolved_root = raw_media_dir.resolve()
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return resolved_path if resolved_path.is_file() and resolved_path.suffix != ".json" else None


def delete_raw_media(raw_media_dir: Path, filename: str) -> bool:
    path = resolve_raw_media(raw_media_dir, filename)
    if path is None:
        return False
    path.unlink()
    path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)
    return True
