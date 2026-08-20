# Courses Quiz Agent

**Ten capabilities, one API, one database, one error envelope** — plus a cross-cutting integrity
layer (UC-11) that validates the other ten rather than adding an eleventh.

Configuration, question bank, attempt delivery, scoring, certification, feedback, AI coaching,
retakes, formal assessment and analytics.

* **UC-01 — Quiz Configuration & Rules.** An administrator configures a quiz; every meaningful
  change creates a new **immutable configuration version**; learners see a rules summary built from
  the active version.
* **UC-02 — Question Bank Management.** Create, edit, tag, retire and bulk-import questions across
  five question types, with backend-authoritative validation and guaranteed historical preservation.
* **UC-03 — Quiz Attempt Delivery.** A learner starts an attempt, which is permanently **locked** to
  the configuration version active at that instant and to a frozen copy of the questions it drew;
  answers autosave; time is server-authoritative; submission is confirmed and idempotent.
* **UC-04 — Answer Validation & Scoring.** A confirmed submission is marked against the answer key
  of the exact question *versions* it was delivered. One **immutable** result per attempt; a data
  defect leaves it *Submitted — Pending Score* and retryable rather than publishing a wrong number.
* **UC-05 — Pass / Fail & Certificate Gating.** The verdict is decided against the pass mark of the
  attempt's **own** configuration version, a certificate is issued at most once per learner and quiz
  through a retryable service boundary, and a CPD record is synchronised across another.
* **UC-06 — Detailed Feedback Report.** Per question: the question, the learner's answer, the correct
  answer, an explanation, the marks scored and a lesson reference — generated once and then frozen,
  with defined fallbacks and nothing invented.
* **UC-07 — AI Coaching Review Mode.** After submission, a learner works through the questions they got
  wrong with an AI coach that asks rather than tells — and that is *architecturally incapable* of
  telling, because the answer key is removed before any coaching context is built. With no provider
  configured it reports itself unavailable rather than inventing teaching.
* **UC-08 — Retake Management.** A retake is a **new, independent attempt**: it draws a fresh paper
  where the bank allows and records the reuse where it cannot, and it leaves every earlier attempt
  byte-for-byte unchanged. An administrator can grant one learner extra attempts **without touching
  the quiz's configured maximum**, so no other learner is affected.
* **UC-09 — Formal Assessment Mode.** A supervised sitting: conditions acknowledged against a
  recorded version, identity matched exactly against the platform directory, one device and only
  one, no pausing, AI coaching refused while it runs, a disconnect that commits the autosaved work
  rather than losing it — and a pass that produces **no certificate until a named assessor
  approves**.
* **UC-10 — Analytics & Reporting.** Aggregate figures over the rows the chain actually wrote, with
  filters by course, cohort, assessment type and date. It distinguishes *no data* from *a measured
  zero*, flags questions whose wrong-answer rate is too high, and records what a reviewer did about
  them in an append-only audit table. It owns two tables and reads everything else through a
  projection that has **no mutating method**.
* **UC-11 — Cross-Cutting Integrity, Security & QA.** Not a feature: a validation layer. It builds
  nothing, and a test enforces that it builds nothing. What it adds is the coverage no single
  capability owns — is a submitted attempt immutable through *every* route; do all five question
  types survive delivery, scoring, feedback and a retake; is the answer key unreachable from every
  endpoint rather than just the coaching one. It has found real defects that green suites did not:
  see [docs/UC11-FINDINGS.md](docs/UC11-FINDINGS.md).

```
Question bank (UC-02)
      ↓  eligible counts per type — retired and draft questions excluded
Quiz configuration (UC-01)
      ↓  authoritative field validation
      ↓  question-bank capacity validation
Immutable configuration version
      ↓  eligibility check — enrolment, attempts remaining, quiz availability
Attempt (UC-03), locked to that version, its questions frozen onto the attempt
      ↓  answer · autosave · flag · review
Confirmed submission
      ↓  the answer key of the delivered question versions
Score (UC-04) — per question, total, maximum, percentage · immutable once confirmed
      ↓  percentage vs the pass mark of the attempt's own configuration version
Pass / fail (UC-05) — certificate on a pass, remaining attempts on a fail, CPD either way
      ↓  frozen score + frozen outcome + authored explanations
Detailed feedback (UC-06) — generated once, then never re-rendered
      ↓  the gate: submitted · scored · feedback released
      ↓  raw question material  →  SANITISER  →  safe coaching context
AI coaching (UC-07) — Socratic review of each wrong answer · the coach never sees the answer key
```

Submission drives the first five stages: UC-03 hands a committed attempt to them through the
``SubmissionDispatchPort`` it always had, and each fails independently — a scoring problem cannot undo
a submission, a certificate outage cannot change a verdict, and a feedback failure cannot remove a
score.

UC-07 is not in that pipeline. Coaching is a conversation a learner chooses to have *afterwards*, so it
runs on its own requests, and it is read-only towards everything above it: an AI outage, a refused
coaching request or a contaminated context cannot change a score, a verdict or a report.

---

## Stack

| Layer     | Choice                                                          |
| --------- | --------------------------------------------------------------- |
| Backend   | Python 3.11 · FastAPI · SQLAlchemy 2.0 · Alembic · pytest        |
| Database  | SQLite locally — **swappable to the company database via one connection string** |
| Test UI   | React 19 · TypeScript · Vite · vitest                           |

No Redis, queues or microservices. The certificate and CPD boundaries are ports with in-process
adapters today, and the submission pipeline runs in the request that submitted the attempt.

**One external service, and it is optional.** UC-07's coach is an AI provider behind the `CoachingLLM`
port. Nothing is bound unless `COACHING_LLM_PROVIDER` and an API key are configured; without them
coaching honestly reports itself unavailable and the other six capabilities are unaffected. The adapter
calls the provider's HTTP API directly rather than through a vendor SDK, so the domain never learns a
provider's schema.

> **Database note.** The company database has not been provisioned yet, so local development uses a
> file-backed SQLite database. Every model uses portable SQLAlchemy types and every constraint is
> explicitly named, so moving to PostgreSQL / MySQL / SQL Server is a `DATABASE_URL` change plus
> `alembic upgrade head`, with **no application-code change**. See [docs/DATABASE.md](docs/DATABASE.md).

> **Frontend note.** `frontend/` is a **development and manual-verification surface**, not a
> production UI. The backend is the product: every rule it enforces is enforced there.

---

## Quick start

```bash
# 1. Backend dependencies (one-off)
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt   # Windows
# source .venv/bin/activate && pip install -r backend/requirements-dev.txt

# 2. Frontend dependencies (one-off)
npm install

# 3. Create the schema, then load demo data
npm run migrate
npm run seed

# 4. Backend on :8000 and the test UI on :5173
npm run dev
```

Open <http://localhost:5173>. Interactive API docs: <http://localhost:8000/api/docs>.
Readiness: <http://localhost:8000/api/health>. Liveness: <http://localhost:8000/api/health/live>.

The seed creates one course, **three quizzes**, a bank of six questions of each of the five types,
and **four identities** with both learners enrolled. Pick one from the top-bar identity switcher;
the development tokens are `admin-token`, `learner-token`, `learner2-token` and `assessor-token`.

The three quizzes each exist for a reason — one is not enough to reach the system:

| Quiz | State | What it is for |
|---|---|---|
| End of Course Assessment | **deliberately unconfigured** | The first administrator save produces version 1, so immutable versioning is visible from the start. |
| Practice Assessment | pre-configured, 12 questions, 3 attempts, 60% | Sittable immediately, so the learner journey can be walked without configuring anything first. Passing, failing and retaking are all reachable. |
| Supervised Final Examination | pre-configured as a **formal assessment** | The only way UC-09's conditions, identity confirmation, device session, disconnect handling and assessor approval can be exercised at all. |

Both configured quizzes are configured **through UC-01's own service** — same validation, same
question-bank capacity check, same immutable version write an administrator's save performs. Nothing
inserts a configuration row directly, so the seeded state is one the application could have produced.

The seed is idempotent and safe to re-run: a quiz that already has an active configuration version is
never reconfigured, so it cannot publish a version behind you or disturb an attempt locked to one.

**The whole workflow in the UI:** *Configuration* → save a configuration (watch versions accumulate)
→ *Take a quiz* → start an attempt, answer, flag, review, submit → and the same screen then shows the
**score**, the **pass/fail verdict with its certificate**, the **per-question feedback** and
**Review with Larry** — all of which the backend decided. Nothing on that screen is computed in the
browser.

Then: *Retakes* for eligibility, attempt history and an administrator's grant (UC-08); *Formal
assessment* for the supervised sitting and — when the **assessor** identity is selected — the review
queue that decides whether a certificate is ever issued (UC-09); *Analytics* for the administrator's
dashboard, filters, flagged questions and CSV exports (UC-10).

Role-specific panels are hidden as a courtesy, not as a control. Every endpoint behind them enforces
the role itself and refuses the wrong credential with 403 — which is why switching identity is the
quickest way to see the authorization model working.

The coaching panel appears only after submission, which is what "coaching controls are unavailable
during an active quiz" looks like from the front. The rule itself is the backend's: it refuses an
unsubmitted attempt outright, because a hidden button is not protection. With no AI provider configured
the panel shows the defined temporary-unavailable message instead of a conversation.

---

## Verify it

```bash
npm test                    # backend (2045 tests) + frontend (111 tests)
npm run test:api            # pytest
npm run test:web            # vitest
npm run typecheck           # tsc over the test UI
npm run lint                # ruff over the backend
npm run verify:e2e          # 469 checks against a LIVE server on a migrated database
npm run smoke:ui            # 74 checks, test UI ↔ backend through the Vite dev proxy
npm run build:web           # production build of the test UI
```

`verify:e2e` boots a real uvicorn server on a real database file created by the real Alembic
migration, drives all ten capabilities over HTTP — configure, author, sit, submit, score, gate,
report, coach, retake, supervise, report on — and re-opens the database file with an independent
connection to confirm the data is genuinely on disk.

**The two live gates are not redundant with pytest, and they are where the real defects have been
found.** pytest uses an in-process ASGI client and builds its schema from the models, so it cannot
see a migration that failed to install a trigger, a CHECK constraint the port fakes do not have, or
a page module that compiles under `tsc` and fails at Vite transform time. Every one of those has
happened here: the findings are recorded in
[docs/UC11-FINDINGS.md](docs/UC11-FINDINGS.md). Sections 30–34 of `verify:e2e` exist because UC-08,
UC-09 and UC-10 had no live coverage at all, which is exactly where three of them were hiding.

Backend tests are grouped by what they protect:

| Path                        | Count | Covers                                               |
| --------------------------- | ----- | ---------------------------------------------------- |
| `tests/question_bank/`      | 197 | UC-02: CRUD, all five types, validation, retirement, CSV import, historical integrity |
| `tests/quiz_configuration/` | 98  | UC-01: validation, capacity, versioning, atomic saves, rules, port substitutability |
| `tests/attempt_delivery/`   | 241 | UC-03: eligibility, question selection, answers, autosave, timing and expiry, flags, submission, idempotency, pending submissions |
| `tests/scoring/`            | 90  | UC-04: the five marking rules, deductions floored at zero, unanswered questions, missing answer keys, idempotency, immutability by trigger, pending-score retry |
| `tests/certification/`      | 49  | UC-05: pass/fail against the attempt's own pass mark, remaining attempts, certificate issue and retry, duplicate prevention, CPD isolation |
| `tests/feedback/`           | 26  | UC-06: the six per-question fields, multi-select option breakdown, defined fallbacks, generate-once, retry after failure |
| `tests/coaching/`           | 263 | UC-07: the coaching gate, incorrect-question eligibility, sanitisation, Socratic behaviour, the five-exchange transition, review-all-wrong-answers, knowledge gaps, failure and retry, adversarial security, the HTTP contract |
| `tests/retakes/`            | 202 | UC-08: eligibility and the three states, the allowance, fresh-paper selection and recorded reuse, administrator grants and idempotency, history assembly, previous-attempt immutability |
| `tests/formal_assessment/`  | 284 | UC-09: conditions and their version, identity matching, the single-device lock, no pausing, heartbeat and disconnect, auto-submit, the coaching restriction, the review queue and its recovery, the certificate gate, security bypass attempts |
| `tests/analytics/`          | 396 | UC-10: every metric and its denominator, no-data versus measured-zero, filters, keyset pagination, timeouts and cancellation, CSV export, flag thresholds, the append-only review trail, dangerous-configuration refusal |
| `tests/integration/`        | 107 | The seams between all ten, each chain over HTTP with every real adapter, plus concurrency and data integrity |
| `tests/global_dod/`         | 69  | UC-11: whole-surface immutability, all five question types end to end, autosave and recovery, negative-marking protection, the OpenAPI-wide authorization sweep, six cross-capability journeys, and the guard that keeps UC-11 from becoming a feature |
| `tests/test_architecture.py`| 16  | The module boundaries and the absence of duplication  |
| `tests/test_schema_migration.py` | 5 | The Alembic migration against the models — table by table, constraint by constraint, **and trigger by trigger** |
| `tests/test_error_signatures.py` | 2 | That every `raise SomeError(...)` in the application can actually be constructed. Three separate defects turned a handled failure into an opaque 500 this way, none visible to any other test |

UC-03's suite runs against **in-memory port fakes** for UC-01 and UC-02 rather than the real
adapters; UC-04/05/06's suites do the same for UC-03 and UC-02, UC-07's for UC-03, UC-04, UC-06 and
the AI provider, and UC-08's, UC-09's and UC-10's for everything upstream of them. That is deliberate: each tests its own logic, and several required behaviours are
otherwise unreachable — UC-01 correctly *refuses* to publish an incoherent configuration, so "UC-03
rejects a configuration it cannot deliver" could not be exercised at all, and neither could "the answer
key is missing", "the paper is worth zero marks", "UC-06 withdrew the report mid-session" or "the model
times out on the third exchange and recovers on the fourth". The adapters *between* UC-04, UC-05 and
UC-06 are real even in those suites, so each stage reads the rows the previous one actually wrote.

**What a fake cannot see, and what covers it.** A fake has no CHECK constraints, no unique indexes
and no triggers, so a rule the *database* enforces is invisible to a suite that uses one. That is
not a theoretical gap: it hid a constraint that rejected every disconnect submission, and eight
raise sites in UC-09's persistence layer that turned each uniqueness and concurrency conflict into
an unhandled `TypeError`. `tests/integration/` (real adapters, real rows) and `npm run verify:e2e`
(a live server on a migrated database) are what close it, which is why both are gates rather than
optional extras.

The real UC-01/UC-02/UC-03 and UC-07 adapters are covered by `tests/integration/`, which drives the
whole chain over HTTP against a real database — including `test_coaching_chain.py`, where the answer key
the coach must never see is the one UC-02 actually authored.

---

## Architecture

```
API layer            routers — thin; no business decisions
      ↓
Schemas / validation authoritative domain validators
      ↓
Service layer        transactional use cases
      ↓
Domain              pure business rules (no persistence, no HTTP)
      ↓
Repository interface Protocols the services depend on
      ↓
Persistence adapter  today: SQLAlchemy over SQLite · tomorrow: the company database
```

```
backend/app/
├── core/                    THE SHARED KERNEL — owned by no capability
│   ├── question_types.py    the five question types, statuses, presentation modes: one vocabulary
│   ├── coercion.py          one set of input primitives, so every validator agrees on "10"
│   ├── errors.py            one error taxonomy (AppError, FieldIssue, PLATFORM_ERROR_CODES)
│   ├── schemas.py           one error envelope + pagination shape for the whole API
│   ├── time.py              one clock — injected, and every timestamp is UTC
│   ├── deps.py              DbSession, declared once
│   ├── config.py            settings
│   ├── logging.py           structured JSON logging
│   └── exception_handlers.py
├── db/                      declarative base, engine/session, the complete metadata
└── modules/
    ├── identity/            the authentication seam — the ONE place a caller is resolved
    ├── question_bank/       UC-02 · tables qb_ · api /question-bank
    │   ├── domain/          its OWN policy: per-type rules, validator, grading, snapshots
    │   ├── services/        question, topic, import, delivery
    │   ├── csv_import/      template, parser, row mapper
    │   └── api/             routers + serialisers
    ├── quiz_configuration/  UC-01 · tables qc_ · api /admin/quizzes + /quizzes
    │   ├── domain/rules.py  its OWN rules: validation, capacity arithmetic, fingerprinting
    │   ├── ports.py         QuestionBankPort, AttemptStatisticsPort — what UC-01 requires
    │   ├── integration/     the ONLY files that import UC-02 or UC-03
    │   ├── repositories.py  Protocols + today's SQLAlchemy implementations
    │   ├── services/        configuration_service, rules_service
    │   └── api/             admin, learner, meta routers
    ├── attempt_delivery/    UC-03 · tables qd_ · api /v1/attempts
    │   ├── domain/          selection, answer validation, timing, state machine, errors
    │   ├── repositories/    Protocols + today's SQLAlchemy implementations
    │   ├── services/        attempt, answer, timing, submission, access
    │   ├── integration/     uc01/ uc02/ enrolment/ submission_dispatch/ — ports + adapters
    │   ├── container.py     composition root: clock, ports, unit of work
    │   └── api/             routers, presenters, schemas
    ├── scoring/             UC-04 · tables qr_ · api /v1/attempts/{id}/result
    │   ├── domain/          the five marking rules, the answer key, aggregation — pure functions
    │   ├── repositories.py  Protocols + today's SQLAlchemy implementations
    │   ├── services/        scoring_service: claim, mark, confirm, retry
    │   ├── integration/     attempt_delivery/ (UC-03) · question_bank/ (UC-02) — ports + adapters
    │   └── api/             router + presenters
    ├── certification/       UC-05 · tables qg_ · api /v1/attempts/{id}/outcome
    │   ├── domain/gating.py its OWN rules: pass/fail, remaining attempts, certificate due
    │   ├── repositories.py  Protocols + today's SQLAlchemy implementations
    │   ├── services/        certification_service: determine, issue, synchronise, retry
    │   ├── integration/     scoring/ · attempt_delivery/ · certificate/ · cpd/ — ports + adapters
    │   └── api/             router + presenters
    ├── feedback/            UC-06 · tables qf_ · api /v1/attempts/{id}/feedback
    │   ├── domain/          report assembly + the defined fallbacks — pure functions
    │   ├── repositories.py  Protocols + today's SQLAlchemy implementations
    │   ├── services/        feedback_service: assemble once, then freeze
    │   ├── integration/     scoring/ · certification/ · question_bank/ — ports + adapters
    │   └── api/             router + presenters
    └── coaching/            UC-07 · tables qk_ · api /v1/attempts/{id}/coaching/…
        ├── domain/          the coaching gate, the SANITISER, the safe context, the session,
        │                    the transcript, the review queue, the reply policy — pure functions
        ├── models.py        qk_ tables: sessions, messages, knowledge gaps, activity
        ├── repositories/    protocols + in-memory + SQLAlchemy implementations
        ├── prompts/         the coaching policy, kept out of the services
        ├── integration/     uc03/uc04/uc06 ports + adapters · the CoachingLLM port and its
        │                    config-activated provider adapter · knowledge gaps · activity
        ├── container.py     composition root: the ports table, and the AI provider or nothing
        ├── services/        authorization · context_builder · coaching · review
        └── api/             9 endpoints, thin translation only
├── composition.py           the results chain's composition root + the submission pipeline
frontend/src/
├── api/                     typed client, contract types, identity selection
├── lib/                     configurationRules · attemptAnswers · attemptTimer (all unit-tested)
├── components/attempt/      the five question renderers, review, submit, and the result panel
└── pages/                   configuration, learner rules, take a quiz, questions, topics, import, reports
```

`app/composition.py` sits at application level rather than inside a capability on purpose: something
has to decide which adapters satisfy UC-04's, UC-05's and UC-06's ports, and a capability that wired
its neighbours would have to import them outside its own `integration/` package — which is exactly
the boundary the architecture tests enforce.

The dependency rules are **enforced by tests**, not just documented
(`backend/tests/test_architecture.py` reads the actual import statements):

| Rule | Why |
| ---- | --- |
| UC-02 imports **nothing** from any other capability | the question bank stays independently deployable |
| Cross-capability imports appear **only** in `integration/` packages | the seams stay small and reviewable |
| `app/core/` imports **no** capability | that is what makes it safe to share |
| `domain/` packages import no FastAPI, SQLAlchemy or `app.db` | business rules stay testable without a server or a database |
| One `utcnow`, one `to_int`, one `QuestionType`, one `ErrorResponse`, one `DbSession` | the duplication removed at merge time cannot come back |
| Exactly **one** owner of attempts | two records of "did this learner attempt" would eventually disagree |
| No provisional stand-in tables for a merged capability | the `ext_*` projections UC-03 shipped with are gone, not dormant |
| A capability's `domain/` holds its own rules, and the shared kernel holds none | UC-04 names its own marking policy rather than importing UC-02's authoring vocabulary |
| UC-04's domain names its own marking policy rather than importing UC-02's scoring strategy | the authoring vocabulary belongs to the question bank; the translation lives in one adapter |

### How the ten capabilities meet

They meet in exactly two ways.

**A shared kernel for what they must agree on.** The five question types live in
`app/core/question_types.py` — not in UC-02 with the others reaching in, and not copied into each.
Along with them: the error envelope, the coercion primitives, the clock. Everything in `app/core/` is
vocabulary and plumbing; every *rule* stays with the capability that owns it. The question bank still
decides that a `SINGLE_CHOICE` needs four options; quiz configuration still decides that an `exam`
needs a time limit; attempt delivery still decides when an answer is complete.

**A `Protocol` port for everything one needs from another**, with a single adapter behind it:

| Consumer | Port | Adapter | For |
| -------- | ---- | ------- | --- |
| UC-01 | `QuestionBankPort` | `integration/question_bank_adapter.py` | eligible counts per type, topic resolution |
| UC-01 | `AttemptStatisticsPort` | `integration/attempt_statistics_adapter.py` | attempts used / remaining, for the rules summary |
| UC-03 | `QuizConfigurationPort` | `integration/uc01/configuration_adapter.py` | the active configuration version to lock onto |
| UC-03 | `QuestionBankPort` | `integration/uc02/question_bank_adapter.py` | the eligible pool, and reporting a delivery back |
| UC-03 | `EnrolmentPort` | `integration/enrolment/platform_adapter.py` | is this learner enrolled on the course |
| UC-03 | `SubmissionDispatchPort` | `app/composition.py::ResultsPipeline` | handing a submitted attempt to the results chain |
| UC-04 | `AttemptSourcePort` | `scoring/integration/attempt_delivery/attempt_adapter.py` | the submitted attempt, its locked rules and its frozen answers |
| UC-04 | `AnswerKeyPort` | `scoring/integration/question_bank/answer_key_adapter.py` | the answer key for the exact question *version* delivered |
| UC-05 | `ScoreResultPort` | `certification/integration/scoring/result_adapter.py` | the confirmed score to gate on |
| UC-05 | `AttemptPolicyPort` | `certification/integration/attempt_delivery/attempt_policy_adapter.py` | the attempt's own pass mark and attempt allowance, and its attempt count |
| UC-05 | `CertificateServicePort` | `certification/integration/certificate/local_adapter.py` | issuing a certificate — the company's service replaces this one file |
| UC-05 | `CpdSyncPort` | `certification/integration/cpd/local_adapter.py` | the CPD record: attempt date, score, pass/fail, course name |
| UC-06 | `ScoreDetailPort` | `feedback/integration/scoring/score_adapter.py` | the frozen per-question scores a report is built from |
| UC-06 | `OutcomePort` | `feedback/integration/certification/outcome_adapter.py` | pass/fail, to report it (never to decide it) |
| UC-06 | `QuestionContentPort` | `feedback/integration/question_bank/content_adapter.py` | the authored explanation and lesson reference |
| UC-07 | `AttemptProvider` | `coaching/integration/uc03_adapter.py` | the submitted attempt, the paper *as delivered*, and the learner's answers |
| UC-07 | `ScoringResultProvider` | `coaching/integration/uc04_adapter.py` | which questions the authoritative result calls incorrect — and the answer key, so the sanitiser can forbid it |
| UC-07 | `FeedbackProvider` | `coaching/integration/uc06_adapter.py` | the release gate, the lesson reference, and the explanation the coach must never see |
| UC-07 | `CoachingLLM` | `coaching/integration/llm_anthropic.py` | the AI coach — **unbound unless configured**, and then honestly unavailable |
| UC-07 | `KnowledgeGapTracker` · `CoachingActivityLog` | `coaching/repositories/sqlalchemy.py` | topics reviewed, and the coaching lifecycle |

Two consequences worth naming:

*UC-01 reports attempt counts without owning attempts.* Its rules summary still answers "2 of 3
attempts remaining", but it reads that through `AttemptStatisticsPort` from UC-03's tables. There is
one record of an attempt in the system, and an architecture test proves it.

*UC-03 reports deliveries back to UC-02.* Its own frozen snapshot answers "what did this learner
see"; UC-02's usage row answers "has this question of mine ever been used", which is what drives its
usage counts, its refusal to hard-delete used content, and its historical report. Different
questions, so both records exist — and the write goes through the port like everything else.

Each port is exercised against an in-memory fake with no database behind it, which is the evidence
that it is a real boundary rather than decoration.

---

## API

Every endpoint is under `/api`. Full reference: [docs/API.md](docs/API.md).

### UC-01 — admin

| Method | Path                                              | Purpose                                       |
| ------ | ------------------------------------------------- | --------------------------------------------- |
| GET    | `/admin/quizzes`                                  | Quizzes to configure                          |
| GET    | `/admin/quizzes/{id}/configuration`               | Active configuration + live capacity          |
| PUT    | `/admin/quizzes/{id}/configuration`               | New immutable version (`201`) / no-op (`200`) |
| GET    | `/admin/quizzes/{id}/configuration/versions`      | Immutable version history                     |
| GET    | `/admin/quizzes/{id}/question-bank`               | Eligible question counts per type             |

### UC-01 — learner

| Method | Path                    | Purpose                                        |
| ------ | ----------------------- | ---------------------------------------------- |
| GET    | `/quizzes/{id}/rules`   | Rules summary — **read-only, creates nothing** |

Attempts are not here. UC-03 owns the attempt lifecycle.

### UC-02

| Method | Path                                    | Purpose                                  |
| ------ | --------------------------------------- | ---------------------------------------- |
| —      | `/question-bank/questions`              | CRUD, retire, reactivate, snapshot history |
| —      | `/question-bank/topics`                 | Topic tagging                            |
| —      | `/question-bank/imports`                | CSV bulk import with row-level reporting |
| —      | `/question-bank/delivery` · `/reporting` | The delivery seam and historical reports |

### UC-03 — learner (versioned under `/api/v1`)

| Method | Path                                                  | Purpose                                            |
| ------ | ----------------------------------------------------- | -------------------------------------------------- |
| GET    | `/v1/quizzes/{id}/attempt-eligibility`                | Pre-flight: enrolment, attempts left, blockers — **creates nothing** |
| POST   | `/v1/attempts`                                        | **Start** — locks the active version, freezes the questions |
| GET    | `/v1/attempts/active?quizId=…`                        | **Resume** — the learner's open attempt, or `404`  |
| GET    | `/v1/attempts/{id}`                                   | The attempt, its locked rules and authoritative timing |
| GET    | `/v1/attempts/{id}/state`                             | Per-question answered / complete / flagged, for a navigator |
| GET    | `/v1/attempts/{id}/timing`                            | Server time and remaining time — the only source a countdown may trust |
| GET    | `/v1/attempts/{id}/questions`                         | The whole paper (refused for a one-at-a-time attempt) |
| GET    | `/v1/attempts/{id}/questions/current` · `/at/{n}`     | One question at a time                             |
| PUT    | `/v1/attempts/{id}/cursor`                            | Persist the resume position                        |
| PUT    | `/v1/attempts/{id}/questions/{qid}/answer`            | Save one answer — idempotent, revision-checked     |
| POST   | `/v1/attempts/{id}/answers`                           | Batch autosave                                     |
| GET    | `/v1/attempts/{id}/answers`                           | The reload path: every delivered question, answered or not |
| GET    | `/v1/attempts/{id}/answers/revisions`                 | The save audit trail                               |
| PUT    | `/v1/attempts/{id}/questions/{qid}/flag`              | Flag / unflag for review                           |
| GET    | `/v1/attempts/{id}/submission/preview`                | What would be submitted — **never submits**        |
| POST   | `/v1/attempts/{id}/submission`                        | Confirmed submission (`confirmed: true` + idempotency key) |
| POST   | `/v1/attempts/{id}/submission/retry`                  | Complete a submission left `PENDING` downstream    |
| GET    | `/v1/attempts/{id}/submission`                        | Submission history                                 |

### UC-04 — scoring (versioned under `/api/v1`)

| Method | Path                                    | Purpose                                                  |
| ------ | --------------------------------------- | -------------------------------------------------------- |
| GET    | `/v1/attempts/{id}/result`              | The score, with the marks awarded per question            |
| POST   | `/v1/attempts/{id}/result`              | Score the attempt — **idempotent**, and the retry path    |
| GET    | `/v1/results`                           | The learner's results, newest attempt first               |

### UC-05 — pass / fail and certificate (versioned under `/api/v1`)

| Method | Path                                              | Purpose                                          |
| ------ | ------------------------------------------------- | ------------------------------------------------ |
| GET    | `/v1/attempts/{id}/outcome`                       | Pass/fail, certificate state, CPD state, attempts remaining |
| POST   | `/v1/attempts/{id}/outcome`                       | Determine pass/fail — **idempotent**; drives pending work |
| POST   | `/v1/attempts/{id}/outcome/certificate/retry`     | Drive a pending certificate, **reporting** failure |
| POST   | `/v1/attempts/{id}/outcome/cpd/retry`             | Drive a pending CPD synchronisation               |
| GET    | `/v1/outcomes`                                    | The learner's outcomes, newest attempt first      |

### UC-06 — detailed feedback (versioned under `/api/v1`)

| Method | Path                                    | Purpose                                                  |
| ------ | --------------------------------------- | -------------------------------------------------------- |
| GET    | `/v1/attempts/{id}/feedback`            | The frozen report: per question and per attempt           |
| POST   | `/v1/attempts/{id}/feedback`            | Generate it — **idempotent**, and the retry path          |
| GET    | `/v1/feedback`                          | The learner's reports, newest attempt first               |

In normal use a client calls none of the three `POST`s: submission drives the chain, so the score, the
outcome and the report already exist by the time it returns. They exist for the failure paths — a
result left `PENDING_SCORE`, a certificate the certificate service could not issue yet — and every one
of them replays rather than recomputes when the work is already done.

### UC-07 — AI coaching review mode (versioned under `/api/v1`)

| Method | Path                                                        | Purpose                                          |
| ------ | ----------------------------------------------------------- | ------------------------------------------------ |
| GET    | `/v1/attempts/{id}/coaching/eligibility`                    | May coaching be offered, and for which questions — **never fails for an ineligible attempt** |
| GET    | `/v1/attempts/{id}/coaching/review`                         | Every incorrectly answered question, in delivery order |
| POST   | `/v1/attempts/{id}/coaching/review/next`                    | Move to the next one — **idempotent** |
| POST   | `/v1/attempts/{id}/coaching/questions/{questionId}`         | Start coaching — **idempotent**, resumes rather than duplicating |
| GET    | `/v1/coaching/sessions/{id}`                                | The session and its conversation |
| POST   | `/v1/coaching/sessions/{id}/messages`                       | Send a learner message, get the coach's reply |
| POST   | `/v1/coaching/sessions/{id}/mode`                           | Socratic, or a direct concept explanation |
| POST   | `/v1/coaching/sessions/{id}/retry`                          | Retry a coach turn that could not be produced |
| POST   | `/v1/coaching/sessions/{id}/complete`                       | Finish with this question |

Every coaching operation is scoped to the learner resolved from the **bearer token** — there is no
learner id in any path — and the ownership check is then re-derived in the domain on every call.

An AI outage is a **503 with a full body**: the session, the stored conversation and
`coachingAvailable: false` with a reason code. The learner keeps their session and their message, the
client knows it may retry, and nothing is invented to fill the gap.

### Shared

| Method | Path             | Purpose                                                   |
| ------ | ---------------- | --------------------------------------------------------- |
| GET    | `/health`        | Readiness — includes a database check, `503` when it fails |
| GET    | `/health/live`   | Liveness — touches nothing, so a database blip cannot restart a healthy process |
| GET    | `/meta`          | Question types, delivery modes, presentation modes, numeric limits |
| GET    | `/session`       | Who am I (plus the local development identities)          |

### Error shape

One envelope for the whole API:

```json
{
  "error": {
    "code": "INSUFFICIENT_QUESTIONS",
    "message": "The question bank does not contain enough eligible questions.",
    "retryable": false,
    "requestId": "5f0e…",
    "timestamp": "2026-08-18T20:15:43.527495Z",
    "details": [{ "field": "passMark", "code": "OUT_OF_RANGE", "message": "Pass mark must be between 1 and 100%." }],
    "context": { "requestedQuestionCount": 20, "availableQuestionCount": 12 }
  }
}
```

* `details` is always a list of `{field, code, message}` — field-level problems.
* `context` is structured, machine-readable context for that specific code.
* `code` is either the owning capability's own code or one of `PLATFORM_ERROR_CODES`
  (`BAD_REQUEST`, `NOT_FOUND`, `CONFLICT`…), and a test asserts that nothing else is ever returned.
* A request that could not be *understood* is `BAD_REQUEST`; one that was understood and broke a rule
  carries the capability's code. Clients act on the two differently.
* Stack traces and driver messages are logged server-side and never returned.

---

## Configuration rules (UC-01)

| Setting          | Rule                                                                              |
| ---------------- | --------------------------------------------------------------------------------- |
| Question count   | 1–100                                                                             |
| Time limit       | 1–480 minutes, or empty for no limit (**required** for `exam` delivery)            |
| Pass mark        | 1–100 %                                                                           |
| Maximum attempts | 1–50                                                                              |
| Question types   | At least one of the five: `SINGLE_CHOICE`, `TRUE_FALSE`, `MULTI_SELECT`, `SCENARIO`, `DRAG_TO_ORDER` |
| Per-type quotas  | All types or none; when set, they must add up to the question count               |
| Topic scope      | Optional; empty means the whole active bank                                        |
| Randomisation    | Question order and option order, independently                                     |
| Delivery mode    | `practice`, `assessment`, `exam`                                                  |
| Presentation     | `ALL_AT_ONCE` or `ONE_AT_A_TIME` — how UC-03 hands the paper over                 |
| Incomplete submission | Allowed or forbidden — UC-03 enforces it at submission                        |
| Question bank    | Must be able to satisfy the count and every per-type quota                        |

Rules live once in
[`backend/app/modules/quiz_configuration/domain/rules.py`](backend/app/modules/quiz_configuration/domain/rules.py)
and are mirrored in [`frontend/src/lib/configurationRules.ts`](frontend/src/lib/configurationRules.ts)
so the admin form can validate before saving. The two are pinned together by
`backend/tests/quiz_configuration/test_frontend_contract_sync.py`, and `/api/meta` publishes the
limits at runtime.

> **A note on two "delivery modes".** UC-01's `deliveryMode` (`practice` / `assessment` / `exam`) is
> an *authoring* concept. UC-03's was a *presentation* concept — whole paper or one question at a
> time. Two different things under one name is how a merged system acquires its first real bug, so
> UC-03's was renamed **question presentation** throughout: field, error code, and UI label.

---

## Data model

```
qa_users                                  (placeholder identity — company IdP replaces it)
qa_enrolments                             (placeholder enrolment — same)

qc_courses ──< qc_quizzes ──< qc_configuration_versions ──< …_question_types
                   │                        │            └──< …_topics  (frozen scope)
                   └── active_configuration_version_id (→ newest version)

qd_attempts ──< qd_attempt_questions   (the frozen question snapshot the learner saw)
            ──< qd_attempt_answers ──< qd_attempt_answer_revisions   (append-only audit)
            ──< qd_attempt_question_flags
            ──< qd_attempt_submissions   (idempotency key, state, downstream reference)
     └── configuration_version_id + configuration_snapshot (locked at creation)

qb_questions ──< qb_question_options
             ──< qb_question_topics >── qb_topics
             ──< qb_question_snapshots ──< qb_question_usages   (attempt_ref ← UC-03's attempt id)
qb_question_imports ──< qb_question_import_errors

qr_attempt_results ──< qr_question_scores   (UC-04: one result per attempt, write-once per question)
     └── attempt_id (unique) · configuration_version_id · pass_mark_percentage (frozen)

qg_attempt_outcomes                         (UC-05: one PASS/FAIL per attempt, never updated)
qg_certificates                             (one request per attempt · ≤1 ISSUED per learner+quiz)
qg_cpd_records                              (one per attempt: date, score, pass/fail, course name)

qf_feedback_reports ──< qf_feedback_items   (UC-06: one report per attempt, frozen once generated)

qk_coaching_sessions ──< qk_coaching_messages   (UC-07: one session per learner+attempt+question)
qk_knowledge_gaps                           (one per coaching session: the topic reviewed)
qk_coaching_activity                        (append-only: the coaching lifecycle, never its content)
```

Integrity is enforced by the database, not only by application code:

* `CHECK` — pass mark 1–100, max attempts 1–50, question count 1–100, time limit 1–480, enum values,
  and the attempt lifecycle (submitted implies a timestamp, complete implies answered, …)
* `UNIQUE (quiz_id, version_number)` — gapless, unique version numbering
* `UNIQUE (learner_id, quiz_id, attempt_number)` — sequential attempt numbering
* partial `UNIQUE INDEX ux_attempt_single_open` — **one open attempt** per learner and quiz
* partial `UNIQUE INDEX ux_submission_single_success` — **at most one successful submission** per attempt
* `UNIQUE (attempt_id, idempotency_key)` — a retried submission cannot become a second one
* `ON DELETE RESTRICT` — a version an attempt uses, or a question with history, cannot be deleted
* `UNIQUE (attempt_id)` on `qr_attempt_results`, `qg_attempt_outcomes`, `qg_certificates`,
  `qg_cpd_records` and `qf_feedback_reports` — **one** score, one verdict, one certificate request,
  one CPD record and one report per attempt, which is what makes every stage idempotent under a race
  rather than only under a read-then-write check
* partial `UNIQUE INDEX ux_qg_certificate_single_issued` — **at most one issued certificate per
  learner and quiz**: passing a second time cannot mint a second document
* `UNIQUE (learner_id, attempt_id, question_id)` on `qk_coaching_sessions` — **one coaching
  conversation per wrong answer**, which is what makes starting coaching idempotent under a race
  rather than only under a check; and `UNIQUE (session_id)` on `qk_knowledge_gaps`, so twenty turns
  on one question record one knowledge gap
* **triggers** — an `UPDATE` on a configuration version, its question types or its topic scope is
  rejected outright; so is an `UPDATE` to a **confirmed score**, to any per-question score, to a
  **determined pass/fail outcome**, to a **generated feedback report** or to any of its items, and to
  any **stored coaching message** or **coaching activity record** — a learner's conversation and an
  audit stream are both append-only.
  Shipped by the Alembic migration as well as by `create_all`, so a migrated database has the same
  guarantees — and `tests/test_schema_migration.py` compares the two schemas table by table and
  constraint by constraint, in both directions.

Every timestamp column is timezone-aware UTC, enforced at the persistence boundary by
`app/db/types.py::UtcDateTime`: a naive datetime cannot be written at all.

---

## How the guarantees are kept

**Immutable versioning.** `save_configuration` validates, resolves the topic scope, checks capacity,
compares a canonical fingerprint against the active version (an unchanged re-save is a `200` no-op),
then inserts a new version, its question types, its topic scope and repoints the quiz **in one
transaction**. Existing rows are never updated — database triggers make that impossible.

**Atomic save.** Any failure rolls the unit of work back: no version row, no question-type rows, no
topic rows, and the quiz still points at the previously active version. The client gets a
`503 PERSISTENCE_FAILED` marked `retryable`. A failed save does not consume a version number.

**Concurrent saves.** Two administrators saving at once cannot produce a duplicated version number:
`UNIQUE (quiz_id, version_number)` decides it, and the loser gets a
`409 CONCURRENT_CONFIGURATION_UPDATE` marked retryable rather than a 500.

**Version locking.** `POST /v1/attempts` resolves the active version inside the transaction and
stores both its id and a full snapshot of its rules on the attempt. Every later read resolves the
rules from that snapshot — never from the quiz's current active version — so a configuration change
mid-attempt cannot alter a running attempt. Neither can withdrawing the version entirely.

**Question locking.** The questions drawn at start are frozen onto `qd_attempt_questions` in the same
transaction as the attempt, answer key included but never presented. Editing or retiring a question
afterwards changes neither what the learner sees nor what is submitted. The selection is also
reproducible: the attempt id is the randomisation seed, stored on the row.

**No answer key leaks.** The learner presenter is an allow-list, not an echo: `isCorrect`,
`correctPosition` and explanations are dropped by construction, and both the test suite and the live
end-to-end script assert their absence in the serialised response.

**Server-authoritative time.** Expiry is computed from the server clock alone. A client may report
its own clock, and the response echoes the observed skew — but the skew never enters a calculation, so
a manipulated device clock cannot extend an attempt. The clock is *injected*
(`app/core/time.py::Clock`), so every timing rule is tested deterministically with no sleeping.

**Autosave that cannot lose work quietly.** Saves are idempotent — re-sending an identical answer
reports `changed: false` and does not advance the revision — and `expectedRevision` turns a
concurrent change from another tab into a `409 ANSWER_REVISION_CONFLICT` rather than a silent
overwrite. Every accepted save appends to `qd_attempt_answer_revisions`. The test UI holds a
**persistent** warning with a manual retry whenever a save has failed, because a save failure the
learner does not notice is the worst outcome the screen has.

**Submission is confirmed, and happens once.** A preview is read-only however often it is called;
committing requires `confirmed: true`; and an idempotency key collapses a double-click, an impatient
retry and a reconnect into one submission — enforced by a unique index, not only by application code.
If the downstream hand-off fails, the attempt is left `SUBMISSION_PENDING` with the answers frozen
and safe, and retrying continues the same submission rather than starting a second.

**Retirement is safe.** A retired question keeps its row, id, reference, options, topics and every
snapshot. It stops counting towards capacity and is never drawn again, because both the count and
the draw are built from the bank's own deliverable query. Hard delete is refused
(`409 QUESTION_HAS_HISTORY`) once a question has been delivered — in the service layer *and* by
`ON DELETE RESTRICT`.

**Reads never write.** `GET /rules` and `GET /attempt-eligibility` both answer "may I start?" without
creating anything, and the live end-to-end script proves it by counting attempt rows after repeated
reads.

**Scoring is idempotent, and a confirmed score is immutable.** One result row per attempt decides a
race; re-scoring a `SCORED` attempt replays the stored result and writes nothing; and a database
trigger rejects an `UPDATE` to it even from raw SQL. Per-question scores are write-once for the same
reason. A score is computed from frozen inputs only — the attempt's locked configuration snapshot, its
frozen question snapshots, and the question bank's immutable snapshot for the exact version delivered —
so editing or retiring a question afterwards cannot change a mark, and neither can re-running scoring.

**A scoring failure costs nothing.** A missing answer key, a question set worth zero marks, an answer
that does not fit its question: each is reported as an anomaly, the result is left `PENDING_SCORE` —
which a learner sees as **Submitted — Pending Score** — and the run is retryable. No per-question marks
are stored for a pending result, because a number computed from broken data is worse than no number.
Nothing about the submission is undone.

**Pass/fail is judged against the attempt's own rules.** The pass mark comes from the configuration
version the attempt was locked to, read from the snapshot UC-03 froze — so reconfiguring the quiz
afterwards cannot move the bar for an attempt already sat. The verdict is written once and a trigger
refuses to update it.

**One certificate, whatever happens.** A certificate is requested only on a pass, issued through the
`CertificateServicePort` boundary, and permitted at most once per learner and quiz by a partial unique
index. A learner who passes twice keeps the certificate they already hold; no second request is even
recorded. If the certificate service is unavailable the request stays `PENDING` with the reason, the
quiz result is untouched, and `POST .../certificate/retry` drives it again — reporting the failure
rather than swallowing it.

**CPD cannot affect a quiz result.** The CPD record — attempt date, score, pass/fail, course name — is
frozen at determination time and pushed across `CpdSyncPort` *after* the outcome is durable. A failure
leaves a `PENDING` row and a retry; there is nothing left for it to damage.

**The coach never gets the answer key.** UC-07's whole security claim is structural, not a prompt
instruction. `SafeCoachingContext` has no field capable of holding a correct answer, an answer key or a
UC-06 explanation — it is an allow-list expressed as a type, so an upstream module that starts
returning a new answer-bearing field cannot leak it. Narrative text is then scrubbed of exact
answer-bearing values and of "the correct answer is …" spans, and the finished payload is walked for any
surviving answer-key key or value. A finding **fails closed**: coaching is refused for that question
rather than delivered with a smaller leak. The prompt policy telling the coach not to reveal answers is
a *second* layer, worth having but not the one that makes the claim true — and a reply that announces an
answer anyway is discarded and regenerated rather than shown.

**Coaching cannot happen during a quiz.** Only a `SUBMITTED` attempt with a confirmed score and a
released feedback report is coachable, and only for questions the authoritative result marks incorrect.
That is enforced in the domain on **every** operation, not once at the start and not by hiding a button:
if the report is withdrawn or a re-score turns a wrong answer into a right one, the next message in a
running conversation is refused.

**Starting coaching twice resumes one conversation.** `(learner, attempt, question)` is unique in the
database, so two simultaneous requests converge on one session rather than both inserting — and a
session that already has a coach turn is resumed rather than re-opened, so a double-tapped button
produces no second opening question and no second model call.

**A coaching failure costs a learner nothing.** The AI is reached through a port whose every failure
raises rather than substituting text. An exchange counts only when a learner message has been answered,
so an outage cannot push anyone closer to the five-exchange transition; the learner's message is stored
*before* the model is called, so a retry re-sends exactly what they typed; and the score, the verdict
and the report are read-only to all of it.

**Feedback is generated once and then frozen.** The report is stored twice — as rows, one per question,
and as the rendered payload the API serves — and a trigger refuses to update a `GENERATED` report or
any of its items. Regeneration replays the stored document instead of re-rendering it, so a report a
learner has read cannot change when the question bank, the topics or the configuration do. A missing
explanation or lesson reference becomes a **defined fallback**; nothing is ever generated to fill a
gap.

---

## Configuration

| Variable          | Default                            | Purpose                                             |
| ----------------- | ---------------------------------- | --------------------------------------------------- |
| `DATABASE_URL`    | `sqlite:///backend/quiz_agent.db`  | **The only thing to change for the company database** |
| `ENVIRONMENT`     | `development`                      | `test` silences logs and hides development identities |
| `LOG_LEVEL`       | `INFO`                             | JSON-lines structured logging                       |
| `PORT`            | `8000`                             | uvicorn port                                        |
| `CORS_ORIGINS`    | `http://localhost:5173,…`          | Comma-separated test-UI origins                     |
| `ADMIN_API_TOKEN` | *(empty — token check disabled)*   | When set, an administrator bearer token is required |
| `CSV_MAX_BYTES`   | `5242880`                          | Upload ceiling (`413` beyond it)                    |
| `CSV_MAX_ROWS`    | `5000`                             | Row ceiling per import                              |
| `SUBMISSION_GRACE_SECONDS` | `0`                       | Allow an in-flight autosave to land just past the deadline |
| `CLOCK_RESYNC_THRESHOLD_SECONDS` | `5`                 | Skew beyond which a client is told to resync         |
| `AUTOSAVE_INTERVAL_SECONDS` | `30`                     | Cadence the server advertises to clients             |
| `MAX_BATCH_ANSWERS` | `500`                            | Ceiling on one batch autosave                        |
| `DIRECT_EXPLANATION_THRESHOLD` | `5`                   | Exchanges before the learner may ask for a direct explanation |
| `COACHING_MAX_EXCHANGES` | `50`                        | Runaway guard on one coaching session                |
| `COACHING_HISTORY_WINDOW` | `20`                       | Trailing messages replayed to the model              |
| `COACHING_MAX_CONSECUTIVE_FAILURES` | `3`              | Failures before a session is parked as `FAILED`      |
| `COACHING_LLM_PROVIDER` | *(empty — no coach bound)*    | `anthropic` binds the shipped adapter. **Empty means coaching honestly reports itself unavailable** |
| `COACHING_LLM_API_KEY` | *(empty)*                      | Read from the environment only; never logged or returned |
| `COACHING_LLM_MODEL` | `claude-sonnet-5`                | Which model the bound provider uses                  |
| `SYSTEM_API_TOKEN` | *(empty)*                          | Guard on UC-09's platform-internal endpoints. **Required outside development** — unset, they accept an administrator credential, and a caller who can report a disconnect can auto-submit a formal assessment |
| `DEMO_IDENTITIES` | `false`                             | Whether `GET /api/session` lists the directory's identities **and their tokens**. For a review deployment; off in production |
| `AUTO_SEED` | `false`                                   | Bootstrap the demo course, three quizzes and four identities on start-up. Idempotent, and never reconfigures a quiz that already has a version |
| `FRONTEND_DIST` | *(empty)*                             | Directory of the built UI to serve at `/`. Set, the API and UI are one origin and **CORS is absent rather than configured** |
| `RETAKE_CONFIGURATION_POLICY` | `ACTIVE_AT_RETAKE`      | Which immutable version a retake locks. An unrecognised value refuses to start |
| `FORMAL_CONDITIONS_VERSION` | `2026.1`                  | Recorded on every acknowledgement, so "which conditions did this learner agree to?" survives a wording change |
| `SESSION_HEARTBEAT_TIMEOUT_SECONDS` | `90`              | The threshold UC-09 publishes so the platform's session monitor and the module agree on one number |
| `ANALYTICS_FLAG_THRESHOLD` | `40`                       | Wrong-answer percentage above which a question is flagged for review |
| `ANALYTICS_FLAG_MIN_RESPONSES` | `5`                    | Graded responses needed before a question can be flagged at all |

`ADMIN_API_TOKEN` and `SYSTEM_API_TOKEN` are **enforced, not advised**: the application refuses to
start when `ENVIRONMENT` is anything outside `{development, dev, test, local}` and either is unset.
Both guards are no-ops while unset and the symptom is silent — nothing errors, they simply admit
everybody — so a requirement nobody enforced would eventually not be met.

No credentials are hard-coded, nothing secret is committed, and no secret, credential, learner answer,
answer key or coaching conversation is written to a log — the JSON formatter drops any context key
whose name suggests one, so a careless `extra=` cannot leak even by accident. `backend/.env.example` is the tracked template; `.env` is ignored.

Authentication is a deliberate seam: `app/modules/identity/` resolves a bearer token to a principal
with a role, and all ten capabilities depend only on that. Replacing
`app/modules/identity/security.py::resolve_principal` with the platform's real dependency is the
whole of the identity integration. Enrolment is the same shape: `qa_enrolments` is a placeholder
behind `EnrolmentPort`.

---

## Documentation

* [docs/API.md](docs/API.md) — every endpoint, status code and error code
* [docs/DATABASE.md](docs/DATABASE.md) — table-by-table schema and the company-database switch-over
* [docs/CSV_IMPORT.md](docs/CSV_IMPORT.md) — the CSV format per question type, with examples
* [docs/INTEGRATION.md](docs/INTEGRATION.md) — every seam between the ten capabilities, and the
  ports the company's own systems plug into
* [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — the hosted architecture, every environment variable a
  deployment needs, how to verify one actually works, and what is deliberately not production-ready
* [docs/DEPLOYMENT-AUDIT.md](docs/DEPLOYMENT-AUDIT.md) — the pre-deployment requirements audit:
  what was verified, what was missing, and what was done about it
* [docs/UC11-FINDINGS.md](docs/UC11-FINDINGS.md) — every defect the integrity layer has found,
  including the three that a fully green test suite could not see
