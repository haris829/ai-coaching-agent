# UC-01 API reference

Base path: `/api/v1`. Live, generated spec: `/docs` (Swagger UI) and `/openapi.json`.

All request bodies are validated by Pydantic with `extra="forbid"`: any field not listed
below is rejected with **422**, so an attempt to send a privileged value is visible rather
than silently ignored.

---

## Authentication

Every endpoint except `/api/v1/healthz` requires a caller identity, resolved
**server-side** from a header. No endpoint accepts a user id as input.

```
Authorization: Bearer <dev-token>
X-Dev-User: <dev-token>            # alternative
```

Development tokens: `dev-alice`, `dev-bob`, `dev-carol`. Missing or unknown → **401**
`authentication_required`.

This is the development provider (`DevHeaderUserContextProvider`) behind the
`UserContextProvider` contract. Replacing it with the company auth system does not change
any endpoint.

---

## Error envelope

Every failure — validation, authorization, dependency outage, unexpected crash — returns
the same shape:

```json
{
  "error": { "code": "session_mode_unavailable", "message": "Courses are temporarily unavailable." },
  "recovery": {
    "session_id": "sess_53da675603b3477cb7e016f8bed3ce89",
    "available_modes": ["free-form", "case-linked"],
    "suggested_mode": "free-form"
  }
}
```

* `error.code` — stable machine code, safe to branch on.
* `error.message` — safe, non-technical text, suitable for direct display.
* `recovery` — present when the user can still do something else (mode rejections).
* `fields` — present on 422; each entry is `{location, type, message}`.
* `debug` — present **only** when `UC01_DEV_MODE=true` **and**
  `UC01_EXPOSE_ERROR_DETAILS=true`. Never in any other configuration.

Never present anywhere: tracebacks, exception class names, SQL, upstream error text,
URLs, keys. Those go to the structured log instead.

### Status codes

| Code | Meaning | `error.code` examples |
| --- | --- | --- |
| 400 | The selection does not fit the mode, or a required selection is missing | `selection_required`, `selection_not_allowed` |
| 401 | Caller could not be identified | `authentication_required` |
| 403 | The selected course / lesson / case is not accessible to this user | `selection_not_accessible` |
| 404 | Session not found **or** not owned by the caller (deliberately indistinguishable) | `session_not_found` |
| 409 | The requested mode is not available right now | `session_mode_unavailable` |
| 422 | Request body failed validation, including unknown/privileged fields | `invalid_request` |
| 500 | Unexpected internal failure (the session record is still written) | `session_initialization_failed`, `internal_error` |
| 503 | A dependency required mid-flow became unavailable | `dependency_unavailable` |

---

## `GET /api/v1/session-bootstrap`

Everything the coaching interface needs before a session is opened. **Never fails because
a dependency is down** — an outage becomes availability metadata.

**Query parameters**

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `continue_without_calibration` | bool | `false` | Reflects the user's choice so the preview and notices match what opening will do |

**200 response**

```json
{
  "user_id": "u_alice",
  "display_name": "Alice Osei",
  "personalisation_available": true,
  "modes": [
    { "mode": "free-form",     "available": true,  "reason": null },
    { "mode": "course-linked", "available": false, "reason": "Courses are temporarily unavailable." },
    { "mode": "case-linked",   "available": false, "reason": "No accessible case files." }
  ],
  "courses": [
    { "course_id": "crs_contract_law", "title": "Contract Law Foundations",
      "lessons": [ { "lesson_id": "lsn_offer", "title": "Offer and Acceptance", "ordinal": 1 } ] }
  ],
  "case_files": [
    { "case_id": "case_alpha", "title": "Alpha Holdings v. Brookfield", "matter_reference": "AH-2026-0142" }
  ],
  "naric": {
    "level": 5,
    "source": "default",
    "is_fallback": true,
    "offer_continue_without_calibration": true,
    "notice": "NARIC calibration is unavailable right now. You can continue without calibration — your coaching explanations will use Level 5 by default."
  },
  "dependencies": [
    { "name": "profile", "state": "available" },
    { "name": "naric",   "state": "unavailable" },
    { "name": "courses", "state": "available" },
    { "name": "cases",   "state": "empty" }
  ],
  "notices": [
    { "code": "naric_calibration_unavailable", "message": "…", "severity": "warning",
      "action": "continue_without_calibration" }
  ],
  "greeting_preview": { "text": "Hi Alice Osei! …", "variant": "personalised.free_form", "personalised": true },
  "integrations": {
    "using_mock_adapters": true,
    "adapters": { "naric": "mock", "courses": "mock", "cases": "mock", "profile": "mock",
                  "identity": "dev", "persistence": "sqlite" },
    "warning": "Development mocks are active: … fixtures, not real integrations."
  }
}
```

**Field notes**

* `modes` is always the three modes in a fixed order. `free-form` is always `available`.
* `reason` is non-null only for a disabled mode, and is the exact text to show the user.
* `naric.source` is `naric`, `default`, or `default_user_acknowledged`. `is_fallback` is
  `false` only when the level genuinely came from NARIC.
* `dependencies[].state` is `available` | `empty` | `incomplete` | `unavailable`. No
  technical detail is included.
* `notices[].action` is a machine-readable affordance: `continue_without_calibration` or
  `retry`.
* `display_name` is `null` whenever personalisation is unavailable or incomplete. A name
  is never invented.

**Errors:** 401 only.

---

## `GET /api/v1/courses`

Courses the caller may open, each with its lessons nested (there is no separate lessons
endpoint — the picker gets both in one call).

**200 response**

```json
{ "available": true, "reason": null, "courses": [ /* CourseOut[] */ ] }
```

An outage or an empty catalogue is **200 with `available: false`** and a reason, so the
picker renders a disabled state instead of an error page:

```json
{ "available": false, "reason": "Courses are temporarily unavailable.", "courses": [] }
{ "available": false, "reason": "You do not have any courses available yet.", "courses": [] }
```

The listing only ever contains courses accessible to the caller — enforced in the adapter,
not in the UI.

**Errors:** 401 only.

---

## `GET /api/v1/case-files`

Same shape and same rules as `/courses`.

```json
{ "available": true, "reason": null,
  "case_files": [ { "case_id": "case_alpha", "title": "…", "matter_reference": "AH-2026-0142" } ] }
```

```json
{ "available": false, "reason": "No accessible case files.", "case_files": [] }
{ "available": false, "reason": "Case files are temporarily unavailable.", "case_files": [] }
```

Having no accessible case files is **not** an error condition for the interface.

**Errors:** 401 only.

---

## `POST /api/v1/sessions`

Open a coaching session. A session record is written **before** any dependency is
contacted, so every attempt is persisted — including attempts that are then rejected or
that fail unexpectedly.

**Request**

```json
{
  "mode": "course-linked",
  "course_id": "crs_contract_law",
  "lesson_id": "lsn_consideration",
  "case_id": null,
  "continue_without_calibration": false,
  "on_dependency_failure": "fail"
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `mode` | `free-form` \| `course-linked` \| `case-linked` | yes | Any other value → 422 |
| `course_id` | string (≤128) | for `course-linked` | Re-validated for accessibility server-side |
| `lesson_id` | string (≤128) | for `course-linked` | Must belong to the selected course |
| `case_id` | string (≤128) | for `case-linked` | Re-validated for accessibility server-side |
| `continue_without_calibration` | bool | no | Records that the user was informed; never gates opening |
| `on_dependency_failure` | `fail` \| `fallback_free_form` | no (`fail`) | `fallback_free_form` opens a degraded free-form session instead of rejecting |

**Not accepted (422):** `user_id`, `naric_level`, `naric_level_source`,
`explanation_level`, `status`, `session_id`, `system_prompt`, `prompt`, `guardrails`,
`instructions`, `greeting`, or any other unknown field.

**Cross-field rules (400)**

* `free-form` must not carry `course_id`, `lesson_id` or `case_id`.
* `course-linked` must not carry `case_id`; `case-linked` must not carry `course_id` /
  `lesson_id`.
* `course-linked` requires both `course_id` and `lesson_id`; `case-linked` requires
  `case_id`.

**201 response**

```json
{
  "session": {
    "session_id": "sess_da5c95444e2b478cb4a39d3e149f1ae3",
    "user_id": "u_alice",
    "session_type": "course-linked",
    "status": "active",
    "requested_mode": "course-linked",
    "downgraded_from": null,
    "linked_resource": {
      "type": "course", "id": "crs_contract_law", "label": "Contract Law Foundations",
      "secondary_id": "lsn_consideration", "secondary_label": "Consideration"
    },
    "naric_level": 8,
    "naric_level_source": "naric",
    "explanation_level": 8,
    "degraded_dependencies": [],
    "failure_code": null,
    "created_at": "2026-08-24T21:24:19.300354+00:00",
    "updated_at": "2026-08-24T21:24:19.301122+00:00"
  },
  "greeting": {
    "text": "Hi Alice Osei! Welcome back to your coaching session. We are working on Consideration from Contract Law Foundations. Explanations are calibrated to your NARIC Level 8.",
    "variant": "personalised.course_linked",
    "personalised": true
  },
  "context": {
    "session_mode": "course-linked",
    "downgraded_from": null,
    "course": { "course_id": "crs_contract_law", "title": "…", "lessons": [ … ] },
    "lesson": { "lesson_id": "lsn_consideration", "title": "Consideration", "ordinal": 2 },
    "case_file": null,
    "naric": { "level": 8, "source": "naric", "is_fallback": false,
               "offer_continue_without_calibration": false, "notice": null },
    "personalisation_available": true,
    "degraded_dependencies": []
  },
  "notices": []
}
```

`session.status`:

| Status | When |
| --- | --- |
| `active` | Everything the session needed was available |
| `degraded` | The session is usable, but something was missing: a defaulted NARIC level, unavailable/incomplete profile, or a mode downgrade |
| `failed` | The attempt was rejected or crashed. The record still exists; the API returns 4xx/5xx |
| `initializing` | Transient, written before dependencies are contacted; only observable if the process dies mid-open |

**Not returned:** `diagnostics`, dependency technical details, `system_prompt_id`,
`system_prompt_version`, prompt bodies. Those are persisted/logged server-side only.

**Failure behaviour**

| Situation | HTTP | Record status | Notes |
| --- | --- | --- | --- |
| Mode's dependency unavailable, `on_dependency_failure=fail` | 409 | `failed` | `recovery.available_modes` lists what still works |
| Mode's dependency unavailable, `on_dependency_failure=fallback_free_form` | 201 | `degraded` | `session_type=free-form`, `downgraded_from=course-linked` |
| No accessible case files, `case-linked` requested | 409 | `failed` | `error.message = "No accessible case files."` |
| Course / lesson / case not accessible | 403 | `failed` | Same message for missing and forbidden |
| NARIC unavailable / incomplete / invalid | 201 | `degraded` | Level 5, `source="default"`. **Never blocks** |
| Profile unavailable / incomplete | 201 | `degraded` | Generic greeting + notice |
| Greeting layer raises | 201 | `degraded` | Safe fallback greeting; the failure is logged |
| Persistence unavailable | 500 | — | Safe message; the technical error is logged |

---

## `GET /api/v1/sessions/{session_id}`

Read one of the caller's own sessions. Response is the same `session` object as above.

A session belonging to another user returns **404** with a body byte-identical to a
genuinely unknown id, so session ids cannot be probed.

**Errors:** 401, 404.

---

## `GET /api/v1/healthz`

No authentication. States plainly whether mock adapters are in use.

```json
{
  "status": "ok",
  "use_case": "UC-01 Coaching Session Initiation",
  "environment": "development",
  "persistence": "sqlite",
  "integrations": { "using_mock_adapters": true, "adapters": { … }, "warning": "…" }
}
```

`GET /healthz` (unversioned) is a minimal alias for infrastructure probes.

---

## `GET /api/v1/dev/context`

**Development only.** Returns 404 when `UC01_DEV_MODE=false`. Powers the reference UI's
user switcher and scenario panel.

```json
{
  "users": [ { "user_id": "u_alice", "token": "dev-alice", "label": "Alice (full access)" } ],
  "scenarios": { "naric": "per_user", "courses": "available", "cases": "available", "profile": "available" },
  "scenario_options": { "naric": ["success","incomplete","calibrating","unavailable","invalid","per_user"], … },
  "scenario_header_enabled": true
}
```

---

## `X-Dev-Scenarios` request header

**Development only.** Selects mock fixtures per request so every UI state can be
exercised:

```
X-Dev-Scenarios: courses=unavailable,naric=incomplete,profile=unavailable
```

* Ignored unless `UC01_DEV_MODE=true` and `UC01_DEV_SCENARIO_HEADER=true`.
* Unknown keys and values are ignored — a malformed header is never a 500.
* It can only choose between mock fixtures. It cannot affect identity, authorization, or
  the recorded `naric_level_source`, and it has no effect once an adapter is set to `real`.

Values are listed in [`MOCKS.md`](MOCKS.md).

---

## Endpoints deliberately absent

No endpoint exists for asking a question, rating an answer, "explain differently",
analytics, or any other future use case. `POST /api/v1/sessions` emits the fields those
use cases will need (see [`PERSISTENCE.md`](PERSISTENCE.md)), but implements none of their
behaviour.
