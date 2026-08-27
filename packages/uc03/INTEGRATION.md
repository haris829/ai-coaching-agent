# UC-03 — Integration Handoff

Everything a company engineer needs to replace the development mocks. The core
service does not change during integration.

```
TODAY                        LATER
UC-03 Core                   UC-03 Core          (unchanged)
    |                            |
Internal Contracts           SAME Internal Contracts   (unchanged)
    |                            |
Mock Providers               Company Adapters
```

## What to replace

Seven adapters. All are wired in one place — `uc03/factory.py`.

| # | Contract (`uc03/contracts.py`) | Replace this mock | With | Notes |
|---|---|---|---|---|
| 1 | `ContextProvider` | `MockContextProvider` | **Company NARIC/context service** + **Legal Footprints** | One call must return NARIC level *and* practice area. Set the availability flags honestly; omit rather than guess. |
| 2 | `LegalAuthorityProvider` | `MockLegalAuthorityProvider` | **Approved legal authority source** | Return `VERIFIED` only for an affirmatively verified citation, with `verified_by` + `verification_id`. Otherwise `NO_VERIFIED_AUTHORITY`. |
| 3 | `QuestionLogger` | `InMemoryQuestionLogger` | **Company database / event log** | One row per question. May raise; the service degrades rather than failing. |
| 4 | `SessionAuthorizer` | `StaticSessionAuthorizer` | **Company authentication / session system** | `authenticate` maps credential → user; `owns_session` is the cross-user guard. |
| 5 | `AnswerGenerator` (+ optionally `QuestionClassifier`) | `TemplateAnswerGenerator` / `RuleBasedClassifier` | **Company generator**, or the bundled `uc03.adapters.llm` Claude adapters | The template generator is a development stand-in and must not ship to learners. |
| 6 | `FramingRegistry` | `InMemoryFramingRegistry` | **Company storage** (own table, or the same store behind the logger) | Holds the never-repeat-a-framing rule. Must survive a process restart. |
| 7 | `InteractionReader` | `InMemoryQuestionLogger` (same object) | **Read path over the company event log** | Needed to anchor a follow-up to its original question. |

### Contracts are structural

Each contract is a `typing.Protocol`. A company adapter does **not** import or
subclass anything from UC-03 — it just needs matching method signatures.
`tests/test_adapter_replacement.py` writes a complete company adapter set from
scratch and runs the unmodified service on it; use those classes as templates.

## Step by step

1. Write your adapter with the signature from `uc03/contracts.py`.
2. **Run the conformance suite for that port against your adapter** (below).
   This is the one command that tells you whether your integration is correct.
3. Swap it into `uc03/factory.py`.
4. Run `python -m pytest`. The core suite must stay green; only the mock-specific
   tests need new fixtures.
5. Re-run `python -m bench.p95` against real dependency latency (see below).

## Conformance kit — point it at your adapter

`uc03.conformance` ships one reusable, adapter-agnostic suite per port. It
asserts the behavioural contract — correct return types, every documented
failure mode raising the correct typed contract exception, no upstream payload
shape or field name or error string escaping the adapter boundary, values
normalised to the platform contract regardless of what the upstream sent, and
timeouts honoured. It imports no mock, so it grades your adapter and ours
identically.

Write one file per port:

```python
# tests/test_our_context_adapter.py
import pytest
from uc03.conformance import ContextProviderConformance
from ourco.uc03 import CompanyContextAdapter


class TestCompanyContext(ContextProviderConformance):
    @pytest.fixture
    def adapter(self):
        return CompanyContextAdapter(base_url="https://context.internal")

    @pytest.fixture
    def known_user(self):
        return ("real-user-id", "real-session-id")
```

Then run the suite for the port you are integrating:

| Port | Suite class | Command |
|---|---|---|
| `ContextProvider` | `ContextProviderConformance` | `pytest tests/test_our_context_adapter.py -v` |
| `LegalAuthorityProvider` | `LegalAuthorityProviderConformance` | `pytest tests/test_our_authority_adapter.py -v` |
| `QuestionLogger` | `QuestionLoggerConformance` | `pytest tests/test_our_logger_adapter.py -v` |
| `InteractionReader` | `InteractionReaderConformance` | `pytest tests/test_our_reader_adapter.py -v` |
| `SessionAuthorizer` | `SessionAuthorizerConformance` | `pytest tests/test_our_auth_adapter.py -v` |
| `FramingRegistry` | `FramingRegistryConformance` | `pytest tests/test_our_framing_adapter.py -v` |
| `QuestionClassifier` | `QuestionClassifierConformance` | `pytest tests/test_our_classifier.py -v` |
| `AnswerGenerator` | `AnswerGeneratorConformance` | `pytest tests/test_our_generator.py -v` |
| `TopicTagger` | `TopicTaggerConformance` | `pytest tests/test_our_tagger.py -v` |

All ports at once, against the shipped mocks (the passing baseline, and a
worked example of every file above):

```bash
pytest tests/test_conformance_kit.py -v
```

Fixtures each suite may ask you to override are documented in the suite's
docstring — e.g. `known_user` for `ContextProvider`, and
`valid_credential` / `owned_session` / `foreign_session` for
`SessionAuthorizer`.

## Required semantics per adapter

**ContextProvider** — never invent a NARIC level or practice area. When a field
is genuinely absent, say so via `naric_level_source` (`retrieved` | `default`)
and `practice_area_availability` (`PROVIDED` | `MISSING` | `PROVIDER_UNAVAILABLE`).
`naric_level` always carries a real level from the closed set LEVEL_3..LEVEL_7_PLUS;
`naric_level_source` is the only thing that says whether it was retrieved or
defaulted, so a stored level is never ambiguous. Raising is acceptable: the
service falls back to LEVEL_3 / `default` / `PROVIDER_UNAVAILABLE` and records
`context_provider_unavailable`. A level outside the closed set is coerced to the
default and recorded as `naric_level_unrecognised` - it will not crash the
request, but it is an adapter bug.

**LegalAuthorityProvider** — the integrity boundary. A `VERIFIED` result is a
claim that the citation is real and was checked, so populate `verified_by`
(which system vouched) and `verification_id` (the traceable reference). Never
derive `VERIFIED` from model output. When in doubt return
`NO_VERIFIED_AUTHORITY`; the service then shows the configured no-authority
message and points the learner at Westlaw/BAILII. Adjust that message in
`uc03/config.py` if the company defines different wording — it must retain the
Westlaw/BAILII direction.

**QuestionLogger** — receives a `QuestionLogRecord` for *every* question:
answers, clarification requests, out-of-scope redirects, errors, timeouts,
unauthorised attempts and oversized input. `answer` is `None` whenever no answer
exists; `status` says why. `rating_state` is always `pending` — UC-10 owns the
transition to `rated`. `topic_tag` is always a member of the controlled vocabulary in
`uc03/domain/topics.py`; extend that enum rather than accepting free text, or
downstream gap-tracking loses its closed dimension.

**SessionAuthorizer** — `owns_session` is what stops one learner reading
another's session. It must be a real server-side check against session
ownership, not a claim taken from the request.

**FramingRegistry** — holds which explanation framings have been used, keyed by
`(session_id, concept_key)`, plus the explanation texts already shown. It must
**not** live in generator memory: the never-repeat rule has to survive a process
restart and hold across generator instances. If the registry is unreachable
during a follow-up, UC-03 fails that request rather than risk repeating a
framing — "never repeat" is not a best-effort rule.

**InteractionReader** — `get_interaction(question_id)` returns the logged record
or `None`. Return `None` for another user's interaction too, so a caller cannot
probe for existence. Usually the same object as your `QuestionLogger`.

**AnswerGenerator** — must return only the three prose parts. If you use an LLM,
keep prompts server-side and keep the citation prohibition: `uc03/adapters/llm.py`
shows the shape, including schema-constrained output and refusal handling. The
citation guard runs regardless.

## Configuration to review

`uc03/config.py` — all server-side, none client-settable:

| Setting | Default | Review because |
|---|---|---|
| `thinking_after_ms` | 1500 | Company requirement |
| `timeout_ms` | 10000 | Company requirement |
| `p95_target_ms` | 3000 | Company requirement |
| `max_question_chars` | 2000 | Tune to the real input surface |
| `no_authority_message` | see file | Must be the company's defined wording; keep the Westlaw/BAILII direction |
| `paraphrase_threshold` | 0.60 | Overlap at which a follow-up counts as a reworded repeat |
| `framings_exhausted_message` | see file | Company tone of voice |
| `out_of_scope_message` | see file | Company tone of voice |
| `verification_routes` | Westlaw, BAILII | Company-approved verification routes |
| `citation_guard_enabled` | `true` | Leave enabled |

All are overridable by environment variable with the `UC03_` prefix.

## Latency re-measurement — do this

`bench/p95.py` defines "normal load" as 300 questions, 20 concurrent, with
mocked dependency latency (generator 300–1200 ms, authority 20–120 ms, context
5–25 ms, log 2–10 ms). The measured P95 of **1185 ms** against the 3000 ms
target is real wall-clock, but it is only as meaningful as that generator
latency band.

**A real LLM generator is the dominant term.** Before quoting a production SLO:

1. Measure your generator's latency distribution.
2. Update `Scenario.generator_ms` in `bench/p95.py`.
3. Re-run `python -m bench.p95`; it exits non-zero if P95 exceeds the target.

If the real generator pushes P95 over 3s, the levers are: lower reasoning effort
or a smaller model for the generator, streaming the response, caching the stable
system prefix, and running the authority lookup concurrently with generation
(currently sequential because the guard's allow-list needs the verified
citation — it can be reordered if needed).

## What UC-03 deliberately does not do

* No frontend. The response carries `thinking_after_ms`, `timeout_ms`,
  `thinking_state_emitted` and `retry_available` so the frontend can build the
  thinking animation and retry UI; the SSE endpoint gives it a real signal.
* No rating workflow — UC-10. UC-03 only emits `pending`.
* No frontend for follow-ups; the three actions are real server operations
  (`POST /uc03/questions/{id}/follow-up`), but nothing renders them here.
* No production database — `QuestionLogger` is the seam.

## Assumptions you may want to overturn

Everything UC-03 invented is listed in [docs/assumptions.md](docs/assumptions.md)
with its risk and its location in code. The field-by-field contract an
integrator needs is [docs/SHARED_CONTRACT.md](docs/SHARED_CONTRACT.md).

## Merge notes for UC-01 / UC-02

UC-03 never imports them. At merge time the only coupling point is
`ContextProvider`: implement it over whatever UC-01/UC-02 expose (NARIC level
and Legal Footprints practice area, keyed by `user_id` + `session_id`) and wire
it in `uc03/factory.py`. If their context model is richer, normalise it into
`LearnerContext` inside the adapter rather than widening the contract, so the
core stays independent.
