"""Mock Courses Agent provider. Covers every scenario in the UC-02 scope section 13."""

from __future__ import annotations

from enum import Enum

from uc02.domain.errors import ProviderInvalidResponse, ProviderUnavailable
from uc02.domain.models.enums import SourceName
from uc02.domain.models.provider_records import CourseEnrolmentRecord, CoursesRecord
from uc02.domain.ports.providers import CoursesProvider
from uc02.infrastructure.providers.mocks.base import RecordingMock


class CoursesScenario(str, Enum):
    SINGLE_ENROLMENT = "single_enrolment"
    MULTIPLE_ENROLMENTS = "multiple_enrolments"
    EMPTY = "empty"
    PARTIAL_MISSING_LESSON = "partial_missing_lesson"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"


_SINGLE = CourseEnrolmentRecord(
    course_id="course-contract-law-101",
    course_name="Contract Law Foundations",
    completion_percentage=42.5,
    last_accessed_lesson_id="lesson-004",
    last_accessed_lesson_name="Offer and Acceptance",
)

_SECOND = CourseEnrolmentRecord(
    course_id="course-tort-201",
    course_name="Tort Law in Practice",
    completion_percentage=100.0,
    last_accessed_lesson_id="lesson-012",
    last_accessed_lesson_name="Remoteness of Damage",
)

_THIRD = CourseEnrolmentRecord(
    course_id="course-evidence-310",
    course_name="Evidence and Procedure",
    completion_percentage=0.0,
    last_accessed_lesson_id="lesson-001",
    last_accessed_lesson_name="Burden and Standard of Proof",
)

#: A learner enrolled but with no lesson opened yet: last-accessed fields absent.
_PARTIAL = CourseEnrolmentRecord(
    course_id="course-land-150",
    course_name="Land Law",
    completion_percentage=5.0,
    last_accessed_lesson_id=None,
    last_accessed_lesson_name=None,
)


class MockCoursesProvider(RecordingMock[CoursesScenario], CoursesProvider):
    def __init__(
        self,
        default_scenario: CoursesScenario = CoursesScenario.MULTIPLE_ENROLMENTS,
        overrides: dict[str, CoursesScenario] | None = None,
    ) -> None:
        super().__init__(default_scenario, overrides)

    async def get_learning_context(self, user_id: str) -> CoursesRecord:
        scenario = self._record(user_id)
        if scenario is CoursesScenario.TIMEOUT:
            await self._hang()
        if scenario is CoursesScenario.UNAVAILABLE:
            raise ProviderUnavailable(SourceName.COURSES, "mock: Courses Agent returned 503")
        if scenario is CoursesScenario.INVALID_RESPONSE:
            raise ProviderInvalidResponse(
                SourceName.COURSES, "mock: enrolments field was not a list"
            )
        if scenario is CoursesScenario.EMPTY:
            return CoursesRecord(enrolments=())
        if scenario is CoursesScenario.SINGLE_ENROLMENT:
            return CoursesRecord(enrolments=(_SINGLE,))
        if scenario is CoursesScenario.PARTIAL_MISSING_LESSON:
            return CoursesRecord(enrolments=(_SINGLE, _PARTIAL))
        return CoursesRecord(enrolments=(_SINGLE, _SECOND, _THIRD))
