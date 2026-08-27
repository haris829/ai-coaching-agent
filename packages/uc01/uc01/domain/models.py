"""UC-01 internal domain model.

These types are the *internal contract*. They are deliberately NOT shaped like any
external API response. Adapters are responsible for normalising upstream payloads into
these structures, which is what allows a real integration to replace a mock without
touching UC-01 business logic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .enums import (
    DependencyFailurePolicy,
    DependencyName,
    DependencyState,
    LinkedResourceType,
    NaricAssessmentState,
    NaricLevelSource,
    SessionMode,
    SessionStatus,
)

DEFAULT_EXPLANATION_LEVEL = 5
"""Documented UC-01 fallback explanation level, used whenever NARIC did not supply one."""

MIN_EXPLANATION_LEVEL = 1
MAX_EXPLANATION_LEVEL = 10


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UserContext:
    """The authenticated caller, resolved server-side only."""

    user_id: str
    tenant_id: str = "dev-tenant"

    def owns(self, owner_user_id: str | None) -> bool:
        return owner_user_id is not None and owner_user_id == self.user_id


# --------------------------------------------------------------------------- #
# Normalised external data
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UserProfile:
    """Personalisation data. Every field is optional on purpose: an incomplete profile
    is a normal, supported state, not an error."""

    user_id: str
    display_name: str | None = None
    preferred_language: str | None = None
    current_course_id: str | None = None
    current_lesson_id: str | None = None

    @property
    def is_complete(self) -> bool:
        return bool(self.display_name)


@dataclass(frozen=True)
class Lesson:
    lesson_id: str
    course_id: str
    title: str
    ordinal: int = 0


@dataclass(frozen=True)
class Course:
    course_id: str
    title: str
    lessons: Sequence[Lesson] = field(default_factory=tuple)

    def lesson(self, lesson_id: str) -> Lesson | None:
        for lesson in self.lessons:
            if lesson.lesson_id == lesson_id:
                return lesson
        return None


@dataclass(frozen=True)
class CaseFile:
    case_id: str
    title: str
    matter_reference: str | None = None


@dataclass(frozen=True)
class NaricAssessment:
    """Normalised NARIC result.

    ``level`` is whatever NARIC actually provided. It may be ``None`` even when the call
    succeeded (incomplete / still calibrating). UC-01 never invents a level here — the
    fallback decision is made explicitly by :func:`uc01.domain.policy.resolve_naric_level`.
    """

    state: NaricAssessmentState
    level: int | None = None
    assessed_at: datetime | None = None
    detail_code: str | None = None

    @property
    def usable(self) -> bool:
        return self.state is NaricAssessmentState.COMPLETE and self.level is not None


# --------------------------------------------------------------------------- #
# Availability / fallback metadata
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DependencyStatus:
    """Per-dependency availability plus the *user-facing* message and the
    *server-only* technical detail. The technical detail never leaves the server."""

    dependency: DependencyName
    state: DependencyState
    user_message: str | None = None
    technical_detail: str | None = None

    @property
    def is_available(self) -> bool:
        return self.state is DependencyState.AVAILABLE

    @property
    def is_degraded(self) -> bool:
        return self.state in (
            DependencyState.UNAVAILABLE,
            DependencyState.INCOMPLETE,
        )


@dataclass(frozen=True)
class ModeAvailability:
    mode: SessionMode
    available: bool
    reason: str | None = None
    """Human-readable, non-technical explanation shown when ``available`` is False."""


@dataclass(frozen=True)
class NaricResolution:
    """The outcome of the NARIC fallback rules."""

    level: int
    source: NaricLevelSource
    calibration_offer: bool
    """True when the UI should offer 'Continue without calibration'."""

    notice: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.source is not NaricLevelSource.NARIC


@dataclass(frozen=True)
class LinkedResource:
    """The resource a session is linked to, if any."""

    resource_type: LinkedResourceType
    resource_id: str
    label: str
    secondary_id: str | None = None
    secondary_label: str | None = None


# --------------------------------------------------------------------------- #
# The UC-01 session context
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SessionContext:
    """Clean internal context object for UC-01.

    user / session_mode / course / lesson / case_file / naric_level +
    availability & fallback metadata.
    """

    user: UserContext
    session_mode: SessionMode
    profile: UserProfile | None = None
    course: Course | None = None
    lesson: Lesson | None = None
    case_file: CaseFile | None = None
    naric: NaricResolution | None = None
    dependencies: Mapping[DependencyName, DependencyStatus] = field(default_factory=dict)
    downgraded_from: SessionMode | None = None

    @property
    def naric_level(self) -> int:
        return self.naric.level if self.naric else DEFAULT_EXPLANATION_LEVEL

    @property
    def naric_level_source(self) -> NaricLevelSource:
        return self.naric.source if self.naric else NaricLevelSource.DEFAULT

    @property
    def degraded_dependencies(self) -> tuple[DependencyName, ...]:
        return tuple(
            name for name, status in self.dependencies.items() if status.is_degraded
        )

    @property
    def personalisation_available(self) -> bool:
        status = self.dependencies.get(DependencyName.PROFILE)
        return bool(status and status.is_available and self.profile is not None)

    def linked_resource(self) -> LinkedResource | None:
        if self.course is not None:
            return LinkedResource(
                resource_type=LinkedResourceType.COURSE,
                resource_id=self.course.course_id,
                label=self.course.title,
                secondary_id=self.lesson.lesson_id if self.lesson else None,
                secondary_label=self.lesson.title if self.lesson else None,
            )
        if self.case_file is not None:
            return LinkedResource(
                resource_type=LinkedResourceType.CASE_FILE,
                resource_id=self.case_file.case_id,
                label=self.case_file.title,
            )
        return None


# --------------------------------------------------------------------------- #
# Greeting
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Greeting:
    """Server-composed greeting.

    ``text`` is user-facing. ``system_prompt_id`` / ``system_prompt_version`` identify the
    privileged server-side template that produced it — the prompt body itself is never
    part of this object, so it cannot accidentally be serialised to a client.
    """

    text: str
    variant: str
    system_prompt_id: str
    system_prompt_version: str
    personalised: bool


# --------------------------------------------------------------------------- #
# Session record (persistence-facing)
# --------------------------------------------------------------------------- #


@dataclass
class SessionRecord:
    """Persisted record of a session-open attempt.

    Required minimum fields: session_id, user_id, session_type, linked_resource,
    timestamp, naric_level. Everything else exists to make partial / degraded
    initialisation diagnosable.
    """

    session_id: str
    user_id: str
    session_type: SessionMode
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    naric_level: int | None = None
    naric_level_source: NaricLevelSource = NaricLevelSource.DEFAULT
    explanation_level: int = DEFAULT_EXPLANATION_LEVEL
    linked_resource: LinkedResource | None = None
    requested_mode: SessionMode | None = None
    downgraded_from: SessionMode | None = None
    degraded_dependencies: tuple[DependencyName, ...] = ()
    failure_code: str | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    greeting_variant: str | None = None
    system_prompt_id: str | None = None
    system_prompt_version: str | None = None
    dependency_failure_policy: DependencyFailurePolicy = DependencyFailurePolicy.FAIL

    @property
    def timestamp(self) -> datetime:
        """Alias for the required ``timestamp`` field of the UC-01 record contract."""
        return self.created_at


@dataclass(frozen=True)
class SessionEvent:
    """Append-only event attached to a session.

    Generic on purpose: future UCs can append their own event types (question asked,
    rating given, explain-differently pressed) without a schema change, and without
    UC-01 implementing any of that behaviour now.
    """

    session_id: str
    event_type: str
    occurred_at: datetime
    payload: Mapping[str, object] = field(default_factory=dict)
    event_id: int | None = None
