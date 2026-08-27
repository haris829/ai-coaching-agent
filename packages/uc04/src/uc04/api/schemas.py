"""API request and response schemas.

Requests use ``extra="forbid"``. An attempt to send ``naric_level``, ``grounding``,
``system_prompt`` or ``disable_quiz_protection`` produces a visible 422 naming the field - never
a silent ignore, and never a value that reaches business logic. The error envelope echoes the
rejected field names back so a caller can see exactly what had no effect.

``user_id`` is absent by design: the principal is resolved server-side.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import (
    ExplanationProfile,
    FollowUpAction,
    FramingStrategy,
    Grounding,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    ResponseAction,
    ResponseStatus,
    SectionRefStatus,
    SourceStatus,
)

MAX_QUESTION_LENGTH = 2000


class AskQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=200)
    course_id: str = Field(min_length=1, max_length=200)
    lesson_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)


class FollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: FollowUpAction


class SectionReferenceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SectionRefStatus
    lesson_section_id: str | None = None


class CrossLessonRefSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_id: str
    title: str
    reason: str = ""


class CoachingResponseSchema(BaseModel):
    """The response contract. Carries an explanation and a section reference - never raw
    lesson content."""

    model_config = ConfigDict(extra="forbid")

    status: ResponseStatus
    interaction_id: str
    session_id: str
    course_id: str
    lesson_id: str
    grounding: Grounding
    explanation: str
    section_reference: SectionReferenceSchema
    concept_tag: str
    topic_tag: str
    framing_used: FramingStrategy | None
    explain_differently_count: int
    cross_lesson_references: list[CrossLessonRefSchema]
    actions: list[ResponseAction]
    notice: str | None
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    explanation_profile: ExplanationProfile
    quiz_intent_detected: bool
    source_status: dict[str, SourceStatus]
    rating_state: RatingState


class ErrorResponse(BaseModel):
    """Uniform error envelope.

    No internal exception text, provider name, prompt content, stack trace or lesson content
    ever appears here.
    """

    model_config = ConfigDict(extra="forbid")

    error_code: str
    message: str
    #: Populated only for validation failures, naming the fields that were rejected.
    rejected_fields: list[str] = Field(default_factory=list)
