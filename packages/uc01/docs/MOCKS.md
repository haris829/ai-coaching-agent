# Mock integrations

> **These are temporary development adapters, not production integrations.**
> Everything they return is fixture data from `uc01/adapters/mock/fixtures.py`. No real
> service is contacted anywhere in this repository. `GET /api/v1/healthz` reports
> `integrations.using_mock_adapters: true` with an explicit warning string, and
> `uc01.adapters.mock.IS_MOCK` is `True`.

They exist because the real NARIC, Courses Agent, Case Prep and Profile APIs are not
available yet, and UC-01 development must not wait for them.

---

## Why they are shaped the way they are

Each mock deliberately does two things in sequence:

1. produces a **plausible external payload** — camelCase keys, nested objects, string
   numbers, ISO timestamps, `enrolledLearners` arrays;
2. **normalises** that payload into UC-01 domain types, raising a contract exception when
   it cannot.

Step 2 is the work a real adapter must do. Keeping it in the mock means the mapping code
already has a home, and replacing the mock is a like-for-like swap rather than a new
design problem.

```python
# uc01/adapters/mock/naric.py — the mapping a real adapter also performs
result = payload.get("result") or {}
level = self._coerce_level(result.get("explanationLevel"))   # "8" -> 8
if state is NaricAssessmentState.COMPLETE and level is None:
    raise InvalidUpstreamResponseError(DEPENDENCY, technical_detail=...)
return NaricAssessment(state=state, level=level, ...)
```

---

## Development users

Three users, chosen so the per-user states the brief requires can be exercised without
any global flag.

| Token | User | NARIC | Courses | Case files | Profile |
| --- | --- | --- | --- | --- | --- |
| `dev-alice` | `u_alice` | complete, level 8 | 3 (incl. one with no lessons) | 1 (`case_alpha`) | complete |
| `dev-bob` | `u_bob` | **calibrating** | 1 (`crs_tort`) | **none** → case-linked disabled | complete |
| `dev-carol` | `u_carol` | **incomplete** | **none** → course-linked disabled | 1 (`case_beta`) | **no name** |

Cross-user access checks fall out of this: `crs_tort` is Bob's, `case_beta` is Carol's, so
Alice requesting either gets a 403.

---

## Fixture catalogue

**Courses** (`COURSE_CATALOGUE`)

| `courseId` | Title | Lessons | Enrolled |
| --- | --- | --- | --- |
| `crs_contract_law` | Contract Law Foundations | `lsn_offer`, `lsn_consideration`, `lsn_terms` | u_alice |
| `crs_evidence` | Evidence and Proof | `lsn_burden`, `lsn_hearsay` | u_alice |
| `crs_tort` | Tort Law Essentials | `lsn_duty` | u_bob |
| `crs_no_lessons` | Advanced Advocacy (coming soon) | *(none)* | u_alice |

`crs_no_lessons` exists so "listed but not openable" is a testable state.

**Case files** (`CASE_CATALOGUE`)

| `caseFileId` | Title | Matter ref | Authorised |
| --- | --- | --- | --- |
| `case_alpha` | Alpha Holdings v. Brookfield | AH-2026-0142 | u_alice |
| `case_beta` | Re: Beta Estate | BE-2026-0077 | u_carol |

---

## Scenarios

Set globally with environment variables, or per request with the dev-only
`X-Dev-Scenarios` header.

### NARIC — `UC01_MOCK_NARIC`

| Value | Behaviour | UC-01 result |
| --- | --- | --- |
| `per_user` *(default)* | Per-user fixture | varies |
| `success` | Complete assessment, `explanationLevel: "7"` | level 7, `source=naric` |
| `incomplete` | `assessmentStatus: PARTIAL`, no level | level 5, `source=default`, notice + offer |
| `calibrating` | `assessmentStatus: IN_CALIBRATION` | level 5, `source=default`, calibrating notice |
| `unavailable` | Raises `DependencyUnavailableError` | level 5, `source=default`, session still opens |
| `invalid` | Unknown status + `"very high"` level → `InvalidUpstreamResponseError` | level 5, `source=default` — no part of the payload is trusted |

A `COMPLETED` assessment whose level cannot be parsed is treated as **invalid**, never as
Level 5 from NARIC.

### Courses — `UC01_MOCK_COURSES`

| Value | Behaviour | Effect on `course-linked` |
| --- | --- | --- |
| `available` *(default)* | Per-user catalogue | Available (Disabled for a user with no courses, reason "You do not have any courses available yet.") |
| `empty` | Reachable, nothing accessible | Disabled — same "no courses" reason |
| `unavailable` | Raises `DependencyUnavailableError` | Disabled — "Courses are temporarily unavailable." |
| `invalid` | Malformed envelope → `InvalidUpstreamResponseError` | Disabled — same as unavailable |

Also covered by fixtures rather than scenarios: **course with lessons**
(`crs_contract_law`), **missing/invalid lesson** (any unknown `lesson_id`, or a real
lesson from a different course), **inaccessible course** (`crs_tort` as Alice).

### Case files — `UC01_MOCK_CASES`

| Value | Behaviour | Effect on `case-linked` |
| --- | --- | --- |
| `available` *(default)* | Per-user set | Available (Disabled for `dev-bob`: "No accessible case files.") |
| `empty` | Reachable, nothing accessible | Disabled — "No accessible case files." |
| `unavailable` | Raises `DependencyUnavailableError` | Disabled — "Case files are temporarily unavailable." |
| `invalid` | Records without ids → `InvalidUpstreamResponseError` | Disabled |

Inaccessible case: `case_beta` as Alice → 403.

### Profile — `UC01_MOCK_PROFILE`

| Value | Behaviour | Effect |
| --- | --- | --- |
| `available` *(default)* | Per-user fixture | Personalised greeting (generic for `dev-carol`, who has no name) |
| `incomplete` | Profile with empty sub-objects | Generic greeting, `personalisation_incomplete` notice, **no invented name** |
| `unavailable` | Raises `DependencyUnavailableError` | Generic greeting + "We couldn't load your personalised profile information…" notice, session still opens |

---

## Driving scenarios

**Per request (development only):**

```bash
curl -H "Authorization: Bearer dev-alice" \
     -H "X-Dev-Scenarios: courses=unavailable,naric=incomplete" \
     http://127.0.0.1:8000/api/v1/session-bootstrap
```

**Globally:**

```bash
UC01_MOCK_COURSES=unavailable UC01_MOCK_NARIC=incomplete python -m uc01
```

**In tests:**

```python
from .conftest import auth, scenarios

client.get("/api/v1/session-bootstrap", headers={**auth(), **scenarios(courses="unavailable")})
```

**In the browser:** the reference UI has a clearly-marked developer panel with a user
switcher and one dropdown per dependency.

### Guardrails on the header

* Ignored entirely unless `UC01_DEV_MODE=true` **and** `UC01_DEV_SCENARIO_HEADER=true`
  (`test_scenario_header_is_ignored_when_dev_mode_is_off`).
* Unknown keys/values are ignored; a malformed header cannot cause a 500
  (`test_malformed_scenario_header_is_not_a_server_error`).
* It selects a fixture. It cannot change identity, authorization, or the recorded
  `naric_level_source` (`test_scenario_header_cannot_forge_a_naric_source`).
* It has no effect at all once an adapter is set to `real` — scenarios only reach mock
  constructors.

---

## Replacing a mock

Short version: implement the same Protocol in `uc01/adapters/real/`, register it in
`uc01/api/container.py`, flip `UC01_<DEP>_ADAPTER=real`. UC-01 business logic does not
change. Full walkthrough with a worked example:
[`ADAPTER_REPLACEMENT.md`](ADAPTER_REPLACEMENT.md).

The mocks should be kept after the real adapters arrive: they are what makes the failure
paths testable in CI without depending on a live external service.
