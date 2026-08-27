# Shared contract — UC-10 Feedback & Improvement

For an integration engineer holding a component they have never seen. This document
describes **what this component emits and expects**, not how to work on it (that is
`README.md`) and not how to write an adapter (that is `docs/INTEGRATION.md`).

Every field is marked **[SPEC]** (specified by the company) or **[OURS]** (assumed by us,
with its row in `docs/assumptions.md`).

---

## 1. What this component does, in one paragraph

It captures a learner's thumbs up / thumbs down on any delivered response, with an optional
comment; it stores that rating with the full context the model improvement pipeline needs;
and it raises a **content review flag** when the thumbs-down rate for a topic, across all
learners, over a rolling 7-day window, reaches a configured threshold on a sample of at
least a configured minimum size. It creates no sessions, answers no questions, and coaches
nobody.

---

## 2. Records this component **writes**

### 2.1 `RatingRecord` — owned by this component

The field set is exactly the specified metadata set; nothing has been added to it.

| Field | Type | Nullable | Provenance | Notes |
|---|---|---|---|---|
| `rating_id` | `string` | no | [SPEC] | `rat_<uuid4hex>`. Opaque; do not parse. [OURS: A-29 format] |
| `interaction_id` | `string` | no | [SPEC] | Exactly the identifier this component received from the `InteractionProvider`. |
| `session_id` | `string` | no | [SPEC] | Opaque. Copied from the interaction. **Never minted here.** |
| `user_id` | `string` | no | [SPEC] | Resolved server-side. Never read from a request body. |
| `rating` | `"up" \| "down"` | no | [SPEC] | Lowercase. |
| `comment` | `string \| null` | yes | [SPEC] | ≤ 500 chars [OURS: A-06]. Blank becomes `null`. A dismissed comment box is `null` and the rating is still recorded. |
| `question_text` | `string` | no | [SPEC] | Stored for the improvement pipeline. **Never logged, never on a flag, never in an error.** |
| `response_text` | `string` | no | [SPEC] | As above. |
| `naric_level` | `NaricLevel` | no | [SPEC] | See §4.1. |
| `session_mode` | `string` | no | [SPEC] | Opaque lowercase slug [OURS: A-20]. |
| `topic_tag` | `string` | no | [SPEC] | Opaque lowercase slug [OURS: A-20]. The aggregation key for flagging. |
| `rated_at` | `datetime` (UTC, tz-aware, ISO-8601) | no | [SPEC] | Server clock. Never client-supplied. |
| `superseded_by` | `string \| null` | yes | [SPEC] | The `rating_id` that replaced this one. Superseded records are **retained, never deleted**. |

Resolution rule: a learner's **current** rating for an interaction is the most recent
non-superseded record by `rated_at`. A learner flipping thumbs down to thumbs up leaves two
records and one current rating.

```json
{
  "rating_id": "rat_9f2c1a7e5b4d4a0e8c3f1b2d6e7a8c90",
  "interaction_id": "int_answer",
  "session_id": "sess_mock_1",
  "user_id": "user_alice",
  "rating": "down",
  "comment": "The limitation period given here looks wrong.",
  "question_text": "…",
  "response_text": "…",
  "naric_level": "level_7",
  "session_mode": "coaching",
  "topic_tag": "contract_formation",
  "rated_at": "2026-06-01T12:00:00+00:00",
  "superseded_by": null
}
```

### 2.2 `ContentReviewFlag` — owned by this component

Carries counts, rates, the rule applied and identifiers. **It has no field capable of
holding question text, response text or a comment**, and a test asserts that.

| Field | Type | Provenance | Notes |
|---|---|---|---|
| `flag_id` | `string` | [SPEC] | `flg_<uuid4hex>`. |
| `topic_tag` | `string` | [SPEC] | The topic under review. |
| `window_start` | `datetime` UTC | [SPEC] | `window_end − FLAG_WINDOW_DAYS`. |
| `window_end` | `datetime` UTC | [SPEC] | Evaluation time. |
| `total_ratings` | `int ≥ 0` | [SPEC] | Current ratings on the topic in the window, all users. |
| `down_ratings` | `int ≥ 0` | [SPEC] | Of which thumbs down. |
| `down_rate` | `float 0.0–1.0` | [SPEC] | `down_ratings / total_ratings`, 6 dp [OURS: A-30]. |
| `threshold_applied` | `float 0.0–1.0` | [SPEC] | The threshold in force when this flag was raised. |
| `flagging_interaction_ids` | `string[]` | [SPEC] | The down-rated interactions, oldest first, deduplicated. Identifiers only. |
| `created_at` | `datetime` UTC | [SPEC] | |
| `status` | `"open" \| "reviewed" \| "confirmed" \| "corrected"` | [SPEC] | See §6.3. |
| `minimum_sample_size_applied` | `int ≥ 1` | [OURS: A-08] | The other half of the rule that produced this flag. |
| `updated_at` | `datetime \| null` | [OURS: A-09] | Set when an open flag is updated instead of re-raised, and on a status change. |

```json
{
  "flag_id": "flg_1d4c2b8a6e5f4c3b9a0d7e6f5c4b3a29",
  "topic_tag": "undue_influence",
  "window_start": "2026-05-25T12:00:00+00:00",
  "window_end": "2026-06-01T12:00:00+00:00",
  "total_ratings": 10,
  "down_ratings": 3,
  "down_rate": 0.3,
  "threshold_applied": 0.3,
  "minimum_sample_size_applied": 10,
  "flagging_interaction_ids": ["int_undue_influence_0", "int_undue_influence_1", "int_undue_influence_2"],
  "created_at": "2026-06-01T12:00:00+00:00",
  "updated_at": null,
  "status": "open"
}
```

### 2.3 `FlagWorkItem` — internal, durable intent to flag [OURS: A-16]

Not part of any external contract; documented because a real persistence adapter must store
it. `work_id`, the decided `FlagCandidate` (topic, window, counts, rate, rule, interaction
ids, evaluation time), `enqueued_at`, `attempts`, `last_reason_code`, `resolved_at`,
`resolved_flag_id`. An item is enqueued **before** a flag write is attempted and resolved
**only after** the repository confirms the write.

---

## 3. Records this component **expects to receive**

### 3.1 `InteractionRecord` — from an `InteractionProvider`, read-only

This is the whole of what this component needs from the rest of the platform.

| Field | Type | Provenance | Notes |
|---|---|---|---|
| `interaction_id` | `string` | [SPEC] | Echoed back on the rating. |
| `session_id` | `string` | [SPEC] | Opaque; passed through untouched. |
| `user_id` | `string` | [SPEC] | The learner who received the response. Used to refuse cross-user rating, so it must be authoritative. |
| `question_text` | `string` | [SPEC] | Stored, never logged. |
| `response_text` | `string` | [SPEC] | Stored, never logged. |
| `response_category` | enum, §4.4 | [OURS: A-05] | Never consulted to decide whether a rating is allowed. |
| `topic_tag` | lowercase slug | [SPEC] | Aggregation key. |
| `session_mode` | lowercase slug | [SPEC] | |
| `naric_level` | `NaricLevel` §4.1 | [SPEC] | Already normalised by the adapter. |
| `naric_level_source` | `"retrieved" \| "default"` | [SPEC] | |
| `explanation_profile` | §4.3 | [SPEC] | Must agree with `naric_level`. |
| `naric_source_status` | `SourceStatus` §4.5 | [OURS: A-07] | Status of the level specifically. |
| `course_completion_percent` | `int 0–100 \| null` | [SPEC] | Integer. Never a float, never a string. |
| `delivered_at` | `datetime` UTC | [SPEC] | **The 24-hour rating window is measured against this.** |
| `source_status` | `SourceStatus` §4.5 | [SPEC] | Status of the interaction as a whole. |

### 3.2 Identity

`user_id` arrives from `CurrentUserProvider.resolve(request)` — server-side, never from a
request body. Admin authority arrives from the separate `AdminIdentityProvider` [OURS: A-15].

---

## 4. Vocabularies, in full

All values are lowercase on the wire. Python enum member names may be uppercase.

### 4.1 `NaricLevel` [SPEC] — closed
`level_3` · `level_4` · `level_5` · `level_6` · `level_7` · `level_7_plus`

Never an integer scale. Never a three-point pedagogic scale. A value mapping to no member is
an **invalid response**, not a level: the `level_5` default is applied, `naric_level_source`
becomes `default`, `naric_source_status` becomes `invalid`, and the event is logged
(`naric_level_defaulted`, shape only — never the raw payload).

### 4.2 `NaricLevelSource` [SPEC] — closed
`retrieved` · `default`

### 4.3 `ExplanationProfile` [SPEC] — closed, derived from the level
`basic` (levels 3, 4) · `intermediate` (levels 5, 6) · `advanced` (levels 7, 7+)
Levels 4 and 6 are an assumption [OURS: A-04].

### 4.4 `ResponseCategory` [OURS: A-05] — closed here, owned upstream
`answer` · `redirect` · `refusal` · `clarifying_question` · `degraded_fallback` · `unknown`

**Every one of these is rateable, including `unknown`.** Rateability never depends on this
value; there is no category branch in the capture path.

### 4.5 `SourceStatus` [SPEC] — closed
`available` · `empty` · `partial` · `unavailable` · `invalid`

`empty` (the upstream answered and had nothing) and `unavailable` (the upstream could not be
reached) are different states and are never conflated.

### 4.6 `RatingValue` [SPEC] — closed
`up` · `down`

### 4.7 `FlagStatus` [SPEC] — closed
`open` · `reviewed` · `confirmed` · `corrected`

### 4.8 Contract error vocabulary [SPEC + OURS: A-10]

| Error | Retryable | Meaning |
|---|---|---|
| `ProviderUnavailable` | yes | Upstream unreachable or refusing. Never means "empty". |
| `ProviderTimeout` | yes | Upstream missed the adapter's own deadline. |
| `ProviderInvalidResponse` | no | Upstream answered something unmappable to this contract. |
| `RecordNotFound` | no | Well-formed request for a record that does not exist. [OURS: A-10] |

Every error carries a port name and a `reason_code` matching `^[a-z][a-z0-9_]{0,63}$`.
Upstream error text, upstream field names and provider names **never** cross the boundary;
the constructor rejects a reason code that is not a snake_case token.

---

## 5. Session identity

* This component **receives** an opaque `session_id` and copies it onto the rating record.
* It **never creates** one on a production path.
* Dev-mode minting exists (`mint_dev_session_id`) and is gated by `ALLOW_DEV_SESSION_MINTING`,
  **defaulted off**; calling it while disabled raises. No request path calls it.
* Nothing parses or validates the shape of a `session_id` beyond "non-empty string".

---

## 6. HTTP surface

Exactly five endpoints. `user_id` is resolved server-side on every one of them.

### 6.1 `POST /api/v1/interactions/{interaction_id}/rating`

Request (`extra="forbid"` — unknown fields are a visible `422`, including attempts to send
`user_id`, `rated_at`, `threshold_applied` or `down_rate`):

```json
{ "rating": "down", "comment": "optional, may be omitted or null" }
```

| Status | Meaning |
|---|---|
| `201` | Recorded. |
| `200` | Replaced — the learner's previous rating was superseded (`superseded_rating_id` names it). |
| `401` | Anonymous. Nothing is stored. |
| `404` | Unknown interaction, or another learner's interaction (existence is not disclosed). |
| `409` | Outside the 24-hour window, measured server-side from `delivered_at`. |
| `422` | Schema violation — reports the field and the issue, never the submitted value. |
| `502` | Upstream answered something unmappable. Not retryable. |
| `503` | Could not be saved. **Retryable** — the learner is invited to try again. |

Error body, always this shape, always content-free:

```json
{ "error": { "code": "failed_retryable", "message": "Your feedback could not be saved. Please try again.", "retryable": true } }
```

### 6.2 `GET /api/v1/interactions/{interaction_id}/rating`
The caller's **own** current rating; `{"interaction_id": "…", "rating": null}` when there is
none [OURS: A-31]. Another learner's rating is never returned. `401` if anonymous.

### 6.3 `GET /api/v1/admin/flags` · `PATCH /api/v1/admin/flags/{flag_id}`
Admin credential required (separate port); `403` otherwise, for learners and anonymous
callers alike. `GET` returns open flags [OURS: A-32]. `PATCH` takes `{"status": "reviewed" |
"confirmed" | "corrected"}`; transitions are `open → reviewed/confirmed/corrected`,
`reviewed → confirmed/corrected`, `confirmed → corrected`, `corrected` terminal
[OURS: A-17]. Re-applying the current status is idempotent; going backwards is `409`.

### 6.4 `GET /api/v1/healthz`
`{"status": "ok", "component": "uc10-feedback-improvement", "wiring": {…}}`. Reports which
adapters are in use. Carries no secret.

---

## 7. Rules a consumer can rely on

1. **Nothing is unrateable.** Any response category, including one this component has never
   seen, can be rated up or down.
2. **A dismissed comment box never loses a rating.** The rating is the signal.
3. **Anonymous ratings never reach the pipeline.** They are refused, not stored.
4. **Changed ratings supersede, never delete.** The history is the signal too.
5. **The 24-hour window is server-side**, computed from the interaction's delivery time. A
   client-supplied timestamp cannot extend it — the schema refuses one outright.
6. **The flagging rule is configuration, not code.** Threshold, minimum sample and window
   length are read through a port at evaluation time.
7. **A decided flag is never dropped.** The intent is persisted before the write is
   attempted and cleared only after the write is confirmed.
8. **No duplicate open flags** for a topic whose window overlaps an existing open flag.
9. **A feedback failure never reaches the caller's main path.** Every foreseeable failure is
   a result object; the facade also absorbs unexpected defects.
10. **Learner content never leaves the store**: not to a log, not to a flag, not to a
    notification, not to an error response.

---

## 8. Extension points — behaviour this component does not own

| # | Point | How it attaches | Notes |
|---|---|---|---|
| 1 | **Interaction source** | Implement `InteractionProvider`, add one line to `INTERACTION_PROVIDERS` in `uc10/adapters/registry.py`, set `INTERACTION_PROVIDER`. | The only external system this component reads. See `docs/INTEGRATION.md`. |
| 2 | **Rating persistence** | Implement `RatingRepository` (including `current_in_window`, [OURS: A-11]) and pass it to `build_container`. | The in-memory implementation is a stand-in, not a database. |
| 3 | **Flag persistence** | Implement `FlagRepository` (including `get`, [OURS: A-13]). | |
| 4 | **Flag retry durability** | Implement `FlagWorkQueue` [OURS: A-16] over a real outbox. | Without durability the never-drop guarantee weakens to the process lifetime. |
| 5 | **Admin notification** | Implement `AdminNotificationSink.flag_created(flag)` — email, ticket, webhook, dashboard event. | Called once, on creation, never on update. A failure here never loses a persisted flag. |
| 6 | **Flagging policy source** | Implement `ThresholdConfigProvider` over an admin console instead of environment variables. | Read at evaluation time, so a change takes effect without a deploy. |
| 7 | **Identity** | Implement `CurrentUserProvider` and `AdminIdentityProvider`. | The dev header adapters are not authentication. |
| 8 | **Evaluation schedule** | Call `FlaggingService.run_cycle()` from whatever scheduler the platform uses. | No scheduler is shipped [OURS: A-24]. Retries of deferred flags happen here. |
| 9 | **Clock** | Implement `Clock.now()` (UTC). | |
| 10 | **Improvement pipeline export** | Read `RatingRecord`s from the rating repository. | This component is the producer; it does not push. Question/response text lives here for exactly this purpose. |

---

## 9. What this component will never do

Create a session · answer a question · coach · analyse gaps · compute streaks · summarise ·
write to any interaction · log learner content · put learner content on a flag · accept a
`user_id` from a request body · flag a topic below the configured minimum sample · fall back
to a mock when a real provider is configured.
