# UC-01 — Coaching Session Initiation — Phase 1 Project Design & Phase 2 Architecture

This document is the design record written **before** implementation. It is kept in the
repository so a future engineer can see the reasoning, not just the result.

Nothing in this repository assumes that any company repository, service, database,
authentication system, API, UI or shared infrastructure exists.

---

## 1. Selected technology stack

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python 3.11 | Already present in the target environment; strong typing support via `typing.Protocol`, dataclasses and enums, which is what the adapter/contract architecture needs. |
| API framework | FastAPI + Pydantic v2 | Request/response schemas are a hard requirement. Pydantic gives declarative validation, `extra="forbid"` (so a client cannot smuggle `naric_level` into a body), and generated OpenAPI docs for free. |
| ASGI server | uvicorn | Standard, already installed. |
| Persistence | SQLite via the stdlib `sqlite3` module plus plain `.sql` migration files | Zero external services, zero ORM lock-in, real SQL schema that a future engineer can read and port. An in-memory repository implementation is also provided for tests. |
| Frontend | Static HTML + CSS + vanilla JS (no build step, no npm) served by the same app | The UI's job here is to prove the degraded/disabled states are driven by the API. A framework plus build pipeline would add dependencies without adding evidence. Jinja2 is not installed in this environment, so rendering is client-side against the documented JSON contract. |
| Tests | pytest plus FastAPI TestClient (httpx) | Already installed. Tests exercise the real HTTP surface and the service/domain layers directly. |
| Logging | stdlib `logging` with a JSON formatter | "Basic structured logging suitable for later integration" — a formatter swap is all that is needed to ship into a company log pipeline. |

No third-party dependency is added beyond what the environment already provides.

---

## 2. Standalone project structure

```
uc01/
  domain/        pure business rules. No I/O, no HTTP, no framework imports.
  contracts/     internal interfaces (Protocols) plus contract-level exceptions.
  adapters/
    mock/        temporary development adapters (clearly labelled).
    real/        placeholders plus instructions for future real adapters.
  application/   the UC-01 use-case service and its request/result DTOs.
  api/           FastAPI app, routes, dependency wiring, auth, error handling.
  persistence/   migrations, SQLite repository, in-memory repository.
  web/           reference frontend (static assets).
tests/           automated tests for happy paths and every failure path.
docs/            design, API, mocks, persistence, adapter replacement, audit.
```

Layering rule (enforced by a test, `tests/test_architecture.py`):

```
api  ->  application  ->  domain
                     ->  contracts
adapters             ->  contracts (+ domain types)
```

* `domain/` imports nothing from the project except `domain/`.
* `application/` does not import `adapters/`, `api/` or `persistence/`.
* `contracts/` does not import `adapters/`.

That is what makes "replace the adapter, keep the business logic" true rather than
aspirational.

---

## 3. Frontend approach

A single reference page (`/`) that:

* calls `GET /api/v1/session-bootstrap` and renders the three modes with
  available / disabled state plus the human reason for each disabled mode;
* renders the course then lesson picker only for `course-linked`, and the case-file
  picker only for `case-linked`;
* renders a NARIC calibration notice with a **Continue without calibration** action
  that never blocks the session;
* renders a non-technical personalisation-unavailable notice;
* has loading states, disabled states, `aria-live` status regions, retry buttons and
  keyboard-reachable controls;
* contains a clearly marked **developer scenario panel** used to drive every mock
  state from the browser, so all UI states can be verified manually.

The frontend holds no authorization logic. Every disabled state it renders comes from
the server, and the server re-validates on `POST /api/v1/sessions`.

---

## 4. Backend approach

`POST /api/v1/sessions` is the only state-changing endpoint. Order of operations
matters and is deliberate:

1. Resolve the caller identity server-side (never from the body).
2. Generate `session_id` and persist the record immediately with status `initializing`.
3. Assemble `SessionContext` through adapters, catching contract exceptions per dependency.
4. Validate the requested mode against server-computed availability.
5. Validate the requested course/lesson/case IDs for accessibility, server-side.
6. Resolve the NARIC level and its source.
7. Compose the greeting from a server-side template layer.
8. Update the record to `active`, `degraded` or `failed`.

Because step 2 happens before any dependency call, a record exists for every attempt,
including rejected and crashed ones.

---

## 5. Persistence approach

Two tables, minimum practical design:

* `coaching_sessions` — one row per session-open attempt.
* `session_events` — append-only `(session_id, event_type, payload_json, occurred_at)`.

`session_events` is the forward-compatibility seam: future fields such as `question`,
`topic_tag`, `explain_differently_count` and `rating` (UC-07 / UC-10 territory) can be
appended as events without a schema change and without UC-01 implementing them.
UC-01 only emits its own initiation events.

Everything sits behind `SessionRepository`, so the standalone SQLite store can be
swapped for the company platform store later.

---

## 6. Authentication / user-context approach

`UserContextProvider` is an interface. The shipped implementation is
`DevHeaderUserContextProvider`: it reads a bearer token or `X-Dev-User` header and looks
it up in a small development user directory. It is documented as development-only and is
the single place to replace with the company authentication system.

The client never supplies `user_id` in a body. Session ownership is always checked
against the resolved caller.

---

## 7. Adapter / interface architecture

Four external dependencies, each with the same three-layer shape:

| Dependency | Contract (Protocol) | Mock adapter | Future real adapter goes here |
| --- | --- | --- | --- |
| NARIC assessment | `NaricService` | `MockNaricAdapter` | `uc01/adapters/real/naric.py` |
| Courses Agent | `CoursesService` | `MockCoursesAdapter` | `uc01/adapters/real/courses.py` |
| Case Prep / Case Files | `CaseFileService` | `MockCaseFileAdapter` | `uc01/adapters/real/cases.py` |
| Profile / personalisation | `ProfileService` | `MockProfileAdapter` | `uc01/adapters/real/profile.py` |

Adapters are the only place allowed to know an upstream payload shape. They return
domain dataclasses and raise contract exceptions (`DependencyUnavailableError`,
`InvalidUpstreamResponseError`, `ResourceNotAccessibleError`). The use-case service
handles those exceptions and never sees an HTTP status code, JSON key or SDK object.

Greeting generation is also behind an interface (`GreetingGenerator`) with a local
template implementation, so a future AI service can be introduced without touching
UC-01 business logic — and without UC-01 depending on one now.

---

## 8. Testing strategy

* **Domain tests** — pure functions: mode availability policy, NARIC resolution, greeting.
* **Service tests** — the use-case service wired to stub adapters, one test per failure mode.
* **API tests** — real HTTP requests through `TestClient`, asserting status codes, schemas,
  and that no technical detail leaks into responses.
* **Security tests** — cross-user session/course/case access, NARIC override attempts,
  disabled-mode bypass, system-prompt exposure and override attempts.
* **Architecture tests** — import-graph assertions that enforce the layering above.
* **Adapter-replacement test** — a hand-written adapter with a completely different
  upstream payload shape is substituted for the mock; the use-case service is not
  modified and all assertions still hold.

---

## 9. Files expected to be created

See `docs/FILES.md` for the final list with a one-line purpose for each file.

---

## 10. Assumptions and limitations

1. **All four integrations are mocked.** No real API is contacted. Mocks live only in
   `uc01/adapters/mock/` and every response they produce is fixture data.
2. NARIC levels are modelled as integers 1..10 with **5** as the documented default
   explanation level. The real scale, if different, is an adapter mapping concern.
3. The dev auth provider is not a security control. It exists so authorization logic can
   be written and tested against a stable user context.
4. SQLite is single-writer and file-local: fine for development and tests, not a
   multi-instance production store.
5. The developer scenario header (`X-Dev-Scenarios`) exists to make UI states testable.
   It is refused whenever `UC01_DEV_MODE` is false, and it can never influence identity,
   authorization or the recorded `naric_level_source`.
6. No UC-02..UC-10 business logic is implemented. `session_events` is the only
   forward-compatibility affordance, and it is generic.
