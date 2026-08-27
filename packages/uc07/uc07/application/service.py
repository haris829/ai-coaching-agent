"""The UC-07 application service.

Responsibilities:

1. Evaluate the qualifying-interaction threshold against the **current** source
   data on every request (never against a stored snapshot).
2. Aggregate the learner's complete history, derive evidence-backed struggle
   gaps and unexplored-speciality gaps, validate recommendations, assemble a
   deterministic report.
3. Persist the report through the only write-capable port and keep the current
   report fresh: identical source state returns the stored report unchanged,
   changed source state produces (and stores) a refreshed one.

Provider failures are handled by exception *type*. Bare ``except Exception`` is
never used, and a failed source is never presented as an empty one.
"""

from __future__ import annotations

from dataclasses import dataclass

from uc07.application.aggregation import aggregate_history
from uc07.application.config import AnalysisThresholds
from uc07.application.recommendations import (
    CoursesLoad,
    RecommendationPlan,
    validate_recommendations,
)
from uc07.application.report_builder import assemble_report
from uc07.application.signals import (
    build_low_rating_index,
    detect_struggles,
)
from uc07.application.topic_descriptions import TopicDescriptionRegistry
from uc07.application.unexplored import ProfileLoad, analyse_unexplored
from uc07.domain.counting import qualifying_interactions
from uc07.domain.enums import SignalKind, SourceStatus, ThresholdStatus
from uc07.domain.errors import (
    InteractionSourceUnusable,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    ReportOwnershipError,
)
from uc07.domain.models import (
    FeedbackRecord,
    GapReport,
    InteractionRecord,
    SourceStatuses,
    ThresholdProgress,
)
from uc07.observability import log_event
from uc07.ports import (
    Clock,
    CoursesProvider,
    FeedbackProvider,
    GapReportRepository,
    InteractionLogProvider,
    LearnerProfileProvider,
)


@dataclass(frozen=True, slots=True)
class InteractionLoad:
    records: tuple[InteractionRecord, ...]
    status: SourceStatus
    provider_reported_count: int | None


@dataclass(frozen=True, slots=True)
class FeedbackLoad:
    records: tuple[FeedbackRecord, ...] | None
    status: SourceStatus


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """What the API returns: always progress, plus a report when available."""

    progress: ThresholdProgress
    report: GapReport | None
    refreshed: bool

    @property
    def status(self) -> ThresholdStatus:
        return self.progress.status


class GapReportService:
    def __init__(
        self,
        *,
        interactions: InteractionLogProvider,
        feedback: FeedbackProvider,
        profiles: LearnerProfileProvider,
        courses: CoursesProvider,
        repository: GapReportRepository,
        clock: Clock,
        descriptions: TopicDescriptionRegistry,
        thresholds: AnalysisThresholds,
    ) -> None:
        self._interactions = interactions
        self._feedback = feedback
        self._profiles = profiles
        self._courses = courses
        self._repository = repository
        self._clock = clock
        self._descriptions = descriptions
        self._thresholds = thresholds

    # -- public API --------------------------------------------------------

    def progress(self, user_id: str) -> ThresholdProgress:
        """Threshold progress from current source data. Never an error state."""
        load = self._load_interactions(user_id)
        qualifying = qualifying_interactions(load.records, user_id=user_id)
        progress = self._progress_for(qualifying.count)
        log_event(
            "progress_evaluated",
            user_id=user_id,
            interaction_count=qualifying.count,
            provider_reported_count=load.provider_reported_count,
            duplicates_discarded=qualifying.duplicates_discarded,
            other_user_records_discarded=qualifying.other_user_records_discarded,
            threshold=progress.threshold,
            threshold_status=progress.status.value,
            interactions_completed=progress.interactions_completed,
            interactions_remaining=progress.interactions_remaining,
            source_status_interactions=load.status.value,
        )
        return progress

    def current_report(self, user_id: str) -> ReportOutcome:
        """Return the learner's current report, refreshing it if sources changed."""
        load = self._load_interactions(user_id)
        qualifying = qualifying_interactions(load.records, user_id=user_id)
        progress = self._progress_for(qualifying.count)

        if progress.status is ThresholdStatus.BELOW_THRESHOLD:
            log_event(
                "report_below_threshold",
                user_id=user_id,
                interaction_count=qualifying.count,
                threshold=progress.threshold,
                threshold_status=progress.status.value,
                interactions_remaining=progress.interactions_remaining,
                source_status_interactions=load.status.value,
            )
            return ReportOutcome(progress=progress, report=None, refreshed=False)

        history = aggregate_history(qualifying, user_id=user_id)

        feedback_load = self._load_feedback(sorted(history.interaction_ids))
        low_ratings = build_low_rating_index(
            history,
            None if feedback_load.records is None else list(feedback_load.records),
        )
        struggles = detect_struggles(history, self._thresholds, low_ratings)

        profile_load = self._load_profile(user_id)
        unexplored = analyse_unexplored(profile_load, history.topic_tags)

        gap_topics = tuple(
            sorted({finding.topic_tag for finding in struggles})
        ) + tuple(unexplored.unexplored_areas)
        courses_load = self._load_courses(gap_topics, user_id=user_id)
        plan = self._plan_recommendations(courses_load, gap_topics, user_id=user_id)

        statuses = SourceStatuses(
            interactions=load.status,
            feedback=feedback_load.status,
            profile=profile_load.status,
            courses=courses_load.status,
        )

        assembly = assemble_report(
            user_id=user_id,
            history=history,
            thresholds=self._thresholds,
            struggles=struggles,
            unexplored=unexplored,
            plan=plan,
            registry=self._descriptions,
            source_statuses=statuses,
            generated_at=self._clock.now(),
        )
        fresh = assembly.report

        stored = self._repository.get_current(user_id)
        if stored is not None and stored.user_id != user_id:
            raise ReportOwnershipError(
                "stored report does not belong to the resolved user"
            )

        if stored is not None and stored.content_fingerprint == fresh.content_fingerprint:
            report, refreshed = stored, False
        else:
            self._repository.save(fresh)
            report, refreshed = fresh, True

        signal_counts = {
            kind: sum(
                1 for gap in report.gaps for signal in gap.signals if signal is kind
            )
            for kind in (
                SignalKind.EXPLAIN_DIFFERENTLY,
                SignalKind.FOLLOW_UP,
                SignalKind.LOW_RATING,
            )
        }
        log_event(
            "report_available",
            user_id=user_id,
            report_id=report.report_id,
            interaction_count=report.source_interaction_count,
            threshold=report.threshold,
            threshold_status=progress.status.value,
            session_count=history.session_count,
            topic_area_count=len(history.topics),
            gap_count=len(report.gaps),
            struggle_gap_count=len(struggles),
            unexplored_gap_count=unexplored.analysis.unexplored_areas_found,
            signal_count_explain_differently=signal_counts[SignalKind.EXPLAIN_DIFFERENTLY],
            signal_count_follow_up=signal_counts[SignalKind.FOLLOW_UP],
            signal_count_low_rating=signal_counts[SignalKind.LOW_RATING],
            rejected_gap_count=assembly.rejected_gap_count,
            recommendation_count=report.recommendations.resolved_count,
            recommendations_rejected_count=(
                report.recommendations.rejected_unresolvable_count
            ),
            recommendation_status=report.recommendations.status.value,
            unexplored_analysis_state=report.unexplored_analysis.state.value,
            source_status_interactions=statuses.interactions.value,
            source_status_feedback=statuses.feedback.value,
            source_status_profile=statuses.profile.value,
            source_status_courses=statuses.courses.value,
            report_refreshed=refreshed,
        )
        return ReportOutcome(progress=progress, report=report, refreshed=refreshed)

    # -- source loading (typed error handling only) -------------------------

    def _progress_for(self, count: int) -> ThresholdProgress:
        threshold = self._thresholds.gap_report_threshold
        return ThresholdProgress(
            status=(
                ThresholdStatus.AVAILABLE
                if count >= threshold
                else ThresholdStatus.BELOW_THRESHOLD
            ),
            interactions_completed=count,
            threshold=threshold,
            interactions_remaining=max(0, threshold - count),
        )

    def _load_interactions(self, user_id: str) -> InteractionLoad:
        try:
            records = tuple(self._interactions.for_user(user_id))
            reported_status = self._interactions.status_for_user(user_id)
        except (ProviderUnavailable, ProviderTimeout) as exc:
            log_event(
                "interaction_source_failed",
                user_id=user_id,
                port=exc.port.value,
                error_code=exc.code,
                source_status_interactions=SourceStatus.UNAVAILABLE.value,
            )
            raise InteractionSourceUnusable(SourceStatus.UNAVAILABLE.value) from exc
        except ProviderInvalidResponse as exc:
            log_event(
                "interaction_source_failed",
                user_id=user_id,
                port=exc.port.value,
                error_code=exc.code,
                source_status_interactions=SourceStatus.INVALID.value,
            )
            raise InteractionSourceUnusable(SourceStatus.INVALID.value) from exc

        if reported_status in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
            raise InteractionSourceUnusable(reported_status.value)

        provider_count: int | None
        try:
            provider_count = self._interactions.count_for_user(user_id)
        except (ProviderUnavailable, ProviderTimeout, ProviderInvalidResponse):
            # Observability only; never a threshold decision.
            provider_count = None

        if reported_status is SourceStatus.PARTIAL:
            effective = SourceStatus.PARTIAL
        elif not records:
            effective = SourceStatus.EMPTY
        else:
            effective = SourceStatus.AVAILABLE

        return InteractionLoad(
            records=records, status=effective, provider_reported_count=provider_count
        )

    def _load_feedback(self, interaction_ids: list[str]) -> FeedbackLoad:
        try:
            records = tuple(self._feedback.for_interactions(interaction_ids))
            reported = self._feedback.status_for_interactions(interaction_ids)
        except (ProviderUnavailable, ProviderTimeout) as exc:
            log_event(
                "feedback_source_failed",
                port=exc.port.value,
                error_code=exc.code,
                source_status_feedback=SourceStatus.UNAVAILABLE.value,
            )
            return FeedbackLoad(records=None, status=SourceStatus.UNAVAILABLE)
        except ProviderInvalidResponse as exc:
            log_event(
                "feedback_source_failed",
                port=exc.port.value,
                error_code=exc.code,
                source_status_feedback=SourceStatus.INVALID.value,
            )
            return FeedbackLoad(records=None, status=SourceStatus.INVALID)

        if reported in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
            return FeedbackLoad(records=None, status=reported)
        if reported is SourceStatus.PARTIAL:
            return FeedbackLoad(records=records, status=SourceStatus.PARTIAL)
        if not records:
            return FeedbackLoad(records=records, status=SourceStatus.EMPTY)
        return FeedbackLoad(records=records, status=SourceStatus.AVAILABLE)

    def _load_profile(self, user_id: str) -> ProfileLoad:
        try:
            profile = self._profiles.get_profile(user_id)
        except (ProviderUnavailable, ProviderTimeout) as exc:
            log_event(
                "profile_source_failed",
                user_id=user_id,
                port=exc.port.value,
                error_code=exc.code,
                source_status_profile=SourceStatus.UNAVAILABLE.value,
            )
            return ProfileLoad.failed(SourceStatus.UNAVAILABLE)
        except ProviderInvalidResponse as exc:
            log_event(
                "profile_source_failed",
                user_id=user_id,
                port=exc.port.value,
                error_code=exc.code,
                source_status_profile=SourceStatus.INVALID.value,
            )
            return ProfileLoad.failed(SourceStatus.INVALID)
        return ProfileLoad.loaded(profile)

    def _load_courses(self, gap_topics: tuple[str, ...], *, user_id: str) -> CoursesLoad:
        try:
            reported = self._courses.status()
            if reported in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
                return CoursesLoad.failed(reported)
            candidates = tuple(self._courses.resolve_recommendations(gap_topics))
            enrolments = tuple(self._courses.enrolments_for(user_id))
            catalogue = tuple(self._courses.catalogue())
        except (ProviderUnavailable, ProviderTimeout) as exc:
            log_event(
                "courses_source_failed",
                port=exc.port.value,
                error_code=exc.code,
                source_status_courses=SourceStatus.UNAVAILABLE.value,
            )
            return CoursesLoad.failed(SourceStatus.UNAVAILABLE)
        except ProviderInvalidResponse as exc:
            log_event(
                "courses_source_failed",
                port=exc.port.value,
                error_code=exc.code,
                source_status_courses=SourceStatus.INVALID.value,
            )
            return CoursesLoad.failed(SourceStatus.INVALID)
        return CoursesLoad(
            status=reported,
            candidates=candidates,
            enrolments=enrolments,
            catalogue=catalogue,
        )

    def _plan_recommendations(
        self, load: CoursesLoad, gap_topics: tuple[str, ...], *, user_id: str
    ) -> RecommendationPlan:
        return validate_recommendations(load, gap_topics, user_id=user_id)
