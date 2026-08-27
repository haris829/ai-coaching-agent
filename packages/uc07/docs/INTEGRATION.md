# UC-07 Integration Runbook

For an engineer who has never seen this repository. Integrating a real company
source means writing **one adapter file**, adding **one registry line**, and
changing **one environment variable**. Nothing else.

Read `docs/SHARED_CONTRACT.md` for the types and `docs/assumptions.md` for the
assumptions you must confirm before you start.

---

## 0. Ground rules

1. The adapter is the **only** place that may know upstream field names, nesting,
   URLs, authentication, value spellings or error strings.
2. The adapter **never invents data**. If the payload cannot satisfy the platform
   contract, raise `ProviderInvalidResponse`. Do not default, guess, or widen a
   domain model.
3. The adapter is **read-only**. No `create`/`update`/`delete`/`patch`/`save`/
   `write`/`post`/`put`/`insert` method may exist — architecture tests fail the
   build if one appears.
4. Authorization stays **server-side**: the adapter receives a `user_id` that was
   resolved by `CurrentUserProvider`. It must never take an identity from a
   request payload, and never echo an upstream identity back into a record.
5. A contract mismatch is a **contract discussion**, not a domain-model hack.

---

## 1. Per-dependency reference

### 1.1 Interaction log (coaching history)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/real/<system>_interaction_log.py` |
| Template to copy | `uc07/adapters/real/_template.py` |
| Port interface | `uc07.ports.read_only.InteractionLogProvider` |
| Methods | `for_user(user_id) -> list[InteractionRecord]`, `count_for_user(user_id) -> int`, `status_for_user(user_id) -> SourceStatus` |
| Registry line | `INTERACTION_LOG_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` in `uc07/composition.py` |
| Environment variable | `INTERACTION_LOG_PROVIDER=<name>` |
| Conformance command | `pytest tests/conformance/test_interaction_log_conformance.py -q` |
| Assumptions to verify first | A-01…A-05, A-07, A-14, A-31, A-32, A-33, A-44 |

### 1.2 Feedback (ratings)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/real/<system>_feedback.py` |
| Template to copy | `uc07/adapters/real/_template.py` |
| Port interface | `uc07.ports.read_only.FeedbackProvider` |
| Methods | `for_interactions(ids) -> list[FeedbackRecord]`, `status_for_interactions(ids) -> SourceStatus` |
| Registry line | `FEEDBACK_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` |
| Environment variable | `FEEDBACK_PROVIDER=<name>` |
| Conformance command | `pytest tests/conformance/test_feedback_conformance.py -q` |
| Assumptions to verify first | A-11, A-13, A-27, A-28, A-34, A-44 |

### 1.3 Learner profile (speciality areas, NARIC level)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/real/<system>_profile.py` |
| Template to copy | `uc07/adapters/real/_template.py` |
| Port interface | `uc07.ports.read_only.LearnerProfileProvider` |
| Methods | `get_profile(user_id) -> LearnerProfile` |
| Registry line | `PROFILE_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` |
| Environment variable | `PROFILE_PROVIDER=<name>` |
| Conformance command | `pytest tests/conformance/test_learner_profile_conformance.py -q` |
| Assumptions to verify first | A-15, A-16, A-17, A-18 (and the NARIC mapping table) |

### 1.4 Courses (catalogue, recommendations, enrolments)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/real/<system>_courses.py` |
| Template to copy | `uc07/adapters/real/_template.py` |
| Port interface | `uc07.ports.read_only.CoursesProvider` |
| Methods | `resolve_recommendations(topics)`, `enrolments_for(user_id)`, `catalogue()`, `status()` |
| Registry line | `COURSES_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` |
| Environment variable | `COURSES_PROVIDER=<name>` |
| Conformance command | `pytest tests/conformance/test_courses_conformance.py -q` |
| Assumptions to verify first | A-23, A-24, A-25, A-26, A-35, A-48 |

### 1.5 Gap-report persistence (the only write seam)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/persistence/<store>.py` |
| Port interface | `uc07.ports.persistence.GapReportRepository` |
| Methods | `save(report) -> None`, `get_current(user_id) -> GapReport \| None` |
| Wiring | pass it to `build_container(settings, repository=...)` |
| Requirement | `get_current` MUST scope by `user_id`; a caller may never receive another learner's report. |
| Assumptions to verify first | A-36, A-37, A-43 |

### 1.6 Identity (`CurrentUserProvider`)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/identity/<mechanism>.py` |
| Port interface | `uc07.ports.identity.CurrentUserProvider` |
| Methods | `resolve(request) -> str`, raising `IdentityUnresolved` |
| Registry line | `CURRENT_USER_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` |
| Environment variable | `CURRENT_USER_PROVIDER=<name>` |
| Requirement | Identity is resolved server-side. The API accepts no input, so never read one from the request body, path or query. |

---

## 2. Worked example (complete)

Company system: "Acme Coach API". It exposes
`GET /v3/learners/{id}/coaching?cursor=…` returning:

```json
{
  "meta": {"completeness": "COMPLETE", "total": 14},
  "items": [
    {
      "id": "ci-1",
      "thread": {"id": "th-9"},
      "createdAt": 1767603600,
      "tags": {"main": "contract_formation"},
      "kind": "CONCEPT",
      "level": "L6",
      "answer": {"id": "an-1"},
      "parentId": null,
      "rephraseCount": 2,
      "ratingState": "RATED",
      "questionText": "…"
    }
  ]
}
```

### Step 1 — the adapter file

`uc07/adapters/real/acme_interaction_log.py`:

```python
"""Acme Coach API adapter for the InteractionLogProvider port (read-only)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError

from uc07.domain.enums import NaricLevel, SourceStatus
from uc07.domain.errors import (
    PortName,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import InteractionRecord
from uc07.ports.read_only import InteractionLogProvider

_PORT = PortName.INTERACTION_LOG

# Upstream vocabularies live here and nowhere else.
_LEVELS = {
    "L3": NaricLevel.LEVEL_3,
    "L4": NaricLevel.LEVEL_4,
    "L5": NaricLevel.LEVEL_5,
    "L6": NaricLevel.LEVEL_6,
    "L7": NaricLevel.LEVEL_7,
    "L7P": NaricLevel.LEVEL_7_PLUS,
}
_COMPLETENESS = {
    "COMPLETE": SourceStatus.AVAILABLE,
    "TRUNCATED": SourceStatus.PARTIAL,
    "NONE": SourceStatus.EMPTY,
}
_RATING_STATE = {"RATED": "rated", "AWAITING": "pending"}


class AcmeInteractionLogProvider(InteractionLogProvider):
    def __init__(self, *, base_url: str, token: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds

    # -- transport + error translation -----------------------------------
    def _get(self, user_id: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self._base_url}/v3/learners/{user_id}/coaching",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(_PORT) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(_PORT) from exc

        if response.status_code >= 500:
            raise ProviderUnavailable(_PORT)
        if response.status_code >= 400:
            raise ProviderInvalidResponse(_PORT)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderInvalidResponse(_PORT) from exc
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(_PORT)
        return payload

    # -- payload mapping --------------------------------------------------
    def _map(self, raw: dict[str, Any], user_id: str) -> InteractionRecord:
        try:
            level = _LEVELS[raw["level"]]                     # unknown -> KeyError -> typed error
            rating_state = _RATING_STATE[raw["ratingState"]]
            return InteractionRecord(
                interaction_id=raw["id"],
                session_id=raw["thread"]["id"],
                user_id=user_id,                              # server-side identity
                asked_at=datetime.fromtimestamp(raw["createdAt"], tz=timezone.utc),
                topic_tag=raw["tags"]["main"],                # consumed as supplied
                question_class=str(raw["kind"]).lower(),
                naric_level=level,
                response_id=raw["answer"]["id"],
                follow_up_of=raw.get("parentId"),
                explain_differently_count=raw.get("rephraseCount", 0),
                rating_state=rating_state,
                # questionText is deliberately NOT read, mapped, stored or logged.
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderInvalidResponse(_PORT) from exc

    # -- port -------------------------------------------------------------
    def for_user(self, user_id: str) -> Sequence[InteractionRecord]:
        payload = self._get(user_id)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ProviderInvalidResponse(_PORT)
        return tuple(self._map(raw, user_id) for raw in items)

    def count_for_user(self, user_id: str) -> int:
        total = self._get(user_id).get("meta", {}).get("total")
        if not isinstance(total, int) or total < 0:
            raise ProviderInvalidResponse(_PORT)
        return total

    def status_for_user(self, user_id: str) -> SourceStatus:
        raw = self._get(user_id).get("meta", {}).get("completeness")
        if raw not in _COMPLETENESS:
            raise ProviderInvalidResponse(_PORT)
        return _COMPLETENESS[raw]
```

### Step 2 — the registry line

In `uc07/composition.py`, inside `INTERACTION_LOG_PROVIDERS`:

```python
    "acme": lambda settings: AcmeInteractionLogProvider(
        base_url=settings.acme_base_url,
        token=settings.acme_token,
        timeout_seconds=settings.provider_timeout_seconds,
    ),
```

(Plus the import, and the two `acme_*` fields on `Settings` if the adapter needs
configuration — configuration belongs to `Settings`, never `os.environ` reads
inside the adapter.)

### Step 3 — the environment variable

```
INTERACTION_LOG_PROVIDER=acme
ACME_BASE_URL=https://<placeholder>
ACME_TOKEN=<placeholder>
```

### Step 4 — join the conformance suite

Add ONE case to `tests/conformance/adapters.py`:

```python
    AdapterCase(
        id="acme",
        user_id="learner-001",
        upstream_tokens=("rephraseCount", "ratingState", "questionText", "Acme"),
        build=lambda: AcmeInteractionLogProvider(...),          # against a stub transport
        build_unavailable=...,
        build_timeout=...,
        build_invalid=...,
        build_empty=...,
    ),
```

Then run:

```
pytest tests/conformance -q          # the suite itself is NOT modified
pytest -q                            # full suite
```

### Step 5 — verify nothing else changed

```
git diff --name-only
```

The expected diff: the new adapter file, `uc07/composition.py`, `Settings`,
`.env`, and one entry in `tests/conformance/adapters.py`. If any of these appear,
stop and reconsider:

* `uc07/domain/**` — domain models or the counting rule
* `uc07/application/**` — services, signals, report assembly
* `uc07/api/**` — routes or schemas
* `uc07/adapters/mock/**` — existing mock adapters
* `uc07/adapters/persistence/**` — persistence
* existing tests

---

## 3. Pre-flight checklist

Before writing any adapter, confirm with the company:

* [ ] Does the interaction endpoint return the learner's **complete** history, or a page/window? (A-07)
* [ ] Are duplicate interaction ids possible on retry? (A-04)
* [ ] Are clarifying/follow-up exchanges separate records, or nested? (A-02, A-03)
* [ ] Exact upstream spelling of NARIC levels, and behaviour for unknown values.
* [ ] How is truncation/partiality signalled, and how is "empty" distinguished from "unavailable"? (A-27, A-28)
* [ ] Are speciality areas drawn from the same vocabulary as `topic_tag`? (A-15)
* [ ] Can the profile return speciality data flagged partial? (A-17)
* [ ] Are course and lesson identifiers globally unique and stable? (A-23)
* [ ] Does the enrolment payload expose completion as an integer 0–100? (A-35)
* [ ] Which upstream statuses mean "retry later" (unavailable) versus "your request was wrong" (invalid)?
* [ ] Confirm the interaction payload has no question text UC-07 could accidentally read; if it does, confirm it is ignored (A-31).

---

## 4. Closing rules

* The **adapter is the ONLY location containing upstream payload knowledge.**
* The **adapter never invents data.** Unsatisfiable payload → typed contract error.
* **Authorization remains server-side.** Identity comes from
  `CurrentUserProvider`; no endpoint accepts a user id.
* **Contract mismatches require a contract discussion, not domain-model hacks.**
