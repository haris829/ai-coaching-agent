# Integration runbook — UC-10 Feedback & Improvement

For an engineer who has never opened this codebase.

**The rule this repository is built to keep:** replacing any mock with a real system costs

1. **one new adapter file**,
2. **one line** in the provider registry (`uc10/adapters/registry.py`),
3. **one environment variable**.

Nothing else changes. No domain model, no application service, no API route, no existing
adapter, no existing test. If your integration needs a fourth change, that is a defect in
this architecture — raise it rather than working around it.

---

## 0. Before you write anything

Read the rows named below in [`docs/assumptions.md`](assumptions.md) and check each against
the real system. Every one of them is a guess we made in your absence.

| Check first | Assumption | What to confirm |
|---|---|---|
| NARIC representation | A-25, A-26 | How the real system expresses an attainment level, and what it sends when it has none. Anything we cannot map becomes `level_5` / `default` / `invalid` — loudly, by design. |
| Response categories | A-05 | The real vocabulary, including whatever the platform calls a degraded fallback. Unmapped values become `unknown`, which is still rateable. |
| Topic tags | A-20 | The real topic vocabulary, and whether tags are stable enough that a 7-day rate over them means anything. |
| Delivery timestamps | §3 below | That the delivery time you return is the platform's authoritative one. The 24-hour rating window is measured against it. |
| Windowed reads | A-11 | That your rating store can serve "non-superseded ratings in a time window, all users" efficiently. |
| Minimum sample size | **A-01** | The real number, against real per-topic volumes. **This one is a policy decision, not an engineering one.** |

---

## 1. Dependency-by-dependency

### 1.1 `InteractionProvider` — the coaching interaction source *(the only external read)*

| | |
|---|---|
| **File to create** | `uc10/adapters/real/<yourname>_interaction_provider.py` |
| **Template to copy** | `uc10/adapters/real/_template.py` |
| **Port to implement** | `uc10.ports.interaction_provider.InteractionProvider` |
| **Signature** | `get(self, interaction_id: str) -> InteractionRecord`<br>`delivered_at(self, interaction_id: str) -> datetime  # tz-aware UTC` |
| **Registry line** | in `uc10/adapters/registry.py`, inside `INTERACTION_PROVIDERS`:<br>`"<yourname>": lambda ctx: YourInteractionProvider(clock=ctx.clock),` |
| **Environment variable** | `INTERACTION_PROVIDER=<yourname>` |
| **Conformance command** | `pytest tests/conformance -q --adapter=<yourname>`<br>with real identifiers: `pytest tests/conformance -q --adapter=<yourname> --conformance-fixtures=fixtures.json` |
| **Assumptions to verify first** | A-05, A-20, A-25, A-26 |
| **Read-only** | This port has no write method and an architecture test fails the build if an adapter adds one. |

### 1.2 `RatingRepository` — where ratings live

| | |
|---|---|
| **File to create** | `uc10/adapters/real/<yourname>_rating_repository.py` |
| **Port** | `uc10.ports.rating_repository.RatingRepository` |
| **Signature** | `save(rating) -> RatingRecord` · `for_interaction(interaction_id) -> list[RatingRecord]` · `supersede(rating_id, by) -> RatingRecord` · `current_in_window(window_start, window_end) -> list[RatingRecord]` |
| **Wiring** | `build_container(ratings_repository=YourRatingRepository(...))` in your process entry point. |
| **Conformance** | `pytest tests/conformance/test_persistence_conformance.py -q` after adding one entry to `RATING_REPOSITORIES` in that file. |
| **Must hold** | Superseded ratings are **retained**, never deleted. `current_in_window` returns only non-superseded ratings, across all users. `supersede` on an unknown id raises `RecordNotFound`. Question, response and comment text are stored — treat the table as containing client-confidential material. |
| **Assumptions to verify** | A-11 (the windowed read), A-06 (comment length in the column type) |

### 1.3 `FlagRepository` — where content review flags live

| | |
|---|---|
| **File to create** | `uc10/adapters/real/<yourname>_flag_repository.py` |
| **Port** | `uc10.ports.flag_repository.FlagRepository` |
| **Signature** | `save(flag)` · `open_flag_for(topic_tag, window) -> ContentReviewFlag \| None` · `update(flag)` · `list_open() -> list[...]` · `get(flag_id)` |
| **Environment / wiring** | `build_container(flag_repository=...)` |
| **Must hold** | `open_flag_for` returns an **open** flag for that topic whose stored window **overlaps** the supplied window (A-03, A-12). Getting or updating an unknown flag raises `RecordNotFound`. |
| **Assumptions to verify** | A-03, A-12, A-13 |

### 1.4 `FlagWorkQueue` — the never-drop guarantee

| | |
|---|---|
| **File to create** | `uc10/adapters/real/<yourname>_flag_work_queue.py` |
| **Port** | `uc10.ports.flag_work_queue.FlagWorkQueue` |
| **Must hold** | An intent is durable from `enqueue` until `resolve`. **Resolve only after the flag repository has confirmed the write.** If this store is not durable, "a flag is never dropped" degrades to "a flag is never dropped while the process lives" — say so to whoever owns the dashboard. |
| **Assumptions to verify** | A-16, A-22 |

### 1.5 `AdminNotificationSink` — telling the platform team

| | |
|---|---|
| **File to create** | `uc10/adapters/real/<yourname>_admin_notification_sink.py` |
| **Port** | `uc10.ports.admin_notification_sink.AdminNotificationSink` |
| **Signature** | `flag_created(self, flag: ContentReviewFlag) -> None` |
| **Must hold** | Called once, on creation, never on update. A failure here must not lose a persisted flag — raise a `PortError`; the service logs it and keeps the flag. **The flag carries no learner content; do not enrich the notification with any.** |

### 1.6 `ThresholdConfigProvider` — the flagging policy

| | |
|---|---|
| **File to create** | `uc10/adapters/real/<yourname>_policy_config.py` (only if policy lives somewhere other than the environment) |
| **Port** | `uc10.ports.threshold_config_provider.ThresholdConfigProvider` |
| **Signature** | `down_rate_threshold() -> float` · `minimum_sample_size() -> int` · `window_days() -> int` · `historical_rating_window_hours() -> int` |
| **Environment variables** | `FLAG_DOWN_RATE_THRESHOLD` · `FLAG_MINIMUM_SAMPLE_SIZE` · `FLAG_WINDOW_DAYS` · `HISTORICAL_RATING_WINDOW_HOURS` |
| **Must hold** | Values are read **at evaluation time**. An administrator changing the threshold must change behaviour with no deploy. |
| **Assumptions to verify** | **A-01**, A-02, A-14 |

### 1.7 `CurrentUserProvider` and `AdminIdentityProvider` — identity

| | |
|---|---|
| **Files to create** | `uc10/adapters/real/<yourname>_identity.py` |
| **Ports** | `uc10.ports.current_user_provider.CurrentUserProvider` (`resolve(request) -> str \| None`) and `AdminIdentityProvider` (`resolve_admin(request) -> str \| None`) |
| **Wiring** | `build_container(current_user=..., admin_identity=...)` |
| **Must hold** | Identity is resolved server-side from the request context, never from a body field. Returning `None` means "anonymous", and anonymous ratings are refused, not stored. **Keep the two ports separate** so no learner credential can produce an admin principal (A-15). Authorisation logic stays inside the adapter. |
| **Assumptions to verify** | A-27, A-28 |

### 1.8 `Clock`

Implement `now() -> datetime` returning tz-aware UTC. Pass via `build_container(clock=...)`.
Every window in this component is computed from it.

---

## 2. Worked example — integrating a real interaction source

Everything an engineer at the company would actually produce, end to end.

### Step 1 — the one new file

`uc10/adapters/real/company_interaction_provider.py`, copied from `_template.py` with the
TODOs filled in. Their fictional upstream returns:

```json
{
  "id": "ix_88213",
  "conversation": { "id": "cv_5510", "learner": { "id": "usr_204" } },
  "turn": { "prompt": "What is the limitation period…", "answer": "…", "kind": "ANSWER" },
  "labels": { "topic": "Limitation Periods", "mode": "Coaching" },
  "attainment": { "framework": "RQF", "level": "Level 7 (Extended)", "origin": "resolved" },
  "progress": { "percentComplete": 62 },
  "servedAt": "2026-06-01T10:59:00Z",
  "health": "complete"
}
```

```python
"""InteractionProvider for the company's coaching service.

The ONLY place the company's payload shape is known.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from uc10.domain.enums import NaricLevelSource, ResponseCategory, SourceStatus
from uc10.domain.models import InteractionRecord
from uc10.domain.naric import normalise_naric_level
from uc10.ports.clock import Clock
from uc10.ports.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    RecordNotFound,
)

PORT_NAME = "InteractionProvider"                       # never a vendor name
REQUEST_TIMEOUT_SECONDS = 5.0                           # TODO(timeout) -> filled in

CATEGORY_BY_UPSTREAM_VALUE = {                          # TODO(mapping) -> filled in
    "ANSWER": ResponseCategory.ANSWER,
    "HANDOFF": ResponseCategory.REDIRECT,
    "REFUSED": ResponseCategory.REFUSAL,
    "FOLLOWUP": ResponseCategory.CLARIFYING_QUESTION,
    "FALLBACK": ResponseCategory.DEGRADED_FALLBACK,
}

NARIC_TOKEN_BY_UPSTREAM_VALUE = {                       # TODO(mapping) -> filled in
    "Level 3": "level_3",
    "Level 4": "level_4",
    "Level 5": "level_5",
    "Level 6": "level_6",
    "Level 7": "level_7",
    "Level 7 (Extended)": "level_7_plus",
}

STATUS_BY_UPSTREAM_VALUE = {                            # TODO(mapping) -> filled in
    "complete": SourceStatus.AVAILABLE,
    "none": SourceStatus.EMPTY,                         # answered, had nothing
    "partial": SourceStatus.PARTIAL,
}


class CompanyInteractionProvider:
    def __init__(self, clock: Clock, base_url: str, service_token: str) -> None:
        self._clock = clock
        # TODO(endpoint)/TODO(auth) -> filled in. Authorisation stays server-side: the
        # service credential is ours, never a token forwarded from the caller's request.
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {service_token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    # ---------------------------------------------------------------- port API

    def get(self, interaction_id: str) -> InteractionRecord:
        return self._to_platform(self._fetch(interaction_id))

    def delivered_at(self, interaction_id: str) -> datetime:
        return self._delivered_at(self._fetch(interaction_id))

    # ------------------------------------------------------- upstream boundary

    def _fetch(self, interaction_id: str) -> dict[str, Any]:
        try:
            response = self._client.get(f"/v2/interactions/{interaction_id}")
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(PORT_NAME, "upstream_timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(PORT_NAME, "upstream_unavailable") from exc
        if response.status_code == 404:
            raise RecordNotFound(PORT_NAME, "interaction_not_found")
        if response.status_code >= 400:
            # Never forward the body: it would carry their error text and field names.
            raise ProviderUnavailable(PORT_NAME, "upstream_unavailable")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_response") from exc

    def _delivered_at(self, raw: dict[str, Any]) -> datetime:
        try:
            return datetime.fromisoformat(
                str(raw["servedAt"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (KeyError, ValueError) as exc:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_response") from exc

    def _to_platform(self, raw: dict[str, Any]) -> InteractionRecord:
        try:
            attainment = raw.get("attainment", {})
            naric = normalise_naric_level(
                NARIC_TOKEN_BY_UPSTREAM_VALUE.get(str(attainment.get("level")))
            )
            return InteractionRecord(
                interaction_id=str(raw["id"]),
                session_id=str(raw["conversation"]["id"]),
                user_id=str(raw["conversation"]["learner"]["id"]),
                question_text=str(raw["turn"]["prompt"]),
                response_text=str(raw["turn"]["answer"]),
                response_category=CATEGORY_BY_UPSTREAM_VALUE.get(
                    str(raw["turn"].get("kind")), ResponseCategory.UNKNOWN
                ),
                topic_tag=str(raw["labels"]["topic"]).strip().lower().replace(" ", "_"),
                session_mode=str(raw["labels"]["mode"]).strip().lower().replace(" ", "_"),
                naric_level=naric.level,
                naric_level_source=(
                    naric.source
                    if attainment.get("origin") == "resolved"
                    else NaricLevelSource.DEFAULT
                ),
                explanation_profile=naric.explanation_profile,
                naric_source_status=naric.status,
                course_completion_percent=self._percent(raw.get("progress", {}).get("percentComplete")),
                delivered_at=self._delivered_at(raw),
                source_status=STATUS_BY_UPSTREAM_VALUE.get(
                    str(raw.get("health")), SourceStatus.INVALID
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_response") from exc

    @staticmethod
    def _percent(value: Any) -> int | None:
        """Integer 0-100 or None. Never a guess."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if 0 <= parsed <= 100 else None
```

### Step 2 — the one registry line

```diff
--- a/uc10/adapters/registry.py
+++ b/uc10/adapters/registry.py
@@
+from uc10.adapters.real.company_interaction_provider import CompanyInteractionProvider
 from uc10.adapters.foreign.interaction_provider import ForeignInteractionProvider
 from uc10.adapters.mock.interaction_provider import MockInteractionProvider
@@
 INTERACTION_PROVIDERS: dict[str, InteractionProviderFactory] = {
     "mock": lambda ctx: MockInteractionProvider(clock=ctx.clock),
     "foreign_demo": lambda ctx: ForeignInteractionProvider(clock=ctx.clock),
+    "company": lambda ctx: CompanyInteractionProvider(
+        clock=ctx.clock,
+        base_url=os.environ["COMPANY_COACHING_BASE_URL"],
+        service_token=os.environ["COMPANY_COACHING_TOKEN"],
+    ),
 }
```

### Step 3 — the one configuration change

```diff
--- a/.env
+++ b/.env
-INTERACTION_PROVIDER=mock
+INTERACTION_PROVIDER=company
```

(Plus the credentials the adapter itself reads — they belong to the adapter, not to this
component's contract.)

### Step 4 — prove it

```bash
# 1. The service refuses to start on a name nobody registered, and never falls back:
INTERACTION_PROVIDER=typo python -c "from uc10.api.app import create_app; create_app()"

# 2. Contract conformance for the new adapter -- no new test written:
pytest tests/conformance -q --adapter=company

# 3. With real identifiers, the scenario-driven contracts run too:
cat > fixtures.json <<'JSON'
{
  "ok": "ix_88213",
  "recent": "ix_88301",
  "stale": "ix_87004",
  "unavailable": "ix_deadbeef",
  "invalid": "ix_malformed",
  "unmapped_level": "ix_88999",
  "forbidden_tokens": ["servedAt", "percentComplete", "conversation", "company"]
}
JSON
pytest tests/conformance -q --adapter=company --conformance-fixtures=fixtures.json

# 4. Nothing else changed -- the whole suite still passes:
pytest -q
```

That is the entire integration: **one file, one line, one variable.**

---

## 3. Non-negotiables

* **The adapter is the only place upstream payload shapes are known.** No upstream field
  name, nesting or error string escapes it. The conformance suite asserts this: give it your
  upstream's vocabulary in `forbidden_tokens` and it will fail if any of it crosses the
  boundary.
* **The adapter never invents data.** A missing value maps to the documented default with its
  source field marked accordingly — `naric_level_source="default"`,
  `naric_source_status="empty"` or `"invalid"` — never to a plausible-looking guess. If you
  find yourself writing "it's probably level 6", stop.
* **`empty` is not `unavailable`.** The upstream answering with nothing and the upstream
  being unreachable are different states and the platform reads them differently.
* **Authorisation stays server-side, inside the adapter.** The service credential is the
  component's own. Never forward a caller-supplied token upstream.
* **Timeouts are the adapter's job.** A slow upstream must become `ProviderTimeout`, not a
  hung request; the conformance suite times it.
* **The interaction port is read-only.** Do not add a method that writes, corrects or
  annotates an interaction. The build fails if you do.
* **Learner content never leaves the store.** Do not log it, do not attach it to a flag, do
  not put it in an error message, do not enrich a notification with it.
* **If the real payload cannot be mapped to the platform contract, that is a contract
  conversation, not an adapter workaround.** Do not bend a domain model to fit an upstream
  quirk, do not add an enum member to accommodate one system, and do not smuggle an extra
  field through in a slug. Raise it.
