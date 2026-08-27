"""Hand-written stub adapters used by the service-level tests.

These are intentionally **not** the mock adapters from ``uc01/adapters/mock``. They are a
second, independent set of implementations of the same contracts, with different internal
data and no shared fixtures. If the UC-01 service can run against both without change,
the adapter boundary is real — which is exactly what ``test_adapter_replacement.py``
asserts.
"""

from __future__ import annotations

from collections.abc import Sequence

from uc01.contracts.exceptions import (
    DependencyUnavailableError,
    InvalidUpstreamResponseError,
    ResourceNotAccessibleError,
)
from uc01.domain.enums import NaricAssessmentState
from uc01.domain.models import (
    CaseFile,
    Course,
    Lesson,
    NaricAssessment,
    UserContext,
    UserProfile,
)

STUB_COURSE = Course(
    course_id="stub_course_1",
    title="Stub Course One",
    lessons=(
        Lesson(lesson_id="stub_lesson_1", course_id="stub_course_1", title="Stub Lesson One", ordinal=1),
        Lesson(lesson_id="stub_lesson_2", course_id="stub_course_1", title="Stub Lesson Two", ordinal=2),
    ),
)

STUB_CASE = CaseFile(case_id="stub_case_1", title="Stub Case One", matter_reference="SC-1")


class StubNaricService:
    """Configurable NARIC stub."""

    def __init__(
        self,
        *,
        level: int | None = 7,
        state: NaricAssessmentState = NaricAssessmentState.COMPLETE,
        unavailable: bool = False,
        invalid: bool = False,
    ) -> None:
        self._level = level
        self._state = state
        self._unavailable = unavailable
        self._invalid = invalid
        self.calls = 0

    def get_assessment(self, user: UserContext) -> NaricAssessment:
        self.calls += 1
        if self._unavailable:
            raise DependencyUnavailableError("naric", technical_detail="stub outage")
        if self._invalid:
            raise InvalidUpstreamResponseError("naric", technical_detail="stub bad payload")
        return NaricAssessment(state=self._state, level=self._level)


class StubCoursesService:
    def __init__(
        self,
        *,
        courses: Sequence[Course] | None = None,
        unavailable: bool = False,
        accessible_to: Sequence[str] = (),
    ) -> None:
        self._courses = tuple(courses) if courses is not None else (STUB_COURSE,)
        self._unavailable = unavailable
        self._accessible_to = tuple(accessible_to)
        self.calls = 0

    def list_accessible_courses(self, user: UserContext) -> Sequence[Course]:
        self.calls += 1
        if self._unavailable:
            raise DependencyUnavailableError("courses", technical_detail="stub outage")
        if self._accessible_to and user.user_id not in self._accessible_to:
            return ()
        return self._courses

    def get_accessible_course(self, user: UserContext, course_id: str) -> Course:
        for course in self.list_accessible_courses(user):
            if course.course_id == course_id:
                return course
        raise ResourceNotAccessibleError(
            "courses", resource_id=course_id, technical_detail="stub: not accessible"
        )


class StubCaseFileService:
    def __init__(
        self,
        *,
        case_files: Sequence[CaseFile] | None = None,
        unavailable: bool = False,
    ) -> None:
        self._case_files = tuple(case_files) if case_files is not None else (STUB_CASE,)
        self._unavailable = unavailable
        self.calls = 0

    def list_accessible_case_files(self, user: UserContext) -> Sequence[CaseFile]:
        self.calls += 1
        if self._unavailable:
            raise DependencyUnavailableError("cases", technical_detail="stub outage")
        return self._case_files

    def get_accessible_case_file(self, user: UserContext, case_id: str) -> CaseFile:
        for case_file in self.list_accessible_case_files(user):
            if case_file.case_id == case_id:
                return case_file
        raise ResourceNotAccessibleError(
            "cases", resource_id=case_id, technical_detail="stub: not accessible"
        )


class StubProfileService:
    def __init__(
        self,
        *,
        display_name: str | None = "Stub Learner",
        unavailable: bool = False,
    ) -> None:
        self._display_name = display_name
        self._unavailable = unavailable
        self.calls = 0

    def get_profile(self, user: UserContext) -> UserProfile:
        self.calls += 1
        if self._unavailable:
            raise DependencyUnavailableError("profile", technical_detail="stub outage")
        return UserProfile(user_id=user.user_id, display_name=self._display_name)


class ExplodingGreetingGenerator:
    """Used to prove a greeting failure cannot break a session."""

    def generate(self, context):  # noqa: ANN001, ANN201 - test double
        raise RuntimeError("greeting exploded")


__all__ = [
    "ExplodingGreetingGenerator",
    "STUB_CASE",
    "STUB_COURSE",
    "StubCaseFileService",
    "StubCoursesService",
    "StubNaricService",
    "StubProfileService",
]
