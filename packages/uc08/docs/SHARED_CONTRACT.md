# Shared contract — UC-08 Learning Streaks & Milestones

For an integration engineer who has not read this code and is holding a
component they have never seen. It describes what UC-08 **emits**, what it
**expects to receive**, the vocabularies it uses, how it handles session
identity, and every point where behaviour it does not own attaches.

`README.md` describes how to work on this repository. This describes what this
component says to a platform it cannot see.

Every field is marked:

- **specified** — fixed by the platform contract in the UC-08 scope document.
- **assumed** — invented here because the company has not specified it. The
  `A-nn` reference points to a row in [`assumptions.md`](./assumptions.md).

---

## 1. What this component is

UC-08 reads coaching activity and writes three things: a **streak record**, a
set of **milestone badges**, and **weekly summaries**. It contains no AI. It
creates no coaching session, answers no question, analyses no gap, and generates
no feedback.

It knows nothing about any sibling component. Everything it needs from outside
itself arrives through one of nine ports (§6).

Two invariants matter more than any other and are worth stating up front:

1. **A streak is never reset by a failure.** A reset is reachable only from a
   genuine inactivity determination. A failed write preserves the last known
   count and pages engineering. A failed activity read preserves the count too.
2. **No endpoint accepts a user identifier.** The account is resolved
   server-side. There is no path segment, query parameter or body field a
   learner can change to reach another learner's data, and an attempt to send
   one is a `422`, not a silent ignore.

---

## 2. Records this component writes

### 2.1 Streak record

One per account. This component owns it.

| Field | Type | Provenance | Notes |
|---|---|---|---|
| `user_id` | `string` | **specified** | The account. Never a device or session. |
| `current_streak_days` | `integer >= 0` | **specified** | `0` only in the read model for an account with no record (A-26); a persisted record is always `>= 1`. |
| `longest_streak_days` | `integer >= 0` | **specified** | High-water mark. Never below `current_streak_days` — enforced by a validator. Survives every reset. |
| `last_activity_at` | `datetime (UTC) \| null` | **specified** | The last interaction this component counted. |
| `streak_started_at` | `datetime (UTC) \| null` | **specified** | Start of the current run. Re-baselined on a reset. |
| `freeze_available` | `boolean` | **specified** | True when no freeze has been *used* in the current UTC calendar month (A-11, A-13). Recomputed on every write. |
| `freeze_used_at` | `datetime (UTC) \| null` | **specified** | When a freeze was last accepted. |
| `updated_at` | `datetime (UTC)` | **specified** | Last write. |

There are no other fields. A request that tries to supply any of them is
rejected.

```json
{
  "user_id": "learner-7781",
  "current_streak_days": 8,
  "longest_streak_days": 11,
  "last_activity_at": "2026-03-10T12:00:00Z",
  "streak_started_at": "2026-03-03T09:14:00Z",
  "freeze_available": false,
  "freeze_used_at": "2026-03-10T12:00:00Z",
  "updated_at": "2026-03-10T12:00:00Z"
}
```

### 2.2 Badge record

Append-only. Permanent. There is no removal method on the repository port and no
code path in this component that deletes, revokes or expires a badge.

| Field | Type | Provenance | Notes |
|---|---|---|---|
| `badge_id` | `string` | **assumed** (A-21) | `badge-<milestone>-<user_id>`. Derived, so a duplicate is unrepresentable. |
| `user_id` | `string` | **specified** | |
| `milestone` | `10 \| 50 \| 100` | **specified** | The configured set; `BADGE_MILESTONES` can change it. |
| `awarded_at` | `datetime (UTC)` | **specified** | The first award stands; a repeat never rewrites it. |
| `question_count_at_award` | `integer >= 0` | **specified** | The lifetime count read at award time. |

### 2.3 Weekly summary record

One per `(account, ISO week)`. Written **before** any delivery is attempted, so
a delivery failure loses the send, never the record.

| Field | Type | Provenance | Notes |
|---|---|---|---|
| `summary_id` | `string` | **assumed** (A-21) | `ws-<user_id>-<week>`. |
| `user_id` | `string` | **specified** | |
| `week` | `string` | **assumed** (A-14) | ISO week key, `GGGG-Www`, e.g. `2026-W11`. Sorts lexically. |
| `week_start_at` | `datetime (UTC)` | **assumed** (A-14) | 00:00:00Z on that Monday. |
| `week_end_at` | `datetime (UTC)` | **assumed** (A-14) | Exclusive; 00:00:00Z the following Monday. |
| `generated_at` | `datetime (UTC)` | **assumed** | |
| `topics_covered` | `string[]` | **specified** | Element 1 of the four. Topics whose first mention fell inside the week. |
| `topics_status` | `SourceStatus` | **specified** (vocabulary) | `available`, `empty`, `partial`, `unavailable` or `invalid`. |
| `questions_asked` | `integer >= 0` | **specified** | Element 2. Count of interactions in the week (A-17). |
| `questions_asked_status` | `SourceStatus` | **specified** (vocabulary) | |
| `current_streak_days` | `integer >= 0` | **specified** | Element 3, read at generation time. |
| `suggested_topic` | `Topic \| null` | **specified** | Element 4, from the gap report port. `null` when the report had nothing or could not answer — never invented. |
| `suggested_topic_status` | `SourceStatus` | **specified** (vocabulary) | `empty` = the report answered with nothing. `unavailable` = it did not answer. Never conflated. |
| `omissions` | `string[]` | **assumed** | Names of elements deliberately left out, e.g. `["suggested_topic"]`. |
| `omission_notes` | `string[]` | **assumed** | Human-readable reasons, e.g. *"suggested topic omitted: the gap report was unavailable. No suggestion was invented."* |
| `delivery_status` | `pending \| sent \| failed` | **assumed** | |
| `send_attempts` | `integer >= 0` | **assumed** | |
| `last_send_attempt_at` | `datetime (UTC) \| null` | **assumed** | |
| `sent_at` | `datetime (UTC) \| null` | **assumed** | |
| `next_retry_at` | `datetime (UTC) \| null` | **assumed** (A-19) | Next UTC midnight after a failure. |
| `skipped_weeks` | `string[]` | **assumed** (A-20) | ISO weeks that went by without a generation call. **Named, never generated.** |

### 2.4 Freeze offer record

Internal to UC-08 today (A-10). Exposed read-only on the streak endpoints so a
frontend can render the offer.

| Field | Type | Provenance | Notes |
|---|---|---|---|
| `offer_id` | `string` | **assumed** (A-21) | |
| `user_id` | `string` | **assumed** | |
| `status` | `offered \| accepted \| declined \| expired` | **assumed** (A-10) | |
| `offered_at` | `datetime (UTC)` | **assumed** | |
| `expires_at` | `datetime (UTC)` | **assumed** (A-12) | `offered_at + FREEZE_OFFER_EXPIRY_HOURS`, default 24h. |
| `preserved_streak_days` | `integer >= 1` | **assumed** (A-18) | The streak held before the missed day. |
| `preserved_streak_started_at` | `datetime (UTC) \| null` | **assumed** | |
| `answered_at` | `datetime (UTC) \| null` | **assumed** | |

---

## 3. Events this component emits

UC-08 renders nothing. It emits events for a caller to render, through
`NotificationSink`.

### 3.1 `badge_awarded`

One per award. Emitted after the badge is persisted, so a failed notification
never loses a badge.

```json
{
  "event_id": "evt-badge-50-learner-7781",
  "event_type": "badge_awarded",
  "user_id": "learner-7781",
  "badge_id": "badge-50-learner-7781",
  "milestone": 50,
  "question_count_at_award": 60,
  "awarded_at": "2026-03-10T12:00:00Z",
  "occurred_at": "2026-03-10T12:00:00Z"
}
```

`event_type` and `event_id` are **assumed** (A-21); `milestone`,
`question_count_at_award` and `awarded_at` are **specified**. A jump past several
thresholds emits one event per threshold crossed, ascending.

### 3.2 `weekly_summary`

```json
{
  "event_id": "evt-ws-learner-7781-2026-W11-1",
  "event_type": "weekly_summary",
  "user_id": "learner-7781",
  "summary_id": "ws-learner-7781-2026-W11",
  "week": "2026-W11",
  "occurred_at": "2026-03-16T09:00:00Z",
  "summary": { "...": "the full weekly summary record from §2.3" }
}
```

All **assumed** in shape; the four content elements inside `summary` are
specified. The record is already written and logged before this is emitted.

### 3.3 `streak_write_failed` incident

Not learner-facing. Sent to `EngineeringAlertSink` when a streak write does not
commit after exactly one retry.

```json
{
  "incident_id": "inc-streak-write-learner-7781-20260310T120000000000Z",
  "user_id": "learner-7781",
  "occurred_at": "2026-03-10T12:00:00Z",
  "attempts": 2,
  "preserved_streak_days": 6,
  "preserved_longest_streak_days": 9,
  "intended_streak_days": 7,
  "error_type": "RepositoryWriteFailed",
  "error_detail": "..."
}
```

`preserved_streak_days` is what remains authoritative. It is never a value
produced by the failure. All fields **assumed**; the behaviour they report is
**specified**.

---

## 4. Shapes this component expects to receive

### 4.1 From `ActivityProvider` (read only)

| Method | Returns | Notes |
|---|---|---|
| `last_activity_at(user_id)` | `datetime (UTC) \| null` | Most recent known interaction. Timezone-aware; a naive value is an invalid response, not an assumption. |
| `interactions_in_window(user_id, since)` | `ActivityWindowRead` | `{ interactions: [{ interaction_id, occurred_at }], status }`. `occurred_at >= since`. |
| `question_count(user_id)` | `QuestionCountRead` | `{ count: integer >= 0, status }`. Lifetime total (A-29). |
| `topics_in_window(user_id, since)` | `TopicsRead` | `{ topics: [{ name, first_mentioned_at }], status }` (A-16). |

Method names and parameters are **specified**; the return record shapes are
**assumed** (`ActivityInteraction`, `TopicMention` and the `status` field).

### 4.2 From `GapReportProvider` (read only)

`suggested_topic(user_id) -> Topic | null`. Method name **specified**.

`Topic`:

| Field | Type | Provenance |
|---|---|---|
| `topic_id` | `string` | **assumed** |
| `name` | `string` | **assumed** |
| `naric_level` | `NaricLevel` | **specified** |
| `naric_level_source` | `retrieved \| default` | **specified** |
| `naric_level_status` | `SourceStatus` | **specified** (vocabulary) |
| `explanation_profile` | `basic \| intermediate \| advanced` | **specified** (bands; levels 4 and 6 are A-06) |
| `course_progress_percent` | `integer 0..100 \| null` | **specified** (type); placement on `Topic` is A-27 |
| `course_progress_status` | `SourceStatus` | **assumed** (A-08) |

`null` from `suggested_topic` means *the report answered and had nothing*. A
report that cannot be reached raises `ProviderUnavailable`. UC-08 invents a
suggestion for neither case.

---

## 5. Vocabularies, in full

Every value below is lowercase on the wire. Uppercase appears only as a Python
enum member name.

**`NaricLevel`** — **specified**, closed. Never an integer scale, never a
three-point pedagogic scale.

```
level_3   level_4   level_5   level_6   level_7   level_7_plus
```

Default when absent or unmappable: `level_5`.

**`naric_level_source`** — **specified**: `retrieved`, `default`.

**`explanation_profile`** — **specified**: `basic`, `intermediate`, `advanced`.

| Level | Profile |
|---|---|
| `level_3`, `level_4` | `basic` (level 4 is A-06) |
| `level_5`, `level_6` | `intermediate` (level 6 is A-06) |
| `level_7`, `level_7_plus` | `advanced` |

**`SourceStatus`** — **specified**, closed. `empty` and `unavailable` are
different states and are never conflated.

| Value | Means |
|---|---|
| `available` | The source answered with usable data. |
| `empty` | The source answered, and the answer is "nothing". |
| `partial` | The source answered, and some of the answer was usable. |
| `unavailable` | The source did not answer. |
| `invalid` | The source answered with something that cannot be mapped onto this contract. |

**NARIC invalid-value rule** — **specified**. A value mapping to no enum member
is an *invalid response*, not a level: apply `level_5`, set
`naric_level_source: default`, set `naric_level_status: invalid`, and log it.
`"Level Six"`, `"7+"`, `"level_6"` and `6` all map to real members; `"masters-ish"`
does not and degrades as above.

**`StreakOutcome`** — **assumed**. What one `record-activity` call did.

| Value | Means |
|---|---|
| `started` | First activity for the account. Count `1`. |
| `unchanged_same_day` | Activity on a UTC day already counted. Count unchanged. |
| `incremented` | Prior qualifying activity in the window. Count `+1`. |
| `reset` | Genuine inactivity determination. Count `1`. |
| `unchanged_source_degraded` | The activity read model could not be consulted. Count **preserved** (A-04). |
| `idempotent_replay` | The interaction was already processed. Nothing changed. |

**`PersistenceOutcome`** — **assumed**: `saved`, `saved_on_retry`,
`preserved_last_known`. The last means both attempts failed, the last known
count stands, and engineering was alerted.

**`FreezeOfferStatus`** — **assumed** (A-10): `offered`, `accepted`, `declined`,
`expired`.

**`DeliveryStatus`** — **assumed**: `pending`, `sent`, `failed`.

**`session_id_source`** — **specified** intent, values **assumed**: `received`,
`dev_minted`.

**Typed port errors** — **specified**: `ProviderUnavailable`,
`ProviderTimeout`, `ProviderInvalidResponse`. Persistence adds
`RepositoryWriteFailed` and `RepositoryReadFailed`; sinks add
`NotificationSendFailed`.

---

## 6. Ports

Nine ports. The first two are **read-only by shape** — no write method exists on
either interface, and an architecture test asserts it against the interfaces and
against every registered adapter.

| Port | Methods | Direction |
|---|---|---|
| `ActivityProvider` | `last_activity_at`, `interactions_in_window`, `question_count`, `topics_in_window` | read only |
| `GapReportProvider` | `suggested_topic` | read only |
| `StreakRepository` | `get`, `save` | read/write, owned record |
| `BadgeRepository` | `get_all`, `award` | append only |
| `WeeklySummaryRepository` | `save`, `get`, `list_for_user` (A-15) | read/write, owned record |
| `FreezeOfferRepository` (A-10) | `get_latest`, `save` | read/write, owned record |
| `ProcessedInteractionStore` (A-05) | `was_processed`, `mark_processed` | read/write, owned record |
| `NotificationSink` | `badge_awarded`, `weekly_summary` | outbound |
| `EngineeringAlertSink` | `streak_write_failed` | outbound |
| `Clock` | `now` (UTC) | read only |
| `CurrentUserProvider` | `resolve(request)` | read only |

No port offers a delete of any kind.

---

## 7. Session identity

**UC-08 receives an opaque `session_id`. It never creates one on a production
path.**

- `POST /api/v1/streaks/record-activity` accepts `session_id` in the body. Its
  value is opaque: it is echoed back and logged, never parsed.
- If it is absent and `ALLOW_DEV_SESSION_MINTING` is false — **the default** —
  the request fails with `400 session_id_required`. It is not quietly invented.
- With the flag explicitly enabled for local development, a value is minted from
  the account and the clock, prefixed `dev-minted-session-`, and the response
  reports `session_id_source: "dev_minted"` with a warning log. It is
  recognisable on sight and reproducible.

The **account** is a different thing and never travels in a payload. It is
resolved server-side by `CurrentUserProvider` (§9).

---

## 8. Behavioural contract a caller can rely on

**Streaks.** Continuity is a rolling `STREAK_WINDOW_HOURS` window from the
current UTC time (A-01). Activity 23h59m ago increments; 24h01m ago resets;
exactly 24h increments (A-03). The count increments at most once per UTC calendar
day (A-02) — twelve questions in an afternoon are one day. `record-activity` is
idempotent on `interaction_id` (A-05). The streak is bound to the account, so
three logins from three devices maintain one streak.

**Failures never cost a streak.** A write failure retries exactly once, then
preserves the last known count and raises an incident. An activity-read failure
preserves the count and reports `unchanged_source_degraded`. No exception handler
in the component can reach the reset path — asserted by an AST call-graph test.

**Badges.** Awarded when the lifetime question count crosses a configured
threshold; exactly once; permanent; a jump awards every threshold crossed. Each
award emits an event for a caller to render.

**Freeze.** A missed day with a streak of `FREEZE_MIN_STREAK_DAYS` or more
produces an offer. Accepting restores `preserved_streak_days + days active since`
(A-18). Usable once per UTC calendar month (A-11). Declined, exhausted or expired
(A-12) leaves the reset in place. Freeze failure never blocks coaching.

**Weekly summary.** Generated on the configured UTC day for the ISO week that
just ended, with all four elements. Written and logged whether sent or not. A
send failure retries the following day (A-19). Missed weeks are named, never
batch-sent (A-20). Gap report unavailable omits the suggestion and says so.

**Scheduling is not this component's job.** Generation is an explicit call:
`POST /api/v1/weekly-summaries/generate`. There is no scheduler, cron daemon or
background worker here. See `docs/INTEGRATION.md §6`.

---

## 9. API surface

Exactly seven routes. Every one resolves the account server-side; none accepts a
user identifier; every request body rejects unknown fields.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/streaks/record-activity` | Record a coaching interaction. Idempotent on `interaction_id`. |
| `GET` | `/api/v1/streaks` | Current streak state, plus any open freeze offer. |
| `GET` | `/api/v1/badges` | Badge collection and the configured milestones. |
| `POST` | `/api/v1/streaks/freeze` | Accept an offered freeze. |
| `POST` | `/api/v1/weekly-summaries/generate` | Explicit generation trigger. |
| `GET` | `/api/v1/weekly-summaries` | Summaries for the authenticated account. |
| `GET` | `/api/v1/healthz` | Liveness and the resolved wiring. |

Request bodies:

```jsonc
// POST /api/v1/streaks/record-activity
{ "interaction_id": "int-8891", "session_id": "sess-abc" }   // both strings; nothing else accepted

// POST /api/v1/streaks/freeze
{}                                                            // no fields

// POST /api/v1/weekly-summaries/generate
{}                                                            // no fields
```

Status codes:

| Code | When |
|---|---|
| `200` | Success, including every degraded outcome. Degradation is reported in `activity_status`, `persistence_outcome` and `omissions`, not by failing. |
| `400` | `session_id_required` — no session id and dev minting disabled. |
| `401` | `identity_not_resolved` — no authenticated account on the request. |
| `409` | `freeze_not_available` — no open offer, or the monthly allowance is spent. |
| `422` | Validation, including any unknown field such as `user_id`, `current_streak_days`, `milestone` or `freeze_available`. |
| `503` | `upstream_unavailable` / `storage_unavailable` — a safety net. The services degrade rather than propagate, so this indicates a bug, and it still leaks nothing. |

No error response contains an upstream field name, vendor name, URL, credential
or stack detail. That is enforced by the conformance suite at the port boundary
and by an integration test on the HTTP responses.

---

## 10. Extension points — where behaviour this component does not own attaches

| # | Point | How to attach | Notes |
|---|---|---|---|
| 1 | Real activity read model | Implement `ActivityProvider`, add one line to `ACTIVITY_PROVIDERS`, set `ACTIVITY_PROVIDER`. | `docs/INTEGRATION.md §4`. |
| 2 | Real gap report | Implement `GapReportProvider`, one registry line, set `GAP_REPORT_PROVIDER`. | Same shape. |
| 3 | Real persistence | Implement the five repository ports; swap in `uc08/composition.py::_build_persistence`. | The repository conformance suite covers the new backend by appending one entry to `BACKENDS`. |
| 4 | Real notification transport | Implement `NotificationSink`. UC-08 emits events; **rendering is the caller's**. There is no notification UI here and none should be added. | |
| 5 | Real engineering alerting | Implement `EngineeringAlertSink` (pager, incident tracker). Must not raise. | |
| 6 | Real authentication | Implement `CurrentUserProvider`. **Required before any deployment** (A-22). | Must ignore any identifier in the path, query or body. |
| 7 | Scheduling | Call `POST /api/v1/weekly-summaries/generate` per account from whatever the platform already uses. UC-08 deliberately owns no scheduler. | Idempotent per week, so over-calling is safe. |
| 8 | Freeze decline | A `decline_freeze` service operation exists with no route (A-28). Agree the eighth route and it is a thin controller. | Offers expire regardless. |
| 9 | Milestones and thresholds | `BADGE_MILESTONES`, `FREEZE_MIN_STREAK_DAYS`, `FREEZE_OFFER_EXPIRY_HOURS`, `STREAK_WINDOW_HOURS`, `WEEKLY_SUMMARY_DAY`. | Config, not code. |
| 10 | Clock | `Clock` is injectable; production uses `SystemClock`. | Do not add a system-clock call anywhere else; an architecture test fails. |

## 11. What this component will not do, by design

- It will not create a coaching session, coach, answer, analyse a gap, or
  generate feedback.
- It will not remove a badge. No method exists.
- It will not reset a streak because something failed.
- It will not invent a suggested topic, a NARIC level, a course-progress
  percentage, or a session id.
- It will not batch-send missed weekly summaries.
- It will not fall back to a mock when a real provider is configured. It refuses
  to start instead.
- It will not widen the NARIC enum to accommodate an upstream value. An
  unmappable value is `invalid`; a genuinely new level is a contract
  conversation.
