# UC-07 — Progress & Knowledge Gap Identification

A standalone, read-only backend service. It reads a learner's coaching history,
ratings, profile and course data through ports, and derives a **deterministic,
evidence-backed knowledge-gap report**. The only data it persists is the report
it generates; it never writes upstream.

No LLM. No RAG. No embeddings. No vector database. No agent framework. No
frontend. No production database. No production authentication.

## Run it

```bash
python -m pip install -e ".[dev]"          # fastapi, pydantic v2, pytest, httpx
cp .env.example .env                        # mock providers by default
uvicorn "uc07.api.app:create_app" --factory --port 8000
```

```bash
curl -H "X-User-Id: learner-001" localhost:8000/api/v1/gap-report
curl -H "X-User-Id: learner-001" localhost:8000/api/v1/gap-report/progress
curl localhost:8000/api/v1/healthz
```

`X-User-Id` is a **development identity seam**, not authentication: it is one
replaceable `CurrentUserProvider` implementation. No endpoint accepts a user id
as a parameter, in a path, or in a body.

## Test it

```bash
python -m pytest -q                 # full suite
python -m pytest tests/conformance -q   # adapter conformance kit
python -m pytest tests/architecture -q  # read-only / privacy / layering guards
```

## Layout

```
uc07/
  domain/        contract types, enums, typed errors, THE counting rule
  ports/         read-only upstream ports, the single write port, identity + clock
  application/   config/thresholds, aggregation, signals, unexplored, recommendations,
                 evidence guard, report assembly, the service
  adapters/
    mock/        deterministic scenarios (no randomness, no network, no sleeping)
    foreign/     a deliberately different upstream shape ("Nexus LMS") for swap proof
    real/        _template.py — copy this for a company adapter
    persistence/ in-memory GapReportRepository
    identity/    header / static CurrentUserProvider
    clock/       system / fixed clock
  api/           FastAPI routes, schemas, uniform error envelope, strict input
  composition.py provider registry + composition root
  observability.py JSON logging with a privacy allowlist
docs/            assumptions.md, SHARED_CONTRACT.md, INTEGRATION.md
tests/           unit, api, architecture, conformance, integration
```

## The rules that shape the code

* **Threshold** — a report exists at exactly 10 qualifying interactions
  (configurable), evaluated against current source data on every request.
* **One counting rule** — `uc07/domain/counting.py` is the only place that decides
  what counts.
* **Evidence or nothing** — every struggle gap carries interaction ids that
  resolve to interactions used in the analysis; a gap that fails the guard is
  rejected, not emitted.
* **Deterministic** — same inputs plus same configuration produce the same report,
  `report_id` included (the id is derived from a content fingerprint).
* **Statuses preserved** — `available`, `empty`, `partial`, `unavailable`,
  `invalid` are five different things; `empty` is never `unavailable`.
* **Read-only upstream** — enforced by architecture tests over ports *and*
  adapters.
* **Swap adapters, not logic** — `INTERACTION_LOG_PROVIDER=foreign` runs the
  unmodified service against a completely different upstream shape and produces
  the identical report.

See `docs/INTEGRATION.md` to add a real company source: one adapter file, one
registry line, one environment variable.
