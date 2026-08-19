"""Server-authoritative timing.

Every value here derives from timestamps the server itself wrote (``started_at``,
``expires_at``) and the server's own clock. No client-supplied time ever participates
in the calculation, so a learner cannot extend an attempt by changing their device
clock. A client may *report* its clock for diagnostics; the response then carries an
advisory skew, but the authoritative remaining time is unaffected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.time import Clock, iso_or_none, parse_instant, to_iso
from app.modules.attempt_delivery.domain.enums import AttemptStatus
from app.modules.attempt_delivery.models import QuizAttempt


@dataclass(frozen=True, slots=True)
class AttemptTiming:
    """The timing payload returned to clients."""

    #: Authoritative "now" from the server.
    server_time: str
    server_time_epoch_ms: int

    status: str
    started_at: str
    #: Hard deadline, or ``None`` for an untimed attempt.
    expires_at: str | None
    #: Configured limit from the locked configuration, or ``None`` if untimed.
    time_limit_seconds: int | None
    timed: bool

    #: Seconds consumed. Frozen at submission once the attempt is committed.
    elapsed_seconds: int
    #: Seconds left, floored at 0. ``None`` for an untimed attempt.
    remaining_seconds: int | None
    #: True when a timed attempt's deadline has passed.
    expired: bool

    submitted_at: str | None

    #: Client should resync if its clock differs from ``server_time`` by more.
    clock_resync_threshold_seconds: int
    #: Cadence at which the client is expected to autosave.
    autosave_interval_seconds: int

    #: Present only when the caller reported its own clock. Advisory only.
    reported_client_skew_seconds: int | None = None

    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "serverTime": self.server_time,
            "serverTimeEpochMs": self.server_time_epoch_ms,
            "status": self.status,
            "startedAt": self.started_at,
            "expiresAt": self.expires_at,
            "timeLimitSeconds": self.time_limit_seconds,
            "timed": self.timed,
            "elapsedSeconds": self.elapsed_seconds,
            "remainingSeconds": self.remaining_seconds,
            "expired": self.expired,
            "submittedAt": self.submitted_at,
            "clockResyncThresholdSeconds": self.clock_resync_threshold_seconds,
            "autosaveIntervalSeconds": self.autosave_interval_seconds,
        }
        if self.reported_client_skew_seconds is not None:
            payload["reportedClientSkewSeconds"] = self.reported_client_skew_seconds
        payload.update(self.extra)
        return payload


class TimingService:
    """Computes and enforces attempt timing from the server clock alone."""

    __slots__ = ("_clock", "_settings")

    def __init__(self, clock: Clock, settings: Settings) -> None:
        self._clock = clock
        self._settings = settings

    def now(self) -> datetime:
        return self._clock.now()

    def compute_expiry(
        self, started_at: datetime, time_limit_seconds: int | None
    ) -> datetime | None:
        """The hard deadline for a new attempt, or ``None`` when untimed."""
        if time_limit_seconds is None:
            return None
        return started_at + timedelta(seconds=time_limit_seconds)

    def is_expired(self, attempt: QuizAttempt, now: datetime | None = None) -> bool:
        """True when the deadline has passed, including any configured grace period.

        The grace period lets operators absorb network latency for in-flight
        autosaves; it defaults to zero, which is what the specification requires.
        """
        if attempt.expires_at is None:
            return False
        moment = now or self._clock.now()
        grace = timedelta(seconds=self._settings.submission_grace_seconds)
        return moment >= attempt.expires_at + grace

    def has_reached_deadline(self, attempt: QuizAttempt, now: datetime | None = None) -> bool:
        """The deadline ignoring the grace period — what is reported to clients."""
        if attempt.expires_at is None:
            return False
        return (now or self._clock.now()) >= attempt.expires_at

    def compute(self, attempt: QuizAttempt, *, client_time: str | None = None) -> AttemptTiming:
        """Build the full timing payload.

        Gives the client everything it needs to run and resync a countdown without
        ever being trusted to measure it.
        """
        now = self._clock.now()

        # Once committed, elapsed time freezes at the moment of commitment, so the
        # record does not keep growing after the learner has finished.
        if attempt.status == str(AttemptStatus.ACTIVE) or attempt.submitted_at is None:
            reference = now
        else:
            reference = min(now, attempt.submitted_at)

        elapsed = max(0, int((reference - attempt.started_at).total_seconds()))

        remaining: int | None = None
        if attempt.expires_at is not None:
            if attempt.status == str(AttemptStatus.ACTIVE):
                remaining = max(0, math.ceil((attempt.expires_at - reference).total_seconds()))
            else:
                # A committed attempt has no runway left to offer.
                remaining = 0

        skew: int | None = None
        if client_time is not None:
            # Purely informational, so a client can detect it must resync. Never fed
            # back into remaining_seconds.
            try:
                skew = round((parse_instant(client_time) - now).total_seconds())
            except ValueError:
                skew = None

        return AttemptTiming(
            server_time=to_iso(now),
            server_time_epoch_ms=int(now.timestamp() * 1000),
            status=attempt.status,
            started_at=to_iso(attempt.started_at),
            expires_at=iso_or_none(attempt.expires_at),
            time_limit_seconds=attempt.time_limit_seconds,
            timed=attempt.expires_at is not None,
            elapsed_seconds=elapsed,
            remaining_seconds=remaining,
            expired=self.has_reached_deadline(attempt, now),
            submitted_at=iso_or_none(attempt.submitted_at),
            clock_resync_threshold_seconds=self._settings.clock_resync_threshold_seconds,
            autosave_interval_seconds=self._settings.autosave_interval_seconds,
            reported_client_skew_seconds=skew,
        )
