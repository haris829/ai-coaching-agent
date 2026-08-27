# Persistence

UC-01 ships its own **standalone development store** because no company database exists.
It is deliberately small, deliberately behind an interface, and deliberately not presented
as the final platform schema.

---

## Contract first

`uc01/contracts/repository.py`:

```python
class SessionRepository(Protocol):
    def create(self, record: SessionRecord) -> SessionRecord: ...
    def update(self, record: SessionRecord) -> SessionRecord: ...
    def get(self, session_id: str) -> SessionRecord | None: ...
    def list_for_user(self, user_id: str, limit: int = 50) -> Sequence[SessionRecord]: ...
    def append_event(self, event: SessionEvent) -> SessionEvent: ...
    def list_events(self, session_id: str) -> Sequence[SessionEvent]: ...
```

Two implementations ship, and `tests/test_persistence.py` runs the same assertions against
both:

| Implementation | Module | Use |
| --- | --- | --- |
| `SqliteSessionRepository` | `uc01/persistence/sqlite_repository.py` | default (`UC01_PERSISTENCE=sqlite`) |
| `InMemorySessionRepository` | `uc01/persistence/memory_repository.py` | tests, `UC01_PERSISTENCE=memory` |

The UC-01 service knows only the Protocol, so a company store is a third implementation —
no business-logic change.

---

## Commands

```bash
python -m uc01.persistence.migrate                      # apply pending migrations
python -m uc01.persistence.migrate --status              # list applied migrations
python -m uc01.persistence.migrate --path ./data/x.db    # target another file
```

Migrations also run on startup unless `UC01_AUTO_MIGRATE=false`. The runner is
idempotent: every `.sql` file in `uc01/persistence/migrations/` is applied once, in
filename order, tracked in `schema_migrations`.

Default file: `./data/uc01.sqlite3` (`UC01_DATABASE_PATH`). `data/` is git-ignored.

To reset: stop the app and delete the file, or run with `UC01_PERSISTENCE=memory`.

---

## Schema

`uc01/persistence/migrations/001_init.sql`.

### `coaching_sessions` — one row per session-open attempt

**Required UC-01 record fields**

| Column | Type | Notes |
| --- | --- | --- |
| `session_id` | TEXT PK | `sess_<uuid4 hex>` |
| `user_id` | TEXT | server-resolved caller, never client-supplied |
| `session_type` | TEXT | `free-form` \| `course-linked` \| `case-linked` (effective mode) |
| `linked_resource_type` | TEXT NULL | `course` \| `case_file` |
| `linked_resource_id` | TEXT NULL | course id or case id |
| `naric_level` | INTEGER NULL | the level actually applied |
| `created_at` | TEXT | ISO-8601 UTC — the record `timestamp` |

**Diagnosis and degradation**

| Column | Type | Notes |
| --- | --- | --- |
| `status` | TEXT | `initializing` \| `active` \| `degraded` \| `failed` |
| `requested_mode` | TEXT NULL | what the client asked for, before any downgrade |
| `downgraded_from` | TEXT NULL | set when the mode was downgraded to free-form |
| `linked_resource_label` | TEXT NULL | course/case title at open time |
| `linked_resource_secondary_id` | TEXT NULL | lesson id |
| `linked_resource_secondary_label` | TEXT NULL | lesson title |
| `naric_level_source` | TEXT | `naric` \| `default` \| `default_user_acknowledged` |
| `explanation_level` | INTEGER | level used for coaching (equals `naric_level` today) |
| `degraded_dependencies` | TEXT | JSON array, e.g. `["courses","profile"]` |
| `failure_code` | TEXT NULL | e.g. `session_mode_unavailable` |
| `diagnostics_json` | TEXT | JSON: requested selection, per-dependency state **and technical detail**, failure context |
| `greeting_variant` | TEXT NULL | e.g. `generic.course_linked` |
| `system_prompt_id` / `system_prompt_version` | TEXT NULL | **identifiers only — never the prompt body** |
| `dependency_failure_policy` | TEXT | `fail` \| `fallback_free_form` |
| `updated_at` | TEXT | ISO-8601 UTC |

Indexes: `(user_id, created_at DESC)`, `(status)`.

`diagnostics_json` is the only column holding upstream technical detail. It is written for
operators and is **never** returned by the API (`test_session_response_shape`).

### `session_events` — append-only

| Column | Type | Notes |
| --- | --- | --- |
| `event_id` | INTEGER PK AUTOINCREMENT | ordering |
| `session_id` | TEXT FK → `coaching_sessions` | `ON DELETE CASCADE`, enforced (`PRAGMA foreign_keys=ON`) |
| `event_type` | TEXT | dotted name |
| `occurred_at` | TEXT | ISO-8601 UTC |
| `payload_json` | TEXT | JSON object |

UC-01 emits exactly these types:

| `event_type` | When |
| --- | --- |
| `session.initializing` | Immediately after the record is created, before any dependency call |
| `session.dependency_degraded` | Each dependency that came back unavailable or incomplete |
| `session.mode_downgraded` | The requested mode was downgraded to free-form |
| `session.opened` | Session became `active` or `degraded` |
| `session.failed` | Attempt rejected or crashed |

The `session.opened` payload carries the fields downstream analytics will want:

```json
{ "session_type": "free-form", "status": "degraded",
  "naric_level": 5, "naric_level_source": "default",
  "linked_resource_type": null, "linked_resource_id": null }
```

---

## The record-first guarantee

`SessionInitiationService.open_session` writes the record **before** contacting anything:

```
generate session_id
INSERT status='initializing'          ← record now exists no matter what follows
append session.initializing
  ├─ load profile   (failure → degraded, logged, session continues)
  ├─ load NARIC     (failure → Level 5 default, logged, session continues)
  ├─ validate the client's selection server-side
  ├─ load the mode's catalogue and authorise the selection
  ├─ compose the greeting (failure → safe fallback, logged)
  └─ UPDATE status='active' | 'degraded'   /   'failed' on any rejection
```

Consequences, all covered by `tests/test_session_logging.py`:

* a rejected open (409/403) leaves a `failed` record with `failure_code`,
  `degraded_dependencies`, the requested selection and the NARIC level that would have
  applied;
* an unexpected exception leaves a `failed` record and returns a safe 500;
* a repository failure during `create` returns a safe 500 and logs the real error;
* no dependency failure can lose the session.

Live evidence is in [`VERIFICATION.md`](VERIFICATION.md).

---

## Forward compatibility

Fields a future use case may need — `question`, `topic_tag`,
`explain_differently_count`, `rating` — are **not** columns here, because UC-01 does not
produce them. They fit `session_events` without a schema change:

```python
repository.append_event(SessionEvent(
    session_id=session_id,
    event_type="coaching.question_asked",
    occurred_at=clock.now(),
    payload={"question": "...", "topic_tag": "contract-law",
             "explain_differently_count": 2, "rating": 5},
))
```

`test_event_payload_supports_future_use_case_fields` demonstrates the seam. **No UC-07 or
UC-10 behaviour is implemented** — UC-01 only emits its own initiation data.

Everything a later consumer needs from UC-01 is already recorded:
`session_id`, `user_id`, `session_type`, `linked_resource`, `naric_level`,
`naric_level_source`, `timestamp`, `status`.

---

## Limitations

1. **SQLite is single-writer and file-local.** One connection guarded by a lock, WAL mode
   for file databases. Fine for development and CI; not a multi-instance production store.
2. **Not the final schema.** Column names are UC-01's internal vocabulary, not a proposal
   for the company database. Port by writing a new `SessionRepository`, not by copying the
   DDL.
3. **No retention, archival or PII policy.** `user_id` and the learner's course/case
   titles are stored; a real deployment needs a retention decision.
4. **No cross-session analytics.** Read access is deliberately limited to
   `get` / `list_for_user` / `list_events`.
5. **Timestamps are ISO-8601 strings**, not native types — portable, but sorts
   lexicographically (safe with UTC).
6. **`diagnostics_json` holds upstream technical detail.** Treat the database as
   operator-only data.

## Replacing the store

1. Implement `SessionRepository` (e.g. `uc01/persistence/postgres_repository.py`).
2. Register it in `AppContainer._build_repository` under a new `UC01_PERSISTENCE` value.
3. Run `tests/test_persistence.py` against it by adding it to the `repo` fixture's
   `params` — the existing assertions are implementation-agnostic.

No change to `uc01/application` or `uc01/domain` is required.
