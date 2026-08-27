# Integration runbook — UC-08 Learning Streaks & Milestones

For an engineer who has never opened this codebase. You have a real upstream
system; UC-08 currently runs on mocks. This is how you connect them.

Read [`SHARED_CONTRACT.md`](./SHARED_CONTRACT.md) for the types and vocabularies,
and [`assumptions.md`](./assumptions.md) for what we guessed. Nothing else in this
repository is required reading.

---

## 1. The cost of an integration

Three points. Nothing else changes.

| # | What | Where |
|---|---|---|
| 1 | **One new adapter file** — the payload mapping. Only you know your shape. | `uc08/adapters/real/<port>.py`, copied from `uc08/adapters/real/_template.py` |
| 2 | **One registry line** | `uc08/registry.py` |
| 3 | **One environment variable** | your deployment config / `.env` |

Then one command to prove it:

```bash
python -m pytest tests/conformance -q
```

If connecting a real provider requires touching a second file beyond those three
points, that is a defect in this architecture, not in your integration. Raise it.

**Zero edits** to `uc08/domain/`, `uc08/application/`, `uc08/api/`,
`uc08/adapters/persistence/`, any existing adapter, or any existing test.

---

## 2. Per-dependency reference

### 2.1 Activity read model

| | |
|---|---|
| **File to create** | `uc08/adapters/real/activity.py` |
| **Template to copy** | `uc08/adapters/real/_template.py` |
| **Port to implement** | `uc08.ports.upstream.ActivityProvider` |
| **Constructor** | `__init__(self, clock: Clock, *, timeout_seconds: float = 5.0)` — fixed (A-23) |
| **Registry line** | in `ACTIVITY_PROVIDERS`, `uc08/registry.py` |
| **Environment variable** | `ACTIVITY_PROVIDER=company` |
| **Conformance command** | `python -m pytest tests/conformance/test_activity_provider_conformance.py -q` |

Exact signatures:

```python
def last_activity_at(self, user_id: str) -> datetime | None: ...
def interactions_in_window(self, user_id: str, since: datetime) -> ActivityWindowRead: ...
def question_count(self, user_id: str) -> QuestionCountRead: ...
def topics_in_window(self, user_id: str, since: datetime) -> TopicsRead: ...
```

**Verify these assumptions against the real system before writing the adapter:**

| ID | Check |
|---|---|
| A-01, A-03 | That continuity means a rolling 24 hours from the current UTC time, inclusive at exactly 24h. **Check this one first — it is the rule most expensive to get wrong.** |
| A-02 | That a "day" for the once-per-day increment is a UTC calendar day, not a learner-local one. |
| A-09 | Whether your read model already shows the interaction being recorded, and whether the `interaction_id` UC-08 is given matches the id you report. If they differ, the exclusion in `_read_continuity` cannot match and a returning learner may never reset. |
| A-04 | What the platform wants when your read model is down. UC-08 preserves the streak. Confirm. |
| A-05, A-07 | That the caller's `interaction_id` is stable and unique per account. |
| A-16 | That you can supply a first-mention timestamp per topic. If not, say so before writing the adapter — the weekly summary needs it to mean "last week". |
| A-17, A-29 | That `question_count` is a lifetime total, and whether one interaction is one question. |

### 2.2 Gap report

| | |
|---|---|
| **File to create** | `uc08/adapters/real/gap_report.py` |
| **Template to copy** | `uc08/adapters/real/_template.py` (same file; swap the port and the one method) |
| **Port to implement** | `uc08.ports.upstream.GapReportProvider` |
| **Constructor** | `__init__(self, clock: Clock, *, timeout_seconds: float = 5.0)` |
| **Registry line** | in `GAP_REPORT_PROVIDERS`, `uc08/registry.py` |
| **Environment variable** | `GAP_REPORT_PROVIDER=company` |
| **Conformance command** | `python -m pytest tests/conformance/test_gap_report_provider_conformance.py -q` |

Exact signature:

```python
def suggested_topic(self, user_id: str) -> Topic | None: ...
```

Use `uc08.domain.naric.normalise_naric_level` and
`normalise_completion_percent` — do **not** map the level yourself. They hold the
invalid-value rule, and using them is what makes your adapter agree with every
other family.

**Verify before writing:** A-06 (levels 4 and 6 → profile), A-08 (the real
completion representation — if it is a 0–1 fraction, convert it in your adapter
to an integer percentage before calling the normaliser), A-27 (whether course
progress belongs on the suggestion at all).

### 2.3 Persistence

| | |
|---|---|
| **File to create** | `uc08/adapters/persistence/<store>.py` |
| **Ports** | `StreakRepository`, `BadgeRepository`, `WeeklySummaryRepository`, `FreezeOfferRepository`, `ProcessedInteractionStore` |
| **Wiring** | `uc08/composition.py::_build_persistence` — this one *is* a composition-root edit, because persistence is not registry-selected |
| **Environment variable** | `PERSISTENCE=<name>` |
| **Conformance command** | `python -m pytest tests/conformance/test_repository_conformance.py -q` after appending one entry to `BACKENDS` |

Two hard requirements:

- A failed write must raise `RepositoryWriteFailed`. UC-08 retries once, then
  preserves the last known count and pages engineering. If your adapter swallows
  a failure and returns normally, that protection is gone.
- `BadgeRepository.award` must be idempotent on `(user_id, milestone)`. A repeat
  is a no-op and the original `awarded_at` stands.

**Verify before writing:** A-30 (nothing here assumes a company schema — do not
import one into the domain), A-21 (whether the platform needs opaque badge ids).

### 2.4 Notifications

| | |
|---|---|
| **File to create** | `uc08/adapters/sinks/<transport>.py` |
| **Port** | `uc08.ports.sinks.NotificationSink` |
| **Wiring** | pass it to `build_container(notifications=...)`, or extend the composition root |

Raise `NotificationSendFailed` on a delivery failure — that is what triggers the
next-day retry for a weekly summary (A-19). **Do not build a notification UI.**
UC-08 emits `badge_awarded` and `weekly_summary` events; rendering is yours.

### 2.5 Engineering alerts

| | |
|---|---|
| **File to create** | `uc08/adapters/sinks/<pager>.py` |
| **Port** | `uc08.ports.sinks.EngineeringAlertSink` |

**Must not raise.** A broken pager must not turn a persistence problem into a
failed coaching request. UC-08 guards this, but do not rely on the guard.

### 2.6 Authentication

| | |
|---|---|
| **File to create** | `uc08/adapters/identity/<mechanism>.py` |
| **Port** | `uc08.ports.identity.CurrentUserProvider` |
| **Wiring** | `build_container(identity=...)` |
| **Environment variable** | replaces `DEV_IDENTITY_HEADER` |

**Required before any deployment (A-22).** The shipped adapter reads a request
header and verifies nothing. Your implementation must resolve the account from
server-side credentials only, and must ignore any identifier in the path, query
string or body. `IdentityNotResolved` becomes a `401`.

---

## 3. Worked example — connecting a real activity read model

Everything below is the complete change. Three files are shown; only the first
is new, the second gains one line, the third is configuration.

### Step 1 — the assumptions you checked first

Before writing anything, you confirmed with the platform team:

- continuity is a rolling 24 hours from the current UTC time (A-01), inclusive at
  exactly 24h (A-03);
- the increment day is a UTC calendar day (A-02);
- `/v2/learners/{id}/interactions` **excludes** an interaction until it is
  committed, and the `id` it reports is the same `interaction_id` UC-08 is passed
  (A-09);
- `questionsTotal` is a lifetime count (A-29);
- topics carry `firstSeenAt` (A-16).

Two of those came back different from our guess, and both were adapter-local:
timestamps arrive as epoch seconds, and the completion percentage arrives as a
string. Neither needed a domain change.

### Step 2 — the new file: `uc08/adapters/real/activity.py`

```python
"""Company activity read model adapter.

The only file in this repository that knows the company payload shape.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import httpx

from uc08.domain.enums import SourceStatus
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import (
    ActivityInteraction,
    ActivityWindowRead,
    QuestionCountRead,
    TopicMention,
    TopicsRead,
)
from uc08.domain.time_utils import ensure_utc
from uc08.ports.clock import Clock
from uc08.ports.upstream import ActivityProvider


class CompanyActivityAdapter(ActivityProvider):
    def __init__(self, clock: Clock, *, timeout_seconds: float = 5.0) -> None:
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        # TODO(1) endpoint -> done: read from the environment, never hard-coded.
        self._base_url = _required_env("COMPANY_ACTIVITY_BASE_URL")
        # TODO(2) auth -> done: server-side only, never echoed to a caller.
        self._token = _required_env("COMPANY_ACTIVITY_TOKEN")

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    # -- reads --------------------------------------------------------------
    def last_activity_at(self, user_id: str) -> datetime | None:
        body = self._get(f"/v2/learners/{user_id}/activity")
        raw = body.get("lastInteractionEpoch")          # TODO(3) mapping -> done
        return None if raw is None else self._epoch_to_utc(raw)

    def interactions_in_window(self, user_id: str, since: datetime) -> ActivityWindowRead:
        boundary = ensure_utc(since)
        body = self._get(
            f"/v2/learners/{user_id}/interactions",
            {"fromEpoch": int(boundary.timestamp())},
        )
        rows = body.get("items")
        if rows is None:
            return ActivityWindowRead(interactions=(), status=SourceStatus.EMPTY)
        if not isinstance(rows, list):
            raise ProviderInvalidResponse(self.port_name, "activity collection shape is not usable")

        found = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderInvalidResponse(self.port_name, "activity entry shape is not usable")
            identifier, epoch = row.get("id"), row.get("atEpoch")
            if not identifier or epoch is None:
                raise ProviderInvalidResponse(
                    self.port_name, "activity entry is missing an identifier or a timestamp"
                )
            moment = self._epoch_to_utc(epoch)
            if moment >= boundary:
                found.append(ActivityInteraction(interaction_id=str(identifier), occurred_at=moment))

        return ActivityWindowRead(
            interactions=tuple(found),
            status=SourceStatus.AVAILABLE if found else SourceStatus.EMPTY,
        )

    def question_count(self, user_id: str) -> QuestionCountRead:
        body = self._get(f"/v2/learners/{user_id}/activity")
        if "questionsTotal" not in body:
            return QuestionCountRead(count=0, status=SourceStatus.EMPTY)
        try:
            count = int(str(body["questionsTotal"]).strip())
        except (TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(self.port_name, "question count is not an integer") from exc
        if count < 0:
            raise ProviderInvalidResponse(self.port_name, "question count is negative")
        return QuestionCountRead(count=count, status=SourceStatus.AVAILABLE)

    def topics_in_window(self, user_id: str, since: datetime) -> TopicsRead:
        boundary = ensure_utc(since)
        body = self._get(
            f"/v2/learners/{user_id}/topics", {"fromEpoch": int(boundary.timestamp())}
        )
        rows = body.get("items") or []
        if not isinstance(rows, list):
            raise ProviderInvalidResponse(self.port_name, "topic collection shape is not usable")

        first_seen: dict[str, datetime] = {}
        for row in rows:
            label, epoch = row.get("label"), row.get("firstSeenAt")
            if not label or epoch is None:
                raise ProviderInvalidResponse(self.port_name, "topic entry is missing a name or a timestamp")
            moment = self._epoch_to_utc(epoch)
            if moment >= boundary and (label not in first_seen or moment < first_seen[label]):
                first_seen[str(label)] = moment

        mentions = tuple(
            TopicMention(name=name, first_mentioned_at=moment) for name, moment in first_seen.items()
        )
        return TopicsRead(
            topics=mentions,
            status=SourceStatus.AVAILABLE if mentions else SourceStatus.EMPTY,
        )

    # -- transport and error translation (TODO(4) -> done) -------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self._base_url}{path}",
                params=params,
                timeout=self._timeout_seconds,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(self.port_name, f"deadline of {self._timeout_seconds}s exceeded") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(self.port_name, "activity read model did not answer") from exc

        if response.status_code == 404:
            return {}                       # absent, not broken: an empty answer
        if response.status_code >= 500:
            raise ProviderUnavailable(self.port_name, "activity read model did not answer")
        if response.status_code >= 400:
            raise ProviderInvalidResponse(self.port_name, "activity read model rejected the request")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderInvalidResponse(self.port_name, "response body is not usable") from exc
        if not isinstance(body, dict):
            raise ProviderInvalidResponse(self.port_name, "response body is not usable")
        return body

    def _epoch_to_utc(self, raw: Any) -> datetime:
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise ProviderInvalidResponse(self.port_name, "timestamp is not usable") from exc

    # -- conformance harness (TODO(5) -> done) ------------------------------
    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], ActivityProvider]]:
        from uc08.adapters.real._company_stub import COMPANY_ACTIVITY_SCENARIOS

        return COMPANY_ACTIVITY_SCENARIOS


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set; refusing to start rather than guess an endpoint")
    return value
```

Note what the error messages do **not** contain: no URL, no vendor name, no
upstream field name, no company error text. The conformance suite fails the
adapter if any of those leak — that is not a style rule, it is an assertion.

The stub referenced by `conformance_scenarios` (a recorded response set or a
patched client) lives beside the adapter. It is part of the same delivery, not a
change to anything that already exists.

### Step 3 — the one registry line: `uc08/registry.py`

```diff
 ACTIVITY_PROVIDERS: dict[str, ProviderEntry] = {
     "mock": ProviderEntry(
         "uc08.adapters.mock.activity:MockActivityProvider",
         "in-process deterministic activity read model",
     ),
     "foreign_lexicon": ProviderEntry(
         "uc08.adapters.foreign.activity:ForeignActivityAdapter",
         "deliberately foreign payload family, used to prove replaceability",
     ),
+    "company": ProviderEntry(
+        "uc08.adapters.real.activity:CompanyActivityAdapter", "company activity read model"
+    ),
 }
```

### Step 4 — the one config value

```diff
-ACTIVITY_PROVIDER=mock
+ACTIVITY_PROVIDER=company
 COMPANY_ACTIVITY_BASE_URL=https://<the real host>
 COMPANY_ACTIVITY_TOKEN=<from the platform secret store>
```

### Step 5 — prove it

```bash
python -m pytest tests/conformance -q          # your adapter is discovered automatically
python -m pytest -q                            # the whole suite, unchanged
```

The conformance suite reads the registry, so `company` appears in it the moment
Step 3 lands. **No new test is written to validate a real adapter.**

Declaring the scope-named scenarios as well (`activity_23h59m_ago`,
`activity_24h01m_ago`, `multiple_interactions_same_day`, `no_activity`,
`question_count_<n>`) opts your adapter into
`tests/integration/test_foreign_adapter_swap.py`, which runs the unmodified
service against every declaring family and requires identical results. That is
the strongest evidence available that your integration is correct, and it costs
you only fixture data.

### Step 6 — what was not touched

```
uc08/domain/**            unchanged
uc08/application/**       unchanged
uc08/api/**               unchanged
uc08/adapters/mock/**     unchanged
uc08/adapters/foreign/**  unchanged
uc08/adapters/persistence/**  unchanged
tests/**                  unchanged
```

---

## 4. If the name is wrong, it fails at startup

Setting a provider name with no registered implementation does not fall back to
a mock. It refuses to start, and says exactly what is missing:

```
ProviderNotRegistered: ACTIVITY_PROVIDER='company' has no registered implementation.
Registered names for the 'activity' port: ['foreign_lexicon', 'mock'].
Add one line to ACTIVITY_PROVIDERS in uc08/registry.py pointing at the class that
implements uc08.ports.upstream.ActivityProvider (start from
uc08/adapters/real/_template.py). There is no fallback to a mock: refusing to start.
```

A registry entry that points at a missing module, a missing class, or a class
that does not implement the port raises `ProviderRegistrationBroken` and names
the file it expected to find.

---

## 5. Running it

```bash
python -m pip install -e ".[dev]"
cp .env.example .env
python -m uvicorn uc08.api.app:create_app --factory --port 8008

# the identity header is the development stand-in for authentication
curl -X POST localhost:8008/api/v1/streaks/record-activity \
  -H 'X-UC08-Subject: learner-7781' -H 'Content-Type: application/json' \
  -d '{"interaction_id":"int-1","session_id":"sess-1"}'

curl localhost:8008/api/v1/streaks -H 'X-UC08-Subject: learner-7781'
```

---

## 6. Driving the weekly summary without a scheduler

UC-08 owns no scheduler, cron daemon or background worker. Generation is an
explicit call, and a caller drives it with whatever the platform already uses.

```bash
# Any Monday (UTC). Safe to call more than once: the second call is a no-op for
# the week and only retries a delivery that is due.
curl -X POST https://uc08/api/v1/weekly-summaries/generate \
  -H "Authorization: <the learner's server-side credential>"
```

Properties a scheduler can rely on:

- **Idempotent per week.** Repeat calls on the same Monday generate one record
  and send one notification.
- **Self-retrying.** If the send failed, a call the following day retries the
  delivery instead of generating a new record.
- **No backlog.** A run after a four-week outage produces **one** summary, for
  the week that just ended. The missed weeks are named in `skipped_weeks`, not
  generated.
- **Never batch-sends.** There is no code path that emits more than one summary
  per call.
- **Fails soft.** A degraded upstream still produces a record, with the omission
  named.

So a daily job is a reasonable choice: on Mondays it generates, on other days it
picks up a failed delivery. There is nothing to make idempotent on your side.

---

## 7. Non-negotiables

1. **The adapter is the only place upstream payload shapes are known.** No
   upstream field name, nesting or error string escapes it. Callers see platform
   types and the three typed contract errors. The conformance suite asserts this
   against every registered adapter, not as a review comment.

2. **The adapter never invents data.** A missing value maps to the documented
   default with its source field marked accordingly — `naric_level_source:
   default` with `naric_level_status: invalid` or `empty` — never to a
   plausible-looking guess. A missing gap-report suggestion is `null`; a missing
   completion percentage is `null` with a status. No default is silent.

3. **`empty` and `unavailable` are different states.** A successful read with
   nothing in it is `empty`. A source that did not answer raises
   `ProviderUnavailable`. Conflating them makes a working learner look broken and
   a broken system look quiet.

4. **Authorisation stays server-side, inside the adapter.** Credentials are read
   from the environment or the platform secret store, used in the adapter, and
   never accepted from or echoed to a caller. No endpoint of this component
   accepts a user identifier, and no adapter should reintroduce one.

5. **If the real payload cannot be mapped onto the platform contract, that is a
   contract conversation, not an adapter workaround.** Raise it. Do not widen the
   NARIC enum locally, do not add a level, do not scale an ambiguous fraction
   into a percentage, do not relabel a naive timestamp as UTC, and do not bend a
   domain model to fit an upstream quirk. Every one of those choices is invisible
   in a code review and expensive in a learner's streak.
