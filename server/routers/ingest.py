"""Capture ingestion endpoints: JSON events plus audio, image, and video uploads."""

import base64
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from datetime import datetime
from lib.auto_chart import mask_faces as _mask_faces
from lib.pose_capture import analyze_pose_frames
from lib.raw_media import RawMediaStage
from pathlib import Path
from pydantic import BaseModel
from typing import Optional

import bridge_core as core
from bridge_core import (
    PROCESS_TIMEOUT_SECONDS,
    _audit_log,
    _capture_origin_from_source,
    _conn,
    _create_pose_capture_events,
    _error,
    _event_status_result,
    _normalize_error,
    _process_event,
    _run_with_timeout,
    _scope_from_request,
    _stage_raw_media_if_consent_active,
    _touch_async_result,
    _validate_upload_size,
    logger,
)

router = APIRouter()


class IngestPayload(BaseModel):
    source: str
    event_type: str  # audio/text/command/image
    text: Optional[str] = None
    audio_path: Optional[str] = None
    image_base64: Optional[str] = None  # base64 encoded JPEG/PNG
    patient_name: Optional[str] = None
    owner_org_id: Optional[str] = None
    owner_provider_person_id: Optional[str] = None
    org_id: Optional[str] = None
    provider_person_id: Optional[str] = None
    subject_person_id: Optional[str] = None
    physio_client_id: Optional[str] = None
    physio_session_id: Optional[str] = None
    client_id: Optional[str] = None
    session_id: Optional[str] = None
    encounter_id: Optional[str] = None


def _process_upload_job(
    event_id: str,
    source: str,
    saved_path: Path,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    physio_client_id: Optional[str] = None,
    physio_session_id: Optional[str] = None,
):
    attempts = 2
    last_error = None
    for i in range(attempts):
        try:
            _touch_async_result(event_id, {
                "status": "processing",
                "message": f"audio processing attempt={i+1}",
            })
            started = datetime.utcnow()
            result = _run_with_timeout(
                _process_event,
                PROCESS_TIMEOUT_SECONDS,
                source=source,
                event_type="audio",
                audio_path=str(saved_path),
                patient_name=patient_name,
                owner_org_id=owner_org_id,
                owner_provider_person_id=owner_provider_person_id,
                subject_person_id=subject_person_id,
                physio_client_id=physio_client_id,
                physio_session_id=physio_session_id,
            )
            inner_id = result.get("event_id", "")
            if inner_id and inner_id != event_id:
                import shutil as _shutil
                inner_chart = core.CHART_DIR / f"{inner_id}_11.txt"
                outer_chart = core.CHART_DIR / f"{event_id}_11.txt"
                if inner_chart.exists() and not outer_chart.exists():
                    _shutil.copy(inner_chart, outer_chart)
            if core.AUDIO_STORE and inner_id and saved_path.exists():
                with _conn() as conn:
                    transcript_row = conn.execute(
                        "SELECT raw_text FROM events WHERE id = ?",
                        (inner_id,),
                    ).fetchone()
                transcript_text = transcript_row[0] if transcript_row and transcript_row[0] else ""
                _stage_raw_media_if_consent_active(
                    saved_path,
                    RawMediaStage(
                        event_id=inner_id,
                        kind="raw_audio",
                        transcript_text=transcript_text,
                        consent_id=str((result.get("policy") or {}).get("consent_id") or ""),
                    ),
                    owner_org_id=owner_org_id,
                    owner_provider_person_id=owner_provider_person_id,
                    subject_person_id=subject_person_id,
                )

            saved_path.unlink(missing_ok=True)
            _touch_async_result(event_id, {"status": "done", "result": _event_status_result(result)})
            took_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
            _audit_log(inner_id or None, "info", f"upload processed attempt={i+1} took_ms={took_ms}")
            return
        except Exception as e:
            last_error = e
            code, msg, retryable = _normalize_error(e)
            logger.exception("upload job failed event_id=%s attempt=%s code=%s", event_id, i + 1, code)
            _audit_log(None, "error", f"upload failed outer_event_id={event_id} attempt={i+1} code={code} msg={msg}")
            if i == attempts - 1:
                _touch_async_result(event_id, {
                    "status": "error",
                    "error": msg,
                    "error_code": code,
                    "retryable": retryable,
                })
    saved_path.unlink(missing_ok=True)


def _process_image_job(
    event_id: str,
    source: str,
    saved_path,
    description: str,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    physio_client_id: Optional[str] = None,
    physio_session_id: Optional[str] = None,
):
    image_store = os.getenv("IMAGE_STORE", "false").lower() == "true"
    try:
        _touch_async_result(event_id, {"status": "processing", "message": "image processing"})
        text = description if description else f"[이미지 캡처] 파일: {saved_path.name}"
        import base64
        image_base64 = base64.b64encode(Path(saved_path).read_bytes()).decode("ascii")
        started = datetime.utcnow()
        result = _run_with_timeout(
            _process_event,
            PROCESS_TIMEOUT_SECONDS,
            source=source,
            event_type="image",
            text=text,
            image_base64=image_base64,
            patient_name=patient_name,
            owner_org_id=owner_org_id,
            owner_provider_person_id=owner_provider_person_id,
            subject_person_id=subject_person_id,
            physio_client_id=physio_client_id,
            physio_session_id=physio_session_id,
        )
        inner_id = result.get("event_id", "")
        if inner_id and inner_id != event_id:
            import shutil as _shutil
            inner_chart = core.CHART_DIR / f"{inner_id}_11.txt"
            outer_chart = core.CHART_DIR / f"{event_id}_11.txt"
            if inner_chart.exists() and not outer_chart.exists():
                _shutil.copy(inner_chart, outer_chart)

        if image_store:
            result["image_path"] = str(saved_path)
        _touch_async_result(event_id, {"status": "done", "result": _event_status_result(result)})
        took_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
        _audit_log(event_id, "info", f"image processed took_ms={took_ms}")
    except Exception as e:
        code, msg, retryable = _normalize_error(e)
        logger.exception("image job failed event_id=%s code=%s", event_id, code)
        _audit_log(event_id, "error", f"image failed code={code} msg={msg}")
        _touch_async_result(event_id, {
            "status": "error",
            "error": msg,
            "error_code": code,
            "retryable": retryable,
        })
    finally:
        if not image_store:
            Path(saved_path).unlink(missing_ok=True)


def _process_video_job(
    event_id: str,
    source: str,
    saved_path: Path,
    patient_name: str = "",
    owner_org_id: Optional[str] = None,
    owner_provider_person_id: Optional[str] = None,
    subject_person_id: Optional[str] = None,
    physio_client_id: Optional[str] = None,
    physio_session_id: Optional[str] = None,
):
    import subprocess
    import tempfile
    import shutil as _shutil

    tmp_dir = Path(tempfile.mkdtemp(prefix="video_"))
    try:
        _touch_async_result(event_id, {"status": "processing", "message": "video processing"})
        # ── 1. 오디오 추출 ──────────────────────────────────────────
        audio_path = tmp_dir / "audio.m4a"
        audio_ok = False
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(saved_path), "-vn", "-acodec", "copy", str(audio_path)],
                capture_output=True, timeout=120,
            )
            audio_ok = r.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0
        except Exception:
            pass

        # ── 2. Whisper STT ──────────────────────────────────────────
        stt_text = ""
        if audio_ok:
            stt_text = core.stt_whisper_local(str(audio_path))

        # ── 3. 키프레임 추출 (1fps, 최대 10장) ─────────────────────
        frames_dir = tmp_dir / "frames"
        frames_dir.mkdir()
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(saved_path),
                 "-vf", "fps=1", "-frames:v", "10",
                 str(frames_dir / "frame_%04d.jpg")],
                capture_output=True, timeout=60,
            )
        except Exception:
            pass

        # ── 4. 프레임 마스킹 ────────────────────────────────────────
        frames = sorted(frames_dir.glob("*.jpg"))
        frame_notes = []
        for i, frame_path in enumerate(frames):
            try:
                masked_path = core.MASKED_DIR / f"{event_id}_f{i:04d}.jpg"
                res = _mask_faces(
                    frame_path,
                    masked_path,
                    method=os.getenv("FACE_MASK_METHOD", "solid"),
                    blur_kernel=91,
                )
                face_count = res.get("face_count", 0)
                detector = res.get("detector", "?")
                shape = res.get("shape", "box")
                sources = res.get("segment_sources") or []
                source_note = f", {'+'.join(sources)}" if sources else ""
                frame_notes.append(f"t+{i}s: {face_count}명 감지 ({detector}, {shape}{source_note})")
            except Exception:
                frame_notes.append(f"t+{i}s: 분석 오류")

        # ── 5. MediaPipe Pose 측정 (저장하지 않는 로컬 evidence) ─────
        pose_analysis = analyze_pose_frames(
            frames,
            frame_interval_ms=1_000,
            duration_sec=max(1.0, len(frames)),
        )
        pose_summary = pose_analysis.get("summary")
        pose_capture_candidates = pose_analysis.get("candidates") or []

        # ── 6. 통합 텍스트 ──────────────────────────────────────────
        parts = []
        if patient_name:
            parts.append("[환자] " + patient_name)
        parts.append(
            "[Ray-Ban 영상] 파일=" + saved_path.name +
            " 크기=" + str(saved_path.stat().st_size // 1024) + "KB"
        )
        if stt_text:
            parts.append("[치료사 음성 기록 — S> 섹션 참고]" + chr(10) + stt_text)
        else:
            parts.append("[치료사 음성] 음성 없음 또는 추출 실패")
        if frame_notes:
            parts.append(
                "[영상 분석 " + str(len(frames)) + "프레임]" + chr(10) +
                chr(10).join(frame_notes)
            )

        combined = (chr(10) + chr(10)).join(parts)

        # ── 7. SOAP 차트 생성 ────────────────────────────────────────
        result = _process_event(
            source=source,
            event_type="video",
            text=combined,
            patient_name=patient_name,
            owner_org_id=owner_org_id,
            owner_provider_person_id=owner_provider_person_id,
            subject_person_id=subject_person_id,
            physio_client_id=physio_client_id,
            physio_session_id=physio_session_id,
        )
        inner_id = result.get("event_id", "")

        pose_capture_events: list[dict] = []
        if inner_id and pose_capture_candidates:
            attached_session = (result.get("visit_auto_attach") or {}).get("session") or {}
            with _conn() as pose_conn:
                pose_capture_events = _create_pose_capture_events(
                    pose_conn,
                    candidates=pose_capture_candidates,
                    visit_session_id=attached_session.get("id"),
                    encounter_id=attached_session.get("encounter_id") or physio_session_id,
                    organization_id=attached_session.get("organization_id") or owner_org_id,
                    provider_person_id=attached_session.get("provider_person_id") or owner_provider_person_id,
                    provider_role=attached_session.get("provider_role"),
                    subject_person_id=attached_session.get("subject_person_id") or subject_person_id,
                    source_event_id=inner_id,
                    source_media_id=event_id,
                    start_ms=0,
                    end_ms=max(1_000, len(frames) * 1_000) if frames else None,
                    capture_origin=_capture_origin_from_source(source),
                )
                pose_conn.commit()
        result["pose_summary"] = pose_summary
        result["pose_capture_events"] = pose_capture_events

        # outer_event_id 로도 차트 조회 가능하도록 복사
        if inner_id and inner_id != event_id:
            inner_chart = core.CHART_DIR / f"{inner_id}_11.txt"
            outer_chart = core.CHART_DIR / f"{event_id}_11.txt"
            if inner_chart.exists() and not outer_chart.exists():
                _shutil.copy(inner_chart, outer_chart)
            for outer_masked in core.MASKED_DIR.glob(f"{event_id}_f*.jpg"):
                inner_masked = core.MASKED_DIR / outer_masked.name.replace(event_id, inner_id, 1)
                if not inner_masked.exists():
                    _shutil.copy(outer_masked, inner_masked)
        if core.VIDEO_STORE and inner_id and saved_path.exists():
            _stage_raw_media_if_consent_active(
                saved_path,
                RawMediaStage(
                    event_id=inner_id,
                    kind="raw_video",
                    consent_id=str((result.get("policy") or {}).get("consent_id") or ""),
                ),
                owner_org_id=owner_org_id,
                owner_provider_person_id=owner_provider_person_id,
                subject_person_id=subject_person_id,
            )

        # iOS EventStatusResponse 구조에 맞게 래핑
        with _conn() as _c:
            ev_row = _c.execute(
                "SELECT id, source, event_type, raw_text, intent, status, created_at, owner_org_id, owner_provider_person_id, subject_person_id, physio_client_id, physio_session_id "
                "FROM events WHERE id = ?",
                (inner_id,),
            ).fetchone()
        event_obj = None
        if ev_row:
            event_obj = {
                "id": ev_row[0], "source": ev_row[1], "event_type": ev_row[2],
                "raw_text": ev_row[3], "intent": ev_row[4],
                "status": ev_row[5], "created_at": ev_row[6],
                "owner_org_id": ev_row[7],
                "owner_provider_person_id": ev_row[8],
                "subject_person_id": ev_row[9],
                "physio_client_id": ev_row[10],
                "physio_session_id": ev_row[11],
            }

        _touch_async_result(event_id, {
            "status": "done",
            "result": {
                "event": event_obj,
                "soap": result.get("soap"),
            },
        })
        _audit_log(inner_id or None, "info", "video processed")

    except Exception as e:
        code, msg, retryable = _normalize_error(e)
        logger.exception("video job failed event_id=%s code=%s", event_id, code)
        _audit_log(None, "error", f"video failed outer_event_id={event_id} code={code} msg={msg}")
        _touch_async_result(event_id, {
            "status": "error",
            "error": msg,
            "error_code": code,
            "retryable": retryable,
        })
    finally:
        _shutil.rmtree(tmp_dir, ignore_errors=True)
        saved_path.unlink(missing_ok=True)


@router.post("/ingest")
def ingest(payload: IngestPayload, request: Request):
    owner_org_id, owner_provider_person_id = _scope_from_request(
        request,
        owner_org_id=payload.owner_org_id or payload.org_id,
        owner_provider_person_id=payload.owner_provider_person_id or payload.provider_person_id,
    )
    return _process_event(
        source=payload.source,
        event_type=payload.event_type,
        text=payload.text,
        audio_path=payload.audio_path,
        image_base64=payload.image_base64,
        patient_name=payload.patient_name or "",
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
        subject_person_id=payload.subject_person_id,
        physio_client_id=payload.physio_client_id or payload.client_id,
        physio_session_id=payload.physio_session_id or payload.session_id or payload.encounter_id,
    )


@router.post("/ingest-upload")
async def ingest_upload(
    background_tasks: BackgroundTasks,
    request: Request,
    source: str = Form("iphone"),
    event_type: str = Form("audio"),
    patient_name: str = Form(""),
    owner_org_id: str = Form(""),
    owner_provider_person_id: str = Form(""),
    subject_person_id: str = Form(""),
    physio_client_id: str = Form(""),
    physio_session_id: str = Form(""),
    audio: UploadFile = File(...),
):
    if event_type != "audio":
        _error(400, "INVALID_EVENT_TYPE", "ingest-upload only supports event_type=audio")

    ext = (Path(audio.filename or "").suffix or "").lower()
    allowed_ext = {".wav"}
    content_type = (audio.content_type or "").lower()
    allowed_content_types = {"audio/wav", "audio/x-wav"}

    if ext not in allowed_ext or content_type not in allowed_content_types:
        _error(400, "INVALID_AUDIO_FILE", "현재 캡처 경로는 WAV 오디오만 허용합니다.")

    safe_ext = ext if ext else ".bin"
    saved_path = core.UPLOAD_DIR / f"{uuid.uuid4()}{safe_ext}"

    content = await audio.read()
    _validate_upload_size(content, "audio")
    if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
        _error(400, "INVALID_AUDIO_FILE", "WAV 파일 서명이 올바르지 않습니다.")
    saved_path.write_bytes(content)

    event_id = str(uuid.uuid4())
    scoped_org_id, scoped_provider_person_id = _scope_from_request(
        request,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
    )
    _touch_async_result(event_id, {"status": "accepted", "message": "uploaded"})
    background_tasks.add_task(
        _process_upload_job,
        event_id,
        source,
        saved_path,
        patient_name,
        scoped_org_id,
        scoped_provider_person_id,
        subject_person_id,
        physio_client_id,
        physio_session_id,
    )

    return {
        "event_id": event_id,
        "status": "accepted",
        "message": "업로드 접수 완료. /events/{event_id} 로 결과를 조회하세요.",
    }


@router.post("/ingest-image")
async def ingest_image(
    background_tasks: BackgroundTasks,
    request: Request,
    source: str = Form("rayban"),
    description: str = Form(""),
    patient_name: str = Form(""),
    owner_org_id: str = Form(""),
    owner_provider_person_id: str = Form(""),
    subject_person_id: str = Form(""),
    physio_client_id: str = Form(""),
    physio_session_id: str = Form(""),
    image: UploadFile = File(...),
):
    ext = (Path(image.filename or "").suffix or "").lower()
    allowed_ext = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
    content_type = (image.content_type or "").lower()

    is_image_type = content_type.startswith("image/")
    is_image_ext = ext in allowed_ext

    if not (is_image_type or is_image_ext):
        _error(400, "INVALID_IMAGE_FILE", f"이미지 파일만 업로드 가능합니다. content_type={content_type or 'unknown'}, ext={ext or 'none'}")

    safe_ext = ext if ext else ".jpg"
    saved_path = core.UPLOAD_DIR / f"{__import__('uuid').uuid4()}{safe_ext}"

    content = await image.read()
    _validate_upload_size(content, "image")
    saved_path.write_bytes(content)

    event_id = str(__import__('uuid').uuid4())
    scoped_org_id, scoped_provider_person_id = _scope_from_request(
        request,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
    )
    _touch_async_result(event_id, {"status": "accepted", "message": "image uploaded"})
    background_tasks.add_task(
        _process_image_job,
        event_id,
        source,
        saved_path,
        description,
        patient_name,
        scoped_org_id,
        scoped_provider_person_id,
        subject_person_id,
        physio_client_id,
        physio_session_id,
    )

    return {
        "event_id": event_id,
        "status": "accepted",
        "image_saved": saved_path.name,
        "message": "이미지 접수 완료. /events/{event_id} 로 결과를 조회하세요.",
    }


@router.post("/ingest-video")
async def ingest_video(
    background_tasks: BackgroundTasks,
    request: Request,
    source: str = Form("rayban-camera"),
    patient_name: str = Form(""),
    owner_org_id: str = Form(""),
    owner_provider_person_id: str = Form(""),
    subject_person_id: str = Form(""),
    physio_client_id: str = Form(""),
    physio_session_id: str = Form(""),
    video: UploadFile = File(...),
):
    ext = (Path(video.filename or "").suffix or "").lower()
    allowed_ext = {".mp4", ".mov"}
    content_type = (video.content_type or "").lower()
    allowed_content_types = {"video/mp4", "video/quicktime"}

    if ext not in allowed_ext or content_type not in allowed_content_types:
        _error(400, "INVALID_VIDEO_FILE", "현재 캡처 경로는 MP4 또는 QuickTime 영상만 허용합니다.")

    safe_ext = ext if ext else ".mp4"
    saved_path = core.UPLOAD_DIR / f"{uuid.uuid4()}{safe_ext}"

    content = await video.read()
    _validate_upload_size(content, "video")
    if len(content) < 12 or content[4:8] != b"ftyp":
        _error(400, "INVALID_VIDEO_FILE", "영상 파일 서명이 올바르지 않습니다.")
    saved_path.write_bytes(content)

    event_id = str(uuid.uuid4())
    scoped_org_id, scoped_provider_person_id = _scope_from_request(
        request,
        owner_org_id=owner_org_id,
        owner_provider_person_id=owner_provider_person_id,
    )
    _touch_async_result(event_id, {"status": "accepted", "message": "video uploaded"})
    background_tasks.add_task(
        _process_video_job,
        event_id,
        source,
        saved_path,
        patient_name,
        scoped_org_id,
        scoped_provider_person_id,
        subject_person_id,
        physio_client_id,
        physio_session_id,
    )

    return {
        "event_id": event_id,
        "status": "accepted",
        "video_saved": saved_path.name,
        "size_kb": len(content) // 1024,
        "message": "영상 접수 완료. /events/{event_id} 로 결과를 조회하세요.",
    }
