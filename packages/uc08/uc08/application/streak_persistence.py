"""Streak persistence with the critical failure rule.

Write fails -> log and retry once. Retry fails -> preserve the last known
streak count and alert engineering. Never reset.

This module is deliberately tiny and deliberately separate. It is the only code
that catches a persistence exception on the streak path, and everything it can
return is either the record it was asked to save or the record that was already
authoritative. It does not import ``uc08.domain.streak_rules``, so the reset
builder is not in scope here; there is no expression it could evaluate that
lowers a streak count.
"""

from __future__ import annotations

from dataclasses import dataclass

from uc08.domain.enums import PersistenceOutcome
from uc08.domain.errors import RepositoryWriteFailed
from uc08.domain.models import StreakRecord, StreakWriteIncident
from uc08.logging_setup import get_logger
from uc08.ports.clock import Clock
from uc08.ports.repositories import StreakRepository
from uc08.ports.sinks import EngineeringAlertSink

_log = get_logger(__name__)

#: One write, then exactly one retry. Not configurable: the scope fixes it.
MAX_WRITE_ATTEMPTS = 2


@dataclass(frozen=True)
class PersistResult:
    """What is authoritative after the write attempt."""

    outcome: PersistenceOutcome
    #: The record a caller should now report. On failure this is the last known
    #: record, unchanged.
    record: StreakRecord
    committed: bool
    incident: StreakWriteIncident | None = None


class StreakWriter:
    def __init__(self, *, repository: StreakRepository, alerts: EngineeringAlertSink, clock: Clock) -> None:
        self._repository = repository
        self._alerts = alerts
        self._clock = clock

    def persist(self, intended: StreakRecord, *, last_known: StreakRecord | None) -> PersistResult:
        """Save ``intended``, retrying once, and never reset.

        ``last_known`` is the record read at the start of the operation. When
        both attempts fail it is what the caller keeps -- byte for byte, count
        included.
        """
        failures: list[BaseException] = []
        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            try:
                self._repository.save(intended)
            except RepositoryWriteFailed as exc:
                failures.append(exc)
                _log.warning(
                    "streak_write_failed",
                    extra={
                        "user_id": intended.user_id,
                        "attempt": attempt,
                        "max_attempts": MAX_WRITE_ATTEMPTS,
                        "intended_streak_days": intended.current_streak_days,
                        "last_known_streak_days": (
                            last_known.current_streak_days if last_known is not None else None
                        ),
                        "error_type": type(exc).__name__,
                        "will_retry": attempt < MAX_WRITE_ATTEMPTS,
                    },
                )
                continue
            outcome = PersistenceOutcome.SAVED if attempt == 1 else PersistenceOutcome.SAVED_ON_RETRY
            return PersistResult(outcome=outcome, record=intended, committed=True)

        return self._preserve(intended, last_known, failures[-1])

    def _preserve(
        self,
        intended: StreakRecord,
        last_known: StreakRecord | None,
        error: BaseException,
    ) -> PersistResult:
        """Keep what was already true and page engineering.

        The only two records this can return are ``last_known`` (when the
        account had one) and ``intended`` (when it did not, so there is nothing
        older to keep -- A-25). Neither is a reset: ``intended`` was produced by the
        rules module before any failure occurred, and this function does not
        construct records at all.
        """
        now = self._clock.now()
        preserved = last_known if last_known is not None else intended
        incident = StreakWriteIncident(
            incident_id=f"inc-streak-write-{intended.user_id}-{now.strftime('%Y%m%dT%H%M%S%f')}Z",
            user_id=intended.user_id,
            occurred_at=now,
            attempts=MAX_WRITE_ATTEMPTS,
            preserved_streak_days=preserved.current_streak_days,
            preserved_longest_streak_days=preserved.longest_streak_days,
            intended_streak_days=intended.current_streak_days,
            error_type=type(error).__name__,
            error_detail=str(error),
        )
        _log.error(
            "streak_write_preserved_last_known",
            extra={
                "user_id": intended.user_id,
                "attempts": MAX_WRITE_ATTEMPTS,
                "preserved_streak_days": incident.preserved_streak_days,
                "intended_streak_days": incident.intended_streak_days,
                "had_previous_record": last_known is not None,
                "reset_applied": False,
            },
        )
        try:
            self._alerts.streak_write_failed(incident)
        except Exception:
            # A broken alert channel must not turn a persistence problem into a
            # failed coaching request. Nothing about the streak changes here.
            _log.error("engineering_alert_sink_failed", extra={"user_id": intended.user_id}, exc_info=True)
        return PersistResult(
            outcome=PersistenceOutcome.PRESERVED_LAST_KNOWN,
            record=preserved,
            committed=False,
            incident=incident,
        )
