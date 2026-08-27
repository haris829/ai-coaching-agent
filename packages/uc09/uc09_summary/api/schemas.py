"""API request and response schemas.

Request schemas set ``extra="forbid"``: an unknown field is rejected outright
rather than ignored. A client sending ``{"user_id": "someone-else"}`` gets a
422, not a silently dropped field - identity is resolved server-side and a
request must not be able to imply otherwise, even by accident.

Response schemas carry only what a frontend needs to render the summary. No
frontend is included here; this is where this component stops.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from uc09_summary.domain.enums import (
    GenerationMode,
    NaricLevel,
    NaricLevelSource,
    ResourceKind,
    SessionStatus,
    SourceStatus,
    SuggestionSource,
)
from uc09_summary.domain.models import SummaryRecord

_REQUEST = ConfigDict(extra="forbid")
_RESPONSE = ConfigDict(extra="forbid", frozen=True)


class GenerateSummaryRequest(BaseModel):
    """Body of ``POST /api/v1/sessions/{session_id}/summary``.

    Deliberately almost empty. Partiality is decided from the session state,
    not asserted by the caller: a client must not be able to label a complete
    session partial, or a partial one complete.
    """

    model_config = _REQUEST


class TopicOut(BaseModel):
    model_config = _RESPONSE

    topic_id: str
    label: str
    interaction_count: int
    first_discussed_at: datetime
    last_discussed_at: datetime


class ConceptOut(BaseModel):
    model_config = _RESPONSE

    concept_id: str
    label: str
    explanation: str
    topic_id: str
    evidence_interaction_ids: list[str]


class ResourceOut(BaseModel):
    model_config = _RESPONSE

    resource_id: str
    kind: ResourceKind
    citation: str
    title: str
    cited_in_interaction_ids: list[str]
    first_cited_at: datetime | None


class SuggestionOut(BaseModel):
    model_config = _RESPONSE

    suggestion_id: str
    label: str
    rationale: str
    source: SuggestionSource
    related_topic_id: str | None


class QuestionLogEntryOut(BaseModel):
    model_config = _RESPONSE

    interaction_id: str
    asked_at: datetime
    question_text: str
    topic_tags: list[str]


class SectionOut(BaseModel):
    """One of the four sections, with the orientation a frontend must show.

    ``orientation`` exists so that a client cannot render Next Steps as though
    it were a record of the session. It is data, not styling.
    """

    model_config = _RESPONSE

    key: str
    title: str
    orientation: str
    status: SourceStatus
    item_count: int
    note: str | None


class SummaryResponse(BaseModel):
    """The structured summary. This is the wire shape other components consume."""

    model_config = _RESPONSE

    summary_id: str
    session_id: str
    user_id: str
    generated_at: datetime
    is_partial: bool
    covers_interactions_through: datetime
    topics_covered: list[TopicOut]
    key_concepts: list[ConceptOut]
    resources_referenced: list[ResourceOut]
    next_steps: list[SuggestionOut]
    source_status: dict[str, SourceStatus]
    generation_mode: GenerationMode
    session_status: SessionStatus

    user_display_name: str
    session_started_at: datetime
    session_ended_at: datetime | None
    session_duration_seconds: int
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    explanation_profile: str
    section_notes: dict[str, str]
    question_log: list[QuestionLogEntryOut]

    sections: list[SectionOut]
    partial_marker: str | None = Field(
        default=None,
        description=(
            "Visible marker a client must display when is_partial is true. "
            "Null on a complete record."
        ),
    )
    cpd_label: str
    product_name: str

    @classmethod
    def from_record(cls, record: SummaryRecord) -> SummaryResponse:
        """Project a stored record onto the wire shape."""
        from uc09_summary.api.sections import describe_sections
        from uc09_summary.rendering.html_document import (
            CPD_LABEL,
            PARTIAL_MARKER,
            PRODUCT_NAME,
        )

        return cls(
            summary_id=record.summary_id,
            session_id=record.session_id,
            user_id=record.user_id,
            generated_at=record.generated_at,
            is_partial=record.is_partial,
            covers_interactions_through=record.covers_interactions_through,
            topics_covered=[TopicOut(**t.model_dump()) for t in record.topics_covered],
            key_concepts=[
                ConceptOut(
                    **{
                        **c.model_dump(),
                        "evidence_interaction_ids": list(c.evidence_interaction_ids),
                    }
                )
                for c in record.key_concepts
            ],
            resources_referenced=[
                ResourceOut(
                    **{
                        **r.model_dump(),
                        "cited_in_interaction_ids": list(r.cited_in_interaction_ids),
                    }
                )
                for r in record.resources_referenced
            ],
            next_steps=[SuggestionOut(**s.model_dump()) for s in record.next_steps],
            source_status=dict(record.source_status),
            generation_mode=record.generation_mode,
            session_status=record.session_status,
            user_display_name=record.user_display_name,
            session_started_at=record.session_started_at,
            session_ended_at=record.session_ended_at,
            session_duration_seconds=record.session_duration_seconds,
            naric_level=record.naric_level,
            naric_level_source=record.naric_level_source,
            explanation_profile=record.explanation_profile,
            section_notes=dict(record.section_notes),
            question_log=[
                QuestionLogEntryOut(
                    **{**q.model_dump(), "topic_tags": list(q.topic_tags)}
                )
                for q in record.question_log
            ],
            sections=describe_sections(record),
            partial_marker=PARTIAL_MARKER if record.is_partial else None,
            cpd_label=CPD_LABEL,
            product_name=PRODUCT_NAME,
        )


class DownloadEventOut(BaseModel):
    model_config = _RESPONSE

    download_id: str
    summary_id: str
    session_id: str
    downloaded_at: datetime
    format: str
    pdf_available: bool
    byte_count: int


class HealthResponse(BaseModel):
    model_config = _RESPONSE

    status: str
    version: str
    providers: dict[str, str]


class ErrorBody(BaseModel):
    """Error payload. Carries a code and a fixed message, and nothing else.

    No summary content, no session content, no upstream error text, no internal
    identifiers beyond the one the caller already sent.
    """

    model_config = _RESPONSE

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = _RESPONSE

    error: ErrorBody
