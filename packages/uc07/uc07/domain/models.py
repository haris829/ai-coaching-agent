"""Platform-contract domain models and UC-07 report models (Pydantic v2).

Every model is frozen and forbids unknown fields. ``extra="forbid"`` on
:class:`InteractionRecord` is a privacy guarantee, not a style choice: a payload
carrying ``question_text`` cannot be turned into a domain record at all.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from uc07.domain.enums import (
    SIGNAL_ORDER,
    DescriptionSource,
    EvidenceBasis,
    GapType,
    NaricLevel,
    NaricLevelSource,
    NoticeCode,
    NoticeSeverity,
    Rating,
    RatingState,
    RecommendationStatus,
    RecommendationType,
    SignalKind,
    SourceStatus,
    ThresholdStatus,
    UnexploredAnalysisState,
)

#: Field names UC-07 refuses to accept anywhere in a domain record.
#: Question text is out of contract and must never be retrieved, inferred,
#: reconstructed, stored or logged.
FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset(
    {"question_text", "question", "prompt_text", "answer_text", "response_text"}
)

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Upstream (read-only) contract types
# ---------------------------------------------------------------------------


class InteractionRecord(_Frozen):
    """SPECIFIED BY COMPANY. One coaching interaction. Read-only for UC-07.

    There is no question-text field, by contract.
    """

    interaction_id: NonEmptyStr
    session_id: NonEmptyStr
    user_id: NonEmptyStr
    asked_at: datetime
    topic_tag: NonEmptyStr
    question_class: NonEmptyStr
    naric_level: NaricLevel
    response_id: NonEmptyStr
    follow_up_of: str | None = None
    explain_differently_count: int = Field(default=0, ge=0)
    rating_state: RatingState = RatingState.PENDING

    _utc_asked_at = field_validator("asked_at")(_as_utc)

    @field_validator("follow_up_of")
    @classmethod
    def _reject_blank_follow_up(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("follow_up_of must be a non-empty id or null")
        return value

    @model_validator(mode="after")
    def _no_self_follow_up(self) -> InteractionRecord:
        if self.follow_up_of is not None and self.follow_up_of == self.interaction_id:
            raise ValueError("an interaction cannot be a follow-up of itself")
        return self

    @property
    def is_follow_up(self) -> bool:
        return self.follow_up_of is not None


class FeedbackRecord(_Frozen):
    """SPECIFIED BY COMPANY. One rating of one interaction. Read-only for UC-07.

    ``comment`` is learner free text: UC-07 reads the record but never emits or
    logs the comment (docs/assumptions.md, A-13).
    """

    rating_id: NonEmptyStr
    interaction_id: NonEmptyStr
    user_id: NonEmptyStr
    rated_at: datetime
    rating: Rating
    comment: str | None = None

    _utc_rated_at = field_validator("rated_at")(_as_utc)


class LearnerProfile(_Frozen):
    """Learner profile projection UC-07 needs. Read-only.

    ``speciality_status`` preserves the upstream source status of the speciality
    subsection so that ``empty`` (learner genuinely has no speciality) is never
    confused with ``partial`` or ``unavailable``.
    """

    user_id: NonEmptyStr
    speciality_areas: tuple[NonEmptyStr, ...] = ()
    speciality_status: SourceStatus = SourceStatus.EMPTY
    naric_level: NaricLevel | None = None
    naric_level_source: NaricLevelSource | None = None

    @field_validator("speciality_areas")
    @classmethod
    def _dedupe_preserving_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for area in value:
            if area not in seen:
                seen.add(area)
                out.append(area)
        return tuple(out)

    @model_validator(mode="after")
    def _status_consistency(self) -> LearnerProfile:
        if self.speciality_status is SourceStatus.AVAILABLE and not self.speciality_areas:
            raise ValueError(
                "speciality_status 'available' requires at least one speciality area; "
                "use 'empty' when the learner genuinely has none"
            )
        if self.speciality_status is SourceStatus.EMPTY and self.speciality_areas:
            raise ValueError("speciality_status 'empty' cannot carry speciality areas")
        if self.naric_level is not None and self.naric_level_source is None:
            raise ValueError("naric_level requires naric_level_source")
        return self


class LessonSummary(_Frozen):
    """ASSUMED BY US. Minimum lesson identity needed to validate a recommendation."""

    lesson_id: NonEmptyStr
    title: str | None = None
    topic_tags: tuple[NonEmptyStr, ...] = ()


class CourseSummary(_Frozen):
    """ASSUMED BY US. Catalogue entry used to validate recommendation identifiers."""

    course_id: NonEmptyStr
    title: str | None = None
    topic_tags: tuple[NonEmptyStr, ...] = ()
    lessons: tuple[LessonSummary, ...] = ()

    @property
    def lesson_ids(self) -> frozenset[str]:
        return frozenset(lesson.lesson_id for lesson in self.lessons)

    def lesson(self, lesson_id: str) -> LessonSummary | None:
        for lesson in self.lessons:
            if lesson.lesson_id == lesson_id:
                return lesson
        return None


class Enrolment(_Frozen):
    """ASSUMED BY US. The learner's existing enrolment in a course."""

    user_id: NonEmptyStr
    course_id: NonEmptyStr
    enrolled_at: datetime | None = None
    completion_percentage: int | None = Field(default=None, ge=0, le=100)

    @field_validator("enrolled_at")
    @classmethod
    def _utc_enrolled_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _as_utc(value)


class Recommendation(_Frozen):
    """ASSUMED BY US. A course- or lesson-level recommendation for one topic."""

    topic_tag: NonEmptyStr
    recommendation_type: RecommendationType
    course_id: NonEmptyStr
    lesson_id: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def _type_consistency(self) -> Recommendation:
        if self.recommendation_type is RecommendationType.LESSON and not self.lesson_id:
            raise ValueError("lesson recommendation requires lesson_id")
        if self.recommendation_type is RecommendationType.COURSE and self.lesson_id:
            raise ValueError("course recommendation must not carry lesson_id")
        return self

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.topic_tag, self.course_id, self.lesson_id or "")


# ---------------------------------------------------------------------------
# UC-07 owned report types
# ---------------------------------------------------------------------------


class SignalEvidence(_Frozen):
    """Why one signal fired: observed value, configured threshold, evidence ids."""

    signal: SignalKind
    observed_value: int = Field(ge=0)
    threshold: int = Field(ge=0)
    interaction_ids: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _must_have_fired(self) -> SignalEvidence:
        if self.observed_value < self.threshold:
            raise ValueError(
                "a signal may only be recorded when observed_value >= threshold"
            )
        return self


class GapEvidence(_Frozen):
    """Structured, mandatory evidence for a gap."""

    basis: EvidenceBasis
    interaction_ids: tuple[NonEmptyStr, ...] = ()
    per_signal: tuple[SignalEvidence, ...] = ()

    @model_validator(mode="after")
    def _basis_consistency(self) -> GapEvidence:
        if self.basis is EvidenceBasis.INTERACTION_IDS and not self.interaction_ids:
            raise ValueError("interaction-id evidence cannot be empty")
        if (
            self.basis is EvidenceBasis.ZERO_INTERACTIONS_FOR_SPECIALITY_AREA
            and self.interaction_ids
        ):
            raise ValueError("zero-interaction evidence must not carry interaction ids")
        if len(set(self.interaction_ids)) != len(self.interaction_ids):
            raise ValueError("evidence interaction ids must be unique")
        allowed = set(self.interaction_ids)
        for signal in self.per_signal:
            if set(signal.interaction_ids) - allowed:
                raise ValueError(
                    "per-signal evidence contains ids missing from the gap evidence set"
                )
        return self


class Gap(_Frozen):
    """One identified knowledge gap. Never emitted without resolvable evidence."""

    topic_tag: NonEmptyStr
    gap_type: GapType
    description: NonEmptyStr
    description_source: DescriptionSource
    signals: tuple[SignalKind, ...]
    evidence: GapEvidence
    recommendations: tuple[Recommendation, ...] = ()

    @model_validator(mode="after")
    def _consistency(self) -> Gap:
        if not self.signals:
            raise ValueError("a gap must carry at least one signal")
        if list(self.signals) != sorted(self.signals, key=SIGNAL_ORDER.index):
            raise ValueError("signals must use the canonical signal order")
        if len(set(self.signals)) != len(self.signals):
            raise ValueError("signals must not repeat")
        signalled = tuple(item.signal for item in self.evidence.per_signal)
        if signalled != self.signals:
            raise ValueError("signals must match the per-signal evidence exactly")
        if self.gap_type is GapType.STRUGGLE:
            if self.evidence.basis is not EvidenceBasis.INTERACTION_IDS:
                raise ValueError("struggle gaps require interaction-id evidence")
        else:
            if self.evidence.basis is EvidenceBasis.INTERACTION_IDS:
                raise ValueError("unexplored gaps cannot carry interaction-id evidence")
            if self.signals != (SignalKind.UNEXPLORED_SPECIALITY,):
                raise ValueError(
                    "unexplored gaps carry exactly the unexplored_speciality signal"
                )
        for recommendation in self.recommendations:
            if recommendation.topic_tag != self.topic_tag:
                raise ValueError("recommendation topic_tag must match the gap topic")
        return self

    @property
    def evidence_interaction_ids(self) -> tuple[str, ...]:
        return self.evidence.interaction_ids


class Notice(_Frozen):
    """A caveat attached to a report (degraded source, low diversity, ...)."""

    code: NoticeCode
    severity: NoticeSeverity
    message: NonEmptyStr


class SourceStatuses(_Frozen):
    """Per-source status. Statuses are preserved, never collapsed."""

    interactions: SourceStatus
    feedback: SourceStatus
    profile: SourceStatus
    courses: SourceStatus


class TopicCoverage(_Frozen):
    """Topic-diversity accounting; drives the minimum-three-topics notice."""

    identifiable_topic_areas: int = Field(ge=0)
    minimum_expected_topic_areas: int = Field(ge=0)
    sufficient_topic_diversity: bool
    topic_areas_in_history: int = Field(ge=0)


class UnexploredAnalysis(_Frozen):
    """Outcome of speciality-coverage analysis, including 'could not perform'."""

    state: UnexploredAnalysisState
    speciality_status: SourceStatus
    speciality_areas_considered: int = Field(ge=0)
    unexplored_areas_found: int = Field(ge=0)
    may_be_incomplete: bool = False
    explanation: NonEmptyStr


class RecommendationSummary(_Frozen):
    """Report-level recommendation status plus what validation discarded."""

    status: RecommendationStatus
    resolved_count: int = Field(ge=0)
    rejected_unresolvable_count: int = Field(ge=0)
    converted_to_lesson_count: int = Field(ge=0)
    dropped_already_enrolled_count: int = Field(default=0, ge=0)


class GapReport(_Frozen):
    """The only artefact UC-07 persists.

    ``user_id`` is internal ownership information: it is stored and checked, but
    never serialised into an API response.
    """

    report_id: NonEmptyStr
    user_id: NonEmptyStr
    generated_at: datetime
    threshold: int = Field(ge=0)
    source_interaction_count: int = Field(ge=0)
    report_version: NonEmptyStr
    analysis_version: NonEmptyStr
    gaps: tuple[Gap, ...]
    recommendations: RecommendationSummary
    source_statuses: SourceStatuses
    topic_coverage: TopicCoverage
    unexplored_analysis: UnexploredAnalysis
    notices: tuple[Notice, ...] = ()
    content_fingerprint: NonEmptyStr

    _utc_generated_at = field_validator("generated_at")(_as_utc)

    @property
    def recommendation_status(self) -> RecommendationStatus:
        return self.recommendations.status

    def content_payload(self) -> dict[str, Any]:
        """Everything that defines the report, minus identity/timestamp."""
        return self.model_dump(
            mode="json",
            exclude={"report_id", "generated_at", "content_fingerprint"},
        )

    @model_validator(mode="after")
    def _fingerprint_matches(self) -> GapReport:
        expected = fingerprint_of(self.content_payload())
        if expected != self.content_fingerprint:
            raise ValueError("content_fingerprint does not match report content")
        if self.report_id != report_id_for(self.content_fingerprint):
            raise ValueError("report_id must be derived from content_fingerprint")
        return self


def fingerprint_of(payload: dict[str, Any]) -> str:
    """Deterministic content fingerprint (stable across processes and runs)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def report_id_for(content_fingerprint: str) -> str:
    """Report ids are content-derived, which is what makes reports comparable."""
    return f"gr_{content_fingerprint[:32]}"


class ThresholdProgress(_Frozen):
    """Progress towards the report threshold. Never an error state."""

    status: ThresholdStatus
    interactions_completed: int = Field(ge=0)
    threshold: int = Field(ge=0)
    interactions_remaining: int = Field(ge=0)

    @model_validator(mode="after")
    def _derivations(self) -> ThresholdProgress:
        expected_remaining = max(0, self.threshold - self.interactions_completed)
        if self.interactions_remaining != expected_remaining:
            raise ValueError("interactions_remaining must be threshold - completed")
        expected_status = (
            ThresholdStatus.AVAILABLE
            if self.interactions_completed >= self.threshold
            else ThresholdStatus.BELOW_THRESHOLD
        )
        if self.status is not expected_status:
            raise ValueError("threshold status must follow the interaction count")
        return self
