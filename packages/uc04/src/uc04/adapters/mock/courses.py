"""Mock CoursesProvider.

Every scenario in the brief's mock matrix is triggered deterministically by identifier - no
randomness, no sleeps, no flakiness. A real adapter implements the same protocol and simply has
none of these branches.
"""

from __future__ import annotations

from ...domain.errors import NotFound, ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from ...domain.models import CourseStructure, EnrolmentRecord, LessonContent
from . import fixtures as fx

PORT = "courses"


class MockCoursesProvider:
    name = "mock"

    def __init__(self) -> None:
        #: Records every content call so a test can prove content was not fetched before
        #: enrolment was verified.
        self.lesson_calls: list[tuple[str, str]] = []
        self.enrolment_calls: list[tuple[str, str]] = []

    # ------------------------------------------------------------------------- lessons

    def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent:
        self.lesson_calls.append((course_id, lesson_id))

        if lesson_id == fx.LESSON_UNAVAILABLE:
            raise ProviderUnavailable(PORT, "content service unavailable")
        if lesson_id == fx.LESSON_TIMEOUT:
            raise ProviderTimeout(PORT, "content service timed out")
        if lesson_id == fx.LESSON_INVALID:
            # Models an upstream payload that cannot be mapped onto the domain model.
            raise ProviderInvalidResponse(PORT, "lesson payload could not be mapped")

        lesson = fx.LESSONS.get(lesson_id)
        if lesson is None:
            raise NotFound(PORT, "lesson not found")
        if lesson.course_id != course_id:
            raise NotFound(PORT, "lesson does not belong to this course")
        return lesson

    # ----------------------------------------------------------------------- structure

    def get_course_structure(self, course_id: str) -> CourseStructure:
        if course_id == fx.COURSE_UNAVAILABLE:
            raise ProviderUnavailable(PORT, "catalogue unavailable")
        structure = fx.COURSE_STRUCTURES.get(course_id)
        if structure is None:
            raise NotFound(PORT, "course not found")
        return structure

    # ----------------------------------------------------------------------- enrolment

    def verify_enrolment(self, user_id: str, course_id: str) -> EnrolmentRecord:
        self.enrolment_calls.append((user_id, course_id))

        if user_id == fx.USER_ENROLMENT_DOWN:
            raise ProviderUnavailable(PORT, "enrolment service unavailable")
        if user_id == fx.USER_LAPSED:
            return EnrolmentRecord(user_id=user_id, course_id=course_id, enrolled=False, reason="lapsed")

        courses = fx.ENROLMENTS.get(user_id)
        if courses is None:
            return EnrolmentRecord(user_id=user_id, course_id=course_id, enrolled=False, reason="unknown_user")
        enrolled = course_id in courses
        return EnrolmentRecord(
            user_id=user_id,
            course_id=course_id,
            enrolled=enrolled,
            reason=None if enrolled else "no_active_enrolment",
        )
