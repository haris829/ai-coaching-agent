# Pre-deployment audit — UC-01 … UC-11 as one system

Written before any change, so the fixes that follow can be checked against the gaps that motivated
them. Every row was verified by reading the code or running the system, not inferred from a passing
test.

## Method

Requirement → UC → implementation → test coverage → integration status. Sources: the UC-01…UC-11
requirement sheets, `README.md`, `docs/API.md`, `docs/DATABASE.md`, `docs/INTEGRATION.md`, the
migrations, the OpenAPI document the application generates, and the five gates.

The audit deliberately distrusts green tests. Three defects found during UC-11 (F-16, F-17, F-18 in
`docs/UC11-FINDINGS.md`) were all invisible to a green suite, and each was found only by exercising
the system the way a caller does. So the question asked of every capability here was not "does it
have tests" but **"has anyone driven it over real HTTP against a migrated database?"**

---

## What is genuinely done

| UC | Implementation | Tests | Live-HTTP verified | Status |
|---|---|---|---|---|
| UC-01 Quiz Configuration & Rules | `modules/quiz_configuration` | yes | **yes** — `verify_e2e` §17–20 | PASS |
| UC-02 Question Bank | `modules/question_bank` | yes | **yes** — §2–14 | PASS |
| UC-03 Attempt Delivery | `modules/attempt_delivery` | yes | **yes** — §21–24 | PASS |
| UC-04 Scoring | `modules/scoring` | yes | **yes** — §25 | PASS |
| UC-05 Pass/Fail & Certificate | `modules/certification` | yes | **yes** — §26 | PASS |
| UC-06 Feedback Report | `modules/feedback` | yes | **yes** — §27 | PASS |
| UC-07 AI Coaching | `modules/coaching` | yes | **yes** — §28 | PASS |
| UC-08 Retake Management | `modules/retakes` | yes | **no** | PARTIAL |
| UC-09 Formal Assessment | `modules/formal_assessment` | yes | **no** | PARTIAL |
| UC-10 Analytics & Reporting | `modules/analytics` | yes | **no** | PARTIAL |
| UC-11 Global DoD | `tests/global_dod` | n/a | in-process | PASS |

The domain work for all eleven is real: real tables, real adapters, real transactions, no fake
business logic. The gap is not implementation — it is **verification depth** for the last three, and
**reachability** for a reviewer.

---

## Gaps

### A. Real-user verification — the three newest capabilities have never been driven over HTTP

**A1.** `scripts/verify_e2e.py` ends at §29 and covers UC-01 → UC-07. UC-08, UC-09 and UC-10 have
no live-server section at all. Their coverage is `TestClient` (in-process ASGI, schema built from
the models) plus `tests/integration/`.

That is exactly the blind spot that hid F-16 (a CHECK constraint the fakes did not have), F-17 (an
error class that raised `TypeError` on every call) and F-18 (an immutability trigger missing from
every migrated database). All three were in UC-08/UC-09 code. Treating those three capabilities as
verified on the strength of in-process tests would be repeating the mistake that produced them.

*Fix:* extend `verify_e2e` with live sections for retakes, formal assessment and analytics, driving
the same journeys the company will.

### B. A reviewer cannot actually exercise UC-09, and the demo credentials are not deployable

**B1.** `scripts/seed.py` seeds an administrator and two learners. **There is no assessor.** UC-09's
entire review-and-approve workflow — the one that decides whether a certificate is ever issued — is
unreachable in a seeded system.

**B2.** The seed creates one quiz, deliberately unconfigured. Nothing is configured as a **formal
assessment**, so the conditions, identity confirmation, device session, disconnect handling and
assessor approval cannot be reached from a fresh deployment either.

**B3.** Seed tokens are the literals `admin-token`, `learner-token`, `learner2-token`, with no way to
override them. On a publicly reachable review deployment those are published credentials for an
administrator account.

**B4.** `GET /api/session` lists the directory's tokens only when `ENVIRONMENT` is `development` or
`test` — correct, and the demo UI's role switcher depends on it. So a deployment configured as
`production` (which `Settings` now *requires* for the guards to be real) has a working API and a UI
nobody can authenticate to. The two requirements are in direct conflict and neither is wrong; the
switch is missing.

**B5.** `settings.auto_seed` is defined and **never read anywhere**. Dead configuration that reads
as a feature.

### C. The demo UI stops at UC-07

`frontend/src/App.tsx` routes cover configuration, questions, topics, CSV import, learner rules,
attempt and reports. There is **no page for retakes or attempt history (UC-08), formal assessment
(UC-09), or analytics (UC-10)**. Section 6 of the brief names all four as things the demo must
demonstrate.

### D. The application is not startable as a hosted service

**D1.** No `railway.json`, `nixpacks.toml`, `Procfile` or `Dockerfile`. No production start command —
`npm run dev:api` hardcodes `--reload --port 8000`.

**D2.** `settings.host` defaults to `127.0.0.1` and `settings.port` to `8000`. Railway injects
`PORT` and requires binding `0.0.0.0`. Bound to loopback, the container passes its own health check
and is unreachable from outside.

**D3.** Nothing serves the built frontend. Deployed as-is this needs two services plus a CORS
allow-list naming the frontend's generated domain — more moving parts and a wider surface than the
single origin the Vite proxy already models locally.

**D4.** No migration step at deploy time. A fresh Railway database would have no schema.

**D5.** `requirements.txt` contains no PostgreSQL driver, so `DATABASE_URL=postgresql://…` fails at
import. The database portability the README promises is currently unreachable in practice.

**D6.** SQLite on Railway's ephemeral filesystem is discarded on every redeploy. Every attempt,
result and certificate the company creates while reviewing would vanish on the next deploy —
silently, which is worse than failing.

### E. Documentation describes a seven-capability system

`README.md` opens "Seven capabilities, one API, one database", lists UC-01 … UC-07, and its API
section stops at UC-07. It is the first thing a reviewer reads and it under-describes the system by
four capabilities. `frontend/package.json` and the root `package.json` description say the same.

---

## Not gaps — checked and correct

* **No committed secrets.** `git ls-files` shows only `backend/.env.example`; `.env`, `*.db` and
  `.venv` are ignored.
* **No hardcoded localhost in application logic.** The only occurrences are the two settings
  defaults, which is where a default belongs.
* **Error envelope.** One shape everywhere, request id echoed, no stack traces — `verify_e2e` §29.
* **Health endpoints.** Split correctly: `/api/health/live` touches nothing, `/api/health` checks
  the database and returns **503** when it is unreachable rather than a cheerful "degraded".
* **Logging redaction.** Recursive, depth-capped, and forbids `token`/`secret`/`password`/`api_key`
  in context.
* **Authorization.** Every route requires a credential except six named public ones, enforced over
  the generated OpenAPI document by `tests/global_dod/test_api_authorization.py`.
* **Immutability.** Eleven database triggers, verified on a *migrated* database by `verify_e2e` §0
  and compared against the models by `tests/test_schema_migration.py`.
* **Provider failure handling.** Certificate, CPD, coaching and review-queue boundaries are all
  retryable ports; with no AI provider configured UC-07 reports itself unavailable rather than
  inventing teaching.
* **Configuration validation at startup.** Unrecognised retake policy, blank conditions version, and
  missing tokens outside development all refuse to start.

---

## Fix order

1. **D** — make it startable as a hosted service (Postgres driver, `0.0.0.0:$PORT`, static serving,
   migrations on deploy, Railway config).
2. **B** — make it reviewable (assessor, formal quiz, overridable tokens, explicit demo switch).
3. **A** — verify it (live sections for UC-08/09/10).
4. **C** — the four missing demo pages.
5. **E** — documentation.
6. All five gates, then deploy.
