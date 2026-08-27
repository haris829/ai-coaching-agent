"""The assembled ``SessionContext`` and its parts.

This is the object downstream use cases consume. ``CONTEXT_VERSION`` is bumped
whenever the shape changes so consumers can detect drift.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from uc02.domain.models.enums import (
    AssumedPriorKnowledge,
    ErrorCategory,
    ExplanationDepth,
    ExplanationDomain,
    ExplanationTemplateId,
    LevelSource,
    SourceName,
    SourceStatus,
    TerminologyLevel,
)

#: Bump on any change to the shape of ``SessionContext`` or its members.
CONTEXT_VERSION = "uc02.context.v1"

#: Applied when NARIC is unavailable, invalid, or holds no qualification.
DEFAULT_NARIC_LEVEL = 5

PERSONALIZATION_UNAVAILABLE_NOTICE = (
    "Personalisation data is temporarily unavailable. You can continue your session."
)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NaricContext(_Model):
    level: int
    level_source: LevelSource
    raw_level_label: str | None = None


class CourseContext(_Model):
    course_id: str
    course_name: str
    completion_percentage: float = Field(ge=0.0, le=100.0)
    last_accessed_lesson_id: str | None = None
    last_accessed_lesson_name: str | None = None


class CoursesContext(_Model):
    enrolments: tuple[CourseContext, ...] = ()


class LegalContext(_Model):
    speciality_areas: tuple[str, ...] = ()
    case_type_preferences: tuple[str, ...] = ()
    practice_area: str | None = None
    explanation_domain: ExplanationDomain = ExplanationDomain.GENERAL_LEGAL


class QuestionHistoryItem(_Model):
    question_id: str
    session_id: str
    asked_at: datetime
    topic_tag: str | None = None
    #: A short excerpt kept server-side only. Never returned by the API, never logged.
    text_excerpt: str = ""


class QuestionHistoryContext(_Model):
    items: tuple[QuestionHistoryItem, ...] = ()
    #: True when the source held more questions than the server-side limit.
    truncated: bool = False
    #: Records the source returned that could not be parsed and were dropped.
    dropped_malformed_count: int = 0

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def earliest_asked_at(self) -> datetime | None:
        return min((i.asked_at for i in self.items), default=None)

    @property
    def latest_asked_at(self) -> datetime | None:
        return max((i.asked_at for i in self.items), default=None)

    @property
    def topic_tags(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.items:
            if item.topic_tag and item.topic_tag not in seen:
                seen.append(item.topic_tag)
        return tuple(seen)


class ExplanationProfile(_Model):
    """The deterministic output of the NARIC-level -> template mapping."""

    template_id: ExplanationTemplateId
    depth: ExplanationDepth
    terminology_level: TerminologyLevel
    assumed_prior_knowledge: AssumedPriorKnowledge
    detail_level: int = Field(ge=1, le=3)


class PersonalizationStatus(_Model):
    """Whether any upstream source contributed usable data.

    ``notice`` is structured data for a future frontend to render. UC-02 renders
    nothing itself (no frontend is in scope).
    """

    available: bool
    notice: str | None = None
    contributing_sources: tuple[SourceName, ...] = ()


class SourceOutcome(_Model):
    """Per-source result recorded on the context for downstream analytics."""

    status: SourceStatus
    error_category: ErrorCategory = ErrorCategory.NONE
    duration_ms: int = 0
    fallback_applied: bool = False


class SessionContext(_Model):
    """Everything UC-02 knows about a learner at the moment a session starts."""

    session_id: str
    user_id: str
    naric: NaricContext
    courses: CoursesContext
    legal_profile: LegalContext
    question_history: QuestionHistoryContext
    explanation_profile: ExplanationProfile
    personalization: PersonalizationStatus
    source_status: dict[SourceName, SourceOutcome]
    built_at: datetime
    context_version: str = CONTEXT_VERSION
