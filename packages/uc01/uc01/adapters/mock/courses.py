"""MOCK Courses Agent adapter — development only.

Implements :class:`uc01.contracts.services.CoursesService`.

The authorization check (``enrolledLearners`` contains the caller) lives here on purpose:
it is the adapter's job to ask the upstream system "may this user open this course?".
UC-01 business logic only ever sees an accessible ``Course`` or a
``ResourceNotAccessibleError``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from ...contracts.exceptions import (
    DependencyUnavailableError,
    InvalidUpstreamResponseError,
    ResourceNotAccessibleError,
)
from ...domain.models import Course, Lesson, UserContext
from . import fixtures
from .scenarios import CoursesScenario

logger = logging.getLogger(__name__)

DEPENDENCY = "courses"


class MockCoursesAdapter:
    """Fixture-backed Courses Agent."""

    def __init__(self, scenario: CoursesScenario = CoursesScenario.AVAILABLE) -> None:
        self._scenario = scenario

    # -- contract ----------------------------------------------------------- #

    def list_accessible_courses(self, user: UserContext) -> Sequence[Course]:
        payloads = self._fetch_catalogue(user)
        return tuple(self._normalise_course(item) for item in payloads)

    def get_accessible_course(self, user: UserContext, course_id: str) -> Course:
        for course in self.list_accessible_courses(user):
            if course.course_id == course_id:
                return course
        # Unknown id and other-user's id are indistinguishable to the caller by design.
        logger.info(
            "courses.access_denied",
            extra={"uc01": {"dependency": DEPENDENCY, "course_id": course_id[:64]}},
        )
        raise ResourceNotAccessibleError(
            DEPENDENCY,
            resource_id=course_id,
            technical_detail="course not in the caller's accessible catalogue",
        )

    # -- "transport" -------------------------------------------------------- #

    def _fetch_catalogue(self, user: UserContext) -> Sequence[Mapping[str, Any]]:
        scenario = self._scenario
        if scenario is CoursesScenario.UNAVAILABLE:
            raise DependencyUnavailableError(
                DEPENDENCY,
                technical_detail="mock: simulated Courses Agent timeout after 5000ms",
            )
        if scenario is CoursesScenario.INVALID:
            return self._normalise_catalogue_envelope(fixtures.COURSES_INVALID_PAYLOAD)
        if scenario is CoursesScenario.EMPTY:
            return ()
        return tuple(
            item
            for item in fixtures.COURSE_CATALOGUE
            if user.user_id in item.get("enrolledLearners", ())
        )

    @staticmethod
    def _normalise_catalogue_envelope(payload: Any) -> Sequence[Mapping[str, Any]]:
        courses = payload.get("courses") if isinstance(payload, Mapping) else None
        if not isinstance(courses, list):
            raise InvalidUpstreamResponseError(
                DEPENDENCY,
                technical_detail=f"courses envelope was {type(courses).__name__}, expected list",
            )
        return courses

    # -- normalisation ------------------------------------------------------ #

    def _normalise_course(self, payload: Mapping[str, Any]) -> Course:
        course_id = payload.get("courseId")
        title = payload.get("courseTitle")
        if not isinstance(course_id, str) or not course_id:
            raise InvalidUpstreamResponseError(
                DEPENDENCY, technical_detail="course entry without a usable courseId"
            )
        modules = payload.get("modules") or []
        if not isinstance(modules, list):
            raise InvalidUpstreamResponseError(
                DEPENDENCY, technical_detail="modules was not a list"
            )
        lessons = tuple(
            self._normalise_lesson(course_id, module)
            for module in modules
            if isinstance(module, Mapping)
        )
        return Course(
            course_id=course_id,
            title=title if isinstance(title, str) and title else course_id,
            lessons=tuple(sorted(lessons, key=lambda lesson: lesson.ordinal)),
        )

    @staticmethod
    def _normalise_lesson(course_id: str, payload: Mapping[str, Any]) -> Lesson:
        lesson_id = payload.get("lessonId")
        if not isinstance(lesson_id, str) or not lesson_id:
            raise InvalidUpstreamResponseError(
                DEPENDENCY, technical_detail="lesson entry without a usable lessonId"
            )
        title = payload.get("lessonTitle")
        seq = payload.get("seq")
        return Lesson(
            lesson_id=lesson_id,
            course_id=course_id,
            title=title if isinstance(title, str) and title else lesson_id,
            ordinal=seq if isinstance(seq, int) and not isinstance(seq, bool) else 0,
        )


__all__ = ["MockCoursesAdapter"]
