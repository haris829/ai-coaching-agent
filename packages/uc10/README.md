# UC-10 — Feedback & Improvement

A standalone backend service for a legal-education platform. It lets a learner rate any
response thumbs up or thumbs down with an optional comment, logs those ratings with the full
context the model improvement pipeline needs, and raises a **content review flag** when
responses on a topic consistently rate badly.

It creates no sessions, answers no questions, coaches nobody, and knows nothing about any
other component of the platform.

> **This file is about working on the repository.**
> What the component emits to a platform it cannot see → [`docs/SHARED_CONTRACT.md`](docs/SHARED_CONTRACT.md)
> How to plug a real upstream into it → [`docs/INTEGRATION.md`](docs/INTEGRATION.md)
> What we invented because the specification did not say → [`docs/assumptions.md`](docs/assumptions.md)

---

## Run it

```bash
python -m pip install -r requirements.txt
cp .env.example .env                     # defaults are safe: mock provider, minting off
uvicorn uc10.api.app:create_app --factory --reload
pytest -q                                # no network, no API key required
```

Health check: `GET /api/v1/healthz` reports which adapters are actually wired in.

## Try it

```bash
# rate a response (identity is a dev header; not authentication -- see docs/INTEGRATION.md)
curl -X POST localhost:8000/api/v1/interactions/int_answer/rating \
     -H 'Content-Type: application/json' -H 'X-User-Id: user_alice' \
     -d '{"rating":"down","comment":"the limitation period looks wrong"}'

# read your own rating back
curl localhost:8000/api/v1/interactions/int_answer/rating -H 'X-User-Id: user_alice'

# the platform team's view (needs DEV_ADMIN_TOKEN set, and its own header)
curl localhost:8000/api/v1/admin/flags -H "X-Admin-Token: $DEV_ADMIN_TOKEN"
```

## Layout

```
uc10/
  domain/         records, closed vocabularies, the flagging rule   (depends on nothing)
  ports/          the interfaces to everything outside this component
  application/    rating capture, flagging, the failure-isolation facade
  adapters/
    memory/       lightweight local persistence, clock, policy config, notification sink
    mock/         the specification's mock scenarios + fault injection + dev identity
    foreign/      a deliberately foreign upstream, used to prove the swap is real
    real/         _template.py -- copy this to write a real adapter
    registry.py   provider selection: one dict, one line per provider
  api/            FastAPI routes, schemas, error shaping, composition root (deps.py)
tests/
  unit/           the platform contract and the flagging rule, in isolation
  integration/    capture, supersede, windows, flag integrity, failure isolation, swap proof
  conformance/    reusable, adapter-agnostic contract suites (see below)
  architecture/   read-only port, no hardcoded threshold, layering, privacy, no network
docs/             assumptions.md · SHARED_CONTRACT.md · INTEGRATION.md
```

Dependency direction is one-way and enforced by tests: `domain ← ports ← application ← api`,
with `adapters` known only to the composition root.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `INTERACTION_PROVIDER` | `mock` | Registry key. An unregistered key **fails at startup**; there is never a silent fallback. |
| `FLAG_DOWN_RATE_THRESHOLD` | `0.30` | Down-rate at or above which a topic flags. Admin-configurable; read at evaluation time. |
| `FLAG_MINIMUM_SAMPLE_SIZE` | `10` | **Assumed by us — needs company confirmation** (`docs/assumptions.md` A-01). |
| `FLAG_WINDOW_DAYS` | `7` | Rolling window. |
| `HISTORICAL_RATING_WINDOW_HOURS` | `24` | How long after delivery a response can still be rated. |
| `ALLOW_DEV_SESSION_MINTING` | `false` | Dev only. This component receives an opaque `session_id` and never creates one. |
| `DEV_ADMIN_TOKEN` | unset | Dev admin credential. Unset ⇒ admin endpoints deny everyone. |
| `LOG_LEVEL` | `INFO` | |

No configuration value is a secret this component cannot start without.

## The conformance kit

Any implementation of a port must pass the same suite. It is parameterised on the adapter
under test, not on the mock:

```bash
pytest tests/conformance -q                                   # every built-in adapter
pytest tests/conformance -q --adapter=company                 # a real one
pytest tests/conformance -q --adapter=company --conformance-fixtures=fixtures.json
```

Point it at a new adapter and it answers, in one command, whether the integration is
correct — no new test is written.

## Things that are true here and worth knowing before you change anything

* **Nothing is unrateable.** There is no branch on response category in the capture path, and
  a test asserts every category — including one the component has never seen — is rateable.
* **A dismissed comment box never loses a rating.**
* **No threshold literal exists in `domain/` or `application/`.** An architecture test fails
  the build if one appears. Policy arrives through `ThresholdConfigProvider` at evaluation
  time.
* **A decided flag cannot be dropped.** The intent is persisted before the write and cleared
  only after the repository confirms it.
* **Feedback failures cannot reach a caller's main path.** Call
  `FeedbackFacade`; it returns a result for every failure, including unexpected defects.
* **Learner content never reaches a log.** Every question, response and comment in the test
  suite carries a canary string, and an autouse fixture fails any test whose log output
  contains one.
* **The interaction port is read-only by shape** and an architecture test enforces it.

## Quality gates

```bash
pytest -q          # 381 tests, no skips, no network
ruff check .       # lint
```
