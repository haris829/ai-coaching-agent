# Integration runbook — UC-09 Session Summary & Export

**Audience:** an engineer who has never opened this codebase and needs to point
it at a real system.

Integrating one dependency costs exactly three things:

1. **one new adapter file** — the payload mapping, which only you can write;
2. **one line** in the provider registry;
3. **one environment variable**.

Nothing else changes. No domain model, no application service, no API code, no
existing adapter, no persistence, and **no existing test**. If your integration
needs a fourth change, that is a defect in this repository — raise it rather
than working around it.

---

## Before you start

```bash
python -m pip install -e ".[test]"
python -m pytest          # 555 tests, no API key, no network
```

A green run before you change anything is your baseline.

---

## The three points, per dependency

| Dependency | Port interface | Method signature | Adapter file to create | Registry key | Environment variable |
|---|---|---|---|---|---|
| Session | `uc09_summary/ports/session_provider.py` | `get_session(session_id: str) -> SessionRecord` | `uc09_summary/adapters/real/<vendor>_session.py` | `REGISTRY["session_provider"]["<name>"]` | `UC09_SESSION_PROVIDER=<name>` |
| Interactions | `ports/interaction_provider.py` | `for_session(session_id: str) -> tuple[InteractionRecord, ...]` | `adapters/real/<vendor>_interaction.py` | `REGISTRY["interaction_provider"]["<name>"]` | `UC09_INTERACTION_PROVIDER=<name>` |
| Citations | `ports/citation_provider.py` | `for_session(session_id: str) -> tuple[Resource, ...]` | `adapters/real/<vendor>_citation.py` | `REGISTRY["citation_provider"]["<name>"]` | `UC09_CITATION_PROVIDER=<name>` |
| Gap report | `ports/gap_report_provider.py` | `suggestions(user_id: str) -> tuple[Suggestion, ...] \| None` | `adapters/real/<vendor>_gap_report.py` | `REGISTRY["gap_report_provider"]["<name>"]` | `UC09_GAP_REPORT_PROVIDER=<name>` |
| Summary generation | `ports/summary_generator.py` | `generate(session_data: SessionData) -> SummaryContent` | `adapters/real/<vendor>_generator.py` | `REGISTRY["summary_generator"]["<name>"]` | `UC09_SUMMARY_GENERATOR=<name>` |
| PDF rendering | `ports/document_renderer.py` | `html_to_pdf(html: str) -> bytes` | `adapters/real/<vendor>_renderer.py` | `REGISTRY["document_renderer"]["<name>"]` | `UC09_DOCUMENT_RENDERER=<name>` |
| Summary storage | `ports/repositories.py` | `save` / `get` / `for_session` | `adapters/real/<vendor>_summary_repository.py` | `REGISTRY["summary_repository"]["<name>"]` | `UC09_SUMMARY_REPOSITORY=<name>` |
| Download log | `ports/repositories.py` | `record` / `for_session` / `for_summary` | `adapters/real/<vendor>_download_log.py` | `REGISTRY["download_log_repository"]["<name>"]` | `UC09_DOWNLOAD_LOG_REPOSITORY=<name>` |
| Identity | `ports/identity.py` | `resolve(request) -> str` | `adapters/real/<vendor>_identity.py` | `REGISTRY["current_user_provider"]["<name>"]` | `UC09_CURRENT_USER_PROVIDER=<name>` |

The template to copy is always the same file:

```bash
cp uc09_summary/adapters/real/_template.py \
   uc09_summary/adapters/real/<vendor>_<port>.py
```

It shows a `SessionProvider`. The other ports differ only in the method name
and return type; the five TODO markers are identical in each.

### Conformance commands

```bash
# One port
python -m pytest tests/conformance/test_session_provider_conformance.py

# Only your adapter, across every port (what you want in your environment)
UC09_CONFORMANCE_ONLY=<name> python -m pytest tests/conformance

# Everything
python -m pytest tests/conformance
```

The conformance suite is **adapter-agnostic and registry-driven**. Your one
registry line enrols your adapter automatically. **You do not write a test to
validate a real adapter.**

### Assumptions to verify per dependency

Read these rows in `assumptions.md` and confirm each against the real system
**before** writing the adapter.

| Dependency | Verify first |
|---|---|
| Session | **A-005** (`user_display_name` on the session payload), A-006, A-007, A-001, A-003 |
| Interactions | **A-009** (topic *and concept* tags exist and are stable identifiers), A-010 |
| Citations | **A-011** (per-interaction citation links exist), A-012, A-013 |
| Gap report | **A-014** (the source can distinguish "no report" from "no suggestions"), A-015, A-016 |
| Summary generation | A-016, A-018, A-019, A-024 |
| PDF rendering | A-036, A-037, A-038 |
| Identity | **A-032** (the header adapter is not authentication) |
| All | A-041 (neutral error details), A-043 (timeout budget) |

**A-009 and A-011 are the two that can invalidate the design.** If the real
interaction log has no concept tagging, Key Concepts cannot be grounded. If the
citation source cannot say which interaction an authority was cited in, "cited
during this session" cannot be verified. Either is a **contract conversation**,
not something to patch inside an adapter.

---

## Worked example: the session provider

This example is **shipped and runnable**. The adapter below exists at
`uc09_summary/adapters/real/larrycore_session.py`, is registered under
`larrycore`, and passes the conformance suite:

```bash
UC09_CONFORMANCE_ONLY=larrycore   python -m pytest tests/conformance/test_session_provider_conformance.py
# 11 passed
```

Its upstream is a hermetic stub so the example runs without a service; in a
real deployment `from_settings` builds an HTTP client from configuration, as
shown below. Delete the file and its registry line once you have your own.

Suppose the company delivers **Larry Core**, reachable at
`https://core.internal.example/api`, authenticated with a bearer token. It
returns:

```json
{
  "data": {
    "sessionRef": "SESS-99201",
    "student": { "id": "USR-4471", "displayName": "Amara Osei" },
    "openedAt": "2026-03-04T09:00:00Z",
    "closedAt": "2026-03-04T09:47:00Z",
    "state": "CLOSED",
    "qualificationLevel": "NQF7",
    "courseProgressPct": 62,
    "courseName": "Employment Law Practice"
  }
}
```

### Step 0 — verify the assumptions

- **A-005** — `student.displayName` is present. Good; no user-directory port needed.
- **A-006** — `openedAt` / `closedAt` are ISO with a `Z`. `closedAt` may be
  `null` on a live session — confirmed with the Core team.
- **A-007** — states are `OPEN`, `CLOSED`, `DROPPED`.
- **A-001 / A-003** — levels are `NQF3`…`NQF7`, plus `NQF7D` for doctoral.
  `NQF7D` maps to `level_7_plus`. Anything else must default; do **not** guess.

### Step 1 — the one new file

`uc09_summary/adapters/real/larrycore_session.py`:

```python
"""Larry Core SessionProvider. READ ONLY.

Every Larry Core detail stops here: the ``data`` envelope, ``sessionRef``,
``qualificationLevel``, ``NQF7``, ``CLOSED``, and the ``httpx`` error types
with their hostnames and status codes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from uc09_summary.config import Settings
from uc09_summary.domain.enums import SessionStatus
from uc09_summary.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    SessionNotFound,
)
from uc09_summary.domain.models import SessionRecord
from uc09_summary.domain.naric import resolve_naric_level

PORT = "session_provider"

# TODO(1) ENDPOINT -> done
SESSION_PATH = "/v2/sessions/{session_id}"

# TODO(2) VALUE MAPPINGS -> done. Anything absent falls through to the
# documented default with status `invalid`. Never to a near miss.
LEVEL_MAP = {
    "NQF3": "level_3",
    "NQF4": "level_4",
    "NQF5": "level_5",
    "NQF6": "level_6",
    "NQF7": "level_7",
    "NQF7D": "level_7_plus",
}
STATE_MAP = {
    "OPEN": SessionStatus.IN_PROGRESS,
    "CLOSED": SessionStatus.COMPLETED,
    "DROPPED": SessionStatus.ABANDONED,
}


class LarryCoreSessionProvider:
    """Maps a Larry Core session payload onto the platform SessionRecord."""

    @classmethod
    def from_settings(cls, settings: Settings) -> LarryCoreSessionProvider:
        # TODO(3) AUTH -> done. Authorisation stays server-side, in here.
        client = httpx.Client(
            base_url=settings.upstream_base_url,
            headers={"Authorization": f"Bearer {settings.upstream_api_key}"},
            timeout=settings.provider_timeout_seconds,
        )
        return cls(client)

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get_session(self, session_id: str) -> SessionRecord:
        # TODO(4) ERROR TRANSLATION -> done. Details are neutral machine
        # codes: they reach logs, and provider identity must not.
        try:
            response = self._client.get(SESSION_PATH.format(session_id=session_id))
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(PORT, "upstream_deadline_exceeded") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(PORT, "upstream_transport_error") from exc

        if response.status_code == 404:
            raise SessionNotFound(session_id)
        if response.status_code >= 500:
            raise ProviderUnavailable(PORT, "upstream_error_response")
        if response.status_code != 200:
            raise ProviderInvalidResponse(PORT, "upstream_unexpected_status")

        return self._to_record(response.json())

    def _to_record(self, envelope: dict[str, Any]) -> SessionRecord:
        # TODO(5) PAYLOAD MAPPING -> done.
        try:
            data = envelope["data"]
            student = data["student"]
            level = resolve_naric_level(
                LEVEL_MAP.get(str(data.get("qualificationLevel", "")))
                or data.get("qualificationLevel"),
                port=PORT,
            )
            return SessionRecord(
                session_id=str(data["sessionRef"]),
                user_id=str(student["id"]),
                user_display_name=str(student["displayName"]),
                started_at=_utc(data["openedAt"]),
                ended_at=_utc(data.get("closedAt")),
                status=STATE_MAP.get(
                    str(data.get("state", "")), SessionStatus.IN_PROGRESS
                ),
                naric_level=level.level,
                naric_level_source=level.source,
                naric_level_status=level.status,
                course_completion_percent=int(data.get("courseProgressPct", 0)),
                course_title=data.get("courseName"),
            )
        except Exception as exc:
            raise ProviderInvalidResponse(PORT, "payload_mapping_failed") from exc

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        """Identifiers in the Core integration environment (CORE-1284)."""
        return {
            "known_id": "SESS-99201",
            "expected_user_id": "USR-4471",
            "missing_id": "SESS-00000",
            "unavailable_id": "SESS-FAULT-503",
            "timeout_id": "SESS-FAULT-SLOW",
            "invalid_naric_id": "SESS-99999",  # qualificationLevel "NQF-UNKNOWN"
            "upstream_tokens": (
                "larrycore", "core.internal.example", "sessionRef",
                "qualificationLevel", "NQF7", "CLOSED", "courseProgressPct",
                "displayName",
            ),
        }


def _utc(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
```

Note what the adapter does **not** do with an unmappable level: it passes the
raw value to `resolve_naric_level`, which applies `LEVEL_5`, marks the source
`default`, records status `invalid` and logs it. It does not pick a nearby
level. A plausible guess about someone's study level is a wrong answer that
looks like a right one.

### Step 2 — the one registry line

In `uc09_summary/registry.py`:

```diff
     "session_provider": {
         "mock": "uc09_summary.adapters.mock.session:MockSessionProvider",
         "foreign": "uc09_summary.adapters.foreign.session:ForeignSessionProvider",
+        "larrycore": "uc09_summary.adapters.real.larrycore_session:LarryCoreSessionProvider",
     },
```

### Step 3 — the one config value

```bash
export UC09_SESSION_PROVIDER=larrycore
export UC09_UPSTREAM_BASE_URL=https://core.internal.example/api
export UC09_UPSTREAM_API_KEY=...        # from your secret store, never committed
```

### Step 4 — run the conformance suite

```bash
UC09_CONFORMANCE_ONLY=larrycore python -m pytest tests/conformance -v
```

You wrote no test. The suite drives your adapter from its
`conformance_profile()` and asserts that:

- it satisfies the port protocol and exposes **no mutating method**;
- it returns a `SessionRecord`, echoing the session id unchanged;
- the NARIC level arrives as the **platform enum**, whatever `NQF7` was;
- an unmappable level becomes `level_5` / `default` / `invalid`;
- completion is an **integer 0–100**;
- timestamps are timezone-aware;
- a missing session raises `SessionNotFound`, a 5xx raises
  `ProviderUnavailable`, a slow response raises `ProviderTimeout`;
- **no upstream token** — not `sessionRef`, not `NQF7`, not
  `core.internal.example` — appears in a returned record or in a raised error,
  including its `detail`.

### Step 5 — confirm nothing else moved

```bash
git status --short
# A  uc09_summary/adapters/real/larrycore_session.py
# M  uc09_summary/registry.py
```

Two files. One is new, one gained a line. If anything else is modified, stop:
the architecture has failed and should be corrected rather than worked around.

> When this exercise was first run against this repository it modified a
> **third** file — and that was treated as a defect, not as an acceptable cost.
> The read-only architecture check flagged the template's own `_to_record`
> helper as a write, because "record" is a mutating verb when it is a verb and
> a noun when it follows "to". The check was corrected
> (`tests/support/readonly.py`) rather than the adapter being renamed to suit
> it. An engineer copying the template today does not hit this.

Then run the whole suite, unchanged:

```bash
python -m pytest
```

---

## Verifying the swap without a real upstream

This repository already ships a **deliberately foreign** adapter family
(`adapters/foreign/`) whose fictional upstream uses different field names,
different nesting, epoch-millisecond timestamps, `RQF-7` instead of `level_7`,
`FINISHED` instead of `completed`, a 0..1 ratio instead of an integer
percentage, and its own error type. The unmodified service runs against it:

```bash
UC09_SESSION_PROVIDER=foreign \
UC09_INTERACTION_PROVIDER=foreign \
UC09_CITATION_PROVIDER=foreign \
UC09_GAP_REPORT_PROVIDER=foreign \
python -m pytest tests/test_foreign_adapter_swap.py -v
```

Read that file before writing your adapter. It is the closest thing to a
specification of what "correct integration" looks like.

---

## Non-negotiables

**The adapter is the only place upstream payload shapes are known.** No
upstream field name, nesting shape or error string escapes it — and that
includes the `detail` on a raised error, because details are written to logs.
The conformance suite checks this against the tokens you declare, so declare
them generously.

**The adapter never invents data.** A missing value maps to the documented
default with its source field marked accordingly — never to a plausible-looking
guess. `resolve_naric_level` exists so that you never have to choose a level
yourself; an unrecognised resource class becomes `other` rather than a guess
between legislation and case law.

**Authorisation stays server-side, inside the adapter.** Credentials come from
settings, never from a literal, never from `os.environ` read directly in the
adapter, and never reach a request path, a response, a log line or a domain
model.

**If the real payload cannot be mapped onto the platform contract, that is a
contract conversation, not an adapter workaround.** Raise it. Do not bend the
domain model to fit an upstream quirk, and do not smuggle the quirk through in
a field meant for something else. The two most likely candidates are A-009 (no
concept tagging on interactions) and A-011 (no per-interaction citation links);
both weaken a guarantee this component makes about a document a regulator may
read, and neither can be repaired downstream.

**A misconfiguration must fail loudly.** Naming a provider with no registered
implementation stops the process at startup, naming the missing key, the
environment variable that selected it, the names that do exist, and the file
expected to supply it. There is no silent fallback to a mock, deliberately: a
service quietly running on fake data in production is worse than one that
refuses to start.
