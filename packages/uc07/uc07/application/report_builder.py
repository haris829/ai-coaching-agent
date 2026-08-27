"""Report assembly.

Deterministic by construction:

* gaps are ordered struggle-first then unexplored, each block sorted by topic tag;
* signals follow the canonical signal order and evidence ids are sorted;
* notices are emitted in a fixed source order;
* ``content_fingerprint`` is a sha256 over the canonical JSON of the report
  content, and ``report_id`` is derived from that fingerprint - so identical
  inputs and configuration produce an identical report, id included.
"""

from __future__ import annotations

from datetime import datetime

from uc07 import ANALYSIS_VERSION, REPORT_VERSION
from uc07.application.aggregation import HistoryAggregate
from uc07.application.config import AnalysisThresholds
from uc07.application.evidence_guard import enforce_evidence_integrity
from uc07.application.recommendations import RecommendationPlan
from uc07.application.signals import StruggleFinding
from uc07.application.topic_descriptions import TopicDescriptionRegistry
from uc07.application.unexplored import UnexploredOutcome
from uc07.domain.enums import (
    EvidenceBasis,
    GapType,
    NoticeCode,
    NoticeSeverity,
    RecommendationStatus,
    SignalKind,
    SourceStatus,
    UnexploredAnalysisState,
)
from uc07.domain.models import (
    Gap,
    GapEvidence,
    GapReport,
    Notice,
    SignalEvidence,
    SourceStatuses,
    TopicCoverage,
    fingerprint_of,
    report_id_for,
)


class ReportAssembly:
    """Result of assembling a report, including guard diagnostics."""

    __slots__ = ("report", "rejected_gap_count", "rejection_reasons")

    def __init__(
        self,
        report: GapReport,
        rejected_gap_count: int,
        rejection_reasons: tuple[str, ...],
    ) -> None:
        self.report = report
        self.rejected_gap_count = rejected_gap_count
        self.rejection_reasons = rejection_reasons


def _struggle_gaps(
    findings: tuple[StruggleFinding, ...],
    registry: TopicDescriptionRegistry,
    plan: RecommendationPlan,
) -> list[Gap]:
    gaps: list[Gap] = []
    for finding in sorted(findings, key=lambda item: item.topic_tag):
        description, source = registry.describe(finding.topic_tag)
        gaps.append(
            Gap(
                topic_tag=finding.topic_tag,
                gap_type=GapType.STRUGGLE,
                description=description,
                description_source=source,
                signals=finding.signal_kinds,
                evidence=GapEvidence(
                    basis=EvidenceBasis.INTERACTION_IDS,
                    interaction_ids=finding.evidence_interaction_ids,
                    per_signal=finding.signals,
                ),
                recommendations=plan.for_topic(finding.topic_tag),
            )
        )
    return gaps


def _unexplored_gaps(
    areas: tuple[str, ...],
    registry: TopicDescriptionRegistry,
    plan: RecommendationPlan,
) -> list[Gap]:
    gaps: list[Gap] = []
    for area in sorted(areas):
        description, source = registry.describe(area)
        gaps.append(
            Gap(
                topic_tag=area,
                gap_type=GapType.UNEXPLORED,
                description=description,
                description_source=source,
                signals=(SignalKind.UNEXPLORED_SPECIALITY,),
                evidence=GapEvidence(
                    basis=EvidenceBasis.ZERO_INTERACTIONS_FOR_SPECIALITY_AREA,
                    interaction_ids=(),
                    per_signal=(
                        SignalEvidence(
                            signal=SignalKind.UNEXPLORED_SPECIALITY,
                            observed_value=0,
                            threshold=0,
                            interaction_ids=(),
                        ),
                    ),
                ),
                recommendations=plan.for_topic(area),
            )
        )
    return gaps


def _notices(
    statuses: SourceStatuses,
    unexplored_state: UnexploredAnalysisState,
    recommendation_status: RecommendationStatus,
    coverage: TopicCoverage,
) -> tuple[Notice, ...]:
    notices: list[Notice] = []

    if statuses.interactions is SourceStatus.PARTIAL:
        notices.append(
            Notice(
                code=NoticeCode.INTERACTION_SOURCE_PARTIAL,
                severity=NoticeSeverity.WARNING,
                message=(
                    "Interaction history was returned as partial: this analysis may "
                    "not cover the learner's complete coaching history."
                ),
            )
        )

    feedback_notice = {
        SourceStatus.UNAVAILABLE: (
            NoticeCode.RATING_SIGNAL_UNAVAILABLE,
            NoticeSeverity.WARNING,
            "The rating source was unavailable, so the low-rating signal could not "
            "be evaluated. Struggle analysis used the remaining signals.",
        ),
        SourceStatus.INVALID: (
            NoticeCode.RATING_SIGNAL_INVALID,
            NoticeSeverity.WARNING,
            "The rating source returned data that does not satisfy the platform "
            "contract, so the low-rating signal could not be evaluated.",
        ),
        SourceStatus.PARTIAL: (
            NoticeCode.RATING_SIGNAL_PARTIAL,
            NoticeSeverity.WARNING,
            "Rating data was partial, so low-rating analysis may be incomplete.",
        ),
        SourceStatus.EMPTY: (
            NoticeCode.RATING_SIGNAL_NO_RATINGS,
            NoticeSeverity.INFO,
            "The learner has no ratings recorded, so the low-rating signal produced "
            "no evidence. This is an empty source, not a failed one.",
        ),
    }.get(statuses.feedback)
    if feedback_notice is not None:
        code, severity, message = feedback_notice
        notices.append(Notice(code=code, severity=severity, message=message))

    speciality_notice = {
        UnexploredAnalysisState.NOT_PERFORMED_PROFILE_UNAVAILABLE: (
            NoticeCode.SPECIALITY_ANALYSIS_UNAVAILABLE,
            NoticeSeverity.WARNING,
            "Speciality-based unexplored analysis was unavailable because the "
            "learner profile source could not be read. No speciality areas were "
            "inferred.",
        ),
        UnexploredAnalysisState.NOT_PERFORMED_PROFILE_INVALID: (
            NoticeCode.SPECIALITY_ANALYSIS_INVALID,
            NoticeSeverity.WARNING,
            "Speciality-based unexplored analysis could not be performed because "
            "the profile source returned data outside the platform contract.",
        ),
        UnexploredAnalysisState.NOT_PERFORMED_NO_SPECIALITY: (
            NoticeCode.SPECIALITY_ANALYSIS_NOT_POSSIBLE_NO_SPECIALITY,
            NoticeSeverity.INFO,
            "Unexplored-speciality analysis could not be performed: no speciality "
            "areas are set for this learner and none were inferred.",
        ),
        UnexploredAnalysisState.PERFORMED_PARTIAL: (
            NoticeCode.SPECIALITY_ANALYSIS_PARTIAL,
            NoticeSeverity.WARNING,
            "Speciality data was partial, so unexplored-speciality analysis may be "
            "incomplete.",
        ),
    }.get(unexplored_state)
    if speciality_notice is not None:
        code, severity, message = speciality_notice
        notices.append(Notice(code=code, severity=severity, message=message))

    if recommendation_status is RecommendationStatus.UNAVAILABLE:
        notices.append(
            Notice(
                code=NoticeCode.RECOMMENDATIONS_TEMPORARILY_UNAVAILABLE,
                severity=NoticeSeverity.WARNING,
                message=(
                    "Course recommendations are temporarily unavailable. The "
                    "knowledge gaps below are unaffected and remain evidence-backed."
                ),
            )
        )
    elif recommendation_status is RecommendationStatus.PARTIAL:
        notices.append(
            Notice(
                code=NoticeCode.RECOMMENDATIONS_PARTIAL,
                severity=NoticeSeverity.WARNING,
                message=(
                    "Course data was partial, so the recommendations below may be "
                    "incomplete."
                ),
            )
        )

    if not coverage.sufficient_topic_diversity:
        notices.append(
            Notice(
                code=NoticeCode.INSUFFICIENT_TOPIC_DIVERSITY,
                severity=NoticeSeverity.INFO,
                message=(
                    "This learner's coaching history covers "
                    f"{coverage.topic_areas_in_history} topic area(s), fewer than the "
                    f"{coverage.minimum_expected_topic_areas} needed for a fuller "
                    "picture. No additional gaps were invented to reach that number; "
                    "broader coaching activity across more topics is recommended."
                ),
            )
        )

    return tuple(notices)


def assemble_report(
    *,
    user_id: str,
    history: HistoryAggregate,
    thresholds: AnalysisThresholds,
    struggles: tuple[StruggleFinding, ...],
    unexplored: UnexploredOutcome,
    plan: RecommendationPlan,
    registry: TopicDescriptionRegistry,
    source_statuses: SourceStatuses,
    generated_at: datetime,
) -> ReportAssembly:
    """Assemble a deterministic, evidence-guarded gap report."""
    candidate_gaps = _struggle_gaps(struggles, registry, plan) + _unexplored_gaps(
        unexplored.unexplored_areas, registry, plan
    )

    guard = enforce_evidence_integrity(candidate_gaps, history.interaction_ids)
    gaps = guard.gaps

    coverage = TopicCoverage(
        identifiable_topic_areas=len({gap.topic_tag for gap in gaps}),
        minimum_expected_topic_areas=thresholds.min_topic_areas,
        sufficient_topic_diversity=len(history.topics) >= thresholds.min_topic_areas,
        topic_areas_in_history=len(history.topics),
    )

    notices = _notices(
        source_statuses, unexplored.analysis.state, plan.summary.status, coverage
    )

    content = {
        "user_id": user_id,
        "threshold": thresholds.gap_report_threshold,
        "source_interaction_count": history.interaction_count,
        "report_version": REPORT_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "gaps": [gap.model_dump(mode="json") for gap in gaps],
        "recommendations": plan.summary.model_dump(mode="json"),
        "source_statuses": source_statuses.model_dump(mode="json"),
        "topic_coverage": coverage.model_dump(mode="json"),
        "unexplored_analysis": unexplored.analysis.model_dump(mode="json"),
        "notices": [notice.model_dump(mode="json") for notice in notices],
    }
    fingerprint = fingerprint_of(content)

    report = GapReport(
        report_id=report_id_for(fingerprint),
        user_id=user_id,
        generated_at=generated_at,
        threshold=thresholds.gap_report_threshold,
        source_interaction_count=history.interaction_count,
        report_version=REPORT_VERSION,
        analysis_version=ANALYSIS_VERSION,
        gaps=gaps,
        recommendations=plan.summary,
        source_statuses=source_statuses,
        topic_coverage=coverage,
        unexplored_analysis=unexplored.analysis,
        notices=notices,
        content_fingerprint=fingerprint,
    )
    return ReportAssembly(
        report=report,
        rejected_gap_count=guard.rejected_gap_count,
        rejection_reasons=guard.rejection_reasons,
    )
