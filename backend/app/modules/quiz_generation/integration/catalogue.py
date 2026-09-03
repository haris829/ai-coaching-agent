"""The catalogue boundary: turning a course code into a brief worth prompting with.

The only file in this module that imports UC-01, per the boundary rule the architecture tests
enforce. Generation knows a ``CourseLookup`` and nothing more about how courses are stored.

WHY THIS IS WORTH A FILE
------------------------
A bare topic string — "Bar Practice Course" — produces generic questions. The same course looked up
in the catalogue carries a description, an RQF level and a module list, and that is the difference
between questions a professional finds trivial and questions pitched at the level they are actually
being certified at. RQF 2 is GCSE-equivalent and RQF 8 is doctoral; passing that one integer through
changes the output more than any prompt wording does.

A missing course is **not** an error. The caller asked for questions about a topic and named a
course as a hint; if the hint does not resolve, the topic still stands. Failing the request would
make the catalogue a hard dependency of generation, which it is not.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.quiz_configuration.models import Course
from app.modules.quiz_generation.domain.generation import CourseBrief


@runtime_checkable
class CourseLookup(Protocol):
    """A course code in, a brief out — or ``None`` when the code is unknown."""

    def find(self, course_ref: str) -> CourseBrief | None: ...


class CatalogueLookup:
    """``CourseLookup`` over UC-01's ``qc_courses``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, course_ref: str) -> CourseBrief | None:
        code = (course_ref or "").strip()
        if not code:
            return None
        course = self._session.scalars(
            select(Course).where(Course.code == code)
        ).one_or_none()
        if course is None:
            return None
        return CourseBrief(
            course_id=course.code,
            name=course.title,
            # Read defensively: the catalogue grew these columns over two imports, and a course row
            # written by the earlier one has the title and nothing else.
            description=getattr(course, "description", None),
            rqf_level=getattr(course, "rqf_level", None),
            subject_area=getattr(course, "subject_area", None),
        )


class NoCatalogue:
    """A lookup that knows nothing, for callers with no database to consult."""

    def find(self, course_ref: str) -> CourseBrief | None:  # noqa: ARG002 - the point is None
        return None
