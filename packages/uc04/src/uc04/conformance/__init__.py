"""Adapter conformance kit.

Point this at your adapter and it tells you, in one command, whether your integration is
correct. You write no tests.

    # tests/test_my_adapter.py
    from uc04.conformance import CoursesProviderConformance, CoursesScenarios

    class TestCompanyCourses(CoursesProviderConformance):
        @pytest.fixture
        def adapter(self):
            return CompanyCoursesAdapter()

        @pytest.fixture
        def scenarios(self):
            return CoursesScenarios(
                course_id="CRS-1", lesson_id="LSN-1",
                unavailable_lesson_id="LSN-DOWN", timeout_lesson_id="LSN-SLOW",
                invalid_lesson_id="LSN-BAD", missing_lesson_id="LSN-NOPE",
                enrolled_user_id="u1", unenrolled_user_id="u2",
            )

    $ pytest tests/test_my_adapter.py

The suites assert the *behavioural contract*, never any particular fixture's data:

* return types are the domain models, fully populated where the contract requires it;
* every documented failure mode raises the correct typed contract exception;
* no upstream payload shape, field name, error string or provider name escapes the boundary;
* values are normalised to the platform contract regardless of what the upstream sent;
* the adapter is deterministic for the same input.

A scenario left as ``None`` is skipped, so an adapter whose upstream genuinely cannot produce
that failure mode is not penalised for it. Everything else is mandatory.
"""

from .courses import CoursesProviderConformance, CoursesScenarios
from .generator import AnswerGeneratorConformance, GeneratorScenarios
from .learner_context import LearnerContextConformance, LearnerContextScenarios
from .repositories import (
    FramingRegistryConformance,
    InteractionLogConformance,
)
from .tagging import (
    ConceptTaggerConformance,
    ConceptTaggerScenarios,
    QuizIntentClassifierConformance,
    QuizClassifierScenarios,
)

__all__ = [
    "AnswerGeneratorConformance",
    "ConceptTaggerConformance",
    "ConceptTaggerScenarios",
    "CoursesProviderConformance",
    "CoursesScenarios",
    "FramingRegistryConformance",
    "GeneratorScenarios",
    "InteractionLogConformance",
    "LearnerContextConformance",
    "LearnerContextScenarios",
    "QuizClassifierScenarios",
    "QuizIntentClassifierConformance",
]
