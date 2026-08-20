#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import shutil
import tempfile
import threading
import uuid
import wave
from pathlib import Path

from fastapi.testclient import TestClient

import app as bridge
import bridge_core  # (mutable config/state lives here)
from mlops_harness import _isolated_bridge_runtime


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write_silent_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 16_000)


def _write_synthetic_mp4(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=2",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )


def _inner_event_id(client: TestClient, outer_event_id: str, headers: dict[str, str]) -> str:
    response = client.get(f"/events/{outer_event_id}", headers=headers)
    require(response.status_code == 200, "processed event should be readable")
    return str(response.json()["result"]["event"]["id"])


def main() -> None:
    original_stt = bridge_core.stt_whisper_local
    with _isolated_bridge_runtime() as runtime, tempfile.TemporaryDirectory(prefix="rayban-roundtrip-") as temp_dir:
        bridge_core.AUDIO_STORE = True
        bridge_core.VIDEO_STORE = True
        bridge_core.stt_whisper_local = lambda _path: "합성 음성 기록"

        try:
            root = Path(temp_dir)
            wav_path = root / "capture.wav"
            mp4_path = root / "capture.mp4"
            _write_silent_wav(wav_path)
            _write_synthetic_mp4(mp4_path)

            client = TestClient(bridge.app)
            health = client.get("/health")
            require(health.json()["security"]["audio_store"] is True, "audio staging should be visible in health")
            require(health.json()["security"]["video_store"] is True, "video staging should be visible in health")
            headers = {
                "x-api-key": str(runtime["api_key"]),
                "x-glasspt-org-id": "550e8400-e29b-41d4-a716-446655440001",
                "x-glasspt-provider-person-id": "550e8400-e29b-41d4-a716-446655440003",
            }
            patient_name = "SyntheticPatient"
            scope = {
                "patient_name": patient_name,
                "subject_person_id": "550e8400-e29b-41d4-a716-446655440002",
                "physio_client_id": "550e8400-e29b-41d4-a716-446655440005",
                "physio_session_id": "550e8400-e29b-41d4-a716-446655440004",
            }

            with wav_path.open("rb") as audio:
                rejected_audio = client.post(
                    "/ingest-upload",
                    headers=headers,
                    data={**scope, "source": "rayban-hfp", "event_type": "audio"},
                    files={"audio": (wav_path.name, audio, "audio/wav")},
                )
            require(rejected_audio.status_code == 200, "async audio rejection should return its tracking id")
            rejected_status = client.get(f"/events/{rejected_audio.json()['event_id']}", headers=headers)
            require(rejected_status.json()["status"] == "error", "missing consent should fail processing")
            require(not list(bridge_core.UPLOAD_DIR.iterdir()), "rejected audio should not remain in uploads")

            rejected_video = client.post(
                "/ingest-video",
                headers=headers,
                data={**scope, "source": "rayban-camera"},
                files={"video": ("capture.avi", b"not-an-avi", "video/x-msvideo")},
            )
            require(rejected_video.status_code == 400, "unsupported video formats should fail before persistence")

            consent = client.post(
                "/consents",
                headers=headers,
                json={
                    "patient_name": patient_name,
                    "subject_person_id": scope["subject_person_id"],
                    "granted_by": "raw-media-roundtrip",
                },
            )
            require(consent.status_code == 200, "synthetic consent should be recorded")

            with wav_path.open("rb") as audio:
                audio_response = client.post(
                    "/ingest-upload",
                    headers=headers,
                    data={**scope, "source": "rayban-hfp", "event_type": "audio"},
                    files={"audio": (wav_path.name, audio, "audio/wav")},
                )
            require(audio_response.status_code == 200, "audio upload should be accepted")
            audio_event_id = _inner_event_id(client, audio_response.json()["event_id"], headers)

            with mp4_path.open("rb") as video:
                video_response = client.post(
                    "/ingest-video",
                    headers=headers,
                    data={**scope, "source": "rayban-camera"},
                    files={"video": (mp4_path.name, video, "video/mp4")},
                )
            require(video_response.status_code == 200, "video upload should be accepted")
            video_event_id = _inner_event_id(client, video_response.json()["event_id"], headers)

            with bridge._conn() as conn:
                audio_audit = conn.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE event_id = ? AND message LIKE 'upload processed%'",
                    (audio_event_id,),
                ).fetchone()[0]
                video_audit = conn.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE event_id = ? AND message = 'video processed'",
                    (video_event_id,),
                ).fetchone()[0]
            require(audio_audit == 1, "audio completion audit should reference the persisted event")
            require(video_audit == 1, "video completion audit should reference the persisted event")

            feed = client.get("/physio/sessions?limit=20", headers=headers)
            require(feed.status_code == 200, "physio session feed should be readable")
            sessions = {item["event_id"]: item for item in feed.json()["items"]}
            audio_artifact = next(
                item for item in sessions[audio_event_id]["artifacts"] if item["kind"] == "raw_audio"
            )
            video_artifact = next(
                item for item in sessions[video_event_id]["artifacts"] if item["kind"] == "raw_video"
            )
            require(audio_artifact["transcript_text"] == "합성 음성 기록", "audio transcript should round-trip")
            require(bool(audio_artifact["consent_id"]), "audio artifact should retain consent provenance")
            require(video_artifact["content_type"] == "video/mp4", "video content type should round-trip")
            require(bool(video_artifact["consent_id"]), "video artifact should retain consent provenance")

            wildcard_delete = client.delete("/events/%2A", headers=headers)
            require(wildcard_delete.status_code == 404, "wildcard event IDs must not delete staged media")
            require(
                all(bridge.resolve_raw_media(bridge_core.RAW_MEDIA_DIR, item["filename"]) for item in (audio_artifact, video_artifact)),
                "wildcard event deletion must leave unrelated artifacts intact",
            )

            configured_key = bridge_core.BRIDGE_API_KEY
            bridge_core.BRIDGE_API_KEY = ""
            try:
                spoofed = client.get(
                    audio_artifact["download_path"],
                    headers={"x-forwarded-for": "127.0.0.1"},
                )
                require(spoofed.status_code == 503, "forwarded loopback must not bypass bridge authentication")
            finally:
                bridge_core.BRIDGE_API_KEY = configured_key

            for artifact in (audio_artifact, video_artifact):
                wrong_scope = {**headers, "x-glasspt-org-id": "550e8400-e29b-41d4-a716-446655440099"}
                forbidden = client.get(artifact["download_path"], headers=wrong_scope)
                require(forbidden.status_code == 403, "raw artifact should reject a mismatched organization")
                download = client.get(artifact["download_path"], headers=headers)
                require(download.status_code == 200, "staged raw artifact should download")
                deleted = client.delete(artifact["download_path"], headers=headers)
                require(deleted.status_code == 200, "staged raw artifact should delete after import")
                missing = client.get(artifact["download_path"], headers=headers)
                require(missing.status_code == 404, "consumed raw artifact should no longer exist")

            with wav_path.open("rb") as audio:
                revocation_response = client.post(
                    "/ingest-upload",
                    headers=headers,
                    data={**scope, "source": "rayban-hfp", "event_type": "audio"},
                    files={"audio": (wav_path.name, audio, "audio/wav")},
                )
            require(revocation_response.status_code == 200, "revocation fixture upload should be accepted")
            revocation_event_id = _inner_event_id(
                client,
                revocation_response.json()["event_id"],
                headers,
            )
            revocation_feed = client.get("/physio/sessions?limit=20", headers=headers).json()["items"]
            revocation_session = next(item for item in revocation_feed if item["event_id"] == revocation_event_id)
            revocation_artifact = next(
                item for item in revocation_session["artifacts"] if item["kind"] == "raw_audio"
            )
            other_headers = {
                "x-api-key": str(runtime["api_key"]),
                "x-glasspt-org-id": "550e8400-e29b-41d4-a716-446655440091",
                "x-glasspt-provider-person-id": "550e8400-e29b-41d4-a716-446655440093",
            }
            other_scope = {
                "patient_name": patient_name,
                "subject_person_id": "550e8400-e29b-41d4-a716-446655440092",
                "physio_client_id": "550e8400-e29b-41d4-a716-446655440095",
                "physio_session_id": "550e8400-e29b-41d4-a716-446655440094",
            }
            other_consent = client.post(
                "/consents",
                headers=other_headers,
                json={
                    "patient_name": patient_name,
                    "subject_person_id": other_scope["subject_person_id"],
                    "granted_by": "other-tenant-fixture",
                },
            )
            require(other_consent.status_code == 200, "same-name other-tenant consent should be isolated")
            with wav_path.open("rb") as audio:
                other_upload = client.post(
                    "/ingest-upload",
                    headers=other_headers,
                    data={**other_scope, "source": "rayban-hfp", "event_type": "audio"},
                    files={"audio": (wav_path.name, audio, "audio/wav")},
                )
            require(other_upload.status_code == 200, "other-tenant fixture upload should be accepted")
            other_event_id = _inner_event_id(client, other_upload.json()["event_id"], other_headers)
            other_feed = client.get(
                "/physio/sessions?limit=20&org_id=550e8400-e29b-41d4-a716-446655440091",
                headers=other_headers,
            ).json()["items"]
            other_session = next(item for item in other_feed if item["event_id"] == other_event_id)
            other_artifact = next(item for item in other_session["artifacts"] if item["kind"] == "raw_audio")
            revoked = client.request(
                "DELETE",
                "/consents",
                headers=headers,
                json={"patient_name": patient_name, "subject_person_id": scope["subject_person_id"]},
            )
            require(revoked.status_code == 200, "consent revocation should succeed")
            require(revoked.json()["purged_raw_files"] >= 1, "revocation should purge staged raw media")
            require(
                client.get(revocation_artifact["download_path"], headers=headers).status_code == 404,
                "revoked-consent raw media should no longer be downloadable",
            )
            require(
                client.get(other_artifact["download_path"], headers=other_headers).status_code == 200,
                "same-name consent revocation must not purge another tenant's raw media",
            )
            require(
                client.delete(other_artifact["download_path"], headers=other_headers).status_code == 200,
                "other-tenant fixture should clean up",
            )

            race_event_id = str(uuid.uuid4())
            with bridge._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO events (
                      id, source, event_type, status, patient_name, owner_org_id,
                      owner_provider_person_id, subject_person_id
                    ) VALUES (?, 'race-fixture', 'audio', 'processed', ?, ?, ?, ?)
                    """,
                    (
                        race_event_id,
                        patient_name,
                        other_headers["x-glasspt-org-id"],
                        other_headers["x-glasspt-provider-person-id"],
                        other_scope["subject_person_id"],
                    ),
                )
                conn.commit()
            race_source = root / "race.wav"
            shutil.copy2(wav_path, race_source)
            original_stage_raw_media = bridge_core.stage_raw_media
            stage_entered = threading.Event()
            release_stage = threading.Event()
            revoke_done = threading.Event()
            stage_errors: list[Exception] = []
            revoke_response: dict[str, object] = {}

            def pausing_stage_raw_media(source_path, raw_media_dir, stage):
                stage_entered.set()
                if not release_stage.wait(timeout=5):
                    raise TimeoutError("race fixture stage release timed out")
                return original_stage_raw_media(source_path, raw_media_dir, stage)

            def run_stage() -> None:
                try:
                    bridge._stage_raw_media_if_consent_active(
                        race_source,
                        bridge.RawMediaStage(
                            event_id=race_event_id,
                            kind="raw_audio",
                            consent_id=other_consent.json()["consent"]["id"],
                        ),
                        owner_org_id=other_headers["x-glasspt-org-id"],
                        owner_provider_person_id=other_headers["x-glasspt-provider-person-id"],
                        subject_person_id=other_scope["subject_person_id"],
                    )
                except Exception as exc:
                    stage_errors.append(exc)

            def run_revoke() -> None:
                response = TestClient(bridge.app).request(
                    "DELETE",
                    "/consents",
                    headers=other_headers,
                    json={
                        "patient_name": patient_name,
                        "subject_person_id": other_scope["subject_person_id"],
                    },
                )
                revoke_response["status_code"] = response.status_code
                revoke_done.set()

            bridge_core.stage_raw_media = pausing_stage_raw_media
            try:
                stage_thread = threading.Thread(target=run_stage)
                revoke_thread = threading.Thread(target=run_revoke)
                stage_thread.start()
                require(stage_entered.wait(timeout=5), "race fixture should reach raw staging")
                revoke_thread.start()
                require(not revoke_done.wait(timeout=0.2), "revocation should serialize with raw staging")
                release_stage.set()
                stage_thread.join(timeout=5)
                revoke_thread.join(timeout=5)
            finally:
                bridge_core.stage_raw_media = original_stage_raw_media
                release_stage.set()
            require(not stage_errors, "race fixture staging should complete without errors")
            require(revoke_response.get("status_code") == 200, "race fixture revocation should complete")
            require(
                not bridge.list_raw_media_artifacts(bridge_core.RAW_MEDIA_DIR, race_event_id),
                "revocation racing with staging must leave no raw media",
            )
            with bridge._conn() as conn:
                phi_log_count = conn.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE message LIKE ?",
                    (f"%{patient_name}%",),
                ).fetchone()[0]
            require(phi_log_count == 0, "patient names must not be written to audit logs")
        finally:
            bridge_core.stt_whisper_local = original_stt

    print("OK: raw media HTTP round-trip smoke test passed")


if __name__ == "__main__":
    main()
