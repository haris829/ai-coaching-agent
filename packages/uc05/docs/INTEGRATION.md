# UC-05 — Integration runbook

For an engineer who has never opened this codebase.

## The rule this repository is built around

Integrating a real upstream costs exactly three edits:

1. **One new adapter file** — the payload mapping. Unavoidable: only you know
   your shape.
2. **One line** added to `ADAPTER_MODULES` in `uc05/composition.py`.
3. **One environment variable** changed.

**Nothing else may change.** No edits to domain models, application services,
the API layer, existing adapters, persistence, or any existing test. If your
integration needs a fourth edit, that is a defect in this architecture — raise
it rather than working around it.

You write **no new tests**. You write one ~20-line *harness* saying how to drive
your adapter into each documented failure state, and the existing conformance
suite runs against your adapter. Template:
`tests/conformance/_template_harness.py`.

---

## Quick reference — every external dependency

| Dependency | Port interface | Registry symbol | Env var | Conformance command |
|---|---|---|---|---|
| Learner context | `LearnerContextProvider` | `LEARNER_CONTEXT_REGISTRY` | `LEARNER_CONTEXT_PROVIDER` | `python -m pytest tests/conformance/test_learner_context_conformance.py -q` |
| Guiding-question generation | `GuidingQuestionGenerator` | `GUIDING_QUESTION_REGISTRY` | `GENERATOR` | `python -m pytest tests/conformance/test_generator_conformance.py -q -k guiding` |
| Four-part answer generation | `AnswerGenerator` | `ANSWER_REGISTRY` | `GENERATOR` | `python -m pytest tests/conformance/test_generator_conformance.py -q -k answer` |
| Intent classification | `IntentClassifier` | `INTENT_REGISTRY` | `INTENT_CLASSIFIER` | `python -m pytest tests/conformance/test_intent_conformance.py -q` |
| Dialogue store | `DialogueRepository` | `DIALOGUE_REPOSITORY_REGISTRY` | `DIALOGUE_REPOSITORY` | `python -m pytest tests/conformance/test_repository_conformance.py -q -k dialogue` |
| Session store (mode flag) | `SessionModeRepository` | `SESSION_MODE_REPOSITORY_REGISTRY` | `SESSION_MODE_REPOSITORY` | `python -m pytest tests/conformance/test_repository_conformance.py -q -k mode` |
| Interaction log store | `InteractionLogRepository` | `INTERACTION_LOG_REPOSITORY_REGISTRY` | `INTERACTION_LOG_REPOSITORY` | `python -m pytest tests/conformance/test_repository_conformance.py -q -k log` |
| Identity / auth | `CurrentUserProvider` | `CURRENT_USER_REGISTRY` | `CURRENT_USER_PROVIDER` | covered by `tests/test_api.py` identity tests |

`GENERATOR` selects **both** generator implementations. If you have separate
upstreams for guiding questions and answers, register both under the same key.

---

## Per-dependency detail

### 1. Learner context

- **Create:** `uc05/adapters/real/<key>_learner_context.py`
- **Copy from:** `uc05/adapters/real/_template.py` (this port is what the
  template implements)
- **Implement:**
  ```python
  async def get_context(self, session_id: str, user_id: str) -> LearnerContext
  ```
- **Registry line:** `"uc05.adapters.real.<key>_learner_context",` in
  `ADAPTER_MODULES`
- **Env:** `LEARNER_CONTEXT_PROVIDER=<key>`
- **Conformance:** `python -m pytest tests/conformance/test_learner_context_conformance.py -q`
- **Check these assumptions first:** `A-PROFILE-4-6` (do Levels 4 and 6 group
  the way we assumed?), `A-CONTEXT-ONCE` (is one fetch per dialogue acceptable?),
  `A-TOPIC-TAG` (does a real taxonomy exist?)

### 2. Guiding-question generator

- **Create:** `uc05/adapters/real/<key>_guiding_question.py`
- **Implement:**
  ```python
  async def generate(self, dialogue_state: Dialogue, question: str,
                     context: LearnerContext) -> GuidingQuestionResult
  ```
- **Registry line + env:** as above, `GENERATOR=<key>`
- **Conformance:** `python -m pytest tests/conformance/test_generator_conformance.py -q -k guiding`
- **Check these assumptions first:** `A-GQ-RESULT` — can your upstream state
  what each question was *probing*? If not, `probing_focus` degrades to a
  placeholder and the cap's reasoning chain gets thinner. `A-GQ-GUARD` and
  `A-GQ-RESTATEMENT` — your output must survive UC-05's rejection rules; the
  conformance suite runs the guard over your happy-path output for exactly this
  reason.
- **Note:** the system instruction comes from the server-side prompt registry
  (`uc05/application/prompts.py`) and the learner's text is fenced as data. Your
  adapter must not compose a prompt from learner input.

### 3. Answer generator

- **Create:** `uc05/adapters/real/<key>_answer.py`
- **Implement:**
  ```python
  async def generate(self, question: str, context: LearnerContext) -> FourPartAnswer
  ```
- **Conformance:** `python -m pytest tests/conformance/test_generator_conformance.py -q -k answer`
- **Check first:** nothing assumed — the four-part shape is specified. But note
  that **a response missing any part is `ProviderInvalidResponse`, never a
  partial answer.**

### 4. Intent classifier

- **Create:** `uc05/adapters/real/<key>_intent.py`
- **Implement:**
  ```python
  async def classify(self, message: str, dialogue_state: Dialogue) -> IntentResult
  ```
- **Conformance:** `python -m pytest tests/conformance/test_intent_conformance.py -q`
- **Check these assumptions first:** `A-FRUSTRATION-SET`,
  `A-FRUSTRATION-RULE`, `A-CASUAL-SET` — this is the highest-risk area in the
  whole component. Detection must remain **explicit-statement based, not
  sentiment scoring**, and `explicit_frustration` must stay separable from
  `casual_difficulty`. A classifier that scores sentiment and thresholds it
  will rescue learners who were enjoying a hard problem, which is the failure
  the brief specifically warns against. Also `A-INTENT-VOCAB` — you may return
  only the six specified intents; the two extras are optional.
- **Note:** do not add a confidence field. UC-05's contract has no notion of
  one; if confidence must affect behaviour, that is a contract conversation.

### 5. Session store (the mode flag)

- **Create:** `uc05/adapters/real/<key>_session_mode.py`
- **Copy from:** `uc05/adapters/memory/repositories.py::InMemorySessionModeRepository`
- **Implement:**
  ```python
  async def get_mode(self, session_id: str) -> ModeState | None
  async def set_mode(self, session_id: str, enabled: bool, owner_user_id: str) -> ModeState
  ```
- **Env:** `SESSION_MODE_REPOSITORY=<key>`
- **Conformance:** `python -m pytest tests/conformance/test_repository_conformance.py -q -k mode`
- **Check these assumptions first:** `A-MODE-DEFAULT` (is the platform default
  really off?), `A-MODE-OWNER` (if your session store knows the owner, consult
  it and drop UC-05's first-writer heuristic), `A-MODE-STATE` (which of these
  fields does your session record already have?)
- **Critical:** `get_mode` must return `None` for a session it has never seen.
  Do **not** invent a default — the application owns it, so that two
  implementations cannot disagree about what "unset" means.

### 6. Dialogue store and interaction log store

- **Create:** `uc05/adapters/real/<key>_dialogue.py`,
  `uc05/adapters/real/<key>_interaction_log.py`
- **Copy from:** `uc05/adapters/memory/repositories.py`
- **Conformance:** `python -m pytest tests/conformance/test_repository_conformance.py -q`
- **Critical:** store **copies**, not live references. A caller must not be able
  to mutate persisted state without going through the state machine; the
  conformance suite asserts this
  (`test_stored_state_is_isolated_from_the_callers_object`).
- **Note:** the interaction log is append-only and order-preserving. Do not
  change `rating_state` — UC-05 writes `"pending"` and another component owns
  rating.

### 7. Identity

- **Create:** `uc05/adapters/real/<key>_identity.py`
- **Implement:** `async def resolve(self, request: Any) -> str`
- **Env:** `CURRENT_USER_PROVIDER=<key>`
- **Check first:** `A-IDENTITY-HEADER` — the shipped adapter trusts a header
  and is **development only**. Raise `ProviderUnavailable` when identity cannot
  be established; the API turns that into `401`.
- **Critical:** authorisation stays inside the adapter, server-side. `user_id`
  is never read from a request body, and the request schemas reject it.

---

## A second example, already in the repository

`uc05/adapters/local/json_session_mode.py` is a real adapter that was added
**after** the component was finished and every test was passing, specifically to
check that the rule holds. It cost exactly:

- one new file (`uc05/adapters/local/json_session_mode.py`, plus its package
  `__init__.py`);
- one line in `ADAPTER_MODULES`;
- one environment variable (`SESSION_MODE_REPOSITORY=jsonfile`);
- one line in the conformance harness list — a *harness* entry, not a test.

No domain model, service, route, existing adapter or existing test changed, and
the whole suite passed unchanged. Read it alongside the worked example below:
it is shorter, and it is the seam §4 of `SHARED_CONTRACT.md` tells you to use
for the company session store.

## Worked example — swapping in a real learner-context provider

Concrete beats description. This is the whole job, end to end.

### Step 0 — check the assumptions this dependency depends on

From `docs/assumptions.md`: `A-PROFILE-4-6`, `A-CONTEXT-ONCE`, `A-TOPIC-TAG`.
Suppose the real system confirms the profile groupings and one fetch per
dialogue. Good — proceed. If it had contradicted `A-PROFILE-4-6`, the fix would
be one line in `uc05/domain/profiles.py`, which is a **contract conversation**,
not something to paper over in the adapter.

### Step 1 — capture a real payload

```json
{
  "learner": {
    "ref": "LRN-88213",
    "qualification": {
      "framework": "NARIC",
      "band": "6",
      "verified": true
    },
    "practice": { "areas": ["Employment", "Discrimination"] }
  }
}
```

Three things this upstream does that UC-05's contract does not:
`band` is `"6"` (a bare numeral, not `LEVEL_6`); provenance is a boolean
`verified`; practice area is a **list**, where UC-05 takes one value.

### Step 2 — the one new file

`uc05/adapters/real/acmecorp_learner_context.py`:

```python
"""Learner context from the AcmeCorp Learner Profile service.

Owned by the Learner Data team. This file is the only place in UC-05 where
AcmeCorp's payload shape is known.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...config import Settings
from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ...domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ...domain.models import LearnerContext
from ...registry import LEARNER_CONTEXT_REGISTRY

PORT = "learner_context_provider"

#: AcmeCorp's band numerals -> the platform enum. Nothing outside this module
#: knows these strings exist. An unlisted band is INVALID, never a guess.
_BAND_TO_LEVEL: dict[str, NaricLevel] = {
    "3": NaricLevel.LEVEL_3,
    "4": NaricLevel.LEVEL_4,
    "5": NaricLevel.LEVEL_5,
    "6": NaricLevel.LEVEL_6,
    "7": NaricLevel.LEVEL_7,
    "7+": NaricLevel.LEVEL_7_PLUS,
}


@LEARNER_CONTEXT_REGISTRY.register("acmecorp")
class AcmeCorpLearnerContextAdapter:
    def __init__(self, settings: Settings, **_: object) -> None:
        if not settings.learner_context_base_url:
            raise ProviderUnavailable(PORT, "no base url configured")
        self._base_url = settings.learner_context_base_url.rstrip("/")
        self._api_key = settings.learner_context_api_key
        self._timeout = settings.generation_timeout_seconds

    async def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._base_url}/v2/learners/{user_id}/profile",
                    params={"session": session_id},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            # Note: str(exc) is NOT forwarded. Upstream error text stays here.
            raise ProviderTimeout(PORT, "upstream did not answer in budget") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable(PORT, "upstream unreachable") from exc

        return self._map(payload)

    @staticmethod
    def _map(payload: Any) -> LearnerContext:
        """AcmeCorp payload -> platform contract. Unit-testable, no network."""
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(PORT, "unexpected payload type")

        learner = payload.get("learner") or {}
        qualification = learner.get("qualification") or {}
        band = qualification.get("band")

        if band is None:
            level = NaricLevel.LEVEL_5
            source = NaricLevelSource.DEFAULT
            status = SourceStatus.EMPTY
        elif str(band) in _BAND_TO_LEVEL:
            level = _BAND_TO_LEVEL[str(band)]
            verified = bool(qualification.get("verified"))
            source = (
                NaricLevelSource.RETRIEVED if verified else NaricLevelSource.DEFAULT
            )
            status = SourceStatus.AVAILABLE if verified else SourceStatus.PARTIAL
        else:
            # A band mapping to no enum member is an INVALID response, not a
            # level. Do not widen NaricLevel. Do not pick the nearest band.
            level = NaricLevel.LEVEL_5
            source = NaricLevelSource.DEFAULT
            status = SourceStatus.INVALID

        # AcmeCorp sends a list; UC-05's contract takes one value. Taking the
        # first is a mapping decision recorded as PARTIAL, not a silent choice.
        areas = (learner.get("practice") or {}).get("areas") or []
        area = areas[0] if areas and isinstance(areas[0], str) else None
        area_status = (
            SourceStatus.PARTIAL
            if len(areas) > 1
            else (SourceStatus.AVAILABLE if area else SourceStatus.EMPTY)
        )

        return LearnerContext(
            naric_level=level,
            naric_level_source=source,
            practice_area=area,
            source_status={"naric_level": status, "practice_area": area_status},
        )
```

### Step 3 — the one registry line

`uc05/composition.py`:

```diff
 ADAPTER_MODULES: tuple[str, ...] = (
     "uc05.adapters.fake",
     "uc05.adapters.memory",
     "uc05.adapters.real",
     "uc05.adapters.foreign",  # <- an added adapter family looks exactly like this
+    "uc05.adapters.real.acmecorp_learner_context",
 )
```

### Step 4 — the one config change

`.env`:

```diff
-LEARNER_CONTEXT_PROVIDER=mock
+LEARNER_CONTEXT_PROVIDER=acmecorp
+LEARNER_CONTEXT_BASE_URL=https://profiles.internal.example
+LEARNER_CONTEXT_API_KEY=<from the secret store, never committed>
```

### Step 5 — the harness (not a test)

`tests/conformance/acmecorp_harness.py`, copied from `_template_harness.py`:

```python
import httpx

from uc05.adapters.real.acmecorp_learner_context import AcmeCorpLearnerContextAdapter
from uc05.config import load_settings

from .harness import PortHarness

OK = {"learner": {"qualification": {"band": "6", "verified": True},
                  "practice": {"areas": ["Employment"]}}}
BAD_BAND = {"learner": {"qualification": {"band": "9", "verified": True},
                        "practice": {"areas": ["Employment"]}}}
NO_BAND = {"learner": {"qualification": {}, "practice": {"areas": []}}}


def _adapter(handler):
    settings = load_settings(LEARNER_CONTEXT_BASE_URL="https://stub.invalid")
    adapter = AcmeCorpLearnerContextAdapter(settings=settings)
    adapter._client_factory = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    return adapter


def _json(payload):
    return lambda request: httpx.Response(200, json=payload)


ACMECORP_HARNESS = PortHarness(
    name="acmecorp",
    port="learner_context_provider",
    leak_markers=("band", "verified", "AcmeCorp", "LRN-88213", "qualification"),
    happy=lambda: _adapter(_json(OK)),
    unavailable=lambda: _adapter(lambda r: httpx.Response(503)),
    timeout=lambda: _adapter(lambda r: (_ for _ in ()).throw(
        httpx.TimeoutException("x"))),
    malformed=lambda: _adapter(_json(["not", "a", "dict"])),
    invalid_value=lambda: _adapter(_json(BAD_BAND)),
    empty=lambda: _adapter(_json(NO_BAND)),
    slow=None,
    expectations={"level": "LEVEL_6", "practice_area": "Employment"},
)
```

Then one line in `harness.py`:

```diff
 LEARNER_CONTEXT_HARNESSES: tuple[PortHarness, ...] = (
     PortHarness(name="mock", ...),
     PortHarness(name="acme", ...),
+    ACMECORP_HARNESS,
 )
```

*(Injecting a client factory is the one place this example goes slightly beyond
the template: give your adapter a seam for its HTTP client so the harness can
script the upstream without a network. The template's `_fetch`/`_map` split
exists for the same reason — `_map` is testable against a captured payload with
no seam at all.)*

### Step 6 — run

```bash
python -m pytest tests/conformance/test_learner_context_conformance.py -q   # your adapter, existing tests
python -m pytest -q                                                          # everything else, unchanged
```

If both pass, you are done. **Files touched: two new files, one line in
`composition.py`, one line in `harness.py`, and `.env`.** No domain model, no
service, no route, no existing adapter, no existing test.

---

## What happens if you get it wrong

| Mistake | What you will see |
|---|---|
| Set a provider key with no implementation | Startup fails with `UnknownProvider`, naming the key, the env var, the expected filename, the registry symbol to import, and the template to copy. It does **not** fall back to a mock. |
| Let an upstream exception escape untranslated | `tests/conformance/shared.py::assert_only_contract_errors_raised` fails. (This is not hypothetical — it caught a real `AttributeError` in the shipped foreign adapter during development.) |
| Let an upstream field name or vendor name cross the boundary | The `leak_markers` check fails. (Also not hypothetical — it caught a provider name leaking through `IntentResult.rule`.) |
| Guess a value instead of defaulting | `test_an_unmappable_level_becomes_the_documented_default` fails. |
| Conflate `empty` with `unavailable` | `test_an_empty_source_is_empty_not_unavailable` fails. |
| Return a partial four-part answer | `test_a_missing_part_is_invalid_never_a_partial_answer` fails. |
| Block the event loop | `test_a_hanging_adapter_honours_the_caller_budget` fails. |

---

## The non-negotiables

1. **The adapter is the only place upstream payload shapes are known.** No
   upstream field name, no nesting, no error string escapes it. Keep the mapping
   in a separate method from the fetch, so it is unit-testable against a
   captured payload with no network.

2. **The adapter never invents data.** A missing value maps to the documented
   default with its source field marked accordingly — `naric_level_source =
   "default"`, `source_status.naric_level = "empty"` — never to a
   plausible-looking guess. A guess is indistinguishable from a real value
   downstream, which makes it worse than an absence.

3. **Authorisation stays server-side inside the adapter.** Credentials come from
   `Settings`. They never travel in a request from a client, and never appear in
   a response or a log.

4. **If the real payload cannot be mapped onto the platform contract, that is a
   contract conversation, not an adapter workaround.** Do not widen an enum, do
   not add a field to a domain model, do not smuggle an upstream value through in
   a string. Raise it. `docs/assumptions.md` §5 already lists two such gaps we
   raised rather than worked around — add yours there.
