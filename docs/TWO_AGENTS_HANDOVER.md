# Quiz Agent and Question Agent — handover brief

**Repository:** `github.com/haris829/ai-coaching-agent`, branch `company-review`
**Date:** 2026-09-10

Read this before touching either agent. It explains what each one is for, where a question actually
comes from, and which of the two owns what. The two are often confused, and confusing them leads to
work being done in the wrong module.

---

## The two agents in one line each

| | **Question Agent** | **Quiz Agent** |
|---|---|---|
| Code | `backend/app/modules/question_bank/` | `backend/app/modules/quiz_generation/` |
| Tables | `qb_*` (9 tables) | `qz_*` (4 tables) |
| API prefix | `/api/question-bank/...` | `/api/v1/generated-quizzes/...` |
| Job | **Stores and manages** questions | **Writes** questions, and marks them |
| Who supplies the questions | A human — typed in, or uploaded as CSV | An AI model |
| Age | Pre-existed this work (it is UC-02) | Built for the quiz-agent brief |

**The short version:** the Question Agent is a library. The Quiz Agent is an author that puts books
into that library and then sets exams from them.

---

## Question Agent — what it is

A question bank. It existed before any AI was involved and was designed for a human to fill.

**What it owns**

- `qb_questions` — the question itself, with a status: `DRAFT`, `ACTIVE` or `RETIRED`
- `qb_question_options` — the A/B/C/D options, one row each, one flagged correct
- `qb_topics`, `qb_question_topics` — tagging, so a quiz can draw from a subject
- `qb_question_snapshots` — a frozen copy per version, so an edited question keeps its history
- `qb_question_usages` — which attempts a question has been delivered to
- `qb_question_imports`, `qb_question_import_errors` — CSV upload runs and every rejected row
- `qb_sequences` — the `Q-000047` reference numbering

**What it enforces.** Every question, however it arrives, goes through the same validator: exactly
four options for a single-choice question, exactly one correct, no duplicate option text, no
equivalent question already in the bank. It refuses; it does not repair.

**The status lifecycle is the important part.**

```
DRAFT ──(an administrator activates it)──► ACTIVE ──(retire)──► RETIRED
```

**Only `ACTIVE` questions are ever delivered to a learner.** This is the human approval gate, and it
is what the Quiz Agent depends on.

**CSV import is the Question Agent's own input route** — an administrator uploads a spreadsheet of
questions they wrote. It is row-level: good rows are saved even when others fail, and every
rejection is stored with its row number and reason.

> **Note.** CSV import is the *alternative* to the Quiz Agent, not a partner to it. One is a human
> supplying questions; the other is an AI writing them. Both land in the same bank.

---

## Quiz Agent — what it is

An author and an examiner. Six endpoints, and exactly **one** of them calls an AI model.

```
POST  /api/v1/generated-quizzes                        admin    generate  ← the only AI call
GET   /api/v1/generated-quizzes/{quiz_id}              any      the quiz to sit — no answer key
POST  /api/v1/generated-quizzes/{quiz_id}/results      any      mark it → PASS / FAIL
GET   /api/v1/generated-quizzes/{quiz_id}/answers      admin    read the key back
GET   /api/v1/generated-quizzes/{quiz_id}/submissions  admin    every stored sitting, in full
GET   /api/v1/quiz-generation/courses                  admin    the courses available
```

**What it owns**

- `qz_generated_quizzes` — one row per quiz: its id, the course, the pass mark, when and by whom
- `qz_generated_quiz_questions` — the questions **as that quiz asked them**: stem, options, key
- `qz_quiz_submissions` — one row per sitting: total, correct, percentage, pass mark, passed
- `qz_submitted_answers` — one row per question answered, including questions left blank

---

## Where a question actually comes from

This is the part people get wrong. Six stages, in order.

### Stage 1 — the course brief

Read from the Question Agent's neighbour, `qc_courses` (the course catalogue), by
`integration/catalogue.py`. The caller sends a course **name** or code; three lookups are tried,
each case-insensitive and each requiring exactly one match:

1. the catalogue code (`LL-45165`)
2. the exact title (`Medical Law MA (Postgraduate)`)
3. a title containing the reference — only if exactly one course does

Ambiguous resolves to **nothing** rather than a guess. Under five characters, no partial match is
attempted at all.

What the brief carries, and nothing more:

```
Course:        the title
Level:         RQF 2–8, where the catalogue records one
Subject area:  where recorded
Description:   the course description, truncated to 1500 characters
Modules:       module titles, up to 30
```

**Nothing else about a course is used.** Fees, deadlines, graduate salaries are marketing metadata
and would only dilute the prompt.

> ### The single most important limitation
>
> **No course in the catalogue currently has a description or a level.** So in practice the model is
> given a course *name* — four or five words — and writes from its own knowledge of the subject. The
> questions are correct law, but **not one can be traced to a particular lesson.**
>
> The company's brief asked for analysis of "the course's content". Closing that gap needs read
> access to their platform's `public.courses` (which has descriptions) and `course_modules` (57
> rows). The import script is written and waiting: `scripts/import_platform_courses.py`.
>
> If you do one thing after reading this brief, make it this.

### Stage 2 — what the course has already been asked

`GeneratedHistory` in `integration/question_bank.py` reads back up to 40 previously-generated
question stems for that course, newest first, matching on course code **and** topic.

This is what makes every request produce a different paper. Without it, asking twice returns the
same quiz.

### Stage 3 — the prompt

Built by `domain/generation.py::build_prompt`. Pure function, no database, no HTTP — which is why
the part most likely to be wrong is also the cheapest to test.

The rules the model is given, all of which are then actually checked:

```
- exactly 4 options per question, labelled A, B, C, D
- exactly one option is correct
- the three wrong options must be plausible to someone who half-knows the material
- no "all of the above", no "none of the above", no negated stems ("which is NOT...")
- no two questions may test the same point
- each question must stand alone; never refer to "the course" or "the text above"
- one sentence of explanation per question
Return ONLY a JSON object, no prose before or after it.
```

Plus the previously-asked list with an instruction not to repeat **or rephrase** them.

**JSON, not prose.** The company's own example asked in prose and got prose back — `Q1. … A. … B. …
Answer: B`. Parsing that is guesswork: option labels drift, explanations run across lines, a
question contains the word "Answer". Asking for JSON moves the ambiguity into the model's job, where
it is good, and out of a regular expression, where it is not.

**A batch of more than one gets an angle.** A large request is split into concurrent batches, and
identical prompts would produce near-identical questions. Each batch is told to focus on one of:
applying the rules to a scenario · thresholds and definitions · exceptions · procedure ·
consequences. Those five are what a professional assessment has to test, so it also makes a better
paper.

### Stage 4 — the model call

`integration/llm.py`. AWS Bedrock (`InvokeModel`), configured by:

```
COACHING_LLM_PROVIDER=bedrock
COACHING_LLM_API_KEY=<bearer key>
COACHING_LLM_MODEL=arn:aws:bedrock:us-east-1:<account>:application-inference-profile/<id>
COACHING_LLM_REGION=us-east-1
```

With no provider configured, generation returns a clean **503** and writes nothing. It never invents
questions from nowhere.

Large requests are split into batches of 10 that run **concurrently** — 50 questions is 5 batches,
about 46 seconds, rather than one 16,000-token call taking minutes. Concurrency is capped at 5. A
throttled request (HTTP 429) is retried three times with a widening pause, honouring `Retry-After`.

### Stage 5 — the parse, which mostly refuses

`domain/generation.py::parse_questions`. A question is thrown away — never repaired — if it:

- is not valid JSON, or carries no `questions` array
- has fewer or more than four options
- has an empty option, or two identical options
- names an answer that is not one of A–D
- repeats an earlier question in the same reply
- repeats a question already asked for this course

Every refusal is counted in `rejected` and named in `reasons`. **Nothing is repaired.** Repairing a
malformed question — picking an answer, inventing a fourth option — is exactly how a plausible wrong
answer reaches somebody's certificate.

### Stage 6 — into the Question Agent, as a draft

`integration/question_bank.py::QuestionBankSink`. This is the **only** file in the Quiz Agent that
imports the Question Agent, which is what keeps the dependency reviewable.

It goes through the Question Agent's own `create_question` service, not through SQL. Inserting rows
directly would be quicker and would hold generated questions to a lower standard than typed ones: no
four-option rule, no one-correct-answer rule, no version-1 snapshot. The bank would then contain two
classes of question and only one of them would be trustworthy.

Status is `DRAFT`, always. Never `ACTIVE`.

### And then frozen onto the quiz

The stem, options and key are **copied** onto `qz_generated_quiz_questions`, not referenced.

A bank question can be edited or retired. With only a reference, editing it would silently rewrite
every quiz that ever used it and every sitting ever marked against it — a learner who passed in March
could be shown different questions in June, and the answer reported as correct for their submission
could be one that was not correct when they sat it.

`question_id` stays as the link back to the bank — provenance, and the route to review or retire the
question. It is no longer the authority on what *that quiz* asked.

---

## Marking, and what a learner is told

Marking reads the **frozen key on the quiz**, not the question bank. Two tests hold this: moving
every correct flag in the bank leaves an earlier verdict unchanged, and deleting the source questions
outright leaves the quiz intact and still markable.

**A learner is told the verdict and the score, and nothing else.**

```json
{ "submissionId": "...", "quizId": "...",
  "total": 4, "correct": 3, "percentage": 75.0,
  "passMark": 50.0, "passed": true, "outcome": "PASS" }
```

No per-question detail. Two separate reasons, and both matter:

1. Per-question corrections would make the route an answer-key oracle — submit guesses, read which
   were wrong, submit again and pass.
2. The company's contract for that route is `Response {Pass / Fail}`.

The full detail *is* stored, and an administrator reads it at `/submissions`. **Stored, not
returned** is the distinction.

**A blank answer is stored and marked wrong**, not skipped. There is no attempt state here to tell
"ran out of time" from "did not know", so omitting the questions you were unsure of cannot improve a
percentage — and a stored sitting accounts for every question that was put.

**50% passes.** The brief said `50> Pass && 50<Fail`, which does not say what 50 itself is. Fifty per
cent passes, matching the rest of the platform (`percentage >= pass_mark`), so a learner cannot pass
one part of the system and fail another on the same score.

---

## Rules to work by

**Do not put a cross-capability import outside `integration/`.** `tests/test_architecture.py` reads
actual import statements and fails the build. It caught two real violations during this work; both
were fixed rather than exempted.

**Do not let the Quiz Agent write to `qb_*` directly.** Go through
`integration/question_bank.py`, which goes through the Question Agent's own service.

**Do not make generated questions `ACTIVE`.** The draft gate is the only thing between a model's
output and somebody's certificate.

**Do not add a "repair" step to the parser.** Refusing and reporting is the design, not an
oversight.

**Booleans in raw SQL:** write `AND is_correct`, never `AND is_correct = 1`. SQLite accepts the
second silently; PostgreSQL fails with `operator does not exist: boolean = integer`. A gate in
`tests/test_database_portability.py` now catches it — it was added because exactly this bug shipped
and was only found by running against a real PostgreSQL.

---

## State of play

| | |
|---|---|
| Backend tests | **2207 passing** |
| Quiz Agent tests | **139 passing** |
| Frontend tests | **111 passing** |
| Lint | `ruff` clean |
| Database | SQLite locally, PostgreSQL by changing `DATABASE_URL` only — verified end to end |
| Generated so far | 33 courses, 676 questions, all `DRAFT` |

**Outstanding, in priority order:**

1. **Course content access.** The gap above. Everything else is polish next to it.
2. **AWS Bedrock quota.** Five simultaneous requests succeed; a sustained stream is throttled.
   Generating for the whole catalogue back-to-back had AWS refuse part way through. It is a
   limit-increase request in the client's console, not a code change — but it must be raised before
   a launch where a cohort starts together.
3. **676 questions awaiting human review.** The machinery is production-ready; the content is not
   signed off until someone signs it off.
4. **One open question with the client.** The written architecture returns the whole quiz in one
   response, and that is what is built. The recorded meeting suggested one question at a time —
   *"charge your answer then we return a question again"*. The platform's existing attempt flow
   (UC-03) already does question-by-question if that is what they want.
5. **Credentials.** The seeded tokens (`admin-token`, …) are published in this repository and must be
   replaced via `SEED_ADMIN_TOKEN` and its siblings before any reachable deployment. The AI provider
   key in use is a personal key and should be rotated.

---

## Where to read further

| | |
|---|---|
| `docs/QUIZ_AGENT_VERIFICATION.md` | step-by-step verification of every requirement, with measured results |
| `docs/DEPLOYMENT.md` | PostgreSQL procedure, verified against PostgreSQL 17 |
| `docs/CSV_IMPORT.md` | the Question Agent's own upload format |
| `docs/INTEGRATION.md` | how the capabilities fit together |
| `backend/app/modules/quiz_generation/domain/generation.py` | the prompt and the parser — start here |
| `backend/app/modules/quiz_generation/api/quizzes.py` | the six endpoints, and why each is scoped as it is |

Every module and every non-obvious decision is documented in the code itself, including the
reasoning and, where a decision was reversed, what the earlier one got wrong.
