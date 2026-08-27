"""Framework-free input/output objects for the UC-01 use-case service.

Deliberately not Pydantic models: the service must be callable (and testable) without
FastAPI. The HTTP layer converts these to and from its own schemas.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..domain.enums import (
    DependencyFailurePolicy,
    DependencyName,
    SessionMode,
)
from ..domain.models import (
    CaseFile,
    Course,
    DependencyStatus,
    Greeting,
    ModeAvailability,
    NaricResolution,
    SessionContext,
    SessionRecord,
    UserContext,
)


@dataclass(frozen=True)
class Notice:
    """A user-facing message for the UI.

    ``code`` is stable and machine-readable; ``message`` is safe, non-technical text;
    ``action`` names an affordance the UI should offer (e.g. ``continue_without_calibration``,
    ``retry``).
    """

    code: str
    message: str
    severity: str = "info"
    action: str | None = None


@dataclass(frozen=True)
class OpenSessionCommand:
    """A validated request to open a coaching session.

    Note what is absent: no user id (resolved server-side), no NARIC level, no prompt or
    guardrail content. Those are not client inputs anywhere in UC-01.
    """

    mode: SessionMode
    course_id: str | None = None
    lesson_id: str | None = None
    case_id: str | None = None
    continue_without_calibration: bool = False
    dependency_failure_policy: DependencyFailurePolicy = DependencyFailurePolicy.FAIL


@dataclass(frozen=True)
class CatalogueResult:
    """Result of a courses / case-files listing.

    A dependency outage is a *result*, not an exception, so the UI can render the
    disabled state instead of an error page.
    """

    available: bool
    reason: str | None = None
    courses: Sequence[Course] = field(default_factory=tuple)
    case_files: Sequence[CaseFile] = field(default_factory=tuple)


@dataclass(frozen=True)
class BootstrapResult:
    """Everything the coaching interface needs to open its session picker."""

    user: UserContext
    display_name: str | None
    modes: Sequence[ModeAvailability]
    courses: Sequence[Course]
    case_files: Sequence[CaseFile]
    naric: NaricResolution
    dependencies: Mapping[DependencyName, DependencyStatus]
    notices: Sequence[Notice]
    greeting_preview: Greeting
    personalisation_available: bool


@dataclass(frozen=True)
class OpenSessionResult:
    """The outcome of a successful (possibly degraded) session open."""

    record: SessionRecord
    context: SessionContext
    greeting: Greeting
    notices: Sequence[Notice]


@dataclass(frozen=True)
class RecoveryHint:
    """What the user can still do after a rejected open attempt."""

    session_id: str
    available_modes: Sequence[SessionMode]
    suggested_mode: SessionMode


__all__ = [
    "BootstrapResult",
    "CatalogueResult",
    "Notice",
    "OpenSessionCommand",
    "OpenSessionResult",
    "RecoveryHint",
]
