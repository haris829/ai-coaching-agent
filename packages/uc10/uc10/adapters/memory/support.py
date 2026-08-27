"""Clock, policy configuration and the admin notification sink."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from uc10.config import Settings, get_settings
from uc10.domain.models import ContentReviewFlag
from uc10.logging_setup import get_logger

log = get_logger("uc10.adapters")


class SystemClock:
    """Clock over the wall clock, always UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Clock a test drives by hand. Used to place ratings inside and outside windows."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware start")
        self._now = start.astimezone(UTC)
        self._lock = threading.RLock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, **delta: float) -> datetime:
        with self._lock:
            self._now = self._now + timedelta(**delta)
            return self._now

    def set(self, moment: datetime) -> datetime:
        with self._lock:
            self._now = moment.astimezone(UTC)
            return self._now


class SettingsThresholdConfigProvider:
    """ThresholdConfigProvider backed by configuration, read at evaluation time.

    Every call re-reads settings, so an administrator changing the configured threshold
    changes flagging behaviour with no code change and no restart of the evaluation logic.
    """

    def __init__(self, settings_factory: Callable[[], Settings] = get_settings) -> None:
        self._settings_factory = settings_factory

    def down_rate_threshold(self) -> float:
        return self._settings_factory().flag_down_rate_threshold

    def minimum_sample_size(self) -> int:
        return self._settings_factory().flag_minimum_sample_size

    def window_days(self) -> int:
        return self._settings_factory().flag_window_days

    def historical_rating_window_hours(self) -> int:
        return self._settings_factory().historical_rating_window_hours


class StaticThresholdConfigProvider:
    """ThresholdConfigProvider with fixed values, for tests and the mock scenario table."""

    def __init__(
        self,
        *,
        down_rate_threshold: float,
        minimum_sample_size: int,
        window_days: int = 7,
        historical_rating_window_hours: int = 24,
    ) -> None:
        self._threshold = down_rate_threshold
        self._minimum = minimum_sample_size
        self._window_days = window_days
        self._historical_hours = historical_rating_window_hours

    def down_rate_threshold(self) -> float:
        return self._threshold

    def minimum_sample_size(self) -> int:
        return self._minimum

    def window_days(self) -> int:
        return self._window_days

    def historical_rating_window_hours(self) -> int:
        return self._historical_hours


class RecordingAdminNotificationSink:
    """AdminNotificationSink that records what the platform team would have been sent.

    The notification carries the flag itself: counts, rate, applied rule and interaction
    identifiers -- no question, response or comment text exists on it to leak.
    """

    def __init__(self) -> None:
        self.notified: list[ContentReviewFlag] = []

    def flag_created(self, flag: ContentReviewFlag) -> None:
        self.notified.append(flag)
        log.info(
            "admin_notified",
            flag_id=flag.flag_id,
            topic_tag=flag.topic_tag,
            down_rate=flag.down_rate,
            threshold_applied=flag.threshold_applied,
            total_ratings=flag.total_ratings,
        )
