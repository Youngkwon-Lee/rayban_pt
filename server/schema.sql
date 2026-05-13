PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    raw_text TEXT,
    intent TEXT,
    status TEXT NOT NULL DEFAULT 'processed',
    patient_name TEXT,
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
    assist_level TEXT NOT NULL,
    performance TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_soap_notes_event_id ON soap_notes(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_id ON audit_logs(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_level_created_at ON audit_logs(level, created_at);
CREATE INDEX IF NOT EXISTS idx_patient_consents_name_scope ON patient_consents(patient_name, scope, revoked_at, created_at);
CREATE INDEX IF NOT EXISTS idx_chart_reviews_reviewed_at ON chart_reviews(reviewed_at);
