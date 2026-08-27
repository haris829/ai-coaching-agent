"""Pydantic request/response schemas for the UC-01 HTTP API.

Two rules shape this module:

1. **Requests forbid unknown fields** (``extra="forbid"``). A client that tries to send
   ``naric_level``, ``user_id``, ``system_prompt`` or any other privileged value gets a
   422 instead of having it silently ignored — the attempt is visible and testable.
2. **Responses are allow-lists.** They are built field-by-field from domain objects, so
   server-only data (technical error detail, prompt bodies, diagnostics) cannot leak by
   accident when a domain model grows a field.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..application.dto import BootstrapResult, CatalogueResult, Notice, OpenSessionResult
from ..domain.enums import (
    DependencyFailurePolicy,
    NaricLevelSource,
    SessionMode,
    SessionStatus,
)
from ..domain.models import (
    CaseFile,
    Course,
    DependencyStatus,
    Greeting,
    Lesson,
    LinkedResource,
    ModeAvailability,
    NaricResolution,
    SessionRecord,
)

# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class OpenSessionRequest(BaseModel):
    """Body of ``POST /api/v1/sessions``.

    Note what a client may *not* send: user identity, NARIC level, explanation level,
    session status, system prompts or guardrails. Those are server-owned.
    """

    model_config = ConfigDict(extra="forbid")

    mode: SessionMode = Field(description="One of free-form, course-linked, case-linked.")
    course_id: str | None = Field(
        default=None, max_length=128, description="Required for course-linked sessions."
    )
    lesson_id: str | None = Field(
        default=None, max_length=128, description="Required for course-linked sessions."
    )
    case_id: str | None = Field(
        default=None, max_length=128, description="Required for case-linked sessions."
    )
    continue_without_calibration: bool = Field(
        default=False,
        description=(
            "Set when the user chose 'Continue without calibration'. Never changes "
            "whether the session opens; it records that the user was informed and "
            "suppresses the repeated notice."
        ),
    )
    on_dependency_failure: DependencyFailurePolicy = Field(
        default=DependencyFailurePolicy.FAIL,
        description=(
            "'fail' rejects the attempt if the mode's dependency is unavailable "
            "(a failed session record is still written). 'fallback_free_form' opens a "
            "degraded free-form session instead."
        ),
    )


# --------------------------------------------------------------------------- #
# Response building blocks
# --------------------------------------------------------------------------- #


class LessonOut(BaseModel):
    lesson_id: str
    title: str
    ordinal: int

    @classmethod
    def of(cls, lesson: Lesson) -> LessonOut:
        return cls(lesson_id=lesson.lesson_id, title=lesson.title, ordinal=lesson.ordinal)


class CourseOut(BaseModel):
    course_id: str
    title: str
    lessons: list[LessonOut]

    @classmethod
    def of(cls, course: Course) -> CourseOut:
        return cls(
            course_id=course.course_id,
            title=course.title,
            lessons=[LessonOut.of(lesson) for lesson in course.lessons],
        )


class CaseFileOut(BaseModel):
    case_id: str
    title: str
    matter_reference: str | None = None

    @classmethod
    def of(cls, case_file: CaseFile) -> CaseFileOut:
        return cls(
            case_id=case_file.case_id,
            title=case_file.title,
            matter_reference=case_file.matter_reference,
        )


class ModeOut(BaseModel):
    mode: SessionMode
    available: bool
    reason: str | None = None

    @classmethod
    def of(cls, availability: ModeAvailability) -> ModeOut:
        return cls(
            mode=availability.mode,
            available=availability.available,
            reason=availability.reason,
        )


class NaricOut(BaseModel):
    """The applied explanation level and, crucially, where it came from."""

    level: int
    source: NaricLevelSource
    is_fallback: bool
    offer_continue_without_calibration: bool
    notice: str | None = None

    @classmethod
    def of(cls, resolution: NaricResolution) -> NaricOut:
        return cls(
            level=resolution.level,
            source=resolution.source,
            is_fallback=resolution.is_fallback,
            offer_continue_without_calibration=resolution.calibration_offer,
            notice=resolution.notice,
        )


class NoticeOut(BaseModel):
    code: str
    message: str
    severity: str
    action: str | None = None

    @classmethod
    def of(cls, notice: Notice) -> NoticeOut:
        return cls(
            code=notice.code,
            message=notice.message,
            severity=notice.severity,
            action=notice.action,
        )


class DependencyOut(BaseModel):
    """Availability only. ``technical_detail`` is deliberately absent."""

    name: str
    state: str

    @classmethod
    def of(cls, status: DependencyStatus) -> DependencyOut:
        return cls(name=status.dependency.value, state=status.state.value)


class GreetingOut(BaseModel):
    """The greeting text and whether it was personalised.

    The prompt body, prompt id and prompt version are server-side only and are not part
    of this schema.
    """

    text: str
    variant: str
    personalised: bool

    @classmethod
    def of(cls, greeting: Greeting) -> GreetingOut:
        return cls(
            text=greeting.text,
            variant=greeting.variant,
            personalised=greeting.personalised,
        )


class LinkedResourceOut(BaseModel):
    type: str
    id: str
    label: str
    secondary_id: str | None = None
    secondary_label: str | None = None

    @classmethod
    def of(cls, linked: LinkedResource) -> LinkedResourceOut:
        return cls(
            type=linked.resource_type.value,
            id=linked.resource_id,
            label=linked.label,
            secondary_id=linked.secondary_id,
            secondary_label=linked.secondary_label,
        )


class SessionOut(BaseModel):
    """The session record as the owner may see it.

    ``diagnostics`` and dependency technical details are never included.
    """

    session_id: str
    user_id: str
    session_type: SessionMode
    status: SessionStatus
    requested_mode: SessionMode | None = None
    downgraded_from: SessionMode | None = None
    linked_resource: LinkedResourceOut | None = None
    naric_level: int | None = None
    naric_level_source: NaricLevelSource
    explanation_level: int
    degraded_dependencies: list[str]
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, record: SessionRecord) -> SessionOut:
        return cls(
            session_id=record.session_id,
            user_id=record.user_id,
            session_type=record.session_type,
            status=record.status,
            requested_mode=record.requested_mode,
            downgraded_from=record.downgraded_from,
            linked_resource=(
                LinkedResourceOut.of(record.linked_resource)
                if record.linked_resource
                else None
            ),
            naric_level=record.naric_level,
            naric_level_source=record.naric_level_source,
            explanation_level=record.explanation_level,
            degraded_dependencies=[dep.value for dep in record.degraded_dependencies],
            failure_code=record.failure_code,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


# --------------------------------------------------------------------------- #
# Endpoint responses
# --------------------------------------------------------------------------- #


class MockNoticeOut(BaseModel):
    """Explicit, machine-readable statement that integrations are mocked.

    Present so a mock deployment can never be mistaken for a real one.
    """

    using_mock_adapters: bool
    adapters: Mapping[str, str]
    warning: str | None = None


class BootstrapResponse(BaseModel):
    user_id: str
    display_name: str | None = None
    personalisation_available: bool
    modes: list[ModeOut]
    courses: list[CourseOut]
    case_files: list[CaseFileOut]
    naric: NaricOut
    dependencies: list[DependencyOut]
    notices: list[NoticeOut]
    greeting_preview: GreetingOut
    integrations: MockNoticeOut

    @classmethod
    def of(cls, result: BootstrapResult, integrations: MockNoticeOut) -> BootstrapResponse:
        return cls(
            user_id=result.user.user_id,
            display_name=result.display_name,
            personalisation_available=result.personalisation_available,
            modes=[ModeOut.of(mode) for mode in result.modes],
            courses=[CourseOut.of(course) for course in result.courses],
            case_files=[CaseFileOut.of(case) for case in result.case_files],
            naric=NaricOut.of(result.naric),
            dependencies=[DependencyOut.of(status) for status in result.dependencies.values()],
            notices=[NoticeOut.of(notice) for notice in result.notices],
            greeting_preview=GreetingOut.of(result.greeting_preview),
            integrations=integrations,
        )


class CoursesResponse(BaseModel):
    available: bool
    reason: str | None = None
    courses: list[CourseOut]

    @classmethod
    def of(cls, result: CatalogueResult) -> CoursesResponse:
        return cls(
            available=result.available,
            reason=result.reason,
            courses=[CourseOut.of(course) for course in result.courses],
        )


class CaseFilesResponse(BaseModel):
    available: bool
    reason: str | None = None
    case_files: list[CaseFileOut]

    @classmethod
    def of(cls, result: CatalogueResult) -> CaseFilesResponse:
        return cls(
            available=result.available,
            reason=result.reason,
            case_files=[CaseFileOut.of(case) for case in result.case_files],
        )


class SessionContextOut(BaseModel):
    """The parts of the internal ``SessionContext`` that are safe to publish."""

    session_mode: SessionMode
    downgraded_from: SessionMode | None = None
    course: CourseOut | None = None
    lesson: LessonOut | None = None
    case_file: CaseFileOut | None = None
    naric: NaricOut
    personalisation_available: bool
    degraded_dependencies: list[str]


class OpenSessionResponse(BaseModel):
    session: SessionOut
    greeting: GreetingOut
    context: SessionContextOut
    notices: list[NoticeOut]

    @classmethod
    def of(cls, result: OpenSessionResult) -> OpenSessionResponse:
        context = result.context
        return cls(
            session=SessionOut.of(result.record),
            greeting=GreetingOut.of(result.greeting),
            context=SessionContextOut(
                session_mode=context.session_mode,
                downgraded_from=context.downgraded_from,
                course=CourseOut.of(context.course) if context.course else None,
                lesson=LessonOut.of(context.lesson) if context.lesson else None,
                case_file=CaseFileOut.of(context.case_file) if context.case_file else None,
                naric=NaricOut.of(context.naric) if context.naric else None,  # type: ignore[arg-type]
                personalisation_available=context.personalisation_available,
                degraded_dependencies=[
                    dep.value for dep in context.degraded_dependencies
                ],
            ),
            notices=[NoticeOut.of(notice) for notice in result.notices],
        )


class ErrorBody(BaseModel):
    code: str
    message: str


class RecoveryOut(BaseModel):
    """What the user can still do after a rejected attempt."""

    session_id: str | None = None
    available_modes: list[SessionMode] = Field(default_factory=list)
    suggested_mode: SessionMode | None = None


class ErrorResponse(BaseModel):
    """Uniform, safe error envelope.

    ``debug`` is populated only when ``UC01_EXPOSE_ERROR_DETAILS=true`` in developer
    mode; it is absent in every other configuration.
    """

    error: ErrorBody
    recovery: RecoveryOut | None = None
    fields: list[Mapping[str, Any]] | None = None
    debug: Mapping[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    use_case: str
    environment: str
    persistence: str
    integrations: MockNoticeOut


class DevContextResponse(BaseModel):
    """Development helper for the reference UI. Disabled when dev mode is off."""

    users: list[Mapping[str, str]]
    scenarios: Mapping[str, str]
    scenario_options: Mapping[str, Sequence[str]]
    scenario_header_enabled: bool


__all__ = [
    "BootstrapResponse",
    "CaseFileOut",
    "CaseFilesResponse",
    "CourseOut",
    "CoursesResponse",
    "DevContextResponse",
    "ErrorBody",
    "ErrorResponse",
    "GreetingOut",
    "HealthResponse",
    "LessonOut",
    "MockNoticeOut",
    "ModeOut",
    "NaricOut",
    "NoticeOut",
    "OpenSessionRequest",
    "OpenSessionResponse",
    "RecoveryOut",
    "SessionOut",
]
