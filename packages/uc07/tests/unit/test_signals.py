"""Struggle signals: each independently, combined, and below-threshold silence."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.conftest import DEFAULT_THRESHOLDS, build_harness
from uc07.application.aggregation import aggregate_history
from uc07.application.signals import (
    build_low_rating_index,
    detect_struggles,
)
from uc07.domain.counting import qualifying_interactions
from uc07.domain.enums import Rating, SignalKind
from uc07.domain.models import FeedbackRecord, InteractionRecord

USER = "learner-signal"
BASE = datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)


def record(
    interaction_id: str,
    topic: str,
    *,
    minute: int = 0,
    explain: int = 0,
    follow_up: str | None = None,
    session: str = "s-1",
) -> InteractionRecord:
    return InteractionRecord(
        interaction_id=interaction_id,
        session_id=session,
        user_id=USER,
        asked_at=BASE.replace(minute=minute),
        topic_tag=topic,
        question_class="concept",
        naric_level="LEVEL_6",
        response_id=f"response-{interaction_id}",
        follow_up_of=follow_up,
        explain_differently_count=explain,
    )


def rating(rating_id: str, interaction_id: str, value: str = "down") -> FeedbackRecord:
    return FeedbackRecord(
        rating_id=rating_id,
        interaction_id=interaction_id,
        user_id=USER,
        rated_at=BASE,
        rating=value,
    )


def history_of(*records: InteractionRecord):
    qualifying = qualifying_interactions(records, user_id=USER)
    return aggregate_history(qualifying, user_id=USER)


def findings_for(records, feedback=None):
    history = history_of(*records)
    index = build_low_rating_index(history, feedback)
    return {
        finding.topic_tag: finding
        for finding in detect_struggles(history, DEFAULT_THRESHOLDS, index)
    }


# ---------------------------------------------------------------------------
# Explain-differently signal, independently
# ---------------------------------------------------------------------------


def test_explain_differently_fires_at_the_configured_threshold():
    findings = findings_for(
        [record("i1", "alpha", explain=2), record("i2", "beta", explain=1, minute=1)],
        feedback=[],
    )
    assert set(findings) == {"alpha"}
    signal = findings["alpha"].signals[0]
    assert signal.signal is SignalKind.EXPLAIN_DIFFERENTLY
    assert (signal.observed_value, signal.threshold) == (2, 2)
    assert signal.interaction_ids == ("i1",)


def test_explain_differently_totals_across_interactions_in_the_topic():
    findings = findings_for(
        [
            record("i1", "alpha", explain=1),
            record("i2", "alpha", explain=1, minute=1),
        ],
        feedback=[],
    )
    assert findings["alpha"].signals[0].observed_value == 2
    assert findings["alpha"].evidence_interaction_ids == ("i1", "i2")


def test_explain_differently_below_threshold_does_not_surface():
    assert findings_for([record("i1", "alpha", explain=1)], feedback=[]) == {}


# ---------------------------------------------------------------------------
# Follow-up signal, independently
# ---------------------------------------------------------------------------


def test_follow_up_signal_fires_at_the_configured_threshold():
    findings = findings_for(
        [
            record("i1", "alpha"),
            record("i2", "alpha", minute=1, follow_up="i1"),
            record("i3", "alpha", minute=2, follow_up="i1"),
        ],
        feedback=[],
    )
    signal = findings["alpha"].signals[0]
    assert signal.signal is SignalKind.FOLLOW_UP
    assert signal.observed_value == 2
    assert signal.interaction_ids == ("i2", "i3")


def test_single_follow_up_does_not_surface():
    assert (
        findings_for(
            [record("i1", "alpha"), record("i2", "alpha", minute=1, follow_up="i1")],
            feedback=[],
        )
        == {}
    )


def test_heavy_follow_up_scenario_surfaces_only_the_follow_up_topic():
    harness = build_harness("heavy_follow_ups")
    report = harness.service.current_report(harness.user_id).report
    assert report is not None
    struggle_topics = {
        gap.topic_tag: [signal.value for signal in gap.signals]
        for gap in report.gaps
        if gap.gap_type.value == "struggle"
    }
    assert struggle_topics == {"trusts_formation": ["follow_up"]}


# ---------------------------------------------------------------------------
# Low-rating signal, independently
# ---------------------------------------------------------------------------


def test_low_rating_signal_fires_on_a_single_thumbs_down():
    findings = findings_for(
        [record("i1", "alpha"), record("i2", "beta", minute=1)],
        feedback=[rating("r1", "i1")],
    )
    assert set(findings) == {"alpha"}
    signal = findings["alpha"].signals[0]
    assert signal.signal is SignalKind.LOW_RATING
    assert (signal.observed_value, signal.threshold) == (1, 1)
    assert signal.interaction_ids == ("i1",)


def test_thumbs_up_is_never_a_struggle_signal():
    assert (
        findings_for([record("i1", "alpha")], feedback=[rating("r1", "i1", "up")]) == {}
    )


def test_ratings_for_unknown_interactions_cannot_manufacture_evidence():
    findings = findings_for(
        [record("i1", "alpha")], feedback=[rating("r1", "not-in-history")]
    )
    assert findings == {}


def test_ratings_owned_by_another_learner_are_ignored():
    foreign = FeedbackRecord(
        rating_id="r-foreign",
        interaction_id="i1",
        user_id="someone-else",
        rated_at=BASE,
        rating=Rating.DOWN,
    )
    assert findings_for([record("i1", "alpha")], feedback=[foreign]) == {}


def test_low_rating_signal_is_skipped_when_the_rating_source_cannot_be_read():
    history = history_of(record("i1", "alpha"))
    index = build_low_rating_index(history, None)
    assert index.evaluated is False
    assert detect_struggles(history, DEFAULT_THRESHOLDS, index) == ()


def test_empty_rating_source_is_evaluated_and_simply_finds_nothing():
    history = history_of(record("i1", "alpha"))
    index = build_low_rating_index(history, [])
    assert index.evaluated is True
    assert detect_struggles(history, DEFAULT_THRESHOLDS, index) == ()


# ---------------------------------------------------------------------------
# Combination and silence
# ---------------------------------------------------------------------------


def test_signals_combine_on_one_topic_in_canonical_order():
    findings = findings_for(
        [
            record("i1", "alpha", explain=2),
            record("i2", "alpha", minute=1, follow_up="i1"),
            record("i3", "alpha", minute=2, follow_up="i1"),
        ],
        feedback=[rating("r1", "i1")],
    )
    finding = findings["alpha"]
    assert finding.signal_kinds == (
        SignalKind.EXPLAIN_DIFFERENTLY,
        SignalKind.FOLLOW_UP,
        SignalKind.LOW_RATING,
    )
    assert finding.evidence_interaction_ids == ("i1", "i2", "i3")


def test_topic_below_every_threshold_is_not_a_struggle():
    findings = findings_for(
        [
            record("i1", "quiet", explain=1),
            record("i2", "quiet", minute=1, follow_up="i1"),
        ],
        feedback=[rating("r1", "i1", "up")],
    )
    assert findings == {}


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("heavy_explain_differently", {"misrepresentation": ["explain_differently"]}),
        ("heavy_follow_ups", {"trusts_formation": ["follow_up"]}),
    ],
)
def test_scenarios_isolate_single_signals(scenario, expected):
    harness = build_harness(scenario)
    report = harness.service.current_report(harness.user_id).report
    assert report is not None
    assert {
        gap.topic_tag: [signal.value for signal in gap.signals]
        for gap in report.gaps
        if gap.gap_type.value == "struggle"
    } == expected


def test_showcase_scenario_signal_matrix():
    harness = build_harness("struggle_mixed")
    report = harness.service.current_report(harness.user_id).report
    assert report is not None
    struggles = {
        gap.topic_tag: [signal.value for signal in gap.signals]
        for gap in report.gaps
        if gap.gap_type.value == "struggle"
    }
    assert struggles == {
        "contract_formation": ["explain_differently", "low_rating"],
        "land_registration": ["low_rating"],
        "negligence": ["follow_up"],
    }
    # professional_conduct (explain=1) and evidence_admissibility (no signals)
    # stay below every threshold and must not appear.
    assert "professional_conduct" not in struggles
    assert "evidence_admissibility" not in struggles
