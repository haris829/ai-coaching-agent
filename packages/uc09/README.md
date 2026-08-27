# UC-09 — Session Summary & Export

Turns a recorded coaching session into a structured summary and exports it as
**CPD evidence** a practising legal professional can present to a regulator.

That last phrase sets the standard for everything here. Every claim in an
emitted document is traceable to recorded session data; content that cannot be
traced is rejected whole rather than trimmed and stored.

This document describes how to work on this repository. For what the component
emits to the wider platform, see [`docs/SHARED_CONTRACT.md`](docs/SHARED_CONTRACT.md).
For pointing it at a real upstream, see [`docs/INTEGRATION.md`](docs/INTEGRATION.md).

---

## Quick start

```bash
python -m pip install -e ".[test]"
python -m pytest                 # 569 tests, no API key, no network
uvicorn uc09_summary.api.app:build_default_app --factory --reload
```

Try it:

```bash
curl -X POST localhost:8000/api/v1/sessions/sess-complete-multi-topic/summary \
     -H "X-User-Id: user-owner-001" -H "Content-Type: application/json" -d '{}'

curl localhost:8000/api/v1/summaries/<summary_id>/preview -H "X-User-Id: user-owner-001"
curl -o evidence.pdf localhost:8000/api/v1/summaries/<summary_id>/pdf -H "X-User-Id: user-owner-001"
```

The default configuration is entirely self-contained: mock upstreams, a
deterministic generator, a pure-Python PDF renderer, in-memory persistence.

---

## Layout

```
uc09_summary/
  domain/          enums, records, NARIC rules, grounding.  No I/O, no framework.
    grounding.py     <- the rule that every claim is true of the session
  ports/           every interaction with anything outside this component
  application/     the summary service: assemble, ground, store, export
  rendering/       ONE canonical HTML document, and the text it contains
  adapters/
    mock/            scenario matrix + deterministic generator + renderer fakes
    foreign/         a deliberately alien upstream, to prove replaceability
    real/            PDF renderer, clock, identity, http generator, _template.py
                     and larrycore_session.py - a runnable worked example
    memory/          in-memory repositories
  registry.py      provider selection: one lookup, one line per implementation
  composition.py   composition root
  api/             FastAPI routes and wire schemas
tests/
  conformance/     reusable, adapter-agnostic contract suites (registry-driven)
  support/         harness, factories, PDF helpers
docs/              assumptions.md, SHARED_CONTRACT.md, INTEGRATION.md
```

## Four ideas worth knowing before reading the code

**1. Grounding is the point.** `domain/grounding.py` checks every element of
every section against the session record before anything is stored. A response
carrying an ungrounded topic or an uncited authority is rejected **whole** — not
stripped of the bad part — because stripping turns a visible failure into an
invisible one. Start there.

**2. HTML is canonical; PDF is a rendering of it.** `rendering/html_document.py`
is the only module that decides what the document says. The renderer receives
that string and composes nothing. The printable fallback is therefore identical
to the PDF by construction, not by discipline.

**3. Provider selection is a registry lookup.** There is no
`if setting == "mock"` anywhere — a test asserts as much. Adding a provider is
one line in `registry.py`. An unregistered name stops startup with a message
naming the missing key and the file expected to supply it; there is no silent
fallback to a mock.

**4. Upstream is read-only by shape.** Every upstream port declares retrieval
methods only, and `tests/test_readonly_architecture.py` walks the registry to
assert that neither the ports nor any registered adapter exposes a mutating
method. Consequently the `summary_generated` transition is recorded on this
component's own record, not written back upstream.

---

## Configuration

Every setting is prefixed `UC09_` and may live in a `.env` file.

| Variable | Default | Purpose |
|---|---|---|
| `UC09_SESSION_PROVIDER` | `mock` | `mock`, `foreign`, or your adapter |
| `UC09_INTERACTION_PROVIDER` | `mock` | |
| `UC09_CITATION_PROVIDER` | `mock` | |
| `UC09_GAP_REPORT_PROVIDER` | `mock` | |
| `UC09_SUMMARY_GENERATOR` | `fake` | the deterministic generator; the whole suite runs on it |
| `UC09_DOCUMENT_RENDERER` | `simple` | pure-Python PDF writer |
| `UC09_SUMMARY_REPOSITORY` | `memory` | |
| `UC09_DOWNLOAD_LOG_REPOSITORY` | `memory` | |
| `UC09_CLOCK` | `system` | `fixed` for reproducible runs |
| `UC09_CURRENT_USER_PROVIDER` | `header` | reads `X-User-Id`; **not** authentication |
| `UC09_PROVIDER_TIMEOUT_SECONDS` | `5.0` | deadline adapters must honour |
| `UC09_ALLOW_DEV_SESSION_MINTING` | `false` | when off, the dev route does not exist |
| `UC09_UPSTREAM_BASE_URL` | `""` | real adapters only |
| `UC09_UPSTREAM_API_KEY` | `""` | real adapters only; unset for the whole suite |
| `UC09_LOG_LEVEL` / `UC09_LOG_JSON` | `INFO` / `true` | |

## Scenarios you can drive by hand

Session ids select a scenario in the mock adapters
(`adapters/mock/scenarios.py`):

| Session id | Scenario |
|---|---|
| `sess-complete-multi-topic` | complete, three topics, three authorities |
| `sess-in-progress` | mid-session → partial summary |
| `sess-single-topic` | one topic, deeper concepts, no padding |
| `sess-one-interaction` | one concept only → section reported `partial` |
| `sess-no-interactions` | nothing logged |
| `sess-no-citations` | nothing cited → empty section that says so |
| `sess-citations-down` | citation source unavailable (≠ empty) |
| `sess-gap-down` | gap report unavailable → session-derived next steps |
| `sess-nothing-to-report` | no interactions and no gap report |
| `sess-invalid-naric` | upstream sent an unmappable level |
| `sess-not-owned` | belongs to another learner |
| `sess-session-provider-down` | session source unavailable |

## Tests

```bash
python -m pytest                     # everything
python -m pytest tests/test_grounding.py -v          # the centre of the suite
python -m pytest tests/conformance                   # port contracts, all adapters
python -m pytest tests/test_foreign_adapter_swap.py  # replaceability, demonstrated
python -m ruff check uc09_summary tests
```

No test is skipped, xfailed or disabled. The suite needs no API key and makes
no network call.

## What this component deliberately does not do

No frontend. No production database. No production authentication. No agent
framework, no RAG, no embeddings, no vector store. No call to any URL that was
not configured through a port. And no other use case: it reads a session and
produces a summary.
