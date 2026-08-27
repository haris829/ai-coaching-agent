# UC-06 integration runbook

For an engineer who has never opened this codebase.

Integrating a real upstream system costs exactly three things:

1. **One new adapter file** — copy `uc06/adapters/real/_template.py`.
2. **One line** in `PROVIDER_REGISTRY` in `uc06/composition.py`.
3. **One environment variable.**

**Nothing else changes.** Not the domain models, not the application services,
not the API layer, not the existing adapters, not persistence, and not one
existing test. If your integration needs a fourth change, the architecture has
failed and that is a bug to raise here — not something to work around in your
adapter.

Proof rather than assertion: `tests/test_integration_swap.py` runs the
unmodified service against a deliberately foreign adapter family whose fictional
upstream uses different field names, different nesting, a different value
representation and a different marker syntax, and asserts identical behaviour.

---

## Before you write any adapter

Read the rows of `docs/assumptions.md` listed for your dependency below and check
each one against the real system. They are the places where we guessed. Finding
that a guess is wrong before you write the mapping is cheap; finding out after is
not.

Then run the baseline so you know the suite was green before you touched it:

```bash
python -m pytest -q
```

---

## Per-dependency instructions

### 1. Case file system (Case Prep Agent)

| | |
|---|---|
| **File to create** | `uc06/adapters/real/<yoursystem>_case_file.py` |
| **Template to copy** | `uc06/adapters/real/_template.py` → `TemplateCaseFileAdapter` |
| **Port to implement** | `uc06/ports/case_file.py::CaseFileProvider` |
| **Signatures** | `verify_read_access(self, user_id: str, case_file_id: str) -> AccessRecord`<br>`get_case_file(self, case_file_id: str) -> CaseFile` |
| **Registry line** | In `uc06/composition.py`, under `"case_file_provider"`:<br>`"<name>": "uc06.adapters.real.<yourfile>:<YourClass>",` |
| **Environment variable** | `CASE_FILE_PROVIDER=<name>` |
| **Conformance command** | `python -m pytest tests/conformance/test_case_file_provider_conformance.py -q --adapter-family=<name>` |
| **Assumptions to check first** | **A-02** case file shape · **A-03** fact identifier scheme (the critical one) · **A-05** how "originated from the Case Prep Agent" is really established · **A-13** whether fact text may be re-served to an authorised reader |

Declare a module-level `CONFORMANCE_SCENARIOS` dict **in your own adapter file**,
naming the identifier in **your** system for each contract case (`readable`,
`partial`, `empty`, `access_denied`, `foreign_origin`, `unavailable`, `invalid`,
`timeout`). The conformance kit reads it from your module, so pointing the kit at
your adapter edits **no test file at all**. An adapter that omits the declaration
fails with a message naming exactly what to add — it is never silently skipped.

**Read-only is structural.** Do not add a create, update, delete, patch or write
method. `tests/test_readonly_architecture.py` walks the registry and will fail
the build for any registered adapter that has one.

### 2. Learner context

| | |
|---|---|
| **File to create** | `uc06/adapters/real/<yoursystem>_learner_context.py` |
| **Template to copy** | `uc06/adapters/real/_template.py` → `TemplateLearnerContextAdapter` |
| **Port to implement** | `uc06/ports/learner_context.py::LearnerContextProvider` |
| **Signature** | `get_context(self, session_id: str, user_id: str) -> LearnerContext` |
| **Registry line** | Under `"learner_context_provider"`: `"<name>": "uc06.adapters.real.<yourfile>:<YourClass>",` |
| **Environment variable** | `LEARNER_CONTEXT_PROVIDER=<name>` |
| **Conformance command** | `python -m pytest tests/conformance/test_learner_context_provider_conformance.py -q --adapter-family=<name>` |
| **Assumptions to check first** | **A-07** levels 4 and 6 grouping · **A-09** where the case-file selection lives · **A-10** what to do when context is unavailable |

The load-bearing rule: map your system's attainment representation onto the
platform `NaricLevel` enum, one line per upstream value, and raise
`ProviderInvalidResponse` for anything unmapped. Never round to a neighbour and
never substitute the default — the service applies the default itself and marks
`naric_level_source="default"`, and an adapter that quietly substitutes a level
makes that field a lie.

Declare `CONFORMANCE_SCENARIOS` in your adapter module for this port too, with
keys `available`, `unavailable`, `timeout`, `unmappable_level` and
`no_practice_area`.

### 3. Answer generator (a real model provider)

> **STOP. This one needs a confidentiality sign-off before it is enabled.** See
> the section at the end of this document. `ConfiguredAnswerGenerator` refuses to
> construct until a constant in its own source file is changed, so it cannot be
> switched on by configuration alone.

| | |
|---|---|
| **File to create** | `uc06/adapters/real/<provider>_generator.py` (or complete `configured_generator.py`) |
| **Port to implement** | `uc06/ports/generator.py::AnswerGenerator` |
| **Signature** | `generate(self, request: GenerationRequest) -> GenerationResult` |
| **Registry line** | Under `"answer_generator"`: `"<name>": "uc06.adapters.real.<yourfile>:<YourClass>",` |
| **Environment variable** | `ANSWER_GENERATOR=<name>` |
| **Conformance command** | `python -m pytest tests/conformance/test_answer_generator_conformance.py -q --adapter-family=<name>` |
| **Assumptions to check first** | **A-03** fact identifiers (the marker contract depends on them) · **A-16** timeout enforcement |

Your adapter must: honour `request.timeout_ms`; take prompts **only** from
`request.system_instructions`; emit fact references as `[[fact:IDENTIFIER]]`
using only identifiers from `request.available_fact_ids`; and never write a
disclaimer. Anything disclaimer-shaped the model returns goes in
`GenerationResult.supplied_disclaimer`, where the service discards it.

### 4. Guard classifier

| | |
|---|---|
| **Port** | `uc06/ports/guard.py::GuardClassifier` — `classify(self, question: str) -> GuardResult` |
| **Registry line** | Under `"guard_classifier"` |
| **Environment variable** | `GUARD_CLASSIFIER=<name>` |
| **Conformance command** | `python -m pytest tests/conformance/test_guard_classifier_conformance.py -q --adapter-family=<name>` |
| **Assumptions to check first** | **A-04** the phrase sets |

The conformance bar is the in-domain rule set's own behaviour: a classifier that
catches less than the fallback is not an upgrade. On failure, **raise** — never
return `none` — so the service falls back to the in-domain rules. The guard is
never skipped because a provider is down.

### 5. Interaction log and halt store (durable persistence)

| | |
|---|---|
| **Ports** | `uc06/ports/storage.py::InteractionLogRepository`, `SessionHaltRepository` |
| **Registry lines** | Under `"interaction_log_repository"` / `"session_halt_repository"` |
| **Environment variables** | `INTERACTION_LOG_REPOSITORY=<name>` / `SESSION_HALT_REPOSITORY=<name>` |
| **Assumptions to check first** | **A-06** halt clearing and who is authorised · **A-17** rating state · **A-18** audit destination |

The interaction record has no `question_text` field. Do not add a column for one.

### 6. Alerting and security incidents

| | |
|---|---|
| **Ports** | `uc06/ports/sinks.py::AdminAlertSink`, `SecurityIncidentSink` |
| **Registry lines** | Under `"admin_alert_sink"` / `"security_incident_sink"` |
| **Environment variables** | `ADMIN_ALERT_SINK=<name>` / `SECURITY_INCIDENT_SINK=<name>` |
| **Assumptions to check first** | **A-12** what a security incident may contain |

Keep these two destinations distinct. A suppression attempt is a security event
with its own review path, not a warning line in request logs. `critical()` must
reach a human immediately — it fires when a case-linked response was withheld and
a session halted.

### 7. Authentication

| | |
|---|---|
| **Port** | `uc06/ports/identity.py::CurrentUserProvider` — `resolve(self, headers: Mapping[str, str]) -> str` |
| **Registry line** | Under `"current_user_provider"` |
| **Environment variable** | `CURRENT_USER_PROVIDER=<name>` |
| **Assumptions to check first** | **A-15** — the shipped adapter trusts a header. **This must be replaced before any deployment that touches real case files.** |

`resolve` receives headers only. The request **body** is never passed and must
never be consulted: `user_id` is never client-asserted content.

---

## Worked example: swapping the case file provider

Everything a company engineer actually produces, in full.

### Step 1 — check the assumptions first

A-03 says every fact must carry a stable, unique identifier. Suppose the real
system returns matters like this:

```json
{
  "matter": {
    "reference": "LX-88213",
    "sourceApplication": "case-prep-agent-2",
    "practice": { "code": "CRIM" },
    "allegations": [ { "id": "AL1", "text": "Robbery", "act": "Theft Act 1968 s.8" } ],
    "factSheet": [ { "factKey": "FS-1", "statement": "…", "type": "client-account" } ],
    "exhibitList": [ { "exhibitId": "EX-1", "title": "Gate camera export", "factKeys": ["FS-1"] } ],
    "legalNotes": [ { "noteId": "LN-1", "reference": "Theft Act 1968 s.8", "note": "Robbery." } ]
  }
}
```

`factKey` is stable and unique, so A-03 holds. `sourceApplication` is the origin
signal, so A-05 is answered by an allow-list — and if it turns out that field is
writable by any importer, that is a contract conversation, not a mapping choice.

### Step 2 — the new file

`uc06/adapters/real/lexos_case_file.py`:

```python
"""LexOS case file adapter. The only place LexOS payload shapes are known."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from ...config import Settings
from ...domain.enums import SourceStatus
from ...domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from ...domain.models import (
    CASE_PREP_AGENT_ORIGIN,
    AccessRecord,
    CaseFact,
    CaseFile,
    Charge,
    EvidenceItem,
    LegislationNote,
)

PORT_NAME = "case_file_provider"

# A-05: confirmed with the LexOS team - these producer values, and no others,
# identify a case file produced by the Case Prep Agent.
CASE_PREP_PRODUCERS = frozenset({"case-prep-agent-2", "case-prep-agent-3"})


class LexOsCaseFileAdapter:
    """Implements CaseFileProvider. READ ONLY - no mutating method here, ever."""

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.generation_timeout_ms / 1000
        self._client = httpx.Client(
            base_url=settings.case_file_base_url,          # add the key to Settings + ENV_KEYS
            headers={"Authorization": f"Bearer {settings.case_file_token}"},
            timeout=self._timeout,
        )

    def verify_read_access(self, user_id: str, case_file_id: str) -> AccessRecord:
        raw = self._get(f"/matters/{case_file_id}/access", {"actor": user_id})
        verdict = raw.get("accessDecision")
        if verdict not in {"ALLOW", "DENY"}:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_access_decision")
        granted = verdict == "ALLOW"
        return AccessRecord(
            user_id=user_id,
            case_file_id=case_file_id,
            granted=granted,
            checked_at=datetime.now(timezone.utc),
            reason_code="ok" if granted else "not_on_matter",
        )

    def get_case_file(self, case_file_id: str) -> CaseFile:
        matter = self._get(f"/matters/{case_file_id}").get("matter")
        if not isinstance(matter, dict):
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_case_payload")
        try:
            facts = tuple(
                CaseFact(str(f["factKey"]), str(f["statement"]), str(f.get("type", "general")))
                for f in matter.get("factSheet", [])
            )
            charges = tuple(
                Charge(str(a["id"]), str(a["text"]), a.get("act"))
                for a in matter.get("allegations", [])
            )
            evidence = tuple(
                EvidenceItem(
                    str(e["exhibitId"]),
                    str(e["title"]),
                    tuple(str(k) for k in e.get("factKeys", [])),
                )
                for e in matter.get("exhibitList", [])
            )
            notes = tuple(
                LegislationNote(str(n["noteId"]), str(n["reference"]), str(n.get("note", "")))
                for n in matter.get("legalNotes", [])
            )
        except (KeyError, TypeError, ValueError) as exc:
            # No payload, no field values, no upstream error text in the message.
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_case_payload") from exc

        producer = str(matter.get("sourceApplication", ""))
        origin = CASE_PREP_AGENT_ORIGIN if producer in CASE_PREP_PRODUCERS else "unknown"

        if not facts and not charges:
            status = SourceStatus.EMPTY          # answered, and legitimately empty
        elif not notes:
            status = SourceStatus.PARTIAL
        else:
            status = SourceStatus.AVAILABLE

        return CaseFile(
            case_file_id=case_file_id,
            origin_system=origin,
            practice_area=str(matter.get("practice", {}).get("code", "unknown")).lower(),
            charges=charges,
            facts=facts,
            evidence=evidence,
            legislation_notes=notes,
            source_status=status,
        )

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(PORT_NAME, "case_read_timeout") from exc
        except (httpx.HTTPError, ValueError) as exc:
            # httpx errors quote the response body, and the body is case content.
            raise ProviderUnavailable(PORT_NAME, "case_service_unreachable") from exc
```

### Step 3 — the one registry line

In `uc06/composition.py`:

```diff
     "case_file_provider": {
         "mock": "uc06.adapters.mock.case_file:MockCaseFileProvider",
         "foreign": "uc06.adapters.foreign.case_file:ForeignCaseFileAdapter",
+        "lexos": "uc06.adapters.real.lexos_case_file:LexOsCaseFileAdapter",
     },
```

No import statement: entries are dotted paths, resolved lazily.

### Step 4 — the one config value

```diff
-CASE_FILE_PROVIDER=mock
+CASE_FILE_PROVIDER=lexos
```

(This example's adapter also reads `case_file_base_url` and `case_file_token`.
New credentials mean adding those two keys to `Settings` and `ENV_KEYS` in
`uc06/config.py` — a declaration of what the adapter reads, not a change to any
behaviour. Never hard-code a URL, key or timeout in the adapter.)

### Step 5 — the scenario map, then run the kit

In `tests/conformance/conftest.py`, add:

```python
    "lexos": {
        "readable": "LX-88213",
        "partial": "LX-88214",
        "empty": "LX-88215",
        "access_denied": "LX-88216",
        "foreign_origin": "LX-88217",
        "unavailable": "LX-00000",
        "invalid": "LX-99999",
        "timeout": "LX-88299",
    },
```

```bash
python -m pytest tests/conformance/test_case_file_provider_conformance.py -q --adapter-family=lexos
```

One command tells you whether the integration is correct. **No new test needs
writing.** If it passes, the unmodified service works against your system with
every safety control intact.

### What was touched

| File | Change |
|---|---|
| `uc06/adapters/real/lexos_case_file.py` | new |
| `uc06/composition.py` | +1 line |
| `.env` / deployment config | 1 value |
| `uc06/config.py` | 2 declarations, only because this upstream needs credentials |

Zero changes to domain models, application services, the API layer, existing
adapters, persistence, **or any test file** — the conformance kit reads your
scenario map out of your own adapter module.

This was performed and verified, not asserted: see the **Integration Swap Proof**
section of the final report, where a third adapter over a third unrelated payload
shape was registered and the whole suite re-run. Exactly two files differed by
content hash — the new adapter and `uc06/composition.py` — and the test count rose
because the read-only architecture test and the conformance kit both discovered
the new adapter through the registry on their own.

---

## Confidentiality: what would be transmitted to a real model provider

**This requires the company's explicit written sign-off before any real provider
is enabled. It is not an engineering default.**

Case files contain confidential and potentially privileged client information.
Enabling `ConfiguredAnswerGenerator` transmits that information to a third party.

### Exactly what leaves the process on each call

| Field | Content |
|---|---|
| `question_text` | The learner's question, verbatim — which may itself describe the matter. |
| `system_instructions` | The server-side prompt from the versioned registry. |
| `available_fact_ids` | Every fact identifier in the case file. |
| `fact_digest` | **The full TEXT of every fact in the case file.** This is the material point. |
| `charges` | Charge labels from the case file. |
| `legislation` | Legislation citations noted on the case file. |
| `practice_area` | The practice area. |
| `profile` | The explanation profile (`basic` / `intermediate` / `advanced`). |
| `case_file_id` | The case file identifier. |

**Not transmitted:** `user_id`, `session_id`, evidence items, interaction
history, audit records. The `GenerationRequest` type has no `user_id` or
`session_id` field at all, so an adapter cannot transmit one by accident.

`tests/test_confidentiality_flag.py` asserts this list matches the request object
and matches this document, so the documentation cannot drift from what the code
would actually send.

### What the company must confirm in writing before this is enabled

1. The provider, the model, and the **region** the data is processed in.
2. Whether the provider trains on, retains, caches or logs submitted content —
   and if it retains, for how long, and who at the provider can read it.
3. That transmission is compatible with the client retainer and with legal
   professional privilege **for every matter type that can reach this
   component** — not for the common case.
4. Who owns the decision for a matter where privilege has not been waived, and
   what happens to case files where it never will be.
5. Whether a data processing agreement is in place and covers this use.
6. Whether the answer differs by matter type, client, or jurisdiction — and if it
   does, how UC-06 is supposed to know which is which, because today it cannot.

### How the gate works

`CONFIDENTIALITY_SIGN_OFF_RECORDED = False` in
`uc06/adapters/real/configured_generator.py`. While it is `False` the adapter
raises on construction, so `ANSWER_GENERATOR=configured` fails at startup with a
message pointing here.

It is deliberately **not** an environment variable. Enabling third-party
transmission of privileged material should require a code change and a review,
not a deploy-time string — and there is no configuration key that can do it.

---

## The non-negotiables

- **The adapter is the only place upstream payload shapes are known.** No
  upstream field name, nesting or error string escapes it. Upstream error text
  quotes payloads, and payloads carry case content.
- **The adapter never invents data.** A missing value maps to the documented
  default with its source marked accordingly — never to a plausible-looking
  guess. A value matching no enum member is `ProviderInvalidResponse`, not a
  rounded-down neighbour.
- **Authorisation stays server-side, inside the adapter.** Never accept an access
  decision, an identity or a role from a request body. Never cache an access
  decision: authorisation can be revoked between two questions in one session.
- **If the real payload cannot be mapped to the platform contract, that is a
  contract conversation, not an adapter workaround.** Raise it. Do not bend the
  domain model to fit an upstream quirk, and do not synthesise a fact identifier
  to satisfy a check — a synthesised identifier makes fact verification
  meaningless while appearing to work.
- **Never fall back to a mock.** An unregistered provider name fails loudly at
  startup, naming the port, the value, the registry file and the template. A
  service quietly running on fake data in production is worse than one that
  refuses to start.
