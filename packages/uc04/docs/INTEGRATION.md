# UC-04 — Integration runbook

For an engineer who has never opened this codebase.

**The rule this repository is built to keep:** integrating a real upstream system costs

1. **one new adapter file** (the payload mapping — only you know your shape),
2. **one line** in the provider registry,
3. **one environment variable**.

Nothing else changes. No edits to domain models, application services, the API layer, existing
adapters, persistence, or any existing test. If your integration needs a second file beyond those
three points, that is an architecture defect here — raise it rather than working around it.

§7 below is a worked example that was actually performed, with the literal diff.

---

## 0. Before you write anything

Read `docs/assumptions.md` and check these rows against the real system. They change what your
adapter has to do:

| Check first | Why it matters |
| --- | --- |
| **A-08 — does the Courses Agent expose lesson quiz items?** | Known-item matching is one of the two independent quiz-protection signals. Without items, detection rests on intent classification alone and `quiz_detection_confirmed` is always `null`. **The most consequential unknown in this component.** |
| **A-16 — does lesson content carry curated key points and one-sentence concept definitions?** | They are the only material UC-04 may quote. Without them every answer becomes "the lesson does not set this out in enough depth". |
| **A-17 — does `get_course_structure` return every lesson in the course?** | It is the whitelist cross-lesson references are verified against. An incomplete list silently strips real references. |
| **A-06/A-07 — what is the real concept/topic taxonomy?** | Ours is invented. Until yours replaces it, most questions tag `unclassified`. |
| **A-11 — are levels 4 and 6 grouped as we assumed?** | Level 6 maps to `intermediate`, not `advanced`. Confirm that is what the company means. |
| **A-19 — should an unverifiable enrolment refuse outright?** | We answer from general knowledge with a notice. Only a definite "not enrolled" is refused. |
| **A-15 — is the redaction of `question_text` acceptable?** | We never persist the learner's words. If tuning needs them, that is a data-protection decision. |

---

## 1. Per-dependency reference

Every row: create one file, implement one protocol, add one registry line, set one variable, run
one command.

### CoursesProvider — the Courses Agent

| | |
| --- | --- |
| **Create** | `src/uc04/adapters/real/company_courses.py` — copy `src/uc04/adapters/real/_template.py` |
| **Implement** | `uc04.ports.CoursesProvider`:<br>`get_lesson(course_id: str, lesson_id: str) -> LessonContent`<br>`get_course_structure(course_id: str) -> CourseStructure`<br>`verify_enrolment(user_id: str, course_id: str) -> EnrolmentRecord` |
| **Register** | `COURSES_PROVIDERS["company"] = "uc04.adapters.real.company_courses:CompanyCoursesAdapter"` in `src/uc04/adapters/registry.py` |
| **Configure** | `COURSES_PROVIDER=company` |
| **Verify** | `pytest tests/test_conformance_kit.py -k CompanyCourses` |
| **Check first** | A-08, A-16, A-17 |

### LearnerContextProvider — the context service

| | |
| --- | --- |
| **Create** | `src/uc04/adapters/real/company_context.py` |
| **Implement** | `get_context(session_id: str, user_id: str) -> LearnerContext` |
| **Register** | `LEARNER_CONTEXT_PROVIDERS["company"] = "uc04.adapters.real.company_context:CompanyContextAdapter"` |
| **Configure** | `LEARNER_CONTEXT_PROVIDER=company` |
| **Verify** | `pytest tests/test_conformance_kit.py -k CompanyLearnerContext` |
| **Check first** | A-11 |

### AnswerGenerator — the model

| | |
| --- | --- |
| **Create** | Fill in `src/uc04/adapters/generators/configured.py` (the TODO is already marked) or add your own |
| **Implement** | `generate(request: GenerationRequest) -> GenerationResult` |
| **Register** | Already registered as `configured` |
| **Configure** | `ANSWER_GENERATOR=configured`, plus `GENERATION_PROVIDER` / `GENERATION_MODEL` / `GENERATION_API_KEY` |
| **Verify** | `pytest tests/test_conformance_kit.py -k Generator` |
| **Check first** | A-10 — the budget is enforced by the service; your generator receives `quotable_spans` and must use nothing else |

### QuizIntentClassifier — a stronger classifier

| | |
| --- | --- |
| **Create** | `src/uc04/adapters/real/company_quiz_classifier.py` |
| **Implement** | `classify(question: str, lesson: LessonContent \| None) -> QuizIntentResult` |
| **Register** | `QUIZ_CLASSIFIERS["company"] = "uc04.adapters.real.company_quiz_classifier:CompanyQuizClassifier"` |
| **Configure** | `QUIZ_CLASSIFIER=company` |
| **Verify** | `pytest tests/test_conformance_kit.py -k QuizClassifier` |
| **Check first** | A-12. Known-item matching stays in UC-04 core; you are replacing signal 2 only |

### ConceptTagger — the real taxonomy

| | |
| --- | --- |
| **Create** | `src/uc04/adapters/real/company_tagger.py`, and replace `src/uc04/domain/vocabularies.py` with the real taxonomy |
| **Implement** | `tag(question: str, lesson: LessonContent \| None) -> ConceptTag` |
| **Register** | `CONCEPT_TAGGERS["company"] = "uc04.adapters.real.company_tagger:CompanyTagger"` |
| **Configure** | `CONCEPT_TAGGER=company` |
| **Verify** | `pytest tests/test_conformance_kit.py -k ConceptTagger` |
| **Check first** | A-06, A-07. This is the one case that also touches a domain file, because the vocabulary *is* data the company owns |

### InteractionLogRepository — the platform database

| | |
| --- | --- |
| **Create** | `src/uc04/adapters/real/company_interaction_log.py` |
| **Implement** | `append`, `get`, `list_for_session`, `append_false_positive`, `list_false_positives` |
| **Register** | `INTERACTION_LOG_REPOSITORIES["company"] = "uc04.adapters.real.company_interaction_log:CompanyInteractionLog"` |
| **Configure** | `INTERACTION_LOG_REPOSITORY=company` |
| **Verify** | `pytest tests/test_conformance_kit.py -k InteractionLog` |
| **Check first** | A-13, A-15. `rating_state` must round-trip as `pending` unchanged |

### FramingRegistry — session store or cache

| | |
| --- | --- |
| **Create** | `src/uc04/adapters/real/company_framing_registry.py` |
| **Implement** | `used_framings`, `record`, `explain_differently_count` |
| **Register** | `FRAMING_REGISTRIES["company"] = "uc04.adapters.real.company_framing_registry:CompanyFramingRegistry"` |
| **Configure** | `FRAMING_REGISTRY=company` |
| **Verify** | `pytest tests/test_conformance_kit.py -k FramingRegistry` |
| **Check first** | `fingerprint_tokens` must survive the round trip, or paraphrase detection silently stops working |

### CurrentUserProvider — the gateway principal

| | |
| --- | --- |
| **Create** | `src/uc04/adapters/real/company_identity.py` |
| **Implement** | `resolve(headers: dict[str, str]) -> str`, raising `AccessDenied` when absent |
| **Register** | `CURRENT_USER_PROVIDERS["gateway"] = "uc04.adapters.real.company_identity:GatewayIdentity"` |
| **Configure** | `CURRENT_USER_PROVIDER=gateway` |
| **Verify** | `pytest tests/test_api.py -k identity` |
| **Check first** | A-22. Must never read identity from the request body |

---

## 2. The four non-negotiables

1. **The adapter is the only place your payload shape is known.** No upstream field name,
   nesting, or error string may escape past its return statement. The conformance kit checks
   this.
2. **Never invent data.** A missing value maps to the documented default with its source field
   marked accordingly — never to a plausible-looking guess. A NARIC value matching no enum member
   is an *invalid response*: apply `LEVEL_5`, mark the source `default`, record status `invalid`.
3. **Authorisation stays server-side, inside the adapter.** Credentials come from configuration.
   Never accept one from a caller, and never echo one outward.
4. **If the real payload cannot be mapped to the platform contract, that is a contract
   conversation, not an adapter workaround.** Raise it. Do not bend the domain model to fit an
   upstream quirk.

---

## 3. Error translation

Your adapter raises exactly these. Nothing else may cross the boundary.

| Upstream condition | Raise |
| --- | --- |
| Connection refused, 5xx, service reports itself down | `ProviderUnavailable(port, "short generic detail")` |
| Deadline exceeded | `ProviderTimeout(...)` |
| 404 / no such thing | `NotFound(...)` |
| 2xx with an unmappable body | `ProviderInvalidResponse(...)` |

Keep the detail string short and generic. It reaches server-side logs, never a client.

---

## 4. The registry

`src/uc04/adapters/registry.py`. Entries are **dotted import paths**, so adding one needs no
import statement — genuinely one line:

```python
COURSES_PROVIDERS: dict[str, str] = {
    "mock": "uc04.adapters.mock.courses:MockCoursesProvider",
    "company": "uc04.adapters.real.company_courses:CompanyCoursesAdapter",   # <- one line
}
```

A configured name with no registered implementation **fails at startup**, naming the missing key,
what is registered, and the file expected to supply it. There is no silent fallback to a mock: a
service quietly running on fake data in production is worse than one that refuses to start.

---

## 5. Running the conformance kit against your adapter

The kit ships inside the package (`uc04.conformance`). You write no assertions — only which
adapter to test and which identifiers drive each scenario.

```python
# tests/test_company_courses.py
import pytest
from uc04.conformance import CoursesProviderConformance, CoursesScenarios
from uc04.adapters.real.company_courses import CompanyCoursesAdapter

class TestCompanyCourses(CoursesProviderConformance):
    @pytest.fixture
    def adapter(self):
        return CompanyCoursesAdapter()

    @pytest.fixture
    def scenarios(self):
        return CoursesScenarios(
            course_id="CRS-1", lesson_id="LSN-1",
            enrolled_user_id="u-enrolled", unenrolled_user_id="u-outsider",
            unavailable_lesson_id="LSN-DOWN", timeout_lesson_id="LSN-SLOW",
            invalid_lesson_id="LSN-BAD", missing_lesson_id="LSN-NOPE",
            missing_course_id="CRS-NOPE", expects_quiz_items=True,
        )
```

```
pytest tests/test_company_courses.py
```

A scenario left as `None` is skipped, so an upstream that genuinely cannot produce that failure
mode is not penalised. Everything else is mandatory.

Suites available: `CoursesProviderConformance`, `LearnerContextConformance`,
`AnswerGeneratorConformance`, `ConceptTaggerConformance`, `QuizIntentClassifierConformance`,
`InteractionLogConformance`, `FramingRegistryConformance`.

---

## 6. Sanity check after any swap

```
pytest                                        # the whole suite must stay green
pytest tests/test_registry_and_swap.py        # registry behaviour and the foreign-adapter proof
```

`tests/test_registry_and_swap.py::test_core_and_domain_never_import_an_adapter` walks
`src/uc04/core`, `domain` and `ports` and fails if any of them imports an adapter. That is the
structural guarantee behind the swap rule, checked mechanically rather than trusted.

---

## 7. Worked example — a Courses Agent adapter, start to finish

This was performed against this repository. The three changes below are the entire diff.

### 7.1 One new file — `src/uc04/adapters/real/company_courses.py`

Copied from `_template.py`, with the four TODOs filled in. Abridged here; the full file is in the
repository.

```python
class CompanyCoursesAdapter:
    """Implements uc04.ports.CoursesProvider against the company Courses Agent."""

    name = "company_courses"

    def __init__(self, transport: Callable[[str], dict[str, Any]] | None = None) -> None:
        # TODO(1/4) ENDPOINT - from configuration, never hard-coded
        self.base_url = os.environ.get("COMPANY_COURSES_BASE_URL", "")
        # TODO(2/4) AUTH - stays inside this adapter
        self.api_key = os.environ.get("COMPANY_COURSES_API_KEY", "")
        self._transport = transport or _default_transport(self.base_url)

    def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent:
        payload = self._transport(f"/courses/{course_id}/lessons/{lesson_id}")
        # TODO(4/4) MAPPING - guard every access; a KeyError escaping here is a contract breach
        ...
```

The transport (TODO 3/4) is where the HTTP call goes. For the integration rehearsal it reads
recorded staging responses from disk when `COMPANY_COURSES_BASE_URL` uses a `file://` prefix, so
the mapping can be exercised before the endpoint is reachable. A real base URL performs the call.

### 7.2 One registry line — `src/uc04/adapters/registry.py`

```diff
 COURSES_PROVIDERS: dict[str, str] = {
     "mock": "uc04.adapters.mock.courses:MockCoursesProvider",
     "foreign_demo": "uc04.adapters.real.foreign_demo:ForeignCoursesAdapter",
+    "company_courses": "uc04.adapters.real.company_courses:CompanyCoursesAdapter",
 }
```

### 7.3 One config value — `.env`

```diff
-COURSES_PROVIDER=mock
+COURSES_PROVIDER=company_courses
```

### 7.4 Verify

```
COURSES_PROVIDER=company_courses \
COMPANY_COURSES_BASE_URL=file://./tests/fixtures/company_staging \
pytest tests/test_company_courses_swap.py
```

That is the whole integration. No domain model, no service, no API file, no existing adapter and
no existing test was edited — verified by checksum in the swap proof in the final report.
