# UC-04 — Published contract

For an integration engineer who has not read this code and is holding a component this component
has never seen.

Every field is marked:

- **[company]** — specified by the platform contract. Do not rename.
- **[assumed]** — invented here because nothing specified it. Safe to challenge; see
  `docs/assumptions.md` for the reasoning and the risk.

UC-04 answers a learner's question inside a specific course lesson. It **writes** interaction
records. It **reads** learner context and lesson content. It does not create sessions, rate
anything, aggregate anything, or report on anything.

---

## 1. Session identity

**UC-04 receives an opaque `session_id` from its caller and never creates one.** It does not own
session lifecycle, does not extend or close sessions, and holds no session state beyond the
framing history keyed on the id.

A blank or missing `session_id` is refused with `session_required` (HTTP 400). It is never
minted. A dev-only helper exists behind `ALLOW_DEV_SESSION_IDS`, which defaults to `false`.

Framing history and the extraction budget are both **scoped to `(session_id, concept_tag)**`. A
new session starts clean.

---

## 2. Interaction log record — what UC-04 writes

One record per answered question, including follow-ups. Written through the
`InteractionLogRepository` port.

| Field | Type | Marked | Notes |
| --- | --- | --- | --- |
| `interaction_id` | `str` | [company] | Unique per turn. The follow-up endpoint addresses this. |
| `session_id` | `str` | [company] | As received. Never generated. |
| `user_id` | `str` | [company] | Resolved server-side from the authenticated request. |
| `asked_at` | `datetime` (tz-aware, UTC) | [company] | |
| `question_text` | `str \| null` | [company] | **Always the literal `[redacted:not_persisted]`.** UC-04 does not store the learner's words (A-15). |
| `topic_tag` | `str` | [company] | From the closed topic vocabulary, or `unclassified`. |
| `question_class` | enum | [company] field, [assumed] values | `concept_explanation` \| `quiz_answer_seeking` \| `out_of_lesson` \| `ambiguous` (A-14). |
| `naric_level` | enum | [company] | `LEVEL_3` \| `LEVEL_4` \| `LEVEL_5` \| `LEVEL_6` \| `LEVEL_7` \| `LEVEL_7_PLUS`. The level actually used, which may be the default. |
| `response_id` | `str` | [company] | Identifies the generated response, distinct from `interaction_id`. |
| `course_id` | `str` | [company] | |
| `lesson_id` | `str` | [company] | |
| `lesson_section_id` | `str \| null` | [company] | `null` when no section resolved. **Never a guess.** |
| `concept_tag` | `str` | [company] | From the closed concept vocabulary, or `unclassified`. |
| `grounding` | enum | [company] | `lesson` \| `general_knowledge`. Exactly two values. |
| `quiz_intent_detected` | `bool` | [company] | True when either quiz signal fired. |
| `quiz_detection_confirmed` | `bool \| null` | [company] field, [assumed] semantics | `true` a known quiz item matched; `false` intent fired, the lesson has items, none matched; `null` no detection, **or the lesson exposes no items so confirmation was impossible**. Do not read `false` as "definitely not a quiz question" (A-13). |
| `framing_used` | enum \| `null` | [company] field, [assumed] values | See §4. `null` on an exhaustion turn. |
| `explain_differently_count` | `int` | [company] | Re-explanations of this concept in this session. 0 on the first. A **signal**, not a diagnosis. |
| `follow_up_of` | `str \| null` | [company] | The `interaction_id` this followed, or `null`. |
| `rating_state` | enum | [company] | Always written `pending`. **UC-04 never changes it.** Whoever owns rating sets `rated`. |

### Sibling records

Not in the platform contract — nobody knew they existed. Documented so they are known. Both are
written through the same repository port.

**`FalsePositiveRecord`** — suspected quiz-detection false positives, kept for tuning. Carries no
question text and no lesson content.

| Field | Type | Marked |
| --- | --- | --- |
| `record_id` | `str` | [assumed] |
| `interaction_id` | `str` | [assumed] |
| `session_id`, `user_id` | `str` | [assumed] |
| `recorded_at` | `datetime` | [assumed] |
| `classifier_label` | `str` | [assumed] |
| `classifier_confidence` | `float` 0–1 | [assumed] |
| `classifier_signals` | `tuple[str, ...]` | [assumed] |
| `known_item_matched` | `bool` | [assumed] |
| `concept_tag` | `str` | [assumed] |
| `explanation_delivered` | `bool` | [assumed] — always `true`; a false positive still gets a full explanation |

**`FramingAttempt`** — one recorded framing use, scoped to `(session_id, concept_tag)`. Written
through the `FramingRegistry` port. A store that drops `fingerprint_tokens` breaks paraphrase
detection.

| Field | Type | Marked |
| --- | --- | --- |
| `session_id`, `concept_tag` | `str` | [assumed] |
| `framing` | enum | [assumed] |
| `fingerprint` | `str` (16 hex) | [assumed] |
| `fingerprint_tokens` | `tuple[str, ...]` | [assumed] |
| `recorded_at` | `datetime` | [assumed] |

---

## 3. Response shape — what UC-04 returns

```json
{
  "status": "answered",
  "interaction_id": "int_000001",
  "session_id": "sess_main_1",
  "course_id": "course_evi_201",
  "lesson_id": "lesson_evi_01",
  "grounding": "lesson",
  "explanation": "Hearsay - Here is the working account. ...",
  "section_reference": { "status": "resolved", "lesson_section_id": "sec_hearsay_definition" },
  "concept_tag": "hearsay",
  "topic_tag": "evidence",
  "framing_used": "first_principles",
  "explain_differently_count": 0,
  "cross_lesson_references": [
    { "lesson_id": "lesson_evi_02", "title": "Witness Evidence, Competence and Compellability", "reason": "related material in this course" }
  ],
  "actions": ["explain_differently", "go_deeper"],
  "notice": null,
  "naric_level": "LEVEL_6",
  "naric_level_source": "retrieved",
  "explanation_profile": "intermediate",
  "quiz_intent_detected": false,
  "source_status": { "enrolment": "available", "lesson": "available", "course_structure": "available", "learner_context": "available" },
  "rating_state": "pending"
}
```

| Field | Type | Marked | Notes |
| --- | --- | --- | --- |
| `status` | `answered` \| `framings_exhausted` | [assumed] | Turn outcome. Distinct from source status. |
| `grounding` | `lesson` \| `general_knowledge` | [company] | Decided by UC-04, never inferred from the prose. |
| `explanation` | `str` | [assumed] | **Never raw lesson content.** See §7. |
| `section_reference.status` | `resolved` \| `unresolved` | [assumed] | |
| `section_reference.lesson_section_id` | `str \| null` | [company] | For deep-linking. `null` when unresolved — never guessed. |
| `cross_lesson_references[]` | `lesson_id`, `title`, `reason` | [assumed] | Only lessons verified against the course structure. Same course only. Titles come from the structure, not the generator. |
| `actions[]` | `explain_differently` \| `go_deeper` \| `start_free_form_session` | [assumed] | Structured affordances. UC-04 does not execute them; `start_free_form_session` is offered on out-of-lesson answers and is **not implemented here**. |
| `notice` | `str \| null` | [assumed] | Learner-visible. Set when the lesson could not be accessed, enrolment could not be verified, or the answer is out of lesson. |
| `naric_level` / `naric_level_source` | enums | [company] | The level used and whether it was `retrieved` or `default`. |
| `explanation_profile` | `basic` \| `intermediate` \| `advanced` | [company] | Derived from the level; see §5. |
| `source_status` | `{dependency: status}` | [company] vocabulary, [assumed] per-dependency shape | Keys: `enrolment`, `lesson`, `course_structure`, `learner_context`, `concept_tagger` (A-24). |
| `rating_state` | `pending` | [company] | |

### Errors

Uniform envelope. No exception text, provider name, prompt content, stack trace or lesson
content ever appears.

```json
{ "error_code": "not_enrolled", "message": "You are not enrolled on this course.", "rejected_fields": [] }
```

| `error_code` | HTTP | Meaning |
| --- | --- | --- |
| `invalid_request` | 422 | Validation failed. `rejected_fields` names every offending field, including unknown ones. |
| `access_denied` | 403 | No principal, or the resource belongs to another user. |
| `not_enrolled` | 403 | Distinct code. The attempt is logged server-side. |
| `session_required` | 400 | No session identifier supplied. |
| `not_found` | 404 | |
| `upstream_invalid` | 502 | A dependency returned something unmappable. |
| `upstream_unavailable` | 503 | Retryable. |
| `upstream_timeout` | 504 | Retryable. |
| `internal_error` | 500 | |

---

## 4. Framing strategies

Closed set, all [assumed] (A-03). Tried in this order; each used at most once per
`(session_id, concept_tag)`; **never reused**.

`first_principles` → `analogy` → `worked_example` → `contrast_near_miss` →
`procedural_walkthrough` → `misconception_correction`

After all six, `status` becomes `framings_exhausted`, `framing_used` is `null`, and the response
says so and offers `go_deeper`.

---

## 5. Explanation depth mapping

[company] profiles, [assumed] grouping of levels 4 and 6 (A-11).

| NARIC level | Profile |
| --- | --- |
| `LEVEL_3`, `LEVEL_4` | `basic` |
| `LEVEL_5`, `LEVEL_6` | `intermediate` |
| `LEVEL_7`, `LEVEL_7_PLUS` | `advanced` |

**`LEVEL_6` is an undergraduate law degree and maps to `intermediate`, not `advanced`.**

When no usable level arrives: `LEVEL_5`, source `default`, and the question is still answered. A
default is never reported as `retrieved`. An upstream value matching no enum member is an
**invalid response**, not a level: the default applies, source is `default`, and
`source_status.learner_context` is `invalid`.

---

## 6. Source status vocabulary

[company]. Reported per dependency.

| Value | Meaning |
| --- | --- |
| `available` | Answered, with usable content |
| `empty` | Answered; genuinely holds nothing for this subject |
| `partial` | Answered; some expected structure absent |
| `unavailable` | Could not be reached, timed out, or does not exist |
| `invalid` | Answered with something unmappable |

**`empty` and `unavailable` are different states and must never be conflated.**

---

## 7. Shapes UC-04 expects to receive

### `LearnerContext` — from the platform's context service

UC-04 **does not assemble this**. All [assumed] except the level enums.

| Field | Type | Notes |
| --- | --- | --- |
| `user_id` | `str` | |
| `naric_level` | `NaricLevel` | Always populated; carries the default when nothing usable arrived |
| `naric_level_source` | `retrieved` \| `default` | |
| `practice_area` | `str \| null` | Optional |
| `source_status` | source-status enum | |

### `LessonContent` — from the Courses Agent

All [assumed] (A-16). **The key points and the one-sentence definitions are load-bearing**: they
are the only material UC-04 may quote. A source without them yields "the lesson does not set this
out in enough depth" for every question.

| Field | Type | Notes |
| --- | --- | --- |
| `course_id`, `lesson_id`, `title` | `str` | |
| `sections[]` | `section_id`, `title`, `body`, `key_points[]`, `concept_tags[]`, `order` | `body` is used for **matching only** and is never emitted |
| `concepts[]` | `concept_tag`, `name`, `summary`, `section_id`, `keywords[]` | `summary` is the one-sentence definition |
| `quiz_items[]` | `quiz_item_id`, `question_text`, `option_ids[]`, `correct_option_id`, `concept_tag` | **Optional but important** — see A-08. Loaded for matching only; never rendered, logged or returned |
| `revision` | `str \| null` | |

### `CourseStructure`

`course_id`, `title`, `lessons[]` of `lesson_id` / `title` / `order`. This is the **whitelist**
cross-lesson references are verified against. Return every lesson in the course (A-17).

---

## 8. Lesson content and intellectual property

- **No endpoint returns raw lesson content.** Responses carry an explanation plus a section
  reference. The response model has no field that could carry lesson text.
- Section body prose is never quotable. Only concept definitions and curated key points are, and
  only within the extraction budget: **3 distinct spans per concept per session, 2 per response,
  25 words per span**.
- Quiz item text and keys never leave the service.
- Measured ceiling on the reference fixture, after exhaustive interrogation: 0/11 body sentences,
  6/15 key points, 33% of all source units.

---

## 9. Vocabularies

Both closed, both [assumed] (A-06). Unmatched → `unclassified`, and the unclassified rate is a
logged metric.

**Topics:** `evidence`, `civil_procedure`, `professional_conduct`, `contract_law`,
`data_protection`

**Concepts** (concept_tag → topic_tag):

| Concept | Topic |
| --- | --- |
| `hearsay` | `evidence` |
| `hearsay_exception` | `evidence` |
| `witness_competence` | `evidence` |
| `witness_compellability` | `evidence` |
| `expert_evidence` | `evidence` |
| `burden_of_proof` | `evidence` |
| `standard_of_proof` | `evidence` |
| `legal_advice_privilege` | `evidence` |
| `litigation_privilege` | `evidence` |
| `standard_disclosure` | `civil_procedure` |
| `without_prejudice` | `civil_procedure` |
| `limitation_period` | `civil_procedure` |
| `duty_of_candour` | `professional_conduct` |
| `conflict_of_interest` | `professional_conduct` |

---

## 10. Extension points

Everywhere behaviour UC-04 does not own attaches. Each is a port; each is one registry line to
replace (`docs/INTEGRATION.md`).

| Extension point | Port | What attaches |
| --- | --- | --- |
| Lesson content, course structure, enrolment | `CoursesProvider` | The Courses Agent |
| Learner context | `LearnerContextProvider` | The context service that assembles NARIC level and practice area |
| Explanation generation | `AnswerGenerator` | A real model. Prompts stay server-side and versioned in UC-04 |
| Quiz intent detection | `QuizIntentClassifier` | A stronger classifier. Known-item matching stays in UC-04 core |
| Tagging | `ConceptTagger` | The real taxonomy and tagger |
| Interaction persistence | `InteractionLogRepository` | The platform database. **Consumers of the interaction record attach here** |
| Framing history | `FramingRegistry` | A session store or cache |
| Identity | `CurrentUserProvider` | The platform gateway's principal |
| Free-form session | *(none — an action)* | `start_free_form_session` is emitted for an orchestrator to act on. Not implemented here |
| Rating | *(none — a field)* | `rating_state` is written `pending`. Whoever owns rating transitions it to `rated` |
| Gap analysis / progress reporting | *(none — the record)* | Consumes `InteractionRecord`. UC-04 performs no aggregation |
