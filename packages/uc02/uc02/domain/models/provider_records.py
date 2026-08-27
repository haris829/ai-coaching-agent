"""Records returned by the four upstream ports.

These are *our* contracts, not the company's. Every field here is an assumption
recorded in docs/assumptions.md. A real adapter's job is to translate the
company's payload into these records; the assembly service never sees a raw
upstream payload.

Records are deliberately permissive (levels and lessons may be missing) because
"the source answered but had nothing for this learner" is a normal outcome that
must be distinguished from "the source is down".
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NaricRecord(_Record):
    """A NARIC qualification lookup result.

    ``level`` is ``None`` when NARIC answered but holds no qualification for the
    learner (assumption A-01/A-02: numeric level plus a human label).
    """

    level: int | None = None
    raw_level_label: str | None = None


class CourseEnrolmentRecord(_Record):
    """One course enrolment as reported by the Courses Agent.

    ``completion_percentage`` is normalised to 0-100 by the adapter
    (assumption A-06). Last-accessed lesson fields are optional because a
    freshly enrolled learner has not opened a lesson yet (assumption A-08).
    """

    course_id: str
    course_name: str
    completion_percentage: float = Field(ge=0.0, le=100.0)
    last_accessed_lesson_id: str | None = None
    last_accessed_lesson_name: str | None = None


class CoursesRecord(_Record):
    enrolments: tuple[CourseEnrolmentRecord, ...] = ()


class LegalProfileRecord(_Record):
    """A Legal Foot Prints profile.

    Speciality areas and case-type preferences are lists (assumption A-10);
    ``practice_area`` is a single optional value (assumption A-11).
    """

    speciality_areas: tuple[str, ...] = ()
    case_type_preferences: tuple[str, ...] = ()
    practice_area: str | None = None


class QuestionRecord(_Record):
    """One previously asked question, across any prior session of this learner.

    ``text`` is the only field in UC-02 that carries learner-authored prose. It
    never reaches a log line or an API response (see docs/integration.md §privacy).
    """

    question_id: str
    session_id: str
    asked_at: datetime
    topic_tag: str | None = None
    text: str = ""
