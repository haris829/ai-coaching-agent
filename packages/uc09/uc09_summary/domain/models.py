"""Domain records.

Two families live here:

* **Received** records - what upstream ports hand us (``SessionRecord``,
  ``InteractionRecord``, ``Resource``, ``Suggestion``). Their shape is defined
  by this component and adapters map onto them; see docs/SHARED_CONTRACT.md.
* **Owned** records - what this component writes (``SummaryRecord``,
  ``DownloadEvent``). Nothing else on the platform owns these.

Every model forbids unknown fields. An upstream payload carrying an extra key
is an adapter concern to drop, not a domain model concern to absorb.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from uc09_summary.domain.enums import (
    GenerationMode,
    NaricLevel,
    NaricLevelSource,
    ResourceKind,
    SessionStatus,
    SourceStatus,
    SuggestionSource,
)

_STRICT = ConfigDict(extra="forbid", frozen=True)


class _Record(BaseModel):
    model_config = _STRICT


# --------------------------------------------------------------------------
# Received from upstream ports
# --------------------------------------------------------------------------


class SessionRecord(_Record):
    """A coaching session as this component needs to see it. READ ONLY upstream."""

    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    user_display_name: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime | None = None
    status: SessionStatus
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    naric_level_status: SourceStatus
    course_completion_percent: int = Field(ge=0, le=100)
    course_title: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status in (SessionStatus.COMPLETED, SessionStatus.SUMMARY_GENERATED)


class InteractionRecord(_Record):
    """One logged learner interaction. READ ONLY upstream.

    ``topic_tags`` and ``concept_tags`` are the *only* admissible source of
    topics and concepts in a summary. ``question_text`` is rendered only in the
    question-log fallback and is never written to application logs.
    """

    interaction_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    occurred_at: datetime
    question_text: str = ""
    topic_tags: tuple[str, ...] = ()
    concept_tags: tuple[str, ...] = ()

    @field_validator("topic_tags", "concept_tags", mode="before")
    @classmethod
    def _tuple_of_str(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class Resource(_Record):
    """An authority *actually cited during the session*. READ ONLY upstream.

    ``cited_in_interaction_ids`` is what makes the citation checkable: a
    resource with no interaction behind it cannot be grounded.
    """

    resource_id: str = Field(min_length=1)
    kind: ResourceKind
    citation: str = Field(min_length=1)
    title: str = Field(min_length=1)
    cited_in_interaction_ids: tuple[str, ...] = ()
    first_cited_at: datetime | None = None

    @field_validator("cited_in_interaction_ids", mode="before")
    @classmethod
    def _tuple_of_str(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class Suggestion(_Record):
    """A forward-looking recommendation. Never invented; always has provenance."""

    suggestion_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    rationale: str = ""
    source: SuggestionSource
    related_topic_id: str | None = None


# --------------------------------------------------------------------------
# Derived inside this component
# --------------------------------------------------------------------------


class Topic(_Record):
    """A topic actually discussed. ``topic_id`` must exist in the tag record."""

    topic_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    interaction_count: int = Field(ge=1)
    first_discussed_at: datetime
    last_discussed_at: datetime


class Concept(_Record):
    """A key point the learner explored.

    ``topic_id`` ties it to a grounded topic and ``evidence_interaction_ids``
    ties it to the interactions it was drawn from. Both are checked.
    """

    concept_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    evidence_interaction_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_interaction_ids", mode="before")
    @classmethod
    def _tuple_of_str(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class QuestionLogEntry(_Record):
    """One line of the question-log fallback: what was asked and when."""

    interaction_id: str = Field(min_length=1)
    asked_at: datetime
    question_text: str
    topic_tags: tuple[str, ...] = ()

    @field_validator("topic_tags", mode="before")
    @classmethod
    def _tuple_of_str(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class SummaryContent(_Record):
    """What a :class:`~uc09_summary.ports.summary_generator.SummaryGenerator` returns.

    A generator hands back exactly the four sections plus optional per-section
    notes. It does not decide grounding, partiality, identity or status - those
    belong to this component.
    """

    topics_covered: tuple[Topic, ...] = ()
    key_concepts: tuple[Concept, ...] = ()
    resources_referenced: tuple[Resource, ...] = ()
    next_steps: tuple[Suggestion, ...] = ()
    section_notes: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "topics_covered",
        "key_concepts",
        "resources_referenced",
        "next_steps",
        mode="before",
    )
    @classmethod
    def _tuple_of_models(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


# --------------------------------------------------------------------------
# Owned by this component
# --------------------------------------------------------------------------


class SummaryRecord(_Record):
    """The record this component owns. See docs/SHARED_CONTRACT.md for the wire shape."""

    summary_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    generated_at: datetime
    is_partial: bool
    covers_interactions_through: datetime
    topics_covered: tuple[Topic, ...] = ()
    key_concepts: tuple[Concept, ...] = ()
    resources_referenced: tuple[Resource, ...] = ()
    next_steps: tuple[Suggestion, ...] = ()
    source_status: dict[str, SourceStatus] = Field(default_factory=dict)
    generation_mode: GenerationMode
    session_status: SessionStatus = SessionStatus.SUMMARY_GENERATED

    # Presentation-supporting fields owned by this component. They exist
    # because the exported PDF is required to carry branding, a name, a date,
    # a duration and a verification id. See docs/assumptions.md A-011..A-014.
    user_display_name: str = Field(min_length=1)
    session_started_at: datetime
    session_ended_at: datetime | None = None
    session_duration_seconds: int = Field(ge=0)
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    explanation_profile: str
    section_notes: dict[str, str] = Field(default_factory=dict)
    question_log: tuple[QuestionLogEntry, ...] = ()

    @field_validator(
        "topics_covered",
        "key_concepts",
        "resources_referenced",
        "next_steps",
        "question_log",
        mode="before",
    )
    @classmethod
    def _tuple_of_models(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class DownloadEvent(_Record):
    """One export download, recorded against the session. Owned by this component."""

    download_id: str = Field(min_length=1)
    summary_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    downloaded_at: datetime
    format: str = Field(min_length=1)
    pdf_available: bool
    byte_count: int = Field(ge=0)
