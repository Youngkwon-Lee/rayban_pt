import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

FILENAME_RE = re.compile(
    r"^(?P<uuid>[0-9a-fA-F-]{36})_(?P<date>\d{8})_(?P<time>\d{6})_(?P<type>[a-zA-Z]+)(?:_[a-zA-Z0-9]+)?\.(?P<ext>[a-zA-Z0-9]+)$"
)

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}
AUDIO_EXTS = {"wav", "mp3", "m4a", "aac", "ogg", "flac"}


@dataclass
class ParsedFile:
    src: Path
    uuid: str
    date: str
    time: str
    ftype: str
    ext: str


def load_settings(root: Path) -> dict:
    settings_path = root / "config" / "settings.json"
    if not settings_path.exists():
        settings_path = root / "config" / "settings.example.json"
    with open(settings_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_filename(path: Path) -> ParsedFile:
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ValueError(f"Invalid filename format: {path.name}")
    d = m.groupdict()
    datetime.strptime(d["date"] + d["time"], "%Y%m%d%H%M%S")
    return ParsedFile(
        src=path,
        uuid=d["uuid"],
        date=d["date"],
        time=d["time"],
        ftype=d["type"].lower(),
        ext=d["ext"].lower(),
    )


def make_session_key(date: str, time: str, window_minutes: int = 120) -> str:
    dt = datetime.strptime(date + time, "%Y%m%d%H%M%S")
    minutes = dt.hour * 60 + dt.minute
    bucket_start_minutes = (minutes // window_minutes) * window_minutes
    session_start = datetime(dt.year, dt.month, dt.day) + timedelta(minutes=bucket_start_minutes)
    session_end = session_start + timedelta(minutes=window_minutes)
    return f"{session_start.strftime('%H%M')}-{session_end.strftime('%H%M')}"


def ensure_dirs(root: Path, uuid: str, date: str) -> dict:
    base = root / "data"
    paths = {
        "raw": base / "raw" / uuid / date,
        "masked": base / "masked" / uuid / date,
        "audio": base / "audio" / uuid / date,
        "transcript": base / "transcript" / uuid / date,
        "charts": base / "charts" / uuid,
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths
