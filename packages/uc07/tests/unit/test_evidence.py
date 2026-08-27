"""Evidence is mandatory, resolvable, and never fabricated."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.conftest import build_harness
from uc07.application.evidence_guard import enforce_evidence_integrity
from uc07.domain.enums import (
    DescriptionSource,
    EvidenceBasis,
    GapType,
    SignalKind,
)
from uc07.domain.models import Gap, GapEvidence, SignalEvidence

SCENARIOS_WITH_REPORTS = [
    "count_10",
    "count_11",
    "count_50",
    "struggle_mixed",
    "diverse_topics",
    "narrow_topics",
    "heavy_explain_differently",
    "heavy_follow_ups",
    "duplicate_interaction_ids",
    "mixed_owner_records",
    "feedback_empty",
    "feedback_unavailable",
    "feedback_partial",
    "feedback_invalid",
    "profile_fully_covered",
    "profile_no_speciality",
    "profile_partial",
    "profile_unavailable",
    "profile_invalid",
    "courses_unavailable",
    "courses_partial",
    "courses_invalid",
    "courses_not_enrolled",
    "courses_only_invalid_candidates",
    "interactions_partial",
]


def _struggle_gap(evidence_ids: tuple[str, ...]) -> Gap:
    return Gap(
        topic_tag="alpha",
        gap_type=GapType.STRUGGLE,
        description="d",
        description_source=DescriptionSource.REGISTRY,
        signals=(SignalKind.EXPLAIN_DIFFERENTLY,),
        evidence=GapEvidence(
            basis=EvidenceBasis.INTERACTION_IDS,
            interaction_ids=evidence_ids,
            per_signal=(
                SignalEvidence(
                    signal=SignalKind.EXPLAIN_DIFFERENTLY,
                    observed_value=2,
                    threshold=2,
                    interaction_ids=evidence_ids,
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Model-level guarantees
# ---------------------------------------------------------------------------


def test_struggle_gap_cannot_be_built_without_evidence_ids():
    with pytest.raises(ValidationError):
        _struggle_gap(())


def test_gap_cannot_be_built_without_signals():
    with pytest.raises(ValidationError):
        Gap(
            topic_tag="alpha",
            gap_type=GapType.STRUGGLE,
            description="d",
            description_source=DescriptionSource.REGISTRY,
            signals=(),
            evidence=GapEvidence(
                basis=EvidenceBasis.INTERACTION_IDS, interaction_ids=("i1",)
            ),
        )


def test_signal_evidence_cannot_claim_a_signal_that_did_not_fire():
    with pytest.raises(ValidationError):
        SignalEvidence(
            signal=SignalKind.LOW_RATING,
            observed_value=0,
            threshold=1,
            interaction_ids=(),
        )


def test_per_signal_evidence_must_be_inside_the_gap_evidence_set():
    with pytest.raises(ValidationError):
        GapEvidence(
            basis=EvidenceBasis.INTERACTION_IDS,
            interaction_ids=("i1",),
            per_signal=(
                SignalEvidence(
                    signal=SignalKind.FOLLOW_UP,
                    observed_value=2,
                    threshold=2,
                    interaction_ids=("i1", "ghost"),
                ),
            ),
        )


# ---------------------------------------------------------------------------
# Guard-level guarantees
# ---------------------------------------------------------------------------


def test_guard_rejects_a_gap_whose_evidence_id_does_not_resolve():
    result = enforce_evidence_integrity([_struggle_gap(("ghost-1",))], {"i1", "i2"})
    assert result.gaps == ()
    assert result.rejected_gap_count == 1
    assert "evidence_id_does_not_resolve" in result.rejection_reasons


def test_guard_keeps_a_gap_whose_evidence_resolves():
    gap = _struggle_gap(("i1",))
    result = enforce_evidence_integrity([gap], {"i1", "i2"})
    assert result.gaps == (gap,)
    assert result.rejected_gap_count == 0


def test_guard_rejects_partially_fabricated_evidence():
    result = enforce_evidence_integrity([_struggle_gap(("i1", "ghost"))], {"i1"})
    assert result.gaps == ()
    assert result.rejected_gap_count == 1


# ---------------------------------------------------------------------------
# Report-level guarantees, exhaustively across generated reports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS_WITH_REPORTS)
def test_every_generated_gap_carries_resolvable_evidence(scenario):
    harness = build_harness(scenario)
    outcome = harness.service.current_report(harness.user_id)
    report = outcome.report
    assert report is not None, scenario

    provider = harness.scenario.interactions[harness.user_id]
    known_ids = {
        raw["interaction_id"]
        for raw in provider.records
        if raw.get("user_id") == harness.user_id
    }

    assert report.gaps, scenario
    for gap in report.gaps:
        assert gap.signals
        assert gap.description
        if gap.gap_type is GapType.STRUGGLE:
            assert gap.evidence.basis is EvidenceBasis.INTERACTION_IDS
            assert gap.evidence_interaction_ids
            assert set(gap.evidence_interaction_ids) <= known_ids
            for signal in gap.evidence.per_signal:
                assert set(signal.interaction_ids) <= set(gap.evidence_interaction_ids)
        else:
            assert gap.evidence.basis is (
                EvidenceBasis.ZERO_INTERACTIONS_FOR_SPECIALITY_AREA
            )
            assert gap.evidence_interaction_ids == ()


@pytest.mark.parametrize("scenario", SCENARIOS_WITH_REPORTS)
def test_no_gap_topic_is_invented_outside_history_or_speciality(scenario):
    harness = build_harness(scenario)
    report = harness.service.current_report(harness.user_id).report
    assert report is not None

    provider = harness.scenario.interactions[harness.user_id]
    history_topics = {
        raw["topic_tag"]
        for raw in provider.records
        if raw.get("user_id") == harness.user_id
    }
    profile_payload = harness.scenario.profiles[harness.user_id].profile or {}
    speciality = set(profile_payload.get("speciality_areas", ()))

    for gap in report.gaps:
        assert gap.topic_tag in history_topics | speciality
