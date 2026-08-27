"""Structured logging and session identity.

The scope requires that a weekly summary is logged whether it is sent or not,
that an invalid upstream value is logged, and that a failed streak write is
logged and alerted. Those log records are part of the deliverable, so they are
asserted rather than assumed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from uc08.adapters.persistence.faults import FaultyStreakRepository
from uc08.adapters.persistence.memory import InMemoryStreakRepository
from uc08.adapters.sinks.local import FailingWeeklySummarySink
from uc08.domain.enums import SessionIdSource
from uc08.domain.errors import SessionIdRequired
from uc08.domain.models import StreakRecord
from uc08.logging_setup import JsonFormatter
from uc08.application.session import DEV_SESSION_PREFIX, resolve_session_id
from tests.conftest import USER, build_harness

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Session identity
# --------------------------------------------------------------------------
def test_a_received_session_id_is_passed_through():
    resolved = resolve_session_id("sess-opaque", user_id=USER, now=NOW, allow_dev_minting=False)
    assert resolved.session_id == "sess-opaque"
    assert resolved.source is SessionIdSource.RECEIVED


@pytest.mark.parametrize("provided", [None, "", "   "])
def test_a_missing_session_id_is_refused_on_the_production_path(provided):
    with pytest.raises(SessionIdRequired) as caught:
        resolve_session_id(provided, user_id=USER, now=NOW, allow_dev_minting=False)
    assert "does not create one" in str(caught.value)


def test_dev_minting_is_deterministic_and_recognisable():
    first = resolve_session_id(None, user_id=USER, now=NOW, allow_dev_minting=True)
    second = resolve_session_id(None, user_id=USER, now=NOW, allow_dev_minting=True)
    assert first.session_id == second.session_id  # derived from account and clock, not random
    assert first.session_id.startswith(DEV_SESSION_PREFIX)
    assert first.source is SessionIdSource.DEV_MINTED


def test_a_minted_session_id_is_logged_as_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="uc08.application.session"):
        resolve_session_id(None, user_id=USER, now=NOW, allow_dev_minting=True)
    record = next(item for item in caplog.records if item.getMessage() == "dev_session_id_minted")
    assert record.session_id_source == "dev_minted"


# --------------------------------------------------------------------------
# Structured logging
# --------------------------------------------------------------------------
def test_the_formatter_emits_one_json_object_per_record():
    record = logging.LogRecord(
        name="uc08.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="activity_recorded",
        args=(),
        exc_info=None,
    )
    record.user_id = "learner-1"
    record.current_streak_days = 5
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "activity_recorded"
    assert payload["level"] == "info"
    assert payload["user_id"] == "learner-1"
    assert payload["current_streak_days"] == 5
    assert "timestamp" in payload


def test_activity_recording_is_logged_with_the_decision_and_the_counts(clock, caplog):
    harness = build_harness(clock)
    with caplog.at_level(logging.INFO, logger="uc08.application.streak_service"):
        harness.record("i-1")
    record = next(item for item in caplog.records if item.getMessage() == "activity_recorded")
    assert record.outcome == "started"
    assert record.current_streak_days == 1
    assert record.persistence_outcome == "saved"
    assert record.window_hours == 24
    assert record.session_id_source == "received"


def test_a_weekly_summary_is_logged_even_when_the_send_fails(monday_clock, caplog):
    sink = FailingWeeklySummarySink(fail_sends=1)
    harness = build_harness(monday_clock, notifications=sink)
    harness.ledger.add_interaction(USER, monday_clock.now() - timedelta(days=3), "last-week", topic="conduct")

    with caplog.at_level(logging.INFO, logger="uc08.application.weekly_summary_service"):
        harness.summary_service.generate(USER)

    events = [item.getMessage() for item in caplog.records]
    assert "weekly_summary_generated" in events
    assert "weekly_summary_send_failed" in events
    generated = next(item for item in caplog.records if item.getMessage() == "weekly_summary_generated")
    assert generated.week == "2026-W11"
    assert generated.questions_asked == 1
    assert generated.delivery_status == "pending"
    failed = next(item for item in caplog.records if item.getMessage() == "weekly_summary_send_failed")
    assert failed.record_retained is True


def test_a_streak_write_failure_is_logged_twice_and_then_alerted(clock, caplog):
    inner = InMemoryStreakRepository()
    inner.save(
        StreakRecord(
            user_id=USER,
            current_streak_days=6,
            longest_streak_days=6,
            last_activity_at=clock.now() - timedelta(hours=23),
            streak_started_at=clock.now() - timedelta(days=6),
            freeze_available=True,
            freeze_used_at=None,
            updated_at=clock.now() - timedelta(hours=23),
        )
    )
    harness = build_harness(clock, streaks=FaultyStreakRepository(inner, fail_writes=2))
    harness.ledger.add_interaction(USER, clock.now() - timedelta(hours=23), "yesterday")

    with caplog.at_level(logging.WARNING):
        harness.record("today")

    attempts = [item for item in caplog.records if item.getMessage() == "streak_write_failed"]
    assert [item.attempt for item in attempts] == [1, 2]
    assert [item.will_retry for item in attempts] == [True, False]

    preserved = next(item for item in caplog.records if item.getMessage() == "streak_write_preserved_last_known")
    assert preserved.preserved_streak_days == 6
    assert preserved.intended_streak_days == 7
    assert preserved.reset_applied is False

    alert = next(item for item in caplog.records if item.getMessage() == "streak_write_failed_alert")
    assert alert.alert_severity == "page_engineering"
    assert alert.preserved_streak_days == 6


def test_a_preserved_streak_on_a_degraded_source_is_logged(clock, caplog):
    from uc08.adapters.mock.ledger import Fault

    inner = InMemoryStreakRepository()
    inner.save(
        StreakRecord(
            user_id=USER,
            current_streak_days=8,
            longest_streak_days=8,
            last_activity_at=clock.now() - timedelta(days=5),
            streak_started_at=clock.now() - timedelta(days=13),
            freeze_available=True,
            freeze_used_at=None,
            updated_at=clock.now() - timedelta(days=5),
        )
    )
    harness = build_harness(clock, streaks=inner)
    harness.ledger.with_fault(Fault.UNAVAILABLE)

    with caplog.at_level(logging.WARNING, logger="uc08.application.streak_service"):
        harness.record_without_upstream_echo("i-1")

    record = next(item for item in caplog.records if item.getMessage() == "streak_preserved_source_degraded")
    assert record.reset_applied is False
    assert record.current_streak_days == 8
    assert record.activity_status == "unavailable"


def test_an_omitted_suggestion_is_logged_without_inventing_one(monday_clock, caplog):
    from uc08.adapters.mock.ledger import Fault

    harness = build_harness(monday_clock)
    harness.gap_plan.with_fault(Fault.UNAVAILABLE)

    with caplog.at_level(logging.WARNING, logger="uc08.application.weekly_summary_service"):
        harness.summary_service.generate(USER)

    record = next(item for item in caplog.records if item.getMessage() == "weekly_summary_suggestion_degraded")
    assert record.suggested_topic_status == "unavailable"
    assert record.invented_suggestion is False
