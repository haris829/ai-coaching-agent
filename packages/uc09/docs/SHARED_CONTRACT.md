# Shared contract — UC-09 Session Summary & Export

**Audience:** an integration engineer holding a component they have never seen,
who has not read its code.

This document describes what UC-09 **emits** to a platform it cannot see, what
it **expects** to receive, the vocabularies it uses in full, how it handles
session identity, and every point where behaviour it does not own attaches. It
is not a guide to working on the repository — that is `README.md` — and it is
not the integration procedure — that is `INTEGRATION.md`.

Every field below is marked:

- **[SPEC]** — specified by the company.
- **[ASSUMED]** — invented by us, with an ID into `assumptions.md`. Confirm it
  against your real system before relying on it.

---

## 1. What this component is

It reads one recorded coaching session and produces one **summary record**,
which it owns, and which can be exported as a **CPD evidence document** in HTML
and PDF.

It does nothing else. It does not create sessions, coach, answer questions,
perform gap analysis, track streaks, or collect feedback.

**It never writes to any upstream system.** Every upstream port declares
retrieval methods only, and an architecture test asserts that no upstream port
or registered adapter exposes a mutating method.

### The guarantee that matters

Every claim in an emitted summary is traceable to recorded session data. A
topic appears only if it is in the interaction tag record; an authority appears
only if it is in the session's citation record. Content that cannot be traced
is **rejected whole** — never trimmed and stored — and the summary falls back to
a question log that is explicitly marked as not being a full summary.

Consumers may rely on this: **if it is in the document, the session record says
it happened.**

---

## 2. Records this component writes

### 2.1 `SummaryRecord`

Owned by this component. Nothing else on the platform writes it.

| Field | Type | Spec | Notes |
|---|---|---|---|
| `summary_id` | `string` | [SPEC] | Format `sum_<32 hex>` [ASSUMED A-029]. Opaque. Not derivable from `session_id`. |
| `session_id` | `string` | [SPEC] | Echoed back exactly as received. |
| `user_id` | `string` | [SPEC] | Owner. Taken from the session record, never from a request. |
| `generated_at` | `datetime` (ISO 8601, UTC) | [SPEC] | |
| `is_partial` | `boolean` | [SPEC] | |
| `covers_interactions_through` | `datetime` (ISO 8601, UTC) | [SPEC] | Generation moment when partial; session end when complete [ASSUMED A-021]. |
| `topics_covered` | `Topic[]` | [SPEC] | |
| `key_concepts` | `Concept[]` | [SPEC] | 0–5 items. |
| `resources_referenced` | `Resource[]` | [SPEC] | |
| `next_steps` | `Suggestion[]` | [SPEC] | 0–3 items. |
| `source_status` | `map<string, SourceStatus>` | [SPEC] | Key set is [ASSUMED A-027]; see §4.3. |
| `generation_mode` | `"generated" \| "question_log_fallback"` | [SPEC] | |
| `session_status` | `"summary_generated"` | [SPEC] | Always this value on an emitted record. |
| `user_display_name` | `string` | [ASSUMED A-028] | Required on the PDF. |
| `session_started_at` | `datetime` | [ASSUMED A-028] | |
| `session_ended_at` | `datetime \| null` | [ASSUMED A-028] | |
| `session_duration_seconds` | `integer ≥ 0` | [ASSUMED A-028] | Elapsed-to-cover-moment when partial [ASSUMED A-022]. |
| `naric_level` | `NaricLevel` | [ASSUMED A-028] | Carried through from the session. |
| `naric_level_source` | `"retrieved" \| "default"` | [ASSUMED A-028] | |
| `explanation_profile` | `"basic" \| "intermediate" \| "advanced"` | [ASSUMED A-028] | Derived; see §4.2. |
| `section_notes` | `map<string, string>` | [ASSUMED A-028] | Learner-facing explanations; see §2.6. |
| `question_log` | `QuestionLogEntry[]` | [ASSUMED A-028] | Non-empty **only** in fallback mode. |

### 2.2 `Topic`

| Field | Type | Spec | Notes |
|---|---|---|---|
| `topic_id` | `string` | [ASSUMED A-009] | Exactly a tag from the interaction record. |
| `label` | `string` | [ASSUMED A-010] | Display only. Never a source of truth. |
| `interaction_count` | `integer ≥ 1` | [ASSUMED A-009] | Must equal the number of interactions carrying the tag. |
| `first_discussed_at` | `datetime` | [ASSUMED A-009] | |
| `last_discussed_at` | `datetime` | [ASSUMED A-009] | |

### 2.3 `Concept`

| Field | Type | Spec | Notes |
|---|---|---|---|
| `concept_id` | `string` | [ASSUMED A-009] | Exactly a concept tag from the interaction record. |
| `label` | `string` | [ASSUMED A-010] | |
| `explanation` | `string` | [ASSUMED A-009] | Depth varies with `explanation_profile`. |
| `topic_id` | `string` | [ASSUMED A-009] | Must be a `topic_id` in `topics_covered`. |
| `evidence_interaction_ids` | `string[]`, non-empty | [ASSUMED A-009] | Every id must exist in the session. **This is the audit trail.** |

### 2.4 `Resource`

| Field | Type | Spec | Notes |
|---|---|---|---|
| `resource_id` | `string` | [ASSUMED A-011] | |
| `kind` | `"legislation" \| "case_law" \| "other"` | [ASSUMED A-012] | |
| `citation` | `string` | [ASSUMED A-011] | e.g. `Employment Rights Act 1996, s 98`. |
| `title` | `string` | [ASSUMED A-011] | |
| `cited_in_interaction_ids` | `string[]` | [ASSUMED A-011] | **The field that makes "cited in this session" checkable.** |
| `first_cited_at` | `datetime \| null` | [ASSUMED A-011] | |

### 2.5 `Suggestion`

| Field | Type | Spec | Notes |
|---|---|---|---|
| `suggestion_id` | `string` | [ASSUMED A-015] | For `gap_report` provenance, must match a gap-report id. |
| `label` | `string` | [ASSUMED A-015] | |
| `rationale` | `string` | [ASSUMED A-015] | May be empty. |
| `source` | `"gap_report" \| "session_content"` | [ASSUMED A-016] | Provenance. Always present. |
| `related_topic_id` | `string \| null` | [ASSUMED A-016] | Required when `source = session_content`. |

### 2.6 `QuestionLogEntry`

Emitted only when `generation_mode = "question_log_fallback"` [ASSUMED A-025].

| Field | Type | Spec |
|---|---|---|
| `interaction_id` | `string` | [ASSUMED A-025] |
| `asked_at` | `datetime` | [ASSUMED A-025] |
| `question_text` | `string` | [ASSUMED A-025] |
| `topic_tags` | `string[]` | [ASSUMED A-025] |

### 2.7 `DownloadEvent`

Written once per export download [SPEC]; never deduplicated [ASSUMED A-045].

| Field | Type | Spec |
|---|---|---|
| `download_id` | `string` (`dl_<32 hex>`) | [ASSUMED A-029] |
| `summary_id` | `string` | [SPEC] |
| `session_id` | `string` | [SPEC] |
| `user_id` | `string` | [ASSUMED A-045] |
| `downloaded_at` | `datetime` | [ASSUMED A-045] |
| `format` | `"pdf" \| "html"` | [ASSUMED A-045] |
| `pdf_available` | `boolean` | [ASSUMED A-045] |
| `byte_count` | `integer ≥ 0` | [ASSUMED A-045] |

### 2.8 Section notes

`section_notes` keys, all [ASSUMED A-028]: `topics_covered`, `key_concepts`,
`resources_referenced`, `next_steps`, `generation`, `explanation_profile`,
`study_level`. Values are learner-facing English explaining why a section is
short, empty or degraded. A consumer may render them or ignore them; they are
never load-bearing data.

---

## 3. Shapes this component expects to receive

Each is produced by an adapter you write. This component never sees your
payload — see `INTEGRATION.md`.

### 3.1 `SessionRecord` — from `SessionProvider` (read only)

| Field | Type | Spec | Notes |
|---|---|---|---|
| `session_id` | `string` | [SPEC] | Must equal the id requested. |
| `user_id` | `string` | [SPEC] | Ownership is enforced against this. |
| `user_display_name` | `string`, non-empty | [ASSUMED A-005] | Appears on the CPD document. |
| `started_at` | `datetime`, tz-aware | [ASSUMED A-006] | |
| `ended_at` | `datetime \| null` | [ASSUMED A-006] | `null` implies partial. |
| `status` | `SessionStatus` | [ASSUMED A-007] | |
| `naric_level` | `NaricLevel` | [SPEC] | Already the platform enum. |
| `naric_level_source` | `"retrieved" \| "default"` | [SPEC] | |
| `naric_level_status` | `SourceStatus` | [SPEC] | `invalid` when the upstream value mapped to nothing. |
| `course_completion_percent` | `integer 0–100` | [SPEC] | Convert a ratio in your adapter. |
| `course_title` | `string \| null` | [ASSUMED A-006] | |

### 3.2 `InteractionRecord` — from `InteractionProvider` (read only)

| Field | Type | Spec | Notes |
|---|---|---|---|
| `interaction_id` | `string` | [ASSUMED A-009] | |
| `session_id` | `string` | [ASSUMED A-009] | |
| `occurred_at` | `datetime`, tz-aware | [ASSUMED A-009] | Drives the partial cover window. |
| `question_text` | `string` | [ASSUMED A-009] | Rendered **only** in the fallback. Never logged. |
| `topic_tags` | `string[]`, lowercase kebab-case | [ASSUMED A-009] | **The only admissible source of Topics Covered.** |
| `concept_tags` | `string[]`, lowercase kebab-case | [ASSUMED A-009] | **The only admissible source of Key Concepts.** |

Returned oldest-first. An empty result means a session with no logged
interaction — never a failure.

### 3.3 `Resource[]` — from `CitationProvider` (read only)

Shape as §2.4.

> **This port returns citation events, not a reading list.** An authority
> merely *relevant* to the topic must not be returned. This is the one
> guarantee that cannot be recovered downstream: everything after this port can
> only confirm the summary matches what the port said.

### 3.4 `Suggestion[] | None` — from `GapReportProvider` (read only)

Shape as §2.5, always with `source = "gap_report"`.

- `None` — no gap report exists for this learner → `source_status.gap_report = "unavailable"`.
- `[]` — a report ran and suggested nothing → `source_status.gap_report = "empty"`.

These are different states [ASSUMED A-014] and must not be conflated.

---

## 4. Vocabularies, in full

Every serialised value is **lowercase** [SPEC]. Python member names are
uppercase; the value on the wire is not.

### 4.1 `NaricLevel` [SPEC] — closed

`level_3`, `level_4`, `level_5`, `level_6`, `level_7`, `level_7_plus`

Never an integer scale. Never a three-point pedagogic scale.

**Resolution rules:**

| Upstream value | `naric_level` | `naric_level_source` | `naric_level_status` |
|---|---|---|---|
| a recognised value | that level | `retrieved` | `available` |
| absent / empty | `level_5` | `default` | `empty` [ASSUMED A-001] |
| present, maps to nothing | `level_5` | `default` | `invalid` [SPEC] — and logged |

### 4.2 `ExplanationProfile`

| Level | Profile | Spec |
|---|---|---|
| `level_3` | `basic` | [SPEC] |
| `level_4` | `basic` | [ASSUMED A-002] |
| `level_5` | `intermediate` | [SPEC] |
| `level_6` | `intermediate` | [ASSUMED A-002] |
| `level_7` | `advanced` | [SPEC] |
| `level_7_plus` | `advanced` | [SPEC] |

An assumed band is disclosed in `section_notes.explanation_profile`
[ASSUMED A-004].

### 4.3 `SourceStatus` [SPEC] — closed

| Value | Meaning |
|---|---|
| `available` | Source responded and carried usable data. |
| `empty` | Source responded and legitimately carried nothing. |
| `partial` | Source responded with less than the section's target. |
| `unavailable` | Source could not be reached, timed out, or errored. |
| `invalid` | Source responded with something that violates the contract. |

`empty` and `unavailable` are **different states** and are never conflated.

**Keys in `source_status`** [ASSUMED A-027] — two families:

*Source keys:* `session`, `interactions`, `citations`, `gap_report`,
`naric_level`, `summary_generator`.

*Section keys:* `topics_covered`, `key_concepts`, `resources_referenced`,
`next_steps`.

Two combinations carry a distinction worth reading:

- `summary_generator = "unavailable"` — the generator could not be reached.
- `summary_generator = "invalid"` — the generator answered and its answer was
  **refused** for containing ungrounded material. More serious. Do not collapse
  these when surfacing status to a user or an operator.

### 4.4 `GenerationMode` [SPEC]

`generated`, `question_log_fallback`

### 4.5 `SessionStatus`

`in_progress` [ASSUMED A-007], `completed` [ASSUMED A-007], `abandoned`
[ASSUMED A-007], `summary_generated` [SPEC].

Only `summary_generated` is ever written, and only onto this component's own
record [ASSUMED A-008].

### 4.6 `ResourceKind` [ASSUMED A-012]

`legislation`, `case_law`, `other`

### 4.7 `SuggestionSource` [ASSUMED A-016]

`gap_report`, `session_content`

---

## 5. Session identity

- This component **receives** an opaque `session_id` and **never creates one**
  on a production path [SPEC].
- The id is treated as opaque: not parsed, not validated for shape, not
  rewritten. `LP-SESS-0001` and `sess-abc` are equally acceptable, and the
  test suite exercises both.
- It is echoed back unchanged in `SummaryRecord.session_id`, printed on the CPD
  document as *"Session ID for verification"*, and returned in the
  `X-Session-Id` response header on download.
- `summary_id` is **not** derivable from `session_id` [ASSUMED A-029], and a
  session id alone is never sufficient to fetch a summary.
- Dev-mode minting exists behind `UC09_ALLOW_DEV_SESSION_MINTING`, **defaulted
  off** [SPEC]. When off, the route does not exist in the application at all.

---

## 6. The exported document

**HTML is canonical. The PDF is a rendering of that same HTML.** There is one
document builder; the renderer composes nothing. A divergence between the
printable fallback and the PDF is therefore impossible rather than unlikely.

Required content [SPEC], each asserted present in the rendered PDF by test:

- `Loophole Larry` branding
- learner name
- session date
- session duration
- all four sections
- the label `CPD Learning Evidence`
- the session id, for verification

**Partial summaries** carry `PARTIAL SUMMARY - SESSION INCOMPLETE`
[ASSUMED A-023] in the banner, the verification block and the footer, in
**both** HTML and PDF, and the complete-record wording is absent. Duration is
labelled as elapsed time [ASSUMED A-022].

**On renderer failure** [SPEC]: HTTP 200 with the canonical HTML plus one
appended notice, `X-Pdf-Available: false` [ASSUMED A-039]. The learner is never
blocked from their record.

---

## 7. API surface

All reads enforce ownership. `user_id` is resolved server-side and is never
taken from a body or query.

| Method | Path | Returns | Spec |
|---|---|---|---|
| `POST` | `/api/v1/sessions/{session_id}/summary` | `201` + `SummaryResponse` | [SPEC] |
| `GET` | `/api/v1/summaries/{summary_id}` | `200` + `SummaryResponse` | [SPEC] |
| `GET` | `/api/v1/summaries/{summary_id}/preview` | `200` + `text/html` | [SPEC] |
| `GET` | `/api/v1/summaries/{summary_id}/pdf` | `200` + `application/pdf` (or `text/html`) | [SPEC] |
| `GET` | `/api/v1/healthz` | `200` | [SPEC] |
| `GET` | `/api/v1/summaries/{summary_id}/downloads` | `200` + `DownloadEvent[]` | [ASSUMED A-034] |
| `POST` | `/api/v1/dev/sessions` | `201` — **absent unless enabled** | [ASSUMED A-034] |

`SummaryResponse` is `SummaryRecord` (§2.1) plus rendering aids: `sections[]`
(key, title, `orientation`, status, item count, note), `partial_marker`,
`cpd_label`, `product_name` — all [ASSUMED A-028].

`orientation` is `retrospective` for the first three sections and
`forward_looking` for Next Steps. **It is data, not styling**: a client must not
be able to render a suggestion as though it were a record of the session.

### Status codes

| Code | Meaning |
|---|---|
| `201` | Summary generated. |
| `200` | Read succeeded. |
| `401` | `identity_unresolved`. |
| `404` | `summary_not_found` / `session_not_found`. **Also returned when the record exists but belongs to another learner** [ASSUMED A-031]. |
| `422` | `invalid_request` — unknown or malformed body field. |
| `503` | `upstream_unavailable` — the session itself could not be read. |

Error bodies are always `{"error": {"code": ..., "message": ...}}` with a fixed
message. They never contain summary content, session content, upstream error
text, provider names or stack detail.

---

## 8. Privacy

- Ownership enforced on generation, read, preview and download.
- Summaries are transmitted to no third party. The only outbound calls are the
  read-only provider ports you configure.
- **Application logs record** `summary_id`, `session_id`, section counts,
  timing, status codes and port names.
- **Application logs never record** summary content, question text, topic or
  concept labels *or identifiers* [ASSUMED A-040], resource titles or
  citations, next-step labels, learner display names, or rendered document
  bodies. A deny-list processor drops these keys structurally, whatever a call
  site passes.
- A raised `ProviderError.detail` must be a neutral machine code
  [ASSUMED A-041].

---

## 9. Extension points

Where behaviour this component does not own attaches.

| # | Extension point | How to attach | Notes |
|---|---|---|---|
| 1 | **Any upstream data source** | Implement the port, add one registry line, set one env var. | `INTEGRATION.md`. The only supported way in. |
| 2 | **Summary generation** | Implement `SummaryGenerator`, register, set `UC09_SUMMARY_GENERATOR`. | Output is put through the same grounding check regardless of implementation. **A generator cannot opt out by being real.** A `http` adapter ships wired and disabled ([ASSUMED A-048]); it speaks a request/response contract *we* define — see its module docstring — and refuses to start unless `UC09_UPSTREAM_BASE_URL` is set. |
| 3 | **Document rendering** | Implement `DocumentRenderer`, register, set `UC09_DOCUMENT_RENDERER`. | Input is the canonical HTML; output is PDF bytes. It must drop no text. |
| 4 | **Persistence** | Implement `SummaryRepository` / `DownloadLogRepository`, register. | The only two write ports. |
| 5 | **Authentication** | Implement `CurrentUserProvider`, register. | Ownership enforcement is in the application layer and does not move when the authentication mechanism does. |
| 6 | **Session-status publication** | **Not implemented.** | This component records `summary_generated` on its own record and does not write upstream [ASSUMED A-008]. If the platform needs the transition published, that is a new outbound port and a **contract conversation** — deliberately not invented here. |
| 7 | **Topic and concept labelling** | Replace `_humanise` in the generator, or supply labels from your source. | Labels are display only; grounding uses identifiers. |
| 8 | **Document wording and branding** | Constants at the top of `rendering/html_document.py`. | Changing `PARTIAL_MARKER` or `CPD_LABEL` changes both output forms at once, by construction. |

### Not extension points

- **Grounding.** There is no configuration that disables or relaxes the
  grounding check. A generator that cannot ground its output falls back; it
  cannot be allowed through.
- **Upstream writes.** Upstream ports are read-only by shape and an
  architecture test enforces it.

---

## 10. Compatibility notes for consumers

- Treat `summary_id` and `session_id` as opaque strings.
- Read enum values as lowercase strings; do not case-fold before comparing.
- Expect additive fields. Unknown fields in a `SummaryResponse` should be
  ignored, not rejected.
- Always read `is_partial` before presenting a summary as a session record.
- Always read `generation_mode`; a `question_log_fallback` record is **not** a
  full summary and must not be shown as one.
- `source_status` may grow keys; treat an unknown key as informational.
