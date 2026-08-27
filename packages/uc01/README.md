# UC-01 — Coaching Session Initiation

A **standalone, self-contained** implementation of UC-01. It assumes no company
repository, database, authentication system, API or UI exists. All four external
dependencies sit behind internal contracts with clearly-labelled **development mock
adapters**, so the real integrations can be added later without rewriting UC-01 business
logic.

> ⚠️ **The integrations in this repository are mocks.** NARIC, Courses Agent, Case Prep /
> Case Files and Profile data are fixtures in `uc01/adapters/mock/`. Nothing here contacts
> a real service. `GET /api/v1/healthz` says so explicitly in its response.

---

## What UC-01 does

Opens a coaching session in one of three modes — `free-form`, `course-linked`,
`case-linked` — and does it *gracefully*:

* a **mode selector** driven by server-computed availability, with a human reason for
  every disabled mode;
* **course → lesson** and **case file** pickers, with server-side access validation (a
  client-supplied id is never trusted);
* a **context-aware greeting** composed from server-side templates, using the learner's
  name, course, lesson and explanation level when available, and degrading to a generic
  greeting when not;
* the documented **Level 5 fallback** when NARIC data is missing, always labelled as a
  default and never as a NARIC result, plus a **Continue without calibration** option;
* a **session record for every open attempt**, including partial, degraded and rejected
  ones, with `initializing / active / degraded / failed` status;
* graceful degradation throughout — one failed dependency disables at most one mode and
  never the interface.

Scope note: this project implements **UC-01 only**. No UC-02..UC-10 business logic is
present.

---

## Technology stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.11 |
| API | FastAPI + Pydantic v2 (typed request/response schemas, generated OpenAPI) |
| Server | uvicorn |
| Persistence | SQLite via stdlib `sqlite3` + plain `.sql` migrations (in-memory store also available) |
| Frontend | Static HTML/CSS/vanilla JS served by the same app — no build step, no npm |
| Tests | pytest + FastAPI `TestClient` |
| Logging | stdlib `logging` with a JSON formatter |

No dependency beyond FastAPI, Pydantic and uvicorn is required at runtime. Rationale for
every choice is in [`docs/DESIGN.md`](docs/DESIGN.md).

---

## Setup

```bash
# 1. (optional) virtual environment
python -m venv .venv
# Windows:  .venv\Scripts\activate
# POSIX:    source .venv/bin/activate

# 2. dependencies
python -m pip install -r requirements-dev.txt

# 3. configuration (optional — defaults work out of the box)
cp .env.example .env        # Windows: copy .env.example .env

# 4. initialise persistence
python -m uc01.persistence.migrate

# 5. run
python -m uc01
#   or: python -m uvicorn uc01.api.asgi:app --reload
```

Then open:

| URL | What it is |
| --- | --- |
| <http://127.0.0.1:8000/> | Reference UI (mode selector, pickers, notices) |
| <http://127.0.0.1:8000/docs> | Interactive OpenAPI documentation |
| <http://127.0.0.1:8000/api/v1/healthz> | Health + which adapters are wired in |

### Signing in during development

Authentication is a development stand-in behind an interface. Send one of:

```
Authorization: Bearer dev-alice     # full access: 3 courses, 1 case file, NARIC level 8
Authorization: Bearer dev-bob       # 1 course, NO case files, NARIC still calibrating
Authorization: Bearer dev-carol     # NO courses, 1 case file, incomplete profile + NARIC
```

The three users exist so the per-user states (no case files, no courses, incomplete
profile, incomplete NARIC) can be exercised without any global flag. The reference UI has
a user switcher.

```bash
curl -H "Authorization: Bearer dev-alice" http://127.0.0.1:8000/api/v1/session-bootstrap
```

---

## Running the tests

```bash
python -m pytest              # whole suite
python -m pytest -v           # per-test names
python -m pytest tests/test_security.py

python -m ruff check .        # lint (optional; ruff is not a required dependency)
```

249 tests covering happy paths and every failure path. See [Tests](#tests) below.

## Verifying the interface states without a browser

```bash
python scripts/verify_states.py
```

Prints mode availability, notices and greetings for the normal state, no-case-files,
courses-unavailable, NARIC-unavailable, profile-unavailable and everything-down
scenarios, plus a partial/failed open and eight authorization attempts. Output is
reproduced in [`docs/VERIFICATION.md`](docs/VERIFICATION.md).

---

## Architecture

```
Reference UI (static JS)  |  any API client
                │
                ▼
        UC-01 API  (uc01/api)            FastAPI routes, Pydantic schemas,
                │                        auth resolution, safe error envelope
                ▼
        UC-01 Service  (uc01/application)   the whole use case; no I/O detail
                │
                ▼
     Internal contracts  (uc01/contracts)   Protocols + contract exceptions
                │
        ┌───────┴─────────────────────────────┐
        ▼                                     ▼
  Mock adapters (uc01/adapters/mock)   Standalone persistence
  ── replace with uc01/adapters/real   (uc01/persistence: SQLite / in-memory)
```

Dependency rule, enforced by `tests/test_architecture.py`:

```
api  ->  application  ->  domain
                     ->  contracts
adapters             ->  contracts (+ domain types)
```

* `uc01/domain` imports nothing outside `uc01/domain` — no framework, no I/O.
* `uc01/application` never imports an adapter, persistence implementation or HTTP.
* `uc01/api/container.py` is the **only** file that knows which concrete adapter is used.

### External dependencies and where the real ones go

| Dependency | Contract | Shipped adapter (mock) | Real adapter goes here |
| --- | --- | --- | --- |
| NARIC | `NaricService` | `MockNaricAdapter` | `uc01/adapters/real/naric.py` |
| Courses Agent | `CoursesService` | `MockCoursesAdapter` | `uc01/adapters/real/courses.py` |
| Case Prep / Case Files | `CaseFileService` | `MockCaseFileAdapter` | `uc01/adapters/real/cases.py` |
| Profile | `ProfileService` | `MockProfileAdapter` | `uc01/adapters/real/profile.py` |
| Identity | `UserContextProvider` | `DevHeaderUserContextProvider` | replace in `container.py` |
| Greeting generation | `GreetingGenerator` | `LocalTemplateGreetingGenerator` (local, no AI service) | optional future adapter |
| Session storage | `SessionRepository` | `SqliteSessionRepository` | company store |

Switch one with an environment variable once its adapter exists:

```bash
UC01_NARIC_ADAPTER=real
```

Set it before the adapter exists and startup fails with an explicit message naming the
file and contract to implement. Full walkthrough:
[`docs/ADAPTER_REPLACEMENT.md`](docs/ADAPTER_REPLACEMENT.md).

---

## Project structure

```
uc01/
  domain/            enums, models, policy, messages, prompts, greeting templates
  contracts/         service Protocols, repository Protocol, contract exceptions, clock
  application/       session_service.py (the use case) + framework-free DTOs
  api/               FastAPI app, routes, schemas, container, deps, error handling
  adapters/
    mock/            TEMPORARY development adapters + fixtures + scenarios
    real/            placeholders + copy-paste template for real integrations
    dev_identity.py  development user-context provider
  persistence/       migrations/, SQLite repository, in-memory repository, migrate CLI
  web/               reference UI (index.html + static/)
  config.py          environment configuration
  logging_setup.py   JSON structured logging
tests/               249 tests: domain, service, API, security, architecture, adapters
docs/                design, API, mocks, persistence, adapters, handoff, audit
scripts/             verify_states.py
```

---

## API

Six endpoints plus a health check and one dev-only helper. Full request/response detail
in [`docs/API.md`](docs/API.md); the live spec is at `/docs` and `/openapi.json`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/session-bootstrap` | Load session-opening data and available modes |
| `GET` | `/api/v1/courses` | Accessible courses, each with its lessons |
| `GET` | `/api/v1/case-files` | Accessible case files |
| `POST` | `/api/v1/sessions` | Open a coaching session |
| `GET` | `/api/v1/sessions/{session_id}` | Read one of the caller's own sessions |
| `GET` | `/api/v1/healthz` | Liveness + which adapters are in use |
| `GET` | `/api/v1/dev/context` | Dev-only: dev users + mock scenario options (404 when dev mode is off) |

No endpoints exist for future use cases.

---

## Mock scenarios

Set them globally in `.env`, or per request with the dev-only `X-Dev-Scenarios` header:

```bash
curl -H "Authorization: Bearer dev-alice" \
     -H "X-Dev-Scenarios: courses=unavailable,naric=incomplete" \
     http://127.0.0.1:8000/api/v1/session-bootstrap
```

| Dependency | Scenarios |
| --- | --- |
| `naric` | `per_user`, `success`, `incomplete`, `calibrating`, `unavailable`, `invalid` |
| `courses` | `available`, `empty`, `unavailable`, `invalid` |
| `cases` | `available`, `empty`, `unavailable`, `invalid` |
| `profile` | `available`, `incomplete`, `unavailable` |

The header is ignored unless `UC01_DEV_MODE=true`, and it can only choose between mock
fixtures — it can never affect identity, authorization or the recorded
`naric_level_source`. Details: [`docs/MOCKS.md`](docs/MOCKS.md).

---

## Interface states

| State | Free-form | Course-linked | Case-linked |
| --- | --- | --- | --- |
| Normal | Available | Available | Available |
| No accessible case files | Available | Available | Disabled — "No accessible case files." |
| Courses unavailable | Available | Disabled — "Courses are temporarily unavailable." | Available |
| Everything down | **Available** | Disabled | Disabled |
| NARIC unavailable | Available | Available | Available |

NARIC never disables anything. Instead the response carries:

```
naric.level = 5
naric.source = "default"
naric.offer_continue_without_calibration = true
naric.notice = "NARIC calibration is unavailable right now. You can continue without
                calibration — your coaching explanations will use Level 5 by default."
```

---

## Persistence

Two tables in SQLite: `coaching_sessions` (one row per open attempt) and `session_events`
(append-only). Schema, migration commands and limitations:
[`docs/PERSISTENCE.md`](docs/PERSISTENCE.md).

```bash
python -m uc01.persistence.migrate            # apply
python -m uc01.persistence.migrate --status   # report
UC01_PERSISTENCE=memory python -m uc01        # no file at all
```

---

## Security posture

Everything below is validated **server-side** and covered by `tests/test_security.py`:

* identity comes from headers via an interface — never from a request body;
* course, lesson and case ids are re-validated for accessibility on every open;
* another user's session returns *404*, so ids cannot be probed;
* requests **forbid unknown fields**, so `naric_level`, `user_id`, `system_prompt`,
  `status` etc. are rejected with 422 rather than silently ignored;
* a disabled mode is refused by the API even if the UI control is bypassed;
* system prompts and guardrails live in `uc01/domain/prompts.py`, are never serialised,
  and cannot be supplied or overridden by a client;
* external text (profile names, course titles) is sanitised and confined to an untrusted
  prompt segment;
* error responses carry no traceback, exception class, SQL, URL or upstream message. A
  `debug` block exists **only** when `UC01_DEV_MODE=true` *and*
  `UC01_EXPOSE_ERROR_DETAILS=true`.

---

## Tests

| File | Covers |
| --- | --- |
| `test_session_modes.py` | all three modes open; enum strictness; cross-mode selection rules |
| `test_courses.py` | courses available / empty / unavailable / invalid; invalid + inaccessible course and lesson; downgrade |
| `test_cases.py` | case files available / none / unavailable / invalid; inaccessible case |
| `test_naric.py` | valid, incomplete, calibrating, unavailable, invalid; Level 5 default; continue without calibration |
| `test_profile.py` | profile available / unavailable / incomplete; generic greeting fallback |
| `test_session_logging.py` | records for normal, partial, degraded, failed and downgraded opens; events; persistence failure |
| `test_security.py` | identity, ownership, resource access, field override, disabled-mode bypass, prompt protection, error safety |
| `test_adapter_replacement.py` | the same service runs against three unrelated adapter families |
| `test_architecture.py` | import-graph layering, no `except: pass`, no other-UC logic |
| `test_domain.py` | mode availability policy and greeting templates |
| `test_mock_adapters.py` | every required mock scenario |
| `test_api_contract.py` | endpoint set, schemas, the four documented UI states |
| `test_persistence.py` | migrations, schema, both repositories, event seam |
| `test_logging.py` | structured logs contain the detail that responses must not |

---

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Phase 1/2 design record: stack, structure, layering, assumptions |
| [`docs/API.md`](docs/API.md) | Every endpoint, request/response schema, error behaviour |
| [`docs/MOCKS.md`](docs/MOCKS.md) | Mock scenarios, fixture data, how to drive each state |
| [`docs/PERSISTENCE.md`](docs/PERSISTENCE.md) | Schema, migrations, limitations, forward compatibility |
| [`docs/ADAPTER_REPLACEMENT.md`](docs/ADAPTER_REPLACEMENT.md) | Worked example of replacing a mock with a real integration |
| [`docs/INTEGRATION_HANDOFF.md`](docs/INTEGRATION_HANDOFF.md) | What a future integration engineer must do; multi-repo integration |
| [`docs/VERIFICATION.md`](docs/VERIFICATION.md) | Recorded output of the manual state verification |
| [`docs/REQUIREMENTS_AUDIT.md`](docs/REQUIREMENTS_AUDIT.md) | Requirement-by-requirement audit with evidence |
| [`docs/FILES.md`](docs/FILES.md) | Every file and why it exists |

---

## Known limitations

1. **All integrations are mocked.** Fixture data only; see the warning at the top.
2. The development identity provider is not a security control — it is a stable,
   replaceable stand-in so authorization logic can be written and tested.
3. SQLite is single-writer and file-local: right for development, not for a multi-instance
   deployment.
4. NARIC levels are modelled as integers 1..10 with 5 as the documented default. A
   different real scale is an adapter mapping concern.
5. The reference UI is a verification surface, not a production front end (no design
   system, no i18n, no client-side routing).
6. `session_events` is generic on purpose. UC-01 emits only its own initiation events;
   nothing from UC-07 or UC-10 is implemented.
