"""Content review flagging.

Reads its entire policy -- threshold, minimum sample size, window length -- through
:class:`~uc10.ports.threshold_config_provider.ThresholdConfigProvider` on every
evaluation.  No numeric policy value appears anywhere in this file.
"""

from __future__ import annotations

from datetime import datetime

from uc10.application.results import CycleReport, FlagEvaluationResult, FlagWriteStatus
from uc10.domain.enums import FlagStatus
from uc10.domain.flag_work import FlagWorkItem
from uc10.domain.flagging import (
    FlagCandidate,
    FlagDecision,
    FlaggingPolicy,
    evaluate_topic,
    topics_in,
)
from uc10.domain.ids import new_flag_id
from uc10.domain.models import ContentReviewFlag
from uc10.domain.window import Window
from uc10.logging_setup import get_logger
from uc10.ports.admin_notification_sink import AdminNotificationSink
from uc10.ports.clock import Clock
from uc10.ports.errors import PortError, RecordNotFound
from uc10.ports.flag_repository import FlagRepository
from uc10.ports.flag_work_queue import FlagWorkQueue
from uc10.ports.rating_repository import RatingRepository
from uc10.ports.threshold_config_provider import ThresholdConfigProvider

log = get_logger("uc10.flagging")

#: ASSUMED BY US (A-17): permitted admin status transitions.
ALLOWED_TRANSITIONS: dict[FlagStatus, frozenset[FlagStatus]] = {
    FlagStatus.OPEN: frozenset({FlagStatus.REVIEWED, FlagStatus.CONFIRMED, FlagStatus.CORRECTED}),
    FlagStatus.REVIEWED: frozenset({FlagStatus.CONFIRMED, FlagStatus.CORRECTED}),
    FlagStatus.CONFIRMED: frozenset({FlagStatus.CORRECTED}),
    FlagStatus.CORRECTED: frozenset(),
}


class InvalidStatusTransition(Exception):
    def __init__(self, current: FlagStatus, requested: FlagStatus) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"cannot move a flag from {current.value} to {requested.value}")


class FlagNotFound(Exception):
    pass


class FlaggingService:
    def __init__(
        self,
        *,
        ratings: RatingRepository,
        flags: FlagRepository,
        work_queue: FlagWorkQueue,
        notifications: AdminNotificationSink,
        config: ThresholdConfigProvider,
        clock: Clock,
    ) -> None:
        self._ratings = ratings
        self._flags = flags
        self._queue = work_queue
        self._notifications = notifications
        self._config = config
        self._clock = clock

    # ----------------------------------------------------------------- policy

    def current_policy(self) -> FlaggingPolicy:
        """Read the rule in force *now*. Called once per evaluation, never cached."""
        return FlaggingPolicy(
            down_rate_threshold=self._config.down_rate_threshold(),
            minimum_sample_size=self._config.minimum_sample_size(),
        )

    def current_window(self) -> Window:
        return Window.rolling(self._clock.now(), self._config.window_days())

    # ------------------------------------------------------------- evaluation

    def evaluate_topic(self, topic_tag: str) -> FlagEvaluationResult:
        """Evaluate one topic across all users over the rolling window."""
        window = self.current_window()
        policy = self.current_policy()
        ratings = self._ratings.current_in_window(window.start, window.end)
        decision = evaluate_topic(
            topic_tag=topic_tag,
            ratings=ratings,
            window=window,
            policy=policy,
            evaluated_at=self._clock.now(),
        )
        self._log_decision(decision, window, policy)
        if decision.candidate is None:
            return FlagEvaluationResult(
                topic_tag=topic_tag,
                decision=decision,
                write_status=FlagWriteStatus.NOT_REQUIRED,
                reason_code=decision.reason.value,
            )

        item = self._record_intent(decision.candidate)
        return self._attempt_write(item, decision=decision)

    def run_cycle(self, *, topics: list[str] | None = None) -> CycleReport:
        """One evaluation cycle: retry every deferred flag, then evaluate every topic.

        Retries run first, so a flag whose write failed is never waiting on a fresh
        rating to arrive before it is attempted again.
        """
        created: list[str] = []
        updated: list[str] = []
        deferred: list[str] = []
        retried: list[str] = []
        results: list[FlagEvaluationResult] = []

        for item in self._queue.pending():
            retried.append(item.work_id)
            result = self._retry(item)
            results.append(result)
            self._tally(result, created, updated, deferred)

        window = self.current_window()
        if topics is None:
            topics = topics_in(self._ratings.current_in_window(window.start, window.end))
        for topic in topics:
            if any(r.topic_tag == topic for r in results):
                continue  # already handled by its retry this cycle
            result = self.evaluate_topic(topic)
            results.append(result)
            self._tally(result, created, updated, deferred)

        report = CycleReport(
            evaluated_topics=tuple(topics),
            created=tuple(created),
            updated=tuple(updated),
            deferred=tuple(deferred),
            retried=tuple(retried),
            results=results,
        )
        log.info(
            "flag_cycle_completed",
            evaluated=len(report.evaluated_topics),
            created=len(report.created),
            updated=len(report.updated),
            deferred=len(report.deferred),
            retried=len(report.retried),
        )
        return report

    # --------------------------------------------------------------- internals

    @staticmethod
    def _log_decision(decision: FlagDecision, window: Window, policy: FlaggingPolicy) -> None:
        log.info(
            "flag_evaluated",
            topic_tag=decision.topic_tag,
            total_ratings=decision.total_ratings,
            down_ratings=decision.down_ratings,
            down_rate=decision.down_rate,
            threshold_applied=policy.down_rate_threshold,
            minimum_sample_size_applied=policy.minimum_sample_size,
            window_start=window.start.isoformat(),
            window_end=window.end.isoformat(),
            decision=decision.reason.value,
        )

    @staticmethod
    def _tally(
        result: FlagEvaluationResult,
        created: list[str],
        updated: list[str],
        deferred: list[str],
    ) -> None:
        if result.write_status is FlagWriteStatus.CREATED and result.flag is not None:
            created.append(result.flag.flag_id)
        elif result.write_status is FlagWriteStatus.UPDATED and result.flag is not None:
            updated.append(result.flag.flag_id)
        elif result.write_status is FlagWriteStatus.DEFERRED and result.work_id is not None:
            deferred.append(result.work_id)

    def _retry(self, item: FlagWorkItem) -> FlagEvaluationResult:
        """Retry a deferred flag.

        The intent is re-evaluated so the flag carries current counts.  If the topic no
        longer meets the rule, the *recorded* candidate is written anyway: the rule did
        fire, and a flag that has been decided is never silently dropped.
        """
        window = self.current_window()
        fresh = evaluate_topic(
            topic_tag=item.candidate.topic_tag,
            ratings=self._ratings.current_in_window(window.start, window.end),
            window=window,
            policy=self.current_policy(),
            evaluated_at=self._clock.now(),
        )
        if fresh.candidate is not None:
            item = self._queue.update_candidate(item.work_id, fresh.candidate)
        else:
            log.info(
                "flag_retry_using_recorded_candidate",
                work_id=item.work_id,
                topic_tag=item.candidate.topic_tag,
                decision=fresh.reason.value,
                attempts=item.attempts,
            )
        return self._attempt_write(item, decision=fresh)

    def _record_intent(self, candidate: FlagCandidate) -> FlagWorkItem:
        """Persist the intent to flag BEFORE any write is attempted."""
        existing = self._queue.pending_for_topic(candidate.topic_tag)
        if existing is not None:
            return self._queue.update_candidate(existing.work_id, candidate)
        item = self._queue.enqueue(candidate)
        log.info(
            "flag_intent_recorded",
            work_id=item.work_id,
            topic_tag=candidate.topic_tag,
            down_ratings=candidate.down_ratings,
            total_ratings=candidate.total_ratings,
        )
        return item

    def _attempt_write(
        self, item: FlagWorkItem, *, decision: FlagDecision
    ) -> FlagEvaluationResult:
        candidate = item.candidate
        try:
            existing = self._flags.open_flag_for(candidate.topic_tag, candidate.window)
            if existing is not None:
                flag = self._flags.update(self._merge(existing, candidate))
                status = FlagWriteStatus.UPDATED
            else:
                flag = self._flags.save(self._build(candidate))
                status = FlagWriteStatus.CREATED
        except PortError as exc:
            self._queue.mark_failed(item.work_id, exc.reason_code)
            log.warning(
                "flag_write_deferred",
                work_id=item.work_id,
                topic_tag=candidate.topic_tag,
                reason_code=exc.reason_code,
                attempts=item.attempts + 1,
                retry="next_cycle",
            )
            return FlagEvaluationResult(
                topic_tag=candidate.topic_tag,
                decision=decision,
                write_status=FlagWriteStatus.DEFERRED,
                work_id=item.work_id,
                reason_code=exc.reason_code,
            )

        # The intent is resolved only after the repository has confirmed the write.
        try:
            self._queue.resolve(item.work_id, flag.flag_id)
        except PortError as exc:
            # The flag exists; only the bookkeeping failed. The intent stays pending and
            # the next cycle finds the open flag and updates it. Nothing is duplicated
            # and nothing is lost.
            log.warning(
                "flag_intent_resolution_deferred",
                work_id=item.work_id,
                flag_id=flag.flag_id,
                reason_code=exc.reason_code,
                retry="next_cycle",
            )
        log.info(
            "flag_created" if status is FlagWriteStatus.CREATED else "flag_updated",
            flag_id=flag.flag_id,
            topic_tag=flag.topic_tag,
            total_ratings=flag.total_ratings,
            down_ratings=flag.down_ratings,
            down_rate=flag.down_rate,
            threshold_applied=flag.threshold_applied,
            minimum_sample_size_applied=flag.minimum_sample_size_applied,
            flagging_interaction_count=len(flag.flagging_interaction_ids),
        )
        if status is FlagWriteStatus.CREATED:
            self._notify(flag)
        return FlagEvaluationResult(
            topic_tag=flag.topic_tag,
            decision=decision,
            write_status=status,
            flag=flag,
            work_id=item.work_id,
        )

    def _build(self, candidate: FlagCandidate) -> ContentReviewFlag:
        return ContentReviewFlag(
            flag_id=new_flag_id(),
            topic_tag=candidate.topic_tag,
            window_start=candidate.window.start,
            window_end=candidate.window.end,
            total_ratings=candidate.total_ratings,
            down_ratings=candidate.down_ratings,
            down_rate=candidate.down_rate,
            threshold_applied=candidate.threshold_applied,
            flagging_interaction_ids=candidate.flagging_interaction_ids,
            created_at=candidate.evaluated_at,
            status=FlagStatus.OPEN,
            minimum_sample_size_applied=candidate.minimum_sample_size_applied,
        )

    def _merge(self, existing: ContentReviewFlag, candidate: FlagCandidate) -> ContentReviewFlag:
        """Update an open flag in place rather than raising a duplicate."""
        merged_ids = tuple(
            dict.fromkeys(existing.flagging_interaction_ids + candidate.flagging_interaction_ids)
        )
        return existing.model_copy(
            update={
                "window_start": candidate.window.start,
                "window_end": candidate.window.end,
                "total_ratings": candidate.total_ratings,
                "down_ratings": candidate.down_ratings,
                "down_rate": candidate.down_rate,
                "threshold_applied": candidate.threshold_applied,
                "minimum_sample_size_applied": candidate.minimum_sample_size_applied,
                "flagging_interaction_ids": merged_ids,
                "updated_at": candidate.evaluated_at,
            }
        )

    def _notify(self, flag: ContentReviewFlag) -> None:
        """A notification failure must not lose a flag that is already persisted."""
        try:
            self._notifications.flag_created(flag)
        except PortError as exc:
            log.warning(
                "flag_notification_failed",
                flag_id=flag.flag_id,
                topic_tag=flag.topic_tag,
                reason_code=exc.reason_code,
            )

    # ------------------------------------------------------------------ admin

    def list_open_flags(self) -> list[ContentReviewFlag]:
        return self._flags.list_open()

    def set_status(self, flag_id: str, status: FlagStatus) -> ContentReviewFlag:
        try:
            flag = self._flags.get(flag_id)
        except RecordNotFound as exc:
            raise FlagNotFound(flag_id) from exc
        if status is flag.status:
            return flag  # idempotent
        if status not in ALLOWED_TRANSITIONS[flag.status]:
            raise InvalidStatusTransition(flag.status, status)
        updated = self._flags.update(
            flag.model_copy(update={"status": status, "updated_at": self._now()})
        )
        log.info(
            "flag_status_changed",
            flag_id=updated.flag_id,
            topic_tag=updated.topic_tag,
            previous_status=flag.status.value,
            status=updated.status.value,
        )
        return updated

    def pending_flag_work(self) -> list[FlagWorkItem]:
        return self._queue.pending()

    def _now(self) -> datetime:
        return self._clock.now()
