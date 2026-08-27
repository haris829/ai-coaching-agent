"""Read-only upstream ports.

Hard rule enforced by architecture tests: these ports (and every adapter that
implements them) expose **no** mutating operation. No ``create``, ``update``,
``delete``, ``patch``, ``save``, ``write``, ``put``, ``post``, ``insert``,
``upsert``, ``set_*``, ``add_*``, ``remove_*`` or ``mutate``.

Each port also exposes a read-only *status* accessor. That is what lets UC-07
preserve the five-state source status contract: ``unavailable`` and ``invalid``
arrive as typed exceptions, while ``available`` / ``empty`` / ``partial`` are
reported without loss ("empty" and "unavailable" are never the same thing).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from uc07.domain.enums import SourceStatus
from uc07.domain.models import (
    CourseSummary,
    Enrolment,
    FeedbackRecord,
    InteractionRecord,
    LearnerProfile,
    Recommendation,
)


class ReadOnlyPort(ABC):
    """Marker base class for every upstream (non-persisting) port."""


class InteractionLogProvider(ReadOnlyPort):
    """Read-only access to a learner's complete coaching interaction history."""

    @abstractmethod
    def for_user(self, user_id: str) -> Sequence[InteractionRecord]:
        """Return every interaction for ``user_id`` across all sessions.

        Raises:
            ProviderUnavailable: source could not answer.
            ProviderTimeout: source exceeded its time budget.
            ProviderInvalidResponse: payload cannot satisfy the platform contract.
        """

    @abstractmethod
    def count_for_user(self, user_id: str) -> int:
        """Provider-reported interaction count (observability only).

        Authoritative counting is done by
        :func:`uc07.domain.counting.qualifying_interactions`; this value is never
        used to make a threshold decision (docs/assumptions.md A-05).
        """

    @abstractmethod
    def status_for_user(self, user_id: str) -> SourceStatus:
        """Source status for this learner's interaction history."""


class FeedbackProvider(ReadOnlyPort):
    """Read-only access to ratings attached to interactions."""

    @abstractmethod
    def for_interactions(self, interaction_ids: Sequence[str]) -> Sequence[FeedbackRecord]:
        """Return feedback records for the given interaction ids."""

    @abstractmethod
    def status_for_interactions(self, interaction_ids: Sequence[str]) -> SourceStatus:
        """Source status for the feedback lookup (empty != unavailable)."""


class LearnerProfileProvider(ReadOnlyPort):
    """Read-only access to the learner profile (speciality areas, NARIC level)."""

    @abstractmethod
    def get_profile(self, user_id: str) -> LearnerProfile:
        """Return the learner's profile projection.

        Raises ``ProviderUnavailable`` when the profile source cannot answer.
        Speciality partiality is carried on
        :attr:`~uc07.domain.models.LearnerProfile.speciality_status`, never
        silently upgraded to ``available``.
        """


class CoursesProvider(ReadOnlyPort):
    """Read-only access to course/lesson data and the learner's enrolments."""

    @abstractmethod
    def resolve_recommendations(self, topic_tags: Sequence[str]) -> Sequence[Recommendation]:
        """Return candidate recommendations for the given topic tags."""

    @abstractmethod
    def enrolments_for(self, user_id: str) -> Sequence[Enrolment]:
        """Return the learner's existing enrolments."""

    @abstractmethod
    def catalogue(self) -> Sequence[CourseSummary]:
        """Return course/lesson identity used to validate recommendations.

        UC-07 validates every recommendation against this catalogue so that no
        unresolved identifier can reach a report.
        """

    @abstractmethod
    def status(self) -> SourceStatus:
        """Source status of the course data."""


#: Every read-only upstream port. Architecture tests iterate this tuple.
READ_ONLY_PORTS: tuple[type[ReadOnlyPort], ...] = (
    InteractionLogProvider,
    FeedbackProvider,
    LearnerProfileProvider,
    CoursesProvider,
)
