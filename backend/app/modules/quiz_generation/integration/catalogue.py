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

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.quiz_configuration.models import Course
from app.modules.quiz_generation.domain.generation import CourseBrief


@dataclass(frozen=True, slots=True)
class CourseSummary:
    """A course as it appears in a list to choose from.

    Carries ``has_brief`` rather than the description itself. A picker needs to show *which*
    courses will generate well, and the description can run to thousands of characters — sending
    all of them to render a dropdown would be wasteful, and sending none would hide the one fact
    that matters when choosing.
    """

    code: str
    title: str
    rqf_level: int | None = None
    subject_area: str | None = None
    #: Whether this course has a description to generate from. A course without one is generated
    #: from its title alone, which produces noticeably more generic questions.
    has_brief: bool = False
    #: How many quizzes have already been generated for it, so a chooser can see what is fresh.
    generated_count: int = 0


@runtime_checkable
class CourseLookup(Protocol):
    """A course code in, a brief out — or ``None`` when the code is unknown."""

    def find(self, course_ref: str) -> CourseBrief | None: ...

    def list_all(self, limit: int = 200) -> tuple[CourseSummary, ...]: ...


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

    def list_all(self, limit: int = 200) -> tuple[CourseSummary, ...]:
        """The courses available to generate from, by name and title.

        Ordered by title rather than by code, because a person choosing a course reads the name.
        The generated-quiz count is joined in so a chooser can see at a glance which courses have
        been covered already and which have not.
        """
        # Imported here rather than at module scope: this is the one place the catalogue needs to
        # know that generated quizzes exist, and the count is a convenience for a picker, not part
        # of what a course *is*.
        from app.modules.quiz_generation.models import GeneratedQuiz

        counts = dict(
            self._session.execute(
                select(GeneratedQuiz.course_ref, func.count(GeneratedQuiz.id))
                .where(GeneratedQuiz.course_ref.is_not(None))
                .group_by(GeneratedQuiz.course_ref)
            ).all()
        )
        courses = self._session.scalars(
            select(Course).order_by(Course.title).limit(max(1, limit))
        ).all()
        return tuple(
            CourseSummary(
                code=course.code,
                title=course.title,
                rqf_level=getattr(course, "rqf_level", None),
                subject_area=getattr(course, "subject_area", None),
                has_brief=bool(getattr(course, "description", None)),
                generated_count=int(counts.get(course.code, 0)),
            )
            for course in courses
        )


class NoCatalogue:
    """A lookup that knows nothing, for callers with no database to consult."""

    def find(self, course_ref: str) -> CourseBrief | None:  # noqa: ARG002 - the point is None
        return None

    def list_all(self, limit: int = 200) -> tuple[CourseSummary, ...]:  # noqa: ARG002
        return ()
