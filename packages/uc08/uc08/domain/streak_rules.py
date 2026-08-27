"""The streak arithmetic. Pure functions, no clock, no I/O, no exceptions caught.

This module is the **only** place in the component that can produce a reset.

The reset builder, :func:`apply_reset`, requires an :class:`InactivityEvidence`
value, and ``InactivityEvidence`` can only be constructed with zero prior
qualifying interactions in the window -- it validates that in ``__post_init__``.
:func:`decide` is the only function that constructs one. So a reset is
reachable only from a genuine inactivity determination, and an exception
handler elsewhere in the codebase cannot manufacture the argument it would need
to call the reset path. ``tests/architecture/test_no_reset_from_exception.py``
asserts that no ``except`` block anywhere in ``uc08/`` even mentions these
names.

The once-per-day rule, stated precisely
---------------------------------------
Given the persisted streak record and the moment ``now`` of the coaching
interaction being recorded:

1. If there is no streak record, the streak **starts** at 1.
2. Otherwise, if ``streak.last_activity_at`` falls on the **same UTC calendar
   day** as ``now``, the count is **unchanged**: this day has already been
   counted. The record still records the newer ``last_activity_at``. Twelve
   questions in an afternoon are one day, not twelve.
3. Otherwise, if at least one *prior* interaction (any interaction other than
   the one being recorded) occurred within the trailing
   ``STREAK_WINDOW_HOURS`` window -- that is, in ``[now - 24h, now)`` -- the
   count **increments by exactly one**.
4. Otherwise the determination is genuine inactivity: **reset**, subject to the
   freeze offer.

Step 3 is a rolling 24-hour window, not a "yesterday" calendar comparison. The
two rules disagree at the boundary and the specification says 24 hours: activity
23h59m ago increments, activity 24h01m ago resets, whichever calendar days
those moments happen to fall on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from uc08.domain.enums import StreakOutcome
from uc08.domain.models import FreezeOffer, StreakRecord
from uc08.domain.time_utils import ensure_utc, same_utc_day, same_utc_month


@dataclass(frozen=True)
class InactivityEvidence:
    """Proof that a genuine inactivity determination was made.

    Constructing this is the *only* way to reach :func:`apply_reset`. It cannot
    be constructed unless the window genuinely contained no prior qualifying
    interaction, so no error-handling path can fabricate one.
    """

    user_id: str
    evaluated_at: datetime
    window_start: datetime
    window_hours: int
    prior_interactions_in_window: int
    last_counted_activity_at: datetime | None

    def __post_init__(self) -> None:
        if self.prior_interactions_in_window != 0:
            raise ValueError(
                "InactivityEvidence requires zero prior qualifying interactions; "
                f"got {self.prior_interactions_in_window}"
            )
        if self.window_hours <= 0:
            raise ValueError("window_hours must be positive")


@dataclass(frozen=True)
class StreakDecision:
    """What to do to the streak count, and the evidence for a reset."""

    outcome: StreakOutcome
    inactivity_evidence: InactivityEvidence | None = None
    window_start: datetime | None = None
    prior_interactions_in_window: int = 0


def window_start_for(now: datetime, window_hours: int) -> datetime:
    return ensure_utc(now) - timedelta(hours=window_hours)


def decide(
    *,
    user_id: str,
    streak: StreakRecord | None,
    now: datetime,
    prior_interactions_in_window: int,
    window_hours: int,
) -> StreakDecision:
    """Classify a coaching interaction. See the module docstring for the rule.

    ``prior_interactions_in_window`` counts interactions in ``[now - window,
    now)`` **excluding the interaction being recorded**. Excluding it matters:
    an activity read model that already shows the current interaction would
    otherwise make every interaction look like continuous activity, and a
    learner returning after a month would never reset.
    """
    moment = ensure_utc(now)
    window_start = window_start_for(moment, window_hours)

    if streak is None:
        return StreakDecision(
            StreakOutcome.STARTED,
            window_start=window_start,
            prior_interactions_in_window=prior_interactions_in_window,
        )

    if streak.last_activity_at is not None and same_utc_day(streak.last_activity_at, moment):
        return StreakDecision(
            StreakOutcome.UNCHANGED_SAME_DAY,
            window_start=window_start,
            prior_interactions_in_window=prior_interactions_in_window,
        )

    if prior_interactions_in_window > 0:
        return StreakDecision(
            StreakOutcome.INCREMENTED,
            window_start=window_start,
            prior_interactions_in_window=prior_interactions_in_window,
        )

    evidence = InactivityEvidence(
        user_id=user_id,
        evaluated_at=moment,
        window_start=window_start,
        window_hours=window_hours,
        prior_interactions_in_window=prior_interactions_in_window,
        last_counted_activity_at=streak.last_activity_at,
    )
    return StreakDecision(
        StreakOutcome.RESET,
        inactivity_evidence=evidence,
        window_start=window_start,
        prior_interactions_in_window=prior_interactions_in_window,
    )


# --------------------------------------------------------------------------
# Builders. Each returns a new record; nothing mutates in place.
# --------------------------------------------------------------------------
def freeze_available_at(now: datetime, freeze_used_at: datetime | None) -> bool:
    """Whether a freeze may be *used* now: none used this UTC calendar month.

    The calendar is UTC (A-11). An offer that was declined or that expired does
    not consume the monthly allowance -- only an accepted freeze does (A-13).
    """
    if freeze_used_at is None:
        return True
    return not same_utc_month(freeze_used_at, now)


def apply_start(*, user_id: str, now: datetime) -> StreakRecord:
    """First ever activity for this account."""
    moment = ensure_utc(now)
    return StreakRecord(
        user_id=user_id,
        current_streak_days=1,
        longest_streak_days=1,
        last_activity_at=moment,
        streak_started_at=moment,
        freeze_available=True,
        freeze_used_at=None,
        updated_at=moment,
    )


def _touch(streak: StreakRecord, now: datetime) -> StreakRecord:
    """Record that activity happened, leaving every count alone."""
    moment = ensure_utc(now)
    return streak.model_copy(
        update={
            "last_activity_at": moment,
            "updated_at": moment,
            "freeze_available": freeze_available_at(moment, streak.freeze_used_at),
        }
    )


def apply_same_day(streak: StreakRecord, now: datetime) -> StreakRecord:
    """Record the activity without touching the count (once-per-day rule)."""
    return _touch(streak, now)


def apply_preserve_on_degraded_source(streak: StreakRecord, now: datetime) -> StreakRecord:
    """Preserve the count when the activity read model could not be consulted.

    An outage is a system problem. Neither this function nor any caller of it
    can reduce the count: it is :func:`_touch`, and the reset builder is not
    reachable from here.
    """
    return _touch(streak, now)


def apply_increment(streak: StreakRecord, now: datetime) -> StreakRecord:
    """Continue the streak by exactly one day."""
    moment = ensure_utc(now)
    new_count = streak.current_streak_days + 1
    return streak.model_copy(
        update={
            "current_streak_days": new_count,
            "longest_streak_days": max(streak.longest_streak_days, new_count),
            "last_activity_at": moment,
            "streak_started_at": streak.streak_started_at or moment,
            "updated_at": moment,
            "freeze_available": freeze_available_at(moment, streak.freeze_used_at),
        }
    )


def apply_reset(streak: StreakRecord, now: datetime, evidence: InactivityEvidence) -> StreakRecord:
    """Start a new streak at 1 after a genuine inactivity determination.

    ``evidence`` is required and unforgeable-by-accident: see the module
    docstring. ``longest_streak_days`` is carried forward -- a reset never
    destroys the record of what the learner achieved.
    """
    if evidence.user_id != streak.user_id:
        raise ValueError("inactivity evidence does not belong to this streak record")
    moment = ensure_utc(now)
    return streak.model_copy(
        update={
            "current_streak_days": 1,
            "longest_streak_days": max(streak.longest_streak_days, streak.current_streak_days),
            "last_activity_at": moment,
            "streak_started_at": moment,
            "updated_at": moment,
            "freeze_available": freeze_available_at(moment, streak.freeze_used_at),
        }
    )


def apply_freeze_acceptance(streak: StreakRecord, offer: FreezeOffer, now: datetime) -> StreakRecord:
    """Undo the reset the offer was created alongside (A-18).

    The restored count is ``preserved_streak_days + current_streak_days``: the
    streak the learner held before the missed day, plus the days they have been
    active since. Accepting immediately after returning restores 7 -> 8.
    """
    moment = ensure_utc(now)
    restored = offer.preserved_streak_days + streak.current_streak_days
    return streak.model_copy(
        update={
            "current_streak_days": restored,
            "longest_streak_days": max(streak.longest_streak_days, restored),
            "streak_started_at": offer.preserved_streak_started_at or streak.streak_started_at,
            "freeze_available": False,
            "freeze_used_at": moment,
            "updated_at": moment,
        }
    )


def eligible_for_freeze_offer(
    streak: StreakRecord,
    *,
    now: datetime,
    min_streak_days: int,
) -> bool:
    """Whether a missed day should be met with a freeze offer.

    The streak being tested is the one held *before* the reset.
    """
    return streak.current_streak_days >= min_streak_days and freeze_available_at(now, streak.freeze_used_at)
