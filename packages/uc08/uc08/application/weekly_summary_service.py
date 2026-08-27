"""The weekly summary.

Rules implemented here:

* Generated on the configured day (Monday, UTC) for the ISO week that just
  ended, containing four elements: topics covered last week, questions asked,
  current streak length, and a suggested topic from the gap report port.
* **Generated and logged whether sent or not.** The record is the deliverable;
  delivery is a side effect that happens afterwards and cannot prevent it.
* A send failure is logged and retried the following day (A-19): ``next_retry_at`` is
  set to the next UTC midnight, and any generation call after that retries the
  delivery instead of producing a new record.
* **Missed weeks are never batch-sent.** Only the week that just ended is ever
  generated. Weeks that went by without a generation call are listed on the new
  summary as ``skipped_weeks`` and are never produced retrospectively.
* Gap report unavailable -> the summary is generated without a suggested topic,
  the omission is named on the record, and nothing is invented.

Generation is an explicit callable operation. There is no scheduler, cron daemon
or background worker in this component; ``docs/INTEGRATION.md`` describes how a
caller drives it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from uc08.application.degradation import status_for_provider_error
from uc08.config import Weekday
from uc08.domain.enums import DeliveryStatus, SourceStatus
from uc08.domain.errors import NotificationSendFailed, ProviderError, RepositoryError
from uc08.domain.models import (
    Topic,
    WeeklySummary,
    WeeklySummaryEvent,
    WeeklySummaryRunResult,
)
from uc08.domain.time_utils import (
    ensure_utc,
    iso_week_key,
    previous_iso_week_bounds,
    start_of_utc_day,
)
from uc08.logging_setup import get_logger
from uc08.ports.clock import Clock
from uc08.ports.repositories import StreakRepository, WeeklySummaryRepository
from uc08.ports.sinks import NotificationSink
from uc08.ports.upstream import ActivityProvider, GapReportProvider

_log = get_logger(__name__)

#: Upper bound on the ``skipped_weeks`` list (A-20). A longer gap is reported in the
#: omission notes rather than silently truncated.
MAX_LISTED_SKIPPED_WEEKS = 52


class WeeklySummaryService:
    def __init__(
        self,
        *,
        clock: Clock,
        activity: ActivityProvider,
        gap_report: GapReportProvider,
        streaks: StreakRepository,
        summaries: WeeklySummaryRepository,
        notifications: NotificationSink,
        summary_day: Weekday,
    ) -> None:
        self._clock = clock
        self._activity = activity
        self._gap_report = gap_report
        self._streaks = streaks
        self._summaries = summaries
        self._notifications = notifications
        self._summary_day = summary_day

    # ----------------------------------------------------------------------
    # Reads
    # ----------------------------------------------------------------------
    def list_for_user(self, user_id: str) -> tuple[WeeklySummary, ...]:
        return self._summaries.list_for_user(user_id)

    def get(self, user_id: str, week: str) -> WeeklySummary | None:
        return self._summaries.get(user_id, week)

    # ----------------------------------------------------------------------
    # The explicit generation operation
    # ----------------------------------------------------------------------
    def generate(self, user_id: str) -> WeeklySummaryRunResult:
        now = self._clock.now()
        week_start, week_end = previous_iso_week_bounds(now)
        week = iso_week_key(week_start)
        is_summary_day = now.isoweekday() == self._summary_day.isoweekday

        existing = self._summaries.get(user_id, week)
        if existing is not None:
            retried = self._retry_if_due(existing, now)
            return WeeklySummaryRunResult(
                generated=None,
                already_generated=True,
                retried=retried,
                reason="summary already generated for this week",
            )

        if not is_summary_day:
            retried = self._retry_latest_if_due(user_id, now)
            return WeeklySummaryRunResult(
                generated=None,
                retried=retried,
                reason=(
                    f"today is not the configured summary day ({self._summary_day.value}); "
                    "no new summary was generated"
                ),
            )

        summary = self._build(user_id, now=now, week=week, week_start=week_start, week_end=week_end)
        self._summaries.save(summary)
        _log.info(
            "weekly_summary_generated",
            extra={
                "user_id": user_id,
                "week": summary.week,
                "summary_id": summary.summary_id,
                "topics_covered_count": len(summary.topics_covered),
                "topics_status": summary.topics_status.value,
                "questions_asked": summary.questions_asked,
                "current_streak_days": summary.current_streak_days,
                "suggested_topic_status": summary.suggested_topic_status.value,
                "omissions": list(summary.omissions),
                "skipped_weeks": list(summary.skipped_weeks),
                "delivery_status": summary.delivery_status.value,
            },
        )
        delivered = self._attempt_send(summary, now)
        return WeeklySummaryRunResult(
            generated=delivered,
            reason="generated",
            skipped_weeks=delivered.skipped_weeks,
        )

    # ----------------------------------------------------------------------
    # Building
    # ----------------------------------------------------------------------
    def _build(
        self,
        user_id: str,
        *,
        now: datetime,
        week: str,
        week_start: datetime,
        week_end: datetime,
    ) -> WeeklySummary:
        omissions: list[str] = []
        notes: list[str] = []

        topics, topics_status = self._topics(user_id, week_start, week_end)
        if topics_status in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
            omissions.append("topics_covered")
            notes.append("topics covered omitted: the activity read model did not answer")

        questions, questions_status = self._questions(user_id, week_start, week_end)
        if questions_status in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
            omissions.append("questions_asked")
            notes.append("questions asked omitted: the activity read model did not answer")

        streak_days = self._streak_days(user_id)

        suggestion, suggestion_status = self._suggestion(user_id)
        if suggestion_status in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
            omissions.append("suggested_topic")
            notes.append(
                "suggested topic omitted: the gap report was unavailable. No suggestion was invented."
            )
        elif suggestion_status is SourceStatus.EMPTY:
            omissions.append("suggested_topic")
            notes.append("suggested topic omitted: the gap report had no suggestion for this learner.")

        skipped, truncated_total = self._skipped_weeks(user_id, week, week_start)
        if truncated_total is not None:
            notes.append(f"{truncated_total} weeks went by without a generation call; the most recent are listed")

        return WeeklySummary(
            summary_id=f"ws-{user_id}-{week}",
            user_id=user_id,
            week=week,
            week_start_at=week_start,
            week_end_at=week_end,
            generated_at=now,
            topics_covered=topics,
            topics_status=topics_status,
            questions_asked=questions,
            questions_asked_status=questions_status,
            current_streak_days=streak_days,
            suggested_topic=suggestion,
            suggested_topic_status=suggestion_status,
            omissions=tuple(omissions),
            omission_notes=tuple(notes),
            delivery_status=DeliveryStatus.PENDING,
            skipped_weeks=skipped,
        )

    def _topics(
        self, user_id: str, week_start: datetime, week_end: datetime
    ) -> tuple[tuple[str, ...], SourceStatus]:
        try:
            read = self._activity.topics_in_window(user_id, week_start)
        except ProviderError as exc:
            status = status_for_provider_error(exc)
            _log.warning("weekly_summary_topics_degraded", extra={"user_id": user_id, "topics_status": status.value})
            return (), status
        names = tuple(
            mention.name for mention in read.topics if mention.first_mentioned_at < ensure_utc(week_end)
        )
        if not names:
            return (), SourceStatus.EMPTY
        status = SourceStatus.AVAILABLE if len(names) == len(read.topics) else SourceStatus.PARTIAL
        return names, status

    def _questions(
        self, user_id: str, week_start: datetime, week_end: datetime
    ) -> tuple[int, SourceStatus]:
        """Questions asked last week.

        One coaching interaction counts as one question asked (A-17): the
        activity port reports interactions, and the scope names questions.
        """
        try:
            read = self._activity.interactions_in_window(user_id, week_start)
        except ProviderError as exc:
            status = status_for_provider_error(exc)
            _log.warning(
                "weekly_summary_questions_degraded",
                extra={"user_id": user_id, "questions_asked_status": status.value},
            )
            return 0, status
        boundary = ensure_utc(week_end)
        in_week = [item for item in read.interactions if item.occurred_at < boundary]
        if not in_week:
            return 0, SourceStatus.EMPTY
        status = SourceStatus.AVAILABLE if len(in_week) == len(read.interactions) else SourceStatus.PARTIAL
        return len(in_week), status

    def _streak_days(self, user_id: str) -> int:
        try:
            streak = self._streaks.get(user_id)
        except RepositoryError:
            _log.error("weekly_summary_streak_read_failed", extra={"user_id": user_id}, exc_info=True)
            return 0
        return streak.current_streak_days if streak is not None else 0

    def _suggestion(self, user_id: str) -> tuple[Topic | None, SourceStatus]:
        try:
            topic = self._gap_report.suggested_topic(user_id)
        except ProviderError as exc:
            status = status_for_provider_error(exc)
            _log.warning(
                "weekly_summary_suggestion_degraded",
                extra={
                    "user_id": user_id,
                    "suggested_topic_status": status.value,
                    "invented_suggestion": False,
                },
            )
            return None, status
        if topic is None:
            return None, SourceStatus.EMPTY
        return topic, SourceStatus.AVAILABLE

    def _skipped_weeks(
        self, user_id: str, target_week: str, target_week_start: datetime
    ) -> tuple[tuple[str, ...], int | None]:
        """ISO weeks between the last generated summary and this one.

        These are named, not produced: UC-08 never generates a summary for a
        week retrospectively.
        """
        try:
            history = self._summaries.list_for_user(user_id)
        except RepositoryError:
            _log.error("weekly_summary_history_read_failed", extra={"user_id": user_id}, exc_info=True)
            return (), None
        if not history:
            return (), None

        latest = max(history, key=lambda item: item.week_start_at)
        cursor = ensure_utc(latest.week_start_at) + timedelta(days=7)
        gaps: list[str] = []
        while cursor < ensure_utc(target_week_start):
            key = iso_week_key(cursor)
            if key != target_week:
                gaps.append(key)
            cursor = cursor + timedelta(days=7)

        if len(gaps) > MAX_LISTED_SKIPPED_WEEKS:
            return tuple(gaps[-MAX_LISTED_SKIPPED_WEEKS:]), len(gaps)
        return tuple(gaps), None

    # ----------------------------------------------------------------------
    # Delivery
    # ----------------------------------------------------------------------
    def _attempt_send(self, summary: WeeklySummary, now: datetime) -> WeeklySummary:
        attempt = summary.model_copy(
            update={"send_attempts": summary.send_attempts + 1, "last_send_attempt_at": now}
        )
        event = WeeklySummaryEvent(
            event_id=f"evt-{attempt.summary_id}-{attempt.send_attempts}",
            user_id=attempt.user_id,
            summary_id=attempt.summary_id,
            week=attempt.week,
            occurred_at=now,
            summary=attempt,
        )
        try:
            self._notifications.weekly_summary(event)
        except NotificationSendFailed:
            retry_at = start_of_utc_day(now) + timedelta(days=1)
            delivered = attempt.model_copy(
                update={"delivery_status": DeliveryStatus.FAILED, "next_retry_at": retry_at}
            )
            _log.warning(
                "weekly_summary_send_failed",
                extra={
                    "user_id": delivered.user_id,
                    "week": delivered.week,
                    "summary_id": delivered.summary_id,
                    "send_attempts": delivered.send_attempts,
                    "next_retry_at": retry_at.isoformat(),
                    "record_retained": True,
                },
            )
        else:
            delivered = attempt.model_copy(
                update={
                    "delivery_status": DeliveryStatus.SENT,
                    "sent_at": now,
                    "next_retry_at": None,
                }
            )
            _log.info(
                "weekly_summary_sent",
                extra={
                    "user_id": delivered.user_id,
                    "week": delivered.week,
                    "summary_id": delivered.summary_id,
                    "send_attempts": delivered.send_attempts,
                },
            )
        self._summaries.save(delivered)
        return delivered

    def _retry_if_due(self, summary: WeeklySummary, now: datetime) -> WeeklySummary | None:
        if summary.delivery_status is DeliveryStatus.SENT:
            return None
        if summary.next_retry_at is not None and now < ensure_utc(summary.next_retry_at):
            return None
        return self._attempt_send(summary, now)

    def _retry_latest_if_due(self, user_id: str, now: datetime) -> WeeklySummary | None:
        """Retry only the most recent summary.

        Older undelivered summaries are left alone for good: a learner returning
        after a month gets one summary, not four.
        """
        history = self._summaries.list_for_user(user_id)
        if not history:
            return None
        latest = max(history, key=lambda item: item.week_start_at)
        return self._retry_if_due(latest, now)
