"""Upstream provider ports.

These four interfaces are the most important artefact in this repository. The
company's NARIC, Courses Agent and Legal Foot Prints systems do not exist yet,
so UC-02 defines what it needs and implements mocks behind it. When the real
systems arrive, an integration engineer writes one class per port and changes one
config value each (see docs/integration.md).

Contract every implementation must honour:

* ``async`` — the assembly service calls all four concurrently.
* Return the declared record type, or raise ``ProviderUnavailable``,
  ``ProviderTimeout`` or ``ProviderInvalidResponse``. Nothing else.
* Never return ``None`` to mean "down". "The learner has nothing" is an empty
  record; "the source is down" is an exception. The distinction is load-bearing.
* Do not apply defaults. Defaulting is the assembly service's job so that the
  fallback is recorded in ``source_status``.
* Do not log question text or full legal profiles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from uc02.domain.models.provider_records import (
    CoursesRecord,
    LegalProfileRecord,
    NaricRecord,
    QuestionRecord,
)


class NaricProvider(ABC):
    """Qualification level lookup (company system: NARIC)."""

    @abstractmethod
    async def get_qualification_level(self, user_id: str) -> NaricRecord:
        """Return the learner's qualification level.

        Return ``NaricRecord(level=None)`` when NARIC holds no qualification for
        this learner — do not substitute a default level here.
        """


class CoursesProvider(ABC):
    """Enrolment / progress lookup (company system: Courses Agent)."""

    @abstractmethod
    async def get_learning_context(self, user_id: str) -> CoursesRecord:
        """Return the learner's enrolments, completion and last-accessed lesson.

        Return ``CoursesRecord(enrolments=())`` for a learner with no enrolments.
        """


class LegalFootprintsProvider(ABC):
    """Speciality / practice-area lookup (company system: Legal Foot Prints)."""

    @abstractmethod
    async def get_profile(self, user_id: str) -> LegalProfileRecord:
        """Return the learner's legal profile.

        Return an all-empty ``LegalProfileRecord()`` for a learner who has not
        declared a speciality — never guess a practice area.
        """


class QuestionHistoryProvider(ABC):
    """Prior-question lookup across the learner's earlier sessions."""

    @abstractmethod
    async def get_recent_questions(self, user_id: str, limit: int) -> list[QuestionRecord]:
        """Return up to ``limit`` most recent questions, newest first.

        ``limit`` is decided server-side by UC-02 and is never caller-controlled.
        An implementation may return more than ``limit``; the assembly service
        truncates and flags it. Returning fewer is normal.
        """
