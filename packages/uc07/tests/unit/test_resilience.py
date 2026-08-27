"""Typed provider failures, partial data, and source-status preservation."""

from __future__ import annotations

import pytest

from tests.conftest import build_harness
from uc07.adapters.mock.interaction_log import (
    MockInteractionLogProvider,
    MockInteractionPayload,
)
from uc07.adapters.mock.scenarios import LEARNER, INVALID_RECORDS
from uc07.domain.enums import SourceStatus
from uc07.domain.errors import (
    InteractionSourceUnusable,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)

# ---------------------------------------------------------------------------
# Interaction source: never an empty report
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("interactions_unavailable", SourceStatus.UNAVAILABLE.value),
        ("interactions_timeout", SourceStatus.UNAVAILABLE.value),
        ("interactions_invalid", SourceStatus.INVALID.value),
    ],
)
def test_unusable_interaction_source_raises_instead_of_returning_an_empty_report(
    scenario, expected_status
):
    harness = build_harness(scenario)
    with pytest.raises(InteractionSourceUnusable) as excinfo:
        harness.service.current_report(harness.user_id)
    assert excinfo.value.source_status == expected_status

    with pytest.raises(InteractionSourceUnusable):
        harness.service.progress(harness.user_id)


def test_invalid_interaction_payload_raises_a_typed_contract_error():
    provider = MockInteractionLogProvider(
        {LEARNER: MockInteractionPayload(records=INVALID_RECORDS)}
    )
    with pytest.raises(ProviderInvalidResponse) as excinfo:
        provider.for_user(LEARNER)
    assert excinfo.value.port.value == "interaction_log"


def test_partial_interaction_source_is_preserved_and_noticed():
    report = (
        build_harness("interactions_partial").service.current_report(LEARNER).report
    )
    assert report is not None
    assert report.source_statuses.interactions is SourceStatus.PARTIAL
    assert "interaction_source_partial" in {n.code.value for n in report.notices}


def test_empty_interaction_history_is_progress_not_failure():
    harness = build_harness("count_0")
    outcome = harness.service.current_report(harness.user_id)
    assert outcome.report is None
    assert outcome.progress.interactions_completed == 0


# ---------------------------------------------------------------------------
# Feedback source
# ---------------------------------------------------------------------------


def test_feedback_unavailable_keeps_gaps_and_drops_only_the_rating_signal():
    report = build_harness("feedback_unavailable").service.current_report(LEARNER).report
    assert report is not None
    assert report.source_statuses.feedback is SourceStatus.UNAVAILABLE
    assert "rating_signal_unavailable" in {n.code.value for n in report.notices}
    signals = {
        signal.value for gap in report.gaps for signal in gap.signals
    }
    assert "low_rating" not in signals
    assert {"explain_differently", "follow_up"} <= signals
    # land_registration only had a low-rating signal, so it must disappear.
    assert "land_registration" not in {gap.topic_tag for gap in report.gaps}


def test_feedback_invalid_is_distinct_from_unavailable():
    report = build_harness("feedback_invalid").service.current_report(LEARNER).report
    assert report is not None
    assert report.source_statuses.feedback is SourceStatus.INVALID
    assert "rating_signal_invalid" in {n.code.value for n in report.notices}


def test_feedback_empty_means_the_learner_genuinely_has_no_ratings():
    report = build_harness("feedback_empty").service.current_report(LEARNER).report
    assert report is not None
    assert report.source_statuses.feedback is SourceStatus.EMPTY
    assert "rating_signal_no_ratings" in {n.code.value for n in report.notices}
    assert "low_rating" not in {
        signal.value for gap in report.gaps for signal in gap.signals
    }


def test_feedback_partial_is_used_but_flagged_as_possibly_incomplete():
    report = build_harness("feedback_partial").service.current_report(LEARNER).report
    assert report is not None
    assert report.source_statuses.feedback is SourceStatus.PARTIAL
    assert "rating_signal_partial" in {n.code.value for n in report.notices}
    contract = next(g for g in report.gaps if g.topic_tag == "contract_formation")
    low_rating = next(
        item for item in contract.evidence.per_signal if item.signal.value == "low_rating"
    )
    assert low_rating.observed_value == 1  # only the retrieved part was counted


def test_empty_and_unavailable_feedback_are_never_the_same_state():
    empty = build_harness("feedback_empty").service.current_report(LEARNER).report
    unavailable = (
        build_harness("feedback_unavailable").service.current_report(LEARNER).report
    )
    assert empty is not None and unavailable is not None
    assert empty.source_statuses.feedback is not unavailable.source_statuses.feedback
    assert empty.content_fingerprint != unavailable.content_fingerprint


# ---------------------------------------------------------------------------
# Profile and courses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("profile_unavailable", SourceStatus.UNAVAILABLE),
        ("profile_invalid", SourceStatus.INVALID),
        ("profile_partial", SourceStatus.PARTIAL),
        ("profile_no_speciality", SourceStatus.EMPTY),
    ],
)
def test_profile_status_is_preserved_verbatim(scenario, expected):
    report = build_harness(scenario).service.current_report(LEARNER).report
    assert report is not None
    assert report.source_statuses.profile is expected


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("courses_unavailable", SourceStatus.UNAVAILABLE),
        ("courses_invalid", SourceStatus.INVALID),
        ("courses_partial", SourceStatus.PARTIAL),
    ],
)
def test_courses_status_is_preserved_verbatim(scenario, expected):
    report = build_harness(scenario).service.current_report(LEARNER).report
    assert report is not None
    assert report.source_statuses.courses is expected
    assert report.gaps  # analysis never depends on course availability


def test_provider_timeout_is_its_own_type():
    from uc07.adapters.mock.feedback import MockFeedbackPayload, MockFeedbackProvider

    provider = MockFeedbackProvider(MockFeedbackPayload(failure="timeout"))
    with pytest.raises(ProviderTimeout):
        provider.for_interactions(["i1"])


def test_provider_unavailable_is_its_own_type():
    from uc07.adapters.mock.profile import MockLearnerProfileProvider, MockProfilePayload

    provider = MockLearnerProfileProvider(
        {LEARNER: MockProfilePayload(failure="unavailable")}
    )
    with pytest.raises(ProviderUnavailable):
        provider.get_profile(LEARNER)


def test_service_never_catches_bare_exceptions_from_providers():
    """An unexpected error type must propagate, not be swallowed as 'empty'."""

    class Exploding(MockInteractionLogProvider):
        def for_user(self, user_id: str):  # type: ignore[override]
            raise RuntimeError("unexpected internal failure")

    from uc07.adapters.clock import FixedClock
    from tests.conftest import DEFAULT_THRESHOLDS, FIXED_NOW, registry
    from uc07.adapters.mock import get_scenario, providers_for
    from uc07.adapters.persistence import InMemoryGapReportRepository
    from uc07.application.service import GapReportService

    scenario = get_scenario("struggle_mixed")
    providers = providers_for(scenario)
    service = GapReportService(
        interactions=Exploding(scenario.interactions),
        feedback=providers.feedback,
        profiles=providers.profiles,
        courses=providers.courses,
        repository=InMemoryGapReportRepository(),
        clock=FixedClock(FIXED_NOW),
        descriptions=registry(),
        thresholds=DEFAULT_THRESHOLDS,
    )
    with pytest.raises(RuntimeError):
        service.current_report(LEARNER)
