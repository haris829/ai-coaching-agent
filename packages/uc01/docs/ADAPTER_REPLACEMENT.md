# Replacing a mock adapter with a real integration

The goal of this project's structure:

```
Real API arrives
      ↓
Implement / replace the adapter
      ↓
Map real response → internal contract
      ↓
UC-01 business logic remains unchanged
```

This document is the actual procedure, with a worked example.

---

## What you may and may not touch

| Do | Don't |
| --- | --- |
| Add a module in `uc01/adapters/real/` | Change `uc01/application/session_service.py` |
| Register it in `uc01/api/container.py` (one branch) | Change anything in `uc01/domain/` |
| Add config for base URLs, keys, timeouts in `uc01/config.py` | Add upstream field names to routes, schemas or UI code |
| Extend a contract in `uc01/contracts/services.py` *if the use case genuinely needs it* | Leak an upstream payload past the adapter |

If you find yourself editing the service to accommodate a real payload, the mapping
belongs in the adapter instead. `tests/test_adapter_replacement.py` fails if the service
module references an adapter.

---

## The four contracts

`uc01/contracts/services.py`:

```python
class NaricService(Protocol):
    def get_assessment(self, user: UserContext) -> NaricAssessment: ...

class CoursesService(Protocol):
    def list_accessible_courses(self, user: UserContext) -> Sequence[Course]: ...
    def get_accessible_course(self, user: UserContext, course_id: str) -> Course: ...

class CaseFileService(Protocol):
    def list_accessible_case_files(self, user: UserContext) -> Sequence[CaseFile]: ...
    def get_accessible_case_file(self, user: UserContext, case_id: str) -> CaseFile: ...

class ProfileService(Protocol):
    def get_profile(self, user: UserContext) -> UserProfile: ...
```

Plus `UserContextProvider` (identity), `GreetingGenerator` (greeting composition) and
`SessionRepository` (storage).

These are `typing.Protocol`s: your class does not need to inherit anything. It only needs
matching method signatures.

---

## The three exceptions you may raise

`uc01/contracts/exceptions.py`. Nothing else may escape an adapter — no `httpx` errors, no
vendor SDK errors, no `KeyError`.

| Exception | Raise when | UC-01 does |
| --- | --- | --- |
| `DependencyUnavailableError` | Unreachable, timeout, 5xx, auth failure against the upstream | Marks the dependency `unavailable`; disables that mode or applies the NARIC fallback |
| `InvalidUpstreamResponseError` | Reachable, but the payload cannot be normalised | Same as unavailable. **Never partially trusted** |
| `ResourceNotAccessibleError` | The requested id does not exist for this user, or they may not use it | 403 with a non-enumerable message |

Always attach `technical_detail=` — it is logged server-side and stored in the session
record's `diagnostics_json`, and never returned to a client.

---

## Procedure

### 1. Copy the template

`uc01/adapters/real/template.py` is a skeleton with the transport and mapping structure
already laid out (and a reference `httpx` error-handling block in its docstring). Copy it
to `uc01/adapters/real/naric.py` (or `courses.py`, `cases.py`, `profile.py`).

### 2. Write the transport

Catch every transport and decode error; translate to contract exceptions.

```python
def _get(self, path: str) -> Mapping[str, Any]:
    try:
        response = httpx.get(
            f"{self._base_url}{path}",
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        logger.warning("naric.timeout", extra={"uc01": {"path": path}})
        raise DependencyUnavailableError(
            "naric", technical_detail=f"timeout after {self._timeout}s"
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("naric.http_error",
                       extra={"uc01": {"status": exc.response.status_code}})
        raise DependencyUnavailableError(
            "naric", technical_detail=f"HTTP {exc.response.status_code}"
        ) from exc
    except ValueError as exc:  # JSON decode
        raise InvalidUpstreamResponseError(
            "naric", technical_detail="response was not valid JSON"
        ) from exc
```

Never log the API key, and never put a URL or key into a `technical_detail` that could be
surfaced by `UC01_EXPOSE_ERROR_DETAILS`.

### 3. Map onto the internal contract

The one rule that matters most:

> **If the level cannot be trusted, return `level=None` with a non-`COMPLETE` state, or
> raise `InvalidUpstreamResponseError`. Never invent a level in the adapter.**

Inventing 5 in the adapter would make a defaulted level look calibrated. UC-01 applies the
Level 5 default itself and records `naric_level_source="default"`.

```python
_STATUS = {"COMPLETED": NaricAssessmentState.COMPLETE,
           "PARTIAL": NaricAssessmentState.INCOMPLETE,
           "IN_CALIBRATION": NaricAssessmentState.CALIBRATING}

def _map(self, payload: Mapping[str, Any]) -> NaricAssessment:
    state = _STATUS.get(str(payload.get("assessmentStatus", "")).upper())
    if state is None:
        raise InvalidUpstreamResponseError("naric", technical_detail="unknown status")
    level = self._coerce_level((payload.get("result") or {}).get("explanationLevel"))
    if state is NaricAssessmentState.COMPLETE and level is None:
        raise InvalidUpstreamResponseError("naric", technical_detail="unparseable level")
    return NaricAssessment(state=state, level=level)
```

If the real scale is not 1..10, map it here. `tests/test_adapter_replacement.py` contains a
working example (`ForeignNaricAdapter`) that maps a 1..4 band onto UC-01's scale.

### 4. Do the authorization check inside the adapter

For courses and cases, "may this user open this?" is an upstream question. Answer it in the
adapter and raise `ResourceNotAccessibleError` for both *missing* and *forbidden* — UC-01
maps them to one non-enumerable message on purpose.

```python
def get_accessible_course(self, user: UserContext, course_id: str) -> Course:
    payload = self._get(f"/learners/{user.user_id}/courses/{course_id}")
    if not payload.get("granted"):
        raise ResourceNotAccessibleError("courses", resource_id=course_id,
                                         technical_detail="upstream denied access")
    return self._map_course(payload)
```

If the real API can answer accessibility in one call, use it — but never rely on the client
to tell you the answer, and never let the UI's disabled state substitute for this check.

### 5. Register it in the container

`uc01/api/container.py` — one branch per dependency, marked with a `>>> <<<` comment:

```python
def _build_naric(self, scenarios: ScenarioSet) -> NaricService:
    choice = self.settings.naric_adapter
    if choice == "mock":
        return MockNaricAdapter(scenarios.naric)
    if choice == "real":                                     # <-- add this
        from ..adapters.real.naric import RealNaricAdapter
        return RealNaricAdapter(
            base_url=self.settings.naric_base_url,
            api_key=self.settings.naric_api_key,
        )
    raise NotImplementedError(...)
```

Add the settings to `uc01/config.py` and document them in `.env.example`. Read secrets from
the environment only — never commit them.

### 6. Flip the switch

```bash
UC01_NARIC_ADAPTER=real python -m uc01
```

`/api/v1/healthz` then reports `using_mock_adapters: false` and shows `naric: real`, so a
deployment can prove which integrations are live.

Setting `real` before the adapter exists fails loudly:

```
NARIC adapter is configured as 'real', but no real adapter is implemented yet.
Add uc01/adapters/real/naric.py implementing the NaricService contract and register it in
uc01/api/container.py. See docs/ADAPTER_REPLACEMENT.md.
```

### 7. Test it

Adapter tests belong next to the adapter, not in the UC-01 service tests. Cover, at
minimum:

* a successful mapping;
* every failure the real API can produce (timeout, 5xx, 401, malformed body, partial data)
  → the right contract exception, with technical detail;
* the accessibility check for both missing and forbidden ids.

Then re-run the whole suite. The existing UC-01 tests keep running against the mocks, so
they still cover the failure paths deterministically in CI. **Keep the mocks** — they are
how the degraded-path tests stay hermetic.

Add the new adapter to the parametrised list in
`tests/test_adapter_replacement.py::test_adapters_satisfy_their_contract` for a static
conformance check.

---

## Replacing identity (authentication)

`DevHeaderUserContextProvider` is a development stand-in. To replace it:

1. Implement `resolve(credential: Optional[str]) -> UserContext` — verify the token
   properly (signature, expiry, audience, issuer) and raise
   `AuthenticationRequiredError` on any failure.
2. Register it in `AppContainer._build_identity` under a new `UC01_IDENTITY_PROVIDER`
   value.

`UserContext.user_id` becomes the platform's user identifier and flows into every
authorization check and session record automatically. Nothing else changes. If the company
system also supplies tenancy, `UserContext.tenant_id` already exists.

---

## Replacing the greeting generator (optional)

The shipped `LocalTemplateGreetingGenerator` needs no AI service. If one is introduced
later, implement `GreetingGenerator.generate(context) -> Greeting` and register it in the
container.

Two rules the replacement must keep:

* the system prompt comes from `uc01/domain/prompts.py` and is never accepted from a
  client;
* external text goes through `sanitize_untrusted_text` and stays in the untrusted segment
  of the prompt payload. `PromptPayload.render()` already produces the correct
  three-segment structure — use it rather than concatenating strings.

---

## Replacing the session store

See [`PERSISTENCE.md`](PERSISTENCE.md#replacing-the-store): implement `SessionRepository`,
register it in `AppContainer._build_repository`, add it to the `repo` fixture params so the
existing tests cover it.

---

## Checklist

- [ ] Adapter lives in `uc01/adapters/real/`, satisfies exactly one Protocol.
- [ ] All mapping is inside the adapter; no upstream field name appears outside it.
- [ ] Every upstream failure becomes a contract exception with `technical_detail`.
- [ ] No secret is logged or placed in a user-facing message.
- [ ] The adapter never invents a NARIC level.
- [ ] Accessibility is checked server-side inside the adapter.
- [ ] Registered in `container.py`; settings documented in `.env.example`.
- [ ] Adapter tests cover success and each failure mode.
- [ ] `python -m pytest` passes unchanged — no edits to `uc01/application` or `uc01/domain`.
