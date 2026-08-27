"""API request/response models.

These are deliberately *not* the domain models. The wire shape is narrower than
``SessionContext``: an authorised internal caller gets what it needs to pitch a
coaching response, and nothing more. In particular the response never carries
question text, and it never carries the raw NARIC level -- only the explanation
profile derived from it.

``extra="forbid"`` on the request model means a caller trying to inject
``user_id``, ``naric_level`` or any other context field is rejected outright
rather than having the field silently ignored.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from uc02.domain.models.context import (
    CoursesContext,
    ExplanationProfile,
    LegalContext,
    PersonalizationStatus,
    QuestionHistoryContext,
    SessionContext,
    SourceOutcome,
)
from uc02.domain.models.enums import ContextStatus, SourceName


class InitializeContextRequest(BaseModel):
    """Body of ``POST /api/v1/context/initialize``.

    ``session_id`` comes from the caller (UC-01 in production). It is optional
    only so the service is runnable standalone in development, and minting is
    gated by ``ALLOW_DEV_SESSION_IDS``.

    There is no ``user_id`` field and there never will be: identity is resolved
    server-side through ``CurrentUserProvider``.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Opaque session identifier created by the caller (UC-01).",
    )
    force_refresh: bool = Field(
        default=False,
        description="Rejected on this public path. Internal/admin use only, and config-gated.",
    )


class QuestionHistoryMetadata(BaseModel):
    """History metadata only. Question text never crosses this boundary."""

    model_config = ConfigDict(frozen=True)

    count: int
    earliest_asked_at: datetime | None = None
    latest_asked_at: datetime | None = None
    truncated: bool = False

    @classmethod
    def from_context(cls, history: QuestionHistoryContext) -> QuestionHistoryMetadata:
        return cls(
            count=history.count,
            earliest_asked_at=history.earliest_asked_at,
            latest_asked_at=history.latest_asked_at,
            truncated=history.truncated,
        )


class InitializeContextResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    context_status: ContextStatus
    context_version: str
    built_at: datetime
    explanation_profile: ExplanationProfile
    courses: CoursesContext
    legal_profile: LegalContext
    question_history: QuestionHistoryMetadata
    personalization: PersonalizationStatus
    source_status: dict[SourceName, SourceOutcome]

    @classmethod
    def from_context(
        cls, context: SessionContext, status: ContextStatus
    ) -> InitializeContextResponse:
        return cls(
            session_id=context.session_id,
            context_status=status,
            context_version=context.context_version,
            built_at=context.built_at,
            explanation_profile=context.explanation_profile,
            courses=context.courses,
            legal_profile=context.legal_profile,
            question_history=QuestionHistoryMetadata.from_context(context.question_history),
            personalization=context.personalization,
            source_status=context.source_status,
        )


class ContextStatusResponse(BaseModel):
    """Status flags only -- never context content."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    exists: bool
    context_version: str
    built_at: datetime
    personalization_available: bool
    source_status: dict[SourceName, SourceOutcome]

    @classmethod
    def from_context(cls, context: SessionContext) -> ContextStatusResponse:
        return cls(
            session_id=context.session_id,
            exists=True,
            context_version=context.context_version,
            built_at=context.built_at,
            personalization_available=context.personalization.available,
            source_status=context.source_status,
        )


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: str
    detail: str
