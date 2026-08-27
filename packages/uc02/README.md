# UC-02 — Contextual Awareness Setup

Assembles everything known about a learner at the moment a coaching session
begins: qualification level (NARIC), course enrolments and progress (Courses
Agent), legal speciality and practice area (Legal Foot Prints), and the last 20
questions asked across prior sessions. Normalises all of it into one typed
`SessionContext`, keyed by the session id the caller supplies.

**None of the four upstream systems exist yet.** This repository defines the
contracts UC-02 needs, implements deterministic mocks behind them, and builds the
real business logic against those contracts. When the company delivers, an
integration engineer replaces four adapter classes and changes four config values.
See [`docs/integration.md`](docs/integration.md) and
[`docs/assumptions.md`](docs/assumptions.md).

## Quick start

```bash
pip install -r requirements-dev.txt
cp .env.example .env          # optional; defaults are safe without it
python -m pytest              # 186 tests, none skipped
uvicorn uc02.main:app --reload
```

```bash
# Initialize context for a session UC-01 created
curl -X POST http://localhost:8000/api/v1/context/initialize \
  -H "Content-Type: application/json" -H "X-User-Id: learner-1" \
  -d '{"session_id": "sess-from-uc01"}'

# Status flags only (never context content)
curl http://localhost:8000/api/v1/context/sess-from-uc01/status -H "X-User-Id: learner-1"
```

## What this repository does not contain

No frontend. No database, ORM, migrations or schema. No LLM, agent framework,
RAG or vector store. No production auth. No calls to any external URL — there
isn't one in the codebase, and a test asserts it. No code from, or reference to,
UC-01 or UC-03 through UC-10.

## Layout

| Path | Responsibility |
|---|---|
| `uc02/domain/ports/` | The six interfaces. The most important artefact here. |
| `uc02/domain/models/` | `SessionContext` and its parts; the provider record types. |
| `uc02/domain/explanation_mapping.py` | NARIC level → explanation template. Configuration, not scattered conditionals. |
| `uc02/domain/errors.py` | Typed provider and application errors. |
| `uc02/application/context_assembly_service.py` | Concurrent retrieval, failure isolation, session binding. |
| `uc02/application/normalisers.py` | The failure/default matrix, as pure functions. |
| `uc02/application/explanation_renderer.py` | Deterministic renderer used to prove the depth difference. No LLM. |
| `uc02/infrastructure/providers/mocks/` | Every scenario, deterministically triggerable. |
| `uc02/infrastructure/providers/company/` | Documented stubs marking where real adapters go. |
| `uc02/infrastructure/repositories/` | In-memory store with TTL, behind one port. |
| `uc02/infrastructure/identity/` | Replaceable header-based identity shim. |
| `uc02/api/v1/` | HTTP surface and the narrow wire projection. |
| `uc02/composition.py` | The only place concrete implementations are chosen. |

## Key behaviours

**Session identity.** UC-02 never invents a `session_id` in production; it receives
one and treats it as opaque. Dev-minting is gated behind `ALLOW_DEV_SESSION_IDS`
(default off, must stay off in production).

**Resilience.** The four providers are called concurrently, each under
`PROVIDER_TIMEOUT_MS`, with a hard `CONTEXT_ASSEMBLY_BUDGET_MS` ceiling. No single
failure — and no combination — prevents a valid context. All four down returns a
valid default context with a personalisation notice. Nothing is ever fabricated to
fill a gap.

**`empty` ≠ `unavailable`.** A learner with no question history and a history
service that is down are different states, recorded differently.

**Build once.** Context is assembled at session start and reused. A second
initialize returns the stored context and issues zero provider calls.

**Privacy.** The full context is not user-readable. A session id alone is
insufficient — ownership is checked against the resolved user id, and another
user's session returns 404, never the context. Question text never leaves the
server and never reaches a log line.

## Configuration

All of it in [`.env.example`](.env.example). Provider selection, timeouts, the
history limit, TTL, and three guarded switches that all default to the safe value.

## Tests

```bash
python -m pytest            # everything
python -m pytest tests/unit tests/integration -v
```

None are skipped or disabled.
