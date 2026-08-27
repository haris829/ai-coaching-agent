# UC-06 published contract

For an integration engineer who has not read the code.

Every field is marked **[COMPANY]** — specified by the company in the scope
document — or **[ASSUMED]** — invented by us, with a row in
`docs/assumptions.md`. Treat every **[ASSUMED]** field as provisional.

---

## 1. What UC-06 is

Case-linked legal advice **coaching**. A learner links a coaching session to a
real case file and asks how the law applies to the facts of that matter. UC-06
explains it as education. It never advises, never predicts an outcome, and never
emits a response without its disclaimer.

It owns case-linked coaching only. It does not create sessions, assemble learner
context, run lessons or quizzes, hold dialogue state, produce gap reports,
streaks or summaries, or handle feedback ratings.

---

## 2. Session identity **[COMPANY]**

UC-06 **receives** an opaque `session_id`. **It never creates one** on a
production path.

- The value is opaque: never parsed, never validated for structure, never
  assumed to encode anything.
- Dev-mode minting exists behind `ALLOW_DEV_SESSION_IDS`, which defaults to
  `false`. When it is off, a request without `session_id` is refused with
  `session_id_required`.
- A minted dev session is prefixed `dev-session-` so it is recognisable in logs.

---

## 3. Closed vocabularies **[COMPANY]**

### NARIC level
`LEVEL_3` `LEVEL_4` `LEVEL_5` `LEVEL_6` `LEVEL_7` `LEVEL_7_PLUS`

A value mapping to no member is an **invalid response**, not a level. An adapter
must raise `ProviderInvalidResponse` — never round to a neighbour, never
substitute the default. UC-06 then applies the default and marks the source
accordingly, and that distinction is only honest if adapters keep it.

### NARIC level source
`retrieved` `default`

### Explanation profile
| NARIC level | Profile | |
|---|---|---|
| `LEVEL_3` | `basic` | **[COMPANY]** |
| `LEVEL_4` | `basic` | **[ASSUMED]** — A-07 |
| `LEVEL_5` | `intermediate` | **[COMPANY]** |
| `LEVEL_6` | `intermediate` | **[ASSUMED]** — A-07 |
| `LEVEL_7` | `advanced` | **[COMPANY]** |
| `LEVEL_7_PLUS` | `advanced` | **[COMPANY]** |

Platform default when context cannot be retrieved: **`LEVEL_5`**, source
`default`. The question is still answered, and the disclaimer is intact. A
context failure never removes a safety control.

### Source status
| Value | Meaning |
|---|---|
| `available` | The source responded and carried usable content. |
| `empty` | The source responded successfully and legitimately held nothing. |
| `partial` | The source responded; some expected sections were absent. |
| `unavailable` | The source could not be reached or refused. We hold no knowledge. |
| `invalid` | The source responded with a shape or value we refuse to map. |

`empty` and `unavailable` are **different states and must never be conflated.**
Empty means we know there is nothing. Unavailable means we know nothing.

### Guard vocabulary
`none` `outcome_prediction` `litigation_strategy`

### Response mode
`case_linked` — a fact-linked educational explanation.
`general_fallback` — degraded, **not** case-linked, carries no case facts. **[ASSUMED]**

### Rating state
`pending` `rated` — UC-06 only ever writes `pending` (A-17).

---

## 4. The disclaimer **[COMPANY]**

### The canonical text

```
This response is provided for educational and training purposes only. It does not constitute legal advice. Always consult a qualified legal professional before acting on any legal matter.
```

Defined exactly once, as `CANONICAL_DISCLAIMER` in
`uc06/domain/disclaimer.py`. There is no second copy of this literal in
non-test source, and a test asserts that.

> ⚠ **UNRESOLVED — see `docs/assumptions.md` A-01.** The scope document states
> this disclaimer twice with **different wording**. The Overview gives the
> three-sentence text above; UC-06 step 5 gives a shortened two-sentence form
> omitting the final sentence. We use the Overview's. **The company must confirm
> which is canonical before release.** For a string the specification calls
> non-negotiable and verifies by automated scan, this ambiguity is not resolved
> by our having chosen.

### The three layers enforcing it

| Layer | Where | What it does | How it fails independently |
|---|---|---|---|
| **1. Type** | `uc06/domain/responses.py` | `disclaimer` is `field(init=False)`, stamped in `__post_init__` from the constant, on a `frozen` dataclass. `to_payload()` writes it after the subclass body. | Catches business logic omitting it — there is no parameter to omit and no setter to blank. Cannot catch a defect in serialisation, because it never sees the payload. |
| **2. Serialisation boundary** | `uc06/application/boundary.py::check_payload` | Runs on the raw outgoing mapping. Exact byte comparison; also refuses any payload carrying a disclaimer-suppression key. | Catches serialisation defects and tampering downstream of the type. Does not consult the response object, so a well-formed object cannot rescue a corrupt payload. |
| **3. Output scan** | `tests/test_output_scan.py` | Scans emitted HTTP response bodies for the exact string across every path — success, guard, degraded, error, and boundary-failure. | Catches anything the first two miss, including a boundary check wired past. Runs in CI, not in the request path. |

### No suppression path exists

- **No configuration key** affects the disclaimer — not a flag, environment
  variable, admin setting, request parameter or test mode. A flag defaulted to
  `false` is still a suppression path. The absence is the guarantee and
  `tests/test_config_surface.py` asserts it by scanning the whole configuration
  surface, by name and by effect.
- **The generator never produces it.** Prompts explicitly instruct the model not
  to write a disclaimer; its output is never scanned for one; anything
  disclaimer-shaped it emits is captured in `supplied_disclaimer`, stripped from
  the content, and discarded.
- **Prompt injection cannot reach it.** Learner text is data. A suppression
  attempt is recorded as a security incident and the question is then answered
  normally, with the disclaimer intact.
- **Request fields cannot reach it.** The request schema is `extra="forbid"`; a
  `disclaimer` or `suppress_disclaimer` field produces a visible `422` and a
  security incident.

### On failure, it fails closed

If a payload reaches the boundary without the exact text:

1. **The response is not emitted.** The learner receives a safe error carrying
   the canonical disclaimer. Status `503`, code `response_withheld`.
2. **The session is halted.** Every further case-linked response in that session
   is refused (`409`, `session_halted`) until cleared.
3. **A critical defect is logged** — identifiers and reason code only.
4. **The platform admin is alerted** through `AdminAlertSink.critical()`, with
   full technical detail and no case content.
5. **A security incident is recorded** through `SecurityIncidentSink.record()`,
   because a case-linked response constructed without its disclaimer is a
   security event, not merely a bug.

---

## 5. Records UC-06 writes

### Interaction log record **[COMPANY]**

Written through `InteractionLogRepository.append()`.

| Field | Type | Notes |
|---|---|---|
| `interaction_id` | `str` | UUID hex. |
| `session_id` | `str` | As received. |
| `user_id` | `str` | Resolved server-side. |
| `asked_at` | `datetime` (UTC, tz-aware) | |
| `question_class` | `str` | `case_linked_explanation` \| `outcome_prediction_redirect` \| `litigation_strategy_redirect` \| `general_topic_fallback` **[ASSUMED]** A-11 |
| `topic_tag` | `str` | Resolved legal topic, e.g. `duress`. **[ASSUMED]** |
| `naric_level` | `NaricLevel` | |
| `response_id` | `str` | Matches the emitted response. |
| `mode` | `"case_linked"` \| `"general_fallback"` | `case_linked` is **[COMPANY]**. |
| `case_file_id` | `str \| None` | `None` only on a fallback with no case file. |
| `case_facts_referenced` | `tuple[str, ...]` | **Identifiers only.** Never text. |
| `guard_triggered` | `"outcome_prediction"` \| `"litigation_strategy"` \| `None` | |
| `disclaimer_present` | `True` | Always. There is no path that writes `False`. |
| `rating_state` | `"pending"` \| `"rated"` | UC-06 writes `pending` only. |

**There is no `question_text` field, and there will not be one.** A question
about a live matter is itself sensitive. A test asserts the field does not exist.

### Audit record **[ASSUMED]** — A-18

Access, not content: that case-linked coaching occurred, which case file, which
user, when. **Never what was discussed.**

| Field | Type | Notes |
|---|---|---|
| `audit_id` | `str` | |
| `occurred_at` | `datetime` (UTC) | |
| `action` | `str` | `case_linked_coaching`, `case_linked_coaching_refused`, `case_linked_coaching_degraded`, `case_linked_coaching_failed` |
| `user_id`, `session_id`, `case_file_id` | `str` / `str \| None` | Identifiers. |
| `outcome` | `str` | `answered`, `guard_redirected`, `access_denied`, `origin_rejected`, `session_halted`, `fabricated_fact_reference`, `degraded_*`, `generation_*` |
| `source_status` | `SourceStatus \| None` | |

### Security incident **[ASSUMED]** — A-12

`incident_id`, `occurred_at`, `kind`, `session_id`, `user_id`, `case_file_id`,
`matched_rule_ids`, `detail_code`, `metadata`.

`kind` ∈ `prompt_disclaimer_suppression`, `request_field_suppression`,
`internal_disclaimer_absent`, `internal_disclaimer_altered`,
`unauthorised_case_access`.

**Carries no question text and no case fact text** — classification and rule
identifiers only.

### Admin incident **[ASSUMED]**

`incident_id`, `occurred_at`, `severity`, `code`, `session_id`, `user_id`,
`case_file_id`, `technical_detail`, `remediation`. Full technical detail for the
responder; no case content.

---

## 6. `CaseFile` — the shape expected **[ASSUMED]** — A-02, A-03

```python
CaseFile(
    case_file_id:      str,
    origin_system:     str,                      # must equal "case_prep_agent"
    practice_area:     str,
    charges:           tuple[Charge, ...],
    facts:             tuple[CaseFact, ...],
    evidence:          tuple[EvidenceItem, ...],
    legislation_notes: tuple[LegislationNote, ...],
    source_status:     SourceStatus,
)

CaseFact(fact_id: str, text: str, category: str = "general")
Charge(charge_id: str, label: str, statute_reference: str | None = None)
EvidenceItem(evidence_id: str, label: str, linked_fact_ids: tuple[str, ...] = ())
LegislationNote(note_id: str, citation: str, summary: str)
```

Frozen: UC-06 does not mutate what it reads. Collections are tuples.

### The fact identifier scheme

`fact_id` is an **opaque, stable, unique string**. It is never parsed and no
format is assumed — mocks use `F-001`, the foreign family uses `p.1`, and the
service treats both identically. **Stable** means the same fact carries the same
identifier across reads, because explanations are verified against these
identifiers and logs record only these identifiers.

> **If your system has no stable per-fact identifier, stop and raise it.** An
> adapter must never synthesise one: a synthesised identifier makes
> fact-reference verification meaningless while appearing to work. That is a
> contract conversation, not an adapter workaround.

### Origin verification **[ASSUMED]** — A-05

`origin_system == "case_prep_agent"`, checked **before any of the content is
used**. A case file from elsewhere is refused (`409`, `case_origin_rejected`) and
the generator is never called. Confirm the real mechanism before release: if
origin is genuinely established by a signature or sealed envelope, a string
comparison is not the check the specification intends.

---

## 7. `LearnerContext` — the shape expected **[ASSUMED]**

```python
LearnerContext(
    session_id:         str,
    user_id:           str,
    naric_level:       NaricLevel,        # platform enum, normalised in the adapter
    naric_level_source: NaricLevelSource,  # "retrieved" | "default"
    source_status:     SourceStatus,
    practice_area:     str | None = None,  # None means absent, never a guess
    case_linked_mode:  bool = True,
    case_file_id:      str | None = None,  # if present, must match the request
)
```

On failure: UC-06 applies `LEVEL_5` / `default`, records the status
(`unavailable` or `invalid`), and **still answers**. Because case-linked mode
cannot be confirmed, that answer is a general-topic fallback carrying no case
facts (A-10).

---

## 8. Halt semantics

| | |
|---|---|
| **What halts** | Case-linked coaching for **one `session_id`**. Nothing global, nothing per user, nothing per case file. |
| **What sets it** | Only a disclaimer boundary failure (§4). Nothing else halts a session. |
| **What it blocks** | Every subsequent case-linked question in that session: `409`, code `session_halted`, `session_halted: true`, disclaimer present. The case file is never read and the generator is never called. |
| **What it does not block** | Reading session status. Other sessions. Anything outside UC-06. |
| **How it clears** | `SessionHaltRepository.clear(session_id)` only. **There is deliberately no endpoint and no configuration key.** **[ASSUMED]** — A-06: the procedure and the authorised role are unspecified by the company. Investigate the serialisation path before clearing. |
| **Visibility** | `GET /api/v1/case-coaching/sessions/{id}/status` returns `case_linked_coaching_halted`, `halt_reason_code`, `halted_at`. |

---

## 9. Ports

```python
CaseFileProvider          get_case_file(case_file_id) -> CaseFile          # READ ONLY
                          verify_read_access(user_id, case_file_id) -> AccessRecord
LearnerContextProvider    get_context(session_id, user_id) -> LearnerContext
AnswerGenerator           generate(request: GenerationRequest) -> GenerationResult
GuardClassifier           classify(question) -> GuardResult
InteractionLogRepository  append(record) / get(id) / list_for_session(session_id)
SessionHaltRepository     halt(session_id, reason) / is_halted(session_id) / clear(session_id) / get(session_id)
AdminAlertSink            critical(incident: AdminIncident)
SecurityIncidentSink      record(incident: SecurityIncident)
CurrentUserProvider       resolve(headers: Mapping[str, str]) -> user_id
```

Every port raises **only**: `ProviderUnavailable`, `ProviderTimeout`,
`ProviderInvalidResponse`. No upstream exception type, error text, payload shape
or provider name may cross the boundary.

`CaseFileProvider` is read-only **structurally** — no create, update, delete,
patch or write method exists on the port or on any adapter, and
`tests/test_readonly_architecture.py` walks the registry to assert it for every
registered adapter, including ones not yet written.

---

## 10. API

### `POST /api/v1/case-coaching/questions`

```json
{ "question": "…", "case_file_id": "…", "session_id": "…" }
```

`extra="forbid"`. Sending `disclaimer`, `naric_level`, `guard_triggered`,
`system_prompt`, `user_id` or any other field produces `422` with the field
named — never a silent ignore. Suppression-shaped field names also record a
security incident. `user_id` is resolved server-side and never read from the
body.

**200 response**

```json
{
  "response_id": "…", "session_id": "…", "mode": "case_linked",
  "case_file_id": "…", "explanation_profile": "advanced",
  "naric_level": "LEVEL_7", "naric_level_source": "retrieved",
  "content": "…", "case_facts_referenced": ["F-001", "F-002"],
  "guard_triggered": null, "case_file_status": "available",
  "learner_context_status": "available", "topic_tag": "duress",
  "notice": null,
  "disclaimer": "This response is provided for educational and training purposes only. …"
}
```

### `GET /api/v1/case-coaching/sessions/{session_id}/status`

Halt state for a caller to render. Carries no case content, and carries the
disclaimer. `403 session_not_visible` for a session belonging to another user.

### `GET /api/v1/healthz`

`{"status": "ok"}`. The only endpoint that does not carry the disclaimer — it is
not a case-coaching response.

### Error envelope

```json
{
  "error": { "code": "…", "message": "…", "request_id": "…",
             "retryable": false, "session_halted": false },
  "disclaimer": "…"
}
```

| Code | Status | Retryable |
|---|---|---|
| `invalid_request` | 422 | no |
| `identity_unavailable` | 401 | no |
| `session_id_required` | 400 | no |
| `case_access_denied` | 403 | no |
| `session_not_visible` | 403 | no |
| `case_origin_rejected` | 409 | no |
| `session_not_case_linked` | 409 | no |
| `case_file_not_linked_to_session` | 409 | no |
| `session_halted` | 409 | no |
| `generation_invalid` | 502 | no |
| `generation_unavailable` | 503 | yes |
| `response_withheld` | 503 | no |
| `generation_timeout` | 504 | yes |
| `internal_error` | 500 | no |

No internal exception text, stack trace, provider name, prompt content or case
content reaches a client on any path.

---

## 11. Privacy rules a caller can rely on

- Case fact **text** never appears in application logs, audit records, security
  incidents, admin alerts, error messages or stack traces. Identifiers only.
- **Question text is never logged anywhere.**
- Audit logging records access, not content.
- No endpoint returns another user's interactions or case content the user cannot
  already access.
- Prompt content, system instructions and generator configuration are never
  returned to a client.
- Fact text **may** appear in a response body to a reader who holds verified read
  access to that case file (A-13).

Asserted across the whole test suite: `tests/test_privacy.py` plus a
session-final scan in `tests/conftest.py::pytest_sessionfinish` that checks every
log line emitted by every test against every case-fact string and every question
the suite sent.

---

## 12. Extension points — where behaviour UC-06 does not own attaches

| Extension point | How to attach | What UC-06 guarantees |
|---|---|---|
| Real case file system | New adapter + one registry line + `CASE_FILE_PROVIDER` | Read access re-verified every request; read-only; origin checked before use. |
| Real learner context | New adapter + one registry line + `LEARNER_CONTEXT_PROVIDER` | Enum normalisation; failure defaults to `LEVEL_5`/`default` and still answers. |
| Real model provider | New adapter + one registry line + `ANSWER_GENERATOR` — **and a confidentiality sign-off** | Prompts stay server-side; output is verified for fabricated facts and scanned for predictions; the disclaimer never comes from the model. |
| Real guard classifier | New adapter + one registry line + `GUARD_CLASSIFIER` | The in-domain rule set remains the fallback, so the guard cannot be skipped by an outage. Must pass the guard conformance suite, whose bar is the fallback's own behaviour. |
| Durable interaction log / halt store | New adapter + one registry line + `INTERACTION_LOG_REPOSITORY` / `SESSION_HALT_REPOSITORY` | Records carry identifiers only. |
| Real alerting / SIEM | New adapter + one registry line + `ADMIN_ALERT_SINK` / `SECURITY_INCIDENT_SINK` | Security incidents stay distinct from application logs. |
| Real authentication | New adapter + one registry line + `CURRENT_USER_PROVIDER` | `user_id` is resolved from request metadata, never from the body. |
| Feedback rating | Consume the interaction log; transition `rating_state`. UC-06 writes `pending` and never transitions it. | `response_id` on the record matches the emitted response. |
| Session creation / learner-context assembly | Owned elsewhere. UC-06 receives `session_id` and never creates one. | Opaque handling; server-side verification of case-linked mode. |
| Halt clearing workflow | `SessionHaltRepository.clear()`. **No endpoint is provided** — see A-06. | The halt holds until explicitly cleared. |
| Legal content library | Replace `uc06/domain/legal_tests.py`, which is versioned (`LEGAL_TEST_LIBRARY_VERSION`). **[ASSUMED]** A-08 — requires a qualified author. | Redirects stay substantive and are never generated by the model. |

**Not extension points, by design:** the disclaimer and the guard redirect. There
is no configuration key, request field or registry entry that alters either, and
tests assert their absence.
