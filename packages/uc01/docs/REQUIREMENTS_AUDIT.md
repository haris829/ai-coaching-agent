# UC-01 requirement audit

Every requirement from the brief, compared against what is actually in this repository.
Status is one of **PASS**, **PARTIAL**, **BLOCKED** — PASS only where the behaviour was
verified by a test run or by the recorded live verification in
[`VERIFICATION.md`](VERIFICATION.md).

Test evidence: `python -m pytest` → **249 passed**.

---

## 1. Session mode selection

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Support `free-form`, `course-linked`, `case-linked` | `SessionMode` enum with those three wire values | `uc01/domain/enums.py` | `test_session_modes.py::test_bootstrap_lists_exactly_the_three_modes` | PASS |
| User can select the mode when opening the interface | Mode radio group rendered from `GET /session-bootstrap`; `mode` is the required field of `POST /sessions` | `uc01/web/index.html`, `uc01/web/static/app.js`, `uc01/api/routes.py` | `test_api_contract.py::test_frontend_is_served_when_enabled`, `test_session_modes.py` (all three open) | PASS |
| Explicit internal enum, not arbitrary strings | All mode/status/source values come from `enums.py`; `SessionMode.parse` rejects anything else | `uc01/domain/enums.py` | `test_session_modes.py::test_session_mode_enum_parse_is_strict`, `::test_unknown_mode_is_rejected` | PASS |

## 2. Mode selector

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Mode selector in the opening flow | `fieldset`/`legend` radio group with title, description, availability badge | `uc01/web/index.html`, `static/app.js` | `test_api_contract.py::test_frontend_is_served_when_enabled` | PASS |
| UI clearly communicates the three modes | Label + description + `Available`/`Disabled` status per mode | `static/app.js` (`renderModes`) | Manual: `VERIFICATION.md` §1–6 | PASS |
| Correctly handles unavailable modes | `available: false` + `reason` from the server; the radio is `disabled` and visually distinct | `uc01/domain/policy.py`, `static/app.js`, `static/styles.css` | `test_api_contract.py::test_ui_state_no_case_files`, `::test_ui_state_courses_unavailable` | PASS |
| Frontend cannot bypass backend validation | The API recomputes availability on every open and refuses a disabled mode with 409 | `uc01/application/session_service.py` (`_handle_unavailable_mode`) | `test_security.py::test_client_cannot_bypass_a_disabled_mode`, `::test_client_cannot_bypass_case_mode_when_it_has_no_cases` | PASS |
| Backend validates the requested mode | Pydantic enum + `SessionMode.parse`; unknown → 422 | `uc01/api/schemas.py`, `uc01/domain/enums.py` | `test_session_modes.py::test_unknown_mode_is_rejected` | PASS |

## 3. Course and lesson picker

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Select a course | `GET /api/v1/courses` + course `<select>` | `uc01/api/routes.py`, `static/app.js` | `test_courses.py::test_courses_available_with_lessons` | PASS |
| Select a lesson belonging to that course | Lessons nested per course; lesson list re-rendered on course change | `uc01/domain/models.py` (`Course.lesson`), `static/app.js` (`renderLessons`) | `test_session_modes.py::test_course_linked_session_opens` | PASS |
| Validate course/lesson accessibility | `get_accessible_course` in the adapter + `Course.lesson()` membership check | `uc01/adapters/mock/courses.py`, `uc01/application/session_service.py` (`_resolve_course_selection`) | `test_courses.py::test_inaccessible_course_belonging_to_another_user_is_rejected`, `::test_lesson_from_a_different_course_is_rejected` | PASS |
| Do not trust client-supplied ids | Every id is re-resolved server-side per request; ids are never used as labels without lookup | `uc01/application/session_service.py` | `test_courses.py::test_invalid_course_id_is_rejected`, `::test_invalid_lesson_id_is_rejected`, `test_security.py::test_user_cannot_use_another_users_course` | PASS |
| Handle Courses Agent unavailable/failure gracefully | `ContractError` → `DependencyStatus(UNAVAILABLE)` → mode disabled with a safe reason | `uc01/application/session_service.py` (`_load_courses`) | `test_courses.py::test_courses_unavailable_returns_200_with_a_reason`, `::test_course_linked_mode_disabled_when_courses_unavailable` | PASS |
| Picker does not break free-form or case-linked | Course loading only happens for a course-linked open; free-form/case-linked never call it | `uc01/application/session_service.py` (`_initialise`) | `test_session_modes.py::test_case_linked_and_free_form_survive_a_courses_outage` | PASS |
| Adapter/interface for Courses Agent | `CoursesService` Protocol | `uc01/contracts/services.py` | `test_adapter_replacement.py::test_adapters_satisfy_their_contract` | PASS |
| Isolated mock Courses adapter on the same contract | `MockCoursesAdapter`, only in `adapters/mock/` | `uc01/adapters/mock/courses.py` | `test_mock_adapters.py` (7 course tests), `test_architecture.py::test_only_the_container_knows_which_adapter_is_used` | PASS |

## 4. Case file picker

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Retrieve accessible case files through an isolated integration | `CaseFileService` + `MockCaseFileAdapter`; `GET /api/v1/case-files` | `uc01/contracts/services.py`, `uc01/adapters/mock/cases.py` | `test_cases.py::test_case_files_available` | PASS |
| Select an accessible case file | Case `<select>` populated from the API | `static/app.js` (`renderCases`) | `test_session_modes.py::test_case_linked_session_opens` | PASS |
| Validate access server-side | `get_accessible_case_file` per open | `uc01/application/session_service.py` (`_resolve_case_selection`) | `test_cases.py::test_inaccessible_case_belonging_to_another_user_is_rejected` | PASS |
| Never trust a client case id | Re-resolved every time; missing and forbidden share one message | `uc01/domain/messages.py`, `session_service.py` | `test_security.py::test_user_cannot_use_another_users_case`, `test_cases.py::test_unknown_case_id_is_rejected` | PASS |
| No accessible cases → case-linked disabled | Empty list → `DependencyState.EMPTY` → mode disabled | `uc01/domain/policy.py` | `test_cases.py::test_no_accessible_case_files_disables_case_mode_only` | PASS |
| UI clearly explains why | `reason: "No accessible case files."` rendered under the disabled mode | `uc01/domain/messages.py`, `static/app.js` | `test_api_contract.py::test_ui_state_no_case_files`; `VERIFICATION.md` §2 | PASS |
| Free-form remains usable | Free-form availability is unconditional in the policy | `uc01/domain/policy.py` | `test_cases.py::test_no_accessible_case_files_disables_case_mode_only` | PASS |
| Course-linked remains usable when Courses is available | Independent dependency evaluation | `uc01/domain/policy.py` | same test (course-linked opens for `dev-bob`) | PASS |
| Absence of cases is not fatal | Bootstrap returns 200 with full data | `session_service.load_bootstrap` | `test_cases.py::test_case_outage_does_not_break_the_rest_of_the_interface` | PASS |

## 5. Context-aware greeting

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Personalised greeting on session open | `LocalTemplateGreetingGenerator.generate` | `uc01/domain/greeting.py` | `test_domain.py::test_personalised_free_form_greeting` | PASS |
| Uses user name, course, lesson, mode, NARIC level | Opening + focus + level sentences assembled from `SessionContext` | `uc01/domain/greeting.py` | `test_domain.py` (5 greeting tests), `test_profile.py::test_profile_available_gives_a_personalised_greeting` | PASS |
| Course-linked greeting references course and lesson | `_focus_sentence` for `COURSE_LINKED` | `uc01/domain/greeting.py` | `test_session_modes.py::test_course_linked_session_opens` (asserts both titles) | PASS |
| Server-side greeting/template layer | Composed in the domain layer; the client receives only rendered text | `uc01/domain/greeting.py`, `uc01/api/schemas.py` (`GreetingOut`) | `test_security.py::test_session_response_does_not_expose_prompt_identifiers` | PASS |
| Client cannot control system prompts or guardrails | No prompt field exists; `extra="forbid"`; registry has no mutation API | `uc01/domain/prompts.py`, `uc01/api/schemas.py` | `test_security.py::test_client_cannot_send_a_system_prompt`, `::test_prompt_registry_has_no_mutation_api` | PASS |
| Implemented locally, no dependency on a future AI service | Deterministic templates; a future generator would implement `GreetingGenerator` | `uc01/domain/greeting.py`, `uc01/contracts/services.py` | `test_adapter_replacement.py::test_adapters_satisfy_their_contract` (greeting generator included) | PASS |

## 6. Greeting data loading

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Clean internal `SessionContext` with user, mode, course, lesson, case_file, naric_level, availability/fallback metadata | `SessionContext` dataclass with exactly those members plus `dependencies` and `downgraded_from` | `uc01/domain/models.py` | `test_domain.py::test_session_context_linked_resource_mapping` | PASS |
| No external response structures copied in | Domain types are hand-defined; adapters normalise into them; `test_architecture` forbids adapter imports in domain/application | `uc01/domain/models.py`, `uc01/adapters/mock/*` | `test_architecture.py::test_domain_imports_nothing_but_domain`, `test_adapter_replacement.py::test_the_same_service_code_runs_against_every_adapter_family` | PASS |
| Normalize all external/mocked data through adapters | Each mock builds an imitation upstream payload then maps it | `uc01/adapters/mock/naric.py`, `courses.py`, `cases.py`, `profile.py` | `test_mock_adapters.py::test_naric_success_scenario` (string→int), `::test_profile_available` (first+last→name) | PASS |

## 7. Session record creation

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Every attempt creates/logs a session record | Record inserted before any dependency call | `session_service._create_initial_record` | `test_session_logging.py::test_record_is_created_before_any_dependency_is_contacted`, `::test_dependency_failure_still_creates_a_session_record` | PASS |
| Record includes session_id, user_id, session_type, linked_resource, timestamp, naric_level | All six are columns; `SessionRecord.timestamp` aliases `created_at` | `uc01/persistence/migrations/001_init.sql`, `uc01/domain/models.py` | `test_session_logging.py::test_normal_session_is_logged_with_every_required_field`, `test_persistence.py::test_migrations_create_the_expected_schema` | PASS |
| Self-contained persistence layer | SQLite + `.sql` migrations, no external service | `uc01/persistence/*` | `test_persistence.py` (18 tests) | PASS |
| Clean repository interface for later replacement | `SessionRepository` Protocol, two implementations | `uc01/contracts/repository.py` | `test_persistence.py::test_repositories_are_interchangeable_for_the_service` | PASS |
| Not assumed to be the final company database | Documented explicitly; replacement procedure given | `docs/PERSISTENCE.md` | — (documentation) | PASS |

## 8. Partial / failed session logging

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Record still created on partial/errored initialisation | Record-first + `_mark_failed` in both the `Uc01Error` and unexpected-exception paths | `session_service.open_session`, `_mark_failed` | `test_session_logging.py::test_dependency_failure_still_creates_a_session_record`, `::test_rejected_selection_is_also_recorded` | PASS |
| Session not lost when a dependency fails | Failure is caught per dependency; the record is updated, never deleted | `session_service._load_*`, `_note_dependency` | `test_session_logging.py::test_partial_session_is_logged_as_degraded_with_the_failing_dependency`; `VERIFICATION.md` §8 | PASS |
| User can continue where possible | 409 carries `recovery.available_modes`; `fallback_free_form` opens a degraded free-form session | `session_service._handle_unavailable_mode`, `uc01/api/errors.py` | `test_courses.py::test_course_linked_open_is_rejected_when_courses_unavailable`, `::test_course_linked_can_fall_back_to_free_form_when_requested` | PASS |
| Enough information to diagnose | `diagnostics_json` holds the requested selection, per-dependency state + technical detail, and failure context; plus `session_events` | `session_service._note_dependency`, `_mark_failed` | `test_session_logging.py::test_partial_session_is_logged_as_degraded_with_the_failing_dependency`; `VERIFICATION.md` §8 | PASS |
| Appropriate status/state field | `status` column | `001_init.sql` | `test_session_logging.py::test_every_status_in_the_model_is_reachable` | PASS |
| Status model: initializing / active / degraded / failed | `SessionStatus` enum, all four used | `uc01/domain/enums.py`, `session_service` | `test_session_logging.py::test_every_status_in_the_model_is_reachable` (active/degraded/failed produced by real flows; `initializing` written first by `_create_initial_record`) | PASS |
| Smallest practical persistence design | Two tables | `001_init.sql` | `test_persistence.py::test_migrations_create_the_expected_schema` | PASS |

## 9. NARIC fallback

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Isolated NARIC adapter interface + mock | `NaricService` + `MockNaricAdapter` | `uc01/contracts/services.py`, `uc01/adapters/mock/naric.py` | `test_mock_adapters.py` (6 NARIC tests) | PASS |
| Works when NARIC is available | Level and `source=naric` | `uc01/domain/policy.py` | `test_naric.py::test_session_opens_with_a_real_naric_level` | PASS |
| …unavailable | `ContractError` → Level 5 default | `session_service._load_naric` | `test_naric.py::test_naric_failure_never_blocks_session_creation[unavailable]` | PASS |
| …incomplete | `INCOMPLETE` state → Level 5 default | `uc01/domain/policy.py` | `test_naric.py::test_incomplete_or_calibrating_falls_back_to_level_five` | PASS |
| …invalid | `InvalidUpstreamResponseError` → Level 5 default; a `COMPLETED` payload with an unusable level is also refused | `uc01/adapters/mock/naric.py`, `policy.py` | `test_naric.py::test_naric_failure_never_blocks_session_creation[invalid]`, `::test_complete_assessment_with_an_unusable_level_is_not_trusted` | PASS |
| …still being calibrated | `CALIBRATING` state with its own notice | `policy.py`, `messages.py` | `test_naric.py::test_bob_calibrating_state_is_reported_as_a_fallback` | PASS |
| Incomplete NARIC does not block session creation | NARIC never affects mode availability or the open decision | `policy.evaluate_mode_availability` (no NARIC input) | `test_domain.py::test_naric_state_never_affects_mode_availability`, `test_naric.py::test_calibration_is_never_required_before_coaching` | PASS |
| Offer "continue without calibration" | `naric.offer_continue_without_calibration` + notice with `action` | `policy.resolve_naric_level`, `dto.Notice` | `test_naric.py::test_bootstrap_offers_continue_without_calibration` | PASS |
| Level 5 default | `DEFAULT_EXPLANATION_LEVEL = 5` | `uc01/domain/models.py` | `test_naric.py::test_unavailable_naric_falls_back_to_level_five` | PASS |
| Fallback is clearly tracked | `naric_level_source` = `default` / `default_user_acknowledged`; `is_fallback` in responses | `enums.py`, `schemas.NaricOut` | `test_naric.py::test_naric_fallback_is_labelled_as_a_default_not_as_naric` | PASS |
| Never pretend Level 5 came from NARIC | `source=naric` only from a usable assessment; the greeting says "by default" | `policy.py`, `greeting._level_sentence` | `test_naric.py::test_continue_without_calibration_cannot_fake_a_naric_source`, `test_domain.py::test_defaulted_level_is_never_attributed_to_naric` | PASS |

## 10. Continue without calibration

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Clear option offered | `offer_continue_without_calibration` + `action: "continue_without_calibration"`; checkbox in the UI | `policy.py`, `dto.py`, `index.html` | `test_api_contract.py::test_ui_state_naric_unavailable_does_not_disable_the_session` | PASS |
| Selecting it still opens the session | `continue_without_calibration: true` → 201 | `session_service`, `schemas.OpenSessionRequest` | `test_naric.py::test_continue_without_calibration_opens_the_session` | PASS |
| Session uses Level 5 | Same fallback path | `policy._fallback` | same test (`naric_level == 5`) | PASS |
| Calibration never forced before coaching | No code path requires a NARIC level | `session_service._initialise` | `test_naric.py::test_calibration_is_never_required_before_coaching` | PASS |

## 11. Courses Agent failure handling

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Treated as an external dependency | Behind `CoursesService` | `uc01/contracts/services.py` | `test_adapter_replacement.py` | PASS |
| Does not crash UC-01 | `ContractError` caught in `_load_courses` | `session_service.py` | `test_courses.py::test_courses_unavailable_returns_200_with_a_reason` | PASS |
| Does not prevent free-form | Free-form never loads courses | `session_service._initialise` | `test_session_modes.py::test_case_linked_and_free_form_survive_a_courses_outage` | PASS |
| Does not prevent case-linked when case access exists | Independent dependency loads | same | same test | PASS |
| Disables course-linked | `DependencyState.UNAVAILABLE` → mode disabled | `policy.py` | `test_courses.py::test_course_linked_mode_disabled_when_courses_unavailable` | PASS |
| Clear courses-unavailable message | "Courses are temporarily unavailable." | `messages.py` | `test_api_contract.py::test_ui_state_courses_unavailable` | PASS |
| Rest of the interface preserved | Bootstrap returns 200 with everything else | `load_bootstrap` | `test_cases.py::test_case_outage_does_not_break_the_rest_of_the_interface` (mirror case), `VERIFICATION.md` §3, §6 | PASS |
| No raw stack traces or internal messages to the user | Safe envelope only; banned-substring assertions | `uc01/api/errors.py`, `messages.py` | `test_security.py::test_error_responses_carry_no_technical_detail` | PASS |
| Technical details logged server-side | `logger.warning("dependency.courses.failed", …)` with detail | `session_service._load_courses` | `test_logging.py::test_dependency_failure_is_logged_with_detail_but_not_returned` | PASS |
| Mock supports success and unavailable | `CoursesScenario.AVAILABLE` / `UNAVAILABLE` (plus `EMPTY`, `INVALID`) | `adapters/mock/scenarios.py` | `test_mock_adapters.py` (4 scenario tests) | PASS |

## 12. Profile load failure

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Handle profile/personalisation load failure | `_load_profile` catches `ContractError` | `session_service.py` | `test_profile.py::test_profile_unavailable_still_opens_the_session_with_a_generic_greeting` | PASS |
| Isolated Profile adapter + mock | `ProfileService` + `MockProfileAdapter` | `contracts/services.py`, `adapters/mock/profile.py` | `test_mock_adapters.py` (4 profile tests) | PASS |
| Session still opens | 201 with `status=degraded` | `session_service.py` | `test_profile.py::test_profile_failure_is_not_fatal_for_any_mode` (all three modes) | PASS |
| Generic greeting used | Name omitted; `personalised: false` | `greeting.py` | `test_profile.py::test_profile_unavailable_still_opens_the_session_with_a_generic_greeting` | PASS |
| Clear, non-technical notice | Exact wording from the brief | `messages.PROFILE_UNAVAILABLE_NOTICE` | `test_profile.py::test_profile_failure_produces_a_clear_non_technical_notice` | PASS |
| Internal errors not exposed | Banned-substring assertion on the payload | `api/errors.py`, `schemas.DependencyOut` | same test | PASS |
| Never invent a name or course | `display_name` stays `None`; greeting omits it | `adapters/mock/profile.py`, `greeting.py` | `test_profile.py::test_profile_failure_never_invents_a_name_or_course`, `::test_incomplete_profile_is_not_an_error_and_does_not_invent_a_name` | PASS |

## 13. Generic greeting fallback

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Generic greeting when personalisation is unavailable | `_opening_sentence(None)` → "Hi! Welcome back…" | `greeting.py` | `test_domain.py::test_generic_greeting_when_profile_is_missing` | PASS |
| Still a usable session | 201, session open, context preserved | `session_service.py` | `test_profile.py::test_generic_greeting_still_references_the_course_when_profile_fails` | PASS |
| Profile failure not fatal | Only `degraded`, never `failed` | `session_service._initialise` | `test_profile.py::test_profile_failure_is_not_fatal_for_any_mode` | PASS |

## Integration architecture

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Clear internal interfaces for external dependencies | `NaricService`, `CoursesService`, `CaseFileService`, `ProfileService` (+ `UserContextProvider`, `GreetingGenerator`, `SessionRepository`) | `uc01/contracts/` | `test_adapter_replacement.py::test_adapters_satisfy_their_contract` | PASS |
| Interface → Adapter → Mock for each | Three-layer shape for all four | `contracts/services.py`, `adapters/mock/*`, `adapters/real/` | `test_mock_adapters.py`, `test_adapter_replacement.py` | PASS |
| No external API logic in controllers, routes, UI or session logic | Import-graph tests + a source scan of the service module | `test_architecture.py`, `test_adapter_replacement.py` | `test_architecture.py::test_only_the_container_knows_which_adapter_is_used`, `test_adapter_replacement.py::test_swapping_an_adapter_requires_no_change_to_the_service_module` | PASS |
| Obvious where real integrations go | `adapters/real/` with instructions + template; `>>> register here <<<` markers; failure message names the file | `adapters/real/__init__.py`, `real/template.py`, `api/container.py` | `test_adapter_replacement.py::test_container_reports_a_clear_error_for_an_unimplemented_real_adapter` | PASS |

## Mock requirements

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| NARIC: successful, incomplete, unavailable, invalid (+ calibrating, per-user) | `NaricScenario` | `adapters/mock/scenarios.py`, `naric.py` | `test_mock_adapters.py::test_naric_*` (6) | PASS |
| Courses: available, empty, unavailable, course with lessons, invalid/missing lesson, inaccessible course/lesson | `CoursesScenario` + fixture catalogue (incl. a lessonless course and another user's course) | `scenarios.py`, `courses.py`, `fixtures.py` | `test_mock_adapters.py::test_courses_*` (7), `test_courses.py` (13) | PASS |
| Cases: available, none accessible, service unavailable, inaccessible case | `CaseScenario` + per-user authorisation | `scenarios.py`, `cases.py`, `fixtures.py` | `test_mock_adapters.py::test_case_*` (5), `test_cases.py` (9) | PASS |
| Profile: available, unavailable, incomplete | `ProfileScenario` + a fixture user with no name | `scenarios.py`, `profile.py` | `test_mock_adapters.py::test_profile_*` (4) | PASS |
| Mocks replaceable without changing business logic | Same service runs against mock, stub and foreign adapter families | `tests/stubs.py`, `test_adapter_replacement.py` | `test_adapter_replacement.py::test_the_same_service_code_runs_against_every_adapter_family` | PASS |
| Not presented as real; clearly isolated and documented as temporary | Package docstring, `IS_MOCK`, `/healthz` warning, `integrations` block in bootstrap, `docs/MOCKS.md` | `adapters/mock/__init__.py`, `api/deps.py`, `docs/MOCKS.md` | `test_mock_adapters.py::test_mock_package_is_labelled_as_mock`, `test_api_contract.py::test_health_endpoint_declares_that_mocks_are_in_use` | PASS |

## Security requirements

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Validation happens server-side | Mode, selection shape, accessibility, ownership — all re-checked per request | `session_service.py` | `test_security.py` (36 tests) | PASS |
| Client not trusted for user identity | Resolved from headers via `UserContextProvider`; `user_id` in a body → 422 | `api/deps.py`, `adapters/dev_identity.py` | `test_security.py::test_client_cannot_supply_its_own_user_id` | PASS |
| …case access | `get_accessible_case_file` per open | `session_service.py` | `test_security.py::test_user_cannot_use_another_users_case` | PASS |
| …course/lesson access | `get_accessible_course` + lesson membership | `session_service.py` | `test_security.py::test_user_cannot_use_another_users_course`, `test_courses.py::test_lesson_from_a_different_course_is_rejected` | PASS |
| …NARIC level | Not an input; comes from the adapter | `schemas.OpenSessionRequest` | `test_security.py::test_client_cannot_override_server_owned_fields`, `::test_naric_level_always_comes_from_the_adapter` | PASS |
| …session ownership | `UserContext.owns` check; foreign session → 404 | `session_service.get_session` | `test_security.py::test_user_cannot_read_another_users_session`, `::test_unknown_and_foreign_session_ids_are_indistinguishable` | PASS |
| …authorization | No authorization decision is taken from client input | `session_service.py` | `test_security.py` (whole module) | PASS |
| …system prompts / guardrails | Server-only registry, no mutation API, no prompt field | `domain/prompts.py`, `schemas.py` | `test_security.py::test_client_cannot_send_a_system_prompt`, `::test_no_endpoint_leaks_the_system_prompt_body` | PASS |
| Frontend cannot submit arbitrary values accepted blindly | `extra="forbid"` on requests + server-side revalidation | `schemas.py`, `session_service.py` | `test_security.py::test_client_cannot_override_server_owned_fields` (5 cases) | PASS |
| Minimal dev auth behind an interface | `DevHeaderUserContextProvider` implementing `UserContextProvider` | `adapters/dev_identity.py` | `test_adapter_replacement.py::test_dev_identity_provider_satisfies_its_contract` | PASS |
| No hard-coded authorization in the frontend | UI renders server-computed availability only; documented in the file header | `web/static/app.js` | `test_security.py::test_client_cannot_bypass_a_disabled_mode` (server refuses independently) | PASS |

## Server-side prompt layer

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Prompts/templates kept server-side | `_REGISTRY` in `domain/prompts.py`; `body` excluded from `repr` and from every schema | `domain/prompts.py` | `test_security.py::test_no_endpoint_leaks_the_system_prompt_body` (incl. `/openapi.json`) | PASS |
| Privileged prompt content not exposed | `GreetingOut` carries text/variant/personalised only | `api/schemas.py` | `test_security.py::test_session_response_does_not_expose_prompt_identifiers` | PASS |
| Client cannot overwrite system instructions | No field accepts them; registry is read-only | `schemas.py`, `prompts.py` | `test_security.py::test_client_cannot_send_a_system_prompt`, `::test_prompt_registry_has_no_mutation_api` | PASS |
| User-controlled content separated from system instructions | `PromptPayload` renders three segments; external text sanitised into the untrusted segment | `domain/prompts.py`, `domain/greeting.py` | `test_security.py::test_external_content_reaches_the_prompt_only_in_the_untrusted_segment`, `::test_untrusted_text_is_neutralised` | PASS |
| Implemented locally, no dependency on future UC repositories | Pure-Python templates; no cross-repo import anywhere | `domain/greeting.py` | `test_architecture.py::test_domain_imports_nothing_but_domain` | PASS |

## Error handling

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Graceful failure behaviour | Per-dependency degradation + safe envelope | `session_service.py`, `api/errors.py` | `test_courses.py`, `test_cases.py`, `test_naric.py`, `test_profile.py` | PASS |
| Expected external failures handled explicitly | Three specific contract exceptions caught by type | `session_service._load_*`, `_resolve_*` | `test_mock_adapters.py` + the failure tests above | PASS |
| No broad silent exception handling | AST scan bans `except …: pass`; the two broad handlers log with traceback and convert | `test_architecture.py`, `session_service.py`, `api/errors.py` | `test_architecture.py::test_no_silent_exception_handling_anywhere` | PASS |
| Log the real technical error server-side | `logger.warning/exception` with `technical_detail` | `session_service.py`, `api/errors.py` | `test_logging.py::test_dependency_failure_is_logged_with_detail_but_not_returned` | PASS |
| Return a safe user-facing response | `ErrorResponse` with `code` + safe `message` | `api/errors.py`, `domain/messages.py` | `test_security.py::test_error_responses_carry_no_technical_detail` | PASS |
| Preserve session creation when possible | Record-first + degraded status | `session_service.py` | `test_session_logging.py` (5 tests) | PASS |
| Identify degraded functionality internally | `degraded_dependencies` + `diagnostics_json` + events | `session_service._note_dependency` | `test_session_logging.py::test_partial_session_is_logged_as_degraded_with_the_failing_dependency` | PASS |
| User never sees traceback / 500 text / DB exception / API key / stack trace | Banned-substring assertions over six representative failures | `api/errors.py` | `test_security.py::test_error_responses_carry_no_technical_detail` | PASS |
| Developer-only mode for details | `debug` only when `UC01_DEV_MODE` and `UC01_EXPOSE_ERROR_DETAILS` are both true | `config.py`, `api/errors.py` | `test_security.py::test_debug_details_only_appear_in_developer_mode` | PASS |
| Structured logging suitable for later integration | JSON formatter, dotted event names, `extra={"uc01": …}` envelope | `logging_setup.py` | `test_logging.py::test_json_formatter_emits_a_stable_envelope` | PASS |

## Database / event logging

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Minimal standalone persistence and logging design | Two tables + JSON logs | `001_init.sql`, `logging_setup.py` | `test_persistence.py`, `test_logging.py` | PASS |
| Preserve session_id, user_id, session_type, linked_resource, naric_level, timestamp | Columns + `session.opened` event payload | `001_init.sql`, `session_service._initialise` | `test_session_logging.py::test_normal_session_is_logged_with_every_required_field`, `::test_events_record_the_initialisation_lifecycle` | PASS |
| Schema + migration/setup instructions included | `001_init.sql` + migrate CLI + docs | `uc01/persistence/`, `docs/PERSISTENCE.md`, `README.md` | `test_persistence.py::test_migration_cli_reports_status` | PASS |
| Development store limitations documented and isolated behind interfaces | Limitations section; `SessionRepository` boundary | `docs/PERSISTENCE.md`, `contracts/repository.py` | `test_persistence.py::test_repositories_are_interchangeable_for_the_service` | PASS |
| Compatible with future fields (question, topic_tag, explain_differently_count, rating) | Generic `session_events.payload_json` | `001_init.sql` | `test_persistence.py::test_event_payload_supports_future_use_case_fields` | PASS |
| UC-07 / UC-10 not implemented | Only UC-01 event types; no endpoints or logic for them | `domain/enums.py` (`SessionEventType`), `api/routes.py` | `test_api_contract.py::test_the_api_exposes_exactly_the_uc01_endpoints`, `test_architecture.py::test_no_other_use_case_logic_is_present` | PASS |

## API design

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Load session-opening data | `GET /api/v1/session-bootstrap` | `api/routes.py` | `test_api_contract.py::test_bootstrap_response_shape` | PASS |
| Inspect available session modes | `modes[]` in the bootstrap response | `api/schemas.py` | `test_api_contract.py::test_ui_state_*` | PASS |
| Retrieve courses/lessons | `GET /api/v1/courses` (lessons nested) | `api/routes.py` | `test_courses.py::test_courses_available_with_lessons` | PASS |
| Retrieve accessible case files | `GET /api/v1/case-files` | `api/routes.py` | `test_cases.py::test_case_files_available` | PASS |
| Create/open a session | `POST /api/v1/sessions` | `api/routes.py` | `test_session_modes.py` (all three modes) | PASS |
| Handle fallback/degraded initialisation | `on_dependency_failure`, `notices`, `recovery`, `status=degraded` | `schemas.py`, `errors.py`, `session_service.py` | `test_courses.py::test_course_linked_can_fall_back_to_free_form_when_requested` | PASS |
| Endpoints documented | `docs/API.md` + generated OpenAPI at `/docs` | `docs/API.md`, `api/routes.py` | `test_api_contract.py::test_openapi_documents_request_and_response_schemas` | PASS |
| Request and response schemas/types | Pydantic models throughout | `api/schemas.py` | `test_api_contract.py::test_session_response_shape`, `::test_bootstrap_response_shape` | PASS |
| No unnecessary endpoints, none for future UCs | Exactly six + health + dev-only helper | `api/routes.py` | `test_api_contract.py::test_the_api_exposes_exactly_the_uc01_endpoints` | PASS |

## Frontend requirements

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| UI supports the three modes | Radio group with all three | `web/index.html`, `static/app.js` | `test_api_contract.py::test_frontend_is_served_when_enabled` | PASS |
| UI dynamically reflects dependency availability | `renderModes` uses `available` + `reason` from the API | `static/app.js` | API states asserted in `test_api_contract.py::test_ui_state_*`; rendering verified manually | PASS |
| Normal state | All three available | `policy.py` → UI | `VERIFICATION.md` §1 | PASS |
| No case files state | Case-linked disabled + "No accessible case files." | `messages.py` → UI | `VERIFICATION.md` §2 | PASS |
| Courses unavailable state | Course-linked disabled + "Courses are temporarily unavailable." | `messages.py` → UI | `VERIFICATION.md` §3 | PASS |
| NARIC unavailable: do not disable, offer continue | NARIC block with the Level 5 explanation and the continue control | `index.html`, `static/app.js` | `VERIFICATION.md` §4, §7 | PASS |
| Documented API contract + a way to test all UI states | `docs/API.md`, `scripts/verify_states.py`, dev scenario panel | `docs/API.md`, `scripts/verify_states.py` | `VERIFICATION.md` (recorded run) | PASS |

## UX requirements

| Requirement | Implementation | File(s) | Test | Status |
| --- | --- | --- | --- | --- |
| Clear state | Per-mode `Available`/`Disabled` badge, session facts list, notices with severity | `static/app.js`, `styles.css` | Manual (`VERIFICATION.md`) | PASS |
| Predictable behaviour | UI renders server state only; availability refreshed after a failed open | `static/app.js` | Manual | PASS |
| Useful error messages | Centralised, non-technical, action-carrying notices | `domain/messages.py`, `dto.Notice` | `test_security.py::test_error_responses_carry_no_technical_detail` | PASS |
| Graceful degradation | One failed dependency disables at most one mode | `domain/policy.py` | `test_session_modes.py::test_free_form_is_available_even_when_every_dependency_fails` | PASS |
| Accessibility | Skip link, `fieldset`/`legend`, radio group, `aria-describedby` reasons, `aria-live` status regions, `aria-busy`, visible focus styles, `prefers-color-scheme` | `index.html`, `styles.css` | Manual review only — **no automated WCAG/axe audit was run** | PARTIAL |
| Loading states | Status text + `aria-busy` during bootstrap; submit button disabled with progress text | `static/app.js` | Manual | PASS |
| Disabled states | Disabled radios with reason text and distinct styling; lessonless course disables the lesson select | `static/app.js`, `styles.css` | `test_courses.py::test_course_without_lessons_cannot_be_opened` (server side) | PASS |
| Retry where appropriate | Bootstrap retry button; `action: "retry"` on recoverable notices | `index.html`, `session_service._build_notices` | `test_profile.py::test_profile_failure_produces_a_clear_non_technical_notice` (asserts the retry action) | PASS |
| A failed dependency never makes the interface unusable | Bootstrap never raises on dependency failure | `session_service.load_bootstrap` | `test_session_modes.py::test_free_form_is_available_even_when_every_dependency_fails` | PASS |

## Testing requirements

| Group | Coverage | Status |
| --- | --- | --- |
| Session modes: free-form / course-linked / case-linked open | `test_session_modes.py` (16) | PASS |
| Courses: available, unavailable, invalid course, invalid lesson, inaccessible course/lesson | `test_courses.py` (13) + `test_mock_adapters.py` | PASS |
| Cases: available, none, unavailable, inaccessible | `test_cases.py` (9) + `test_mock_adapters.py` | PASS |
| NARIC: valid, incomplete, unavailable, invalid, default Level 5, continue without calibration | `test_naric.py` (23) | PASS |
| Profile: available, unavailable, incomplete, generic greeting | `test_profile.py` (8) + `test_domain.py` | PASS |
| Session logging: normal, partial, dependency failure still records, degraded status recorded | `test_session_logging.py` (13) | PASS |
| Security: cross-user session/course/case, NARIC override, disabled-mode bypass, prompt control | `test_security.py` (36) | PASS |
| Adapter replacement: business logic on contracts, mock swap needs no service change | `test_adapter_replacement.py` (16) | PASS |

## Scope rule

| Requirement | Evidence | Status |
| --- | --- | --- |
| UC-01 only; no UC-02..UC-10 business logic | Endpoint set assertion + source scan | PASS |
| Generic interfaces only where UC-01 needs them | Four dependency contracts, all used by UC-01; no speculative Legal Foot Prints adapter (rationale in `INTEGRATION_HANDOFF.md`) | PASS |
| No premature platform build | 81 files, two DB tables, six endpoints, three runtime dependencies | PASS |

---

## Definition of Done

| Item | Status |
| --- | --- |
| Complete standalone repository created | PASS |
| Setup and run instructions included | PASS (`README.md`) |
| All UC-01 requirements implemented | PASS (one PARTIAL: automated accessibility audit) |
| UC-01 works independently | PASS (no external service contacted; `pytest` and `verify_states.py` are hermetic) |
| External dependencies isolated behind adapters/interfaces | PASS (`test_architecture.py`) |
| Missing dependencies do not block development | PASS (mocks + scenarios) |
| Mock implementations exist where required | PASS (4 dependencies, 17 scenarios) |
| Real integrations can replace mocks without rewriting business logic | PASS (`test_adapter_replacement.py`, three adapter families) |
| Free-form works independently | PASS |
| Course-linked works when Courses is available | PASS |
| Course-linked gracefully disables when Courses is unavailable | PASS |
| Case-linked works when cases are available | PASS |
| Case-linked gracefully disables when no accessible cases exist | PASS |
| NARIC failure never blocks session creation | PASS |
| Level 5 fallback applied and identified as fallback | PASS |
| Generic greeting works when profile data fails | PASS |
| Session records created even for partial/errored opens | PASS |
| Server-side validation exists | PASS |
| Server-side prompts/guardrails protected | PASS |
| Tests cover happy and failure paths | PASS (249 tests) |
| No UC-02+ business logic introduced | PASS |
| No fake production API presented as real | PASS (`IS_MOCK`, `/healthz` warning, docs) |
| Ready for later repository-level integration | PASS (`INTEGRATION_HANDOFF.md`) |
| Documentation explains how to replace the mock adapters | PASS (`ADAPTER_REPLACEMENT.md`) |

## Not done

| Item | Why | Impact |
| --- | --- | --- |
| Automated accessibility audit | No axe/pa11y in the environment; a11y was implemented and reviewed by hand | The markup uses semantic landmarks, labelled controls, `aria-live`, `aria-describedby` and focus styles, but no WCAG conformance level is claimed |
| Real integrations | The real APIs do not exist yet — the premise of the task | Mocks are labelled everywhere; the swap procedure and template are in place |
| Browser-automation tests for the UI | Would require a headless browser dependency | UI states are asserted at the API level and verified manually; `scripts/verify_states.py` makes that repeatable |
| Multi-instance persistence | SQLite chosen deliberately for a standalone project | Documented in `PERSISTENCE.md`; swap via `SessionRepository` |
