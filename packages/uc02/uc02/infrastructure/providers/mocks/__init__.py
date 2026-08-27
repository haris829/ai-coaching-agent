from uc02.infrastructure.providers.mocks.courses import CoursesScenario, MockCoursesProvider
from uc02.infrastructure.providers.mocks.history import (
    HistoryScenario,
    MockQuestionHistoryProvider,
    build_questions,
)
from uc02.infrastructure.providers.mocks.legal import LegalScenario, MockLegalFootprintsProvider
from uc02.infrastructure.providers.mocks.naric import MockNaricProvider, NaricScenario

__all__ = [
    "CoursesScenario",
    "HistoryScenario",
    "LegalScenario",
    "MockCoursesProvider",
    "MockLegalFootprintsProvider",
    "MockNaricProvider",
    "MockQuestionHistoryProvider",
    "NaricScenario",
    "build_questions",
]
