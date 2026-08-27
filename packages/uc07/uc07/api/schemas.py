"""API response schemas.

The report schema deliberately omits ``user_id``: ownership is internal state,
checked server-side and never echoed back. Gaps expose both the flat
``evidence_interaction_ids`` list and the structured ``evidence`` object.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from uc07.domain.models import Gap, GapReport, ThresholdProgress


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SignalEvidenceOut(_Out):
    signal: str
    observed_value: int
    threshold: int
    interaction_ids: list[str]


class GapEvidenceOut(_Out):
    basis: str
    interaction_ids: list[str]
    per_signal: list[SignalEvidenceOut]


class RecommendationOut(_Out):
    topic_tag: str
    recommendation_type: str
    course_id: str
    lesson_id: str | None
    title: str | None


class GapOut(_Out):
    topic_tag: str
    gap_type: str
    description: str
    description_source: str
    signals: list[str]
    evidence_interaction_ids: list[str]
    evidence: GapEvidenceOut
    recommendations: list[RecommendationOut]


class RecommendationSummaryOut(_Out):
    status: str
    resolved_count: int
    rejected_unresolvable_count: int
    converted_to_lesson_count: int
    dropped_already_enrolled_count: int


class SourceStatusesOut(_Out):
    interactions: str
    feedback: str
    profile: str
    courses: str


class TopicCoverageOut(_Out):
    identifiable_topic_areas: int
    minimum_expected_topic_areas: int
    sufficient_topic_diversity: bool
    topic_areas_in_history: int


class UnexploredAnalysisOut(_Out):
    state: str
    speciality_status: str
    speciality_areas_considered: int
    unexplored_areas_found: int
    may_be_incomplete: bool
    explanation: str


class NoticeOut(_Out):
    code: str
    severity: str
    message: str


class GapReportOut(_Out):
    report_id: str
    generated_at: datetime
    threshold: int
    source_interaction_count: int
    report_version: str
    analysis_version: str
    gaps: list[GapOut]
    recommendations: RecommendationSummaryOut
    source_statuses: SourceStatusesOut
    topic_coverage: TopicCoverageOut
    unexplored_analysis: UnexploredAnalysisOut
    notices: list[NoticeOut]
    content_fingerprint: str


class ProgressOut(_Out):
    status: str
    interactions_completed: int
    threshold: int
    interactions_remaining: int


class GapReportEnvelopeOut(_Out):
    status: str
    interactions_completed: int
    threshold: int
    interactions_remaining: int
    report: GapReportOut | None = None


class ErrorBody(_Out):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorEnvelopeOut(_Out):
    error: ErrorBody


class HealthOut(_Out):
    status: str
    report_version: str
    analysis_version: str
    threshold: int


# ---------------------------------------------------------------------------
# Mapping helpers (domain -> API). Explicit, so nothing leaks by accident.
# ---------------------------------------------------------------------------


def gap_out(gap: Gap) -> GapOut:
    return GapOut(
        topic_tag=gap.topic_tag,
        gap_type=gap.gap_type.value,
        description=gap.description,
        description_source=gap.description_source.value,
        signals=[signal.value for signal in gap.signals],
        evidence_interaction_ids=list(gap.evidence_interaction_ids),
        evidence=GapEvidenceOut(
            basis=gap.evidence.basis.value,
            interaction_ids=list(gap.evidence.interaction_ids),
            per_signal=[
                SignalEvidenceOut(
                    signal=item.signal.value,
                    observed_value=item.observed_value,
                    threshold=item.threshold,
                    interaction_ids=list(item.interaction_ids),
                )
                for item in gap.evidence.per_signal
            ],
        ),
        recommendations=[
            RecommendationOut(
                topic_tag=rec.topic_tag,
                recommendation_type=rec.recommendation_type.value,
                course_id=rec.course_id,
                lesson_id=rec.lesson_id,
                title=rec.title,
            )
            for rec in gap.recommendations
        ],
    )


def report_out(report: GapReport) -> GapReportOut:
    """Serialise a report for the API. ``user_id`` is intentionally dropped."""
    return GapReportOut(
        report_id=report.report_id,
        generated_at=report.generated_at,
        threshold=report.threshold,
        source_interaction_count=report.source_interaction_count,
        report_version=report.report_version,
        analysis_version=report.analysis_version,
        gaps=[gap_out(gap) for gap in report.gaps],
        recommendations=RecommendationSummaryOut(
            status=report.recommendations.status.value,
            resolved_count=report.recommendations.resolved_count,
            rejected_unresolvable_count=report.recommendations.rejected_unresolvable_count,
            converted_to_lesson_count=report.recommendations.converted_to_lesson_count,
            dropped_already_enrolled_count=(
                report.recommendations.dropped_already_enrolled_count
            ),
        ),
        source_statuses=SourceStatusesOut(
            interactions=report.source_statuses.interactions.value,
            feedback=report.source_statuses.feedback.value,
            profile=report.source_statuses.profile.value,
            courses=report.source_statuses.courses.value,
        ),
        topic_coverage=TopicCoverageOut(
            identifiable_topic_areas=report.topic_coverage.identifiable_topic_areas,
            minimum_expected_topic_areas=(
                report.topic_coverage.minimum_expected_topic_areas
            ),
            sufficient_topic_diversity=report.topic_coverage.sufficient_topic_diversity,
            topic_areas_in_history=report.topic_coverage.topic_areas_in_history,
        ),
        unexplored_analysis=UnexploredAnalysisOut(
            state=report.unexplored_analysis.state.value,
            speciality_status=report.unexplored_analysis.speciality_status.value,
            speciality_areas_considered=(
                report.unexplored_analysis.speciality_areas_considered
            ),
            unexplored_areas_found=report.unexplored_analysis.unexplored_areas_found,
            may_be_incomplete=report.unexplored_analysis.may_be_incomplete,
            explanation=report.unexplored_analysis.explanation,
        ),
        notices=[
            NoticeOut(
                code=notice.code.value,
                severity=notice.severity.value,
                message=notice.message,
            )
            for notice in report.notices
        ],
        content_fingerprint=report.content_fingerprint,
    )


def progress_out(progress: ThresholdProgress) -> ProgressOut:
    return ProgressOut(
        status=progress.status.value,
        interactions_completed=progress.interactions_completed,
        threshold=progress.threshold,
        interactions_remaining=progress.interactions_remaining,
    )
