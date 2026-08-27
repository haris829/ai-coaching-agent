# Integration handoff

For the engineer who arrives once the real APIs exist, and for whoever assembles the
separately-developed UC repositories into one platform.

---

## Part 1 — What must be replaced when the real APIs arrive

Everything below is a **swap**, not a rewrite. `uc01/application` and `uc01/domain` are
untouched in every case.

| Real system | Replaces | Implement | Register in | Switch |
| --- | --- | --- | --- | --- |
| **NARIC** assessment API | `MockNaricAdapter` | `uc01/adapters/real/naric.py` → `NaricService` | `AppContainer._build_naric` | `UC01_NARIC_ADAPTER=real` |
| **Courses Agent** | `MockCoursesAdapter` | `uc01/adapters/real/courses.py` → `CoursesService` | `AppContainer._build_courses` | `UC01_COURSES_ADAPTER=real` |
| **Case Prep Agent** / case files | `MockCaseFileAdapter` | `uc01/adapters/real/cases.py` → `CaseFileService` | `AppContainer._build_cases` | `UC01_CASES_ADAPTER=real` |
| **Legal Foot Prints** (see below) | — | `uc01/adapters/real/profile.py` → `ProfileService`, if it owns the learner profile | `AppContainer._build_profile` | `UC01_PROFILE_ADAPTER=real` |
| Company **authentication** | `DevHeaderUserContextProvider` | `UserContextProvider` | `AppContainer._build_identity` | `UC01_IDENTITY_PROVIDER=<name>` |
| Company **database** | `SqliteSessionRepository` | `SessionRepository` | `AppContainer._build_repository` | `UC01_PERSISTENCE=<name>` |

Step-by-step procedure with code: [`ADAPTER_REPLACEMENT.md`](ADAPTER_REPLACEMENT.md).

### Where Legal Foot Prints fits

UC-01 needs exactly four things from the outside world: an explanation level (NARIC), a
course catalogue (Courses Agent), a case-file catalogue (Case Prep), and personalisation
(profile). Legal Foot Prints was not required by any UC-01 requirement, so **no speculative
adapter was built for it** — building one would be premature platform work.

When it arrives, it will land in one of three places, and none of them is a UC-01 change:

1. **It owns the learner profile / personalisation** → implement `ProfileService` in
   `uc01/adapters/real/profile.py`. Zero other changes.
2. **It owns learning content that should appear in the picker** → implement
   `CoursesService`, or an adapter that merges Courses Agent + Legal Foot Prints behind
   that one contract. UC-01 only ever sees `Course` and `Lesson`.
3. **It is a coaching-content source used after the session opens** → out of UC-01 scope.
   It belongs to whichever use case consumes it, with its own contract in that repository.

The same reasoning applies to any other integration that appears later: add a contract only
when a UC-01 requirement genuinely needs it.

### Contract-mapping cheat sheet

| Internal type | Fields UC-01 needs | Adapter must guarantee |
| --- | --- | --- |
| `NaricAssessment` | `state`, `level`, `assessed_at?`, `detail_code?` | `level` is `None` unless genuinely known. Never invent 5 — UC-01 applies and labels the default |
| `Course` | `course_id`, `title`, `lessons[]` | Only courses this user may open |
| `Lesson` | `lesson_id`, `course_id`, `title`, `ordinal` | Belongs to its course |
| `CaseFile` | `case_id`, `title`, `matter_reference?` | Only cases this user is authorised for |
| `UserProfile` | `user_id`, `display_name?`, `preferred_language?`, `current_course_id?`, `current_lesson_id?` | Empty rather than invented; a missing profile is not an error |
| `UserContext` | `user_id`, `tenant_id` | Derived from a verified credential, never from a request body |

### Non-negotiables to preserve

1. **Never invent a NARIC level in an adapter.** `naric_level_source` must stay truthful:
   `naric` only when NARIC actually supplied it.
2. **Authorization stays server-side, inside the adapter.** Missing and forbidden ids must
   be indistinguishable to the caller.
3. **No upstream payload shape escapes the adapter.** No upstream field name in routes,
   schemas, the service or the UI.
4. **No upstream error text reaches a user.** Contract exception + `technical_detail` for
   the log; the safe message comes from `uc01/domain/messages.py`.
5. **System prompts stay in `uc01/domain/prompts.py`** and are never client-supplied.
6. **The record-first guarantee holds.** A session record must exist for every open
   attempt, whatever a dependency does.

### Verification after each swap

```bash
python -m pytest                 # UC-01 suite still green against the mocks
python scripts/verify_states.py  # every interface state still renders
curl localhost:8000/api/v1/healthz   # confirms which adapters are live
```

Keep the mocks after the real adapters land: they are how the failure paths stay testable
in CI without a live external service. `UC01_*_ADAPTER` selects per dependency, so a
partly-integrated environment (real Courses, mock NARIC) is a supported configuration.

---

## Part 2 — Collecting this repository into the wider platform

Other use cases are being built separately, in different codebases. This repository was
built so that assembly is a wiring exercise.

### What UC-01 exposes to the platform

| Surface | Stability |
| --- | --- |
| `POST /api/v1/sessions`, `GET /api/v1/session-bootstrap`, `GET /api/v1/courses`, `GET /api/v1/case-files`, `GET /api/v1/sessions/{id}` | Versioned under `/api/v1`; documented in [`API.md`](API.md) and `/openapi.json` |
| `SessionRecord` fields: `session_id`, `user_id`, `session_type`, `linked_resource`, `naric_level`, `naric_level_source`, `status`, `timestamp` | The data other use cases will read |
| `session_events` rows | Append-only, generic `event_type` + `payload_json` |
| `SessionMode`, `SessionStatus`, `NaricLevelSource` enum values | Wire values are stable strings |

### What UC-01 requires from the platform

1. **A user identity** — one `UserContextProvider` implementation.
2. **The four external services** — four adapters.
3. **A session store** — one `SessionRepository` implementation, or leave UC-01's.

Nothing else. UC-01 does not import from, call, or assume the existence of any other UC.

### Three viable integration shapes

**A. Keep UC-01 as its own service (recommended first step)**

Deploy as-is. Other use cases call `/api/v1/...`. Replace the four adapters plus identity;
optionally point `SessionRepository` at the shared database so downstream use cases read
sessions directly.

*Pros:* smallest change, independent deploys, per-dependency rollout.
*Cons:* one more service to operate.

**B. Merge into a shared application (modular monolith)**

Copy `uc01/` in as a package and mount its router:

```python
from uc01.api.routes import router as uc01_router
from uc01.api.container import AppContainer

app.include_router(uc01_router)
app.state.container = AppContainer(uc01_settings)   # or platform-wide DI
```

Then reconcile at the seams:

* **Identity** — replace `DevHeaderUserContextProvider` with the platform's, and use the
  platform's dependency in `uc01/api/deps.py::get_current_user`.
* **Persistence** — implement `SessionRepository` over the shared database; keep
  `coaching_sessions` / `session_events` or map onto platform tables.
* **Adapters** — if the platform already has a Courses client, wrap it to satisfy
  `CoursesService`. Wrap, don't rewrite the service.
* **Errors** — either keep `register_error_handlers` for UC-01 paths or map
  `Uc01Error → platform error envelope` using the table in `uc01/api/errors.py`.
* **Config** — `UC01_*` variables can stay, or `load_settings()` can be replaced by a
  platform config loader that returns a `Settings`.

Because `uc01/domain` and `uc01/application` import nothing outside themselves, the merge
touches only `uc01/api/` and `uc01/adapters/`.

**C. Extract UC-01 as a library**

Drop `uc01/api` and `uc01/web`, keep `domain` + `contracts` + `application` + `persistence`,
and call `SessionInitiationService` directly from the platform's own HTTP layer. The
service takes all its collaborators via constructor injection, so no framework is implied.

### Naming collisions to expect when merging

| Risk | Mitigation |
| --- | --- |
| Two `Course` / `UserContext` types across repositories | UC-01's live in `uc01.domain.models`; import qualified, or map at the adapter boundary |
| Duplicate session tables | Either UC-01 keeps its own tables, or its repository maps onto the platform's |
| Duplicate `/courses` routes | UC-01's are under `/api/v1`; re-prefix with `include_router(prefix=...)` |
| Multiple config prefixes | `UC01_*` is namespaced already |
| Several structured-log formats | Replace `configure_logging` with the platform's handler; UC-01's events use dotted names and an `extra={"uc01": {...}}` envelope |

### Sequenced plan

1. **Inventory** — confirm which real APIs exist and which team owns each.
2. **Identity first** — swap `UserContextProvider`. Everything else depends on a real
   `user_id`.
3. **Persistence second** — swap `SessionRepository` so the records land where downstream
   use cases can read them.
4. **Adapters one at a time**, each with its own tests, flipped independently via
   `UC01_*_ADAPTER`. Run `scripts/verify_states.py` after each.
5. **Choose the topology** (A, B or C above) once the adapters are real.
6. **Re-run** `python -m pytest` and `scripts/verify_states.py` at every step; both are
   hermetic and depend on no external service.

### What must not happen during integration

* Do not add UC-02..UC-10 business logic to this repository — add a contract only if a
  UC-01 requirement needs it.
* Do not move authorization into the frontend or into a shared gateway *only*. UC-01
  re-validates server-side by design, and its tests assert that.
* Do not expose `diagnostics_json`, prompt bodies or dependency technical detail through
  any platform API.
* Do not delete the mock adapters. They are the CI substitute for external services.
