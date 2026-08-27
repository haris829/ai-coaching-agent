# UC-02 Assumptions Register

Every field name, response shape, enum value and behaviour below was **invented by
UC-02** because the company has not delivered NARIC, the Courses Agent, Legal Foot
Prints, or a question-history service. There are no API specs, no endpoints and no
sample payloads.

This file is the handover instrument. When real specs arrive, the integration
engineer reads each row, diffs it against reality, and knows exactly which adapter
needs work before any code is written.

**How to use it:** work top to bottom. For each row, mark the assumption
*confirmed*, *wrong-but-adaptable* (fix inside the adapter), or *wrong-and-structural*
(the port itself must change, which means `docs/integration.md` and the domain
models change too). Only the rows marked structural can ripple past the adapter
boundary.

Legend for **Blast radius**:

- **adapter** — a wrong guess is absorbed by the adapter's translation code. Nothing else changes.
- **port** — the port signature or record shape changes; `ContextAssemblyService` may need a change.
- **policy** — a product decision that needs the company's confirmation, not an engineering fix.

---

## NARIC

| ID | Source | Assumption | Why | Risk if wrong | Where in code | Blast radius |
|---|---|---|---|---|---|---|
| A-01 | NARIC | The qualification level is an **integer** on the RQF scale (3–8), not a string label such as `"Level 7"` or `"Masters"`. | The scope document talks in numeric levels ("Level 3", "Level 7+"), and the mapping table is keyed by number. An integer makes the mapping total and testable. | If NARIC returns only free-text labels, the adapter must parse them, and unparseable labels become `ProviderInvalidResponse` → every such learner silently gets the Level 5 default and an over/under-pitched explanation. | `uc02/domain/models/provider_records.py::NaricRecord.level`, `uc02/domain/explanation_mapping.py` | adapter |
| A-02 | NARIC | A human-readable label (`raw_level_label`) accompanies the level and is **optional**. | Useful for support and audit; UC-02 must not depend on it. | None material — it is display metadata only and nothing branches on it. | `NaricRecord.raw_level_label`, `NaricContext.raw_level_label` | adapter |
| A-03 | NARIC | Levels **4 and 6 exist** and group as 4→`basic`, 6→`intermediate`. | The scope document specifies only 3, 5, 7 and 7+. Level 6 is an undergraduate law degree, not Masters level; mapping it to `advanced` would pitch explanations above the learner. Level 4 sits below the practitioner-foundation threshold. | Learners at 4 and 6 receive the adjacent register. A wrong grouping for Level 6 is the more damaging direction (explanations too technical), which is why it groups **down**, not up. | `uc02/domain/explanation_mapping.py::LEVEL_TO_TEMPLATE` | policy |
| A-04 | NARIC | Levels **outside 3–8** are possible (1, 2, 9) and are clamped to the nearest mapped row rather than rejected. | An unexpected level must never break session start. | A Level 1–2 learner gets `basic` (correct in spirit). A Level 9 learner gets `advanced` (correct in spirit). No failure mode identified. | `uc02/domain/explanation_mapping.py::template_for_level` | adapter |
| A-05 | NARIC | "No qualification held" is expressed as a **successful response with a null level**, not an error and not a 404. | `empty` and `unavailable` must stay distinguishable (scope §8). | If NARIC 404s for unknown learners, the adapter must translate 404 → `NaricRecord(level=None)` rather than letting it become `ProviderUnavailable`. Getting this wrong makes every new learner look like an outage and corrupts UC-07's gap analysis. | `MockNaricProvider` MISSING_QUALIFICATION, `normalise_naric` | adapter |

## Courses Agent

| ID | Source | Assumption | Why | Risk if wrong | Where in code | Blast radius |
|---|---|---|---|---|---|---|
| A-06 | Courses | `completion_percentage` is on a **0–100** scale, not 0–1. | "Completion percentage" in the scope document reads as a percentage. The record validates the range, so a 0–1 source would be caught immediately rather than silently reporting 0.42%. | A 0–1 source passed through untranslated makes every learner look ~0% complete. The `ge=0, le=100` bound does not catch it (0.42 is a valid percentage), so this must be verified against the spec, not left to validation. | `CourseEnrolmentRecord.completion_percentage` | adapter |
| A-07 | Courses | **One call returns all enrolments** for a learner — no pagination, no per-course fan-out. | Keeps the port to a single round trip inside the 2s provider timeout. | If enrolments are paginated, the adapter must loop internally and stay within its own timeout, or return the first page and accept a `partial` status. Either is an adapter change; the port signature survives. | `CoursesProvider.get_learning_context` | adapter |
| A-08 | Courses | The last-accessed lesson is recorded **per course** and is optional (a fresh enrolment has none). | A learner who has enrolled but not opened a lesson is a normal state, not a data error. | If the platform tracks only one *global* last-accessed lesson, the record shape is wrong and `CourseContext` changes. This is the one Courses assumption that reaches past the adapter. | `CourseEnrolmentRecord.last_accessed_lesson_id/name`, status `partial` in `normalise_courses` | port |
| A-09 | Courses | `course_id`/`course_name` and `last_accessed_lesson_id`/`last_accessed_lesson_name` are **opaque strings**; UC-02 never parses them. | Nothing in UC-02 needs structure inside an identifier. | None. | `CourseEnrolmentRecord` | adapter |

## Legal Foot Prints

| ID | Source | Assumption | Why | Risk if wrong | Where in code | Blast radius |
|---|---|---|---|---|---|---|
| A-10 | Legal | `speciality_areas` is a **list**, and a learner may declare several. | "Speciality areas" is plural in the scope document. A list degrades safely to a one-element list; a single value cannot represent multiple. | Low. If the real system holds a single value, the adapter wraps it. | `LegalProfileRecord.speciality_areas` | adapter |
| A-11 | Legal | `practice_area` is a **single optional value**, distinct from speciality areas. | The scope document lists them as separate concepts. | If practice area is actually a list, `LegalContext.practice_area` changes type and any downstream consumer reading it breaks. | `LegalProfileRecord.practice_area`, `LegalContext.practice_area` | port |
| A-12 | Legal | `case_type_preferences` is a **list of free-text strings**, not a controlled vocabulary. | No enum was supplied. Free text cannot be validated wrongly. | If there is a controlled vocabulary, UC-02 loses the ability to validate but nothing breaks. | `LegalProfileRecord.case_type_preferences` | adapter |
| A-13 | Legal | Presence of at least one speciality area is what makes explanations *speciality-pitched*; absence means `general_legal`. | The scope document requires a fallback to general legal explanations when speciality is missing. Practice area alone is too coarse to pitch a speciality explanation. | If the company intends `practice_area` to drive the domain instead, learners with a practice area but no speciality get general explanations where they should get speciality ones. Product decision, not a bug. | `normalise_legal`, `LegalContext.explanation_domain` | policy |
| A-14 | Legal | An **all-empty profile is a normal state** (`empty`), and a partially filled one is `partial`. | Learners who have not completed onboarding must not look like an outage. | Misclassification would flow into UC-07's analytics, not into the learner's experience. | `normalise_legal` | adapter |

## Question history

| ID | Source | Assumption | Why | Risk if wrong | Where in code | Blast radius |
|---|---|---|---|---|---|---|
| A-15 | History | Questions are queryable **across all prior sessions of one learner in a single call**, with a caller-supplied limit — no pagination, no per-session fan-out. | The requirement is "last 20 questions across prior sessions". A single bounded call is the cheapest shape that satisfies it. | If history is per-session only, the adapter must first enumerate the learner's sessions — which means UC-02 would depend on UC-01's session store, a dependency this repository is forbidden to take. **This is the assumption most likely to force a design conversation.** | `QuestionHistoryProvider.get_recent_questions` | port |
| A-16 | History | Results come back **newest-first**. UC-02 re-sorts defensively rather than trusting the order. | Truncation to 20 must keep the *most recent* 20, so order cannot be left to chance. | None: the defensive sort makes UC-02 correct either way. Verified by `test_truncation_keeps_the_most_recent_questions`. | `normalise_history` | adapter |
| A-17 | History | Each record carries an optional `topic_tag`. | Useful to downstream use cases; UC-02 itself does not branch on it. | If absent, `topic_tag` is `None` everywhere and nothing in UC-02 changes. | `QuestionRecord.topic_tag` | adapter |
| A-18 | History | The question **text** is available, but is treated as the most sensitive field UC-02 holds: kept server-side as a 160-character excerpt, never returned by the API, never logged. | Privacy requirement (scope §11, §15). The excerpt length is arbitrary. | If the company forbids UC-02 from holding text at all, drop the field — nothing in UC-02 reads it. If downstream needs full text, the excerpt length must be revisited. | `QUESTION_EXCERPT_CHARS`, `QuestionHistoryItem.text_excerpt` | policy |
| A-19 | History | A provider may return **more than the requested limit**; UC-02 truncates server-side and flags `truncated`. | The 20-question limit must be enforced by UC-02, not trusted to the upstream system. A caller asking for 500 gets 20. | None — this is defence, not a guess. | `normalise_history`, `question_history_limit` | adapter |
| A-20 | History | An unparsable record can appear alongside good ones; it is dropped, counted, and the source is marked `partial`. | One bad row must not cost the learner their whole history. | If the company would rather fail the whole source, change `normalise_history` — a one-line policy switch. | `normalise_history`, `dropped_malformed_count` | policy |

## Session and identity

| ID | Source | Assumption | Why | Risk if wrong | Where in code | Blast radius |
|---|---|---|---|---|---|---|
| A-21 | UC-01 | `session_id` is an **opaque string** (1–256 chars) created by UC-01 *before* UC-02 is called. UC-02 never parses it and never invents one in production. | UC-02 owns context, not the session lifecycle. Guessing a format would couple the two repositories. | If UC-01 expects UC-02 to create sessions, integration fails at the first call — loudly, with a 400 naming UC-01, which is the intended failure mode. See `docs/integration.md`. | `SessionIdentity.session_id`, `composition.resolve_session_id` | port |
| A-22 | Platform auth | `user_id` is an **opaque string** resolved server-side per request, stable across sessions, and usable as the lookup key for all four upstream systems. | All four providers key on the learner. If NARIC keys on something else (a qualification reference, an email), the adapter must map it. | If the systems key on different identifiers, each adapter needs its own lookup step and its latency budget shrinks accordingly. | `CurrentUserProvider`, all four ports | port |
| A-23 | Platform auth | A header-based identity shim is acceptable **for development only**, and the production replacement returns the same opaque string. | UC-02 ships no production auth by constraint. | None if replaced. Shipping `DevelopmentUserProvider` to production would let any caller claim any identity — the single most dangerous misconfiguration in this repository. | `DevelopmentUserProvider` | policy |

## UC-02's own behaviour (not upstream, but still guesses)

| ID | Source | Assumption | Why | Risk if wrong | Where in code | Blast radius |
|---|---|---|---|---|---|---|
| A-24 | UC-02 | The Level 5 default is applied to **both** "NARIC is down" and "NARIC holds no qualification", but the two are recorded with **different statuses** (`unavailable` vs `empty`). | The scope document mandates the default; it does not say the two causes should be indistinguishable, and §8 says they must not be. | If the company wants different defaults per cause, change `normalise_naric` only. | `normalise_naric` | policy |
| A-25 | UC-02 | `personalization.available` is true when **at least one** source returned usable data (`available` or `partial`). A source that is healthy but `empty` does not count as contributing. | The scope document defines only the all-four-down case. This rule generalises it without contradicting it. | A learner with genuinely nothing recorded sees `available: false`, which is honest but might not be the intended product behaviour. | `ContextAssemblyService._personalization` | policy |
| A-26 | UC-02 | When every source is healthy but the learner has **nothing recorded**, the notice says so explicitly rather than reusing the outage wording. | Telling a brand-new learner that data is "temporarily unavailable" is false. `empty` is not `unavailable`. | Two notice strings instead of one for the future frontend to handle. The mandated outage string is used verbatim for the all-down case and is asserted in tests. | `PERSONALIZATION_EMPTY_NOTICE` | policy |
| A-27 | UC-02 | A `ProviderInvalidResponse`, and any undeclared exception escaping an adapter, map to status `invalid`; timeouts, unavailability and budget overruns map to `unavailable`. | Splits "the network failed" from "the contract was broken" so on-call can tell an outage from a bad deploy. | Downstream consumers treating `invalid` as fatal would over-react; it is a degraded state like any other. | `normalisers.status_for_error` | adapter |
| A-28 | UC-02 | Stored context expires after a **12-hour TTL**, and an expired context is indistinguishable from an absent one, so the next initialize rebuilds it. | Bounds memory growth in a process with no database. | The company's persistence layer will likely handle expiry differently (row TTL, background sweep, or never). A shorter session-service TTL would cause more rebuilds; a longer one risks serving stale context. | `InMemorySessionContextRepository` | policy |
| A-29 | UC-02 | A stored context is returned **unchanged** for the life of the session even if upstream data changes. | The scope document requires build-once-at-session-start and no re-query. | A learner who completes a lesson mid-session sees stale progress until the next session. This is the specified behaviour, recorded here because it is a real product consequence. | `ContextAssemblyService.initialize` | policy |
| A-30 | UC-02 | The API response **omits the raw NARIC level** and returns only the derived explanation profile. | The level is personal data and the caller does not need it to pitch a response. §10 lists the fields and the level is not among them. | If a downstream use case genuinely needs the level, add it deliberately and bump `context_version`. | `InitializeContextResponse` | policy |
| A-31 | UC-02 | Another user's `session_id` returns **404, not 403**. | 403 would confirm the session exists, letting a caller enumerate sessions. | None identified; it is strictly the safer of the two options the scope permits. | `main._register_error_handlers` | policy |
| A-32 | UC-02 | `context_version` is the string `uc02.context.v1`, bumped on any shape change. | Downstream use cases need to detect drift; a plain string is the least presumptuous format. | If the platform standardises on semver or a date, change the constant. | `CONTEXT_VERSION` | adapter |
| A-33 | UC-02 | The in-memory store is **per-process**, so the "no re-query" guarantee holds per worker. Two workers each build their own context for the same session. | Consequence of shipping without a database. | With N workers, up to N context builds per session and N sets of provider calls. Disappears once a shared store replaces the in-memory one. | `InMemorySessionContextRepository` | policy |
| A-34 | UC-02 | The deterministic explanation renderer is a **testing and demonstration artefact**, not the platform's answer generator. It exists to prove the Level 3 / Level 7 difference without an LLM. | UC-02 must introduce no LLM, yet must prove the mapping has a material effect. | If someone mistakes it for the coaching engine, they will ship template text to learners. Marked clearly in the module docstring. | `uc02/application/explanation_renderer.py` | policy |
| A-35 | UC-02 | The technical-term list used to measure explanation complexity is an **illustrative English-law sample**, not a curated taxonomy. | The depth-difference test needs a defined term list to measure against. | The metric is only as good as the list. Replace it with the company's terminology when one exists; the test asserts a relationship, not a specific count. | `TECHNICAL_TERMS` | policy |
| A-36 | UC-02 | `user_id` reaches logs only as a salted SHA-256 reference (`uref_…`). | §15 permits "hashed or referenced". A salted digest is not reversible by anyone holding only the logs. | If the platform requires log/user correlation across services, they must share the salt — a deployment decision. | `logging/setup.py::user_reference` | policy |
| A-37 | UC-02 | Default timeouts (2000 ms per provider, 3000 ms total) are **placeholders** chosen for a session-start interaction. | No latency data exists for systems that have not been built. | If the real systems are slower, more sources fall back to defaults on every session start. All three values are config, so this is a `.env` change, not a code change. | `Settings.provider_timeout_ms`, `context_assembly_budget_ms` | policy |
| A-38 | UC-02 | `ALLOW_FORCE_REFRESH` was added to the configuration set in §14, which does not list it. | §9 requires the force-refresh flag to be "gated by config"; that needs a config value to exist. | None — it defaults to false, and the public path rejects `force_refresh` regardless of its value. | `Settings.allow_force_refresh` | policy |
| A-39 | UC-02 | `source_status` maps each source to a small **`SourceOutcome` object** (status, error category, duration, fallback flag) rather than to a bare status enum as sketched in the scope. | On-call needs to tell a timeout from a refused connection, and needs per-source latency, without turning on debug logging. The status enum is still the first field. | A consumer expecting `source_status["naric"] == "available"` gets an object instead. Documented here and detectable via `context_version`. | `SessionContext.source_status`, `SourceOutcome` | port |

---

## Rows the integration engineer should verify **first**

Ordered by how much breaks if the guess is wrong:

1. **A-15** (history queryable cross-session in one call) — if wrong, UC-02 may need a dependency it is forbidden to take. Resolve before writing any adapter.
2. **A-01** (numeric NARIC level) — everything about explanation pitching hangs off it.
3. **A-06** (completion 0–100 vs 0–1) — silently wrong data, no exception raised.
4. **A-22** (one user identifier works across all four systems) — determines whether each adapter needs its own lookup hop.
5. **A-08** (last-accessed lesson is per course) — the only Courses guess that reaches past the adapter.
6. **A-05** (missing qualification is a success, not a 404) — gets `empty` vs `unavailable` right, which UC-07 depends on.
