"""Full-history aggregation: all sessions, topic tags consumed exactly as supplied."""

from __future__ import annotations

from tests.conftest import build_harness
from uc07.adapters.mock.interaction_log import (
    MockInteractionLogProvider,
    MockInteractionPayload,
)
from uc07.adapters.mock.scenarios import (
    DIVERSE_TOPIC_RECORDS,
    LEARNER,
    STRUGGLE_MIXED_RECORDS,
    interaction,
)
from uc07.application.aggregation import aggregate_history
from uc07.domain.counting import qualifying_interactions


def _history(raw, user_id: str = LEARNER):
    provider = MockInteractionLogProvider({user_id: MockInteractionPayload(records=raw)})
    qualifying = qualifying_interactions(provider.for_user(user_id), user_id=user_id)
    return aggregate_history(qualifying, user_id=user_id)


def test_aggregation_spans_every_session_not_just_the_latest():
    history = _history(STRUGGLE_MIXED_RECORDS)
    assert history.session_count == 3
    assert history.interaction_count == len(STRUGGLE_MIXED_RECORDS)
    contract = next(t for t in history.topics if t.topic_tag == "contract_formation")
    assert contract.session_ids == ("session-1", "session-2")


def test_topics_are_grouped_by_the_supplied_topic_tag():
    history = _history(STRUGGLE_MIXED_RECORDS)
    assert history.topic_tags == (
        "contract_formation",
        "evidence_admissibility",
        "land_registration",
        "negligence",
        "professional_conduct",
    )


def test_unusual_topic_tags_are_never_rewritten_or_reclassified():
    raw = tuple(
        interaction(
            interaction_id=f"odd-{index}",
            session_id="s",
            topic_tag=tag,
            minute=index,
        )
        for index, tag in enumerate(
            ("Contract Formation", "contract_formation", "ZZ-unknown-tag/2026", "  ")
        )
        if tag.strip()
    )
    history = _history(raw)
    assert history.topic_tags == (
        "Contract Formation",
        "ZZ-unknown-tag/2026",
        "contract_formation",
    )


def test_per_topic_signal_inputs_are_aggregated_over_the_whole_history():
    history = _history(STRUGGLE_MIXED_RECORDS)
    contract = next(t for t in history.topics if t.topic_tag == "contract_formation")
    negligence = next(t for t in history.topics if t.topic_tag == "negligence")

    assert contract.explain_differently_total == 3  # 2 (session-1) + 1 (session-2)
    assert contract.explain_differently_interaction_ids == (
        "interaction-101",
        "interaction-103",
    )
    assert contract.follow_up_count == 1
    assert negligence.follow_up_interaction_ids == ("interaction-202", "interaction-203")


def test_aggregate_ordering_is_deterministic():
    first = _history(DIVERSE_TOPIC_RECORDS)
    second = _history(tuple(reversed(DIVERSE_TOPIC_RECORDS)))
    assert first.topics == second.topics
    assert first.interactions == second.interactions


def test_report_uses_full_history_across_sessions():
    harness = build_harness("struggle_mixed")
    report = harness.service.current_report(harness.user_id).report
    assert report is not None
    assert report.source_interaction_count == 14
    assert report.topic_coverage.topic_areas_in_history == 5
