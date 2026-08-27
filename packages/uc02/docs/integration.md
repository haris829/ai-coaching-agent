# UC-02 Integration Guide

For the engineer who receives this repository when the company delivers the real
systems. Read `docs/assumptions.md` alongside this file: this one says *what to
change*, that one says *what to verify first*.

---

## 1. What UC-02 is, and what it deliberately is not

UC-02 assembles a learner's context at the moment a coaching session begins. It
calls four upstream systems concurrently, normalises whatever comes back into one
typed object, stores it against a session id, and hands a narrow projection of it
to internal callers.

It does **not**:

- create or own sessions (that is UC-01),
- generate coaching responses (no LLM, no agent framework, no vector store),
- serve a frontend,
- assume a production database,
- ship production auth,
- call any external URL. There is not one in the codebase, and a test asserts it.

Nine sibling use cases exist in other repositories. Nothing here imports, calls,
stubs or references them.

---

## 2. The session identity problem — read before writing any integration code

**UC-01 creates sessions. UC-02 does not.** UC-02 owns the *context* keyed by a
session id; it does not own the session lifecycle. Getting this backwards is the
most likely way for integration to break, so it is enforced in code rather than
described in prose.

### The rule

UC-02 never invents a `session_id` in a production path. It receives one from its
caller and treats it as an **opaque string** — never parsed, never validated
beyond non-emptiness and length, never assigned meaning.

### The input contract

```
SessionIdentity
├── session_id: str            # opaque, supplied by the caller (UC-01 in production)
├── user_id: str               # resolved via CurrentUserProvider, never read from the request body
├── requested_at: datetime
└── session_id_origin: str     # "caller" in production; "dev-minted" only in development
```

### What the caller must do

1. UC-01 creates the session and obtains a session id.
2. The caller invokes `POST /api/v1/context/initialize` with that id, carrying the
   learner's authenticated identity.
3. UC-02 builds the context once and stores it against that id.

Calling initialize for a session UC-01 has not created is not an error UC-02 can
detect — it will happily build context for any opaque string. The ordering
guarantee lives with the caller.

### Standalone development

So the service is runnable on its own, `session_id` may be omitted **only** when
`ALLOW_DEV_SESSION_IDS=true`. UC-02 then mints `dev-session-<uuid4>`.

- Default: **off**.
- Must be **off in production**. `Settings.production_guard_violations()` reports it
  as a violation and `create_app` logs `config.production_guard.violation` at ERROR.
- With it off, a request without a session id returns `400 session_id_required`
  with a message naming UC-01. That loud failure is intentional.
- A `dev-session-` prefix in a production log means the guard was bypassed.

---

## 3. Architecture

```
        HTTP (internal service-to-service)
                    │
        uc02/api/v1/context.py ── uc02/api/v1/schemas.py   (narrow wire projection)
                    │
        uc02/composition.py                               (the only place concretes are chosen)
                    │
   uc02/application/context_assembly_service.py           (all business logic; depends only on ports)
                    │
        ┌───────────┼─────────────────────────┐
        │           │                         │
  domain/ports  domain/models        application/normalisers.py
  (6 contracts)  (typed context)     (failure + default matrix, pure)
        │
        ├── infrastructure/providers/mocks/     ← today
        ├── infrastructure/providers/company/   ← the stubs you replace
        ├── infrastructure/repositories/        ← in-memory, TTL
        └── infrastructure/identity/            ← header shim
```

The dependency arrow only ever points inward. `ContextAssemblyService` imports no
concrete adapter — asserted by `test_the_assembly_service_never_imports_a_concrete_adapter`.

---

## 4. Replacing the mocks: the whole procedure

For each provider the work is the same four steps.

1. **Verify the assumptions.** Open `docs/assumptions.md`, find the rows listed in
   the class docstring in `uc02/infrastructure/providers/company/__init__.py`, and
   diff each against the delivered spec.
2. **Implement the port** in `uc02/infrastructure/providers/company/`. Translate the
   real payload into the record type; translate transport failures into
   `ProviderUnavailable` / `ProviderTimeout` / `ProviderInvalidResponse`.
3. **Register it** in `uc02/infrastructure/providers/factory.py` (the `company` key
   is already wired to the stub class — replacing the class body is enough).
4. **Flip the config value**, e.g. `NARIC_PROVIDER=company`.

`ContextAssemblyService` does not change. Neither do the domain models, the
normalisers, the API, or the tests — unless an assumption marked **port** in the
register turns out to be wrong.

### Rules every real adapter must honour

- **Be `async`.** All four are called concurrently.
- **Return the record, or raise one of the three typed errors.** Nothing else.
  Anything undeclared is caught, categorised as `unexpected` and recorded as
  `invalid` — it will not crash assembly, but it is a bug in the adapter.
- **Never return `None` to mean "down".** "The learner has nothing" is an *empty
  record*; "the source is down" is an *exception*. UC-02 records these as `empty`
  and `unavailable`, and downstream analytics depends on the distinction.
- **Do not apply defaults.** Defaulting is the assembly service's job, so that
  every fallback is recorded in `source_status` and logged.
- **Do not log question text or full legal profiles.**
- **Stay inside the timeout.** `PROVIDER_TIMEOUT_MS` is enforced by the caller, but
  an adapter that internally paginates must budget for it.

---

## 5. Per-component handoff

| Component | File to create / replace | Interface | Config to change | Verify first |
|---|---|---|---|---|
| `MockNaricProvider` | `uc02/infrastructure/providers/company/__init__.py` → `CompanyNaricProvider` | `NaricProvider.get_qualification_level(user_id) -> NaricRecord` | `NARIC_PROVIDER=company` | A-01, A-02, A-03, A-04, A-05 |
| `MockCoursesProvider` | same file → `CompanyCoursesProvider` | `CoursesProvider.get_learning_context(user_id) -> CoursesRecord` | `COURSES_PROVIDER=company` | A-06, A-07, A-08, A-09 |
| `MockLegalFootprintsProvider` | same file → `CompanyLegalFootprintsProvider` | `LegalFootprintsProvider.get_profile(user_id) -> LegalProfileRecord` | `LEGAL_PROVIDER=company` | A-10, A-11, A-12, A-13 |
| `MockQuestionHistoryProvider` | same file → `CompanyQuestionHistoryProvider` | `QuestionHistoryProvider.get_recent_questions(user_id, limit) -> list[QuestionRecord]` | `HISTORY_PROVIDER=company` | **A-15 first**, then A-16, A-17, A-18 |
| `InMemorySessionContextRepository` | new class in `uc02/infrastructure/repositories/` | `SessionContextRepository.save/get/delete` | wire it in `composition.get_repository()` | A-28, A-33 |
| `DevelopmentUserProvider` | new class in `uc02/infrastructure/identity/` | `CurrentUserProvider.resolve(request) -> user_id` | wire it in `composition.get_current_user_provider()` | A-22, A-23 |

The last two have no config switch because there is no second implementation to
choose between yet. If you want one, extend `Settings` the way the four provider
choices are done — it is four lines.

---

## 6. Replacing persistence

`InMemorySessionContextRepository` is the entire persistence layer: no migrations,
no ORM, no schema, no indexes. Replace the class, keep the port.

What the replacement must preserve:

- `get` returns `None` for an unknown **or expired** session, and never raises for
  a miss.
- `save` overwrites by `session_id`.
- Expiry: 12-hour TTL today (`CONTEXT_TTL_HOURS`). The company's layer will likely
  do this differently — row TTL, a background sweep, or not at all. Either is fine
  provided an expired context reads as absent.
- The stored value is a `SessionContext`, which contains question excerpts and the
  learner's legal profile. Treat the store as holding personal data.

What changes for free once storage is shared: the "no re-query on second
initialize" guarantee currently holds **per process** (A-33). With N workers, a
session can be built up to N times. A shared store makes the guarantee global.

---

## 7. Replacing identity

`DevelopmentUserProvider` reads a configurable header (`DEV_USER_ID_HEADER`,
default `X-User-Id`) and trusts it. That is acceptable in development and in tests,
and unacceptable in production — anyone could claim any identity.

The replacement validates the platform's real credential and returns the subject.
One method, one return value. Nothing else in the codebase reads identity, and the
request body has no identity field at all (`extra="forbid"` on the request model
rejects one outright).

---

## 8. Behaviour the integration must not regress

These are guaranteed by tests. If a change makes one fail, the change is wrong.

| Guarantee | Test |
|---|---|
| No single source failure blocks context creation | `test_one_source_down_still_returns_a_valid_context` |
| All four failing still returns a valid context plus the notice | `test_all_four_down_returns_a_valid_default_context_with_the_notice` |
| `empty` is never conflated with `unavailable` | `test_zero_questions_is_empty_not_unavailable`, `test_empty_enrolments_are_empty_not_unavailable` |
| Providers are called concurrently | `test_providers_are_called_concurrently_not_serially` |
| Total assembly budget is a hard ceiling | `test_budget_shorter_than_provider_timeout_is_still_enforced` |
| Second initialize triggers zero provider calls | `test_second_initialize_triggers_no_provider_calls` |
| A client cannot override the resolved NARIC level | `test_a_client_supplied_naric_level_is_rejected_not_absorbed` |
| A session id alone cannot retrieve context | `test_a_session_id_alone_is_not_sufficient_to_retrieve_context` |
| Adapters are swappable without touching the service | `tests/unit/test_adapter_independence.py` |

---

## 9. Source status vocabulary

Recorded per source on every context, and returned in the API response.

| Status | Meaning | Example |
|---|---|---|
| `available` | The source answered with complete data. | Three enrolments, all with a last-accessed lesson. |
| `empty` | The source answered; the learner has nothing. **Not a failure.** | A learner who has never asked a question. |
| `partial` | The source answered with some fields missing. | An enrolment with no lesson opened yet; a profile with no practice area. |
| `unavailable` | The source could not be reached, timed out, or the assembly budget elapsed first. | NARIC refused the connection. |
| `invalid` | The source answered in a shape UC-02 cannot use, or an adapter broke its contract. | A payload with no level field. |

`empty` and `unavailable` must never be collapsed. UC-07's gap report draws
conclusions from the difference: a learner with no history needs prompting, a
learner whose history service is down needs nothing except a retry.

---

## 10. What UC-02 expects from its callers

- Send the session id UC-01 created. Do not let UC-02 mint one in production.
- Authenticate the request; UC-02 resolves the user id itself and ignores the body.
- Call initialize once at session start. Calling it again is safe and cheap — it
  returns the stored context with `context_status: "existing"` and `200` instead of
  `201` — but it will not pick up upstream changes (A-29).
- Do not expect the full context. The response is a deliberate projection; the full
  object is private server-side data (see §11 of the scope, and `docs/assumptions.md`
  A-30).
- Read `context_version`. It changes when the shape changes.
