"""Internal service contracts for UC-01's external dependencies.

Each contract is a ``typing.Protocol``: an adapter satisfies it structurally, so no
adapter needs to import UC-01 machinery beyond the domain data types and the contract
exceptions. UC-01 business logic depends only on what is declared here.

Implementations shipped today are mocks (``uc01/adapters/mock``). Real implementations
go in ``uc01/adapters/real`` and must satisfy exactly these signatures.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.models import (
    CaseFile,
    Course,
    Greeting,
    NaricAssessment,
    SessionContext,
    UserContext,
    UserProfile,
)


@runtime_checkable
class NaricService(Protocol):
    """NARIC assessment lookup.

    Must raise :class:`~uc01.contracts.exceptions.DependencyUnavailableError` when the
    service cannot be reached, and
    :class:`~uc01.contracts.exceptions.InvalidUpstreamResponseError` when the payload
    cannot be normalised. Incomplete / still-calibrating assessments are *successful*
    calls that return a ``NaricAssessment`` with ``level=None``.
    """

    def get_assessment(self, user: UserContext) -> NaricAssessment: ...


@runtime_checkable
class CoursesService(Protocol):
    """Courses Agent access.

    ``list_accessible_courses`` returns only courses this user may open (possibly empty).
    ``get_accessible_course`` performs the server-side authorization check for a
    client-supplied id and must raise
    :class:`~uc01.contracts.exceptions.ResourceNotAccessibleError` for unknown or
    forbidden ids.
    """

    def list_accessible_courses(self, user: UserContext) -> Sequence[Course]: ...

    def get_accessible_course(self, user: UserContext, course_id: str) -> Course: ...


@runtime_checkable
class CaseFileService(Protocol):
    """Case Prep / Case File access, with the same authorization guarantees."""

    def list_accessible_case_files(self, user: UserContext) -> Sequence[CaseFile]: ...

    def get_accessible_case_file(self, user: UserContext, case_id: str) -> CaseFile: ...


@runtime_checkable
class ProfileService(Protocol):
    """Personalisation profile lookup.

    A missing/partial profile is returned as a ``UserProfile`` with empty fields; only a
    genuine failure raises.
    """

    def get_profile(self, user: UserContext) -> UserProfile: ...


@runtime_checkable
class GreetingGenerator(Protocol):
    """Isolated interface for greeting composition.

    Implemented locally today by ``uc01.domain.greeting.LocalTemplateGreetingGenerator``.
    A future AI-backed generator implements this same contract; UC-01 business logic does
    not change.
    """

    def generate(self, context: SessionContext) -> Greeting: ...


@runtime_checkable
class UserContextProvider(Protocol):
    """Resolves the authenticated caller.

    Must raise :class:`~uc01.domain.errors.AuthenticationRequiredError` when the caller
    cannot be identified. Implementations must never trust a client-supplied user id.
    """

    def resolve(self, credential: str | None) -> UserContext: ...


__all__ = [
    "CaseFileService",
    "CoursesService",
    "GreetingGenerator",
    "NaricService",
    "ProfileService",
    "UserContextProvider",
]
