# UC-04 — Course Content Coaching

Standalone backend use case: coaching a learner **inside a course-linked session**, grounded in
the linked lesson's own content, with quiz-answer protection and an "explain differently" memory.

Built independently of UC-01/02/03. It imports nothing from them and assumes nothing about them.
Every external dependency sits behind an internal contract with a deterministic mock behind it,
so integration is an adapter swap — see [INTEGRATION.md](INTEGRATION.md).

---

## Quick start

```bash
npm install
npm test          # 143 tests
npm run typecheck
npm start         # http://localhost:4004/api/v1/uc04
```

```bash
curl -X POST http://localhost:4004/api/v1/uc04/coaching/turns \
  -H 'content-type: application/json' \
  -H 'x-user-id: user_learner_1' \
  -d '{"session_id":"sess_main_1","question":"What does consent actually mean in this lesson?"}'
```

---

## Architecture

```
                 HTTP  (src/api)
                   |  authenticate -> validate (allow-list) -> delegate
                   v
        CourseCoachingService            <- src/core, the only place business rules live
                   |
                   v
        Internal contracts (ports)       <- src/contracts, the stable seam
   ________________|________________________________________
  |          |          |         |        |        |       |
Course   Enrollment  Lesson    Context  Section  Explan.  Quiz      Activity /
Provider  Provider   Content   Provider Retriever Engine  Classifier History / FP log
                     Provider
  |          |          |         |        |        |       |
  v          v          v         v        v        v       v
        Mock + in-memory adapters        <- src/adapters (replaced at integration)
```

Two rules keep the seam honest:

1. **`src/core` and `src/domain` import from `src/contracts` only.** They never import an adapter,
   never see vendor JSON, and never touch HTTP.
2. **`src/core/lesson-normalizer.ts` is the only component allowed to read a provider payload.**
   Everything downstream works on the internal `LessonContext`.

### Turn pipeline

```
session binding (authoritative course/lesson identity)
  -> ownership check (principal owns the session; client assertions must match)
  -> ENROLLMENT GUARD  ......... fails closed; nothing below runs without it
  -> learner context (optional)
  -> course lookup
  -> lesson fetch + normalize .. failure => LESSON_UNAVAILABLE, not a dead session
  -> quiz classification ....... always runs, lesson available or not
  -> branch:
       QUIZ_ANSWER_REQUEST -> refuse + explain the concept, never the answer
       UNCERTAIN           -> safe clarification
       else                -> retrieve -> resolve scope -> explain -> log
```

---

## Files

| Path | What it is |
| --- | --- |
| `src/domain/enums.ts` | Shared vocabulary: statuses, scopes, framings, activity types |
| `src/domain/lesson-context.ts` | **Internal lesson contract** — `LessonContext`, sections, concepts, related lessons |
| `src/domain/coaching.ts` | Service request/response contract |
| `src/contracts/*.ts` | The ports (see table below) |
| `src/core/course-coaching-service.ts` | UC-04 orchestration and all invariants |
| `src/core/lesson-normalizer.ts` | Provider payload -> `LessonContext`; drops anything unverifiable |
| `src/core/retrieval/keyword-section-retriever.ts` | Deterministic IDF-weighted section/concept retrieval |
| `src/core/explanation/framing-selector.ts` | Unused-first, then least-recently-used framing policy |
| `src/core/explanation/fingerprint.ts` | Stable fingerprint + near-duplicate detection |
| `src/core/explanation/template-explanation-engine.ts` | Deterministic lesson-grounded explanation generator |
| `src/core/quiz/heuristic-quiz-intent-classifier.ts` | Weighted multi-signal quiz-intent detection |
| `src/core/quiz/answer-leak-guard.ts` | Last-line defence: strips answer-revealing sentences |
| `src/adapters/mock/*` | Deterministic mock providers + fixtures |
| `src/adapters/memory/*` | In-memory activity repo, explanation history, false-positive log |
| `src/composition-root.ts` | **The integration seam** — the one file that names implementations |
| `src/api/*` | Express surface + allow-list validation |
| `tests/*` | 143 tests |

---

## Internal contracts

| Port | Responsibility | Default implementation |
| --- | --- | --- |
| `CourseProvider` | Course name + the lesson ids that really exist in it | `MockCourseProvider` |
| `EnrollmentProvider` | Is this user enrolled on this course | `MockEnrollmentProvider` |
| `LessonContentProvider` | Raw lesson payload | `MockLessonContentProvider` |
| `ContextProvider` | Session binding (authoritative) + optional learner context | `MockContextProvider` |
| `SectionRetriever` | Question -> section/concept/related lesson | `KeywordSectionRetriever` |
| `ExplanationEngine` | Framed, grounded explanation text | `TemplateExplanationEngine` |
| `QuizIntentClassifier` | Quiz-answer vs concept-learning intent | `HeuristicQuizIntentClassifier` |
| `ActivityRepository` | Progress/activity events + explained-concept view | `InMemoryActivityRepository` |
| `ExplanationHistoryStore` | Session-scoped explanation attempts | `InMemoryExplanationHistoryStore` |
| `FalsePositiveLog` | Suspected classifier false positives | `InMemoryFalsePositiveLog` |
| `Clock` / `IdGenerator` | Time and ids, injected for determinism | `SystemClock` / `SequentialIdGenerator` |

---

## API contract

### `POST /api/v1/uc04/coaching/turns`

Header `x-user-id` carries the authenticated principal (placeholder for the company gateway).

Body — **allow-list**; every other field is ignored and echoed back in `ignored_request_fields`:

| Field | Required | Notes |
| --- | --- | --- |
| `session_id` | yes | Course/lesson identity is resolved from this, server-side |
| `question` | yes unless `intent=EXPLAIN_DIFFERENTLY` | max 2000 chars |
| `intent` | no | `ASK` (default) or `EXPLAIN_DIFFERENTLY` |
| `concept_id` | no | Hint only; validated against the loaded lesson |
| `expected_course_id` / `expected_lesson_id` | no | Assertions; a mismatch is a 403, never a redirect |

Answered response:

```json
{
  "status": "ANSWERED",
  "session_id": "sess_main_1",
  "course_id": "course_dp_101",
  "lesson_id": "lesson_dp_01",
  "source_scope": "LESSON",
  "section_id": "sec_consent",
  "concept_id": "concept_consent",
  "answer": "...",
  "concept_explanation": null,
  "framing": "DIRECT",
  "actions": ["EXPLAIN_DIFFERENTLY"],
  "quiz_protected": false,
  "answer_revealed": false,
  "free_form_available": false,
  "notice": null,
  "related_lesson_id": null,
  "related_lessons": [{ "lesson_id": "lesson_dp_02", "title": "Data Subject Rights", "relationship": "follow-on lesson" }],
  "diagnostics": { "lesson_loaded": true, "enrollment_verified": true, "retrieval_score": 0.7611, "quiz_label": "CONCEPT_LEARNING_REQUEST", "quiz_confidence": 1, "explanation_attempt_index": 0, "framings_used": [], "degraded": [] }
}
```

Quiz-protected (HTTP 200 — a refusal is a normal outcome, not an error):

```json
{ "status": "QUIZ_PROTECTED", "quiz_protected": true, "answer_revealed": false,
  "answer": null, "concept_explanation": "...", "actions": [],
  "notice": "I can explain the concept being tested. I will not confirm or reveal answers." }
```

Lesson unavailable (HTTP 200 — the session is not blocked):

```json
{ "status": "LESSON_UNAVAILABLE", "source_scope": "GENERAL", "free_form_available": true,
  "actions": ["EXPLAIN_DIFFERENTLY", "START_FREE_FORM_SESSION"],
  "notice": "The linked lesson content is temporarily unavailable, ..." }
```

Statuses and codes:

| Status | HTTP | Meaning |
| --- | --- | --- |
| `ANSWERED` | 200 | Answer produced; see `source_scope` for where it came from |
| `QUIZ_PROTECTED` | 200 | Answer withheld; concept help offered |
| `NEEDS_CLARIFICATION` | 200 | Classifier unsure; nothing revealed |
| `LESSON_UNAVAILABLE` | 200 | Lesson could not be loaded; general coaching offered |
| `ENROLLMENT_REQUIRED` | 403 | Not enrolled — no lesson content |
| `SESSION_FORBIDDEN` | 403 | Session not owned by the caller, or asserted identity mismatch |
| `SESSION_NOT_FOUND` / `COURSE_NOT_FOUND` | 404 | |
| `ENROLLMENT_UNVERIFIED` / `CONTEXT_UNAVAILABLE` | 503 | Fail-closed: no lesson content |

`source_scope` is decided by UC-04, never by the engine: `LESSON` (the linked lesson),
`COURSE` (a real related lesson in the same course), `GENERAL` (outside the lesson — never
attributed to it), `NONE` (nothing substantive returned).

### `GET /api/v1/uc04/coaching/sessions/:sessionId/explained-concepts`

Explained-concept rollup for future UCs (e.g. gap tracking). Session-owner only: 401 without a
principal, 403 for another user's session.

---

## Quiz protection design

Three layers, so no single component is load-bearing:

1. **`QuizIntentClassifier`** (replaceable port). The default is weighted multi-signal, not a
   keyword list. Hard answer-seeking signals (asking for the answer, the correct option, a
   revealing hint, an elimination) score high; soft ones (confirmation seeking, explanation
   suppression, assessment context) add up. Learning intent *discounts* the score, but that
   discount is capped when a hard signal fired — so `explain which option is correct` is blocked
   while `explain the principle this question tests` is not. Phrases like `don't explain` are
   stripped before learning detection so a refusal cannot earn learning credit.
2. **Three-way outcome.** `QUIZ_ANSWER_REQUEST` blocks; `UNCERTAIN` returns a learning-oriented
   clarification rather than guessing; `CONCEPT_LEARNING_REQUEST` answers normally. A classifier
   that throws is treated as `UNCERTAIN` — it fails safe.
3. **`AnswerLeakGuard`.** Every protected response is scanned and sentences that would reveal or
   confirm an answer (option letters, "the correct answer", elimination, "almost right") are
   dropped before the response leaves the service.

Protection is server-side and unconditional: the service input type has no protection switch, the
API validator ignores unknown body fields, and the assessment-context hint is derived from lesson
data — nothing a client sends can weaken it. Suspected false positives (blocked or uncertain turns
that showed learning intent or named a real lesson concept) are written to `FalsePositiveLog` with
the question, classifier result, final decision and timestamp — and no lesson content.

---

## Explanation-history design ("explain differently")

Per `(session_id, concept_id)`:

1. **Framing plan** — unused framings first in preference order (`DIRECT`, `ANALOGY`,
   `PRACTICAL_EXAMPLE`, `STEP_BY_STEP`, `CONTRAST`, `SCENARIO`), then least-recently-used.
2. **Generate** with the chosen framing and a variant seed derived from the attempt count.
3. **Fingerprint** the result: content tokens, stopwords removed, de-duplicated and sorted, hashed
   (FNV-1a). Reordering or repunctuating an explanation cannot fake novelty.
4. **Reject duplicates** — exact fingerprint collision, or Jaccard similarity ≥ 0.82 against any
   earlier attempt — and walk to the next framing.
5. **Exhaustion** — if every framing has been used and every candidate collides, the
   least-recently-used framing is used with a distinct closing line, so the response is never
   byte-identical to a previous one. Verified over 12 consecutive turns.

History is session-scoped: the same user asking the same thing in a new session starts at
`DIRECT`. Every `EXPLAIN_DIFFERENTLY` turn is also written as a difficulty **signal**
(`difficulty_signal: true`, `signal_type: EXPLAIN_DIFFERENTLY`) — an observation for later UCs,
not a diagnosis.

---

## Persistence

Development-grade and in-memory by design; UC-04 does not own the company database.
`ActivityRepository`, `ExplanationHistoryStore` and `FalsePositiveLog` are ports. Activity and
false-positive writes are best-effort: a persistence outage degrades (recorded in
`diagnostics.degraded`) but never fails a learner's turn.

---

## Security and access control

| Control | Where |
| --- | --- |
| Principal comes from the authenticated request, never the body | `src/api/validation.ts` |
| Course/lesson identity comes from the session binding, never the body | `CourseCoachingService` step 1 |
| Client assertions must match the binding, and can never redirect it | step 2 |
| Enrollment verified before any content call; failure fails closed | step 3 |
| Client-supplied `concept_id` validated against the loaded lesson | `resolveExplainDifferentlyTarget` |
| Body is an allow-list — injected `lesson_content`/`sections` are inert | `src/api/validation.ts` |
| Quiz protection cannot be disabled by any request parameter | no switch exists in the input type |
| Activity endpoint is session-owner only | `listExplainedConcepts` |
| Terminal/failure responses never carry lesson content | `terminal()` |
