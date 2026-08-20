# moai_web Upsert Payload Drafts

This document defines practical payload drafts for sending `rayban_pt` bridge data into `moai_web`.

It assumes:

- `organization_id` is already known
- `provider_person_id` is already known
- `subject_person_id` is already resolved
- an `encounter_id` may already exist, or will be created first

All examples are drafts for integration design. Exact insert/upsert code can be built on top of these shapes.

## 1. Canonical Context

This is the minimum context every bridge-originated payload should carry.

```json
{
  "source_system": "rayban_pt",
  "source_event_id": "evt_01j123...",
  "organization_id": "org_123",
  "provider_person_id": "person_provider_123",
  "subject_person_id": "person_patient_456",
  "encounter_id": "enc_789",
  "captured_at": "2026-05-25T08:42:00Z",
  "capture_device": "rayban",
  "capture_mode": "image|video|audio|text|merged"
}
```

## 2. `encounters`

Use `encounters` as the session anchor.

When to create:

- first capture for a new visit/session
- first upload when there is no existing `encounter_id`

Suggested payload:

```json
{
  "id": "enc_789",
  "organization_id": "org_123",
  "provider_person_id": "person_provider_123",
  "subject_person_id": "person_patient_456",
  "class": "outpatient",
  "status": "draft",
  "session_type": "standing_training",
  "service_domain": "physical_therapy",
  "source_system": "rayban_pt",
  "flow_mode": "capture_to_note",
  "period_start": "2026-05-25T08:42:00Z",
  "chief_complaint": "balance and standing tolerance",
  "created_by": "person_provider_123"
}
```

Minimal required payload:

```json
{
  "organization_id": "org_123",
  "provider_person_id": "person_provider_123",
  "subject_person_id": "person_patient_456",
  "class": "outpatient",
  "status": "draft",
  "period_start": "2026-05-25T08:42:00Z",
  "created_by": "person_provider_123"
}
```

## 3. `encounter_media`

Use for image/video artifacts.

When to create:

- image capture
- masked image upload
- representative frame from video
- original video record if retained

Suggested image payload:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "created_by": "person_provider_123",
  "media_type": "image",
  "media_subtype": "masked_capture",
  "content_type": "image/jpeg",
  "storage_bucket": "encounter-media",
  "storage_path": "rayban_pt/enc_789/evt_01j123_masked.jpg",
  "original_filename": "evt_01j123_masked.jpg",
  "captured_at": "2026-05-25T08:42:02Z",
  "file_size_bytes": 284102,
  "width": 1280,
  "height": 720,
  "analysis_status": "completed",
  "analysis_result": {
    "masking": {
      "status": "completed",
      "face_count": 1,
      "detector": "yunet",
      "shape": "solid"
    }
  },
  "metadata": {
    "source_system": "rayban_pt",
    "source_event_id": "evt_01j123",
    "capture_device": "rayban"
  },
  "title": "Ray-Ban masked still"
}
```

Suggested video payload:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "created_by": "person_provider_123",
  "media_type": "video",
  "media_subtype": "session_capture",
  "content_type": "video/mp4",
  "storage_bucket": "encounter-media",
  "storage_path": "rayban_pt/enc_789/evt_01j124.mp4",
  "original_filename": "evt_01j124.mp4",
  "captured_at": "2026-05-25T08:43:10Z",
  "duration_seconds": 58,
  "analysis_status": "pending",
  "metadata": {
    "source_system": "rayban_pt",
    "source_event_id": "evt_01j124",
    "capture_device": "rayban"
  }
}
```

## 4. `voice_memos`

Use for audio capture and transcript lifecycle.

Suggested payload:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "provider_person_id": "person_provider_123",
  "subject_person_id": "person_patient_456",
  "file_path": "rayban_pt/enc_789/evt_01j125.m4a",
  "recorded_at": "2026-05-25T08:44:00Z",
  "duration_seconds": 42,
  "file_size_bytes": 583221,
  "processing_status": "completed",
  "processed_at": "2026-05-25T08:44:08Z",
  "transcript_text": "환아 기립 유지 20초 가능. 후반부 몸통 흔들림 보임.",
  "transcript_segments": [
    {
      "start": 0.0,
      "end": 4.2,
      "text": "환아 기립 유지 20초 가능."
    },
    {
      "start": 4.2,
      "end": 8.0,
      "text": "후반부 몸통 흔들림 보임."
    }
  ]
}
```

## 5. `ai_inference_log`

Use for every AI-produced output that should be auditable.

Suggested payload for SOAP draft:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "agent_type": "soap_draft",
  "model_key": "gpt-5",
  "model_version": "2026-05",
  "prompt_version": "rayban-pt-soap-v1",
  "output_type": "soap_note",
  "confidence": 0.78,
  "latency_ms": 1840,
  "token_count": 1421,
  "review_status": "pending",
  "target_resource_type": "encounter_note",
  "target_resource_id": "note_123",
  "output_snapshot": {
    "subjective": "환자 주관적 호소 미입력",
    "objective": "기립 유지 20초, 후반부 몸통 흔들림 관찰",
    "assessment": "체간 안정성 저하와 피로 누적 소견",
    "plan": "기립 유지 훈련, 체간 안정화 훈련 지속"
  }
}
```

Suggested payload for image analysis:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "agent_type": "media_summary",
  "model_key": "pose-pipeline",
  "model_version": "rtmpose-v3",
  "output_type": "structured_findings",
  "confidence": 0.72,
  "review_status": "pending",
  "target_resource_type": "encounter_media",
  "target_resource_id": "media_456",
  "output_snapshot": {
    "body_region": "trunk",
    "findings": [
      "standing posture observed",
      "trunk sway increased near end of hold"
    ],
    "timing": {
      "hold_seconds": 20
    }
  }
}
```

## 6. `client_media_summaries`

Use for summarized media findings intended for retrieval or downstream drafting.

Suggested payload:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "author_person_id": "person_provider_123",
  "media_kind": "image",
  "media_ref_type": "encounter_media",
  "media_ref_id": "media_456",
  "body_region": "trunk",
  "observed_at": "2026-05-25T08:42:02Z",
  "title": "Standing posture summary",
  "summary_text": "Standing posture captured with increased trunk sway in later phase.",
  "structured_findings": {
    "session_type": "standing_training",
    "core_task": "supported_standing",
    "assist_level": "mod",
    "performance": "fair",
    "flags": ["fatigue", "trunk_instability"]
  },
  "metadata": {
    "source_system": "rayban_pt",
    "source_event_id": "evt_01j123",
    "analysis_source": "image_pipeline"
  }
}
```

## 7. `encounter_notes`

Use as the final or draft clinical note destination.

Suggested SOAP draft payload:

```json
{
  "id": "note_123",
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "provider_person_id": "person_provider_123",
  "subject_person_id": "person_patient_456",
  "note_format": "SOAP",
  "status": "draft",
  "source_system": "rayban_pt",
  "source_type": "rayban_pt_bridge",
  "subjective": "환자 주관적 호소 미입력",
  "objective": "기립 유지 20초, 후반부 체간 흔들림 관찰",
  "assessment": "체간 안정성 저하와 피로 누적 소견",
  "plan": "기립 유지 훈련, 체간 안정화 훈련, 다음 방문 시 재평가",
  "note_content": "S> 환자 주관적 호소 미입력\nO> 기립 유지 20초...\nA> 체간 안정성 저하...\nP> 기립 유지 훈련...",
  "ai_draft_snapshot": {
    "source_event_id": "evt_01j125",
    "source_system": "rayban_pt",
    "draft_version": 1
  },
  "requires_approval": true,
  "approval_status": "pending"
}
```

Suggested therapist-approved update:

```json
{
  "id": "note_123",
  "status": "final",
  "approval_status": "approved",
  "approved_at": "2026-05-25T09:02:00Z",
  "approved_by_person_id": "person_provider_123",
  "review_action": "edited_and_approved"
}
```

## 8. `observations`

Use for structured label outputs that should survive beyond note text.

### Example A: assist level

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "created_by": "person_provider_123",
  "source_type": "rayban_pt_label",
  "status": "final",
  "category": ["functional_status"],
  "code": "assist_level",
  "code_display": "Assist Level",
  "value_type": "string",
  "value_string": "mod",
  "note": "Therapist-reviewed label",
  "effective_datetime": "2026-05-25T08:45:00Z"
}
```

### Example B: performance

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "created_by": "person_provider_123",
  "source_type": "rayban_pt_label",
  "status": "final",
  "category": ["functional_status"],
  "code": "task_performance",
  "code_display": "Task Performance",
  "value_type": "string",
  "value_string": "fair",
  "measurement_context": {
    "core_task": "supported_standing"
  },
  "effective_datetime": "2026-05-25T08:45:00Z"
}
```

### Example C: flags

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "created_by": "person_provider_123",
  "source_type": "rayban_pt_label",
  "status": "final",
  "category": ["risk_flag"],
  "code": "session_flags",
  "code_display": "Session Flags",
  "value_type": "json",
  "value_json": {
    "flags": ["fatigue", "trunk_instability"]
  },
  "effective_datetime": "2026-05-25T08:45:00Z"
}
```

## 9. `activity_sessions`

Use when a captured session clearly corresponds to a functional exercise task.

Suggested payload:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "created_by": "person_provider_123",
  "activity_type": "supported_standing",
  "performed_at": "2026-05-25T08:42:02Z",
  "duration_seconds": 20,
  "source": "rayban_pt",
  "status": "completed",
  "notes": "Moderate assist required. Increased trunk sway near end.",
  "metrics": {
    "assist_level": "mod",
    "performance": "fair",
    "flags": ["fatigue", "trunk_instability"],
    "hold_seconds": 20
  },
  "has_timeseries": false
}
```

## 10. `clinical_extraction_reviews`

Use for human-in-the-loop review of AI structured outputs.

Suggested payload:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "subject_person_id": "person_patient_456",
  "ai_inference_id": "infer_001",
  "source_modality": "image",
  "source_table": "encounter_media",
  "source_record_id": "media_456",
  "source_locator": {
    "source_event_id": "evt_01j123"
  },
  "proposed_payload": {
    "assist_level": "mod",
    "performance": "fair",
    "flags": ["fatigue"]
  },
  "final_payload": {
    "assist_level": "mod",
    "performance": "fair",
    "flags": ["fatigue", "trunk_instability"]
  },
  "review_status": "approved_with_edits",
  "review_note": "Added trunk instability flag after manual review.",
  "reviewed_at": "2026-05-25T09:00:00Z",
  "reviewer_person_id": "person_provider_123"
}
```

## 11. `ai_feedback`

Use for narrative or draft correction feedback.

Suggested payload:

```json
{
  "organization_id": "org_123",
  "encounter_id": "enc_789",
  "inference_id": "infer_001",
  "reviewer_person_id": "person_provider_123",
  "action": "edited",
  "modification_type": "soap_assessment",
  "original_content": {
    "assessment": "체간 안정성 저하"
  },
  "modified_content": {
    "assessment": "체간 안정성 저하 및 피로 누적 소견"
  },
  "review_duration_ms": 22000
}
```

## 12. `patient_consent_records`

Suggested payload:

```json
{
  "organization_id": "org_123",
  "person_id": "person_patient_456",
  "created_by": "person_provider_123",
  "content_code": "capture_analysis_storage",
  "delivery_method": "in_person_verbal_plus_written",
  "signed_at": "2026-05-25T08:40:00Z",
  "signed_content_snapshot": {
    "text": "capture, analysis, and storage consent",
    "source_system": "rayban_pt"
  }
}
```

## 13. `clinical_events`

Use for audit and operational tracing.

Suggested payload:

```json
{
  "organization_id": "org_123",
  "subject_person_id": "person_patient_456",
  "actor_id": "person_provider_123",
  "actor_type": "user",
  "event_type": "create",
  "event_subtype": "rayban_pt_image",
  "resource_type": "encounter_media",
  "resource_id": "media_456",
  "action_description": "Bridge accepted masked Ray-Ban image upload.",
  "api_endpoint": "/ingest-image",
  "http_method": "POST",
  "http_status_code": 200,
  "response_time_ms": 1840,
  "consent_verified": true,
  "severity": "info",
  "occurred_at": "2026-05-25T08:42:05Z",
  "new_value": {
    "source_event_id": "evt_01j123",
    "capture_device": "rayban"
  }
}
```

## 14. Bundle Example

For one image + one voice memo + one SOAP draft, the bridge would typically emit:

1. `encounters`
2. `encounter_media`
3. `voice_memos`
4. `ai_inference_log`
5. `client_media_summaries`
6. `encounter_notes`
7. `observations`
8. `clinical_extraction_reviews`
9. `clinical_events`

## 15. Recommended Bridge Output Contract

If the local bridge starts producing a normalized export object before writing to Supabase, use something like this:

```json
{
  "context": {
    "organization_id": "org_123",
    "provider_person_id": "person_provider_123",
    "subject_person_id": "person_patient_456",
    "encounter_id": "enc_789",
    "source_event_id": "evt_01j123",
    "source_system": "rayban_pt"
  },
  "encounter": {},
  "media": [],
  "voice_memos": [],
  "ai_inference_logs": [],
  "media_summaries": [],
  "notes": [],
  "observations": [],
  "activity_sessions": [],
  "reviews": [],
  "audit_events": []
}
```

This makes it easier to keep the local bridge logic stable even if Supabase write strategy changes later.
