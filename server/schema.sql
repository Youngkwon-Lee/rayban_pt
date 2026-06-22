PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    raw_text TEXT,
    intent TEXT,
    status TEXT NOT NULL DEFAULT 'processed',
    patient_name TEXT,
    owner_org_id TEXT,
    owner_provider_person_id TEXT,
    subject_person_id TEXT,
    physio_client_id TEXT,
    physio_session_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS soap_notes (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    s TEXT,
    o TEXT,
    a TEXT,
    p TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    event_id TEXT,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS patient_consents (
    id TEXT PRIMARY KEY,
    patient_name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'capture_analysis_storage',
    consent_text TEXT NOT NULL,
    granted_by TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rehab_labels (
    event_id TEXT PRIMARY KEY,
    session_type TEXT NOT NULL,
    core_task TEXT NOT NULL,
    custom_task TEXT NOT NULL DEFAULT '',
    body_position TEXT NOT NULL DEFAULT '',
    assist_level TEXT NOT NULL,
    performance TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'reviewed',
    reviewer_person_id TEXT NOT NULL DEFAULT '',
    usable_for_training INTEGER NOT NULL DEFAULT 0,
    label_confidence REAL,
    repetition_count INTEGER,
    hold_duration_seconds REAL,
    tolerance TEXT NOT NULL DEFAULT '',
    fatigue_level TEXT NOT NULL DEFAULT '',
    compensations TEXT NOT NULL DEFAULT '[]',
    caregiver_present INTEGER,
    flags TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chart_reviews (
    event_id TEXT PRIMARY KEY,
    reviewer TEXT NOT NULL DEFAULT 'therapist',
    notes TEXT NOT NULL DEFAULT '',
    quality_score INTEGER NOT NULL DEFAULT 0,
    quality_level TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS moai_sync_jobs (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    trigger_reason TEXT NOT NULL,
    operation_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_plan_summary TEXT NOT NULL DEFAULT '{}',
    last_result_summary TEXT NOT NULL DEFAULT '{}',
    last_attempted_at TEXT,
    synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS visit_sessions (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    provider_person_id TEXT NOT NULL,
    subject_person_id TEXT NOT NULL,
    encounter_id TEXT NOT NULL,
    patient_alias TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL DEFAULT 'pre_review',
    status TEXT NOT NULL DEFAULT 'active',
    recording_status TEXT NOT NULL DEFAULT 'idle',
    selected_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    session_timer_started_at TEXT,
    history_summary TEXT NOT NULL DEFAULT '',
    readiness TEXT NOT NULL DEFAULT 'ready',
    error_state TEXT,
    cue TEXT NOT NULL DEFAULT '',
    event_ids TEXT NOT NULL DEFAULT '[]',
    draft_progress_note TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_owner_org_created_at ON events(owner_org_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_owner_provider_created_at ON events(owner_provider_person_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_subject_created_at ON events(subject_person_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_physio_client_created_at ON events(physio_client_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_physio_session_created_at ON events(physio_session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_soap_notes_event_id ON soap_notes(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_id ON audit_logs(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_level_created_at ON audit_logs(level, created_at);
CREATE INDEX IF NOT EXISTS idx_patient_consents_name_scope ON patient_consents(patient_name, scope, revoked_at, created_at);
CREATE INDEX IF NOT EXISTS idx_rehab_labels_review_status_updated_at ON rehab_labels(review_status, updated_at);
CREATE INDEX IF NOT EXISTS idx_chart_reviews_reviewed_at ON chart_reviews(reviewed_at);
CREATE INDEX IF NOT EXISTS idx_moai_sync_jobs_status_updated_at ON moai_sync_jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_moai_sync_jobs_event_id ON moai_sync_jobs(event_id);
CREATE INDEX IF NOT EXISTS idx_visit_sessions_org_updated_at ON visit_sessions(organization_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_visit_sessions_subject_updated_at ON visit_sessions(subject_person_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_visit_sessions_encounter_id ON visit_sessions(encounter_id);
