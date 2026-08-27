# UC-07 Shared Contract

This is the contract UC-07 consumes and produces. Every field is marked
**SPECIFIED BY COMPANY** (fixed platform contract, must not be changed locally) or
**ASSUMED BY US** (a UC-07 modelling decision; see `docs/assumptions.md`).

## Data ownership

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

## 1. Enumerations

### NaricLevel — SPECIFIED BY COMPANY

`LEVEL_3 | LEVEL_4 | LEVEL_5 | LEVEL_6 | LEVEL_7 | LEVEL_7_PLUS`

No integer NARIC scale exists. Adapters map upstream spellings explicitly; an
unmappable value is a contract error.

### NaricLevelSource — SPECIFIED BY COMPANY

`retrieved | default`

### SourceStatus — SPECIFIED BY COMPANY

`available | empty | partial | unavailable | invalid`

`empty` ≠ `unavailable`. Statuses are preserved, never collapsed.

### RatingState — SPECIFIED BY COMPANY

`pending | rated` (field on `InteractionRecord`)

### Rating — SPECIFIED BY COMPANY

`up | down` (field on `FeedbackRecord`)

### ThresholdStatus — ASSUMED BY US

`below_threshold | available`

### GapType — ASSUMED BY US

`struggle | unexplored`

### SignalKind — ASSUMED BY US

`explain_differently | follow_up | low_rating | unexplored_speciality`

Canonical order is exactly the order above; every report uses it.

### EvidenceBasis — ASSUMED BY US

`interaction_ids | zero_interactions_for_speciality_area`

### RecommendationStatus — ASSUMED BY US

`available | partial | unavailable | empty`

### RecommendationType — ASSUMED BY US

`course | lesson`

### DescriptionSource — ASSUMED BY US

`registry | registry_default`

### UnexploredAnalysisState — ASSUMED BY US

`performed | performed_partial | not_performed_no_speciality |
not_performed_profile_unavailable | not_performed_profile_invalid`

### NoticeSeverity / NoticeCode — ASSUMED BY US

Severity: `info | warning`. Codes:
`recommendations_temporarily_unavailable`, `recommendations_partial`,
`rating_signal_unavailable`, `rating_signal_partial`, `rating_signal_no_ratings`,
`rating_signal_invalid`, `speciality_analysis_unavailable`,
`speciality_analysis_invalid`, `speciality_analysis_partial`,
`speciality_analysis_not_possible_no_speciality`,
`insufficient_topic_diversity`, `interaction_source_partial`.

---

## 2. Upstream types (read-only)

### InteractionRecord

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

### FeedbackRecord

| Field | Type | Ownership | Notes |
|-------|------|-----------|-------|
| `rating_id` | string, non-empty | SPECIFIED BY COMPANY | |
| `interaction_id` | string, non-empty | SPECIFIED BY COMPANY | Must resolve to an analysed interaction to count. |
| `user_id` | string, non-empty | SPECIFIED BY COMPANY | Ratings owned by another learner are ignored. |
| `rated_at` | datetime, tz-aware (UTC) | SPECIFIED BY COMPANY | |
| `rating` | `Rating` | SPECIFIED BY COMPANY | Only `down` is a struggle signal. |
| `comment` | string \| null | SPECIFIED BY COMPANY | Read, never emitted, never logged (A-13). |

### LearnerProfile

| Field | Type | Ownership | Notes |
|-------|------|-----------|-------|
| `user_id` | string, non-empty | SPECIFIED BY COMPANY | |
| `speciality_areas` | tuple[string] | SPECIFIED BY COMPANY | Same vocabulary as `topic_tag` (A-15); duplicates removed, order preserved. |
| `speciality_status` | `SourceStatus` | ASSUMED BY US | Status of the speciality subsection (A-18). `available` requires ≥1 area; `empty` requires none. |
| `naric_level` | `NaricLevel` \| null | SPECIFIED BY COMPANY | |
| `naric_level_source` | `NaricLevelSource` \| null | SPECIFIED BY COMPANY | Required when `naric_level` is present. |

### CourseSummary / LessonSummary — ASSUMED BY US

`CourseSummary`: `course_id` (non-empty), `title` (nullable), `topic_tags`
(tuple[string]), `lessons` (tuple[`LessonSummary`]).
`LessonSummary`: `lesson_id` (non-empty), `title` (nullable), `topic_tags`
(tuple[string]).

Used solely to validate that recommendation identifiers exist and to find the
lessons in an already-enrolled course that carry a gap's topic.

### Enrolment — ASSUMED BY US

`user_id`, `course_id`, `enrolled_at` (datetime \| null),
`completion_percentage` (integer 0–100 \| null — SPECIFIED BY COMPANY: integer
0–100 only).

### Recommendation — ASSUMED BY US

`topic_tag`, `recommendation_type` (`course`/`lesson`), `course_id`,
`lesson_id` (required for `lesson`, forbidden for `course`), `title` (nullable,
display only).

---

## 3. UC-07 output types

### SignalEvidence — ASSUMED BY US

| Field | Type | Notes |
|-------|------|-------|
| `signal` | `SignalKind` | |
| `observed_value` | integer ≥ 0 | Must be ≥ `threshold`; a signal cannot be recorded otherwise. |
| `threshold` | integer ≥ 0 | The configured threshold that was crossed. |
| `interaction_ids` | tuple[string] | Subset of the gap's evidence ids. |

### GapEvidence — ASSUMED BY US

| Field | Type | Notes |
|-------|------|-------|
| `basis` | `EvidenceBasis` | |
| `interaction_ids` | tuple[string] | Non-empty and unique for `interaction_ids` basis; empty for the zero-interaction basis. |
| `per_signal` | tuple[`SignalEvidence`] | Every per-signal id must be inside `interaction_ids`. |

### Gap — ASSUMED BY US

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

### GapReport — ASSUMED BY US (except where noted)

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

### Threshold state — `ThresholdProgress`

| Field | Type | Notes |
|-------|------|-------|
| `status` | `ThresholdStatus` | Derived from the count; never an error. |
| `interactions_completed` | integer ≥ 0 | Qualifying interactions. |
| `threshold` | integer ≥ 0 | Configured. |
| `interactions_remaining` | integer ≥ 0 | `max(0, threshold - completed)`. |

---

## 4. HTTP contract

### `GET /api/v1/gap-report`

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

### `GET /api/v1/gap-report/progress` (HTTP 200)

```json
{
  "status": "below_threshold",
  "interactions_completed": 5,
  "threshold": 10,
  "interactions_remaining": 5
}
```

### `GET /api/v1/healthz` (HTTP 200)

```json
{"status": "ok", "report_version": "1.0.0", "analysis_version": "1.0.0", "threshold": 10}
```

No endpoint accepts a user id, a query parameter or a body.

### Error envelope (uniform)

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

## 5. Port contract

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

## 6. Extension points

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
