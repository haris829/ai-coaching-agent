-- UC-01 standalone development schema.
--
-- Minimum practical design: one row per session-open attempt, plus a generic
-- append-only event table.
--
-- The event table is the forward-compatibility seam. Fields that future use cases may
-- need (question, topic_tag, explain_differently_count, rating) can be appended as
-- events without a schema change. UC-01 does not implement those behaviours.

CREATE TABLE IF NOT EXISTS coaching_sessions (
    -- Required UC-01 record fields ------------------------------------------
    session_id                      TEXT    PRIMARY KEY,
    user_id                         TEXT    NOT NULL,
    session_type                    TEXT    NOT NULL,   -- SessionMode value
    linked_resource_type            TEXT,               -- 'course' | 'case_file' | NULL
    linked_resource_id              TEXT,
    naric_level                     INTEGER,            -- level actually applied
    created_at                      TEXT    NOT NULL,   -- ISO-8601 UTC ("timestamp")

    -- Diagnosis / degradation ----------------------------------------------
    status                          TEXT    NOT NULL,   -- SessionStatus value
    requested_mode                  TEXT,               -- what the client asked for
    downgraded_from                 TEXT,               -- set when the mode was downgraded
    linked_resource_label           TEXT,
    linked_resource_secondary_id    TEXT,               -- lesson id for course-linked
    linked_resource_secondary_label TEXT,
    naric_level_source              TEXT    NOT NULL,   -- 'naric' | 'default' | 'default_user_acknowledged'
    explanation_level               INTEGER NOT NULL,
    degraded_dependencies           TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    failure_code                    TEXT,
    diagnostics_json                TEXT    NOT NULL DEFAULT '{}',   -- JSON object
    greeting_variant                TEXT,
    system_prompt_id                TEXT,               -- identifier only, never the body
    system_prompt_version           TEXT,
    dependency_failure_policy       TEXT    NOT NULL DEFAULT 'fail',
    updated_at                      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created
    ON coaching_sessions (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_status
    ON coaching_sessions (status);

CREATE TABLE IF NOT EXISTS session_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT    NOT NULL REFERENCES coaching_sessions (session_id) ON DELETE CASCADE,
    event_type   TEXT    NOT NULL,
    occurred_at  TEXT    NOT NULL,
    payload_json TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_session
    ON session_events (session_id, event_id);
