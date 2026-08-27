# UC-08 — Learning Streaks & Milestones

A standalone backend service that tracks consecutive-day coaching activity,
awards milestone badges, and generates a weekly summary. It is the gamification
layer for CPD engagement on a legal-education platform.

**There is no AI in this component.** It is arithmetic over timestamps, which is
exactly why it has to be exact: a streak silently reset by a timezone bug or a
write error destroys weeks of engagement and cannot be reconstructed. The one
rule that shapes the whole design is that a streak is **never** reset because
something failed.

This file is about working on the repository. Two other documents matter more if
you are integrating with it:

- **[`docs/SHARED_CONTRACT.md`](docs/SHARED_CONTRACT.md)** — what this component
  emits and expects, with every field marked *specified* or *assumed*.
- **[`docs/INTEGRATION.md`](docs/INTEGRATION.md)** — how to replace a mock with a
  real upstream: one file, one registry line, one environment variable.
- **[`docs/assumptions.md`](docs/assumptions.md)** — everything we invented
  because the company has not specified it, and what breaks if we guessed wrong.

---

## Quick start

```bash
python -m pip install -e ".[dev]"
python -m pytest -q                       # 310 tests, no network, no API key
cp .env.example .env
python -m uvicorn uc08.api.app:create_app --factory --port 8008
```

```bash
# The identity header is a development stand-in for authentication (A-22).
curl -X POST localhost:8008/api/v1/streaks/record-activity \
  -H 'X-UC08-Subject: learner-7781' -H 'Content-Type: application/json' \
  -d '{"interaction_id":"int-1","session_id":"sess-1"}'

curl localhost:8008/api/v1/streaks  -H 'X-UC08-Subject: learner-7781'
curl localhost:8008/api/v1/badges   -H 'X-UC08-Subject: learner-7781'
```

Out of the box it runs on deterministic in-process mocks and an in-memory store.
No network call is made anywhere, by anything.

---

## What it does, and what it deliberately does not

**Does:** records a coaching interaction against a streak; increments once per
UTC day; resets only on genuine inactivity; offers a monthly streak freeze;
awards permanent milestone badges at 10 / 50 / 100 questions; generates a weekly
summary with topics, question count, streak length and a suggested topic; emits
notification events for a caller to render.

**Does not:** create coaching sessions, coach, answer questions, analyse gaps,
generate feedback, render any UI, talk to a production database, authenticate
anyone for real, run a scheduler, or assume that any sibling component exists.

---

## Layout

```
uc08/
  domain/           pure rules. No I/O, no clock, no framework.
    enums.py            closed platform vocabularies (all lowercase values)
    models.py           the records this component owns and the shapes it reads
    streak_rules.py     THE streak arithmetic. The only place a reset can be produced.
    naric.py            upstream value -> platform contract normalisation
    time_utils.py       UTC-only helpers. Rejects naive datetimes.
    errors.py           typed contract errors
  ports/            the nine interfaces. Two are read-only by shape.
  application/      services. Orchestration only; the rules live in domain/.
    streak_service.py       record activity, read state, freeze
    streak_persistence.py   retry once, then preserve. Never reset.
    badge_service.py        milestones, exactly once, permanently
    weekly_summary_service.py   generate, log, deliver, retry, never batch
  adapters/
    mock/           deterministic in-process upstreams + the scenario matrix
    foreign/        a deliberately foreign payload family (see below)
    real/_template.py   copy-paste skeleton for a real adapter
    persistence/    in-memory and JSON-file stores, plus fault injection
    clock/          SystemClock and FixedClock
    sinks/          notification and engineering-alert sinks
    identity/       minimal replaceable identity
  registry.py       provider selection: one lookup, one line per provider
  composition.py    the composition root
  config.py         environment configuration
  api/              FastAPI routes and schemas
tests/
  unit/             rules, boundary, idempotency, badges, freeze, summaries, write failure
  conformance/      adapter-agnostic contract suites, parameterised on the adapter
  integration/      HTTP surface, security, the foreign-family swap proof
  architecture/     read-only ports, no-reset-from-exception, clock injection, scope
```

---

## The rules, precisely

**The streak window is a rolling 24 hours from the current UTC time**, not a
calendar comparison. Activity 23h59m ago increments. Activity 24h01m ago resets.
Both of those moments are "yesterday" on the calendar, which is why the
distinction is tested explicitly.

**The count increments at most once per UTC calendar day.** Precisely: increment
only when a prior qualifying interaction fell in the window *and* the last
counted activity was not on the current UTC day. Twelve questions in an afternoon
are one day.

**Failures never cost a streak.** A write failure retries exactly once, then
preserves the last known count and pages engineering. An activity-read failure
preserves the count too. The reset builder requires an `InactivityEvidence` value
that can only be constructed with zero prior qualifying interactions, so no
exception handler can reach it — and an AST call-graph test asserts that across
every `except` block in the package.

**Time is a dependency.** Nothing outside `uc08/adapters/clock/clocks.py` reads
the machine clock; an architecture test enforces it. Tests drive the boundary by
advancing a `FixedClock`. Nothing sleeps, and nothing is random.

---

## Configuration

See [`.env.example`](.env.example). The variables that change behaviour:

| Variable | Default | Effect |
|---|---|---|
| `ACTIVITY_PROVIDER` | `mock` | Registry key for the activity read model. An unknown name fails at startup. |
| `GAP_REPORT_PROVIDER` | `mock` | Registry key for the gap report. |
| `STREAK_WINDOW_HOURS` | `24` | The rolling qualifying window. |
| `BADGE_MILESTONES` | `10,50,100` | Thresholds, ascending. |
| `FREEZE_MIN_STREAK_DAYS` | `7` | Minimum streak for a freeze offer. |
| `FREEZE_PER_CALENDAR_MONTH` | `1` | Must be `1`; anything else fails loudly (A-11). |
| `FREEZE_OFFER_EXPIRY_HOURS` | `24` | How long an unanswered offer stays acceptable (A-12). |
| `WEEKLY_SUMMARY_DAY` | `monday` | Generation day, UTC. |
| `PERSISTENCE` | `memory` | `memory` or `jsonfile`. Neither is a production database. |
| `ALLOW_DEV_SESSION_MINTING` | `false` | Leave off. UC-08 receives a `session_id`; it does not create one. |

---

## Swapping a mock for a real provider

Three points, and nothing else:

1. one new adapter file, copied from `uc08/adapters/real/_template.py`
2. one line in `uc08/registry.py`
3. one environment variable

```bash
python -m pytest tests/conformance -q     # covers your adapter automatically
```

The conformance suites discover adapters from the registry, so a newly
registered adapter is validated without a test being written. An unregistered
provider name refuses to start and names the missing implementation — there is no
silent fallback to a mock.

`tests/integration/test_integration_swap_cost.py` performs that swap against a
third adapter family with a third payload shape and asserts, from the repository
itself, that no protected file was touched.
`tests/integration/test_foreign_adapter_swap.py` runs the unmodified service
against every registered family and requires identical results.

Full runbook, with a worked example: [`docs/INTEGRATION.md`](docs/INTEGRATION.md).

---

## Running the tests

```bash
python -m pytest -q                                       # everything
python -m pytest tests/unit/test_streak_boundary.py -v    # the 23h59m / 24h01m rule
python -m pytest tests/unit/test_streak_write_failure.py -v   # the critical rule
python -m pytest tests/architecture -v                    # the structural guarantees
python -m pytest tests/conformance -q                     # every adapter, every port
```

No test is skipped, xfailed or disabled. The suite needs no network and no API
key, and `tests/integration/test_no_network_no_credentials.py` proves it by
blocking egress and running a full flow.

---

## Scheduling the weekly summary

There is no scheduler here on purpose. Generation is an explicit call:

```
POST /api/v1/weekly-summaries/generate
```

It is idempotent per week, retries a failed delivery the following day, and never
produces a backlog: a run after a four-week outage yields **one** summary, for the
week that just ended, with the missed weeks named rather than generated. A plain
daily job is enough. See [`docs/INTEGRATION.md`](docs/INTEGRATION.md) §6.
