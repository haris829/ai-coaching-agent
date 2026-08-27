# UC-07 — Progress & Knowledge Gap Identification: Implementation Report

All numbers, ids and fingerprints below were produced by the code in this
repository. Raw command output is reproduced in the appendices.

Reproduce everything:

```
python -m pytest -vv          # 421 passed, 0 skipped
```

---

## 1. What was built

A standalone FastAPI backend service that reads a learner's coaching history,
ratings, profile and course data through read-only ports and derives a
deterministic, explainable knowledge-gap report. The report is the only thing it
persists.

Delivered:

* **Domain layer** — the fixed platform contract as frozen Pydantic v2 models
  (`InteractionRecord`, `FeedbackRecord`, `LearnerProfile`, …), the NARIC and
  five-state source-status enumerations, a typed error taxonomy, and **one**
  definition of what counts as a qualifying interaction.
* **Ports** — four read-only upstream ports with no write surface, one
  write-capable port (`GapReportRepository`), plus `CurrentUserProvider` and
  `Clock` seams.
* **Application layer** — configuration-driven thresholds, full-history
  aggregation, three independently testable struggle signals, unexplored-
  speciality analysis, recommendation validation, an evidence-integrity guard,
  deterministic report assembly, and the service that orchestrates them.
* **Adapters** — deterministic mocks with 30 named scenarios; a deliberately
  FOREIGN adapter set ("Nexus LMS") with different field names, nesting and value
  representations; an in-memory report repository; header/static identity; system
  and fixed clocks; and a real-adapter template with TODO markers.
* **API** — three GET endpoints, a uniform error envelope, strict input rejection
  (no query parameters, no body, therefore no way to pass a user id).
* **Composition root** — a provider registry that fails loudly on an unknown
  provider name and never falls back to mocks.
* **Tests** — 421 tests: unit, API, architecture, a reusable adapter-agnostic
  conformance kit, and integration/swap proofs. Zero skipped.
* **Docs** — `docs/assumptions.md` (48 assumptions), `docs/SHARED_CONTRACT.md`,
  `docs/INTEGRATION.md`, plus `README.md`.

Not built, deliberately: no frontend, no production database, no production
authentication, no LLM/RAG/embeddings/vector store, no agent framework, no
session creation, no coaching, no ratings, no enrolment writes.

---

## 2. Threshold evidence — 9 vs 10 vs 11

Raw output (`evidence/evidence.txt`, section 2):

```
count=0   status=below_threshold  completed=0   remaining=10  report=None
count=5   status=below_threshold  completed=5   remaining=5   report=None
count=9   status=below_threshold  completed=9   remaining=1   report=None
count=10  status=available        completed=10  remaining=0   report=gr_b4e40965ac2595b5634c050954fa9172
count=11  status=available        completed=11  remaining=0   report=gr_526d92d6dc6650378703f5e72c643ac1
count=50  status=available        completed=50  remaining=0   report=gr_9d1942f552ccc3e3c4e1210ff19e9dff
```

* **No report at 9** — `report=None`, HTTP 200, progress only. Below threshold is
  never an error.
* **Report at exactly 10** — the first count that produces a report.
* **Report remains at 11 and 50** — with a different id, because the content
  (including `source_interaction_count`) changed.

Tests: `tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix`
(6 cases), `::test_no_report_below_ten_interactions` (0/5/9),
`::test_no_report_at_nine_but_report_at_ten`,
`::test_report_available_at_and_above_ten` (10/11/50), and the same matrix over
HTTP in `tests/api/test_endpoints.py`.

The threshold itself is configuration (`GAP_REPORT_THRESHOLD`), proven by
`::test_threshold_comes_from_configuration_not_code`, which moves it to 5 and
gets a report at 5.

---

## 3. Exact interaction-counting rule

Defined once, in `uc07/domain/counting.py` (`qualifying_interactions`). Nothing
else in UC-07 counts interactions.

1. The record must be a valid `InteractionRecord`. A payload that cannot satisfy
   the platform contract never becomes a record: the adapter raises
   `ProviderInvalidResponse` instead of bending the model or silently dropping
   rows.
2. The record must belong to the resolved learner (`user_id` match). Records for
   other learners are discarded and counted as `other_user_records_discarded`.
3. Duplicate `interaction_id` values count **once** — first occurrence in
   provider order wins.
4. **Follow-ups count.** Clarifying interactions count when represented as an
   `InteractionRecord`. `explain_differently_count` is a *counter on* an
   interaction, never an extra interaction.
5. Counting spans the learner's **complete history, all sessions**.
6. Output is sorted by `(asked_at, interaction_id)`, so provider order cannot
   change the result.

`InteractionLogProvider.count_for_user` is **advisory only** — logged as
`provider_reported_count`, never used for a threshold decision (A-05).

Evidence: the `duplicate_interaction_ids` scenario feeds 12 raw records with two
repeated ids and yields exactly 10 qualifying; `mixed_owner_records` feeds 12 with
two belonging to another learner and yields exactly 10. Both are asserted in
`tests/unit/test_counting_and_threshold.py`. Order-independence is asserted by
`::test_counting_is_order_independent`.

---

## 4. Evidence-linkage proof

Three layers, all tested:

1. **Model level** — a `struggle` gap cannot be constructed with empty evidence;
   per-signal ids must be a subset of the gap's evidence ids; a `SignalEvidence`
   cannot claim a signal whose `observed_value < threshold`. (`uc07/domain/models.py`)
2. **Guard level** — `enforce_evidence_integrity` drops any gap whose evidence
   does not resolve to an interaction used in the analysis, reporting a reason
   code. Fabricated and partially fabricated evidence are both rejected.
   (`uc07/application/evidence_guard.py`)
3. **Report level** — `tests/unit/test_evidence.py` runs 25 report-producing
   scenarios and asserts, for every gap in every report, that each evidence id
   resolves to a real interaction of that learner, and that no gap topic exists
   outside the learner's history or speciality set. That is 50 parameterized
   assertions plus the model/guard tests: 57 tests in that file alone.

Unexplored gaps are the one case with no interaction ids — by definition. They
carry `basis = zero_interactions_for_speciality_area`, and the guard rejects an
unexplored gap that carries ids and a struggle gap that does not (A-20).

Raw linkage output for the showcase report:

```
struggle   contract_formation     signals=['explain_differently', 'low_rating'] evidence=['interaction-101', 'interaction-103'] all_resolve=True
    signal explain_differently    observed=3 threshold=2 ids=['interaction-101', 'interaction-103']
    signal low_rating             observed=2 threshold=1 ids=['interaction-101', 'interaction-103']
struggle   land_registration      signals=['low_rating'] evidence=['interaction-301'] all_resolve=True
    signal low_rating             observed=1 threshold=1 ids=['interaction-301']
struggle   negligence             signals=['follow_up'] evidence=['interaction-202', 'interaction-203'] all_resolve=True
    signal follow_up              observed=2 threshold=2 ids=['interaction-202', 'interaction-203']
unexplored commercial_drafting    signals=['unexplored_speciality'] evidence=[] all_resolve=True
unexplored data_protection        signals=['unexplored_speciality'] evidence=[] all_resolve=True
```

---

## 5. One complete generated report

Scenario `struggle_mixed`: 14 interactions, 3 sessions, 5 topic tags, fixed clock
`2026-03-01T12:00:00Z`. The complete, unabridged API response is reproduced in
**Appendix A** (also saved to `evidence/sample_report.json`). Key content:

| Gap | Type | Signals | Evidence ids | Recommendations |
|-----|------|---------|--------------|-----------------|
| `contract_formation` | struggle | `explain_differently` (3 ≥ 2), `low_rating` (2 ≥ 1) | `interaction-101`, `interaction-103` | lessons `lesson-cf-01`, `lesson-cf-02` (learner already enrolled in `course-contract-essentials`) |
| `land_registration` | struggle | `low_rating` (1 ≥ 1) | `interaction-301` | course `course-property-practice` |
| `negligence` | struggle | `follow_up` (2 ≥ 2) | `interaction-202`, `interaction-203` | course `course-tort-foundations` |
| `commercial_drafting` | unexplored | `unexplored_speciality` | — (zero interactions) | course `course-commercial-drafting` |
| `data_protection` | unexplored | `unexplored_speciality` | — (zero interactions) | course `course-data-protection` |

Report envelope: `report_id=gr_fa59e7169dbb4f165badc92faaa98fa6`,
`threshold=10`, `source_interaction_count=14`, `report_version=1.0.0`,
`analysis_version=1.0.0`, all four source statuses `available`,
`recommendations.status=available` (resolved 6, rejected 2, converted 1),
`topic_coverage.topic_areas_in_history=5`, `unexplored_analysis.state=performed`,
`notices=[]`.

Two topics in the history deliberately do **not** appear: `professional_conduct`
(explain-differently 1 < 2, follow-ups 0 < 2, thumbs-down 0 < 1) and
`evidence_admissibility` (no signals at all).

---

## 6. Signal definitions and threshold tests

| Signal | Observed value | Configured threshold | Evidence ids | Independent test |
|--------|----------------|----------------------|--------------|------------------|
| `explain_differently` | Sum of `explain_differently_count` over the topic's interactions | `EXPLAIN_DIFFERENTLY_STRUGGLE_THRESHOLD` = 2 | interactions whose counter ≥ 1 | `test_explain_differently_fires_at_the_configured_threshold`, `test_explain_differently_totals_across_interactions_in_the_topic`, `test_explain_differently_below_threshold_does_not_surface` |
| `follow_up` | Count of interactions in the topic with `follow_up_of` set | `FOLLOW_UP_STRUGGLE_THRESHOLD` = 2 | those follow-up interactions | `test_follow_up_signal_fires_at_the_configured_threshold`, `test_single_follow_up_does_not_surface`, `test_heavy_follow_up_scenario_surfaces_only_the_follow_up_topic` |
| `low_rating` | Count of thumbs-down feedback records for the topic's interactions, owned by the learner | `LOW_RATING_STRUGGLE_THRESHOLD` = 1 | the rated interactions | `test_low_rating_signal_fires_on_a_single_thumbs_down`, `test_thumbs_up_is_never_a_struggle_signal`, `test_ratings_for_unknown_interactions_cannot_manufacture_evidence`, `test_ratings_owned_by_another_learner_are_ignored` |

Combination and silence:

* `test_signals_combine_on_one_topic_in_canonical_order` — one topic carrying all
  three signals, canonical order, union of evidence.
* `test_topic_below_every_threshold_is_not_a_struggle` — a topic with
  explain=1, follow-ups=1 and a thumbs-**up** produces nothing.
* `test_showcase_scenario_signal_matrix` — asserts the exact signal matrix above
  and that `professional_conduct`/`evidence_admissibility` are absent.
* Signal isolation scenarios: `heavy_explain_differently` yields only
  `{'misrepresentation': ['explain_differently']}`; `heavy_follow_ups` yields only
  `{'trusts_formation': ['follow_up']}`.
* `test_low_rating_signal_is_skipped_when_the_rating_source_cannot_be_read`
  versus `test_empty_rating_source_is_evaluated_and_simply_finds_nothing` — the
  unavailable/empty distinction reaches the signal layer.

No LLM, no heuristics beyond these comparisons, no randomness.

---

## 7. Unexplored-speciality evidence

| Situation | State | Report behaviour | Test |
|-----------|-------|------------------|------|
| Speciality area with zero interactions | `performed` | Unexplored gap per area, `unexplored_areas_found` set | `test_speciality_area_with_zero_interactions_is_unexplored`, `test_report_contains_unexplored_gaps_for_uncovered_speciality_areas` |
| All speciality areas covered | `performed` | No unexplored gap | `test_fully_covered_speciality_produces_no_unexplored_gap`, `test_fully_covered_speciality_yields_no_unexplored_gaps_in_the_report` |
| No speciality set | `not_performed_no_speciality` | Explicit statement + `speciality_analysis_not_possible_no_speciality` notice; nothing inferred from history | `test_no_speciality_is_stated_explicitly_and_never_inferred`, `test_no_speciality_reports_that_analysis_could_not_be_performed` |
| Partial speciality | `performed_partial` | Partial status preserved, `may_be_incomplete=true`, `speciality_analysis_partial` notice, retrieved areas still analysed | `test_partial_speciality_keeps_partial_status_and_flags_incompleteness`, `test_partial_speciality_is_preserved_and_documented_in_the_report` |
| Profile unavailable | `not_performed_profile_unavailable` | Struggle analysis continues, `speciality_analysis_unavailable` notice, no invented areas | `test_unavailable_profile_does_not_invent_speciality_areas`, `test_unavailable_profile_still_yields_evidence_based_struggle_analysis` |
| Profile invalid | `not_performed_profile_invalid` | Distinct from unavailable; status `invalid` preserved | `test_invalid_profile_is_distinct_from_unavailable` |

Raw degraded output confirms it (`evidence.txt` section 6):

```
profile_unavailable      statuses=(available/available/unavailable/available) recs=available    gaps=3 notices=['speciality_analysis_unavailable']
profile_partial          statuses=(available/available/partial/available)     recs=available    gaps=4 notices=['speciality_analysis_partial']
profile_invalid          statuses=(available/available/invalid/available)     recs=available    gaps=3 notices=['speciality_analysis_invalid']
profile_no_speciality    statuses=(available/available/empty/available)       recs=available    gaps=3 notices=['speciality_analysis_not_possible_no_speciality']
```

(3 gaps = the three evidence-backed struggles; the unexplored gaps disappear
exactly when speciality analysis cannot be performed.)

---

## 8. Recommendation validation

Rules and their tests (`tests/unit/test_recommendations.py`, 17 tests):

* Valid lesson kept; **unknown lesson id removed** and never replaced
  (`rejected_unresolvable_count` = 1).
* **Unknown course id removed** and never replaced.
* **Already enrolled → lesson recommendations** inside that course, filtered to
  the gap's topic. No duplicate enrolment is ever recommended.
* Enrolled course with **no lesson carrying the topic → candidate dropped**
  (`dropped_already_enrolled_count`), never guessed.
* Another learner's enrolment does not affect this learner.
* Candidates for topics that are not gaps are ignored (not counted as rejects).
* Courses **unavailable** → `recommendations.status = unavailable`, gaps intact,
  `recommendations_temporarily_unavailable` notice.
* Courses **partial** → `status = partial` + `recommendations_partial` notice.
* Courses **invalid** → status preserved as `invalid`, recommendations
  `unavailable`, gaps intact.
* Recommendations are deduplicated and sorted.

End-to-end on the showcase scenario, the courses mock deliberately offers two
unresolvable candidates (`course-does-not-exist`, `lesson-lr-99`):

```
recommendation summary: resolved=6 rejected_unresolvable=2 converted_to_lesson=1 dropped_already_enrolled=0
```

and `test_report_recommendations_resolve_to_real_catalogue_identifiers` checks
every emitted `course_id`/`lesson_id` against the catalogue.

Gap analysis never depends on course availability:
`test_gaps_survive_when_the_course_source_is_unavailable` shows all 5 gaps intact
with recommendations marked unavailable.

---

## 9. Determinism proof

`report_id` is derived from `content_fingerprint`, a sha256 over the canonical
JSON of the report content (excluding id and timestamp), so equality is checkable
without a database.

```
fingerprint A = fa59e7169dbb4f165badc92faaa98fa68ce60a284a18f5b9566bbc138747b678
fingerprint B = fa59e7169dbb4f165badc92faaa98fa68ce60a284a18f5b9566bbc138747b678
identical objects: True; identical ids: True
```

Tests:

* `test_identical_inputs_produce_identical_reports` — 10 scenarios, each generated
  twice from independent service instances, compared as objects, as JSON, and by
  id/fingerprint.
* `test_provider_record_order_does_not_change_the_report` — reversed provider
  order, identical report.
* `test_different_clocks_do_not_change_report_content_only_generated_at` — content
  and id stable, only `generated_at` differs.
* `test_report_is_stable_across_repeated_requests` (HTTP) — byte-identical
  responses.
* `test_foreign_source_is_deterministic_too`.

Ordering is fixed everywhere: gaps struggle-then-unexplored each sorted by topic
tag, signals in canonical order, evidence ids sorted, notices in fixed source
order. Mocks contain no randomness, no clock reads and no I/O.

---

## 10. Freshness / refresh proof

Every current-report request re-evaluates the source data, recomputes the report,
and compares fingerprints with the stored one.

```
at 10: id=gr_b4e40965ac2595b5634c050954fa9172 count=10 refreshed=True
at 11: id=gr_526d92d6dc6650378703f5e72c643ac1 count=11 refreshed=True
stored current reflects 11 interactions; versions saved: 2
unchanged source re-request: refreshed=False same_id=True
source shrinks to 9: status=below_threshold report=None
```

* A report generated at 10 is current at 10.
* Once an 11th qualifying interaction exists, the current report is regenerated,
  persisted and returned — the stale one is never served.
* Unchanged source state returns the stored report unchanged and writes nothing
  (one saved version, not two).
* If the source drops below the threshold, progress is returned — never a stale
  report.

Tests: `test_current_report_reflects_an_eleventh_interaction`,
`test_threshold_is_re_evaluated_against_current_source_data`,
`test_report_is_stable_across_repeated_requests_in_one_service`.

---

## 11. Read-only architecture proof

`tests/architecture/test_read_only_architecture.py` (49 tests) inspects classes,
not comments:

* Every read-only port (`InteractionLogProvider`, `FeedbackProvider`,
  `LearnerProfileProvider`, `CoursesProvider`) has **no** member whose name starts
  with `create|update|delete|patch|save|write|put|post|insert|upsert|store|persist|push|send|mutate|set_|add_|remove_|modify|edit|record_|submit`.
* Every adapter implementing those ports is checked the same way — mocks, foreign
  adapters, and the real-adapter template.
* `test_gap_report_repository_is_the_only_write_seam` walks **every class in the
  whole `uc07` package** and asserts that the only classes with a write-shaped
  member are `GapReportRepository` implementations.
* `test_repository_write_surface_is_exactly_save` — the repository's write surface
  is the single method `save`.
* `test_every_read_only_port_has_at_least_two_independent_adapters` — the ports
  are genuinely abstractions, not one-offs.
* Layering: the domain imports no application/adapter/API/framework code; the
  application imports no adapter or HTTP code; ports depend only on the domain.
* Banned technology: 26 import scans (langgraph, langchain, openai, anthropic,
  transformers, torch, chromadb, faiss, pinecone, sqlalchemy, redis, jinja2, …)
  plus a scan for `embedding`, `vector_store`, `rag_`, `prompt_template`.
* `test_no_frontend_assets_exist_in_the_repository` — no `.html/.css/.jsx/.tsx/.vue/.svelte` anywhere.
* Behavioural check: `test_repository_is_the_only_component_that_records_anything`
  re-runs the analysis twice and asserts the upstream mock payloads are unchanged
  and only one report version was written.

---

## 12. Privacy proof

| Guarantee | Mechanism | Test |
|-----------|-----------|------|
| No endpoint accepts a user id | Endpoints accept no query parameters and no body; identity comes from `CurrentUserProvider` | `test_no_endpoint_accepts_a_user_id_parameter` (walks the OpenAPI document), `test_query_parameters_are_rejected` (`user_id` → HTTP 400), `test_request_body_is_rejected` |
| Unknown request fields rejected | `reject_request_input` dependency | `test_unknown_query_parameters_are_rejected_on_progress_too` |
| No cross-user reads | Repository reads scoped by `user_id`; service re-checks ownership and raises `ReportOwnershipError` | `test_each_caller_only_ever_sees_their_own_data`, `test_cross_user_report_access_is_refused_at_the_service_boundary` (deliberately leaky repository → HTTP 403, no gap content in the body) |
| Report never carries identity | `report_out` drops `user_id` | `test_report_response_never_contains_the_user_id`, `test_report_payload_carries_no_learner_identity` |
| Question text never read/stored/emitted | `InteractionRecord` forbids unknown fields, so `question_text` cannot even be constructed; an AST scan forbids reading such a key anywhere | `test_interaction_record_rejects_forbidden_fields` (5 field names), `test_no_code_reads_a_question_text_key`, `test_question_text_is_only_ever_mentioned_in_order_to_forbid_it`, `test_response_contains_no_question_text_fields`, `test_openapi_schema_exposes_no_question_or_identity_fields` |
| Feedback comments never surface | Never mapped into a report or a log | `test_feedback_comments_never_reach_a_report`, `test_no_log_line_ever_contains_a_feedback_comment` |
| Logs carry no weak topics or report content | `log_event` drops every field outside an allowlist | `test_disallowed_fields_are_dropped_before_they_reach_a_log_record`, `test_report_generation_logs_counts_but_no_weak_topic_content` (asserts no gap topic or description appears in any log line), `test_progress_logging_stays_within_the_allowlist` |
| Errors leak nothing | Typed provider errors carry a port label only — no free-text parameter exists; handlers emit fixed messages | `test_error_responses_never_leak_provider_names_or_internals`, and the conformance kit's `assert_error_is_opaque` on every adapter |

Raw privacy probes:

```
no identity header     -> HTTP 401 {"error": {"code": "identity_unresolved", ...}}
user_id query param    -> HTTP 400 {"error": {"code": "invalid_request", "details": {"rejected_fields": ["user_id"]}}}
body supplied          -> HTTP 400 {"error": {"code": "invalid_request", "details": {"rejected_fields": ["body"]}}}
different learner      -> HTTP 200 {"status": "below_threshold", ..., "report": null}
'user_id' key in successful report body: False
'question' substring anywhere in report body: False
```

---

## 13. Degraded-source behaviour

Five statuses are preserved and never collapsed. Raw matrix
(`statuses=(interactions/feedback/profile/courses)`):

```
feedback_unavailable     statuses=(available/unavailable/available/available) recs=available    gaps=4 notices=['rating_signal_unavailable']
feedback_partial         statuses=(available/partial/available/available)     recs=available    gaps=4 notices=['rating_signal_partial']
feedback_empty           statuses=(available/empty/available/available)       recs=available    gaps=4 notices=['rating_signal_no_ratings']
feedback_invalid         statuses=(available/invalid/available/available)     recs=available    gaps=4 notices=['rating_signal_invalid']
profile_unavailable      statuses=(available/available/unavailable/available) recs=available    gaps=3 notices=['speciality_analysis_unavailable']
profile_partial          statuses=(available/available/partial/available)     recs=available    gaps=4 notices=['speciality_analysis_partial']
profile_invalid          statuses=(available/available/invalid/available)     recs=available    gaps=3 notices=['speciality_analysis_invalid']
profile_no_speciality    statuses=(available/available/empty/available)       recs=available    gaps=3 notices=['speciality_analysis_not_possible_no_speciality']
courses_unavailable      statuses=(available/available/available/unavailable) recs=unavailable  gaps=5 notices=['recommendations_temporarily_unavailable']
courses_partial          statuses=(available/available/available/partial)     recs=partial      gaps=5 notices=['recommendations_partial']
courses_invalid          statuses=(available/available/available/invalid)     recs=unavailable  gaps=5 notices=['recommendations_temporarily_unavailable']
interactions_partial     statuses=(partial/available/available/available)     recs=available    gaps=5 notices=['interaction_source_partial']
interactions_unavailable raised InteractionSourceUnusable (source_status=unavailable)
interactions_timeout     raised InteractionSourceUnusable (source_status=unavailable)
interactions_invalid     raised InteractionSourceUnusable (source_status=invalid)
```

Reading it:

* **Interaction source unusable → clear error (HTTP 503), never an empty report.**
  A timeout is a distinct exception type recorded as `unavailable`; an invalid
  payload is recorded as `invalid`.
* **`empty` ≠ `unavailable`** for feedback: `rating_signal_no_ratings` (info) vs
  `rating_signal_unavailable` (warning). Their fingerprints differ, asserted by
  `test_empty_and_unavailable_feedback_are_never_the_same_state`.
* Feedback unavailable removes only the low-rating signal, so
  `land_registration` (low-rating only) drops out and 4 gaps remain — the other
  signals are untouched.
* **Partial is never promoted to complete** for any source.
* **Courses down never shrinks the gap list**: 5 gaps with recommendations
  `unavailable` plus an explicit temporarily-unavailable notice.

---

## 14. Architecture and ports

```
API (FastAPI)            routes / schemas / uniform error envelope / strict input
        |  Depends
Composition root         provider registry -> Container (service, identity, repo, clock)
        |
Application              service, thresholds config, aggregation, signals,
                         unexplored, recommendations, evidence guard, assembly
        |
Domain                   contract models, enums, typed errors, THE counting rule
        |
Ports          read-only: InteractionLogProvider, FeedbackProvider,
                          LearnerProfileProvider, CoursesProvider
               write:     GapReportRepository (only)
               seams:     CurrentUserProvider, Clock
        |
Adapters       mock/ (deterministic), foreign/ ("Nexus LMS"), real/_template.py,
               persistence/ (in-memory), identity/ (header, static), clock/
```

Port signatures are reproduced in `docs/SHARED_CONTRACT.md` §5. Each read-only
port also exposes a read-only *status* accessor — that is what makes the
five-state source-status contract expressible through fixed method signatures
(A-44).

Typed provider errors: `ProviderUnavailable`, `ProviderTimeout`,
`ProviderInvalidResponse`, each carrying only a port label. The service branches
on these types; `except Exception` appears nowhere in it, proven behaviourally by
`test_service_never_catches_bare_exceptions_from_providers`.

---

## 15. Project structure

```
uc07/
  __init__.py                    REPORT_VERSION, ANALYSIS_VERSION
  observability.py               JSON logging + privacy allowlist
  composition.py                 provider registry + composition root
  domain/
    enums.py                     NARIC, source status, signals, notices, ...
    errors.py                    typed error taxonomy (no free-text payloads)
    models.py                    frozen contract + report models
    counting.py                  THE qualifying-interaction rule
  ports/
    read_only.py                 4 upstream ports (no write surface)
    persistence.py               GapReportRepository (the only write seam)
    identity.py                  CurrentUserProvider, Clock, IdentityUnresolved
  application/
    config.py                    Settings + AnalysisThresholds
    topic_descriptions.py        registry lookup (never generated)
    aggregation.py               full-history aggregation by supplied topic_tag
    signals.py                   3 independent struggle signals
    unexplored.py                speciality-coverage analysis
    recommendations.py           validation, enrolment -> lessons
    evidence_guard.py            rejects unresolvable evidence
    report_builder.py            deterministic assembly, notices, fingerprint
    service.py                   orchestration, threshold, freshness
  adapters/
    mock/                        interaction_log, feedback, profile, courses, scenarios
    foreign/                     adapters + Nexus payload (swap proof)
    real/_template.py            copy-me adapter with TODO markers
    persistence/in_memory.py     in-memory GapReportRepository
    identity/header.py           header + static CurrentUserProvider
    clock/clocks.py              SystemClock, FixedClock
  api/
    app.py, routes.py, schemas.py, errors.py, dependencies.py
  config/topic_descriptions.json  15 configured topic descriptions + default template
docs/     assumptions.md, SHARED_CONTRACT.md, INTEGRATION.md, FINAL_REPORT.md
evidence/ evidence.txt, sample_report.json, pytest_verbose.txt, test_list.txt
tests/    unit/ api/ architecture/ conformance/ integration/
.env.example, pyproject.toml, README.md
```

---

## 16. Mock scenarios

30 named scenarios, all pure functions of constants — no randomness, no sleeping,
no network, no API key. Selected by `MOCK_SCENARIO`; an unknown name fails loudly.

**Interaction counts**: `count_0`, `count_5`, `count_9`, `count_10`, `count_11`,
`count_50`.

**Topic shape**: `struggle_mixed` (14 interactions, 3 sessions, 5 topics, mixed
signals — the showcase), `diverse_topics` (6 topics), `narrow_topics` (1 topic →
insufficient diversity), `heavy_explain_differently` (explain signal only),
`heavy_follow_ups` (follow-up signal only).

**Counting edge cases**: `duplicate_interaction_ids` (12 raw → 10 qualifying),
`mixed_owner_records` (12 raw incl. 2 for another learner → 10 qualifying).

**Interaction source**: `interactions_unavailable`, `interactions_timeout`,
`interactions_invalid` (a record with a blank id and `LEVEL_99`),
`interactions_partial`.

**Feedback**: `feedback_clustered_down` (the default in `struggle_mixed`),
`feedback_empty`, `feedback_unavailable`, `feedback_partial`, `feedback_invalid`.

**Profile**: speciality with unexplored areas (default),
`profile_fully_covered`, `profile_no_speciality`, `profile_partial`,
`profile_unavailable`, `profile_invalid`.

**Courses**: valid catalogue with two deliberately unresolvable candidates
(default), `courses_not_enrolled`, `courses_only_invalid_candidates`,
`courses_unavailable`, `courses_partial`, `courses_invalid`.

**Foreign adapter** (`uc07/adapters/foreign/`): the same learner expressed in a
completely different upstream shape — `entryRef`, nested `conversation.ref`,
`occurredAtEpochMs` (epoch millis), nested `taxonomy.primary`, `promptKind` in
upper case, `eqfBand` = `EQF-6`, `reexplainTally`, `verdictLifecycle` =
`COMPLETE`/`AWAITING`, `sentiment` = `POSITIVE`/`NEGATIVE`, `completeness` =
`FULL`/`PARTIAL`/`ABSENT`, `programmeRef`/`moduleRef`, `focusAreas`,
`registrations`.

---

## 17. API endpoints and schemas

| Method | Path | Success | Notes |
|--------|------|---------|-------|
| GET | `/api/v1/gap-report` | 200 `GapReportEnvelopeOut` | `status` = `below_threshold` \| `available`; `report` is `null` below the threshold |
| GET | `/api/v1/gap-report/progress` | 200 `ProgressOut` | `status`, `interactions_completed`, `threshold`, `interactions_remaining` |
| GET | `/api/v1/healthz` | 200 `HealthOut` | `status`, `report_version`, `analysis_version`, `threshold` |

Below threshold:

```json
{"status":"below_threshold","interactions_completed":9,"threshold":10,"interactions_remaining":1,"report":null}
```

Available: `{"status":"available", "interactions_completed":14, "threshold":10,
"interactions_remaining":0, "report": {...}}` — the report carries `report_id`,
`generated_at`, `threshold`, `source_interaction_count`, `report_version`,
`analysis_version`, `gaps[]` (each with `topic_tag`, `gap_type`, `description`,
`description_source`, `signals[]`, `evidence_interaction_ids[]`, structured
`evidence`, `recommendations[]`), `recommendations` summary, `source_statuses`,
`topic_coverage`, `unexplored_analysis`, `notices[]`, `content_fingerprint` — and
never `user_id`.

Uniform error envelope: `{"error": {"code", "message", "details?"}}` with
`invalid_request` (400), `identity_unresolved` (401), `forbidden` (403),
`interaction_source_unusable` (503), `internal_error` (500). No endpoint accepts
input of any kind.

---

## 18. Test list and raw pytest output

Per-file counts (421 total):

```
 57  tests/unit/test_evidence.py
 49  tests/architecture/test_read_only_architecture.py
 26  tests/conformance/test_interaction_log_conformance.py
 26  tests/conformance/test_feedback_conformance.py
 26  tests/conformance/test_courses_conformance.py
 25  tests/test_docs_and_config.py
 22  tests/unit/test_counting_and_threshold.py
 22  tests/conformance/test_learner_profile_conformance.py
 21  tests/unit/test_resilience.py
 20  tests/api/test_endpoints.py
 18  tests/integration/test_provider_registry.py
 17  tests/unit/test_signals.py
 17  tests/unit/test_recommendations.py
 15  tests/unit/test_determinism_and_freshness.py
 12  tests/unit/test_unexplored.py
 11  tests/integration/test_foreign_adapter_swap.py
 11  tests/architecture/test_privacy_architecture.py
 10  tests/unit/test_report_assembly.py
  6  tests/unit/test_aggregation.py
  5  tests/unit/test_observability_privacy.py
  5  tests/integration/test_persistence.py
```

RAW output of `python -m pytest`:

```
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\Administrator\Documents\tas77
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2, asyncio-1.4.0, cov-7.1.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 421 items

tests\api\test_endpoints.py ....................                         [  4%]
tests\architecture\test_privacy_architecture.py ...........              [  7%]
tests\architecture\test_read_only_architecture.py .......................... [ 13%]
.......................                                                  [ 19%]
tests\conformance\test_courses_conformance.py .......................... [ 25%]
tests\conformance\test_feedback_conformance.py ..........................[ 31%]
tests\conformance\test_interaction_log_conformance.py .................... [ 37%]
......                                                                   [ 43%]
tests\conformance\test_learner_profile_conformance.py ..................... [ 48%]
.                                                                        [ 48%]
tests\integration\test_foreign_adapter_swap.py ...........               [ 51%]
tests\integration\test_persistence.py .....                              [ 52%]
tests\integration\test_provider_registry.py ..................           [ 57%]
tests\test_docs_and_config.py .........................                  [ 63%]
tests\unit\test_aggregation.py ......                                    [ 64%]
tests\unit\test_counting_and_threshold.py ......................         [ 70%]
tests\unit\test_determinism_and_freshness.py ...............             [ 73%]
tests\unit\test_evidence.py ............................................ [ 84%]
.............                                                            [ 87%]
tests\unit\test_observability_privacy.py .....                           [ 88%]
tests\unit\test_recommendations.py .................                     [ 92%]
tests\unit\test_report_assembly.py ..........                            [ 95%]
tests\unit\test_resilience.py .....................                      [100%]
tests\unit\test_unexplored.py ............                               [100%]

============================== warnings summary ===============================
..\..\AppData\Local\Programs\Python\Python311\Lib\site-packages\fastapi\testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.

======================= 421 passed, 1 warning in 1.85s ========================
```

The single warning comes from FastAPI's own `testclient` import, not from UC-07
code. The complete verbose run (every test id with its `PASSED` verdict) is in
`evidence/pytest_verbose.txt`; the full test-id list is in
`evidence/test_list.txt` and reproduced in **Appendix B**.

---

## 19. Total test count

**421 tests, 421 passed, 0 failed, 0 errors.**

---

## 20. Zero skipped tests — explicit confirmation

**Zero tests are skipped, xfailed, xpassed or disabled.**

```
$ python -m pytest 2>&1 | grep -E "passed|failed|skipped"
421 passed, 1 warning in 1.96s

$ grep -c PASSED evidence/pytest_verbose.txt
421
$ grep -cE "SKIPPED|XFAIL|XPASS|FAILED|ERROR" evidence/pytest_verbose.txt
0
```

The summary line contains no `skipped`/`xfailed` term at all, no
`pytest.mark.skip`/`xfail` marker exists anywhere in `tests/`, and no test was
disabled or commented out to make the suite pass.

---

---


## 21. docs/assumptions.md (reproduced in full)

## UC-07 Assumptions Register

Every assumption UC-07 makes is listed here. Nothing is hidden in code. If the
company contradicts one of these, the fix is a contract discussion plus an
adapter/configuration change — not a change to the domain model.

Legend for **Where in Code**: file paths are the single place that owns the
behaviour.

| ID | Area | Assumption | Why / Risk if Wrong | Where in Code |
|----|------|-----------|---------------------|---------------|
| A-01 | Interaction counting | A qualifying interaction is any valid `InteractionRecord` belonging to the resolved learner. Every such record counts exactly once. | Wrong: the threshold fires at the wrong moment for every learner. | `uc07/domain/counting.py` (`qualifying_interactions`) |
| A-02 | Interaction counting | Follow-up interactions count. A follow-up is a first-class interaction that happens to carry `follow_up_of`. | Wrong: learners who ask deeply but narrowly reach the threshold later than intended. | `uc07/domain/counting.py` |
| A-03 | Interaction counting | Clarifying interactions count when the platform represents them as an `InteractionRecord`. "Explain differently" is a *counter on* an interaction, not an interaction, so it never adds to the count. | Wrong: the threshold either double-counts one exchange or ignores real activity. | `uc07/domain/counting.py`; test `test_explain_differently_counter_does_not_add_to_the_count` |
| A-04 | Interaction counting | Duplicate `interaction_id` values count once; the first occurrence in provider order wins. Records whose `user_id` is not the resolved learner are discarded. | Wrong: retries/replays inflate the count, or another learner's data pollutes a report. | `uc07/domain/counting.py` |
| A-05 | Interaction counting | `InteractionLogProvider.count_for_user` is advisory only (logged as `provider_reported_count`). Threshold decisions always use the counting rule over the returned records. | Wrong (i.e. if the provider count were trusted): the threshold could disagree with the evidence actually available. | `uc07/application/service.py` (`_load_interactions`), `uc07/ports/read_only.py` |
| A-06 | Threshold | The report becomes available at exactly 10 qualifying interactions and stays available above it. Configurable via `GAP_REPORT_THRESHOLD`. | Wrong: reports appear too early (thin evidence) or too late (learner sees nothing). | `uc07/application/config.py`, `uc07/domain/models.py` (`ThresholdProgress`) |
| A-07 | Threshold | "Ten interactions" means the learner's **complete, all-time history across all sessions**, not the current session and not a rolling window. | Wrong: reports would reset or drift as sessions end. | `uc07/application/aggregation.py`, `uc07/ports/read_only.py` (`for_user`) |
| A-08 | Threshold | Being below the threshold is a normal state reported as progress with HTTP 200, never an error. | Wrong: clients would treat a new learner as a failure. | `uc07/api/routes.py`, `uc07/application/service.py` |
| A-09 | Signals | Explain-differently threshold = 2, measured as the **sum** of `explain_differently_count` across the topic's interactions. Evidence = the interactions whose counter is ≥ 1. | Wrong: either one puzzled moment flags a topic, or repeated confusion is missed. | `uc07/application/signals.py` (`_explain_differently_signal`) |
| A-10 | Signals | Follow-up threshold = 2, measured as the count of interactions in the topic whose `follow_up_of` is set. | Wrong: normal curiosity is read as struggle, or genuine struggle is missed. | `uc07/application/signals.py` (`_follow_up_signal`) |
| A-11 | Signals | Low-rating threshold = 1, measured as the count of thumbs-down `FeedbackRecord`s pointing at interactions in the topic **and** owned by the learner. | Wrong: a single unhappy rating may over-flag a topic; raising it in config is a one-line change. | `uc07/application/signals.py` (`_low_rating_signal`) |
| A-12 | Signals | Signals combine additively: a topic crossing two thresholds carries two signals, each with its own evidence and observed/threshold pair. A topic below **all** thresholds is not a struggle. | Wrong: reports either under- or over-report the reason for a gap. | `uc07/application/signals.py` (`detect_struggles`) |
| A-13 | Feedback | `FeedbackRecord.comment` is read as part of the contract but never emitted in a report and never logged. | Wrong direction (emitting it): learner free text could contain question content and leak. | `uc07/domain/models.py`, `uc07/observability.py` (allowlist) |
| A-14 | Topics | `topic_tag` is consumed exactly as supplied: no retagging, normalising, casing, classification or inference. Grouping is exact string equality. | Wrong: gaps would be attributed to topics the platform never assigned. | `uc07/application/aggregation.py` |
| A-15 | Speciality | Speciality areas are drawn from the same vocabulary as `topic_tag`, so coverage is decided by exact, case-sensitive string equality. No fuzzy matching. | Wrong: a speciality could look unexplored when it was covered under a differently spelled tag. Needs a company-supplied mapping if the vocabularies differ. | `uc07/application/unexplored.py` |
| A-16 | Speciality | An empty speciality set means the learner genuinely has none (`speciality_status = empty`). UC-07 never infers a speciality from history. | Wrong direction (inferring): the report would invent professional intent. | `uc07/application/unexplored.py`, `uc07/domain/models.py` (`LearnerProfile`) |
| A-17 | Speciality | Partial speciality data is used but flagged: state `performed_partial`, `may_be_incomplete = true`, plus a `speciality_analysis_partial` notice. Partial is never silently promoted to complete. | Wrong: learners would believe unexplored analysis was exhaustive. | `uc07/application/unexplored.py`, `uc07/application/report_builder.py` |
| A-18 | Speciality | The profile source status reported in the report is the status of the **speciality subsection** (`LearnerProfile.speciality_status`), since that is the only profile data UC-07 consumes. | Wrong: consumers might read it as the health of the whole profile system. | `uc07/application/unexplored.py` (`ProfileLoad`), `uc07/application/service.py` |
| A-19 | Gaps / evidence | Struggle gaps must carry at least one evidence interaction id, and every id must resolve to an interaction used in the analysis. A gap failing this is rejected, never emitted. | Wrong: unexplainable gaps would reach learners. | `uc07/application/evidence_guard.py`, `uc07/domain/models.py` (`Gap`) |
| A-20 | Gaps / evidence | Unexplored-speciality gaps carry **no** interaction ids by definition; their evidence basis is `zero_interactions_for_speciality_area`. That documented absence is the evidence. | Wrong: either unexplored gaps could not exist, or fake ids would be minted to satisfy a rule. | `uc07/domain/models.py` (`GapEvidence`), `uc07/application/report_builder.py` |
| A-21 | Topic diversity | Topic diversity is judged on the number of distinct topic tags in the learner's history (`MIN_TOPIC_AREAS`, default 3), not on the number of gaps. Fewer than the minimum adds an `insufficient_topic_diversity` notice; no gap is ever invented to reach it. | Wrong: reports would pad weak topics to hit a number. | `uc07/application/report_builder.py` |
| A-22 | Descriptions | Gap descriptions come from a configured topic-description registry file (`TOPIC_DESCRIPTION_REGISTRY_PATH`). Unknown tags use a single configured `default_template` containing the tag verbatim, marked `description_source = registry_default`. Descriptions are never generated. | Wrong: descriptions could become model output, i.e. unverifiable content in a professional record. | `uc07/application/topic_descriptions.py`, `uc07/config/topic_descriptions.json` |
| A-23 | Recommendations | A recommendation is valid only if its `course_id` exists in the courses catalogue and, for a lesson recommendation, the `lesson_id` exists inside that course. Invalid candidates are removed and never replaced with a guess. | Wrong: learners would be sent to non-existent courses. | `uc07/application/recommendations.py` |
| A-24 | Recommendations | If the learner is already enrolled in a recommended course, the course-level recommendation is replaced by the lessons in that course carrying the gap's topic tag. If no lesson matches, the candidate is dropped (counted as `dropped_already_enrolled_count`) rather than guessed. | Wrong: duplicate enrolment prompts, or an arbitrary lesson chosen for the learner. | `uc07/application/recommendations.py` |
| A-25 | Recommendations | Recommendation shape is `topic_tag` + `recommendation_type` (`course`/`lesson`) + `course_id` + optional `lesson_id` + optional `title`. Titles are display-only and never used for matching. | Wrong: the integration adapter must map more fields; the port signature stays the same. | `uc07/domain/models.py` (`Recommendation`) |
| A-26 | Recommendations | Gap analysis never depends on course availability. When the courses source fails, gaps stand and `recommendations.status = unavailable` with an explicit temporarily-unavailable notice. | Wrong: a course outage would silently shrink knowledge-gap reporting. | `uc07/application/service.py`, `uc07/application/report_builder.py` |
| A-27 | Source status | Five statuses are preserved and never collapsed: `available`, `empty`, `partial`, `unavailable`, `invalid`. `empty` (source answered with nothing) is never reported as `unavailable` (source could not answer). | Wrong: "no ratings" and "rating system down" would be indistinguishable. | `uc07/domain/enums.py`, `uc07/application/service.py` |
| A-28 | Source status | A source that returns zero records while reporting `available` is recorded as `empty`; a source reporting `partial` stays `partial` regardless of volume. This is derivation, not silent conversion. | Wrong: reports would claim complete data where none exists. | `uc07/application/service.py` (`_load_interactions`, `_load_feedback`) |
| A-29 | Source status | A provider timeout is surfaced as `ProviderTimeout` and recorded as source status `unavailable` (the five-state contract has no `timeout` member). The distinct exception type is preserved for handling and logging. | Wrong: timeouts and outages would be indistinguishable in logs. | `uc07/application/service.py`, `uc07/domain/errors.py` |
| A-30 | Source status | Interaction history that is `unavailable` or `invalid` produces HTTP 503 with a clear error. UC-07 never returns an empty report in that case. | Wrong: a learner would be told they have no gaps when the data simply could not be read. | `uc07/api/errors.py`, `uc07/application/service.py` |
| A-31 | Interaction record shape | Fields are exactly: `interaction_id`, `session_id`, `user_id`, `asked_at`, `topic_tag`, `question_class`, `naric_level`, `response_id`, `follow_up_of`, `explain_differently_count`, `rating_state`. There is no question text, and unknown fields are rejected outright. | Wrong: adapters must map upstream extras away; the model must not grow. | `uc07/domain/models.py` (`InteractionRecord`) |
| A-32 | Interaction record shape | `question_class` is an open, non-empty string (the company has not published an enumeration). Mocks use `concept`, `application`, `clarification`. | Wrong: if it is a closed enum, add it in `enums.py` and map it in adapters; no analysis logic depends on it today. | `uc07/domain/models.py` |
| A-33 | Interaction record shape | `asked_at`/`rated_at` must be timezone-aware and are normalised to UTC. `follow_up_of` is `null` or a non-empty id, and may not reference the record itself. | Wrong: ordering, determinism and follow-up counting would be unstable. | `uc07/domain/models.py` |
| A-34 | Feedback record shape | Fields are exactly: `rating_id`, `interaction_id`, `user_id`, `rated_at`, `rating` (`up`/`down`), `comment`. Only thumbs-down is a struggle signal; thumbs-up is never one. | Wrong: rating semantics beyond up/down (e.g. stars) would need a contract discussion. | `uc07/domain/models.py` (`FeedbackRecord`), `uc07/application/signals.py` |
| A-35 | Enrolment shape | Enrolment is `user_id` + `course_id` + optional `enrolled_at` + optional `completion_percentage` (integer 0–100 only). | Wrong: an adapter mapping a float or a 0–1 ratio must convert or raise a contract error. | `uc07/domain/models.py` (`Enrolment`) |
| A-36 | Report identity | `report_id` is derived from the content fingerprint (`gr_<first 32 hex of sha256>`), and `content_fingerprint` is the sha256 of the canonical JSON of the report content excluding id and timestamp. | Wrong: reports could not be compared for equality or freshness without a database. | `uc07/domain/models.py` (`fingerprint_of`, `report_id_for`) |
| A-37 | Report freshness | Every current-report request re-evaluates the source data. If the recomputed content matches the stored report, the stored report (with its original `generated_at`) is returned; otherwise a new report is generated, persisted and returned. A stale report is never served. | Wrong: learners would see a snapshot that no longer matches their history. | `uc07/application/service.py` (`current_report`) |
| A-38 | Report versioning | `report_version` describes the document shape; `analysis_version` describes the derivation rules. Both are bumped deliberately in `uc07/__init__.py`; consumers can pin behaviour on them. | Wrong: silent rule changes would be indistinguishable from data changes. | `uc07/__init__.py` |
| A-39 | Determinism | Identical source data plus identical configuration produce an identical report, including `report_id`. Ordering is fixed (gaps: struggle then unexplored, each by topic tag; signals in canonical order; evidence ids sorted). | Wrong: reports could not be audited or diffed. | `uc07/application/report_builder.py` |
| A-40 | Identity | The learner identity comes only from `CurrentUserProvider`. The API accepts no path, query or body input at all, so a `user_id` cannot be supplied by a caller. | Wrong: one learner could read another's professional-development data. | `uc07/api/dependencies.py`, `uc07/adapters/identity/header.py` |
| A-41 | Identity | The header-based `CurrentUserProvider` is a development seam, not production authentication. Production replaces the adapter, not the rule. | Wrong: treating the header as authentication in production would be an authorisation hole. | `uc07/adapters/identity/header.py` |
| A-42 | Logging | Logs may carry user id, counts, statuses and timings. Weak topics, gap descriptions, report contents, feedback comments and question text are never logged; a field allowlist enforces this at the call site. | Wrong: log aggregation would become a sensitive-data store. | `uc07/observability.py` |
| A-43 | Persistence | The only data UC-07 writes is the gap report it generates, through `GapReportRepository`. The local implementation is in-memory; a real deployment swaps the adapter. Upstream sources are read-only, enforced by architecture tests. | Wrong: UC-07 would own data it does not own. | `uc07/ports/persistence.py`, `uc07/adapters/persistence/in_memory.py` |
| A-44 | Ports | Each read-only port exposes an extra read accessor for source status (`status_for_user`, `status_for_interactions`, `status()`, and `LearnerProfile.speciality_status`). This is what allows `partial`/`empty` to be preserved through the fixed method signatures. | Wrong: `partial` would be indistinguishable from `available` and the five-state contract could not be honoured. | `uc07/ports/read_only.py` |
| A-45 | Providers | Provider selection is registry-driven. An unknown provider name fails at startup; there is never a silent fallback to mocks. | Wrong: a mis-set environment variable would serve mock data in production. | `uc07/composition.py` |
| A-46 | Time | The clock is a port. Report timestamps come from it, so generation is reproducible in tests. | Wrong: determinism tests could not distinguish content changes from clock ticks. | `uc07/ports/identity.py`, `uc07/adapters/clock/clocks.py` |
| A-47 | Session identity | UC-07 never creates a `session_id`; it only reads them for grouping/observability, and treats them as opaque. | Wrong: UC-07 would be participating in session lifecycle, which is not its use case. | `uc07/application/aggregation.py` |
| A-48 | Recommendation scope | Recommendations are requested only for topics that became gaps, and candidates for other topics are ignored (not counted as rejects). | Wrong: reject counters would be noisy and recommendations could drift off-report. | `uc07/application/recommendations.py` |

---

## 22. docs/SHARED_CONTRACT.md (reproduced in full)

## UC-07 Shared Contract

This is the contract UC-07 consumes and produces. Every field is marked
**SPECIFIED BY COMPANY** (fixed platform contract, must not be changed locally) or
**ASSUMED BY US** (a UC-07 modelling decision; see `docs/assumptions.md`).

### Data ownership

**UC-07 READS:**

* interaction data (coaching history) — `InteractionLogProvider`
* feedback data (ratings) — `FeedbackProvider`
* learner profile (speciality areas, NARIC level) — `LearnerProfileProvider`
* course data (catalogue, recommendations, enrolments) — `CoursesProvider`

**UC-07 WRITES:**

* generated gap reports only — `GapReportRepository`

**UC-07 NEVER writes upstream data.** It creates no session, records no
interaction, stores no rating, and changes no profile, enrolment or course. This
is enforced by architecture tests over both ports and adapters
(`tests/architecture/test_read_only_architecture.py`).

---

### 1. Enumerations

#### NaricLevel — SPECIFIED BY COMPANY

`LEVEL_3 | LEVEL_4 | LEVEL_5 | LEVEL_6 | LEVEL_7 | LEVEL_7_PLUS`

No integer NARIC scale exists. Adapters map upstream spellings explicitly; an
unmappable value is a contract error.

#### NaricLevelSource — SPECIFIED BY COMPANY

`retrieved | default`

#### SourceStatus — SPECIFIED BY COMPANY

`available | empty | partial | unavailable | invalid`

`empty` ≠ `unavailable`. Statuses are preserved, never collapsed.

#### RatingState — SPECIFIED BY COMPANY

`pending | rated` (field on `InteractionRecord`)

#### Rating — SPECIFIED BY COMPANY

`up | down` (field on `FeedbackRecord`)

#### ThresholdStatus — ASSUMED BY US

`below_threshold | available`

#### GapType — ASSUMED BY US

`struggle | unexplored`

#### SignalKind — ASSUMED BY US

`explain_differently | follow_up | low_rating | unexplored_speciality`

Canonical order is exactly the order above; every report uses it.

#### EvidenceBasis — ASSUMED BY US

`interaction_ids | zero_interactions_for_speciality_area`

#### RecommendationStatus — ASSUMED BY US

`available | partial | unavailable | empty`

#### RecommendationType — ASSUMED BY US

`course | lesson`

#### DescriptionSource — ASSUMED BY US

`registry | registry_default`

#### UnexploredAnalysisState — ASSUMED BY US

`performed | performed_partial | not_performed_no_speciality |
not_performed_profile_unavailable | not_performed_profile_invalid`

#### NoticeSeverity / NoticeCode — ASSUMED BY US

Severity: `info | warning`. Codes:
`recommendations_temporarily_unavailable`, `recommendations_partial`,
`rating_signal_unavailable`, `rating_signal_partial`, `rating_signal_no_ratings`,
`rating_signal_invalid`, `speciality_analysis_unavailable`,
`speciality_analysis_invalid`, `speciality_analysis_partial`,
`speciality_analysis_not_possible_no_speciality`,
`insufficient_topic_diversity`, `interaction_source_partial`.

---

### 2. Upstream types (read-only)

#### InteractionRecord

| Field | Type | Ownership | Notes |
|-------|------|-----------|-------|
| `interaction_id` | string, non-empty | SPECIFIED BY COMPANY | Opaque. |
| `session_id` | string, non-empty | SPECIFIED BY COMPANY | Opaque; UC-07 never creates one. |
| `user_id` | string, non-empty | SPECIFIED BY COMPANY | Must equal the resolved learner. |
| `asked_at` | datetime, tz-aware (UTC) | SPECIFIED BY COMPANY | ASSUMED BY US: tz-aware required, normalised to UTC (A-33). |
| `topic_tag` | string, non-empty | SPECIFIED BY COMPANY | Consumed exactly as supplied (A-14). |
| `question_class` | string, non-empty | SPECIFIED BY COMPANY (values ASSUMED BY US) | Open string; no enumeration published (A-32). |
| `naric_level` | `NaricLevel` | SPECIFIED BY COMPANY | |
| `response_id` | string, non-empty | SPECIFIED BY COMPANY | Opaque; never dereferenced. |
| `follow_up_of` | string \| null | SPECIFIED BY COMPANY | Null or a non-empty id; never self-referencing (A-33). |
| `explain_differently_count` | integer ≥ 0 | SPECIFIED BY COMPANY | A counter, not an interaction (A-03). |
| `rating_state` | `RatingState` | SPECIFIED BY COMPANY | Not used as a signal. |

**There is no `question_text` field.** UC-07 never retrieves, infers,
reconstructs, stores, emits or logs question text. Unknown fields — including
`question_text` — are rejected at construction.

#### FeedbackRecord

| Field | Type | Ownership | Notes |
|-------|------|-----------|-------|
| `rating_id` | string, non-empty | SPECIFIED BY COMPANY | |
| `interaction_id` | string, non-empty | SPECIFIED BY COMPANY | Must resolve to an analysed interaction to count. |
| `user_id` | string, non-empty | SPECIFIED BY COMPANY | Ratings owned by another learner are ignored. |
| `rated_at` | datetime, tz-aware (UTC) | SPECIFIED BY COMPANY | |
| `rating` | `Rating` | SPECIFIED BY COMPANY | Only `down` is a struggle signal. |
| `comment` | string \| null | SPECIFIED BY COMPANY | Read, never emitted, never logged (A-13). |

#### LearnerProfile

| Field | Type | Ownership | Notes |
|-------|------|-----------|-------|
| `user_id` | string, non-empty | SPECIFIED BY COMPANY | |
| `speciality_areas` | tuple[string] | SPECIFIED BY COMPANY | Same vocabulary as `topic_tag` (A-15); duplicates removed, order preserved. |
| `speciality_status` | `SourceStatus` | ASSUMED BY US | Status of the speciality subsection (A-18). `available` requires ≥1 area; `empty` requires none. |
| `naric_level` | `NaricLevel` \| null | SPECIFIED BY COMPANY | |
| `naric_level_source` | `NaricLevelSource` \| null | SPECIFIED BY COMPANY | Required when `naric_level` is present. |

#### CourseSummary / LessonSummary — ASSUMED BY US

`CourseSummary`: `course_id` (non-empty), `title` (nullable), `topic_tags`
(tuple[string]), `lessons` (tuple[`LessonSummary`]).
`LessonSummary`: `lesson_id` (non-empty), `title` (nullable), `topic_tags`
(tuple[string]).

Used solely to validate that recommendation identifiers exist and to find the
lessons in an already-enrolled course that carry a gap's topic.

#### Enrolment — ASSUMED BY US

`user_id`, `course_id`, `enrolled_at` (datetime \| null),
`completion_percentage` (integer 0–100 \| null — SPECIFIED BY COMPANY: integer
0–100 only).

#### Recommendation — ASSUMED BY US

`topic_tag`, `recommendation_type` (`course`/`lesson`), `course_id`,
`lesson_id` (required for `lesson`, forbidden for `course`), `title` (nullable,
display only).

---

### 3. UC-07 output types

#### SignalEvidence — ASSUMED BY US

| Field | Type | Notes |
|-------|------|-------|
| `signal` | `SignalKind` | |
| `observed_value` | integer ≥ 0 | Must be ≥ `threshold`; a signal cannot be recorded otherwise. |
| `threshold` | integer ≥ 0 | The configured threshold that was crossed. |
| `interaction_ids` | tuple[string] | Subset of the gap's evidence ids. |

#### GapEvidence — ASSUMED BY US

| Field | Type | Notes |
|-------|------|-------|
| `basis` | `EvidenceBasis` | |
| `interaction_ids` | tuple[string] | Non-empty and unique for `interaction_ids` basis; empty for the zero-interaction basis. |
| `per_signal` | tuple[`SignalEvidence`] | Every per-signal id must be inside `interaction_ids`. |

#### Gap — ASSUMED BY US

| Field | Type | Notes |
|-------|------|-------|
| `topic_tag` | string | Exactly as supplied upstream, or a speciality area. |
| `gap_type` | `GapType` | |
| `description` | string | From the topic-description registry only. |
| `description_source` | `DescriptionSource` | Proves it was looked up, not generated. |
| `signals` | tuple[`SignalKind`] | Canonical order, no repeats, matches `evidence.per_signal`. |
| `evidence` | `GapEvidence` | Mandatory. |
| `evidence_interaction_ids` | list[string] (API) | Flat mirror of `evidence.interaction_ids`. |
| `recommendations` | tuple[`Recommendation`] | All validated; topic must match the gap. |

#### GapReport — ASSUMED BY US (except where noted)

| Field | Type | Notes |
|-------|------|-------|
| `report_id` | string | `gr_<32 hex>`, derived from `content_fingerprint`. |
| `user_id` | string | **Internal only.** Stored and ownership-checked; never serialised to the API. |
| `generated_at` | datetime (UTC) | From the `Clock` port. |
| `threshold` | integer | The configured threshold in force. |
| `source_interaction_count` | integer | Qualifying interactions used. |
| `report_version` | string | Document shape version. |
| `analysis_version` | string | Derivation-rules version. |
| `gaps` | tuple[`Gap`] | Struggle gaps (by topic), then unexplored gaps (by topic). |
| `recommendations` | `RecommendationSummary` | `status`, `resolved_count`, `rejected_unresolvable_count`, `converted_to_lesson_count`, `dropped_already_enrolled_count`. |
| `source_statuses` | `SourceStatuses` | `interactions`, `feedback`, `profile`, `courses` — each a `SourceStatus`. |
| `topic_coverage` | `TopicCoverage` | `identifiable_topic_areas`, `minimum_expected_topic_areas`, `sufficient_topic_diversity`, `topic_areas_in_history`. |
| `unexplored_analysis` | `UnexploredAnalysis` | `state`, `speciality_status`, `speciality_areas_considered`, `unexplored_areas_found`, `may_be_incomplete`, `explanation`. |
| `notices` | tuple[`Notice`] | `code`, `severity`, `message`. |
| `content_fingerprint` | string | sha256 over the canonical content JSON (excluding id/timestamp). |

#### Threshold state — `ThresholdProgress`

| Field | Type | Notes |
|-------|------|-------|
| `status` | `ThresholdStatus` | Derived from the count; never an error. |
| `interactions_completed` | integer ≥ 0 | Qualifying interactions. |
| `threshold` | integer ≥ 0 | Configured. |
| `interactions_remaining` | integer ≥ 0 | `max(0, threshold - completed)`. |

---

### 4. HTTP contract

#### `GET /api/v1/gap-report`

Below threshold (HTTP 200):

```json
{
  "status": "below_threshold",
  "interactions_completed": 9,
  "threshold": 10,
  "interactions_remaining": 1,
  "report": null
}
```

Available (HTTP 200):

```json
{
  "status": "available",
  "interactions_completed": 14,
  "threshold": 10,
  "interactions_remaining": 0,
  "report": { "...": "GapReport without user_id" }
}
```

#### `GET /api/v1/gap-report/progress` (HTTP 200)

```json
{
  "status": "below_threshold",
  "interactions_completed": 5,
  "threshold": 10,
  "interactions_remaining": 5
}
```

#### `GET /api/v1/healthz` (HTTP 200)

```json
{"status": "ok", "report_version": "1.0.0", "analysis_version": "1.0.0", "threshold": 10}
```

No endpoint accepts a user id, a query parameter or a body.

#### Error envelope (uniform)

```json
{
  "error": {
    "code": "interaction_source_unusable",
    "message": "Coaching interaction history could not be loaded, ...",
    "details": {"interaction_source_status": "unavailable"}
  }
}
```

| HTTP | `code` | When |
|------|--------|------|
| 400 | `invalid_request` | Any query parameter or body was supplied (`details.rejected_fields`). |
| 401 | `identity_unresolved` | `CurrentUserProvider` could not resolve a learner. |
| 403 | `forbidden` | A stored report did not belong to the resolved learner. |
| 503 | `interaction_source_unusable` | Interaction history was `unavailable`/`invalid` (`details.interaction_source_status`). |
| 500 | `internal_error` | Anything unexpected. No message detail, ever. |

Error responses never contain report contents, weak topics, provider names,
upstream error text, internal exception messages or stack traces.

---

### 5. Port contract

```
InteractionLogProvider      (read-only)
    for_user(user_id) -> list[InteractionRecord]
    count_for_user(user_id) -> int                 # advisory only (A-05)
    status_for_user(user_id) -> SourceStatus       # ASSUMED BY US (A-44)

FeedbackProvider            (read-only)
    for_interactions(ids) -> list[FeedbackRecord]
    status_for_interactions(ids) -> SourceStatus   # ASSUMED BY US (A-44)

LearnerProfileProvider      (read-only)
    get_profile(user_id) -> LearnerProfile

CoursesProvider             (read-only)
    resolve_recommendations(topics) -> list[Recommendation]
    enrolments_for(user_id) -> list[Enrolment]
    catalogue() -> list[CourseSummary]             # ASSUMED BY US (validation)
    status() -> SourceStatus                       # ASSUMED BY US (A-44)

GapReportRepository         (the ONLY write seam)
    save(report) -> None
    get_current(user_id) -> GapReport | None

CurrentUserProvider
    resolve(request) -> user_id

Clock                       (ASSUMED BY US, for determinism)
    now() -> datetime
```

Typed provider errors: `ProviderUnavailable`, `ProviderTimeout`,
`ProviderInvalidResponse`. Each carries a port label only — never a provider
name, URL, payload or upstream message.

---

### 6. Extension points

| Extension point | How to extend | What must NOT change |
|-----------------|---------------|----------------------|
| New upstream source (any port) | Copy `uc07/adapters/real/_template.py`, add one registry line in `uc07/composition.py`, set one environment variable. | Domain models, application services, API, persistence, existing mock adapters, existing tests. |
| Thresholds (`GAP_REPORT_THRESHOLD`, `MIN_TOPIC_AREAS`, `EXPLAIN_DIFFERENTLY_STRUGGLE_THRESHOLD`, `LOW_RATING_STRUGGLE_THRESHOLD`, `FOLLOW_UP_STRUGGLE_THRESHOLD`) | Environment/config only. | No business logic; `AnalysisThresholds` is passed in. |
| Topic descriptions | Edit the registry JSON at `TOPIC_DESCRIPTION_REGISTRY_PATH`. | No code change; never generated text. |
| New struggle signal | Add a detector in `uc07/application/signals.py` returning `SignalEvidence`, add its `SignalKind` and its position in `SIGNAL_ORDER`, add a configured threshold, bump `ANALYSIS_VERSION`. | Evidence rules and the guard stay as they are. |
| Real persistence | Implement `GapReportRepository` and pass it to `build_container`. | Service, API and domain untouched. |
| Real authentication | Implement `CurrentUserProvider` and register it under `CURRENT_USER_PROVIDERS`. | The rule that identity is server-side only. |
| Report shape change | Bump `REPORT_VERSION` (and `ANALYSIS_VERSION` if rules changed) in `uc07/__init__.py`. | Consumers pin on those versions. |
| Clock / time source | Provide a `Clock` implementation. | Determinism guarantees. |

---

## 23. docs/INTEGRATION.md (reproduced in full)

## UC-07 Integration Runbook

For an engineer who has never seen this repository. Integrating a real company
source means writing **one adapter file**, adding **one registry line**, and
changing **one environment variable**. Nothing else.

Read `docs/SHARED_CONTRACT.md` for the types and `docs/assumptions.md` for the
assumptions you must confirm before you start.

---

### 0. Ground rules

1. The adapter is the **only** place that may know upstream field names, nesting,
   URLs, authentication, value spellings or error strings.
2. The adapter **never invents data**. If the payload cannot satisfy the platform
   contract, raise `ProviderInvalidResponse`. Do not default, guess, or widen a
   domain model.
3. The adapter is **read-only**. No `create`/`update`/`delete`/`patch`/`save`/
   `write`/`post`/`put`/`insert` method may exist — architecture tests fail the
   build if one appears.
4. Authorization stays **server-side**: the adapter receives a `user_id` that was
   resolved by `CurrentUserProvider`. It must never take an identity from a
   request payload, and never echo an upstream identity back into a record.
5. A contract mismatch is a **contract discussion**, not a domain-model hack.

---

### 1. Per-dependency reference

#### 1.1 Interaction log (coaching history)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/real/<system>_interaction_log.py` |
| Template to copy | `uc07/adapters/real/_template.py` |
| Port interface | `uc07.ports.read_only.InteractionLogProvider` |
| Methods | `for_user(user_id) -> list[InteractionRecord]`, `count_for_user(user_id) -> int`, `status_for_user(user_id) -> SourceStatus` |
| Registry line | `INTERACTION_LOG_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` in `uc07/composition.py` |
| Environment variable | `INTERACTION_LOG_PROVIDER=<name>` |
| Conformance command | `pytest tests/conformance/test_interaction_log_conformance.py -q` |
| Assumptions to verify first | A-01…A-05, A-07, A-14, A-31, A-32, A-33, A-44 |

#### 1.2 Feedback (ratings)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/real/<system>_feedback.py` |
| Template to copy | `uc07/adapters/real/_template.py` |
| Port interface | `uc07.ports.read_only.FeedbackProvider` |
| Methods | `for_interactions(ids) -> list[FeedbackRecord]`, `status_for_interactions(ids) -> SourceStatus` |
| Registry line | `FEEDBACK_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` |
| Environment variable | `FEEDBACK_PROVIDER=<name>` |
| Conformance command | `pytest tests/conformance/test_feedback_conformance.py -q` |
| Assumptions to verify first | A-11, A-13, A-27, A-28, A-34, A-44 |

#### 1.3 Learner profile (speciality areas, NARIC level)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/real/<system>_profile.py` |
| Template to copy | `uc07/adapters/real/_template.py` |
| Port interface | `uc07.ports.read_only.LearnerProfileProvider` |
| Methods | `get_profile(user_id) -> LearnerProfile` |
| Registry line | `PROFILE_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` |
| Environment variable | `PROFILE_PROVIDER=<name>` |
| Conformance command | `pytest tests/conformance/test_learner_profile_conformance.py -q` |
| Assumptions to verify first | A-15, A-16, A-17, A-18 (and the NARIC mapping table) |

#### 1.4 Courses (catalogue, recommendations, enrolments)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/real/<system>_courses.py` |
| Template to copy | `uc07/adapters/real/_template.py` |
| Port interface | `uc07.ports.read_only.CoursesProvider` |
| Methods | `resolve_recommendations(topics)`, `enrolments_for(user_id)`, `catalogue()`, `status()` |
| Registry line | `COURSES_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` |
| Environment variable | `COURSES_PROVIDER=<name>` |
| Conformance command | `pytest tests/conformance/test_courses_conformance.py -q` |
| Assumptions to verify first | A-23, A-24, A-25, A-26, A-35, A-48 |

#### 1.5 Gap-report persistence (the only write seam)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/persistence/<store>.py` |
| Port interface | `uc07.ports.persistence.GapReportRepository` |
| Methods | `save(report) -> None`, `get_current(user_id) -> GapReport \| None` |
| Wiring | pass it to `build_container(settings, repository=...)` |
| Requirement | `get_current` MUST scope by `user_id`; a caller may never receive another learner's report. |
| Assumptions to verify first | A-36, A-37, A-43 |

#### 1.6 Identity (`CurrentUserProvider`)

| Item | Value |
|------|-------|
| Adapter file to create | `uc07/adapters/identity/<mechanism>.py` |
| Port interface | `uc07.ports.identity.CurrentUserProvider` |
| Methods | `resolve(request) -> str`, raising `IdentityUnresolved` |
| Registry line | `CURRENT_USER_PROVIDERS["<name>"] = lambda settings: <Adapter>(...)` |
| Environment variable | `CURRENT_USER_PROVIDER=<name>` |
| Requirement | Identity is resolved server-side. The API accepts no input, so never read one from the request body, path or query. |

---

### 2. Worked example (complete)

Company system: "Acme Coach API". It exposes
`GET /v3/learners/{id}/coaching?cursor=…` returning:

```json
{
  "meta": {"completeness": "COMPLETE", "total": 14},
  "items": [
    {
      "id": "ci-1",
      "thread": {"id": "th-9"},
      "createdAt": 1767603600,
      "tags": {"main": "contract_formation"},
      "kind": "CONCEPT",
      "level": "L6",
      "answer": {"id": "an-1"},
      "parentId": null,
      "rephraseCount": 2,
      "ratingState": "RATED",
      "questionText": "…"
    }
  ]
}
```

#### Step 1 — the adapter file

`uc07/adapters/real/acme_interaction_log.py`:

```python
"""Acme Coach API adapter for the InteractionLogProvider port (read-only)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError

from uc07.domain.enums import NaricLevel, SourceStatus
from uc07.domain.errors import (
    PortName,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import InteractionRecord
from uc07.ports.read_only import InteractionLogProvider

_PORT = PortName.INTERACTION_LOG

# Upstream vocabularies live here and nowhere else.
_LEVELS = {
    "L3": NaricLevel.LEVEL_3,
    "L4": NaricLevel.LEVEL_4,
    "L5": NaricLevel.LEVEL_5,
    "L6": NaricLevel.LEVEL_6,
    "L7": NaricLevel.LEVEL_7,
    "L7P": NaricLevel.LEVEL_7_PLUS,
}
_COMPLETENESS = {
    "COMPLETE": SourceStatus.AVAILABLE,
    "TRUNCATED": SourceStatus.PARTIAL,
    "NONE": SourceStatus.EMPTY,
}
_RATING_STATE = {"RATED": "rated", "AWAITING": "pending"}


class AcmeInteractionLogProvider(InteractionLogProvider):
    def __init__(self, *, base_url: str, token: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds

    # -- transport + error translation -----------------------------------
    def _get(self, user_id: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self._base_url}/v3/learners/{user_id}/coaching",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(_PORT) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(_PORT) from exc

        if response.status_code >= 500:
            raise ProviderUnavailable(_PORT)
        if response.status_code >= 400:
            raise ProviderInvalidResponse(_PORT)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderInvalidResponse(_PORT) from exc
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(_PORT)
        return payload

    # -- payload mapping --------------------------------------------------
    def _map(self, raw: dict[str, Any], user_id: str) -> InteractionRecord:
        try:
            level = _LEVELS[raw["level"]]                     # unknown -> KeyError -> typed error
            rating_state = _RATING_STATE[raw["ratingState"]]
            return InteractionRecord(
                interaction_id=raw["id"],
                session_id=raw["thread"]["id"],
                user_id=user_id,                              # server-side identity
                asked_at=datetime.fromtimestamp(raw["createdAt"], tz=timezone.utc),
                topic_tag=raw["tags"]["main"],                # consumed as supplied
                question_class=str(raw["kind"]).lower(),
                naric_level=level,
                response_id=raw["answer"]["id"],
                follow_up_of=raw.get("parentId"),
                explain_differently_count=raw.get("rephraseCount", 0),
                rating_state=rating_state,
                # questionText is deliberately NOT read, mapped, stored or logged.
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderInvalidResponse(_PORT) from exc

    # -- port -------------------------------------------------------------
    def for_user(self, user_id: str) -> Sequence[InteractionRecord]:
        payload = self._get(user_id)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ProviderInvalidResponse(_PORT)
        return tuple(self._map(raw, user_id) for raw in items)

    def count_for_user(self, user_id: str) -> int:
        total = self._get(user_id).get("meta", {}).get("total")
        if not isinstance(total, int) or total < 0:
            raise ProviderInvalidResponse(_PORT)
        return total

    def status_for_user(self, user_id: str) -> SourceStatus:
        raw = self._get(user_id).get("meta", {}).get("completeness")
        if raw not in _COMPLETENESS:
            raise ProviderInvalidResponse(_PORT)
        return _COMPLETENESS[raw]
```

#### Step 2 — the registry line

In `uc07/composition.py`, inside `INTERACTION_LOG_PROVIDERS`:

```python
    "acme": lambda settings: AcmeInteractionLogProvider(
        base_url=settings.acme_base_url,
        token=settings.acme_token,
        timeout_seconds=settings.provider_timeout_seconds,
    ),
```

(Plus the import, and the two `acme_*` fields on `Settings` if the adapter needs
configuration — configuration belongs to `Settings`, never `os.environ` reads
inside the adapter.)

#### Step 3 — the environment variable

```
INTERACTION_LOG_PROVIDER=acme
ACME_BASE_URL=https://<placeholder>
ACME_TOKEN=<placeholder>
```

#### Step 4 — join the conformance suite

Add ONE case to `tests/conformance/adapters.py`:

```python
    AdapterCase(
        id="acme",
        user_id="learner-001",
        upstream_tokens=("rephraseCount", "ratingState", "questionText", "Acme"),
        build=lambda: AcmeInteractionLogProvider(...),          # against a stub transport
        build_unavailable=...,
        build_timeout=...,
        build_invalid=...,
        build_empty=...,
    ),
```

Then run:

```
pytest tests/conformance -q          # the suite itself is NOT modified
pytest -q                            # full suite
```

#### Step 5 — verify nothing else changed

```
git diff --name-only
```

The expected diff: the new adapter file, `uc07/composition.py`, `Settings`,
`.env`, and one entry in `tests/conformance/adapters.py`. If any of these appear,
stop and reconsider:

* `uc07/domain/**` — domain models or the counting rule
* `uc07/application/**` — services, signals, report assembly
* `uc07/api/**` — routes or schemas
* `uc07/adapters/mock/**` — existing mock adapters
* `uc07/adapters/persistence/**` — persistence
* existing tests

---

### 3. Pre-flight checklist

Before writing any adapter, confirm with the company:

* [ ] Does the interaction endpoint return the learner's **complete** history, or a page/window? (A-07)
* [ ] Are duplicate interaction ids possible on retry? (A-04)
* [ ] Are clarifying/follow-up exchanges separate records, or nested? (A-02, A-03)
* [ ] Exact upstream spelling of NARIC levels, and behaviour for unknown values.
* [ ] How is truncation/partiality signalled, and how is "empty" distinguished from "unavailable"? (A-27, A-28)
* [ ] Are speciality areas drawn from the same vocabulary as `topic_tag`? (A-15)
* [ ] Can the profile return speciality data flagged partial? (A-17)
* [ ] Are course and lesson identifiers globally unique and stable? (A-23)
* [ ] Does the enrolment payload expose completion as an integer 0–100? (A-35)
* [ ] Which upstream statuses mean "retry later" (unavailable) versus "your request was wrong" (invalid)?
* [ ] Confirm the interaction payload has no question text UC-07 could accidentally read; if it does, confirm it is ignored (A-31).

---

### 4. Closing rules

* The **adapter is the ONLY location containing upstream payload knowledge.**
* The **adapter never invents data.** Unsatisfiable payload → typed contract error.
* **Authorization remains server-side.** Identity comes from
  `CurrentUserProvider`; no endpoint accepts a user id.
* **Contract mismatches require a contract discussion, not domain-model hacks.**

---


## 24. Requirement audit

Status values are only PASS / PARTIAL / BLOCKED, and PASS is claimed only where a
passing automated test exists.

| Requirement | Status | Implementation | Test | Evidence |
|---|---|---|---|---|
| Exactly 10-interaction threshold | PASS | `uc07/domain/counting.py`, `uc07/application/service.py` | `test_progress_across_the_threshold_matrix`, `test_no_report_at_nine_but_report_at_ten` | §2 matrix: 9 → none, 10 → `gr_b4e4…` |
| Progress at 0 / 5 / 9 | PASS | `ThresholdProgress`, `/gap-report/progress` | `test_no_report_below_ten_interactions`, `test_below_threshold_returns_progress_not_an_error` | §2 rows 0/5/9, remaining 10/5/1 |
| Report at 10 / 11 / 50 | PASS | `service.current_report` | `test_report_available_at_and_above_ten`, `test_available_report_envelope` | §2 rows 10/11/50 |
| Below threshold is not an HTTP error | PASS | `uc07/api/routes.py` | `test_below_threshold_returns_progress_not_an_error` | HTTP 200 with `report: null` |
| Threshold re-evaluated against current data | PASS | `service.current_report` | `test_threshold_is_re_evaluated_against_current_source_data` | §10: shrink to 9 → `below_threshold` |
| One place defines a qualifying interaction | PASS | `uc07/domain/counting.py` | `tests/unit/test_counting_and_threshold.py` (22) | §3 |
| Follow-ups / clarifying interactions count | PASS | `counting.py` | `test_follow_up_interactions_count`, `test_clarifying_interactions_count_when_represented_as_records` | §3 |
| Duplicate ids count once | PASS | `counting.py` | `test_duplicate_interaction_ids_count_once` | 12 raw → 10 |
| Invalid records do not count | PASS | adapters raise `ProviderInvalidResponse`; ownership filter in `counting.py` | `test_invalid_interaction_payload_raises_a_typed_contract_error`, `test_records_belonging_to_another_learner_are_discarded` | §13 `interactions_invalid` |
| Full-history aggregation across sessions | PASS | `uc07/application/aggregation.py` | `test_aggregation_spans_every_session_not_just_the_latest`, `test_report_uses_full_history_across_sessions` | 3 sessions, 14 interactions |
| Topic tags consumed exactly as supplied | PASS | `aggregation.py` | `test_topics_are_grouped_by_the_supplied_topic_tag`, `test_unusual_topic_tags_are_never_rewritten_or_reclassified` | §14 |
| Explain-differently signal | PASS | `signals.py::_explain_differently_signal` | 3 dedicated tests | observed 3 ≥ 2 |
| Follow-up signal | PASS | `signals.py::_follow_up_signal` | 3 dedicated tests | observed 2 ≥ 2 |
| Low-rating signal | PASS | `signals.py::_low_rating_signal` | 4 dedicated tests | observed 1 ≥ 1 |
| Signals combine | PASS | `signals.py::detect_struggles` | `test_signals_combine_on_one_topic_in_canonical_order` | `contract_formation` has 2 signals |
| Topic below all thresholds does not surface | PASS | `detect_struggles` | `test_topic_below_every_threshold_is_not_a_struggle`, `test_no_gap_is_emitted_for_a_topic_below_every_threshold` | `professional_conduct` absent |
| Thresholds live in configuration | PASS | `uc07/application/config.py` | `test_configuration_defaults_match_the_documented_thresholds`, `test_no_threshold_literal_is_hard_coded_in_business_logic` | `.env.example` |
| Evidence mandatory on every gap | PASS | `models.Gap`, `evidence_guard.py` | `test_struggle_gap_cannot_be_built_without_evidence_ids`, `test_every_generated_gap_carries_resolvable_evidence` (25 scenarios) | §4 |
| Every evidence id resolves | PASS | `evidence_guard.enforce_evidence_integrity` | `test_every_generated_gap_carries_resolvable_evidence` | `all_resolve=True` for every gap |
| Fabricated evidence rejected | PASS | `evidence_guard.py` | `test_guard_rejects_a_gap_whose_evidence_id_does_not_resolve`, `test_guard_rejects_partially_fabricated_evidence` | reason `evidence_id_does_not_resolve` |
| Unexplored speciality detection | PASS | `uc07/application/unexplored.py` | `test_speciality_area_with_zero_interactions_is_unexplored` + 11 more | 2 unexplored gaps |
| Fully covered speciality → no unexplored gap | PASS | `unexplored.py` | `test_fully_covered_speciality_yields_no_unexplored_gaps_in_the_report` | `unexplored_areas_found=0` |
| No speciality → stated, never inferred | PASS | `unexplored.py` | `test_no_speciality_reports_that_analysis_could_not_be_performed` | notice `speciality_analysis_not_possible_no_speciality` |
| Partial speciality preserved | PASS | `unexplored.py` | `test_partial_speciality_is_preserved_and_documented_in_the_report` | state `performed_partial` |
| Profile unavailable → struggle analysis continues | PASS | `service._load_profile` | `test_unavailable_profile_still_yields_evidence_based_struggle_analysis` | 3 struggle gaps kept |
| ≥3 topic areas when history supports it | PASS | `report_builder.py` | `test_report_surfaces_at_least_three_topic_areas_when_history_supports_it` | 5 topic areas |
| No padding when fewer exist | PASS | `report_builder.py` | `test_narrow_history_is_not_padded_and_says_so` | 1 struggle + diversity notice |
| Descriptions from a configured registry | PASS | `topic_descriptions.py`, `config/topic_descriptions.json` | `test_gap_descriptions_come_from_the_configured_registry`, `test_unknown_topic_tags_fall_back_to_the_configured_default_template` | `description_source: registry` |
| Valid course/lesson recommendations | PASS | `recommendations.py` | `test_report_recommendations_resolve_to_real_catalogue_identifiers` | resolved 6 |
| Invalid recommendations removed | PASS | `recommendations.py` | `test_unknown_lesson_id_is_removed_and_not_replaced`, `test_unknown_course_id_is_removed_and_not_replaced` | rejected 2 |
| Existing enrolment → lesson recommendation | PASS | `recommendations.py` | `test_existing_enrolment_becomes_lesson_recommendations`, `test_enrolled_course_yields_lesson_recommendations_in_the_report` | `lesson-cf-01`, `lesson-cf-02` |
| No guessed identifier when no lesson matches | PASS | `recommendations.py` | `test_enrolment_without_a_matching_lesson_drops_the_candidate_rather_than_guessing` | `dropped_already_enrolled_count` |
| Courses unavailable → notice, gaps intact | PASS | `service._load_courses`, `report_builder._notices` | `test_gaps_survive_when_the_course_source_is_unavailable` | 5 gaps, `recommendations_temporarily_unavailable` |
| Feedback unavailable notice | PASS | `service._load_feedback` | `test_feedback_unavailable_keeps_gaps_and_drops_only_the_rating_signal` | `rating_signal_unavailable` |
| Partial source distinction preserved | PASS | `service`, `report_builder` | `test_partial_*`, `test_courses_status_is_preserved_verbatim`, `test_profile_status_is_preserved_verbatim` | §13 matrix |
| empty ≠ unavailable | PASS | `service._load_feedback`, enums | `test_empty_and_unavailable_feedback_are_never_the_same_state` | different fingerprints |
| Interaction source unusable → clear error | PASS | `InteractionSourceUnusable`, `api/errors.py` | `test_unusable_interaction_source_raises_instead_of_returning_an_empty_report`, `test_interaction_source_failure_returns_a_clear_error_not_an_empty_report` | HTTP 503 |
| Deterministic reports | PASS | `report_builder.py`, `models.fingerprint_of` | `test_identical_inputs_produce_identical_reports` (10 scenarios) | identical fingerprints |
| Current report refreshes on source change | PASS | `service.current_report` | `test_current_report_reflects_an_eleventh_interaction` | §10 |
| All upstream ports read-only | PASS | `uc07/ports/read_only.py` | `test_read_only_ports_expose_no_write_operation` | §11 |
| All adapters read-only | PASS | mock/foreign/template adapters | `test_read_only_adapters_expose_no_write_operation` (8 adapters), `test_the_real_adapter_template_is_also_read_only` | §11 |
| Only `GapReportRepository` persists | PASS | `uc07/ports/persistence.py` | `test_gap_report_repository_is_the_only_write_seam`, `test_repository_write_surface_is_exactly_save` | §11 |
| No endpoint accepts a user id | PASS | `api/dependencies.py` | `test_no_endpoint_accepts_a_user_id_parameter`, `test_query_parameters_are_rejected` | §12 probes |
| Cross-user access denied | PASS | repository scoping + `ReportOwnershipError` | `test_cross_user_report_access_is_refused_at_the_service_boundary` | HTTP 403 |
| Unknown request fields rejected | PASS | `reject_request_input` | `test_request_body_is_rejected`, `test_unknown_query_parameters_are_rejected_on_progress_too` | HTTP 400 + `rejected_fields` |
| No question text read/stored/logged | PASS | `extra="forbid"` + AST scan | `test_interaction_record_rejects_forbidden_fields`, `test_no_code_reads_a_question_text_key` | §12 |
| No weak-topic content in logs | PASS | `uc07/observability.py` allowlist | `test_report_generation_logs_counts_but_no_weak_topic_content` | §12 |
| Errors leak no internals | PASS | `api/errors.py`, typed errors | `test_error_responses_never_leak_provider_names_or_internals`, conformance `assert_error_is_opaque` | §12 |
| Typed provider errors handled by type | PASS | `service._load_*` | `test_provider_timeout_is_its_own_type`, `test_service_never_catches_bare_exceptions_from_providers` | §13 |
| Provider timeout handled | PASS | `ProviderTimeout` | `test_timeout_raises_provider_timeout` (all 4 ports × 2 adapters) | §13 |
| No LLM / RAG / embeddings / vectors | PASS | none present | `test_no_banned_dependency_is_imported` (26), `test_no_llm_rag_or_vector_machinery_anywhere` | §11 |
| No frontend | PASS | none present | `test_no_frontend_assets_exist_in_the_repository` | §11 |
| No production database | PASS | `adapters/persistence/in_memory.py` | `test_no_banned_dependency_is_imported` (sqlalchemy/psycopg/pymongo/redis) | §11 |
| No agent framework | PASS | none present | `test_no_banned_dependency_is_imported` (langgraph/langchain) | §11 |
| Provider registry (no if/elif chains) | PASS | `uc07/composition.py` | `test_provider_selection_uses_a_registry_not_a_conditional_chain` (AST-based) | §25 |
| Unknown provider fails loudly | PASS | `composition.resolve` | `test_unknown_provider_name_fails_loudly` (4 ports) | `ConfigurationError` |
| No silent mock fallback | PASS | `composition.resolve` | `test_missing_real_provider_never_silently_falls_back_to_mock` (4 ports) | raises instead |
| Deterministic mocks (no randomness/sleep/network/keys) | PASS | `adapters/mock/scenarios.py` | `test_identical_inputs_produce_identical_reports`, `test_reads_are_repeatable_and_side_effect_free` | §9 |
| Real adapter template with TODO markers | PASS | `adapters/real/_template.py` | `test_real_adapter_template_has_every_required_todo_marker` | 5 markers |
| Conformance test kit, adapter-agnostic | PASS | `tests/conformance/` | 100 tests across 4 ports × 2 adapters | §25 |
| Foreign adapter proof | PASS | `adapters/foreign/` | `tests/integration/test_foreign_adapter_swap.py` (11) | identical fingerprint |
| `docs/assumptions.md` | PASS | 48 assumptions | `test_assumptions_register_documents_each_required_assumption`, `test_every_assumption_row_names_a_real_source_file` | §21 |
| `docs/SHARED_CONTRACT.md` | PASS | full type contract | `test_shared_contract_states_what_is_read_and_written` | §22 |
| `docs/INTEGRATION.md` | PASS | runbook + worked example | `test_integration_runbook_covers_every_dependency_and_the_closing_rules` | §23 |
| `.env.example` with placeholders only | PASS | `.env.example` | `test_env_example_contains_placeholders_only` | no URLs/secrets |
| Full suite passes | PASS | — | `python -m pytest` | 421 passed |
| Zero skipped tests | PASS | — | `grep -cE "SKIPPED\|XFAIL..."` → 0 | §20 |

---

## 25. Integration swap proof

The literal change needed to integrate a new upstream source, demonstrated with
the foreign ("Nexus LMS") source that ships in this repository.

### Change 1 — one new adapter file

`uc07/adapters/foreign/adapters.py` (+ its payload module). Everything upstream
lives here: `entryRef`, `conversation.ref`, `occurredAtEpochMs` (epoch millis),
`taxonomy.primary`, `promptKind`, `eqfBand` (`EQF-6`), `reexplainTally`,
`verdictLifecycle`, `sentiment` (`POSITIVE`/`NEGATIVE`), `completeness`
(`FULL`/`PARTIAL`/`ABSENT`), `programmeRef`, `moduleRef`, `focusAreas`,
`registrations`.

### Change 2 — one registry line per port

```python
# uc07/composition.py
INTERACTION_LOG_PROVIDERS: dict[str, Factory[InteractionLogProvider]] = {
    "mock": lambda settings: MockInteractionLogProvider(...),
    "foreign": lambda settings: ForeignInteractionLogProvider(NEXUS_PAYLOAD),   # <-- this line
}
```

### Change 3 — one environment-variable change

```
INTERACTION_LOG_PROVIDER=foreign
FEEDBACK_PROVIDER=foreign
PROFILE_PROVIDER=foreign
COURSES_PROVIDER=foreign
```

### Result: the identical report from a completely different upstream shape

```
mock    report_id=gr_fa59e7169dbb4f165badc92faaa98fa6 fingerprint=fa59e7169dbb4f165badc92faaa98fa68ce60a284a18f5b9566bbc138747b678
foreign report_id=gr_fa59e7169dbb4f165badc92faaa98fa6 fingerprint=fa59e7169dbb4f165badc92faaa98fa68ce60a284a18f5b9566bbc138747b678
reports identical: True
```

`test_the_unmodified_api_serves_the_foreign_source` runs the same FastAPI app
over the foreign source (200, 5 gaps, 14 interactions), and
`test_no_nexus_vocabulary_reaches_the_api_response` proves no upstream vocabulary
escapes. `test_each_port_can_be_swapped_independently_without_touching_the_others`
swaps one port at a time and still gets the identical report.

### Proof that nothing else needed modification

`test_the_swap_touches_only_adapters_registry_and_configuration` asserts that
these files contain no reference to the foreign source or its vocabulary:

* **Domain models** — `uc07/domain/models.py`, `counting.py`, `enums.py`, `errors.py`
* **Application services** — `service.py`, `signals.py`, `aggregation.py`,
  `report_builder.py`, `recommendations.py`, `unexplored.py`
* **API** — `routes.py`, `schemas.py`, `app.py`
* **Existing mock adapters** — `mock/interaction_log.py`, `mock/feedback.py`,
  `mock/profile.py`, `mock/courses.py`
* **Persistence** — `persistence/in_memory.py`
* **Existing tests** — unchanged; the conformance suite gained the foreign adapter
  as one registry entry in `tests/conformance/adapters.py`, and the suite files
  themselves were not modified.

The same test then enumerates every file in `uc07/` that mentions the foreign
source at all, and asserts the set is exactly:

```
uc07/adapters/foreign/adapters.py
uc07/adapters/foreign/payload.py
uc07/adapters/foreign/__init__.py
uc07/composition.py            <- the registry lines
```

### The conformance kit is adapter-agnostic

`tests/conformance/` is parameterized over `tests/conformance/adapters.py`, which
holds one `AdapterCase` per adapter per port. Both the mock and the foreign
adapter pass the same 100 tests: domain return types, normalised values
(`EQF-6` → `LEVEL_6`, epoch millis → tz-aware UTC, `NEGATIVE` → `down`,
`COMPLETE` → `rated`, `FULL` → `available`), typed failure modes
(unavailable/timeout/invalid), `empty` ≠ `unavailable`, no upstream payload
leakage, no upstream error-text or provider-name leakage in exceptions,
repeatable side-effect-free reads, and read-only surfaces. A real company adapter
joins by adding one `AdapterCase` — the suite is not modified.

---


## Appendix A — complete generated report (raw API response)

```json
{
  "status": "available",
  "interactions_completed": 14,
  "threshold": 10,
  "interactions_remaining": 0,
  "report": {
    "report_id": "gr_fa59e7169dbb4f165badc92faaa98fa6",
    "generated_at": "2026-03-01T12:00:00Z",
    "threshold": 10,
    "source_interaction_count": 14,
    "report_version": "1.0.0",
    "analysis_version": "1.0.0",
    "gaps": [
      {
        "topic_tag": "contract_formation",
        "gap_type": "struggle",
        "description": "Formation of a binding contract: offer, acceptance, consideration, intention to create legal relations and certainty of terms.",
        "description_source": "registry",
        "signals": [
          "explain_differently",
          "low_rating"
        ],
        "evidence_interaction_ids": [
          "interaction-101",
          "interaction-103"
        ],
        "evidence": {
          "basis": "interaction_ids",
          "interaction_ids": [
            "interaction-101",
            "interaction-103"
          ],
          "per_signal": [
            {
              "signal": "explain_differently",
              "observed_value": 3,
              "threshold": 2,
              "interaction_ids": [
                "interaction-101",
                "interaction-103"
              ]
            },
            {
              "signal": "low_rating",
              "observed_value": 2,
              "threshold": 1,
              "interaction_ids": [
                "interaction-101",
                "interaction-103"
              ]
            }
          ]
        },
        "recommendations": [
          {
            "topic_tag": "contract_formation",
            "recommendation_type": "lesson",
            "course_id": "course-contract-essentials",
            "lesson_id": "lesson-cf-01",
            "title": "Offer and acceptance"
          },
          {
            "topic_tag": "contract_formation",
            "recommendation_type": "lesson",
            "course_id": "course-contract-essentials",
            "lesson_id": "lesson-cf-02",
            "title": "Consideration and intention"
          }
        ]
      },
      {
        "topic_tag": "land_registration",
        "gap_type": "struggle",
        "description": "Registered title, registrable dispositions, overriding interests and the effect of registration on priority.",
        "description_source": "registry",
        "signals": [
          "low_rating"
        ],
        "evidence_interaction_ids": [
          "interaction-301"
        ],
        "evidence": {
          "basis": "interaction_ids",
          "interaction_ids": [
            "interaction-301"
          ],
          "per_signal": [
            {
              "signal": "low_rating",
              "observed_value": 1,
              "threshold": 1,
              "interaction_ids": [
                "interaction-301"
              ]
            }
          ]
        },
        "recommendations": [
          {
            "topic_tag": "land_registration",
            "recommendation_type": "course",
            "course_id": "course-property-practice",
            "lesson_id": null,
            "title": "Property Practice"
          }
        ]
      },
      {
        "topic_tag": "negligence",
        "gap_type": "struggle",
        "description": "The tort of negligence: duty of care, breach, factual and legal causation, and recoverable loss.",
        "description_source": "registry",
        "signals": [
          "follow_up"
        ],
        "evidence_interaction_ids": [
          "interaction-202",
          "interaction-203"
        ],
        "evidence": {
          "basis": "interaction_ids",
          "interaction_ids": [
            "interaction-202",
            "interaction-203"
          ],
          "per_signal": [
            {
              "signal": "follow_up",
              "observed_value": 2,
              "threshold": 2,
              "interaction_ids": [
                "interaction-202",
                "interaction-203"
              ]
            }
          ]
        },
        "recommendations": [
          {
            "topic_tag": "negligence",
            "recommendation_type": "course",
            "course_id": "course-tort-foundations",
            "lesson_id": null,
            "title": "Tort Foundations"
          }
        ]
      },
      {
        "topic_tag": "commercial_drafting",
        "gap_type": "unexplored",
        "description": "Drafting and reviewing commercial agreements: structure, risk allocation and boilerplate.",
        "description_source": "registry",
        "signals": [
          "unexplored_speciality"
        ],
        "evidence_interaction_ids": [],
        "evidence": {
          "basis": "zero_interactions_for_speciality_area",
          "interaction_ids": [],
          "per_signal": [
            {
              "signal": "unexplored_speciality",
              "observed_value": 0,
              "threshold": 0,
              "interaction_ids": []
            }
          ]
        },
        "recommendations": [
          {
            "topic_tag": "commercial_drafting",
            "recommendation_type": "course",
            "course_id": "course-commercial-drafting",
            "lesson_id": null,
            "title": "Commercial Drafting"
          }
        ]
      },
      {
        "topic_tag": "data_protection",
        "gap_type": "unexplored",
        "description": "Lawful bases for processing, data-subject rights and accountability obligations.",
        "description_source": "registry",
        "signals": [
          "unexplored_speciality"
        ],
        "evidence_interaction_ids": [],
        "evidence": {
          "basis": "zero_interactions_for_speciality_area",
          "interaction_ids": [],
          "per_signal": [
            {
              "signal": "unexplored_speciality",
              "observed_value": 0,
              "threshold": 0,
              "interaction_ids": []
            }
          ]
        },
        "recommendations": [
          {
            "topic_tag": "data_protection",
            "recommendation_type": "course",
            "course_id": "course-data-protection",
            "lesson_id": null,
            "title": "Data Protection in Practice"
          }
        ]
      }
    ],
    "recommendations": {
      "status": "available",
      "resolved_count": 6,
      "rejected_unresolvable_count": 2,
      "converted_to_lesson_count": 1,
      "dropped_already_enrolled_count": 0
    },
    "source_statuses": {
      "interactions": "available",
      "feedback": "available",
      "profile": "available",
      "courses": "available"
    },
    "topic_coverage": {
      "identifiable_topic_areas": 5,
      "minimum_expected_topic_areas": 3,
      "sufficient_topic_diversity": true,
      "topic_areas_in_history": 5
    },
    "unexplored_analysis": {
      "state": "performed",
      "speciality_status": "available",
      "speciality_areas_considered": 4,
      "unexplored_areas_found": 2,
      "may_be_incomplete": false,
      "explanation": "Speciality areas were compared against the topic tags present in the learner's interaction history."
    },
    "notices": [],
    "content_fingerprint": "fa59e7169dbb4f165badc92faaa98fa68ce60a284a18f5b9566bbc138747b678"
  }
}
```

---

## Appendix B — complete test list (421 tests)

```
tests/api/test_endpoints.py::test_healthz_reports_versions_and_threshold
tests/api/test_endpoints.py::test_below_threshold_returns_progress_not_an_error[count_0-0-10]
tests/api/test_endpoints.py::test_below_threshold_returns_progress_not_an_error[count_5-5-5]
tests/api/test_endpoints.py::test_below_threshold_returns_progress_not_an_error[count_9-9-1]
tests/api/test_endpoints.py::test_available_report_envelope[count_10]
tests/api/test_endpoints.py::test_available_report_envelope[count_11]
tests/api/test_endpoints.py::test_available_report_envelope[count_50]
tests/api/test_endpoints.py::test_report_response_never_contains_the_user_id
tests/api/test_endpoints.py::test_no_endpoint_accepts_a_user_id_parameter
tests/api/test_endpoints.py::test_query_parameters_are_rejected
tests/api/test_endpoints.py::test_unknown_query_parameters_are_rejected_on_progress_too
tests/api/test_endpoints.py::test_request_body_is_rejected
tests/api/test_endpoints.py::test_missing_identity_is_rejected
tests/api/test_endpoints.py::test_each_caller_only_ever_sees_their_own_data
tests/api/test_endpoints.py::test_cross_user_report_access_is_refused_at_the_service_boundary
tests/api/test_endpoints.py::test_interaction_source_failure_returns_a_clear_error_not_an_empty_report
tests/api/test_endpoints.py::test_error_responses_never_leak_provider_names_or_internals
tests/api/test_endpoints.py::test_degraded_report_exposes_source_information_explicitly
tests/api/test_endpoints.py::test_response_contains_no_question_text_fields
tests/api/test_endpoints.py::test_report_is_stable_across_repeated_requests
tests/architecture/test_privacy_architecture.py::test_no_domain_model_has_a_question_text_field
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[answer_text]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[prompt_text]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[question]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[question_text]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[response_text]
tests/architecture/test_privacy_architecture.py::test_question_text_is_only_ever_mentioned_in_order_to_forbid_it
tests/architecture/test_privacy_architecture.py::test_no_code_reads_a_question_text_key
tests/architecture/test_privacy_architecture.py::test_feedback_comments_never_reach_a_report
tests/architecture/test_privacy_architecture.py::test_openapi_schema_exposes_no_question_or_identity_fields
tests/architecture/test_privacy_architecture.py::test_report_payload_carries_no_learner_identity
tests/architecture/test_read_only_architecture.py::test_read_only_ports_expose_no_write_operation[InteractionLogProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_expose_no_write_operation[FeedbackProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_expose_no_write_operation[LearnerProfileProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_expose_no_write_operation[CoursesProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_are_marked_as_such
tests/architecture/test_read_only_architecture.py::test_gap_report_repository_is_the_only_write_seam
tests/architecture/test_read_only_architecture.py::test_repository_write_surface_is_exactly_save
tests/architecture/test_read_only_architecture.py::test_every_read_only_port_has_at_least_two_independent_adapters
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[ForeignCoursesProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[ForeignFeedbackProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[ForeignInteractionLogProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[ForeignLearnerProfileProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[MockCoursesProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[MockFeedbackProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[MockInteractionLogProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[MockLearnerProfileProvider]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[TemplateInteractionLogProvider]
tests/architecture/test_read_only_architecture.py::test_the_real_adapter_template_is_also_read_only
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[langgraph]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[langchain]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[llama_index]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[openai]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[anthropic]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[cohere]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[transformers]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[sentence_transformers]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[torch]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[chromadb]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[faiss]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[pinecone]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[weaviate]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[qdrant]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[milvus]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[sqlalchemy]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[psycopg]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[pymongo]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[asyncpg]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[redis]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[boto3]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[celery]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[jinja2]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[flask]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[django]
tests/architecture/test_read_only_architecture.py::test_no_llm_rag_or_vector_machinery_anywhere
tests/architecture/test_read_only_architecture.py::test_no_frontend_assets_exist_in_the_repository
tests/architecture/test_read_only_architecture.py::test_domain_layer_does_not_depend_on_adapters_api_or_frameworks
tests/architecture/test_read_only_architecture.py::test_application_layer_does_not_depend_on_adapters_or_http
tests/architecture/test_read_only_architecture.py::test_ports_layer_depends_only_on_the_domain
tests/architecture/test_read_only_architecture.py::test_provider_selection_uses_a_registry_not_a_conditional_chain
tests/conformance/test_courses_conformance.py::test_adapter_implements_the_port[mock]
tests/conformance/test_courses_conformance.py::test_adapter_implements_the_port[foreign]
tests/conformance/test_courses_conformance.py::test_adapter_is_read_only[mock]
tests/conformance/test_courses_conformance.py::test_adapter_is_read_only[foreign]
tests/conformance/test_courses_conformance.py::test_recommendations_are_domain_objects_for_requested_topics[mock]
tests/conformance/test_courses_conformance.py::test_recommendations_are_domain_objects_for_requested_topics[foreign]
tests/conformance/test_courses_conformance.py::test_catalogue_returns_course_summaries_with_lessons[mock]
tests/conformance/test_courses_conformance.py::test_catalogue_returns_course_summaries_with_lessons[foreign]
tests/conformance/test_courses_conformance.py::test_enrolments_are_domain_objects_scoped_to_the_learner[mock]
tests/conformance/test_courses_conformance.py::test_enrolments_are_domain_objects_scoped_to_the_learner[foreign]
tests/conformance/test_courses_conformance.py::test_no_upstream_payload_leaks_into_domain_objects[mock]
tests/conformance/test_courses_conformance.py::test_no_upstream_payload_leaks_into_domain_objects[foreign]
tests/conformance/test_courses_conformance.py::test_status_is_a_source_status[mock]
tests/conformance/test_courses_conformance.py::test_status_is_a_source_status[foreign]
tests/conformance/test_courses_conformance.py::test_empty_catalogue_is_reported_as_empty_not_unavailable[mock]
tests/conformance/test_courses_conformance.py::test_empty_catalogue_is_reported_as_empty_not_unavailable[foreign]
tests/conformance/test_courses_conformance.py::test_unavailable_source_raises_provider_unavailable[mock]
tests/conformance/test_courses_conformance.py::test_unavailable_source_raises_provider_unavailable[foreign]
tests/conformance/test_courses_conformance.py::test_timeout_raises_provider_timeout[mock]
tests/conformance/test_courses_conformance.py::test_timeout_raises_provider_timeout[foreign]
tests/conformance/test_courses_conformance.py::test_contract_breach_raises_provider_invalid_response[mock]
tests/conformance/test_courses_conformance.py::test_contract_breach_raises_provider_invalid_response[foreign]
tests/conformance/test_courses_conformance.py::test_reads_are_repeatable_and_side_effect_free[mock]
tests/conformance/test_courses_conformance.py::test_reads_are_repeatable_and_side_effect_free[foreign]
tests/conformance/test_courses_conformance.py::test_unrequested_topics_are_not_recommended[mock]
tests/conformance/test_courses_conformance.py::test_unrequested_topics_are_not_recommended[foreign]
tests/conformance/test_feedback_conformance.py::test_adapter_implements_the_port[mock]
tests/conformance/test_feedback_conformance.py::test_adapter_implements_the_port[foreign]
tests/conformance/test_feedback_conformance.py::test_adapter_is_read_only[mock]
tests/conformance/test_feedback_conformance.py::test_adapter_is_read_only[foreign]
tests/conformance/test_feedback_conformance.py::test_returns_domain_records[mock]
tests/conformance/test_feedback_conformance.py::test_returns_domain_records[foreign]
tests/conformance/test_feedback_conformance.py::test_ratings_are_normalised_to_the_platform_vocabulary[mock]
tests/conformance/test_feedback_conformance.py::test_ratings_are_normalised_to_the_platform_vocabulary[foreign]
tests/conformance/test_feedback_conformance.py::test_only_requested_interactions_are_returned[mock]
tests/conformance/test_feedback_conformance.py::test_only_requested_interactions_are_returned[foreign]
tests/conformance/test_feedback_conformance.py::test_no_upstream_payload_leaks_into_domain_records[mock]
tests/conformance/test_feedback_conformance.py::test_no_upstream_payload_leaks_into_domain_records[foreign]
tests/conformance/test_feedback_conformance.py::test_status_is_a_source_status[mock]
tests/conformance/test_feedback_conformance.py::test_status_is_a_source_status[foreign]
tests/conformance/test_feedback_conformance.py::test_empty_source_is_reported_as_empty_not_unavailable[mock]
tests/conformance/test_feedback_conformance.py::test_empty_source_is_reported_as_empty_not_unavailable[foreign]
tests/conformance/test_feedback_conformance.py::test_unavailable_source_raises_provider_unavailable[mock]
tests/conformance/test_feedback_conformance.py::test_unavailable_source_raises_provider_unavailable[foreign]
tests/conformance/test_feedback_conformance.py::test_timeout_raises_provider_timeout[mock]
tests/conformance/test_feedback_conformance.py::test_timeout_raises_provider_timeout[foreign]
tests/conformance/test_feedback_conformance.py::test_contract_breach_raises_provider_invalid_response[mock]
tests/conformance/test_feedback_conformance.py::test_contract_breach_raises_provider_invalid_response[foreign]
tests/conformance/test_feedback_conformance.py::test_reads_are_repeatable_and_side_effect_free[mock]
tests/conformance/test_feedback_conformance.py::test_reads_are_repeatable_and_side_effect_free[foreign]
tests/conformance/test_feedback_conformance.py::test_unknown_interaction_ids_yield_nothing[mock]
tests/conformance/test_feedback_conformance.py::test_unknown_interaction_ids_yield_nothing[foreign]
tests/conformance/test_interaction_log_conformance.py::test_adapter_implements_the_port[mock]
tests/conformance/test_interaction_log_conformance.py::test_adapter_implements_the_port[foreign]
tests/conformance/test_interaction_log_conformance.py::test_adapter_is_read_only[mock]
tests/conformance/test_interaction_log_conformance.py::test_adapter_is_read_only[foreign]
tests/conformance/test_interaction_log_conformance.py::test_returns_domain_records[mock]
tests/conformance/test_interaction_log_conformance.py::test_returns_domain_records[foreign]
tests/conformance/test_interaction_log_conformance.py::test_values_are_normalised_not_upstream_spellings[mock]
tests/conformance/test_interaction_log_conformance.py::test_values_are_normalised_not_upstream_spellings[foreign]
tests/conformance/test_interaction_log_conformance.py::test_no_upstream_payload_leaks_into_domain_records[mock]
tests/conformance/test_interaction_log_conformance.py::test_no_upstream_payload_leaks_into_domain_records[foreign]
tests/conformance/test_interaction_log_conformance.py::test_count_is_a_non_negative_integer[mock]
tests/conformance/test_interaction_log_conformance.py::test_count_is_a_non_negative_integer[foreign]
tests/conformance/test_interaction_log_conformance.py::test_status_is_a_source_status[mock]
tests/conformance/test_interaction_log_conformance.py::test_status_is_a_source_status[foreign]
tests/conformance/test_interaction_log_conformance.py::test_empty_source_is_reported_as_empty_not_unavailable[mock]
tests/conformance/test_interaction_log_conformance.py::test_empty_source_is_reported_as_empty_not_unavailable[foreign]
tests/conformance/test_interaction_log_conformance.py::test_unavailable_source_raises_provider_unavailable[mock]
tests/conformance/test_interaction_log_conformance.py::test_unavailable_source_raises_provider_unavailable[foreign]
tests/conformance/test_interaction_log_conformance.py::test_timeout_raises_provider_timeout[mock]
tests/conformance/test_interaction_log_conformance.py::test_timeout_raises_provider_timeout[foreign]
tests/conformance/test_interaction_log_conformance.py::test_contract_breach_raises_provider_invalid_response[mock]
tests/conformance/test_interaction_log_conformance.py::test_contract_breach_raises_provider_invalid_response[foreign]
tests/conformance/test_interaction_log_conformance.py::test_reads_are_repeatable_and_side_effect_free[mock]
tests/conformance/test_interaction_log_conformance.py::test_reads_are_repeatable_and_side_effect_free[foreign]
tests/conformance/test_interaction_log_conformance.py::test_unknown_learner_is_empty_not_a_failure[mock]
tests/conformance/test_interaction_log_conformance.py::test_unknown_learner_is_empty_not_a_failure[foreign]
tests/conformance/test_learner_profile_conformance.py::test_adapter_implements_the_port[mock]
tests/conformance/test_learner_profile_conformance.py::test_adapter_implements_the_port[foreign]
tests/conformance/test_learner_profile_conformance.py::test_adapter_is_read_only[mock]
tests/conformance/test_learner_profile_conformance.py::test_adapter_is_read_only[foreign]
tests/conformance/test_learner_profile_conformance.py::test_returns_a_domain_profile[mock]
tests/conformance/test_learner_profile_conformance.py::test_returns_a_domain_profile[foreign]
tests/conformance/test_learner_profile_conformance.py::test_naric_values_are_normalised[mock]
tests/conformance/test_learner_profile_conformance.py::test_naric_values_are_normalised[foreign]
tests/conformance/test_learner_profile_conformance.py::test_no_upstream_payload_leaks_into_the_profile[mock]
tests/conformance/test_learner_profile_conformance.py::test_no_upstream_payload_leaks_into_the_profile[foreign]
tests/conformance/test_learner_profile_conformance.py::test_no_speciality_is_reported_as_empty_not_unavailable[mock]
tests/conformance/test_learner_profile_conformance.py::test_no_speciality_is_reported_as_empty_not_unavailable[foreign]
tests/conformance/test_learner_profile_conformance.py::test_unavailable_source_raises_provider_unavailable[mock]
tests/conformance/test_learner_profile_conformance.py::test_unavailable_source_raises_provider_unavailable[foreign]
tests/conformance/test_learner_profile_conformance.py::test_timeout_raises_provider_timeout[mock]
tests/conformance/test_learner_profile_conformance.py::test_timeout_raises_provider_timeout[foreign]
tests/conformance/test_learner_profile_conformance.py::test_contract_breach_raises_provider_invalid_response[mock]
tests/conformance/test_learner_profile_conformance.py::test_contract_breach_raises_provider_invalid_response[foreign]
tests/conformance/test_learner_profile_conformance.py::test_reads_are_repeatable_and_side_effect_free[mock]
tests/conformance/test_learner_profile_conformance.py::test_reads_are_repeatable_and_side_effect_free[foreign]
tests/conformance/test_learner_profile_conformance.py::test_unknown_learner_yields_an_empty_speciality_not_an_invented_one[mock]
tests/conformance/test_learner_profile_conformance.py::test_unknown_learner_yields_an_empty_speciality_not_an_invented_one[foreign]
tests/integration/test_foreign_adapter_swap.py::test_report_from_the_foreign_source_is_identical_to_the_mock_report
tests/integration/test_foreign_adapter_swap.py::test_foreign_source_produces_the_same_evidence_identifiers
tests/integration/test_foreign_adapter_swap.py::test_foreign_values_are_normalised_into_platform_types
tests/integration/test_foreign_adapter_swap.py::test_the_unmodified_api_serves_the_foreign_source
tests/integration/test_foreign_adapter_swap.py::test_no_nexus_vocabulary_reaches_the_api_response
tests/integration/test_foreign_adapter_swap.py::test_the_swap_touches_only_adapters_registry_and_configuration
tests/integration/test_foreign_adapter_swap.py::test_foreign_source_is_deterministic_too
tests/integration/test_foreign_adapter_swap.py::test_each_port_can_be_swapped_independently_without_touching_the_others[interaction_log]
tests/integration/test_foreign_adapter_swap.py::test_each_port_can_be_swapped_independently_without_touching_the_others[feedback]
tests/integration/test_foreign_adapter_swap.py::test_each_port_can_be_swapped_independently_without_touching_the_others[profile]
tests/integration/test_foreign_adapter_swap.py::test_each_port_can_be_swapped_independently_without_touching_the_others[courses]
tests/integration/test_persistence.py::test_generated_report_is_persisted_through_the_repository
tests/integration/test_persistence.py::test_nothing_is_persisted_below_the_threshold
tests/integration/test_persistence.py::test_reports_are_scoped_by_owner
tests/integration/test_persistence.py::test_stored_report_keeps_internal_ownership_information
tests/integration/test_persistence.py::test_repository_is_the_only_component_that_records_anything
tests/integration/test_provider_registry.py::test_every_port_registry_offers_mock_and_foreign[COURSES_PROVIDER]
tests/integration/test_provider_registry.py::test_every_port_registry_offers_mock_and_foreign[FEEDBACK_PROVIDER]
tests/integration/test_provider_registry.py::test_every_port_registry_offers_mock_and_foreign[INTERACTION_LOG_PROVIDER]
tests/integration/test_provider_registry.py::test_every_port_registry_offers_mock_and_foreign[PROFILE_PROVIDER]
tests/integration/test_provider_registry.py::test_unknown_provider_name_fails_loudly[COURSES_PROVIDER]
tests/integration/test_provider_registry.py::test_unknown_provider_name_fails_loudly[FEEDBACK_PROVIDER]
tests/integration/test_provider_registry.py::test_unknown_provider_name_fails_loudly[INTERACTION_LOG_PROVIDER]
tests/integration/test_provider_registry.py::test_unknown_provider_name_fails_loudly[PROFILE_PROVIDER]
tests/integration/test_provider_registry.py::test_missing_real_provider_never_silently_falls_back_to_mock[COURSES_PROVIDER]
tests/integration/test_provider_registry.py::test_missing_real_provider_never_silently_falls_back_to_mock[FEEDBACK_PROVIDER]
tests/integration/test_provider_registry.py::test_missing_real_provider_never_silently_falls_back_to_mock[INTERACTION_LOG_PROVIDER]
tests/integration/test_provider_registry.py::test_missing_real_provider_never_silently_falls_back_to_mock[PROFILE_PROVIDER]
tests/integration/test_provider_registry.py::test_mock_selection_wires_mock_adapters
tests/integration/test_provider_registry.py::test_foreign_selection_wires_foreign_adapters_with_no_other_change
tests/integration/test_provider_registry.py::test_ports_can_be_mixed_independently
tests/integration/test_provider_registry.py::test_unknown_mock_scenario_fails_loudly
tests/integration/test_provider_registry.py::test_thresholds_come_from_settings
tests/integration/test_provider_registry.py::test_registered_adapters_are_the_only_write_free_surface_used
tests/test_docs_and_config.py::test_deliverable_documents_exist[path0]
tests/test_docs_and_config.py::test_deliverable_documents_exist[path1]
tests/test_docs_and_config.py::test_deliverable_documents_exist[path2]
tests/test_docs_and_config.py::test_deliverable_documents_exist[path3]
tests/test_docs_and_config.py::test_assumptions_register_uses_the_required_table_shape
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[qualifying interaction]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Follow-up interactions count]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Duplicate `interaction_id`]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[exactly 10 qualifying interactions]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Explain-differently threshold = 2]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Follow-up threshold = 2]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Low-rating threshold = 1]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[topic-description registry]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Partial speciality data]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[complete, all-time history]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[report_version]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[analysis_version]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[re-evaluates the source data]
tests/test_docs_and_config.py::test_every_assumption_row_names_a_real_source_file
tests/test_docs_and_config.py::test_shared_contract_states_what_is_read_and_written
tests/test_docs_and_config.py::test_integration_runbook_covers_every_dependency_and_the_closing_rules
tests/test_docs_and_config.py::test_real_adapter_template_has_every_required_todo_marker
tests/test_docs_and_config.py::test_env_example_contains_placeholders_only
tests/test_docs_and_config.py::test_configuration_defaults_match_the_documented_thresholds
tests/test_docs_and_config.py::test_no_threshold_literal_is_hard_coded_in_business_logic
tests/unit/test_aggregation.py::test_aggregation_spans_every_session_not_just_the_latest
tests/unit/test_aggregation.py::test_topics_are_grouped_by_the_supplied_topic_tag
tests/unit/test_aggregation.py::test_unusual_topic_tags_are_never_rewritten_or_reclassified
tests/unit/test_aggregation.py::test_per_topic_signal_inputs_are_aggregated_over_the_whole_history
tests/unit/test_aggregation.py::test_aggregate_ordering_is_deterministic
tests/unit/test_aggregation.py::test_report_uses_full_history_across_sessions
tests/unit/test_counting_and_threshold.py::test_every_valid_record_counts_once
tests/unit/test_counting_and_threshold.py::test_follow_up_interactions_count
tests/unit/test_counting_and_threshold.py::test_clarifying_interactions_count_when_represented_as_records
tests/unit/test_counting_and_threshold.py::test_explain_differently_counter_does_not_add_to_the_count
tests/unit/test_counting_and_threshold.py::test_duplicate_interaction_ids_count_once
tests/unit/test_counting_and_threshold.py::test_records_belonging_to_another_learner_are_discarded
tests/unit/test_counting_and_threshold.py::test_counting_is_order_independent
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[0-below_threshold-10]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[5-below_threshold-5]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[9-below_threshold-1]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[10-available-0]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[11-available-0]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[50-available-0]
tests/unit/test_counting_and_threshold.py::test_no_report_below_ten_interactions[0]
tests/unit/test_counting_and_threshold.py::test_no_report_below_ten_interactions[5]
tests/unit/test_counting_and_threshold.py::test_no_report_below_ten_interactions[9]
tests/unit/test_counting_and_threshold.py::test_no_report_at_nine_but_report_at_ten
tests/unit/test_counting_and_threshold.py::test_report_available_at_and_above_ten[10]
tests/unit/test_counting_and_threshold.py::test_report_available_at_and_above_ten[11]
tests/unit/test_counting_and_threshold.py::test_report_available_at_and_above_ten[50]
tests/unit/test_counting_and_threshold.py::test_below_threshold_is_not_an_error_and_reports_progress_fields
tests/unit/test_counting_and_threshold.py::test_threshold_comes_from_configuration_not_code
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[count_10]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[count_11]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[count_50]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[struggle_mixed]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[diverse_topics]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[narrow_topics]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[feedback_unavailable]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[profile_partial]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[courses_unavailable]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[interactions_partial]
tests/unit/test_determinism_and_freshness.py::test_report_is_stable_across_repeated_requests_in_one_service
tests/unit/test_determinism_and_freshness.py::test_provider_record_order_does_not_change_the_report
tests/unit/test_determinism_and_freshness.py::test_different_clocks_do_not_change_report_content_only_generated_at
tests/unit/test_determinism_and_freshness.py::test_current_report_reflects_an_eleventh_interaction
tests/unit/test_determinism_and_freshness.py::test_threshold_is_re_evaluated_against_current_source_data
tests/unit/test_evidence.py::test_struggle_gap_cannot_be_built_without_evidence_ids
tests/unit/test_evidence.py::test_gap_cannot_be_built_without_signals
tests/unit/test_evidence.py::test_signal_evidence_cannot_claim_a_signal_that_did_not_fire
tests/unit/test_evidence.py::test_per_signal_evidence_must_be_inside_the_gap_evidence_set
tests/unit/test_evidence.py::test_guard_rejects_a_gap_whose_evidence_id_does_not_resolve
tests/unit/test_evidence.py::test_guard_keeps_a_gap_whose_evidence_resolves
tests/unit/test_evidence.py::test_guard_rejects_partially_fabricated_evidence
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[count_10]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[count_11]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[count_50]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[struggle_mixed]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[diverse_topics]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[narrow_topics]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[heavy_explain_differently]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[heavy_follow_ups]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[duplicate_interaction_ids]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[mixed_owner_records]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[feedback_empty]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[feedback_unavailable]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[feedback_partial]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[feedback_invalid]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_fully_covered]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_no_speciality]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_partial]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_unavailable]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_invalid]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_unavailable]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_partial]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_invalid]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_not_enrolled]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_only_invalid_candidates]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[interactions_partial]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[count_10]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[count_11]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[count_50]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[struggle_mixed]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[diverse_topics]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[narrow_topics]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[heavy_explain_differently]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[heavy_follow_ups]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[duplicate_interaction_ids]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[mixed_owner_records]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[feedback_empty]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[feedback_unavailable]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[feedback_partial]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[feedback_invalid]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_fully_covered]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_no_speciality]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_partial]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_unavailable]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_invalid]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_unavailable]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_partial]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_invalid]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_not_enrolled]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_only_invalid_candidates]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[interactions_partial]
tests/unit/test_observability_privacy.py::test_disallowed_fields_are_dropped_before_they_reach_a_log_record
tests/unit/test_observability_privacy.py::test_sanitise_only_keeps_allowlisted_fields
tests/unit/test_observability_privacy.py::test_report_generation_logs_counts_but_no_weak_topic_content
tests/unit/test_observability_privacy.py::test_progress_logging_stays_within_the_allowlist
tests/unit/test_observability_privacy.py::test_no_log_line_ever_contains_a_feedback_comment
tests/unit/test_recommendations.py::test_valid_lesson_recommendation_is_kept
tests/unit/test_recommendations.py::test_unknown_lesson_id_is_removed_and_not_replaced
tests/unit/test_recommendations.py::test_unknown_course_id_is_removed_and_not_replaced
tests/unit/test_recommendations.py::test_existing_enrolment_becomes_lesson_recommendations
tests/unit/test_recommendations.py::test_enrolment_without_a_matching_lesson_drops_the_candidate_rather_than_guessing
tests/unit/test_recommendations.py::test_another_learners_enrolment_does_not_affect_this_learner
tests/unit/test_recommendations.py::test_candidates_for_topics_that_are_not_gaps_are_ignored
tests/unit/test_recommendations.py::test_courses_unavailable_marks_recommendations_unavailable
tests/unit/test_recommendations.py::test_partial_course_data_marks_recommendations_partial
tests/unit/test_recommendations.py::test_recommendations_are_deduplicated_and_sorted
tests/unit/test_recommendations.py::test_report_recommendations_resolve_to_real_catalogue_identifiers
tests/unit/test_recommendations.py::test_enrolled_course_yields_lesson_recommendations_in_the_report
tests/unit/test_recommendations.py::test_not_enrolled_learner_gets_the_course_level_recommendation
tests/unit/test_recommendations.py::test_gaps_survive_when_the_course_source_is_unavailable
tests/unit/test_recommendations.py::test_partial_course_source_is_reported_as_partial_in_the_report
tests/unit/test_recommendations.py::test_invalid_course_source_keeps_gaps_and_marks_recommendations_unavailable
tests/unit/test_recommendations.py::test_all_invalid_candidates_leave_gaps_without_recommendations
tests/unit/test_report_assembly.py::test_report_surfaces_at_least_three_topic_areas_when_history_supports_it
tests/unit/test_report_assembly.py::test_narrow_history_is_not_padded_and_says_so
tests/unit/test_report_assembly.py::test_no_gap_is_emitted_for_a_topic_below_every_threshold
tests/unit/test_report_assembly.py::test_gap_descriptions_come_from_the_configured_registry
tests/unit/test_report_assembly.py::test_unknown_topic_tags_fall_back_to_the_configured_default_template
tests/unit/test_report_assembly.py::test_registry_rejects_a_default_template_without_the_topic_placeholder
tests/unit/test_report_assembly.py::test_registry_missing_file_fails_loudly
tests/unit/test_report_assembly.py::test_report_carries_versions_threshold_and_source_statuses
tests/unit/test_report_assembly.py::test_minimum_topic_areas_is_configuration_driven
tests/unit/test_report_assembly.py::test_gap_ordering_is_struggle_first_then_unexplored_each_sorted_by_topic
tests/unit/test_resilience.py::test_unusable_interaction_source_raises_instead_of_returning_an_empty_report[interactions_unavailable-unavailable]
tests/unit/test_resilience.py::test_unusable_interaction_source_raises_instead_of_returning_an_empty_report[interactions_timeout-unavailable]
tests/unit/test_resilience.py::test_unusable_interaction_source_raises_instead_of_returning_an_empty_report[interactions_invalid-invalid]
tests/unit/test_resilience.py::test_invalid_interaction_payload_raises_a_typed_contract_error
tests/unit/test_resilience.py::test_partial_interaction_source_is_preserved_and_noticed
tests/unit/test_resilience.py::test_empty_interaction_history_is_progress_not_failure
tests/unit/test_resilience.py::test_feedback_unavailable_keeps_gaps_and_drops_only_the_rating_signal
tests/unit/test_resilience.py::test_feedback_invalid_is_distinct_from_unavailable
tests/unit/test_resilience.py::test_feedback_empty_means_the_learner_genuinely_has_no_ratings
tests/unit/test_resilience.py::test_feedback_partial_is_used_but_flagged_as_possibly_incomplete
tests/unit/test_resilience.py::test_empty_and_unavailable_feedback_are_never_the_same_state
tests/unit/test_resilience.py::test_profile_status_is_preserved_verbatim[profile_unavailable-unavailable]
tests/unit/test_resilience.py::test_profile_status_is_preserved_verbatim[profile_invalid-invalid]
tests/unit/test_resilience.py::test_profile_status_is_preserved_verbatim[profile_partial-partial]
tests/unit/test_resilience.py::test_profile_status_is_preserved_verbatim[profile_no_speciality-empty]
tests/unit/test_resilience.py::test_courses_status_is_preserved_verbatim[courses_unavailable-unavailable]
tests/unit/test_resilience.py::test_courses_status_is_preserved_verbatim[courses_invalid-invalid]
tests/unit/test_resilience.py::test_courses_status_is_preserved_verbatim[courses_partial-partial]
tests/unit/test_resilience.py::test_provider_timeout_is_its_own_type
tests/unit/test_resilience.py::test_provider_unavailable_is_its_own_type
tests/unit/test_resilience.py::test_service_never_catches_bare_exceptions_from_providers
tests/unit/test_signals.py::test_explain_differently_fires_at_the_configured_threshold
tests/unit/test_signals.py::test_explain_differently_totals_across_interactions_in_the_topic
tests/unit/test_signals.py::test_explain_differently_below_threshold_does_not_surface
tests/unit/test_signals.py::test_follow_up_signal_fires_at_the_configured_threshold
tests/unit/test_signals.py::test_single_follow_up_does_not_surface
tests/unit/test_signals.py::test_heavy_follow_up_scenario_surfaces_only_the_follow_up_topic
tests/unit/test_signals.py::test_low_rating_signal_fires_on_a_single_thumbs_down
tests/unit/test_signals.py::test_thumbs_up_is_never_a_struggle_signal
tests/unit/test_signals.py::test_ratings_for_unknown_interactions_cannot_manufacture_evidence
tests/unit/test_signals.py::test_ratings_owned_by_another_learner_are_ignored
tests/unit/test_signals.py::test_low_rating_signal_is_skipped_when_the_rating_source_cannot_be_read
tests/unit/test_signals.py::test_empty_rating_source_is_evaluated_and_simply_finds_nothing
tests/unit/test_signals.py::test_signals_combine_on_one_topic_in_canonical_order
tests/unit/test_signals.py::test_topic_below_every_threshold_is_not_a_struggle
tests/unit/test_signals.py::test_scenarios_isolate_single_signals[heavy_explain_differently-expected0]
tests/unit/test_signals.py::test_scenarios_isolate_single_signals[heavy_follow_ups-expected1]
tests/unit/test_signals.py::test_showcase_scenario_signal_matrix
tests/unit/test_unexplored.py::test_speciality_area_with_zero_interactions_is_unexplored
tests/unit/test_unexplored.py::test_fully_covered_speciality_produces_no_unexplored_gap
tests/unit/test_unexplored.py::test_no_speciality_is_stated_explicitly_and_never_inferred
tests/unit/test_unexplored.py::test_partial_speciality_keeps_partial_status_and_flags_incompleteness
tests/unit/test_unexplored.py::test_unavailable_profile_does_not_invent_speciality_areas
tests/unit/test_unexplored.py::test_invalid_profile_is_distinct_from_unavailable
tests/unit/test_unexplored.py::test_speciality_comparison_is_exact_and_case_sensitive
tests/unit/test_unexplored.py::test_report_contains_unexplored_gaps_for_uncovered_speciality_areas
tests/unit/test_unexplored.py::test_fully_covered_speciality_yields_no_unexplored_gaps_in_the_report
tests/unit/test_unexplored.py::test_no_speciality_reports_that_analysis_could_not_be_performed
tests/unit/test_unexplored.py::test_partial_speciality_is_preserved_and_documented_in_the_report
tests/unit/test_unexplored.py::test_unavailable_profile_still_yields_evidence_based_struggle_analysis
```

---

## Appendix C — raw verbose pytest output (421 PASSED, 0 skipped)

```
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0 -- C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\Administrator\Documents\tas77
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2, asyncio-1.4.0, cov-7.1.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 421 items

tests/api/test_endpoints.py::test_healthz_reports_versions_and_threshold PASSED [  0%]
tests/api/test_endpoints.py::test_below_threshold_returns_progress_not_an_error[count_0-0-10] PASSED [  0%]
tests/api/test_endpoints.py::test_below_threshold_returns_progress_not_an_error[count_5-5-5] PASSED [  0%]
tests/api/test_endpoints.py::test_below_threshold_returns_progress_not_an_error[count_9-9-1] PASSED [  0%]
tests/api/test_endpoints.py::test_available_report_envelope[count_10] PASSED [  1%]
tests/api/test_endpoints.py::test_available_report_envelope[count_11] PASSED [  1%]
tests/api/test_endpoints.py::test_available_report_envelope[count_50] PASSED [  1%]
tests/api/test_endpoints.py::test_report_response_never_contains_the_user_id PASSED [  1%]
tests/api/test_endpoints.py::test_no_endpoint_accepts_a_user_id_parameter PASSED [  2%]
tests/api/test_endpoints.py::test_query_parameters_are_rejected PASSED   [  2%]
tests/api/test_endpoints.py::test_unknown_query_parameters_are_rejected_on_progress_too PASSED [  2%]
tests/api/test_endpoints.py::test_request_body_is_rejected PASSED        [  2%]
tests/api/test_endpoints.py::test_missing_identity_is_rejected PASSED    [  3%]
tests/api/test_endpoints.py::test_each_caller_only_ever_sees_their_own_data PASSED [  3%]
tests/api/test_endpoints.py::test_cross_user_report_access_is_refused_at_the_service_boundary PASSED [  3%]
tests/api/test_endpoints.py::test_interaction_source_failure_returns_a_clear_error_not_an_empty_report PASSED [  3%]
tests/api/test_endpoints.py::test_error_responses_never_leak_provider_names_or_internals PASSED [  4%]
tests/api/test_endpoints.py::test_degraded_report_exposes_source_information_explicitly PASSED [  4%]
tests/api/test_endpoints.py::test_response_contains_no_question_text_fields PASSED [  4%]
tests/api/test_endpoints.py::test_report_is_stable_across_repeated_requests PASSED [  4%]
tests/architecture/test_privacy_architecture.py::test_no_domain_model_has_a_question_text_field PASSED [  4%]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[answer_text] PASSED [  5%]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[prompt_text] PASSED [  5%]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[question] PASSED [  5%]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[question_text] PASSED [  5%]
tests/architecture/test_privacy_architecture.py::test_interaction_record_rejects_forbidden_fields[response_text] PASSED [  6%]
tests/architecture/test_privacy_architecture.py::test_question_text_is_only_ever_mentioned_in_order_to_forbid_it PASSED [  6%]
tests/architecture/test_privacy_architecture.py::test_no_code_reads_a_question_text_key PASSED [  6%]
tests/architecture/test_privacy_architecture.py::test_feedback_comments_never_reach_a_report PASSED [  6%]
tests/architecture/test_privacy_architecture.py::test_openapi_schema_exposes_no_question_or_identity_fields PASSED [  7%]
tests/architecture/test_privacy_architecture.py::test_report_payload_carries_no_learner_identity PASSED [  7%]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_expose_no_write_operation[InteractionLogProvider] PASSED [  7%]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_expose_no_write_operation[FeedbackProvider] PASSED [  7%]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_expose_no_write_operation[LearnerProfileProvider] PASSED [  8%]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_expose_no_write_operation[CoursesProvider] PASSED [  8%]
tests/architecture/test_read_only_architecture.py::test_read_only_ports_are_marked_as_such PASSED [  8%]
tests/architecture/test_read_only_architecture.py::test_gap_report_repository_is_the_only_write_seam PASSED [  8%]
tests/architecture/test_read_only_architecture.py::test_repository_write_surface_is_exactly_save PASSED [  9%]
tests/architecture/test_read_only_architecture.py::test_every_read_only_port_has_at_least_two_independent_adapters PASSED [  9%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[ForeignCoursesProvider] PASSED [  9%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[ForeignFeedbackProvider] PASSED [  9%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[ForeignInteractionLogProvider] PASSED [  9%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[ForeignLearnerProfileProvider] PASSED [ 10%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[MockCoursesProvider] PASSED [ 10%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[MockFeedbackProvider] PASSED [ 10%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[MockInteractionLogProvider] PASSED [ 10%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[MockLearnerProfileProvider] PASSED [ 11%]
tests/architecture/test_read_only_architecture.py::test_read_only_adapters_expose_no_write_operation[TemplateInteractionLogProvider] PASSED [ 11%]
tests/architecture/test_read_only_architecture.py::test_the_real_adapter_template_is_also_read_only PASSED [ 11%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[langgraph] PASSED [ 11%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[langchain] PASSED [ 12%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[llama_index] PASSED [ 12%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[openai] PASSED [ 12%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[anthropic] PASSED [ 12%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[cohere] PASSED [ 13%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[transformers] PASSED [ 13%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[sentence_transformers] PASSED [ 13%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[torch] PASSED [ 13%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[chromadb] PASSED [ 14%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[faiss] PASSED [ 14%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[pinecone] PASSED [ 14%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[weaviate] PASSED [ 14%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[qdrant] PASSED [ 14%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[milvus] PASSED [ 15%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[sqlalchemy] PASSED [ 15%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[psycopg] PASSED [ 15%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[pymongo] PASSED [ 15%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[asyncpg] PASSED [ 16%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[redis] PASSED [ 16%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[boto3] PASSED [ 16%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[celery] PASSED [ 16%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[jinja2] PASSED [ 17%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[flask] PASSED [ 17%]
tests/architecture/test_read_only_architecture.py::test_no_banned_dependency_is_imported[django] PASSED [ 17%]
tests/architecture/test_read_only_architecture.py::test_no_llm_rag_or_vector_machinery_anywhere PASSED [ 17%]
tests/architecture/test_read_only_architecture.py::test_no_frontend_assets_exist_in_the_repository PASSED [ 18%]
tests/architecture/test_read_only_architecture.py::test_domain_layer_does_not_depend_on_adapters_api_or_frameworks PASSED [ 18%]
tests/architecture/test_read_only_architecture.py::test_application_layer_does_not_depend_on_adapters_or_http PASSED [ 18%]
tests/architecture/test_read_only_architecture.py::test_ports_layer_depends_only_on_the_domain PASSED [ 18%]
tests/architecture/test_read_only_architecture.py::test_provider_selection_uses_a_registry_not_a_conditional_chain PASSED [ 19%]
tests/conformance/test_courses_conformance.py::test_adapter_implements_the_port[mock] PASSED [ 19%]
tests/conformance/test_courses_conformance.py::test_adapter_implements_the_port[foreign] PASSED [ 19%]
tests/conformance/test_courses_conformance.py::test_adapter_is_read_only[mock] PASSED [ 19%]
tests/conformance/test_courses_conformance.py::test_adapter_is_read_only[foreign] PASSED [ 19%]
tests/conformance/test_courses_conformance.py::test_recommendations_are_domain_objects_for_requested_topics[mock] PASSED [ 20%]
tests/conformance/test_courses_conformance.py::test_recommendations_are_domain_objects_for_requested_topics[foreign] PASSED [ 20%]
tests/conformance/test_courses_conformance.py::test_catalogue_returns_course_summaries_with_lessons[mock] PASSED [ 20%]
tests/conformance/test_courses_conformance.py::test_catalogue_returns_course_summaries_with_lessons[foreign] PASSED [ 20%]
tests/conformance/test_courses_conformance.py::test_enrolments_are_domain_objects_scoped_to_the_learner[mock] PASSED [ 21%]
tests/conformance/test_courses_conformance.py::test_enrolments_are_domain_objects_scoped_to_the_learner[foreign] PASSED [ 21%]
tests/conformance/test_courses_conformance.py::test_no_upstream_payload_leaks_into_domain_objects[mock] PASSED [ 21%]
tests/conformance/test_courses_conformance.py::test_no_upstream_payload_leaks_into_domain_objects[foreign] PASSED [ 21%]
tests/conformance/test_courses_conformance.py::test_status_is_a_source_status[mock] PASSED [ 22%]
tests/conformance/test_courses_conformance.py::test_status_is_a_source_status[foreign] PASSED [ 22%]
tests/conformance/test_courses_conformance.py::test_empty_catalogue_is_reported_as_empty_not_unavailable[mock] PASSED [ 22%]
tests/conformance/test_courses_conformance.py::test_empty_catalogue_is_reported_as_empty_not_unavailable[foreign] PASSED [ 22%]
tests/conformance/test_courses_conformance.py::test_unavailable_source_raises_provider_unavailable[mock] PASSED [ 23%]
tests/conformance/test_courses_conformance.py::test_unavailable_source_raises_provider_unavailable[foreign] PASSED [ 23%]
tests/conformance/test_courses_conformance.py::test_timeout_raises_provider_timeout[mock] PASSED [ 23%]
tests/conformance/test_courses_conformance.py::test_timeout_raises_provider_timeout[foreign] PASSED [ 23%]
tests/conformance/test_courses_conformance.py::test_contract_breach_raises_provider_invalid_response[mock] PASSED [ 23%]
tests/conformance/test_courses_conformance.py::test_contract_breach_raises_provider_invalid_response[foreign] PASSED [ 24%]
tests/conformance/test_courses_conformance.py::test_reads_are_repeatable_and_side_effect_free[mock] PASSED [ 24%]
tests/conformance/test_courses_conformance.py::test_reads_are_repeatable_and_side_effect_free[foreign] PASSED [ 24%]
tests/conformance/test_courses_conformance.py::test_unrequested_topics_are_not_recommended[mock] PASSED [ 24%]
tests/conformance/test_courses_conformance.py::test_unrequested_topics_are_not_recommended[foreign] PASSED [ 25%]
tests/conformance/test_feedback_conformance.py::test_adapter_implements_the_port[mock] PASSED [ 25%]
tests/conformance/test_feedback_conformance.py::test_adapter_implements_the_port[foreign] PASSED [ 25%]
tests/conformance/test_feedback_conformance.py::test_adapter_is_read_only[mock] PASSED [ 25%]
tests/conformance/test_feedback_conformance.py::test_adapter_is_read_only[foreign] PASSED [ 26%]
tests/conformance/test_feedback_conformance.py::test_returns_domain_records[mock] PASSED [ 26%]
tests/conformance/test_feedback_conformance.py::test_returns_domain_records[foreign] PASSED [ 26%]
tests/conformance/test_feedback_conformance.py::test_ratings_are_normalised_to_the_platform_vocabulary[mock] PASSED [ 26%]
tests/conformance/test_feedback_conformance.py::test_ratings_are_normalised_to_the_platform_vocabulary[foreign] PASSED [ 27%]
tests/conformance/test_feedback_conformance.py::test_only_requested_interactions_are_returned[mock] PASSED [ 27%]
tests/conformance/test_feedback_conformance.py::test_only_requested_interactions_are_returned[foreign] PASSED [ 27%]
tests/conformance/test_feedback_conformance.py::test_no_upstream_payload_leaks_into_domain_records[mock] PASSED [ 27%]
tests/conformance/test_feedback_conformance.py::test_no_upstream_payload_leaks_into_domain_records[foreign] PASSED [ 28%]
tests/conformance/test_feedback_conformance.py::test_status_is_a_source_status[mock] PASSED [ 28%]
tests/conformance/test_feedback_conformance.py::test_status_is_a_source_status[foreign] PASSED [ 28%]
tests/conformance/test_feedback_conformance.py::test_empty_source_is_reported_as_empty_not_unavailable[mock] PASSED [ 28%]
tests/conformance/test_feedback_conformance.py::test_empty_source_is_reported_as_empty_not_unavailable[foreign] PASSED [ 28%]
tests/conformance/test_feedback_conformance.py::test_unavailable_source_raises_provider_unavailable[mock] PASSED [ 29%]
tests/conformance/test_feedback_conformance.py::test_unavailable_source_raises_provider_unavailable[foreign] PASSED [ 29%]
tests/conformance/test_feedback_conformance.py::test_timeout_raises_provider_timeout[mock] PASSED [ 29%]
tests/conformance/test_feedback_conformance.py::test_timeout_raises_provider_timeout[foreign] PASSED [ 29%]
tests/conformance/test_feedback_conformance.py::test_contract_breach_raises_provider_invalid_response[mock] PASSED [ 30%]
tests/conformance/test_feedback_conformance.py::test_contract_breach_raises_provider_invalid_response[foreign] PASSED [ 30%]
tests/conformance/test_feedback_conformance.py::test_reads_are_repeatable_and_side_effect_free[mock] PASSED [ 30%]
tests/conformance/test_feedback_conformance.py::test_reads_are_repeatable_and_side_effect_free[foreign] PASSED [ 30%]
tests/conformance/test_feedback_conformance.py::test_unknown_interaction_ids_yield_nothing[mock] PASSED [ 31%]
tests/conformance/test_feedback_conformance.py::test_unknown_interaction_ids_yield_nothing[foreign] PASSED [ 31%]
tests/conformance/test_interaction_log_conformance.py::test_adapter_implements_the_port[mock] PASSED [ 31%]
tests/conformance/test_interaction_log_conformance.py::test_adapter_implements_the_port[foreign] PASSED [ 31%]
tests/conformance/test_interaction_log_conformance.py::test_adapter_is_read_only[mock] PASSED [ 32%]
tests/conformance/test_interaction_log_conformance.py::test_adapter_is_read_only[foreign] PASSED [ 32%]
tests/conformance/test_interaction_log_conformance.py::test_returns_domain_records[mock] PASSED [ 32%]
tests/conformance/test_interaction_log_conformance.py::test_returns_domain_records[foreign] PASSED [ 32%]
tests/conformance/test_interaction_log_conformance.py::test_values_are_normalised_not_upstream_spellings[mock] PASSED [ 33%]
tests/conformance/test_interaction_log_conformance.py::test_values_are_normalised_not_upstream_spellings[foreign] PASSED [ 33%]
tests/conformance/test_interaction_log_conformance.py::test_no_upstream_payload_leaks_into_domain_records[mock] PASSED [ 33%]
tests/conformance/test_interaction_log_conformance.py::test_no_upstream_payload_leaks_into_domain_records[foreign] PASSED [ 33%]
tests/conformance/test_interaction_log_conformance.py::test_count_is_a_non_negative_integer[mock] PASSED [ 33%]
tests/conformance/test_interaction_log_conformance.py::test_count_is_a_non_negative_integer[foreign] PASSED [ 34%]
tests/conformance/test_interaction_log_conformance.py::test_status_is_a_source_status[mock] PASSED [ 34%]
tests/conformance/test_interaction_log_conformance.py::test_status_is_a_source_status[foreign] PASSED [ 34%]
tests/conformance/test_interaction_log_conformance.py::test_empty_source_is_reported_as_empty_not_unavailable[mock] PASSED [ 34%]
tests/conformance/test_interaction_log_conformance.py::test_empty_source_is_reported_as_empty_not_unavailable[foreign] PASSED [ 35%]
tests/conformance/test_interaction_log_conformance.py::test_unavailable_source_raises_provider_unavailable[mock] PASSED [ 35%]
tests/conformance/test_interaction_log_conformance.py::test_unavailable_source_raises_provider_unavailable[foreign] PASSED [ 35%]
tests/conformance/test_interaction_log_conformance.py::test_timeout_raises_provider_timeout[mock] PASSED [ 35%]
tests/conformance/test_interaction_log_conformance.py::test_timeout_raises_provider_timeout[foreign] PASSED [ 36%]
tests/conformance/test_interaction_log_conformance.py::test_contract_breach_raises_provider_invalid_response[mock] PASSED [ 36%]
tests/conformance/test_interaction_log_conformance.py::test_contract_breach_raises_provider_invalid_response[foreign] PASSED [ 36%]
tests/conformance/test_interaction_log_conformance.py::test_reads_are_repeatable_and_side_effect_free[mock] PASSED [ 36%]
tests/conformance/test_interaction_log_conformance.py::test_reads_are_repeatable_and_side_effect_free[foreign] PASSED [ 37%]
tests/conformance/test_interaction_log_conformance.py::test_unknown_learner_is_empty_not_a_failure[mock] PASSED [ 37%]
tests/conformance/test_interaction_log_conformance.py::test_unknown_learner_is_empty_not_a_failure[foreign] PASSED [ 37%]
tests/conformance/test_learner_profile_conformance.py::test_adapter_implements_the_port[mock] PASSED [ 37%]
tests/conformance/test_learner_profile_conformance.py::test_adapter_implements_the_port[foreign] PASSED [ 38%]
tests/conformance/test_learner_profile_conformance.py::test_adapter_is_read_only[mock] PASSED [ 38%]
tests/conformance/test_learner_profile_conformance.py::test_adapter_is_read_only[foreign] PASSED [ 38%]
tests/conformance/test_learner_profile_conformance.py::test_returns_a_domain_profile[mock] PASSED [ 38%]
tests/conformance/test_learner_profile_conformance.py::test_returns_a_domain_profile[foreign] PASSED [ 38%]
tests/conformance/test_learner_profile_conformance.py::test_naric_values_are_normalised[mock] PASSED [ 39%]
tests/conformance/test_learner_profile_conformance.py::test_naric_values_are_normalised[foreign] PASSED [ 39%]
tests/conformance/test_learner_profile_conformance.py::test_no_upstream_payload_leaks_into_the_profile[mock] PASSED [ 39%]
tests/conformance/test_learner_profile_conformance.py::test_no_upstream_payload_leaks_into_the_profile[foreign] PASSED [ 39%]
tests/conformance/test_learner_profile_conformance.py::test_no_speciality_is_reported_as_empty_not_unavailable[mock] PASSED [ 40%]
tests/conformance/test_learner_profile_conformance.py::test_no_speciality_is_reported_as_empty_not_unavailable[foreign] PASSED [ 40%]
tests/conformance/test_learner_profile_conformance.py::test_unavailable_source_raises_provider_unavailable[mock] PASSED [ 40%]
tests/conformance/test_learner_profile_conformance.py::test_unavailable_source_raises_provider_unavailable[foreign] PASSED [ 40%]
tests/conformance/test_learner_profile_conformance.py::test_timeout_raises_provider_timeout[mock] PASSED [ 41%]
tests/conformance/test_learner_profile_conformance.py::test_timeout_raises_provider_timeout[foreign] PASSED [ 41%]
tests/conformance/test_learner_profile_conformance.py::test_contract_breach_raises_provider_invalid_response[mock] PASSED [ 41%]
tests/conformance/test_learner_profile_conformance.py::test_contract_breach_raises_provider_invalid_response[foreign] PASSED [ 41%]
tests/conformance/test_learner_profile_conformance.py::test_reads_are_repeatable_and_side_effect_free[mock] PASSED [ 42%]
tests/conformance/test_learner_profile_conformance.py::test_reads_are_repeatable_and_side_effect_free[foreign] PASSED [ 42%]
tests/conformance/test_learner_profile_conformance.py::test_unknown_learner_yields_an_empty_speciality_not_an_invented_one[mock] PASSED [ 42%]
tests/conformance/test_learner_profile_conformance.py::test_unknown_learner_yields_an_empty_speciality_not_an_invented_one[foreign] PASSED [ 42%]
tests/integration/test_foreign_adapter_swap.py::test_report_from_the_foreign_source_is_identical_to_the_mock_report PASSED [ 42%]
tests/integration/test_foreign_adapter_swap.py::test_foreign_source_produces_the_same_evidence_identifiers PASSED [ 43%]
tests/integration/test_foreign_adapter_swap.py::test_foreign_values_are_normalised_into_platform_types PASSED [ 43%]
tests/integration/test_foreign_adapter_swap.py::test_the_unmodified_api_serves_the_foreign_source PASSED [ 43%]
tests/integration/test_foreign_adapter_swap.py::test_no_nexus_vocabulary_reaches_the_api_response PASSED [ 43%]
tests/integration/test_foreign_adapter_swap.py::test_the_swap_touches_only_adapters_registry_and_configuration PASSED [ 44%]
tests/integration/test_foreign_adapter_swap.py::test_foreign_source_is_deterministic_too PASSED [ 44%]
tests/integration/test_foreign_adapter_swap.py::test_each_port_can_be_swapped_independently_without_touching_the_others[interaction_log] PASSED [ 44%]
tests/integration/test_foreign_adapter_swap.py::test_each_port_can_be_swapped_independently_without_touching_the_others[feedback] PASSED [ 44%]
tests/integration/test_foreign_adapter_swap.py::test_each_port_can_be_swapped_independently_without_touching_the_others[profile] PASSED [ 45%]
tests/integration/test_foreign_adapter_swap.py::test_each_port_can_be_swapped_independently_without_touching_the_others[courses] PASSED [ 45%]
tests/integration/test_persistence.py::test_generated_report_is_persisted_through_the_repository PASSED [ 45%]
tests/integration/test_persistence.py::test_nothing_is_persisted_below_the_threshold PASSED [ 45%]
tests/integration/test_persistence.py::test_reports_are_scoped_by_owner PASSED [ 46%]
tests/integration/test_persistence.py::test_stored_report_keeps_internal_ownership_information PASSED [ 46%]
tests/integration/test_persistence.py::test_repository_is_the_only_component_that_records_anything PASSED [ 46%]
tests/integration/test_provider_registry.py::test_every_port_registry_offers_mock_and_foreign[COURSES_PROVIDER] PASSED [ 46%]
tests/integration/test_provider_registry.py::test_every_port_registry_offers_mock_and_foreign[FEEDBACK_PROVIDER] PASSED [ 47%]
tests/integration/test_provider_registry.py::test_every_port_registry_offers_mock_and_foreign[INTERACTION_LOG_PROVIDER] PASSED [ 47%]
tests/integration/test_provider_registry.py::test_every_port_registry_offers_mock_and_foreign[PROFILE_PROVIDER] PASSED [ 47%]
tests/integration/test_provider_registry.py::test_unknown_provider_name_fails_loudly[COURSES_PROVIDER] PASSED [ 47%]
tests/integration/test_provider_registry.py::test_unknown_provider_name_fails_loudly[FEEDBACK_PROVIDER] PASSED [ 47%]
tests/integration/test_provider_registry.py::test_unknown_provider_name_fails_loudly[INTERACTION_LOG_PROVIDER] PASSED [ 48%]
tests/integration/test_provider_registry.py::test_unknown_provider_name_fails_loudly[PROFILE_PROVIDER] PASSED [ 48%]
tests/integration/test_provider_registry.py::test_missing_real_provider_never_silently_falls_back_to_mock[COURSES_PROVIDER] PASSED [ 48%]
tests/integration/test_provider_registry.py::test_missing_real_provider_never_silently_falls_back_to_mock[FEEDBACK_PROVIDER] PASSED [ 48%]
tests/integration/test_provider_registry.py::test_missing_real_provider_never_silently_falls_back_to_mock[INTERACTION_LOG_PROVIDER] PASSED [ 49%]
tests/integration/test_provider_registry.py::test_missing_real_provider_never_silently_falls_back_to_mock[PROFILE_PROVIDER] PASSED [ 49%]
tests/integration/test_provider_registry.py::test_mock_selection_wires_mock_adapters PASSED [ 49%]
tests/integration/test_provider_registry.py::test_foreign_selection_wires_foreign_adapters_with_no_other_change PASSED [ 49%]
tests/integration/test_provider_registry.py::test_ports_can_be_mixed_independently PASSED [ 50%]
tests/integration/test_provider_registry.py::test_unknown_mock_scenario_fails_loudly PASSED [ 50%]
tests/integration/test_provider_registry.py::test_thresholds_come_from_settings PASSED [ 50%]
tests/integration/test_provider_registry.py::test_registered_adapters_are_the_only_write_free_surface_used PASSED [ 50%]
tests/test_docs_and_config.py::test_deliverable_documents_exist[path0] PASSED [ 51%]
tests/test_docs_and_config.py::test_deliverable_documents_exist[path1] PASSED [ 51%]
tests/test_docs_and_config.py::test_deliverable_documents_exist[path2] PASSED [ 51%]
tests/test_docs_and_config.py::test_deliverable_documents_exist[path3] PASSED [ 51%]
tests/test_docs_and_config.py::test_assumptions_register_uses_the_required_table_shape PASSED [ 52%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[qualifying interaction] PASSED [ 52%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Follow-up interactions count] PASSED [ 52%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Duplicate `interaction_id`] PASSED [ 52%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[exactly 10 qualifying interactions] PASSED [ 52%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Explain-differently threshold = 2] PASSED [ 53%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Follow-up threshold = 2] PASSED [ 53%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Low-rating threshold = 1] PASSED [ 53%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[topic-description registry] PASSED [ 53%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[Partial speciality data] PASSED [ 54%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[complete, all-time history] PASSED [ 54%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[report_version] PASSED [ 54%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[analysis_version] PASSED [ 54%]
tests/test_docs_and_config.py::test_assumptions_register_documents_each_required_assumption[re-evaluates the source data] PASSED [ 55%]
tests/test_docs_and_config.py::test_every_assumption_row_names_a_real_source_file PASSED [ 55%]
tests/test_docs_and_config.py::test_shared_contract_states_what_is_read_and_written PASSED [ 55%]
tests/test_docs_and_config.py::test_integration_runbook_covers_every_dependency_and_the_closing_rules PASSED [ 55%]
tests/test_docs_and_config.py::test_real_adapter_template_has_every_required_todo_marker PASSED [ 56%]
tests/test_docs_and_config.py::test_env_example_contains_placeholders_only PASSED [ 56%]
tests/test_docs_and_config.py::test_configuration_defaults_match_the_documented_thresholds PASSED [ 56%]
tests/test_docs_and_config.py::test_no_threshold_literal_is_hard_coded_in_business_logic PASSED [ 56%]
tests/unit/test_aggregation.py::test_aggregation_spans_every_session_not_just_the_latest PASSED [ 57%]
tests/unit/test_aggregation.py::test_topics_are_grouped_by_the_supplied_topic_tag PASSED [ 57%]
tests/unit/test_aggregation.py::test_unusual_topic_tags_are_never_rewritten_or_reclassified PASSED [ 57%]
tests/unit/test_aggregation.py::test_per_topic_signal_inputs_are_aggregated_over_the_whole_history PASSED [ 57%]
tests/unit/test_aggregation.py::test_aggregate_ordering_is_deterministic PASSED [ 57%]
tests/unit/test_aggregation.py::test_report_uses_full_history_across_sessions PASSED [ 58%]
tests/unit/test_counting_and_threshold.py::test_every_valid_record_counts_once PASSED [ 58%]
tests/unit/test_counting_and_threshold.py::test_follow_up_interactions_count PASSED [ 58%]
tests/unit/test_counting_and_threshold.py::test_clarifying_interactions_count_when_represented_as_records PASSED [ 58%]
tests/unit/test_counting_and_threshold.py::test_explain_differently_counter_does_not_add_to_the_count PASSED [ 59%]
tests/unit/test_counting_and_threshold.py::test_duplicate_interaction_ids_count_once PASSED [ 59%]
tests/unit/test_counting_and_threshold.py::test_records_belonging_to_another_learner_are_discarded PASSED [ 59%]
tests/unit/test_counting_and_threshold.py::test_counting_is_order_independent PASSED [ 59%]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[0-below_threshold-10] PASSED [ 60%]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[5-below_threshold-5] PASSED [ 60%]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[9-below_threshold-1] PASSED [ 60%]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[10-available-0] PASSED [ 60%]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[11-available-0] PASSED [ 61%]
tests/unit/test_counting_and_threshold.py::test_progress_across_the_threshold_matrix[50-available-0] PASSED [ 61%]
tests/unit/test_counting_and_threshold.py::test_no_report_below_ten_interactions[0] PASSED [ 61%]
tests/unit/test_counting_and_threshold.py::test_no_report_below_ten_interactions[5] PASSED [ 61%]
tests/unit/test_counting_and_threshold.py::test_no_report_below_ten_interactions[9] PASSED [ 61%]
tests/unit/test_counting_and_threshold.py::test_no_report_at_nine_but_report_at_ten PASSED [ 62%]
tests/unit/test_counting_and_threshold.py::test_report_available_at_and_above_ten[10] PASSED [ 62%]
tests/unit/test_counting_and_threshold.py::test_report_available_at_and_above_ten[11] PASSED [ 62%]
tests/unit/test_counting_and_threshold.py::test_report_available_at_and_above_ten[50] PASSED [ 62%]
tests/unit/test_counting_and_threshold.py::test_below_threshold_is_not_an_error_and_reports_progress_fields PASSED [ 63%]
tests/unit/test_counting_and_threshold.py::test_threshold_comes_from_configuration_not_code PASSED [ 63%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[count_10] PASSED [ 63%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[count_11] PASSED [ 63%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[count_50] PASSED [ 64%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[struggle_mixed] PASSED [ 64%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[diverse_topics] PASSED [ 64%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[narrow_topics] PASSED [ 64%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[feedback_unavailable] PASSED [ 65%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[profile_partial] PASSED [ 65%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[courses_unavailable] PASSED [ 65%]
tests/unit/test_determinism_and_freshness.py::test_identical_inputs_produce_identical_reports[interactions_partial] PASSED [ 65%]
tests/unit/test_determinism_and_freshness.py::test_report_is_stable_across_repeated_requests_in_one_service PASSED [ 66%]
tests/unit/test_determinism_and_freshness.py::test_provider_record_order_does_not_change_the_report PASSED [ 66%]
tests/unit/test_determinism_and_freshness.py::test_different_clocks_do_not_change_report_content_only_generated_at PASSED [ 66%]
tests/unit/test_determinism_and_freshness.py::test_current_report_reflects_an_eleventh_interaction PASSED [ 66%]
tests/unit/test_determinism_and_freshness.py::test_threshold_is_re_evaluated_against_current_source_data PASSED [ 66%]
tests/unit/test_evidence.py::test_struggle_gap_cannot_be_built_without_evidence_ids PASSED [ 67%]
tests/unit/test_evidence.py::test_gap_cannot_be_built_without_signals PASSED [ 67%]
tests/unit/test_evidence.py::test_signal_evidence_cannot_claim_a_signal_that_did_not_fire PASSED [ 67%]
tests/unit/test_evidence.py::test_per_signal_evidence_must_be_inside_the_gap_evidence_set PASSED [ 67%]
tests/unit/test_evidence.py::test_guard_rejects_a_gap_whose_evidence_id_does_not_resolve PASSED [ 68%]
tests/unit/test_evidence.py::test_guard_keeps_a_gap_whose_evidence_resolves PASSED [ 68%]
tests/unit/test_evidence.py::test_guard_rejects_partially_fabricated_evidence PASSED [ 68%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[count_10] PASSED [ 68%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[count_11] PASSED [ 69%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[count_50] PASSED [ 69%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[struggle_mixed] PASSED [ 69%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[diverse_topics] PASSED [ 69%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[narrow_topics] PASSED [ 70%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[heavy_explain_differently] PASSED [ 70%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[heavy_follow_ups] PASSED [ 70%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[duplicate_interaction_ids] PASSED [ 70%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[mixed_owner_records] PASSED [ 71%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[feedback_empty] PASSED [ 71%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[feedback_unavailable] PASSED [ 71%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[feedback_partial] PASSED [ 71%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[feedback_invalid] PASSED [ 71%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_fully_covered] PASSED [ 72%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_no_speciality] PASSED [ 72%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_partial] PASSED [ 72%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_unavailable] PASSED [ 72%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[profile_invalid] PASSED [ 73%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_unavailable] PASSED [ 73%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_partial] PASSED [ 73%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_invalid] PASSED [ 73%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_not_enrolled] PASSED [ 74%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[courses_only_invalid_candidates] PASSED [ 74%]
tests/unit/test_evidence.py::test_every_generated_gap_carries_resolvable_evidence[interactions_partial] PASSED [ 74%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[count_10] PASSED [ 74%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[count_11] PASSED [ 75%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[count_50] PASSED [ 75%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[struggle_mixed] PASSED [ 75%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[diverse_topics] PASSED [ 75%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[narrow_topics] PASSED [ 76%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[heavy_explain_differently] PASSED [ 76%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[heavy_follow_ups] PASSED [ 76%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[duplicate_interaction_ids] PASSED [ 76%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[mixed_owner_records] PASSED [ 76%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[feedback_empty] PASSED [ 77%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[feedback_unavailable] PASSED [ 77%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[feedback_partial] PASSED [ 77%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[feedback_invalid] PASSED [ 77%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_fully_covered] PASSED [ 78%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_no_speciality] PASSED [ 78%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_partial] PASSED [ 78%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_unavailable] PASSED [ 78%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[profile_invalid] PASSED [ 79%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_unavailable] PASSED [ 79%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_partial] PASSED [ 79%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_invalid] PASSED [ 79%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_not_enrolled] PASSED [ 80%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[courses_only_invalid_candidates] PASSED [ 80%]
tests/unit/test_evidence.py::test_no_gap_topic_is_invented_outside_history_or_speciality[interactions_partial] PASSED [ 80%]
tests/unit/test_observability_privacy.py::test_disallowed_fields_are_dropped_before_they_reach_a_log_record PASSED [ 80%]
tests/unit/test_observability_privacy.py::test_sanitise_only_keeps_allowlisted_fields PASSED [ 80%]
tests/unit/test_observability_privacy.py::test_report_generation_logs_counts_but_no_weak_topic_content PASSED [ 81%]
tests/unit/test_observability_privacy.py::test_progress_logging_stays_within_the_allowlist PASSED [ 81%]
tests/unit/test_observability_privacy.py::test_no_log_line_ever_contains_a_feedback_comment PASSED [ 81%]
tests/unit/test_recommendations.py::test_valid_lesson_recommendation_is_kept PASSED [ 81%]
tests/unit/test_recommendations.py::test_unknown_lesson_id_is_removed_and_not_replaced PASSED [ 82%]
tests/unit/test_recommendations.py::test_unknown_course_id_is_removed_and_not_replaced PASSED [ 82%]
tests/unit/test_recommendations.py::test_existing_enrolment_becomes_lesson_recommendations PASSED [ 82%]
tests/unit/test_recommendations.py::test_enrolment_without_a_matching_lesson_drops_the_candidate_rather_than_guessing PASSED [ 82%]
tests/unit/test_recommendations.py::test_another_learners_enrolment_does_not_affect_this_learner PASSED [ 83%]
tests/unit/test_recommendations.py::test_candidates_for_topics_that_are_not_gaps_are_ignored PASSED [ 83%]
tests/unit/test_recommendations.py::test_courses_unavailable_marks_recommendations_unavailable PASSED [ 83%]
tests/unit/test_recommendations.py::test_partial_course_data_marks_recommendations_partial PASSED [ 83%]
tests/unit/test_recommendations.py::test_recommendations_are_deduplicated_and_sorted PASSED [ 84%]
tests/unit/test_recommendations.py::test_report_recommendations_resolve_to_real_catalogue_identifiers PASSED [ 84%]
tests/unit/test_recommendations.py::test_enrolled_course_yields_lesson_recommendations_in_the_report PASSED [ 84%]
tests/unit/test_recommendations.py::test_not_enrolled_learner_gets_the_course_level_recommendation PASSED [ 84%]
tests/unit/test_recommendations.py::test_gaps_survive_when_the_course_source_is_unavailable PASSED [ 85%]
tests/unit/test_recommendations.py::test_partial_course_source_is_reported_as_partial_in_the_report PASSED [ 85%]
tests/unit/test_recommendations.py::test_invalid_course_source_keeps_gaps_and_marks_recommendations_unavailable PASSED [ 85%]
tests/unit/test_recommendations.py::test_all_invalid_candidates_leave_gaps_without_recommendations PASSED [ 85%]
tests/unit/test_report_assembly.py::test_report_surfaces_at_least_three_topic_areas_when_history_supports_it PASSED [ 85%]
tests/unit/test_report_assembly.py::test_narrow_history_is_not_padded_and_says_so PASSED [ 86%]
tests/unit/test_report_assembly.py::test_no_gap_is_emitted_for_a_topic_below_every_threshold PASSED [ 86%]
tests/unit/test_report_assembly.py::test_gap_descriptions_come_from_the_configured_registry PASSED [ 86%]
tests/unit/test_report_assembly.py::test_unknown_topic_tags_fall_back_to_the_configured_default_template PASSED [ 86%]
tests/unit/test_report_assembly.py::test_registry_rejects_a_default_template_without_the_topic_placeholder PASSED [ 87%]
tests/unit/test_report_assembly.py::test_registry_missing_file_fails_loudly PASSED [ 87%]
tests/unit/test_report_assembly.py::test_report_carries_versions_threshold_and_source_statuses PASSED [ 87%]
tests/unit/test_report_assembly.py::test_minimum_topic_areas_is_configuration_driven PASSED [ 87%]
tests/unit/test_report_assembly.py::test_gap_ordering_is_struggle_first_then_unexplored_each_sorted_by_topic PASSED [ 88%]
tests/unit/test_resilience.py::test_unusable_interaction_source_raises_instead_of_returning_an_empty_report[interactions_unavailable-unavailable] PASSED [ 88%]
tests/unit/test_resilience.py::test_unusable_interaction_source_raises_instead_of_returning_an_empty_report[interactions_timeout-unavailable] PASSED [ 88%]
tests/unit/test_resilience.py::test_unusable_interaction_source_raises_instead_of_returning_an_empty_report[interactions_invalid-invalid] PASSED [ 88%]
tests/unit/test_resilience.py::test_invalid_interaction_payload_raises_a_typed_contract_error PASSED [ 89%]
tests/unit/test_resilience.py::test_partial_interaction_source_is_preserved_and_noticed PASSED [ 89%]
tests/unit/test_resilience.py::test_empty_interaction_history_is_progress_not_failure PASSED [ 89%]
tests/unit/test_resilience.py::test_feedback_unavailable_keeps_gaps_and_drops_only_the_rating_signal PASSED [ 89%]
tests/unit/test_resilience.py::test_feedback_invalid_is_distinct_from_unavailable PASSED [ 90%]
tests/unit/test_resilience.py::test_feedback_empty_means_the_learner_genuinely_has_no_ratings PASSED [ 90%]
tests/unit/test_resilience.py::test_feedback_partial_is_used_but_flagged_as_possibly_incomplete PASSED [ 90%]
tests/unit/test_resilience.py::test_empty_and_unavailable_feedback_are_never_the_same_state PASSED [ 90%]
tests/unit/test_resilience.py::test_profile_status_is_preserved_verbatim[profile_unavailable-unavailable] PASSED [ 90%]
tests/unit/test_resilience.py::test_profile_status_is_preserved_verbatim[profile_invalid-invalid] PASSED [ 91%]
tests/unit/test_resilience.py::test_profile_status_is_preserved_verbatim[profile_partial-partial] PASSED [ 91%]
tests/unit/test_resilience.py::test_profile_status_is_preserved_verbatim[profile_no_speciality-empty] PASSED [ 91%]
tests/unit/test_resilience.py::test_courses_status_is_preserved_verbatim[courses_unavailable-unavailable] PASSED [ 91%]
tests/unit/test_resilience.py::test_courses_status_is_preserved_verbatim[courses_invalid-invalid] PASSED [ 92%]
tests/unit/test_resilience.py::test_courses_status_is_preserved_verbatim[courses_partial-partial] PASSED [ 92%]
tests/unit/test_resilience.py::test_provider_timeout_is_its_own_type PASSED [ 92%]
tests/unit/test_resilience.py::test_provider_unavailable_is_its_own_type PASSED [ 92%]
tests/unit/test_resilience.py::test_service_never_catches_bare_exceptions_from_providers PASSED [ 93%]
tests/unit/test_signals.py::test_explain_differently_fires_at_the_configured_threshold PASSED [ 93%]
tests/unit/test_signals.py::test_explain_differently_totals_across_interactions_in_the_topic PASSED [ 93%]
tests/unit/test_signals.py::test_explain_differently_below_threshold_does_not_surface PASSED [ 93%]
tests/unit/test_signals.py::test_follow_up_signal_fires_at_the_configured_threshold PASSED [ 94%]
tests/unit/test_signals.py::test_single_follow_up_does_not_surface PASSED [ 94%]
tests/unit/test_signals.py::test_heavy_follow_up_scenario_surfaces_only_the_follow_up_topic PASSED [ 94%]
tests/unit/test_signals.py::test_low_rating_signal_fires_on_a_single_thumbs_down PASSED [ 94%]
tests/unit/test_signals.py::test_thumbs_up_is_never_a_struggle_signal PASSED [ 95%]
tests/unit/test_signals.py::test_ratings_for_unknown_interactions_cannot_manufacture_evidence PASSED [ 95%]
tests/unit/test_signals.py::test_ratings_owned_by_another_learner_are_ignored PASSED [ 95%]
tests/unit/test_signals.py::test_low_rating_signal_is_skipped_when_the_rating_source_cannot_be_read PASSED [ 95%]
tests/unit/test_signals.py::test_empty_rating_source_is_evaluated_and_simply_finds_nothing PASSED [ 95%]
tests/unit/test_signals.py::test_signals_combine_on_one_topic_in_canonical_order PASSED [ 96%]
tests/unit/test_signals.py::test_topic_below_every_threshold_is_not_a_struggle PASSED [ 96%]
tests/unit/test_signals.py::test_scenarios_isolate_single_signals[heavy_explain_differently-expected0] PASSED [ 96%]
tests/unit/test_signals.py::test_scenarios_isolate_single_signals[heavy_follow_ups-expected1] PASSED [ 96%]
tests/unit/test_signals.py::test_showcase_scenario_signal_matrix PASSED  [ 97%]
tests/unit/test_unexplored.py::test_speciality_area_with_zero_interactions_is_unexplored PASSED [ 97%]
tests/unit/test_unexplored.py::test_fully_covered_speciality_produces_no_unexplored_gap PASSED [ 97%]
tests/unit/test_unexplored.py::test_no_speciality_is_stated_explicitly_and_never_inferred PASSED [ 97%]
tests/unit/test_unexplored.py::test_partial_speciality_keeps_partial_status_and_flags_incompleteness PASSED [ 98%]
tests/unit/test_unexplored.py::test_unavailable_profile_does_not_invent_speciality_areas PASSED [ 98%]
tests/unit/test_unexplored.py::test_invalid_profile_is_distinct_from_unavailable PASSED [ 98%]
tests/unit/test_unexplored.py::test_speciality_comparison_is_exact_and_case_sensitive PASSED [ 98%]
tests/unit/test_unexplored.py::test_report_contains_unexplored_gaps_for_uncovered_speciality_areas PASSED [ 99%]
tests/unit/test_unexplored.py::test_fully_covered_speciality_yields_no_unexplored_gaps_in_the_report PASSED [ 99%]
tests/unit/test_unexplored.py::test_no_speciality_reports_that_analysis_could_not_be_performed PASSED [ 99%]
tests/unit/test_unexplored.py::test_partial_speciality_is_preserved_and_documented_in_the_report PASSED [ 99%]
tests/unit/test_unexplored.py::test_unavailable_profile_still_yields_evidence_based_struggle_analysis PASSED [100%]

============================== warnings summary ===============================
..\..\AppData\Local\Programs\Python\Python311\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\Administrator\AppData\Local\Programs\Python\Python311\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 421 passed, 1 warning in 1.86s ========================
```
