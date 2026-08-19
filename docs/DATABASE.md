# Database — schema and company-DB switch-over

Every table is prefixed by the capability that owns it, so this schema can share a database with the
company's own without colliding:

| Prefix | Owner | Tables |
| ------ | ----- | ------ |
| `qb_` | UC-02 Question Bank | `questions`, `question_options`, `topics`, `question_topics`, `question_snapshots`, `question_usages`, `question_imports`, `question_import_errors`, `sequences` |
| `qc_` | UC-01 Quiz Configuration | `courses`, `quizzes`, `configuration_versions`, `configuration_version_question_types`, `configuration_version_topics` |
| `qd_` | UC-03 Attempt Delivery | `attempts`, `attempt_questions`, `attempt_answers`, `attempt_answer_revisions`, `attempt_question_flags`, `attempt_submissions` |
| `qr_` | UC-04 Answer Validation & Scoring | `attempt_results`, `question_scores` |
| `qg_` | UC-05 Pass/Fail & Certificate Gating | `attempt_outcomes`, `certificates`, `cpd_records` |
| `qf_` | UC-06 Detailed Feedback Report | `feedback_reports`, `feedback_items` |
| `qk_` | UC-07 AI Coaching Review Mode | `coaching_sessions`, `coaching_messages`, `knowledge_gaps`, `coaching_activity` |
| `qa_` | Platform placeholder | `users`, `enrolments` |

`qa_users`, `qa_enrolments`, `qc_courses` and `qc_quizzes` are **placeholders** for data the company
system owns — identity, enrolment, the course catalogue, the quiz catalogue. They exist so the
capabilities run end-to-end locally. Nothing in the business rules reads their columns beyond
identity, role, enrolment status and title, so replacing them is a repository or adapter change; see
[INTEGRATION.md](INTEGRATION.md).

`qc_configuration_versions`, `qc_configuration_version_*` and every `qd_`, `qr_`, `qg_`, `qf_` and
`qk_` table are **ours to keep**: they hold configuration history, attempt state, scores, verdicts,
feedback and coaching conversations that no existing company table has.

> **Why `qk_` and not `qc_`.** UC-07 is *coaching*, but `qc_` was already UC-01's quiz
> configuration. Two capabilities sharing a prefix is how a schema stops being self-describing,
> so coaching took the next unambiguous letter.

> **One owner of attempts.** UC-01 shipped its own `qc_attempts` table. It was **dropped** when UC-03
> was merged in, because two records of "did this learner attempt this quiz" would eventually
> disagree. UC-01 still reports attempts used and remaining — it reads them from `qd_attempts` through
> `AttemptStatisticsPort`. An architecture test asserts there is exactly one attempt model.

---

## 1. Switching to the company database

The company database has not been chosen yet. Local development runs on SQLite; nothing in the
application code depends on that choice.

**To switch, change one line and run one command:**

```bash
# backend/.env
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/quizagent
# or  mysql+pymysql://user:pass@host:3306/quizagent
# or  mssql+pyodbc://user:pass@host/quizagent?driver=ODBC+Driver+18+for+SQL+Server
```

```bash
cd backend
pip install psycopg[binary]        # or pymysql / pyodbc — the only extra dependency needed
python -m alembic upgrade head
```

`alembic/env.py` reads the URL from `app.core.config.settings`, so there is no second place to
update. No model, service, router or test needs to change.

### Why the swap is safe

The schema was written to be portable from the start:

| Decision                                                  | Reason |
| --------------------------------------------------------- | ------ |
| Primary keys are application-generated `String(36)` UUID hex | No dependence on a database-specific UUID type or sequence |
| Enum-like columns are `String` validated in the domain layer | No native `ENUM` to migrate; adding a question type is a code change, not a DDL change |
| Frozen structures are `Text` holding JSON, not `JSONB`     | Works identically on all four candidate databases |
| Every constraint and index is explicitly named             | Alembic can alter them by name on any backend; SQLite cannot alter unnamed ones |
| `render_as_batch=True` in `alembic/env.py`                 | SQLite table rebuilds; harmless elsewhere |
| Question references come from a counter table, not `AUTOINCREMENT` | SQLite cannot autoincrement a non-primary-key column |
| Timestamps are `DateTime(timezone=True)`, always written as UTC | SQLite drops the offset; server databases keep it. Values are UTC either way |
| The integrity triggers are emitted per dialect | SQLite and PostgreSQL DDL both ship in the migration, from one definition in `quiz_configuration/models.py` |
| `qc_*` primary keys are integers, `qb_*` are UUID hex | Deliberate: quiz and course ids appear in URLs and will be supplied by the company system, whose keys are most likely integers or opaque strings. Both are portable |

### Optional hardening once the database is known

These are improvements, not requirements — the module is correct without them:

1. **Promote enum-like columns to native enums** (PostgreSQL/MySQL). The vocabularies live in
   `app/modules/question_bank/domain/enums.py`; add an Alembic revision that converts the columns.
   Application code is unaffected because it already writes `.value` strings.
2. **Move snapshot payloads to `JSONB`** (PostgreSQL) if reports need to query inside them.
   `app/modules/question_bank/domain/snapshots.py` is the only place that serialises them.
3. **Add CHECK constraints for the vocabularies.** Deliberately omitted for now: SQLite cannot alter
   a CHECK in place, and adding one per enum would need a table rebuild every time a question type
   is added. The domain validator already rejects invalid values before persistence.
4. **Full-text search.** The list screen currently uses `LIKE` on `question_text` / `scenario_text`.
   Swap for `tsvector` / `MATCH` in `question_service.list_questions` if the bank grows large.
5. **Reconsider `busy_timeout`.** `app/db/session.py` sets `PRAGMA busy_timeout=5000` so a concurrent
   writer waits its turn instead of failing immediately — a SQLite-only concern. A server database
   handles concurrency itself; that hook is already guarded by a dialect check.

### Integrity that must survive the move

Eight triggers enforce guarantees application code must not be the only thing protecting:

| Trigger | Table | Rejects |
| ------- | ----- | ------- |
| `trg_qc_config_version_no_update` | `qc_configuration_versions` | Any `UPDATE` — a version is immutable |
| `trg_qc_config_version_types_no_update` | `qc_configuration_version_question_types` | Any `UPDATE` |
| `trg_qc_config_version_topics_no_update` | `qc_configuration_version_topics` | Any `UPDATE` — a frozen topic scope stays frozen |
| `trg_qr_result_immutable_when_scored` | `qr_attempt_results` | Any `UPDATE` once `status = 'SCORED'` — a confirmed score is final |
| `trg_qr_question_score_no_update` | `qr_question_scores` | Any `UPDATE` — a per-question score is write-once |
| `trg_qg_outcome_no_update` | `qg_attempt_outcomes` | Any `UPDATE` — a determined verdict is a historical fact |
| `trg_qf_report_immutable_when_generated` | `qf_feedback_reports` | Any `UPDATE` once `status = 'GENERATED'` |
| `trg_qf_item_no_update` | `qf_feedback_items` | Any `UPDATE` — a feedback item is write-once |

Each is declared once, in the models of the capability that owns it, and the Alembic migration consumes
those declarations for SQLite **and** PostgreSQL — so a migrated database has the same guarantees as a
`create_all` one. On any other backend the migration runs but skips them; adding a dialect means one
revision that calls `<dialect>_trigger_statements()`. `backend/scripts/verify_e2e.py` proves each one on
a real migrated database by attempting the forbidden `UPDATE` and requiring it to fail, so a missing
guarantee fails loudly rather than quietly.

**Why `UPDATE` only, and never `DELETE`.** Nothing in the application deletes a version, a score, a
verdict or a report, and a blanket `DELETE` trigger would also stop a test database being truncated
between tests — trading a guarantee nobody needs for a suite that cannot run. Deletion is governed by
`ON DELETE RESTRICT` where it matters.

> **A trap worth knowing about.** `batch_alter_table` on SQLite rebuilds the table, which silently
> **drops its triggers**. The revision that added UC-01's presentation columns therefore recreates the
> immutability trigger afterwards, and `tests/test_schema_migration.py` compares the migrated schema
> against the models constraint by constraint — which is how that was caught.

UC-03's equivalent guarantees are constraints rather than triggers, because they are shapes a
constraint can express:

| Constraint | Table | Guarantees |
| ---------- | ----- | ---------- |
| `ux_attempt_single_open` (partial unique) | `qd_attempts` | One open attempt per learner and quiz |
| `ux_attempt_number` | `qd_attempts` | Sequential, gapless attempt numbering |
| `ux_submission_single_success` (partial unique) | `qd_attempt_submissions` | At most one successful submission per attempt |
| `UNIQUE (attempt_id, idempotency_key)` | `qd_attempt_submissions` | A retried submission cannot become a second one |
| lifecycle `CHECK`s | `qd_attempts`, `qd_attempt_answers` | Submitted implies a timestamp; complete implies answered; a timed attempt has an expiry |

---

## 2. Tables

Twenty-nine tables. Foreign keys, unique constraints, indexes and timestamps throughout. Every timestamp
is timezone-aware UTC, enforced at the boundary by `app/db/types.py::UtcDateTime` — a naive datetime
cannot be written at all.

### `qb_questions`

The question itself. Never hard-deleted once it has attempt history.

| Column                                     | Type          | Notes |
| ------------------------------------------ | ------------- | ----- |
| `id`                                       | `String(36)` PK | UUID hex |
| `seq`                                      | `Integer` UNIQUE | Allocated from `qb_sequences` |
| `reference`                                | `String(32)` UNIQUE | `Q-000042`. Human-readable identity, **never reassigned, including after retirement** |
| `external_ref`                             | `String(128)` UNIQUE NULL | Source-system key; blocks accidental re-import |
| `type`                                     | `String(32)`  | `QuestionType` |
| `status`                                   | `String(16)`  | `DRAFT` \| `ACTIVE` \| `RETIRED` |
| `question_text`                            | `Text`        | Required |
| `scenario_text`                            | `Text` NULL   | Required for `SCENARIO`, NULL for every other type |
| `explanation`                              | `Text` NULL   | Required by policy |
| `difficulty`                               | `String(16)` NULL | `EASY` \| `MEDIUM` \| `HARD` |
| `points`                                   | `Float`       | `CHECK points > 0` |
| `scoring_strategy`                         | `String(40)`  | Validated against the type |
| `penalty_per_incorrect`                    | `Float`       | `CHECK >= 0` |
| `version`                                  | `Integer`     | `CHECK >= 1`. Bumped on every content edit |
| `content_hash`                             | `String(64)`  | SHA-256 of normalised content; drives duplicate detection |
| `retired_at` / `retired_reason` / `retired_by` | | Retirement audit trail |
| `created_by` / `updated_by`                | `String(128)` NULL | Actor from the admin guard |
| `created_at` / `updated_at`                | `DateTime(tz)` | |
| `import_id` → `qb_question_imports.id`     | `ON DELETE SET NULL` | CSV provenance |
| `import_row_number`                        | `Integer` NULL | Row it came from |

Indexes: `status`, `type`, `(status, type)`, `content_hash`, `created_at`, `import_id`.

### `qb_question_options`

Answer options (choice types) **and** orderable items (drag-to-order).

| Column             | Type | Notes |
| ------------------ | ---- | ----- |
| `id`               | `String(36)` PK | |
| `question_id`      | FK → `qb_questions.id` `ON DELETE CASCADE` | Options are owned by their question |
| `label`            | `String(32)` | `A`..`D`, `TRUE`/`FALSE`, or an item key |
| `text`             | `Text` | |
| `position`         | `Integer` | **Default presentation order** — `CHECK >= 1` |
| `is_correct`       | `Boolean` | Choice types |
| `is_primary`       | `Boolean` | The single primary answer for `SCENARIO` |
| `correct_position` | `Integer` NULL | **Correct answer order** for `DRAG_TO_ORDER`. `CHECK NULL OR >= 1` |
| `feedback`         | `Text` NULL | Per-option feedback |

Unique: `(question_id, label)`, `(question_id, position)`, `(question_id, correct_position)`.
The last one constrains only ordering questions, because NULLs are distinct in every supported
database. Index on `question_id`.

> `position` and `correct_position` are deliberately separate columns. Conflating them would
> destroy the answer key for drag-to-order questions the moment delivery shuffled the display.

### `qb_topics` / `qb_question_topics`

Relational tagging — never a comma-separated string.

* `qb_topics`: `id`, `slug` UNIQUE, `name` UNIQUE, `description`, `is_active`, timestamps.
* `qb_question_topics`: composite PK `(question_id, topic_id)`, both FKs `ON DELETE CASCADE`,
  plus `assigned_at` / `assigned_by`. Index on `topic_id`.

Slugs make name resolution case- and punctuation-insensitive, so repeated CSV imports converge on
one row per topic instead of creating near-duplicates.

### `qb_question_snapshots`

**The historical-preservation mechanism.** Written once, never updated.

| Column | Notes |
| ------ | ----- |
| `id` | PK |
| `question_id` | FK `ON DELETE CASCADE` |
| `version` | UNIQUE with `question_id` |
| `reference`, `type`, `status`, `question_text`, `scenario_text`, `explanation` | Denormalised so a report renders without joining the live question at all |
| `points`, `scoring_strategy`, `penalty_per_incorrect` | Scoring as it stood |
| `content_hash` | |
| `payload` | `Text` JSON: every option with `label`, `text`, `position`, `isCorrect`, `isPrimary`, `correctPosition`, `feedback`; plus `correctLabels`, `correctOrder`, `primaryLabel` and frozen topic **names** |
| `created_at`, `created_by` | |

Freezing topic *names* is what makes a report survive a topic rename or deletion.

### `qb_question_usages`

Links an attempt to the snapshot it was actually delivered.

| Column | Notes |
| ------ | ----- |
| `id` | PK |
| `attempt_ref` | `String(128)` — **opaque id owned by the caller. Deliberately NOT a foreign key** (see [INTEGRATION.md](INTEGRATION.md)). UC-03 writes its own `qd_attempts.id` here when it reports a delivery |
| `learner_ref` | `String(128)` NULL |
| `question_id` | FK **`ON DELETE RESTRICT`** — the database itself refuses to destroy a question with history |
| `snapshot_id` | FK **`ON DELETE RESTRICT`** |
| `snapshot_version` | The version delivered |
| `delivery_position` | `Integer` NULL — 1-based position within the attempt, fixed at creation. This is what stops a learner's question order shifting mid-attempt |
| `attempt_status` | `IN_PROGRESS` \| `COMPLETED` \| `ABANDONED` |
| `learner_response` | `Text` JSON, shaped per type |
| `presentation_order` | `Text` JSON array — the order actually **shown**, kept separate from the answer key |
| `is_correct`, `awarded_points`, `max_points` | Score information |
| `delivered_at`, `responded_at`, `completed_at` | |

Unique `(attempt_ref, question_id)` and `(attempt_ref, delivery_position)` — NULLs are distinct in
every supported database, so the second constrains only callers that order their questions. Indexes
on `question_id`, `attempt_ref`, `attempt_status`.

### `qb_question_imports` / `qb_question_import_errors`

CSV run summary and per-row rejection reasons.

* `qb_question_imports`: `filename`, `status` (`PROCESSING`/`COMPLETED`/`FAILED`), `total_rows`,
  `imported_rows`, `rejected_rows` (all `CHECK >= 0`), `error_message` for whole-file failures,
  `created_by`, `started_at`, `completed_at`.
* `qb_question_import_errors`: `import_id` FK `ON DELETE CASCADE`, `row_number` (spreadsheet row —
  header is row 1, `0` means the whole file), `field`, `code`, `message`, `raw_row` JSON.
  Indexes on `import_id` and `(import_id, row_number)`.

Multiple errors per row are stored, so an admin sees every problem with a row at once.

### `qb_sequences`

`name` PK, `value`, `updated_at`. Portable monotonic counter for question references, incremented
inside the same transaction that creates the question.

---

### `qa_users`

Placeholder identity directory. The company's identity provider replaces it.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `Integer` PK | |
| `email` | `String(255)` unique | |
| `display_name` | `String(255)` | |
| `role` | `String(16)` | `admin` \| `learner`, CHECK-constrained |
| `api_token` | `String(128)` unique | **Development credential only** |
| `created_at` | `DateTime(tz)` | |

### `qc_courses` / `qc_quizzes`

Placeholder catalogue. `qc_quizzes.active_configuration_version_id` is a forward pointer to the
newest configuration version, set in the same transaction that creates it, with `ON DELETE RESTRICT`
so it cannot dangle. `UNIQUE (course_id, slug)`.

### `qc_configuration_versions`

An immutable snapshot of every setting needed to run the quiz. **Rows are never updated** — a
configuration change inserts the next version and repoints the quiz.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `Integer` PK | |
| `quiz_id` | FK → `qc_quizzes` | `CASCADE` |
| `version_number` | `Integer` | `UNIQUE (quiz_id, version_number)`, `>= 1`. Gapless: allocated inside the insert's transaction |
| `question_count` | `Integer` | CHECK 1–100 |
| `time_limit_minutes` | `Integer` nullable | CHECK 1–480 when set; NULL means no limit |
| `pass_mark` | `Integer` | CHECK 1–100 |
| `randomise_questions` | `Boolean` | |
| `max_attempts` | `Integer` | CHECK 1–50 |
| `delivery_mode` | `String(32)` | CHECK `practice` \| `assessment` \| `exam` |
| `settings_fingerprint` | `String(64)` | SHA-256 of the canonical settings; distinguishes a real change from a no-op re-save |
| `created_by_user_id` | FK → `qa_users` nullable | `RESTRICT` |
| `created_by` | `String(128)` nullable | Audit label; survives the placeholder user table |
| `created_at` | `DateTime(tz)` | |

Protected by `trg_qc_config_version_no_update`.

### `qc_configuration_version_question_types`

Which types a version selected, and how many of each.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `configuration_version_id` | FK → version, PK part | `CASCADE` |
| `question_type` | `String(32)`, PK part | CHECK: one of the five |
| `question_quota` | `Integer` nullable | NULL = draw freely across the selected types |
| `position` | `Integer` | CHECK `>= 1` |

Protected by `trg_qc_config_version_types_no_update`.

### `qc_configuration_version_topics`

The optional topic scope, **frozen** onto the version.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `configuration_version_id` | FK → version, PK part | `CASCADE` |
| `topic_id` | `String(36)`, PK part | References `qb_topics.id`, deliberately **not** a foreign key |
| `topic_slug` / `topic_name` | `String` | Frozen at save time, so a rename or deletion cannot rewrite a past version |
| `position` | `Integer` | CHECK `>= 1` |

Protected by `trg_qc_config_version_topics_no_update`.

### `qa_enrolments`

Placeholder enrolment, behind `EnrolmentPort`. UC-03 refuses to create an attempt for a learner who is
not actively enrolled on the course.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `learner_id` | `String(128)` PK | Opaque — UC-03 never parses it |
| `course_id` | `String(128)` PK | Opaque |
| `status` | `String(32)` | CHECK `ACTIVE` \| `SUSPENDED` \| `WITHDRAWN` \| `COMPLETED` |
| `enrolled_at` | `DateTime(tz)` | |

`ACTIVE` and `COMPLETED` permit an attempt; the other two do not. That policy lives in
`app/modules/identity/enums.py::ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES`, in one place, so the adapter
does not restate it.

### `qd_attempts`

A learner's attempt, permanently locked to one configuration version — **by value, not only by
reference**: `configuration_snapshot` holds the full rules, so the attempt is readable even if the
version is later withdrawn.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | UUID. Also the randomisation seed, and the `attempt_ref` UC-02 records |
| `learner_id`, `course_id`, `quiz_id` | `String(128)` | Opaque ids owned by other capabilities |
| `configuration_version_id` | `String(128)` | Opaque — deliberately **not** a foreign key (see below) |
| `configuration_version_number` | `Integer` | For display and diagnostics |
| `configuration_snapshot` | `JSON` | The locked rules, complete |
| `attempt_number` | `Integer` | `UNIQUE (learner_id, quiz_id, attempt_number)`, `>= 1` |
| `status` | `String(32)` | CHECK `ACTIVE` \| `SUBMITTED` \| `SUBMISSION_PENDING` \| `EXPIRED` \| `ABANDONED` |
| `question_presentation` | `String(24)` | CHECK `ONE_AT_A_TIME` \| `ALL_AT_ONCE` — locked at creation |
| `selection_seed` | `String(64)` | Makes the question draw reproducible from the row alone |
| `total_questions`, `current_position` | `Integer` | The paper size and the resume cursor |
| `time_limit_seconds` | `Integer` nullable | Locked from the configuration |
| `started_at` | `DateTime(tz)` | |
| `expires_at` | `DateTime(tz)` nullable | Computed once, from the server clock |
| `submitted_at`, `finalised_at` | nullable | |
| `submission_reason` | `String(32)` nullable | CHECK `LEARNER_CONFIRMED` \| `TIME_EXPIRED` |
| `last_activity_at` | `DateTime(tz)` | |

Indexes: `(learner_id, quiz_id)`, `(status, expires_at)` for the expiry sweep, plus the two unique
indexes above.

**Why `configuration_version_id` is not a foreign key.** UC-03 treats every cross-capability id as
opaque, so it can be deployed against a different configuration service without a schema change. The
guarantee that matters — "a configuration change cannot alter a running attempt" — is provided by
`configuration_snapshot`, which no other capability can reach. See [INTEGRATION.md](INTEGRATION.md).

### `qd_attempt_questions`

The frozen question, exactly as delivered. This is what makes an attempt survive editing or retiring
its questions.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `attempt_id` | FK → `qd_attempts` | `CASCADE` |
| `question_id` | `String(128)` | Opaque bank id |
| `question_version` | `Integer` | The version delivered |
| `question_type` | `String(32)` | One of the five |
| `position` | `Integer` | `UNIQUE (attempt_id, position)` and `UNIQUE (attempt_id, question_id)` — a question cannot occupy two positions, or appear twice |
| `points` | `Float` | Locked |
| `question_snapshot` | `JSON` | The complete structure, answer key included — **never presented** |

### `qd_attempt_answers`

One row per delivered question, created on first save. `UNIQUE (attempt_id, attempt_question_id)`.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `response` | `JSON` nullable | The learner's answer in its type's shape; `NULL` means cleared |
| `answered` | `Boolean` | Anything was given |
| `complete` | `Boolean` | Enough was given — CHECK complete implies answered |
| `revision` | `Integer` | Advances only on a genuine change; the basis of `expectedRevision` |
| `source` | `String(16)` | `MANUAL` \| `AUTOSAVE` — so an autosave stays distinguishable |
| `saved_at` | `DateTime(tz)` | |

### `qd_attempt_answer_revisions`

Append-only. One row per **accepted** save, so an operator can confirm an autosave landed and
reconstruct a learner's progress. Never updated, never deleted while the attempt exists.

### `qd_attempt_question_flags`

`UNIQUE (attempt_id, attempt_question_id)`. Flagging is idempotent and preserves the original instant;
unflagging deletes the row.

### `qd_attempt_submissions`

The submission ledger, and the reason a double-click cannot submit twice.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `attempt_id` | FK → `qd_attempts` | `CASCADE` |
| `idempotency_key` | `String(128)` | `UNIQUE (attempt_id, idempotency_key)` |
| `state` | `String(16)` | CHECK `PENDING` \| `SUBMITTED` \| `FAILED` |
| `reason` | `String(32)` nullable | Why it was submitted |
| `downstream_reference` | `String(128)` nullable | What the marking service returned |
| `attempts` | `Integer` | Retry count |
| `error_code`, `error_message` | nullable | The downstream failure, for diagnosis |

`ux_submission_single_success` is a **partial** unique index on `attempt_id WHERE state =
'SUBMITTED'`: many `PENDING` or `FAILED` rows may exist for one attempt, but only ever one success.

### `qr_attempt_results`

UC-04's result: one row per attempt, forever. `UNIQUE (attempt_id)` is what makes scoring idempotent
under a race — a second concurrent run loses the insert and adopts the winner's row.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `attempt_id` | `String(36)` **UNIQUE** | Soft reference to `qd_attempts.id` |
| `submission_id` | `String(36)` nullable | The successful submission, when there is one |
| `learner_id`, `course_id`, `quiz_id` | `String(64)` | Soft references, indexed |
| `attempt_number` | `Integer` | |
| `configuration_version_id` | `String(64)` | The version the attempt was **locked to** |
| `configuration_version_number` | `Integer` | |
| `pass_mark_percentage` | `Float` | Frozen from that version, so UC-05 need not re-resolve it |
| `status` | `String(24)` | CHECK `PENDING_SCORE` \| `SCORED` |
| `total_marks`, `maximum_marks`, `percentage` | `Float` | CHECK: non-negative, total ≤ maximum, 0–100 |
| `total_questions`, `correct_count`, `incorrect_count`, `unanswered_count` | `Integer` | |
| `started_at`, `submitted_at` | UTC nullable | Copied from UC-03's server-authoritative stamps |
| `time_taken_seconds` | `Integer` nullable | |
| `anomalies` | JSON nullable | `[{code, questionId?, position?}]` — why a result is pending |
| `failure_code`, `failure_message` | nullable | The first anomaly, for a support screen |
| `scoring_attempt_count` | `Integer` | Runs so far; a retry increments it rather than adding a row |
| `algorithm_version` | `Integer` | Which version of the marking rules produced it |
| `scored_at` | UTC nullable | CHECK `(status = 'SCORED') = (scored_at IS NOT NULL)` |

`trg_qr_result_immutable_when_scored` rejects every `UPDATE` once `status = 'SCORED'`. A confirmed
score is therefore immutable in the database, not merely in the service.

### `qr_question_scores`

One row per delivered question, written once. Carries a frozen copy of the question text, the learner's
answer, the correct answer and the per-option mark contributions, so UC-06 can build a report without
reading UC-02 or UC-03 again.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `result_id` | FK → `qr_attempt_results` | `CASCADE` |
| `attempt_question_id`, `question_id` | soft references | The frozen UC-03 row and UC-02 question |
| `question_version` | `Integer` | The version marked — the answer key came from this one |
| `question_type` | `String(24)` | CHECK: one of the five |
| `position` | `Integer` | `UNIQUE (result_id, position)`; also `UNIQUE (result_id, question_id)` |
| `awarded_marks`, `maximum_marks`, `raw_marks`, `deduction` | `Float` | CHECK: awarded ≤ maximum, deduction ≥ 0 |
| `outcome` | `String(24)` | CHECK `CORRECT` \| `PARTIALLY_CORRECT` \| `INCORRECT` \| `UNANSWERED` \| `NOT_SCORED` |
| `answered` | `Boolean` | CHECK: an `UNANSWERED`/`NOT_SCORED` question carries zero marks |
| `question_text`, `scenario_text`, `explanation` | `Text` | Frozen for the report |
| `learner_answer`, `learner_answer_display`, `correct_answer_display`, `option_marks`, `topics` | JSON | Frozen for the report |
| `anomaly`, `answer_key_source` | nullable | Why it could not be scored; which frozen copy of the key was used |

`trg_qr_question_score_no_update` rejects every `UPDATE`. A re-run of a *pending* result deletes and
re-inserts these rows; a confirmed one can do neither.

### `qg_attempt_outcomes`

UC-05's verdict: `UNIQUE (attempt_id)`, and `trg_qg_outcome_no_update` rejects every `UPDATE`. It is a
derived fact about an immutable score, so it has no legitimate reason to change.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `attempt_id` | `String(36)` **UNIQUE** | |
| `result_id` | `String(36)` | The UC-04 result it was derived from |
| `learner_id`, `course_id`, `quiz_id`, `attempt_number`, `configuration_version_id` | | Soft references |
| `outcome` | `String(8)` | CHECK `PASS` \| `FAIL` |
| `percentage`, `pass_mark_percentage`, `total_marks`, `maximum_marks` | `Float` | The pass mark is the **attempt's own** |
| `attempts_used_at_outcome`, `max_attempts`, `attempts_remaining_at_outcome` | `Integer` nullable | The audit copy; the live figure is recomputed on read |
| `certificate_required` | `Boolean` | CHECK `(outcome = 'PASS') = (certificate_required = 1)` |
| `determined_at`, `created_at` | UTC | |

### `qg_certificates`

One request per attempt, and **at most one issued certificate per learner and quiz**.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `attempt_id` | `String(36)` **UNIQUE** | Makes "request a certificate" idempotent |
| `outcome_id` | `String(36)` | |
| `learner_id`, `course_id`, `quiz_id` | `String(64)` | |
| `course_name`, `quiz_title` | `String(255)` | Frozen, so a rename cannot rewrite an issued certificate |
| `percentage` | `Float` | |
| `status` | `String(16)` | CHECK `PENDING` \| `ISSUED` \| `FAILED` |
| `certificate_number` | `String(64)` nullable **UNIQUE** | CHECK: an `ISSUED` row always has one |
| `document_reference`, `metadata_payload` | nullable | What the certificate service returned |
| `generation_attempt_count` | `Integer` | So a permanently failing certificate is visible |
| `failure_code`, `failure_message` | nullable | |
| `requested_at`, `last_attempted_at`, `issued_at` | UTC | CHECK `(status = 'ISSUED') = (issued_at IS NOT NULL)` |

`ux_qg_certificate_single_issued` is a **partial** unique index on `(learner_id, quiz_id) WHERE status
= 'ISSUED'`. That is the duplicate-prevention guarantee: two concurrent issue calls cannot both win,
and a learner who passes a second time cannot acquire a second document.

### `qg_cpd_records`

One CPD record per attempt, carrying the four facts the CPD system is given.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `attempt_id` | `String(36)` **UNIQUE** | A retried synchronisation updates this row rather than logging twice |
| `outcome_id` | `String(36)` | |
| `attempt_date` | UTC | **Fact 1** |
| `score_percentage` | `Float` | **Fact 2**, CHECK 0–100 |
| `passed` | `Boolean` | **Fact 3** |
| `course_name` | `String(255)` | **Fact 4**, frozen |
| `total_marks`, `maximum_marks` | `Float` | Context for the score |
| `status` | `String(16)` | CHECK `PENDING` \| `SYNCHRONISED` \| `FAILED` |
| `external_reference` | `String(200)` nullable | What the CPD system returned |
| `sync_attempt_count`, `failure_code`, `failure_message` | | |
| `requested_at`, `last_attempted_at`, `synchronised_at` | UTC | CHECK `(status = 'SYNCHRONISED') = (synchronised_at IS NOT NULL)` |

### `qf_feedback_reports`

UC-06's report: `UNIQUE (attempt_id)`, and `trg_qf_report_immutable_when_generated` rejects every
`UPDATE` once `status = 'GENERATED'`.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `attempt_id` | `String(36)` **UNIQUE** | |
| `result_id`, `outcome_id` | `String(36)` / nullable | UC-04's score and UC-05's verdict |
| `learner_id`, `course_id`, `quiz_id`, `attempt_number` | | Soft references |
| `status` | `String(16)` | CHECK `PENDING` \| `GENERATED` \| `FAILED` |
| `total_marks`, `maximum_marks`, `percentage`, `pass_mark_percentage` | `Float` | |
| `passed` | `Boolean` nullable | NULL when pass/fail had not been determined — reported, never guessed |
| `time_taken_seconds`, `total_questions`, `correct_count`, `incorrect_count`, `unanswered_count` | `Integer` | |
| `payload` | JSON nullable | **The rendered report**, served verbatim. CHECK: a `GENERATED` row has one |
| `generation_attempt_count`, `failure_code`, `failure_message` | | |
| `generated_at` | UTC nullable | CHECK `(status = 'GENERATED') = (generated_at IS NOT NULL)` |

### `qf_feedback_items`

One row per question, written once — `trg_qf_item_no_update` rejects every `UPDATE`. The same content
as the payload, field by field, so a report is queryable in SQL rather than only readable as JSON.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `report_id` | FK → `qf_feedback_reports` | `CASCADE` |
| `position` | `Integer` | `UNIQUE (report_id, position)`; also `UNIQUE (report_id, question_id)` |
| `question_id`, `question_version`, `question_reference`, `question_type` | | |
| `question_text`, `scenario_text` | `Text` | |
| `explanation` | `Text` **NOT NULL** | The authored text, or the defined fallback — never empty |
| `lesson_reference` | `String(255)` **NOT NULL** | Same rule |
| `learner_answer`, `correct_answer`, `option_breakdown` | JSON | `option_breakdown` carries each option's correct/incorrect status and its mark contribution |
| `question_score`, `maximum_marks`, `deduction` | `Float` | CHECK: score ≤ maximum |
| `outcome`, `answered` | | |

### `qk_coaching_sessions`

UC-07's coaching conversation about one incorrectly answered question.

The load-bearing constraint is **`UNIQUE (learner_id, attempt_id, question_id)`**. "Starting coaching
twice resumes the same conversation" is a claim about concurrency, and a read-then-write check in the
service cannot make it true — two simultaneous requests both read "no session" and both insert. The
constraint decides it, the loser is caught as `DUPLICATE_COACHING_SESSION`, and the service reads the
winner.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | Generated, and *not* what makes a session unique |
| `learner_id`, `attempt_id`, `course_id`, `question_id` | | Soft references; **`UNIQUE (learner_id, attempt_id, question_id)`** |
| `question_position` | `Integer` nullable | Delivery position, so a review queue orders without re-reading UC-03 |
| `topic` | `String(255)` nullable | A label, never content. NULL when the question is untagged — recorded as-is rather than invented |
| `status` | `String(16)` | CHECK `ACTIVE` \| `COMPLETED` \| `FAILED` \| `UNAVAILABLE` |
| `mode` | `String(24)` | CHECK `SOCRATIC` \| `DIRECT_EXPLANATION` |
| `exchange_count` | `Integer` | One learner message answered by one coach reply. A failed turn does not count |
| `direct_explanation_threshold` | `Integer` | Copied at creation, so changing the setting cannot move the goalposts under a running conversation |
| `direct_explanation_offered` | `Boolean` | Recorded so the offer is made once |
| `consecutive_failures`, `last_failure_code` | | A code from UC-07's taxonomy — never a provider message |
| `revision` | `Integer` | Advances on every stored transition, for optimistic concurrency |
| `started_at`, `updated_at` | UTC | |
| `completed_at` | UTC nullable | CHECK `(status = 'COMPLETED') = (completed_at IS NOT NULL)` |

### `qk_coaching_messages`

The conversation. Append-only: `trg_qk_message_no_update` rejects every `UPDATE`, so "nothing rewrites
what a learner said" holds against raw SQL and not only against the service.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `session_id` | FK → `qk_coaching_sessions` | `CASCADE` |
| `message_index` | `Integer` | `UNIQUE (session_id, message_index)`. Assigned by the service, so replay order never depends on clock skew |
| `role` | `String(16)` | CHECK `LEARNER` \| `COACH`. **There is no `SYSTEM` role** — the coaching policy is assembled per request and never stored, so it cannot be edited, replayed or injected through the history |
| `content` | `Text` | |
| `mode` | `String(24)` nullable | The mode the coach was in. NULL for learner messages |
| `created_at` | UTC | |

### `qk_knowledge_gaps`

One topic a learner may need to revisit, written when a coaching session is created.

`UNIQUE (session_id)` is the idempotency: a learner who spends twenty turns on one question has one
knowledge gap in one topic, and counting their persistence as twenty would make the dataset actively
misleading. Not a foreign key to the session — the gap is an analytics fact about the learner and
outlives any decision to prune conversations under a retention policy.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `session_id` | `String(36)` **UNIQUE** | |
| `learner_id`, `attempt_id`, `course_id`, `question_id` | | Soft references |
| `topic` | `String(255)` nullable | NULL when the question carries no topic in UC-03 or UC-06. An untagged question is a content problem worth seeing in the data; inventing "General" would hide it |
| `source` | `String(48)` | Currently always `COACHING_SESSION_STARTED`; explicit so a future source is distinguishable |
| `occurred_at`, `created_at` | UTC | |

### `qk_coaching_activity`

The coaching lifecycle, as an audit stream. Append-only — `trg_qk_activity_no_update` — because an
audit record that could be edited is not an audit record.

Identifiers, counts, statuses and codes only. There is **no column capable of holding** an answer key,
a correct answer or the conversation, which is a stronger guarantee than a rule saying not to write
them. The conversation is *state*, kept in `qk_coaching_messages` because the next request needs it; it
is not activity, and it does not belong in a stream that gets fanned out to dashboards.

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | `String(36)` PK | |
| `event_type` | `String(32)` | CHECK: `SESSION_STARTED`, `EXCHANGE_COMPLETED`, `DIRECT_EXPLANATION_OFFERED`, `MODE_CHANGED`, `SESSION_COMPLETED`, `SESSION_FAILED`, `SESSION_RETRIED` |
| `session_id`, `learner_id`, `attempt_id`, `question_id` | | Soft references |
| `course_id`, `topic`, `mode`, `status` | nullable | |
| `exchange_count` | `Integer` | |
| `failure_code` | `String(64)` nullable | UC-07's code for a `SESSION_FAILED` event |
| `occurred_at`, `created_at` | UTC | |

> **What UC-07 deliberately does not store.** No coaching *context*. The sanitised material handed to
> the model is rebuilt on every turn and never persisted, so there is no representation of a question —
> safe or otherwise — sitting in UC-07's storage waiting to be found. And no answer key: none of the
> four tables above has a column that could hold one, and an integration test asserts that by reading
> the schema.

---

## 3. Referential integrity summary

| Relationship                | On delete   | Consequence |
| --------------------------- | ----------- | ----------- |
| option → question           | `CASCADE`   | Options are owned by the question |
| topic link → question/topic | `CASCADE`   | Untagging is safe; snapshots preserve history |
| snapshot → question         | `CASCADE`   | Only reachable when there is no usage |
| **usage → question**        | `RESTRICT`  | **A question with history can never be destroyed** |
| **usage → snapshot**        | `RESTRICT`  | **A delivered snapshot can never be destroyed** |
| question → import           | `SET NULL`  | Purging import logs does not touch questions |
| quiz → course               | `CASCADE`   | A quiz belongs to its course |
| configuration version → quiz | `CASCADE`  | Versions belong to their quiz |
| coaching message → session  | `CASCADE`   | The conversation is owned by its session |
| version question type / topic → version | `CASCADE` | Owned by the version |
| **quiz.active_configuration_version_id → version** | `RESTRICT` | The active version cannot be deleted out from under a quiz |
| configuration version → user | `RESTRICT` | An author who created a version cannot be purged silently |
| attempt question / answer / flag / submission → attempt | `CASCADE` | Owned by the attempt |
| answer revision → attempt   | `CASCADE`   | The audit trail lives as long as the attempt |
| question score → attempt result | `CASCADE` | Owned by the result |
| feedback item → feedback report | `CASCADE` | Owned by the report |

`qc_configuration_version_topics.topic_id` references a question-bank topic **without** a foreign
key, and freezes the topic's slug and name alongside it. This mirrors how question snapshots freeze
topic names: a version must keep meaning what it meant when written, so renaming or deleting a topic
later cannot rewrite history.

`qb_question_usages.attempt_ref` likewise has no foreign key. UC-03 writes its own attempt id there
when it reports a delivery, so the two are joinable — but the bank stays deployable without an attempt
service, which is the point. See [INTEGRATION.md](INTEGRATION.md) for why, and for the revision that
would add the FK once both live in one database for good.

UC-03's own cross-capability columns (`quiz_id`, `course_id`, `learner_id`,
`configuration_version_id`) are opaque strings with no foreign keys, for the same reason and by the
same deliberate choice. So are UC-04's, UC-05's and UC-06's `attempt_id`, `result_id`, `outcome_id` and
`question_id`: a score, a verdict and a report must survive the rows they refer to being superseded, and
a cascading foreign key would defeat exactly the guarantee those tables exist to hold. Referential
correctness is enforced at *write* time through the ports, and at *read* time by never needing the
external row again — every one of these tables carries its own frozen copy of what it reports.

SQLite disables foreign-key enforcement per connection, so `app/db/session.py` issues
`PRAGMA foreign_keys=ON` on every connect. A test asserts this, because the `RESTRICT` guarantees
depend on it.

---

## 4. Migrations

Four revisions, applied in order:

| Revision | Creates |
| -------- | ------- |
| `5ea5d718773d_initial_question_bank_schema` | the `qb_*` tables |
| `998d713ed495_quiz_configuration_schema` | `qa_users`, the `qc_*` tables, `qb_question_usages.delivery_position`, and the integrity triggers |
| `f2edce6a1ae0_attempt_delivery_schema` | the `qd_*` tables, `qa_enrolments`, UC-01's three presentation columns — and **drops `qc_attempts`**, because UC-03 now owns attempts |
| `2839992033cb_uc04_uc05_uc06_results_chain_schema` | the `qr_*`, `qg_*` and `qf_*` tables, and the five immutability triggers over them |

```bash
python -m alembic upgrade head       # apply
python -m alembic downgrade base     # roll back (drops every table it created)
python -m alembic revision --autogenerate -m "…"   # after a model change
```

Two of the revisions are **hand-adjusted after autogeneration**, for reasons that matter on a real
server database:

1. `qc_quizzes` and `qc_configuration_versions` reference each other. Autogenerate inlined the
   forward reference into `CREATE TABLE qc_quizzes`, which SQLite tolerates but PostgreSQL and SQL
   Server reject because the target does not exist yet. The pointer is added as a separate
   constraint once both tables exist.
2. Autogenerate does not emit the triggers, because they hang off SQLAlchemy `after_create` events
   rather than being table constraints.
3. Autogenerate does not emit a `CHECK` constraint for an **added** column either. The revision that
   added `question_presentation` therefore adds its constraint explicitly; without it a migrated
   database would accept a value the ORM rejects.
4. `batch_alter_table` rebuilds a SQLite table, which silently drops its triggers. The same revision
   recreates the immutability trigger afterwards.

Every one of those four was found by a test, not by review.

`tests/test_schema_migration.py` runs `alembic upgrade head` on a throwaway database and compares the
result against the **complete** ORM metadata (`app/db/metadata.py`, which registers every capability's
tables) **table by table and constraint by constraint** — columns, nullability, indexes, unique
constraints, foreign keys, check-constraint names and primary keys. It also asserts the constraints
that carry a business rule by name, and that `downgrade base` leaves no table behind. A stronger check
than a bare `compare_metadata` diff, and it is what caught points 3 and 4 above.

`alembic/env.py` honours an explicitly configured `sqlalchemy.url` in preference to `settings`, so a
one-off migration against a named database — or that drift test — is not silently redirected at
whatever the environment happens to hold.

Adding a module's tables means importing its models in `app/db/metadata.py`; a half-registered module
would otherwise surface as a confusing "no such table" rather than an import error.
