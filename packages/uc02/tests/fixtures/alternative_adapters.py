"""Second implementations of the ports, used to prove adapter independence.

These stand in for the real company adapters that do not exist yet. If
``ContextAssemblyService`` needed to know anything about a specific adapter,
substituting these would break it -- which is exactly what
``tests/unit/test_adapter_independence.py`` asserts does not happen.

``FixtureNaricProvider`` subclasses the port. ``DuckTypedCoursesProvider``
deliberately does not, proving the service depends on the call signature rather
than on an inheritance relationship.
"""

from __future__ import annotations

from datetime import datetime, timezone

from uc02.domain.errors import ProviderUnavailable
from uc02.domain.models.enums import SourceName
from uc02.domain.models.provider_records import (
    CourseEnrolmentRecord,
    CoursesRecord,
    LegalProfileRecord,
    NaricRecord,
    QuestionRecord,
)
from uc02.domain.ports.providers import (
    LegalFootprintsProvider,
    NaricProvider,
    QuestionHistoryProvider,
)


class FixtureNaricProvider(NaricProvider):
    """Serves levels from a per-user table, unknown users raise unavailable."""

    def __init__(self, levels: dict[str, int]) -> None:
        self._levels = levels
        self.calls: list[str] = []

    async def get_qualification_level(self, user_id: str) -> NaricRecord:
        self.calls.append(user_id)
        if user_id not in self._levels:
            raise ProviderUnavailable(SourceName.NARIC, "fixture: no row for user")
        level = self._levels[user_id]
        return NaricRecord(level=level, raw_level_label=f"Fixture level {level}")


class DuckTypedCoursesProvider:
    """Implements the Courses port structurally, without inheriting from it."""

    def __init__(self, course_name: str = "Fixture Course") -> None:
        self._course_name = course_name
        self.calls: list[str] = []

    async def get_learning_context(self, user_id: str) -> CoursesRecord:
        self.calls.append(user_id)
        return CoursesRecord(
            enrolments=(
                CourseEnrolmentRecord(
                    course_id="fixture-course",
                    course_name=self._course_name,
                    completion_percentage=12.0,
                    last_accessed_lesson_id="fixture-lesson",
                    last_accessed_lesson_name="Fixture Lesson",
                ),
            )
        )


class FixtureLegalProvider(LegalFootprintsProvider):
    def __init__(self, speciality: str | None = "Family law") -> None:
        self._speciality = speciality
        self.calls: list[str] = []

    async def get_profile(self, user_id: str) -> LegalProfileRecord:
        self.calls.append(user_id)
        if self._speciality is None:
            return LegalProfileRecord()
        return LegalProfileRecord(
            speciality_areas=(self._speciality,),
            case_type_preferences=("Fixture case type",),
            practice_area="Fixture practice",
        )


class FixtureHistoryProvider(QuestionHistoryProvider):
    """Always returns exactly ``count`` records and records the limit it was given."""

    def __init__(self, count: int = 3) -> None:
        self._count = count
        self.calls: list[str] = []
        self.observed_limits: list[int] = []

    async def get_recent_questions(self, user_id: str, limit: int) -> list[QuestionRecord]:
        self.calls.append(user_id)
        self.observed_limits.append(limit)
        base = datetime(2026, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
        return [
            QuestionRecord(
                question_id=f"fixture-q-{i}",
                session_id="fixture-session",
                asked_at=base,
                topic_tag="fixture",
                text="fixture question text",
            )
            for i in range(self._count)
        ]
