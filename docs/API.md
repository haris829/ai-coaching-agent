# API reference

Base path: **`/api`**. Interactive docs: `/api/docs`. OpenAPI: `/api/openapi.json`.

Ten capabilities share one API, one error envelope and one set of conventions:

| Section | Base path | Capability |
| ------- | --------- | ---------- |
| [Quiz configuration — admin](#quiz-configuration--admin) | `/api/admin/quizzes` | UC-01 |
| [Quiz configuration — learner](#quiz-configuration--learner) | `/api/quizzes` | UC-01 |
| [Questions](#questions), [Topics](#topics), [CSV import](#csv-import), [Delivery and historical reporting](#delivery-and-historical-reporting) | `/api/question-bank` | UC-02 |
| [Attempt delivery](#attempt-delivery) | `/api/v1` | UC-03 |
| [Results — scoring, pass/fail and feedback](#results--scoring-passfail-and-feedback) | `/api/v1` | UC-04, UC-05, UC-06 |
| [AI coaching review mode](#ai-coaching-review-mode) | `/api/v1` | UC-07 |
| Retakes — learner | `/api/v1/quizzes/{id}/retakes`, `/retake-eligibility`, `/attempt-history` | UC-08 |
| Retakes — administrator grants | `/api/admin/retakes` | UC-08 |
| Formal assessment — learner | `/api/v1/quizzes/{id}/formal-*`, `/api/v1/formal-attempts` | UC-09 |
| Formal assessment — assessor | `/api/assessor` | UC-09 |
| Formal assessment — platform-internal | `/api/system/formal-assessments` | UC-09 |
| Analytics and reporting | `/api/admin/analytics` | UC-10 |
| [Shared](#shared) | `/api` | all of them |

**Three audiences, three roots.** `/api/v1` is the learner's; `/api/admin` and `/api/question-bank`
are an administrator's; `/api/assessor` is an assessor's; and `/api/system` is for platform-internal
callers with no human behind them. They are separate because the credentials are: an administrator
is deliberately *refused* on `/api/assessor` (a review exists so a named person signs off), and an
assessor is refused on `/api/system`. The full matrix is asserted over the generated OpenAPI
document by `tests/global_dod/test_api_authorization.py`, so a route added tomorrow is covered the
day it ships.

**Serialisation differs by capability, and the boundary is deliberate.** UC-01 to UC-07 serialise in
`camelCase`; UC-08, UC-09 and UC-10 in `snake_case`. Renaming either to match would mean rewriting a
published contract or hiding the difference behind a translation layer that the next person reading a
network trace would have to undo. The seam is documented instead.

The learner-facing routes are **versioned** (`/api/v1`) because they are the contract a client
application depends on; the administrative ones are internal. UC-04 through UC-07 share that prefix
with UC-03 deliberately: a score, a verdict, a feedback report and a coaching conversation are all
statements about one attempt.

Paths in the UC-02 sections below are relative to `/api/question-bank`.

Requests and responses are camelCase. `{questionId}` accepts either the internal id or the
human-readable reference (`Q-000042`).

---

## Conventions

### Error envelope

Every failure returns the same shape. Stack traces and driver messages are never included — they are
logged server-side only.

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "The question is not valid (2 problem(s) found).",
    "details": [
      { "field": "options.correct", "code": "SINGLE_CHOICE_REQUIRES_ONE_CORRECT",
        "message": "Single-choice question requires exactly one correct answer (received 2)." }
    ]
  }
}
```

Every envelope also carries `retryable` (a boolean), `requestId` (echoed from `X-Request-Id` when
supplied, generated otherwise) and `timestamp`.

`details` is always a list of `{field, code, message}` — **field-level problems** — whichever
capability produced it and whether the payload arrived as JSON or as a CSV row.

`context` is a flat object of **machine-readable context** for that specific code. Some UC-01 codes
also attach a named key directly on the `error` object:

| Key          | Appears with                                        | Meaning |
| ------------ | --------------------------------------------------- | ------- |
| `capacity`   | `QUESTION_BANK_INSUFFICIENT`                        | The full capacity report: `satisfiable`, `requestedTotal`, `availableTotal`, `totalShortfall`, `breakdown[]`, `messages[]` |
| `retryable`  | every envelope                                      | `true` when nothing was written, so the same request can be sent again |
| `context`    | most UC-03 codes                                    | e.g. `{"activeAttemptId": "…"}`, `{"currentRevision": 2, "expectedRevision": 1}`, `{"shortfalls": [...]}` |

Nothing may return a code outside its capability's own taxonomy plus
`app.core.errors.PLATFORM_ERROR_CODES`, and a test asserts it.

### Status codes

| Code | Meaning |
| ---- | ------- |
| `200` | Success |
| `201` | Created (question, topic, import run, usage record) |
| `400` | The **request** is malformed — bad JSON, bad query params, an unprocessable CSV file |
| `401` | `ADMIN_API_TOKEN` is configured and the bearer token is missing or wrong |
| `404` | Not found |
| `405` | Method not allowed |
| `409` | Conflicts with the resource's lifecycle or with existing data |
| `413` | CSV upload exceeds `CSV_MAX_BYTES` |
| `422` | The request was understood but describes something **invalid** — always carries `details` |
| `500` | Internal / database failure (generic message only) |
| `503` | A write failed and **nothing was applied** — safe to retry (`PERSISTENCE_FAILED`) |

`400` vs `422` is a deliberate distinction: `400` means "I cannot read this request", `422` means
"I read it, and what it describes is not valid".

`500` vs `503` is the same kind of distinction for failures: `503 PERSISTENCE_FAILED` promises the
previous state is intact, which is what lets a client offer a retry without risking a duplicate.

### Error codes

| Code | Status | Meaning |
| ---- | ------ | ------- |
| `VALIDATION_FAILED` | 422 | Field-level problems, listed in `details` |
| `QUESTION_BANK_INSUFFICIENT` | 422 / 409 | The bank cannot satisfy the configuration. 422 when saving, 409 when starting a quiz |
| `BAD_REQUEST` | 400 | Malformed request or unreadable CSV |
| `UNAUTHORIZED` | 401 | No resolvable credential |
| `FORBIDDEN` | 403 | Authenticated, wrong role |
| `NOT_FOUND` | 404 | Unknown resource |
| `CONFIGURATION_UNAVAILABLE` | 409 | The quiz has never been configured |
| `IMMUTABLE_CONFIGURATION_VERSION` | 409 | Something tried to edit a stored version |
| `CONCURRENT_CONFIGURATION_UPDATE` | 409 | Two saves raced; nothing was written |
| `QUESTION_ALREADY_RETIRED` · `QUESTION_NOT_RETIRED` | 409 | Lifecycle conflict |
| `QUESTION_HAS_HISTORY` | 409 | Hard delete refused — retire instead |
| `QUESTION_NOT_DELIVERABLE` | 409 | Only ACTIVE questions can be delivered |
| `DUPLICATE_QUESTION` | 409 | Same type, text and answer key as an existing question |
| `INTEGRITY_CONFLICT` | 409 | A database constraint rejected the write |
| `USAGE_ALREADY_RECORDED` · `USAGE_ALREADY_COMPLETED` | 409 | Delivery seam conflicts |
| `PAYLOAD_TOO_LARGE` | 413 | CSV beyond `CSV_MAX_BYTES` |
| `PERSISTENCE_FAILED` | 503 | Write failed, nothing applied, retryable |
| `DATABASE_ERROR` · `INTERNAL_ERROR` | 500 | Generic; detail is logged, never returned |

UC-03 (attempt delivery) has its own taxonomy:

| Code | Status | Meaning |
| ---- | ------ | ------- |
| `UNAUTHENTICATED` | 401 | No learner identity resolved |
| `VALIDATION_ERROR` | 400 | A domain check failed — a position outside the paper, a batch over the ceiling |
| `LEARNER_NOT_ENROLLED` · `ENROLMENT_NOT_ACTIVE` | 403 | The enrolment rule refuses the attempt |
| `QUIZ_NOT_FOUND` · `QUIZ_NOT_AVAILABLE` | 404 / 409 | The quiz cannot be attempted |
| `INVALID_CONFIGURATION` | 422 | The locked configuration is not deliverable |
| `CONFIGURATION_VERSION_UNAVAILABLE` | 409 | The version an attempt is locked to cannot be resolved |
| `INSUFFICIENT_QUESTIONS` | 422 | The bank cannot fill the paper — `context.shortfalls` names each type |
| `QUESTION_BANK_UNAVAILABLE` · `QUESTION_UNAVAILABLE` | 503 / 409 | The bank could not supply content |
| `MAX_ATTEMPTS_REACHED` | 409 | The allowance is used up — `context` reports used / max / remaining |
| `ACTIVE_ATTEMPT_EXISTS` | 409 | An open attempt already exists — `context.activeAttemptId` is the one to resume |
| `ATTEMPT_NOT_FOUND` | 404 | Unknown attempt, **or another learner's** — deliberately indistinguishable |
| `NO_ACTIVE_ATTEMPT` | 404 | Nothing to resume |
| `ATTEMPT_ALREADY_SUBMITTED` · `ATTEMPT_EXPIRED` | 409 | The attempt is closed to writes |
| `ATTEMPT_SUBMISSION_PENDING` · `ATTEMPT_NOT_SUBMITTABLE` | 409 | Submission state forbids the operation |
| `INVALID_ANSWER` | 422 | The answer does not fit the delivered question — `context` names the offending value |
| `ANSWER_REVISION_CONFLICT` | 409 | Another tab or device moved the answer on |
| `QUESTION_PRESENTATION_VIOLATION` | 409 | Asking for the whole paper on a one-at-a-time attempt |
| `INVALID_FLAG_OPERATION` | 409 | Flagging a question outside the attempt |
| `SUBMISSION_NOT_CONFIRMED` | 400 | `confirmed: true` was not sent |
| `DUPLICATE_SUBMISSION` · `IDEMPOTENCY_KEY_REUSED` | 409 | The idempotency rules refused a second submission |
| `SUBMISSION_FAILED` | 502 | The downstream hand-off failed; the answers are frozen and safe |
| `NO_PENDING_SUBMISSION` | 409 | Nothing to retry |

UC-04, UC-05 and UC-06 own the codes listed with the
[results endpoints](#results--scoring-passfail-and-feedback). Every one of them is either that
capability's own code or one of the platform codes above — nothing else is ever returned, and a test
per capability asserts it.

### Headers

| Header | Purpose |
| ------ | ------- |
| `Authorization: Bearer <token>` | Resolved to a principal with a role. Required by every UC-01 endpoint; required by mutating UC-02 endpoints when `ADMIN_API_TOKEN` is set |
| `X-Admin-User: <id>` | Fallback audit label, used only when no token resolves. Defaults to `admin` |

A token is resolved in this order: a row in the placeholder identity directory (`qa_users`), then
`ADMIN_API_TOKEN` if configured, otherwise unresolved. An unresolved credential is `401`; a resolved
learner calling an administrative endpoint is `403`. One token therefore works across both
capabilities — see `app/modules/identity/security.py`.

---

## Quiz configuration — admin

Base path `/api/admin/quizzes`. Every endpoint requires an administrator.

### `GET /api/admin/quizzes`

Quizzes available to configure. `{"quizzes": [{id, courseId, courseTitle, slug, title}]}`.

### `GET /api/admin/quizzes/{quizId}/configuration`

The active configuration plus a **live** capacity report.

```json
{
  "quiz": { "id": 1, "courseId": 1, "courseTitle": "…", "slug": "…", "title": "…" },
  "configuration": {
    "id": 3, "quizId": 1, "versionNumber": 2,
    "questionCount": 20, "timeLimitMinutes": 30, "passMark": 70,
    "randomiseQuestions": true, "maxAttempts": 2, "deliveryMode": "assessment",
    "questionTypes": [{ "type": "SINGLE_CHOICE", "quota": 10 }, { "type": "TRUE_FALSE", "quota": 10 }],
    "topics": [], "isActive": true,
    "settingsFingerprint": "…", "createdByUserId": 1, "createdBy": "admin@example.com",
    "createdAt": "2026-08-18T10:00:00.000Z", "attemptCount": 4
  },
  "capacity": { "satisfiable": true, "requestedTotal": 20, "availableTotal": 34,
                "totalShortfall": 0, "breakdown": [], "messages": [] }
}
```

`configuration` and `capacity` are both `null` when the quiz has never been configured.

### `PUT /api/admin/quizzes/{quizId}/configuration`

Saves by creating a **new immutable version**. Request body:

```json
{
  "questionCount": 20,
  "timeLimitMinutes": 30,
  "passMark": 70,
  "maxAttempts": 2,
  "deliveryMode": "assessment",
  "randomiseQuestions": true,
  "questionTypes": [{ "type": "SINGLE_CHOICE", "quota": 10 }, { "type": "TRUE_FALSE", "quota": 10 }],
  "topicIds": []
}
```

| Field | Rule |
| ----- | ---- |
| `questionCount` | 1–100, required |
| `timeLimitMinutes` | 1–480, or `null`/omitted for no limit. **Required** when `deliveryMode` is `exam` |
| `passMark` | 1–100, required |
| `maxAttempts` | 1–50, required |
| `deliveryMode` | `practice` \| `assessment` \| `exam`, required |
| `randomiseQuestions` | boolean, defaults to `false` |
| `questionTypes` | At least one. Accepts `"SINGLE_CHOICE"` or `{"type": …, "quota": …}`. Quotas are all-or-nothing and must sum to `questionCount` |
| `topicIds` | Optional. Empty means the whole active bank |

| Status | Meaning |
| ------ | ------- |
| `201` | A new version was created. Body has `created: true` |
| `200` | Nothing meaningful changed. Body has `created: false, unchanged: true` and the existing version |
| `422` | `VALIDATION_FAILED` or `QUESTION_BANK_INSUFFICIENT` (with `capacity`) |
| `409` | `CONCURRENT_CONFIGURATION_UPDATE` — a simultaneous save won; retry |
| `503` | `PERSISTENCE_FAILED` — nothing written, retry is safe |

### `GET /api/admin/quizzes/{quizId}/configuration/versions`

The immutable history, newest first: `{"quiz": {…}, "versions": [ConfigurationVersion, …]}`.

### `GET /api/admin/quizzes/{quizId}/question-bank`

Eligible question counts per type. Optional repeatable `?topicId=` narrows the scope.

```json
{ "quiz": {…}, "topicIds": [],
  "availableByType": { "SINGLE_CHOICE": 14, "TRUE_FALSE": 12, "MULTI_SELECT": 6,
                       "SCENARIO": 6, "DRAG_TO_ORDER": 6 } }
```

Retired and draft questions never appear in these counts.

---

## Quiz configuration — learner

Base path `/api/quizzes`. Every endpoint requires an authenticated caller.

### `GET /api/quizzes/{quizId}/rules`

The rules summary. **Read-only — it never creates an attempt.**

```json
{
  "quiz": {…},
  "configurationVersionId": 3, "configurationVersionNumber": 2,
  "questionCount": 20, "timeLimitMinutes": 30, "passMark": 70,
  "randomiseQuestions": true, "deliveryMode": "assessment", "maxAttempts": 2,
  "questionTypes": [{ "type": "SINGLE_CHOICE", "quota": 10 }], "topics": [],
  "attemptsUsed": 1, "remainingAttempts": 1,
  "canStart": true, "blockedReason": null, "attemptInProgress": null
}
```

`blockedReason` is `attempt_in_progress`, `attempt_limit_reached` or
`question_bank_insufficient`. `409 CONFIGURATION_UNAVAILABLE` when the quiz has never been
configured.

Attempts are **not** part of UC-01's API. UC-03 owns the attempt lifecycle — see
[Attempt delivery](#attempt-delivery). `attemptsUsed`, `remainingAttempts`, `canStart` and
`attemptInProgress` above are read from UC-03 through a port, so the two never disagree.

---

## Attempt delivery

Base path `/api/v1`. Every endpoint requires an authenticated **learner**; the learner is resolved
from the bearer token, never from the request body, so one learner cannot act as another.

Ids are **opaque strings** here. UC-03 never parses a quiz, course or learner id — those belong to
other capabilities — so `quizId` is sent as a string even though UC-01 numbers its quizzes.

### `GET /api/v1/quizzes/{quizId}/attempt-eligibility`

Pre-flight. Applies exactly the checks attempt creation applies, and **creates nothing**.

```json
{
  "eligibility": {
    "quizId": "1", "courseId": "1", "learnerId": "2",
    "eligible": true, "reasons": [],
    "enrolled": true, "enrolmentStatus": "ACTIVE",
    "attemptsUsed": 1, "maxAttempts": 3, "attemptsRemaining": 2,
    "openAttemptId": null,
    "activeConfigurationVersionId": "3", "activeConfigurationVersion": 2
  }
}
```

`reasons` lists **every** blocker, not just the first — two things can be wrong at once.

### `POST /api/v1/attempts` → `201`

**Start.** Validates eligibility, locks the active UC-01 configuration version onto the attempt,
selects the question set from the UC-02 bank and freezes it. All validation happens before any write;
the attempt and its complete question set commit in one transaction.

```json
{ "quizId": "1" }
```

```json
{
  "attempt": {
    "attemptId": "095c94b8-…", "learnerId": "2", "courseId": "1", "quizId": "1",
    "attemptNumber": 2, "status": "ACTIVE", "questionPresentation": "ALL_AT_ONCE",
    "totalQuestions": 20, "currentPosition": 1,
    "startedAt": "…", "expiresAt": "…", "submittedAt": null, "finalisedAt": null,
    "submissionReason": null, "lastActivityAt": "…",
    "configurationVersionId": "3",
    "configuration": { "version": 2, "questionCount": 20, "timeLimitSeconds": 1800,
                       "passMarkPercentage": 70, "maxAttempts": 3,
                       "questionPresentation": "ALL_AT_ONCE", "randomiseQuestionOrder": true,
                       "randomiseOptionOrder": true, "allowIncompleteSubmission": true,
                       "questionTypeQuotas": [], "activatedAt": "…" },
    "timing": { "…": "see GET /timing" }
  },
  "delivery": {
    "questionPresentation": "ALL_AT_ONCE", "totalQuestions": 20,
    "questionTypeCounts": { "SINGLE_CHOICE": 10, "…": 0 },
    "questionsUrl": "/api/v1/attempts/095c94b8-…/questions"
  }
}
```

The questions are **not** in this response. `questionsUrl` names the correct endpoint for the locked
presentation, so a client cannot fetch the whole paper for a one-at-a-time attempt by accident.

`409 ACTIVE_ATTEMPT_EXISTS`, `409 MAX_ATTEMPTS_REACHED`, `403 LEARNER_NOT_ENROLLED`,
`422 INSUFFICIENT_QUESTIONS`.

### `GET /api/v1/attempts/active?quizId=…`

**Resume.** The learner's open attempt, or `404 NO_ACTIVE_ATTEMPT`. This is the first call a client
should make: a refresh, a closed laptop or a flat battery costs nothing.

### `GET /api/v1/attempts/{attemptId}`

The attempt, its locked configuration and authoritative timing. Another learner's attempt returns
`404 ATTEMPT_NOT_FOUND` — indistinguishable from one that does not exist.

### `GET /api/v1/attempts/{attemptId}/state`

Everything a navigator needs.

```json
{
  "state": {
    "attemptId": "…", "status": "ACTIVE", "questionPresentation": "ALL_AT_ONCE",
    "currentPosition": 3, "totalQuestions": 20,
    "answeredCount": 12, "completeCount": 11, "unansweredCount": 8, "flaggedCount": 2,
    "questions": [{ "questionId": "…", "position": 1, "questionType": "SCENARIO",
                    "answered": true, "complete": false, "flagged": true }],
    "timing": { "…": "see below" }
  }
}
```

`answered` and `complete` differ on purpose: a scenario with two of three sub-questions filled in is
answered but not complete, and that is exactly the question a review screen must surface.

### `GET /api/v1/attempts/{attemptId}/timing`

The **only** source a countdown may trust.

```json
{
  "timing": {
    "serverTime": "2026-03-01T09:05:00Z", "serverTimeEpochMs": 1772442300000,
    "status": "ACTIVE", "startedAt": "…", "expiresAt": "…",
    "timeLimitSeconds": 1800, "timed": true,
    "elapsedSeconds": 300, "remainingSeconds": 1500, "expired": false,
    "submittedAt": null,
    "clockResyncThresholdSeconds": 5, "autosaveIntervalSeconds": 20,
    "reportedClientSkewSeconds": 2
  },
  "attempt": { "attemptId": "…", "status": "ACTIVE", "submittedAt": null, "submissionReason": null }
}
```

Optional `?clientTime=<ISO-8601>` is echoed back as `reportedClientSkewSeconds`. It is **advisory
only** and never enters a calculation, so a manipulated device clock cannot extend an attempt. Resync
whenever the reported skew exceeds `clockResyncThresholdSeconds`.

### `GET /api/v1/attempts/{attemptId}/questions`

The whole paper, from the attempt's frozen snapshots. `409 QUESTION_PRESENTATION_VIOLATION` for a
one-at-a-time attempt.

```json
{
  "questions": [
    { "questionId": "…", "position": 1, "questionType": "MULTI_SELECT", "questionVersion": 2,
      "points": 2.0, "prompt": "…", "minSelections": 1, "maxSelections": 3,
      "options": [{ "optionId": "…", "text": "…" }] }
  ]
}
```

No grading data appears — the presenter is an allow-list, so `isCorrect`, `correctPosition` and
explanations are dropped by construction rather than filtered.

### `GET /api/v1/attempts/{attemptId}/questions/current` · `/at/{position}` · `/{questionId}`

One question at a time. The `at/{position}` form also returns sibling navigation links.
`400 VALIDATION_ERROR` for a position outside the paper.

### `PUT /api/v1/attempts/{attemptId}/cursor`

`{"position": 4}` — persists the resume position, so a reload returns to the same question.

### `PUT /api/v1/attempts/{attemptId}/questions/{questionId}/answer`

```json
{ "response": { "selectedOptionIds": ["opt-1", "opt-3"] },
  "source": "MANUAL", "expectedRevision": 2 }
```

```json
{ "answer": { "questionId": "…", "position": 1, "answered": true, "complete": true,
              "response": {…}, "revision": 3, "source": "MANUAL", "savedAt": "…",
              "changed": true },
  "timing": {…}, "persistedAt": "…" }
```

**Idempotent:** re-sending the same response succeeds, reports `changed: false` and does not advance
the revision — which is what makes a periodic autosave safe. `expectedRevision` is optional; when it
does not match, `409 ANSWER_REVISION_CONFLICT` reports `currentRevision` and `expectedRevision` in
`context` rather than overwriting another device's answer. `response: null` clears the answer.

The payload shape per type:

| Type | `response` |
| ---- | ---------- |
| `SINGLE_CHOICE` | `{"selectedOptionId": "…"}` |
| `TRUE_FALSE` | `{"value": true}` — a strict boolean; `1` is rejected |
| `MULTI_SELECT` | `{"selectedOptionIds": ["…"]}` — compared as a set, so order is not a change |
| `DRAG_TO_ORDER` | `{"orderedItemIds": ["…"]}` — every item, exactly once |
| `SCENARIO` | `{"responses": [{"subQuestionId": "…", "answer": {…}}]}` — each sub-answer in its own type's shape |

### `POST /api/v1/attempts/{attemptId}/answers`

Batch autosave — one request for every dirty answer.

```json
{ "answers": [{ "questionId": "…", "response": {…}, "expectedRevision": 1 }],
  "source": "AUTOSAVE" }
```

Returns `{saved: [...], savedCount, changedCount, timing, persistedAt}`. **Atomic:** one bad entry
rejects the whole batch, so a client never has to reason about a partial save. At most
`MAX_BATCH_ANSWERS` entries.

### `GET /api/v1/attempts/{attemptId}/answers`

The reload path. Every delivered question is listed, answered or not, so "answered" and "unanswered"
are both explicit. A client that reconnects discards its own state and rebuilds from here.

### `GET /api/v1/attempts/{attemptId}/answers/revisions`

Append-only record of every accepted save. Operationally useful for confirming an autosave landed.

### `PUT` · `DELETE /api/v1/attempts/{attemptId}/questions/{questionId}/flag`

`{"flagged": true}`. Idempotent, and re-flagging preserves the original instant.
`GET /flags` lists them.

### `GET /api/v1/attempts/{attemptId}/submission/preview`

What would be submitted. **Writes nothing and never submits**, however many times it is called.

```json
{
  "preview": {
    "attemptId": "…", "attemptStatus": "ACTIVE",
    "totalQuestions": 20, "answeredCount": 18, "completeCount": 18, "unansweredCount": 2,
    "unanswered": [{ "position": 7, "questionId": "…" }],
    "flagged": [{ "position": 3, "questionId": "…" }],
    "allowIncompleteSubmission": true,
    "canSubmit": true,
    "blockers": [],
    "warnings": [{ "code": "UNANSWERED_QUESTIONS", "message": "2 question(s) are unanswered…" }],
    "timing": {…},
    "requiresConfirmation": true,
    "suggestedIdempotencyKey": "attempt-…-submit"
  }
}
```

A **blocker** disables submission (`INCOMPLETE_SUBMISSION_NOT_ALLOWED`,
`ATTEMPT_ALREADY_SUBMITTED`); a **warning** is shown and proceeds (`UNANSWERED_QUESTIONS`,
`FLAGGED_QUESTIONS`, `TIME_ALMOST_ELAPSED`). The server decides which is which.

### `POST /api/v1/attempts/{attemptId}/submission`

```json
{ "confirmed": true, "idempotencyKey": "attempt-…-submit" }
```

`confirmed: true` is required — `400 SUBMISSION_NOT_CONFIRMED` otherwise. The idempotency key may
also be sent as an `Idempotency-Key` header; when omitted, a deterministic key is derived, so a
double-click still collapses into one submission. Enforced by a unique index, not only in code.

On a downstream failure the attempt is left `SUBMISSION_PENDING` with the answers frozen, and
`502 SUBMISSION_FAILED` reports `submissionId` and `submissionState` in `context`.

### `POST /api/v1/attempts/{attemptId}/submission/retry`

Completes a submission left `PENDING`. Safe to call repeatedly: it continues the same submission
rather than creating a second. `409 NO_PENDING_SUBMISSION` when there is nothing to retry.

### `GET /api/v1/attempts/{attemptId}/submission`

`{attemptId, status, submittedAt, history: [{submissionId, state, reason, idempotencyKey,
downstreamReference, attempts, createdAt, updatedAt}]}`.

---

## Results — scoring, pass/fail and feedback

Base path `/api/v1`, alongside the attempt, because a score, a verdict and a feedback report are all
part of the same conversation about one attempt. Every endpoint requires an authenticated **learner**
and is scoped to attempts that learner owns: somebody else's attempt reads as `404`, never as `403`,
so an id cannot be probed. An administrator token is refused (`403`) rather than treated as a learner.

**All three `POST`s are idempotent, and each is also its own retry path.** The chain normally runs
automatically inside `POST /attempts/{id}/submission` — UC-03 hands the submitted attempt downstream
and the downstream is this. These endpoints exist so a stage that a transient failure left pending can
be driven again, and so a client can read what submission already produced.

### `GET /api/v1/attempts/{attemptId}/result`

The score (UC-04), with the marks awarded per question.

```json
{
  "result": {
    "resultId": "…", "attemptId": "…", "submissionId": "…",
    "learnerId": "9001", "courseId": "1", "quizId": "1", "attemptNumber": 1,
    "status": "SCORED", "statusLabel": "Scored",
    "totalMarks": 7.0, "maximumMarks": 9.0, "percentage": 77.78,
    "passMarkPercentage": 60.0,
    "totalQuestions": 5, "correctCount": 4, "incorrectCount": 1, "unansweredCount": 0,
    "timeTakenSeconds": 750,
    "startedAt": "…", "submittedAt": "…", "scoredAt": "…",
    "configurationVersionId": "3", "configurationVersion": 2,
    "anomalies": [], "failureCode": null, "failureMessage": null,
    "scoringAttemptCount": 1, "algorithmVersion": 1
  },
  "questionScores": [{
    "questionId": "…", "questionVersion": 1, "questionType": "MULTI_SELECT", "position": 3,
    "questionText": "…", "scenarioText": null,
    "awardedMarks": 1.0, "maximumMarks": 3.0, "rawMarks": 1.0, "deduction": 0.5,
    "outcome": "PARTIALLY_CORRECT", "answered": true,
    "learnerAnswer": { "optionIds": ["A", "C"], "labels": ["…", "…"] },
    "correctAnswer": { "optionIds": ["A", "B"], "labels": ["…", "…"] },
    "optionMarks": [{ "optionId": "A", "text": "…", "selected": true, "correct": true,
                      "markContribution": 1.5 }],
    "anomaly": null, "answerKeySource": "QUESTION_BANK_SNAPSHOT"
  }]
}
```

`status` is `SCORED` or `PENDING_SCORE`; `statusLabel` carries the wording to show a learner, which for
a pending result is **“Submitted — Pending Score”**. A pending result also carries `failureCode` and
`anomalies` — `MISSING_ANSWER_KEY`, `ZERO_MAXIMUM_MARKS`, `AMBIGUOUS_PRIMARY_ANSWER`,
`UNREADABLE_ANSWER`, `UNSUPPORTED_QUESTION_TYPE`, `NO_QUESTIONS_DELIVERED` — and stores **no**
per-question scores, because nobody should be shown marks computed from an answer key that could not
be read.

`configurationVersionId` is the version the *attempt* was locked to. Reconfiguring the quiz afterwards
cannot change it, and neither can re-scoring: a confirmed score is immutable, enforced by a database
trigger as well as by the service.

`404 RESULT_NOT_FOUND` when the attempt has never been scored; `404 ATTEMPT_NOT_FOUND` when it is not
this learner's.

### `POST /api/v1/attempts/{attemptId}/result`

Scores the attempt, or replays the score it already has.

An attempt that is already `SCORED` answers `200` with `replayed: true` and **writes nothing**. One
left `PENDING_SCORE` is scored again — so this is the retry path once the underlying data is fixed.

`409 ATTEMPT_NOT_SUBMITTED` while the attempt is still in progress. `503 PERSISTENCE_FAILED`
(retryable) if the result could not be saved, in which case nothing was saved.

### `GET /api/v1/attempts/{attemptId}/outcome`

Pass/fail (UC-05), the certificate and the CPD record.

```json
{
  "outcome": {
    "outcomeId": "…", "attemptId": "…", "resultId": "…", "attemptNumber": 1,
    "outcome": "PASS", "outcomeLabel": "Pass", "passed": true,
    "percentage": 77.78, "passMarkPercentage": 60.0,
    "totalMarks": 7.0, "maximumMarks": 9.0,
    "configurationVersionId": "3", "certificateRequired": true, "determinedAt": "…",
    "attemptsUsedAtOutcome": 1, "attemptsRemainingAtOutcome": 2, "maxAttempts": 3
  },
  "certificate": {
    "certificateId": "…", "status": "ISSUED", "certificateNumber": "CERT-9F2C1A4B7D",
    "documentReference": "local://certificates/CERT-9F2C1A4B7D",
    "courseName": "Fire Safety Awareness", "quizTitle": "…", "percentage": 77.78,
    "generationAttemptCount": 1, "failureCode": null, "failureMessage": null,
    "requestedAt": "…", "lastAttemptedAt": "…", "issuedAt": "…"
  },
  "cpd": {
    "cpdRecordId": "…", "status": "SYNCHRONISED",
    "attemptDate": "…", "scorePercentage": 77.78, "passed": true,
    "courseName": "Fire Safety Awareness",
    "externalReference": "CPD-LOCAL-…", "syncAttemptCount": 1,
    "failureCode": null, "failureMessage": null, "synchronisedAt": "…"
  },
  "attemptsUsed": 1, "attemptsRemaining": 2, "maxAttempts": 3, "mayReattempt": true
}
```

`passMarkPercentage` is the pass mark of **the attempt's own configuration version** — the bar the
learner sat under. `percentage >= passMarkPercentage` is a pass; the comparison is inclusive.

`certificate` is `null` for a fail, and one is never issued for one. Its `status` is `PENDING` while the
certificate service has not confirmed issue, `ISSUED` with a certificate number once it has, and
`FAILED` when the service refused — including the deliberate refusal `CERTIFICATE_ALREADY_ISSUED`, which
is how a second pass at the same quiz is prevented from minting a second document.

`cpd` carries exactly the four fields the CPD system is given: attempt date, score, pass/fail, course
name. Its status is independent of everything else: a CPD failure never changes the quiz result.

`attemptsRemaining` at the response root is recomputed **live** from UC-03's attempt count, which is
the figure to show a learner who failed. `attemptsRemainingAtOutcome` on the outcome is the audit copy
of what they were told at the time.

`404 OUTCOME_NOT_FOUND` when pass/fail has not been determined for this attempt.

### `POST /api/v1/attempts/{attemptId}/outcome`

Determines pass/fail from the attempt's **confirmed** score, records it, then requests a certificate
(on a pass) and a CPD synchronisation.

Idempotent: an attempt that already has an outcome keeps it — the verdict is never recomputed, because
the score behind it is immutable — but any certificate or CPD record still `PENDING` is driven again.
`created` in the response says which happened.

A certificate or CPD failure does **not** fail this call: the outcome is returned with the pending
status on the affected record. `409 RESULT_NOT_CONFIRMED` (retryable) when the attempt has no confirmed
score yet — pass/fail cannot be determined from a pending one.

### `POST /api/v1/attempts/{attemptId}/outcome/certificate/retry`

Re-drives certificate issuance for a passing attempt, reusing the existing certificate record so a
retry can never mint a second document. An already-issued certificate is returned unchanged.

Unlike `POST /outcome`, a failure here is reported: `502 CERTIFICATE_UNAVAILABLE` with
`retryable: true`. `409 CERTIFICATE_NOT_APPLICABLE` for a failed attempt. The quiz result and the
pass/fail outcome are unchanged in every case.

### `POST /api/v1/attempts/{attemptId}/outcome/cpd/retry`

Re-drives the CPD record, reusing the existing row so the learner's CPD cannot be double-logged.
`502 CPD_SYNC_UNAVAILABLE` (retryable) when the CPD system refuses.

### `GET /api/v1/attempts/{attemptId}/feedback`

The detailed feedback report (UC-06), served from the frozen document it was generated as.

```json
{
  "feedbackId": "…", "attemptId": "…", "resultId": "…", "outcomeId": "…",
  "status": "GENERATED", "statusLabel": "Feedback ready",
  "summary": {
    "totalScore": 7.0, "maximumMarks": 9.0, "percentage": 77.78, "passMarkPercentage": 60.0,
    "passed": true, "timeTakenSeconds": 750,
    "totalQuestions": 5, "correctCount": 4, "incorrectCount": 1, "unansweredCount": 0
  },
  "items": [{
    "position": 1, "questionId": "…", "questionVersion": 1, "questionReference": "Q-000001",
    "questionType": "SINGLE_CHOICE", "question": "…", "scenarioText": null,
    "learnerAnswer": { "labels": ["…"], "summary": "…" },
    "correctAnswer": { "labels": ["…"], "summary": "…" },
    "explanation": "…", "lessonReference": "Topic: Evacuation",
    "questionScore": 1.0, "maximumMarks": 1.0, "deduction": 0.0,
    "outcome": "CORRECT", "answered": true,
    "optionBreakdown": [{ "optionId": "A", "text": "…", "selected": true, "correct": true,
                          "markContribution": 1.0, "feedback": "…" }]
  }],
  "generationAttemptCount": 1, "failureCode": null, "failureMessage": null, "generatedAt": "…"
}
```

Every item carries the six things the specification asks for: the question, the learner's answer, the
correct answer, an explanation, the marks scored, and a lesson reference. For a multi-select,
`optionBreakdown` reports each option's correct/incorrect status and the marks it contributed.

**Missing content is reported, never invented.** A question with no authored explanation reads *“No
explanation was recorded for this question.”*, and one with no resolvable lesson reads *“No lesson
reference is recorded for this question.”* No text is generated, summarised or inferred.

A generated report is immutable: the database refuses to update it, and every input it was built from
was already frozen. Editing or retiring the question afterwards cannot change it.

`404 FEEDBACK_NOT_FOUND` when no report has been generated.

### `POST /api/v1/attempts/{attemptId}/feedback`

Generates the report and freezes it, or returns the one already generated with `replayed: true`.

`409 SCORE_NOT_CONFIRMED` (retryable) when the attempt has no confirmed score.
`502 FEEDBACK_GENERATION_FAILED` (retryable) when assembly failed — the score and the pass/fail
outcome are untouched, and the report stays `PENDING` for another attempt.

### `GET /api/v1/results` · `GET /api/v1/outcomes` · `GET /api/v1/feedback`

The learner's own results, outcomes and reports, newest attempt first, each optionally filtered with
`?quizId=`. Summary rows only: `{results | outcomes | reports: […], total: n}`.

### Error codes

| Code | Status | Meaning |
| ---- | ------ | ------- |
| `ATTEMPT_NOT_FOUND` | 404 | No such attempt, or not this learner's |
| `ATTEMPT_NOT_SUBMITTED` | 409 | Scoring was asked for while the attempt is still in progress |
| `RESULT_NOT_FOUND` | 404 | The attempt has never been scored |
| `RESULT_ALREADY_SCORED` | 409 | A confirmed score cannot be recomputed |
| `SCORING_FAILED` | 502 | Scoring could not complete; retryable |
| `RESULT_NOT_CONFIRMED` | 409 | Pass/fail or feedback was asked for from a pending score; retryable |
| `OUTCOME_NOT_FOUND` | 404 | Pass/fail has not been determined |
| `OUTCOME_ALREADY_DETERMINED` | 409 | A determined outcome is never re-determined |
| `CERTIFICATE_NOT_APPLICABLE` | 409 | A certificate was requested for a failed attempt |
| `CERTIFICATE_NOT_FOUND` | 404 | No certificate has been requested for this attempt |
| `CERTIFICATE_UNAVAILABLE` | 502 | The certificate service is unavailable or refused; retryable |
| `CPD_SYNC_UNAVAILABLE` | 502 | The CPD system is unavailable or refused; retryable |
| `SCORE_NOT_CONFIRMED` | 409 | Feedback was asked for from a pending score; retryable |
| `FEEDBACK_NOT_FOUND` | 404 | No report has been generated |
| `FEEDBACK_GENERATION_FAILED` | 502 | Report assembly failed; retryable |
| `PERSISTENCE_FAILED` | 503 | Nothing was written; safe to retry |


---

## AI coaching review mode

UC-07. Post-submission Socratic coaching on the questions a learner got wrong — "Review with Larry".
Shares UC-03's `/api/v1` prefix, because a coaching conversation is another statement about one
attempt.

Nine operations, and **every one of them is learner-scoped by the bearer token**. There is no learner
id in any path: the caller is resolved by `app/modules/identity`, exactly as for `/result`, `/outcome`
and `/feedback`. Every operation then re-checks in the domain that the attempt and the session belong
to that learner, because "a token resolved" and "this attempt is theirs" are different claims.

### What has to be true before coaching is offered

```
attempt exists → belongs to this learner → SUBMITTED → score CONFIRMED → feedback GENERATED
              → the question is in the attempt → the question was answered INCORRECTLY
              → the AI coaching service is reachable → coaching may begin
```

Every one of those is re-checked on **every** operation, not once at the start. A session that was
legitimately opened is not a licence: if the feedback report is withdrawn or a re-score turns a wrong
answer into a right one, the next message in a running conversation is refused.

The order matters. Ownership is decided before anything else, so probing another learner's attempt id
reveals only that it is not theirs — never which questions they got wrong. Service availability is
checked *last*, so a permanent refusal (`QUESTION_NOT_INCORRECT`) is reported as permanent even during
an outage, rather than telling a client to retry something that will never succeed.

### Two guarantees worth stating here

**The AI coach never receives the answer key.** It is removed at a sanitisation boundary before any
coaching context is built, so there is nothing in the model's input for a prompt to extract. **No
response below carries a correct answer either** — not on a review item, not on a coaching turn. The
learner reads the correct answer on their feedback report, which is a different endpoint with a
different purpose.

**Nothing here can change a result.** Every upstream port is read-only. A coaching failure of any
kind leaves the score, the pass/fail outcome and the feedback report exactly as they were.

### `GET /api/v1/attempts/{attemptId}/coaching/eligibility`

May coaching be offered, and for which questions. Optional `?questionId=` narrows it to one question.

**Never fails for an ineligible attempt.** This is the call a result screen makes before it renders
anything, so an attempt still in progress, an unreleased report and a correctly answered question all
come back as reasons rather than errors.

```json
{
  "attemptId": "…",
  "coachingAvailable": true,
  "reason": "ELIGIBLE",
  "message": null,
  "retryable": false,
  "details": null,
  "incorrectQuestionCount": 3,
  "questions": [
    { "questionId": "…", "position": 2, "outcome": "INCORRECT",
      "coachingAvailable": true, "reason": "ELIGIBLE" },
    { "questionId": "…", "position": 1, "outcome": "CORRECT",
      "coachingAvailable": false, "reason": "QUESTION_NOT_INCORRECT" }
  ]
}
```

`reason` is one of `ELIGIBLE`, `ATTEMPT_NOT_FOUND`, `NOT_ATTEMPT_OWNER`, `ATTEMPT_NOT_SUBMITTED`,
`SCORE_NOT_CONFIRMED`, `FEEDBACK_UNAVAILABLE`, `QUESTION_NOT_IN_ATTEMPT`, `QUESTION_NOT_INCORRECT` or
`SERVICE_UNAVAILABLE`. `retryable` is true only for the three that the state of the world may change:
an unconfirmed score, an unreleased report, an unavailable service.

`coachingAvailable` appears at every level of every response in this section. It is the backend half
of "show Review with Larry": UC-07 states whether the action may be offered and a client decides how
to render it.

### `GET /api/v1/attempts/{attemptId}/coaching/review`

Every incorrectly answered question, in delivery order.

Only questions the authoritative scoring result marks **incorrect** appear. Correct and unanswered
questions never enter the queue — a learner who ran out of time has no misconception to uncover, and
UC-04 is where a deployment that disagrees would say so.

Progress is **derived** from the coaching sessions that exist, not stored. There is no cursor to keep
in sync, so the queue is correct however a client abandoned a review, reloaded a day later, or ran two
devices at once. It does not require the AI service to be up.

```json
{
  "attemptId": "…",
  "totalIncorrect": 3,
  "completedCount": 1,
  "remainingCount": 2,
  "finished": false,
  "nextQuestionId": "…",
  "items": [
    { "questionId": "…", "position": 2, "status": "COMPLETED", "topic": "Reporting concerns",
      "sessionId": "…", "exchangeCount": 4, "coachingAvailable": false }
  ]
}
```

`status` is `PENDING`, `IN_PROGRESS` or `COMPLETED`. A session parked by an AI failure counts as
`IN_PROGRESS`, so the review returns the learner to it rather than silently skipping past.

### `POST /api/v1/attempts/{attemptId}/coaching/review/next`

Finish with the question currently being coached and return the next one.

```json
{ "completeCurrent": true }
```

`completeCurrent: false` looks ahead without leaving the current question. **Idempotent**: once every
question has been reviewed it keeps returning the finished queue with no next question, rather than
wrapping around. `409 NO_INCORRECT_QUESTIONS` when there is nothing to review — which means the
learner got everything right.

### `POST /api/v1/attempts/{attemptId}/coaching/questions/{questionId}`

Start — or resume — coaching for one incorrectly answered question. Returns the coach's opening
question. No body.

**Idempotent.** `(learnerId, attemptId, questionId)` is unique in the database, so repeating the call
resumes the same session (`outcome: "RESUMED"`) and never opens a second conversation or produces a
second opening question — even when two requests race.

```json
{
  "outcome": "STARTED",
  "coachingAvailable": true,
  "reason": null,
  "sanitization": {
    "removedFields": ["uc04.question_result.answer_key", "uc06.question_feedback.explanation"],
    "scrubbedFields": [],
    "forbiddenValueCount": 4,
    "contaminationFindings": [],
    "answerKeyExcluded": true
  },
  "session": {
    "sessionId": "…", "questionId": "…", "questionPosition": 2, "topic": "Reporting concerns",
    "mode": "SOCRATIC", "status": "ACTIVE", "exchangeCount": 0,
    "directExplanationAvailable": false, "directExplanationThreshold": 5,
    "exchangesUntilChoice": 5, "revision": 2, "…": "…"
  },
  "messageCount": 1,
  "messages": [{ "role": "COACH", "content": "…", "index": 0, "mode": "SOCRATIC", "…": "…" }]
}
```

`sanitization` reports what the answer-key boundary removed on the way in: **field names and counts,
never values**. Putting a removed value there would recreate the leak one layer down, in the place
people forget to look.

`outcome` is `STARTED`, `RESUMED` or `UNAVAILABLE`.

### `GET /api/v1/coaching/sessions/{sessionId}`

The session and its stored conversation. Readable during an AI outage: a learner can always see what
has already been said to them.

`404` when the session does not exist **or is not this learner's** — deliberately not `403`, so a
learner probing session ids cannot tell the two apart.

### `POST /api/v1/coaching/sessions/{sessionId}/messages`

One exchange: the learner's message answered by one coach turn.

```json
{ "message": "I thought recording it could wait until Monday." }
```

The learner's message is stored **before** the model is called, so a failure loses nothing and a retry
re-sends exactly that message. The exchange count moves only when both halves complete, so an outage
cannot push a learner closer to the five-exchange transition.

`400 BAD_REQUEST` for an empty message or one over `COACHING_MAX_MESSAGE_CHARS`.
`409 COACHING_SESSION_STATE_CONFLICT` when the session is not accepting messages.

### `POST /api/v1/coaching/sessions/{sessionId}/mode`

Choose Socratic coaching or a direct concept explanation.

```json
{ "mode": "DIRECT_EXPLANATION" }
```

After `directExplanationThreshold` completed exchanges the learner may ask for the concept to be
explained instead of continuing to be questioned. Choosing `DIRECT_EXPLANATION` produces the
explanation immediately, and that turn is **not** counted as an exchange — the learner asked to be
told, not to be asked.

`409 DIRECT_EXPLANATION_NOT_AVAILABLE` before the threshold. That refusal is the whole point of the
transition: without it, direct explanation would be an answer button on turn one. Switching back to
`SOCRATIC` is always allowed and produces no turn.

The explanation teaches the concept. The coach has never been given the answer key, so it has nothing
else to explain.

### `POST /api/v1/coaching/sessions/{sessionId}/retry`

Retry a coach turn that could not be produced. No body.

Three cases, none of which creates a session or duplicates an exchange: nothing said yet → produce
the opening question; the learner spoke last → answer them; the coach spoke last → return the stored
reply and reactivate a failed session. Retrying a healthy session is a no-op, not an extra model call.

### `POST /api/v1/coaching/sessions/{sessionId}/complete`

Mark the session `COMPLETED`, which advances the review queue past this question. Idempotent.

### An AI outage is a 503 **with a full body**

The four endpoints that produce a coach turn — start, messages, mode, retry — return `503` carrying
the session, the stored conversation and `coachingAvailable: false` with a `reason` code:

```json
{
  "outcome": "UNAVAILABLE",
  "coachingAvailable": false,
  "reason": "COACHING_SERVICE_UNAVAILABLE",
  "retryable": true,
  "reply": null,
  "session": { "status": "UNAVAILABLE", "exchangeCount": 0, "…": "…" },
  "messages": ["…"]
}
```

The learner keeps their session and their message, the client has the id it needs to retry, and
**nothing was invented to fill the gap**. `reason` is always an error code from the taxonomy below —
never a provider message, because an AI provider's error body can echo back the prompt it was sent,
and forwarding one would be a route around the sanitiser.

With no provider configured — the default — every coaching request reports itself unavailable this
way, and the rest of the quiz chain is completely unaffected. `GET /api/health` reports
`coachingProvider.configured` so an operator can see that at a glance.

### Error codes

| Code | Status | Meaning |
| ---- | ------ | ------- |
| `ATTEMPT_NOT_FOUND` | 404 | No such attempt |
| `LEARNER_NOT_AUTHORIZED` | 403 | The attempt belongs to another learner; says nothing else about it |
| `ATTEMPT_NOT_SUBMITTED` | 409 | The quiz is not over; retryable |
| `SCORE_NOT_CONFIRMED` | 409 | No confirmed score, so nothing is known to be incorrect; retryable |
| `FEEDBACK_UNAVAILABLE` | 409 | The feedback report has not been released; retryable |
| `QUESTION_NOT_IN_ATTEMPT` | 404 | The question is not part of this attempt |
| `QUESTION_NOT_INCORRECT` | 409 | The question was answered correctly; **not** retryable |
| `NO_INCORRECT_QUESTIONS` | 409 | Nothing to review |
| `COACHING_SESSION_NOT_FOUND` | 404 | No such session, or not this learner's |
| `DUPLICATE_COACHING_SESSION` | 409 | The natural key already holds a session |
| `COACHING_SESSION_STATE_CONFLICT` | 409 | The session cannot do what was asked in its current state |
| `DIRECT_EXPLANATION_NOT_AVAILABLE` | 409 | Requested before the exchange threshold |
| `COACHING_EXCHANGE_LIMIT_REACHED` | 409 | The session hit its hard exchange ceiling |
| `COACHING_UPSTREAM_UNAVAILABLE` | 503 | UC-03, UC-04 or UC-06 could not be read; retryable |
| `COACHING_SERVICE_UNAVAILABLE` | 503 | The AI coaching service could not be reached; retryable |
| `COACHING_TIMEOUT` | 504 | The model did not answer in time; retryable |
| `INVALID_COACHING_RESPONSE` | 502 | The provider answered with nothing usable; retryable |
| `COACHING_POLICY_VIOLATION` | 502 | The model would not follow the coaching policy; retryable |
| `ANSWER_KEY_CONTAMINATION` | 500 | Answer-key material was found in something about to reach the model. **Fails closed and is not retryable** — coaching is refused for that question until someone looks at it, rather than stripping harder and carrying on. Carries *where* it was found, never *what*. |


---

## Shared

### `GET /api/meta`

The configuration vocabulary and numeric limits, so a client never hardcodes them.

```json
{
  "questionTypes": [{ "value": "SINGLE_CHOICE", "label": "Single choice" }, "…"],
  "deliveryModes": [{ "value": "practice", "label": "Practice (…)" }, "…"],
  "questionPresentations": [{ "value": "ALL_AT_ONCE", "label": "All questions at once" }, "…"],
  "limits": { "questionCount": { "min": 1, "max": 100 }, "…": {} },
  "maxConfigurationTopics": 20,
  "deliverableQuestionStatuses": ["ACTIVE"]
}
```

### `GET /api/health` · `GET /api/health/live`

`/health` is **readiness**: it checks the database and answers `503` when it is unreachable, so a load
balancer can take a broken instance out of rotation. `/health/live` is **liveness**: it touches
nothing, so a database blip cannot restart an otherwise healthy process. Neither needs
authentication.

### `GET /api/session`

`{"user": {id, displayName, role} | null}`. Under `ENVIRONMENT=development` or `test` it also returns
`users` — the local development identities and their tokens, for the test UI's identity switcher.
Never returned in any other environment.

---

## Questions

### `GET /questions` — list

| Query param | Notes |
| ----------- | ----- |
| `search` | Matches question text, scenario text, reference or external ref |
| `type` | Repeatable |
| `status` | Repeatable |
| `topicId`, `topicSlug` | Filter by topic |
| `difficulty` | `EASY` \| `MEDIUM` \| `HARD` |
| `deliverableOnly` | `true` returns only questions eligible for future delivery |
| `page`, `pageSize` | Default `1`, `25` (max `200`) |
| `sortBy`, `sortDir` | `createdAt` \| `updatedAt` \| `reference` \| `type` \| `status`; `asc` \| `desc` |

```json
{
  "items": [{
    "id": "…", "reference": "Q-000001", "type": "SINGLE_CHOICE", "status": "ACTIVE",
    "questionText": "…", "topics": [{ "id": "…", "slug": "networking", "name": "Networking" }],
    "points": 1.0, "scoringStrategy": "ALL_OR_NOTHING", "difficulty": "EASY",
    "version": 1, "optionCount": 4, "usageCount": 0, "isDeliverable": true,
    "createdAt": "…", "updatedAt": "…"
  }],
  "meta": { "page": 1, "pageSize": 25, "total": 1, "totalPages": 1,
            "hasNext": false, "hasPrevious": false }
}
```

### `POST /questions` — create → `201`

```json
{
  "type": "SINGLE_CHOICE",
  "questionText": "Which OSI layer routes packets between networks?",
  "explanation": "Layer 3 handles logical addressing and routing.",
  "difficulty": "EASY",
  "status": "ACTIVE",
  "externalRef": "SRC-001",
  "topics": ["Networking", "OSI Model"],
  "options": [
    { "label": "A", "text": "Layer 2 - Data Link", "isCorrect": false },
    { "label": "B", "text": "Layer 3 - Network",   "isCorrect": true  },
    { "label": "C", "text": "Layer 4 - Transport", "isCorrect": false },
    { "label": "D", "text": "Layer 7 - Application","isCorrect": false }
  ],
  "scoring": { "points": 1, "scoringStrategy": "ALL_OR_NOTHING", "penaltyPerIncorrect": 0 }
}
```

`topics` are names (created on demand); `topicIds` accepts existing ids instead. For `SCENARIO` add
`scenarioText` and mark one option `isPrimary`. For `DRAG_TO_ORDER` give each option a
`correctPosition` forming a complete `1..n` sequence and omit `isCorrect`.

Errors: `422 VALIDATION_FAILED`, `409 DUPLICATE_QUESTION`, `409 EXTERNAL_REF_ALREADY_USED`.

### `GET /questions/{questionId}`

Returns the full question including `options`, `topics`, `correctLabels`, `correctOrder`,
`primaryLabel`, `isDeliverable` and a `usage` summary:

```json
"usage": { "total": 3, "completed": 2, "inProgress": 1,
           "hasHistory": true, "canHardDelete": false }
```

Works identically for retired questions — retirement withholds a question from *delivery*, never
from *reading*.

### `PATCH /questions/{questionId}` — update

Partial: unset fields keep their values, and the **merged** result is re-validated in full, so an
edit can never leave a question invalid. A content change bumps `version` and writes a new snapshot;
attempts already recorded stay pinned to the version they were delivered.

Metadata-only edits (`explanation`, `difficulty`, `externalRef`, `topics`) do **not** cut a new
version. Scoring changes do. Supplying a new `scoringStrategy` without a `penaltyPerIncorrect` resets
the penalty, since it is only meaningful for `PARTIAL_CREDIT_WITH_PENALTY`.

Errors: `422 VALIDATION_FAILED`, `409 QUESTION_RETIRED`, `409 USE_RETIRE_ENDPOINT`,
`409 USE_REACTIVATE_ENDPOINT`, `409 DUPLICATE_QUESTION`.

### `POST /questions/{questionId}/retire`

```json
{ "reason": "Superseded by the 2026 syllabus" }
```

Sets `status: RETIRED`, records `retiredAt` / `retiredReason` / `retiredBy`. The question keeps its
row, id, reference, options, topics and every snapshot; it is excluded from all future delivery and
remains fully reportable.

Errors: `409 QUESTION_ALREADY_RETIRED`.

### `POST /questions/{questionId}/reactivate`

Returns a retired question to `ACTIVE`. Errors: `409 QUESTION_NOT_RETIRED`,
`409 DUPLICATE_QUESTION` (if an equivalent question went live meanwhile).

### `DELETE /questions/{questionId}`

Hard delete, permitted **only** when the question has no recorded usage at all.

```json
{ "id": "…", "reference": "Q-000001", "deleted": true,
  "message": "Q-000001 had no attempt history and was permanently deleted." }
```

Errors: `409 QUESTION_HAS_HISTORY` — *"…has been used by 3 quiz attempt(s) (2 completed) and cannot
be deleted. Retire it instead…"*. Also enforced by `ON DELETE RESTRICT` in the database.

### `GET /questions/{questionId}/versions` · `GET /questions/{questionId}/versions/{version}`

Every frozen snapshot, oldest first. Each carries the full `payload` (options with `position` and
`correctPosition`, `correctLabels`, `correctOrder`, frozen topic names).

### `GET /questions/{questionId}/usages`

Attempts that used this question — `attemptRef`, `snapshotVersion`, `attemptStatus`,
`learnerResponse`, `presentationOrder`, `isCorrect`, `awardedPoints`. Resolvable after retirement.

### `POST /questions/{questionId}/topics` · `DELETE /questions/{questionId}/topics/{topicId}`

```json
{ "topicIds": ["…"], "topicNames": ["Routing"], "replace": false }
```

Permitted on retired questions (tagging is metadata and cannot affect history). Removing the last
topic returns `409 LAST_TOPIC_CANNOT_BE_REMOVED`.

---

## Topics

| Endpoint | Notes |
| -------- | ----- |
| `GET /topics` | `includeInactive` (default `true`), `search`. Each row carries `questionCount` |
| `POST /topics` → `201` | `{ "name": "Networking", "description": null, "isActive": true }`. `409 TOPIC_ALREADY_EXISTS` on a case-insensitive clash |
| `GET /topics/{topicId}` | |
| `PATCH /topics/{topicId}` | Rename / describe / deactivate. Renaming updates every tagged question |
| `DELETE /topics/{topicId}` | `409 TOPIC_IN_USE` unless `?force=true`, which also detaches it from questions. Historical reports are unaffected — snapshots freeze topic names |

---

## CSV import

| Endpoint | Notes |
| -------- | ----- |
| `GET /imports/template` | The documented CSV template, one worked example per question type |
| `GET /imports/template/guide` | The same format as JSON, for the UI's help panel |
| `POST /imports` → `201` | `multipart/form-data` with a `file` field, **or** a raw `text/csv` body (set `X-Filename` to name it) |
| `GET /imports` | Past runs, most recent first. `limit`, `offset` |
| `GET /imports/{importId}` | Re-read a result, including every row-level error |

`POST /imports` returns the full report — `totalRows`, `importedRows`, `rejectedRows`, the imported
rows with their new references, and every rejected row with its row number, field, code and message.
See [CSV_IMPORT.md](CSV_IMPORT.md).

Errors: `400` for a whole-file failure (nothing imported, run recorded as `FAILED`), `413` beyond
`CSV_MAX_BYTES`.

---

## Delivery and historical reporting

These are the integration seam for the quiz-delivery module — see [INTEGRATION.md](INTEGRATION.md).

### `GET /delivery/pool`

Questions eligible for a **future** quiz. Filters: `topicId`, `topicSlug`, `type` (all repeatable),
`difficulty`, `limit`. **Only `ACTIVE` questions can appear**, and the response deliberately
withholds the answer key — each option carries only `label`, `text` and `position`.

### `POST /delivery/usages` → `201`

```json
{ "attemptRef": "attempt-123", "questionId": "…", "learnerRef": "learner-42",
  "presentationOrder": ["C", "A", "D", "B"] }
```

Pins the question's current snapshot to the attempt. `presentationOrder` records the order actually
shown — kept separate from the answer key.

Errors: `409 QUESTION_NOT_DELIVERABLE`, `409 USAGE_ALREADY_RECORDED`, `422` for unknown labels.

### `PATCH /delivery/usages/{usageId}`

```json
{ "selectedLabels": ["B"], "attemptStatus": "COMPLETED" }
{ "orderedLabels": ["A", "B", "C", "D"], "attemptStatus": "COMPLETED" }
```

Records the learner's response and scores it **against the pinned snapshot**, so a score stays
reproducible after the question is edited or retired. Drag-to-order grading uses the stored
`correctPosition` and ignores the presentation order entirely.

Errors: `409 USAGE_ALREADY_COMPLETED`, `422 UNKNOWN_OPTION_LABEL`, `422 RESPONSE_SHAPE_MISMATCH`,
`422 TOO_MANY_SELECTIONS`, `409 SNAPSHOT_UNREADABLE`.

### `GET /reporting/attempts/{attemptRef}`

The historical report, rendered entirely from frozen snapshots.

```json
{
  "attemptRef": "attempt-123", "learnerRef": "learner-42", "attemptStatus": "COMPLETED",
  "questionCount": 2, "totalAwardedPoints": 4.0, "totalMaxPoints": 5.0,
  "items": [{
    "questionId": "…", "questionReference": "Q-000001", "snapshotVersion": 1,
    "currentQuestionStatus": "RETIRED",
    "type": "SINGLE_CHOICE", "questionText": "…", "scenarioText": null, "explanation": "…",
    "options": [ { "label": "A", "text": "…", "position": 1, "isCorrect": false, "correctPosition": null } ],
    "correctLabels": ["B"], "correctOrder": [], "topics": ["Networking"],
    "learnerResponse": { "selectedLabels": ["B"], "orderedLabels": [] },
    "presentationOrder": null,
    "isCorrect": true, "awardedPoints": 1.0, "maxPoints": 1.0,
    "deliveredAt": "…", "completedAt": "…"
  }]
}
```

`currentQuestionStatus` is the live status, reported as context only. Everything else comes from the
snapshot, which is why retiring or editing the question cannot change this response.

---
