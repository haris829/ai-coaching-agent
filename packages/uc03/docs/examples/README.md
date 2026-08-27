# Integration Swap Proof — replacing the legal authority source

The complete change a company engineer makes to swap one dependency. Three
edits, one of them a new file. **No file under `uc03/` other than `factory.py`
is touched, and no test is modified.**

## 1. The new file

[`company_authority_adapter.py`](company_authority_adapter.py) — the adapter
over the approved legal authority service. It subclasses nothing and imports
only the two domain types it must return.

Move it to wherever company code lives, e.g. `ourco/uc03/authority.py`.

## 2. The one registry line

`uc03/factory.py`:

```diff
 from .adapters.mocks import (
     InMemoryFramingRegistry,
     InMemoryQuestionLogger,
     MockContextProvider,
-    MockLegalAuthorityProvider,
     StaticSessionAuthorizer,
     SystemClock,
 )
+from ourco.uc03.authority import CompanyAuthorityAdapter

@@
         context_provider=MockContextProvider(),          # -> company NARIC / Legal Footprints
-        authority_provider=MockLegalAuthorityProvider(),  # -> approved legal authority source
+        authority_provider=CompanyAuthorityAdapter(),     # approved legal authority source
         tagger=RuleBasedTopicTagger(),
```

## 3. The one config change

Environment only — nothing in code:

```bash
export COMPANY_AUTHORITY_URL="https://authority.internal"
export COMPANY_AUTHORITY_KEY="…"
```

Optionally review `UC03_NO_AUTHORITY_MESSAGE` if the company has defined its own
wording (it must keep the Westlaw/BAILII direction).

## 4. Grade it

```bash
pytest tests/test_company_authority_adapter.py -v   # the conformance suite
python -m pytest                                    # the core suite, unchanged
```

The conformance suite checks the behavioural contract: return types, that a
`VERIFIED` result carries `verified_by` + `verification_id`, that an unknown
topic yields `NO_VERIFIED_AUTHORITY` rather than an invented citation, that
`NO_VERIFIED_AUTHORITY` never smuggles a citation through, and that no upstream
payload shape or error string escapes the boundary.

## What was NOT touched

Confirmed by `git diff --name-only` on a real swap:

| Untouched | Why |
|---|---|
| `uc03/service.py` | The core depends on `LegalAuthorityProvider`, not on any adapter. Enforced by `test_service_module_imports_no_concrete_adapter`. |
| `uc03/contracts.py` | The contract did not change — that is the point. |
| `uc03/domain/` | Domain types are shared, not adapter-specific. |
| `uc03/api.py` | The HTTP surface knows nothing about authority sources. |
| `uc03/adapters/mocks.py` | Kept, so the test suite still runs offline. |
| `uc03/conformance/` | Grades the new adapter unchanged. |
| every file in `tests/` | No test needed editing to accommodate the swap. |

Only `uc03/factory.py` changes — plus the new file, which lives outside `uc03/`.
