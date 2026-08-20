"""File download endpoints for uploads, masked frames, and staged raw media."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from lib.raw_media import delete_raw_media, list_raw_media_artifacts, resolve_raw_media
from pathlib import Path

import bridge_core as core
from bridge_core import (
    _audit_log,
    _conn,
    _error,
)

router = APIRouter()


def _authorize_raw_media_request(filename: str, request: Request) -> tuple[Path, str]:
    file_path = resolve_raw_media(core.RAW_MEDIA_DIR, filename)
    if file_path is None:
        raise HTTPException(status_code=404, detail="file not found")
    event_id = file_path.stem.rsplit("_", 1)[0]
    requested_org_id = request.headers.get("x-glasspt-org-id", "").strip()
    requested_provider_id = request.headers.get("x-glasspt-provider-person-id", "").strip()
    if not requested_org_id or not requested_provider_id:
        raise HTTPException(status_code=403, detail="scoped artifact access headers are required")
    with _conn() as conn:
        row = conn.execute(
            "SELECT owner_org_id, owner_provider_person_id FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="event not found")
    if row[0] != requested_org_id or row[1] != requested_provider_id:
        raise HTTPException(status_code=403, detail="artifact scope mismatch")
    return file_path, event_id


@router.get("/files/{filename}")
def get_uploaded_file(filename: str):
    if not core.ENABLE_FILE_DOWNLOADS:
        _error(404, "FILE_DOWNLOAD_DISABLED", "원본 업로드 파일 다운로드는 기본 비활성화되어 있습니다.")

    safe_name = Path(filename).name
    file_path = core.UPLOAD_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    media_type = None
    ext = file_path.suffix.lower()
    if ext in {".mp4", ".m4v"}:
        media_type = "video/mp4"
    elif ext == ".mov":
        media_type = "video/quicktime"
    elif ext == ".avi":
        media_type = "video/x-msvideo"
    elif ext == ".mkv":
        media_type = "video/x-matroska"

    return FileResponse(str(file_path), media_type=media_type, filename=safe_name)


@router.get("/masked-files/{filename}")
def get_masked_file(filename: str):
    """마스킹이 끝난 산출물만 보호된 경로로 내려준다."""
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=404, detail="file not found")

    file_path = core.MASKED_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(str(file_path), media_type="image/jpeg", filename=safe_name)


@router.get("/raw-media/{filename}")
def get_raw_media(filename: str, request: Request):
    file_path, event_id = _authorize_raw_media_request(filename, request)
    artifacts = list_raw_media_artifacts(core.RAW_MEDIA_DIR, event_id)
    content_type = next(
        (item["content_type"] for item in artifacts if item["filename"] == file_path.name),
        "application/octet-stream",
    )
    _audit_log(event_id, "info", "raw media accessed for scoped import")
    return FileResponse(str(file_path), media_type=content_type, filename=file_path.name)


@router.delete("/raw-media/{filename}")
def consume_raw_media(filename: str, request: Request):
    _, event_id = _authorize_raw_media_request(filename, request)
    if not delete_raw_media(core.RAW_MEDIA_DIR, filename):
        raise HTTPException(status_code=404, detail="file not found")
    _audit_log(event_id, "info", "raw media consumed after durable import")
    return {"ok": True, "filename": filename}
