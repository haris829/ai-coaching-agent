# Files created and why

81 files, ~12,250 lines. Every file has one job.

## Project root

| File | Why |
| --- | --- |
| `README.md` | Entry point: what UC-01 does, stack, setup, run, tests, architecture, API summary, limitations |
| `pyproject.toml` | Package metadata, dependencies, pytest configuration, ruff configuration |
| `requirements.txt` | Runtime dependencies (fastapi, pydantic, uvicorn) |
| `requirements-dev.txt` | Adds pytest + httpx for the test client |
| `.env.example` | Every environment variable with its default and its meaning — including the four adapter switches |
| `.gitignore` | Excludes `data/`, `.env`, caches, build artefacts |

## `uc01/` — application package

| File | Why |
| --- | --- |
| `__init__.py` | Package docstring stating the layering rule and that no other UC lives here |
| `__main__.py` | `python -m uc01` development server entry point |
| `config.py` | Environment/`.env` configuration → immutable `Settings`; forces the dev scenario header and error `debug` off unless dev mode is on |
| `logging_setup.py` | JSON structured logging with a stable envelope, so a company log pipeline is a formatter swap |

### `uc01/domain/` — pure business rules (no I/O, no framework)

| File | Why |
| --- | --- |
| `enums.py` | Every mode/status/source value in one place: `SessionMode`, `SessionStatus`, `NaricLevelSource`, `DependencyName`, `DependencyState`, `NaricAssessmentState`, `LinkedResourceType`, `DependencyFailurePolicy`, `SessionEventType`. `SessionMode.parse` is the strict parser |
| `models.py` | The internal contract: `UserContext`, `UserProfile`, `Course`, `Lesson`, `CaseFile`, `NaricAssessment`, `DependencyStatus`, `ModeAvailability`, `NaricResolution`, `LinkedResource`, `SessionContext`, `Greeting`, `SessionRecord`, `SessionEvent`. Defines `DEFAULT_EXPLANATION_LEVEL = 5` |
| `policy.py` | `evaluate_mode_availability` (free-form is unconditional) and `resolve_naric_level` (the Level 5 fallback and its source labelling) |
| `messages.py` | Every user-facing string. Centralised so tests can assert nothing technical is ever shown |
| `prompts.py` | Privileged system prompts + guardrails, versioned, never serialised. `sanitize_untrusted_text` and `PromptPayload` keep external text out of the instruction channel |
| `greeting.py` | `LocalTemplateGreetingGenerator`: the server-side greeting/template layer. Never invents a name/course; never attributes a defaulted level to NARIC |
| `errors.py` | UC-01 business errors, each with a stable `code`, a safe `user_message` and a `failure_code` for the session record |

### `uc01/contracts/` — internal interfaces

| File | Why |
| --- | --- |
| `services.py` | `NaricService`, `CoursesService`, `CaseFileService`, `ProfileService`, `GreetingGenerator`, `UserContextProvider` — the Protocols UC-01 depends on |
| `repository.py` | `SessionRepository` Protocol, so the store is replaceable |
| `exceptions.py` | The only three failures an adapter may raise: `DependencyUnavailableError`, `InvalidUpstreamResponseError`, `ResourceNotAccessibleError` |
| `clock.py` | `Clock` / `IdGenerator` Protocols plus `SystemClock` / `UuidIdGenerator`, injected so records are deterministic in tests |

### `uc01/application/` — the use case

| File | Why |
| --- | --- |
| `session_service.py` | All of UC-01: bootstrap, catalogue listing, session opening, ownership-checked read. Enforces record-first persistence, server-side validation, per-dependency degradation, NARIC fallback, greeting composition and status assignment |
| `dto.py` | Framework-free inputs/outputs (`OpenSessionCommand`, `BootstrapResult`, `CatalogueResult`, `OpenSessionResult`, `Notice`) so the service is callable without FastAPI |

### `uc01/api/` — HTTP layer

| File | Why |
| --- | --- |
| `app.py` | `create_app()` factory: container, error handlers, router, static frontend, lifespan |
| `asgi.py` | `uvicorn uc01.api.asgi:app` entry point |
| `routes.py` | The six UC-01 endpoints plus health and the dev-only context helper |
| `schemas.py` | Pydantic request/response schemas. Requests use `extra="forbid"`; responses are allow-lists built field-by-field so server-only data cannot leak |
| `container.py` | Composition root — **the only file that knows which concrete adapter is used**. Contains the `>>> register the real adapter here <<<` seams |
| `deps.py` | FastAPI dependencies: identity from headers only, scenario resolution, per-request service, integrations notice |
| `errors.py` | Exception handlers producing one safe envelope for every failure; `debug` only in developer mode |

### `uc01/adapters/` — the boundary

| File | Why |
| --- | --- |
| `dev_identity.py` | `DevHeaderUserContextProvider` — development identity behind the real interface |
| `mock/__init__.py` | Labels the package as temporary development adapters; exports `IS_MOCK` |
| `mock/scenarios.py` | Every required mock state as an enum, plus the dev-header parser |
| `mock/fixtures.py` | Imitation upstream payloads and the three development users |
| `mock/naric.py` | Mock NARIC: success / incomplete / calibrating / unavailable / invalid, with the normalisation a real adapter needs |
| `mock/courses.py` | Mock Courses Agent: available / empty / unavailable / invalid, plus the accessibility check |
| `mock/cases.py` | Mock Case Prep: available / empty / unavailable / invalid, plus the accessibility check |
| `mock/profile.py` | Mock Profile: available / incomplete / unavailable, never inventing a name |
| `real/__init__.py` | Instructions for where and how the real adapters are added |
| `real/template.py` | Copy-paste skeleton with transport error handling and the mapping rules; raises `NotImplementedError` so it can never pass for a real integration |

### `uc01/persistence/` — standalone store

| File | Why |
| --- | --- |
| `migrations/001_init.sql` | The schema: `coaching_sessions` + append-only `session_events` |
| `db.py` | Connection management (locked single connection, WAL, foreign keys) and the idempotent migration runner |
| `sqlite_repository.py` | `SessionRepository` over SQLite; all row↔domain mapping in one class |
| `memory_repository.py` | In-memory `SessionRepository` for tests and `UC01_PERSISTENCE=memory` |
| `migrate.py` | `python -m uc01.persistence.migrate [--status] [--path]` |

### `uc01/web/` — reference UI

| File | Why |
| --- | --- |
| `index.html` | Mode selector, course/lesson and case pickers, NARIC block, notices, session view, dev panel; skip link, fieldset/legend, `aria-live` regions |
| `static/app.js` | Renders exactly what the API reports; no authorization or availability logic of its own |
| `static/styles.css` | Visual distinction for available/disabled/degraded states; focus styles; dark-mode support |

## `tests/` — 249 tests

| File | Why |
| --- | --- |
| `conftest.py` | Isolated-database app fixture, auth/scenario header helpers, deterministic clock and id generator, service factory |
| `stubs.py` | A second, independent adapter family — proves UC-01 depends on contracts, not on the mocks |
| `test_session_modes.py` | All three modes open; strict enum parsing; cross-mode selection rules; free-form survives everything |
| `test_courses.py` | Courses available/empty/unavailable/invalid; invalid + inaccessible course and lesson; rejection and downgrade |
| `test_cases.py` | Cases available/none/unavailable/invalid; inaccessible case; outage does not break the interface |
| `test_naric.py` | Policy unit tests + API tests for valid/incomplete/calibrating/unavailable/invalid, Level 5 default, continue-without-calibration |
| `test_profile.py` | Profile available/unavailable/incomplete; generic greeting; no invented data; course context preserved |
| `test_session_logging.py` | Records for normal/partial/degraded/failed/downgraded opens; events; persistence failure; record-first guarantee |
| `test_security.py` | Identity, ownership, resource access, field override, disabled-mode bypass, prompt protection and injection, error safety, dev-header gating |
| `test_adapter_replacement.py` | Three unrelated adapter families through one service; container error for an unimplemented real adapter |
| `test_architecture.py` | Import-graph layering, adapter imports confined to the container, no `except: pass`, no other-UC logic |
| `test_domain.py` | Mode availability policy and greeting templates in isolation |
| `test_mock_adapters.py` | Every required mock scenario and the scenario plumbing |
| `test_api_contract.py` | Endpoint set, OpenAPI schemas, response shapes, the four documented UI states, frontend serving |
| `test_persistence.py` | Migrations, schema columns, both repositories under identical assertions, event seam, foreign key |
| `test_logging.py` | Structured log envelope; technical detail is logged but not returned |

## `scripts/`

| File | Why |
| --- | --- |
| `verify_states.py` | Prints every interface state, a partial/failed open and eight authorization attempts, using the real API and no server. Re-run after any adapter swap |

## `docs/`

| File | Why |
| --- | --- |
| `DESIGN.md` | Phase 1/2 record: stack choices, structure, layering, testing strategy, assumptions |
| `API.md` | Every endpoint, request/response schema, status codes, failure behaviour table |
| `MOCKS.md` | Mock scenarios, fixture data, how to drive each state, header guardrails |
| `PERSISTENCE.md` | Schema, migrations, record-first flow, forward compatibility, limitations |
| `ADAPTER_REPLACEMENT.md` | Step-by-step mock → real replacement with code and a checklist |
| `INTEGRATION_HANDOFF.md` | What to replace when the real APIs arrive; three ways to merge this repo into the platform |
| `VERIFICATION.md` | Recorded output of the state verification, with a requirement→evidence table |
| `REQUIREMENTS_AUDIT.md` | Requirement-by-requirement audit: implementation, files, test, status |
| `FILES.md` | This file |
