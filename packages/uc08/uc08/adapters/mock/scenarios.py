"""The mock scenario matrix, built deliberately.

Every scenario named in the scope document appears here as a builder taking a
clock and returning a configured adapter. Positions are relative to
``clock.now()`` at build time, so a fake clock places the data exactly.

The ``23h59m`` / ``24h01m`` pair and the ``9 / 10 / 11 / 49 / 50 / 99 / 100 /
150`` counts exist for boundary testing and are wired here rather than improvised
inside individual tests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta

from uc08.adapters.mock.activity import MockActivityProvider
from uc08.adapters.mock.gap_report import GapReportPlan, MockGapReportProvider
from uc08.adapters.mock.ledger import ActivityLedger, Fault
from uc08.ports.clock import Clock
from uc08.ports.conformance import CONFORMANCE_USER_ID
from uc08.ports.upstream import ActivityProvider, GapReportProvider

# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------
QUESTION_COUNT_SCENARIOS: tuple[int, ...] = (9, 10, 11, 49, 50, 99, 100, 150)


def activity_ledger_at_offset(
    clock: Clock,
    *,
    user_id: str = CONFORMANCE_USER_ID,
    hours: int = 0,
    minutes: int = 0,
    question_count: int = 1,
    topic: str = "professional-conduct",
) -> ActivityLedger:
    """A ledger with one prior interaction ``hours``/``minutes`` before now."""
    ledger = ActivityLedger()
    ledger.add_interaction(
        user_id,
        clock.now() - timedelta(hours=hours, minutes=minutes),
        f"prior-{hours}h{minutes:02d}m",
        topic=topic,
    )
    ledger.set_question_count(user_id, question_count)
    return ledger


def _provider(clock: Clock, ledger: ActivityLedger) -> ActivityProvider:
    return MockActivityProvider(clock, ledger)


def activity_23h59m_ago(clock: Clock) -> ActivityProvider:
    """Inside the 24-hour window by one minute. Must increment."""
    return _provider(clock, activity_ledger_at_offset(clock, hours=23, minutes=59))


def activity_24h01m_ago(clock: Clock) -> ActivityProvider:
    """Outside the 24-hour window by one minute. Must reset."""
    return _provider(clock, activity_ledger_at_offset(clock, hours=24, minutes=1))


def multiple_interactions_same_day(clock: Clock) -> ActivityProvider:
    """Twelve questions in one afternoon: one day of streak, not twelve."""
    ledger = ActivityLedger()
    for index in range(12):
        ledger.add_interaction(
            CONFORMANCE_USER_ID,
            clock.now() - timedelta(minutes=5 * (index + 1)),
            f"same-day-{index}",
            topic="wills-and-probate",
        )
    ledger.set_question_count(CONFORMANCE_USER_ID, 12)
    return _provider(clock, ledger)


def no_activity(clock: Clock) -> ActivityProvider:
    """The read model answered, and the learner has no interactions at all."""
    ledger = ActivityLedger()
    ledger.set_question_count(CONFORMANCE_USER_ID, None)
    return _provider(clock, ledger)


def activity_unavailable(clock: Clock) -> ActivityProvider:
    return _provider(clock, ActivityLedger().with_fault(Fault.UNAVAILABLE))


def activity_timeout(clock: Clock) -> ActivityProvider:
    return _provider(clock, ActivityLedger().with_fault(Fault.TIMEOUT))


def activity_invalid(clock: Clock) -> ActivityProvider:
    return _provider(clock, ActivityLedger().with_fault(Fault.INVALID))


def activity_with_question_count(count: int) -> Callable[[Clock], ActivityProvider]:
    def build(clock: Clock) -> ActivityProvider:
        return _provider(clock, activity_ledger_at_offset(clock, hours=1, question_count=count))

    return build


MOCK_ACTIVITY_SCENARIOS: Mapping[str, Callable[[Clock], ActivityProvider]] = {
    # Conformance-required states
    "available": activity_23h59m_ago,
    "empty": no_activity,
    "unavailable": activity_unavailable,
    "timeout": activity_timeout,
    "invalid": activity_invalid,
    # Scope-named behavioural scenarios
    "activity_23h59m_ago": activity_23h59m_ago,
    "activity_24h01m_ago": activity_24h01m_ago,
    "multiple_interactions_same_day": multiple_interactions_same_day,
    "no_activity": no_activity,
    **{f"question_count_{count}": activity_with_question_count(count) for count in QUESTION_COUNT_SCENARIOS},
}


# --------------------------------------------------------------------------
# Gap report
# --------------------------------------------------------------------------
SUGGESTION_PAYLOAD = {
    "topic_id": "topic-solicitors-accounts",
    "name": "Solicitors Accounts Rules",
    "naric_level": "level_6",
    "course_progress_percent": 64,
}


def gap_report_suggestion_available(clock: Clock) -> GapReportProvider:
    plan = GapReportPlan()
    plan.set_suggestion(CONFORMANCE_USER_ID, dict(SUGGESTION_PAYLOAD))
    return MockGapReportProvider(clock, plan)


def gap_report_no_suggestion(clock: Clock) -> GapReportProvider:
    plan = GapReportPlan()
    plan.set_suggestion(CONFORMANCE_USER_ID, None)
    return MockGapReportProvider(clock, plan)


def gap_report_unavailable(clock: Clock) -> GapReportProvider:
    return MockGapReportProvider(clock, GapReportPlan().with_fault(Fault.UNAVAILABLE))


def gap_report_timeout(clock: Clock) -> GapReportProvider:
    return MockGapReportProvider(clock, GapReportPlan().with_fault(Fault.TIMEOUT))


def gap_report_invalid(clock: Clock) -> GapReportProvider:
    return MockGapReportProvider(clock, GapReportPlan().with_fault(Fault.INVALID))


def gap_report_unmappable_level(clock: Clock) -> GapReportProvider:
    """A suggestion whose level maps to no platform enum member.

    The contract says degrade, not discard: LEVEL_5 / source default / status
    invalid, logged.
    """
    plan = GapReportPlan()
    plan.set_suggestion(
        CONFORMANCE_USER_ID,
        {**SUGGESTION_PAYLOAD, "naric_level": "postgraduate-ish", "course_progress_percent": 0.64},
    )
    return MockGapReportProvider(clock, plan)


MOCK_GAP_REPORT_SCENARIOS: Mapping[str, Callable[[Clock], GapReportProvider]] = {
    "available": gap_report_suggestion_available,
    "empty": gap_report_no_suggestion,
    "unavailable": gap_report_unavailable,
    "timeout": gap_report_timeout,
    "invalid": gap_report_invalid,
    "suggestion_available": gap_report_suggestion_available,
    "no_suggestion": gap_report_no_suggestion,
    "unmappable_level": gap_report_unmappable_level,
}
