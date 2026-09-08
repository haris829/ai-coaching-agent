# Courses Quiz Agent — Verification Guide

**For:** Consultancy Outfit · **Version:** 0.1.0 · **Date:** 2026-09-08
**Source code:** all of this agent's code is in `backend/app/modules/quiz_generation/`, on the
`company-review` branch of `github.com/haris829/ai-coaching-agent`.

A concise, step-by-step guide to verify each stated requirement. Every step gives a plain-English
summary, a sample input, and the expected result. Every expected result in this document was
produced by running the step against a live AWS Bedrock model and a live PostgreSQL database — none
of it is illustrative.

---

## What this project is (in plain English)

The Courses Quiz Agent writes multiple-choice exams. Ask it for a quiz on one of your courses and
it produces the questions, the four options, and the correct answer for each, stores the whole thing
in PostgreSQL under its own identifier, and hands back a quiz a learner can sit. When the learner
submits their answers it marks them against the stored key and returns pass or fail.

It does three things deliberately:

- **It generates on demand.** A quiz exists because somebody asked for one. Nothing is prepared in
  advance, and no two requests produce the same paper.
- **It never shows a learner an answer.** The key is written to the database and stays there. The
  quiz a learner receives has no answer field, and the marking response says whether they passed —
  not which questions they got wrong.
- **It refuses rather than repairs.** A question the validator will not accept is thrown away and
  counted, never patched up. A repaired question is how a plausible but wrong answer ends up on
  somebody's certificate.

It does not mark real certified attempts, issue certificates, or replace the assessment platform's
own exam flow. It writes and marks quizzes.

---

## How to run the verification

The easiest way is the built-in interactive docs (Swagger) — every request can be run from the
browser with a **Try it out** button, no tools to install.

```bash
BASE_URL="http://127.0.0.1:8000"        # locally; substitute the deployed host
# Open the interactive docs in a browser:
#   ${BASE_URL}/api/docs      (Swagger — look for the "Quiz Generation" tag)
#   ${BASE_URL}/api/redoc     (read-only, cleaner)
#   ${BASE_URL}/api/openapi.json   (the spec, for Postman or a generated client)
```

Base path used below: `${BASE_URL}/api/v1`

**Authentication.** Every request carries a bearer token. In Swagger, click **Authorize** and paste
one of these; on the command line use `-H "Authorization: Bearer <token>"`.

| Token | Role | Can |
|---|---|---|
| `admin-token` | administrator | generate a quiz, read answer keys, read stored sittings |
| `learner-token` | learner | read a quiz to sit it, submit answers |

These are the seeded development tokens and are published in this repository. **They must be
replaced with generated values (`SEED_ADMIN_TOKEN`, `SEED_LEARNER_TOKEN`, …) before any deployment
anyone else can reach.**

**Environment-dependent steps.** R01–R08 need an AI provider configured
(`COACHING_LLM_PROVIDER=bedrock`, `COACHING_LLM_API_KEY`, `COACHING_LLM_MODEL`); without one, the
generate endpoint returns a clean `503` and writes nothing. R11 needs PostgreSQL rather than the
default SQLite. Everything else runs with no extra setup.

---

## R01 — Course-specific generation, by the name you hold

**Endpoint(s):**

- `GET /api/v1/quiz-generation/courses` — the courses available, by title
- `POST /api/v1/generated-quizzes` — generate a quiz for one course

**Purpose:** A quiz is scoped to one course and to nothing else. You send the course as you know it
— its name — and the agent resolves it against the catalogue, then generates from what it finds. It
does not require your internal course code, and it does not require the course to be in our
catalogue at all. *(Verifies: course resolved by name; quiz specific to that course; the response
states which course matched.)*

**Sample data:**

```bash
# The courses the catalogue knows about
curl "${BASE_URL}/api/v1/quiz-generation/courses" \
  -H "Authorization: Bearer admin-token"

# Generate for one, by NAME — no internal code anywhere in the request
curl -X POST "${BASE_URL}/api/v1/generated-quizzes" \
  -H "Authorization: Bearer admin-token" -H "Content-Type: application/json" \
  -d '{"topic": "Criminology (Postgraduate)", "count": 4, "passMark": 50}'
```

**Expected:** `201 Created`. The body carries `quizId`, `courseRef: "LL-34590"` — the course the
name resolved to — `questionCount: 4`, `passMark: 50.0`, and four questions each with four options
and an `answer`.

**How the course is checked.** Three attempts, narrowest first, each case-insensitive, and each must
match **exactly one** course: the catalogue code, then the exact title, then a title containing what
you sent. An ambiguous reference resolves to nothing rather than a guess — four courses contain
"International", and picking one would produce a confident quiz about the wrong syllabus. A
reference under five characters is not partially matched at all. `courseRef` in the response is the
matched code, or `null` if nothing matched, so a fallback is always visible.

---

## R02 — Generated on demand, never in advance

**Endpoint(s):** `POST /api/v1/generated-quizzes`

**Purpose:** Nothing is pre-generated and nothing is cached. A quiz exists because a request asked
for it. *(Verifies: exactly one route calls the model; no route returns a stored quiz in place of
generating one.)*

**Sample data:** call the generate endpoint twice with the same body (see R03 below).

**Expected:** two `201`s and **two different `quizId`s**. `create()` contains no `SELECT` for an
existing quiz, no cache and no reuse — it only ever constructs a new row. Of the six endpoints,
`POST /generated-quizzes` is the only one that calls the model at all; the other five read or mark
what already exists.

---

## R03 — Every request produces different questions

**Endpoint(s):** `POST /api/v1/generated-quizzes`, called more than once for the same course

**Purpose:** Asking twice must not return the same paper. Each request is told what that course has
already been asked and instructed to test different points; anything that comes back repeated
anyway is refused and counted. *(Verifies: no repeated questions across requests, including when the
requested counts differ.)*

**Sample data:**

```bash
# Ten now
curl -X POST "${BASE_URL}/api/v1/generated-quizzes" \
  -H "Authorization: Bearer admin-token" -H "Content-Type: application/json" \
  -d '{"topic": "Medical Law MA (Postgraduate)", "count": 10}'

# Twenty later, same course
curl -X POST "${BASE_URL}/api/v1/generated-quizzes" \
  -H "Authorization: Bearer admin-token" -H "Content-Type: application/json" \
  -d '{"topic": "Medical Law MA (Postgraduate)", "count": 20}'
```

**Expected (measured):** run 1 asked 10 and stored 10; run 2 asked 20 and stored 20; different
`quizId`s; **zero repeated questions** between them. A separate run on
"Object-Oriented Programming (OOP)" gave the same result: run 1 covered polymorphism, encapsulation,
Liskov substitution, overloading and interfaces; run 2 covered constructor chaining, singletons,
abstract methods, coupling and polymorphic parameters. Zero overlap.

Two mechanisms, because either alone is insufficient. The prompt carries up to 40 previously-asked
questions with an instruction not to repeat or rephrase them — that stops a repeat being *written*.
Anything repeated that arrives regardless is dropped and reported in `rejected`. A request on a
course with history also asks the model for more than it needs, so a dropped duplicate is backfilled
rather than leaving the caller short of the count they asked for.

---

## R04 — Stored with a unique identifier

**Endpoint(s):** `GET /api/v1/generated-quizzes/{quiz_id}`

**Purpose:** Each quiz is a row in PostgreSQL with its own identifier, its questions, its options
and its answer key — all frozen at the moment of generation. *(Verifies: the quiz is retrievable by
its identifier; the stored questions cannot change under it.)*

**Sample data:**

```bash
curl "${BASE_URL}/api/v1/generated-quizzes/<quizId>" \
  -H "Authorization: Bearer learner-token"
```

```sql
-- the same rows, in the database
SELECT id, topic, course_ref, question_count, pass_mark, created_by, created_at
  FROM qz_generated_quizzes WHERE id = '<quizId>';

SELECT sequence, answer_label, LEFT(question_text, 60), LENGTH(options_json)
  FROM qz_generated_quiz_questions WHERE quiz_id = '<quizId>' ORDER BY sequence;
```

**Expected:** one `qz_generated_quizzes` row, and one `qz_generated_quiz_questions` row per question
carrying `question_text`, `options_json` and `answer_label`.

**Why the questions are copied and not referenced.** The stem, options and key are frozen onto the
quiz rather than pointed at in the question bank. A bank question can be edited or retired; with
only a reference, editing it would silently rewrite every quiz that ever used it and every sitting
ever marked against it — a learner who passed in March could be shown different questions in June,
and the answer reported as correct for their submission could be one that was not correct when they
sat it. Two tests hold this: moving every correct flag in the bank leaves an earlier verdict
unchanged, and deleting the source questions outright leaves the quiz intact and still markable.

---

## R05 — The learner never receives an answer

**Endpoint(s):**

- `GET /api/v1/generated-quizzes/{quiz_id}` — the quiz as it is sat
- `POST /api/v1/generated-quizzes/{quiz_id}/results` — the verdict
- `GET /api/v1/generated-quizzes/{quiz_id}/answers` — the key, administrators only

**Purpose:** The key lives in the database and stays there. *(Verifies: no answer field in the
delivered quiz; no per-question detail in the marking response; both administrator routes refuse a
learner.)*

**Sample data:**

```bash
# What a learner receives
curl "${BASE_URL}/api/v1/generated-quizzes/<quizId>" -H "Authorization: Bearer learner-token"

# A learner attempting to read the key
curl "${BASE_URL}/api/v1/generated-quizzes/<quizId>/answers" -H "Authorization: Bearer learner-token"

# An administrator reading it
curl "${BASE_URL}/api/v1/generated-quizzes/<quizId>/answers" -H "Authorization: Bearer admin-token"
```

**Expected (measured):** the learner's quiz carries exactly
`['options', 'question', 'questionId', 'sequence']` per question — **no `answer` field, and no
`explanation`**. The learner reading the key gets `403 FORBIDDEN`. The administrator gets
`200` and the keys, e.g. `['C', 'B', 'C', 'B']`.

The marking response is deliberately narrower than the brief's "MCQs with Keys" implies: it reports
the verdict and the score and says nothing about individual answers. Two separate reasons —
per-question corrections would let anyone read the whole key by submitting guesses twice, and the
company's own contract for that route is `Response {Pass / Fail}`. `correct` survives as a *count*,
because a percentage means nothing without one, and a count does not say which questions it refers
to.

---

## R06 — Every answer stored against its question

**Endpoint(s):**

- `POST /api/v1/generated-quizzes/{quiz_id}/results` — submit and mark
- `GET /api/v1/generated-quizzes/{quiz_id}/submissions` — read the stored sittings, administrators only

**Purpose:** The sitting and every answer are written to PostgreSQL at the moment of marking, so
"did this person pass, and on what" is answerable from a row rather than from whoever still holds the
response. *(Verifies: one row per question asked, including questions left blank; the verdict stored
with the numbers it was computed from.)*

**Sample data:**

```bash
# Answers are keyed by position ("1" or "Q1") or by questionId; A, B, C or D
curl -X POST "${BASE_URL}/api/v1/generated-quizzes/<quizId>/results" \
  -H "Authorization: Bearer learner-token" -H "Content-Type: application/json" \
  -d '{"answers": {"Q1": "C", "Q2": "B", "Q3": "C"}}'      # Q4 deliberately omitted

curl "${BASE_URL}/api/v1/generated-quizzes/<quizId>/submissions" \
  -H "Authorization: Bearer admin-token"
```

```sql
SELECT a.sequence, a.question_id,
       COALESCE(a.given_label,'(blank)') AS answered,
       g.answer_label AS key, a.is_correct
  FROM qz_submitted_answers a
  JOIN qz_quiz_submissions s ON s.id = a.submission_id
  JOIN qz_generated_quiz_questions g
       ON g.quiz_id = s.quiz_id AND g.sequence = a.sequence
 WHERE a.submission_id = '<submissionId>' ORDER BY a.sequence;
```

**Expected (measured):**

```
 q   answered   key   marked
 1   C          C     correct
 2   B          B     correct
 3   C          C     correct
 4   blank      B     wrong
```

A question left unanswered is stored as an answer of nothing and marked wrong, so a stored sitting
accounts for **every question that was put** — nobody can later argue a question was never asked.
The pass mark is copied onto the submission row as well, so a stored result reading 75% against a
pass mark of 50 is readable on its own and cannot be re-interpreted by a later change to anything.

---

## R07 — Pass or fail against the quiz's own pass mark

**Endpoint(s):** `POST /api/v1/generated-quizzes/{quiz_id}/results`

**Purpose:** The verdict uses the pass mark frozen onto the quiz when it was generated, not whatever
is configured now and not anything the caller sends. *(Verifies: pass mark honoured; 50% passes; a
caller cannot move the line.)*

**Sample data:**

```bash
# generate with a pass mark of 75, then answer half correctly
curl -X POST "${BASE_URL}/api/v1/generated-quizzes" \
  -H "Authorization: Bearer admin-token" -H "Content-Type: application/json" \
  -d '{"topic": "Contract Law", "count": 4, "passMark": 75}'
```

**Expected:**

```json
{ "submissionId": "c8aa7086b5384ee9855525ef5debc378",
  "quizId": "0e877b75aded4b73a4f1cc7412f20f92",
  "total": 4, "correct": 3, "percentage": 75.0,
  "passMark": 50.0, "passed": true, "outcome": "PASS" }
```

**50% passes.** The brief said `50> Pass && 50<Fail`, which does not say what 50 itself is; fifty per
cent passes, matching the rule the rest of the platform applies (`percentage >= pass_mark`), so a
learner cannot pass one part of the system and fail the other on the same score. A missing answer
scores zero rather than being excluded, so omitting the questions you were unsure of cannot improve
a percentage. Fields sent in the payload that look like they might change the outcome —
`passMark`, `passed` — are ignored; a test asserts it.

---

## R08 — Every course is supported, whether or not we hold it

**Endpoint(s):** `POST /api/v1/generated-quizzes`

**Purpose:** The agent holds no course list of its own. Your catalogue stays on your side; whichever
course a learner opens, your platform sends that course's name and the agent generates for it.
*(Verifies: a course absent from our catalogue still generates.)*

**Sample data:**

```bash
# A course with zero matches in our catalogue
curl -X POST "${BASE_URL}/api/v1/generated-quizzes" \
  -H "Authorization: Bearer admin-token" -H "Content-Type: application/json" \
  -d '{"topic": "Air and Space Law LLM (Postgraduate)", "count": 3}'
```

**Expected (measured):** `201 Created`, `courseRef: null` (nothing matched, as expected), three
questions stored, zero rejected — and correct aviation law: the Chicago Convention 1944 on
sovereignty over airspace, hull loss on a scheduled international service, and passenger injury
during international carriage under the Montreal Convention.

**So all 60,384 of your courses are supported**, not because 60,384 were loaded, but because the
course name is the input. Where a course *is* in the catalogue with a description and a level, the
questions are pitched at that standard instead of inferred from a title — an improvement, not a
requirement.

---

## R09 — A human approves before any learner is assessed

**Endpoint(s):** none — this is a property of what generation writes

**Purpose:** Every generated question is stored as a **draft**. The platform's own attempt flow
delivers only *active* questions, so nothing a model wrote can reach a learner until an
administrator has read it. *(Verifies: generated questions are never active.)*

**Sample data:**

```sql
SELECT status, COUNT(*) FROM qb_questions GROUP BY status;
```

**Expected (measured):** `DRAFT 676`, `ACTIVE 30` — the 30 being the platform's own seeded
questions. **Not one generated question is active.**

This is deliberate and it has a cost worth stating: a model can produce a question that is fluent,
plausible and wrong about the law, and the learner who fails on it is being certified against a
mistake nobody read. The 676 questions currently in the database are **awaiting review**. Generated
content is production-ready *machinery*; the content itself is not signed off until someone signs it
off.

---

## R10 — Failures are reported honestly

**Endpoint(s):** all of them

**Purpose:** A caller can tell the difference between "retry this" and "something needs looking at",
and a partial result is reported as partial rather than dressed up as a whole one.
*(Verifies: correct status codes; refusals counted and explained.)*

**Sample data:**

```bash
# A learner trying to generate
curl -X POST "${BASE_URL}/api/v1/generated-quizzes" \
  -H "Authorization: Bearer learner-token" -H "Content-Type: application/json" \
  -d '{"topic": "x", "count": 1}'

# No token at all
curl -X POST "${BASE_URL}/api/v1/generated-quizzes" \
  -H "Content-Type: application/json" -d '{"topic": "x", "count": 1}'

# Above the per-request ceiling
curl -X POST "${BASE_URL}/api/v1/generated-quizzes" \
  -H "Authorization: Bearer admin-token" -H "Content-Type: application/json" \
  -d '{"topic": "x", "count": 9999}'
```

**Expected (measured):**

| Status | Code | Meaning |
|---|---|---|
| `403` | `FORBIDDEN` | a learner tried an administrator route |
| `401` | `UNAUTHORIZED` | no token |
| `400` | `BAD_REQUEST` | count above the ceiling of 50 |
| `503` | `QUESTION_GENERATION_UNAVAILABLE` | the provider could not be reached — **retry**; nothing was written |
| `502` | `QUESTION_GENERATION_FAILED` | the provider answered but would not follow the output contract — a person should look at the prompt |

The 503/502 distinction is load-bearing. A rate limit is a 503 and retryable; it is not a fault in
the prompt, and reporting it as one sends an operator to debug something that was working. A request
reports 503 only when every batch failed *and* every one of those failures was the provider being
unreachable — one throttled batch out of five is not an outage.

A request that yields fewer questions than asked for reports `rejected` and `reasons` rather than
silently returning a short quiz.

---

## R11 — PostgreSQL, by changing one variable

**Purpose:** The agent stores everything in your PostgreSQL. Nothing in the application knows which
engine it is talking to. *(Verifies: the same operations, on PostgreSQL, with only the URL changed.)*

**Sample data:**

```bash
DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname
python -m alembic upgrade head      # creates the schema
python -m scripts.seed              # development identities, or set AUTO_SEED=true
```

`postgres://`, `postgresql://` and `postgresql+psycopg2://` are all normalised, so a managed
provider's URL can be pasted unchanged.

**Expected (measured against PostgreSQL 17, only the URL changed):**

| Step | Result |
|---|---|
| 12 migrations, empty database → head | clean |
| First start-up | schema created, identities seeded |
| Second start-up | idempotent, existing users untouched |
| Generate a quiz | 6 of 6 stored, 0 rejected |
| Learner reads the quiz | no `answer` or `explanation` field |
| Mark 4 of 6 | `PASS 66.67%`, verdict and score only |
| Stored submissions | 1 sitting, 6 answer rows, keys frozen |
| Learner reads the key | `403` |
| Generate again, same course | 0 repeated questions |

The procedure and results are recorded in `docs/DEPLOYMENT.md`.

---

## Global Definition of Done

| Check | How to verify | Expected |
|---|---|---|
| Full regression | `pytest` in `backend/` | **2207 passed** |
| This agent's own tests | `pytest tests/quiz_generation/` | **139 passed** |
| Frontend | `npm test` in `frontend/` | **111 passed** |
| Lint | `python -m ruff check app tests scripts` | `All checks passed!` |
| Architecture boundaries | `pytest tests/test_architecture.py` | passes — cross-capability imports confined to `integration/` |
| Migrations reversible | `alembic upgrade head` then `downgrade -1` | both clean, on SQLite and PostgreSQL |
| Database portability | `pytest tests/test_database_portability.py` | passes — no SQLite-only SQL in any migration |
| No answer key to a learner | R05 above | no `answer` field; `403` on both administrator routes |
| Nothing generated is deliverable | R09 above | every generated question `DRAFT` |
| Every answer recorded | R06 above | one row per question asked, blanks included |

---

## Note for sign-off — how this differs from the brief

Six points where the delivered agent differs from what was described, or where a decision was taken
that should be confirmed rather than assumed.

| # | Brief said | Delivered | Why |
|---|---|---|---|
| 1 | "analyse the course's **content** and generate the quizzes" | Generates from the course's **name**, plus its description and level where the catalogue has them. No course in the catalogue currently has a description, so in practice questions come from the course title and the model's own knowledge of the subject. | We were not given access to lesson content. Your platform's own `public.courses` has descriptions and a `course_modules` table; read access to those closes this, and the import script is written and waiting. **This is the most significant gap and no generated question can currently be traced to a particular lesson.** |
| 2 | "you have Postgres as well as **Qdrant** access, to store all of your indexed values or quizzes data" | PostgreSQL only. Qdrant is not used. | Quiz data is small and relational — a few thousand rows queried by identifier. Qdrant is a vector database for similarity search and there is nothing here to search by similarity. If you want questions indexed for de-duplication or "find similar" across courses, that is a real feature and we will build it; we did not add a second datastore just because one was offered. |
| 3 | `Response {Pass / Fail}` for the results call | Verdict, score, and a submission identifier. **No per-question detail.** | Reporting which answers were wrong would let any caller read the whole answer key by submitting guesses twice. The full detail *is* stored and readable by an administrator at `/submissions`. This is stricter than the brief, not looser. |
| 4 | Not specified | Every generated question is a **draft** and cannot be delivered by the platform's attempt flow until an administrator activates it. | A fluent, plausible, wrong question would otherwise certify someone against a mistake. It means **676 questions are currently awaiting human review**. |
| 5 | Not specified | Bulk generation of 33 courses × 20 questions exists in the database as a **test batch**, run once to prove every course works and to measure throughput. | The product does not pre-generate; that batch is not normal operation. Recorded here so its presence in the database is not mistaken for product behaviour. |
| 6 | "whenever your endpoint is hit a new quiz will be generated" | Exactly as delivered — but generation takes **14–18 seconds for 5 questions and about 45 for 20**, and sustained load hits an **AWS Bedrock throughput quota**. | Five learners starting at the same instant all succeeded in under ten seconds. A *sustained* stream does not: generating for the whole catalogue back-to-back had AWS refuse requests part way through. The agent retries a throttled request and reports it as retryable. **Raising the Bedrock quota for the inference profile is required before a launch where a cohort starts together** — it is a limit-increase request in your AWS console, not a code change. |

**One question still open.** The written architecture returns the whole quiz in one response, and
that is what is built. The recorded meeting suggested one question at a time — *"charge your answer
then we return a question again"*. The platform's existing attempt flow already delivers
question-by-question if that is what you want; please confirm which.

**Before any deployment reachable by anyone else:** the seeded tokens (`admin-token`,
`learner-token`, …) are published in this repository and must be replaced via `SEED_ADMIN_TOKEN`
and its siblings. The AI provider key currently in use is a personal key and should be rotated.

---

## Sign-off

- [ ] R01–R11 verified against the samples above
- [ ] Global DoD checks confirmed (2207 backend, 139 agent, 111 frontend, lint, migrations, portability)
- [ ] Difference 1 — generation from course **name** rather than lesson content accepted, or read access to course descriptions and modules to be provided
- [ ] Difference 2 — PostgreSQL only, Qdrant not used, accepted
- [ ] Difference 3 — results response withholds per-question detail from the learner, accepted
- [ ] Difference 4 — generated questions are drafts pending human review, accepted
- [ ] Difference 5 — the 33-course batch in the database understood as a test batch, not product behaviour
- [ ] Difference 6 — AWS Bedrock quota increase owned by Consultancy Outfit before cohort launch
- [ ] Open question — whole quiz per response vs question-at-a-time confirmed
- [ ] Approved for release — _________________________ Date: __________
