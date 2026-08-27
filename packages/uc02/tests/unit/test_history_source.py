"""Question-history behaviour, including server-side limit enforcement."""

from __future__ import annotations

from uc02.domain.models.enums import SourceName, SourceStatus
from uc02.infrastructure.providers.mocks import HistoryScenario
from uc02.infrastructure.providers.mocks.history import MockQuestionHistoryProvider
from tests.fixtures.factories import make_harness, make_identity, make_settings


async def _harness_context(scenario: HistoryScenario, **settings_overrides):
    settings = make_settings(**settings_overrides) if settings_overrides else None
    harness = make_harness(history=scenario, settings=settings)
    outcome = await harness.service.initialize(make_identity())
    return harness, outcome.context


async def test_exactly_20_questions_are_all_kept_and_not_flagged_truncated():
    _, context = await _harness_context(HistoryScenario.EXACTLY_20)
    assert context.question_history.count == 20
    assert context.question_history.truncated is False
    assert context.source_status[SourceName.QUESTION_HISTORY].status is SourceStatus.AVAILABLE


async def test_fewer_than_20_questions_are_all_kept():
    _, context = await _harness_context(HistoryScenario.FEWER_THAN_20)
    assert context.question_history.count == 7
    assert context.question_history.truncated is False
    assert context.source_status[SourceName.QUESTION_HISTORY].status is SourceStatus.AVAILABLE


async def test_zero_questions_is_empty_not_unavailable():
    _, context = await _harness_context(HistoryScenario.ZERO)
    assert context.question_history.count == 0
    outcome = context.source_status[SourceName.QUESTION_HISTORY]
    assert outcome.status is SourceStatus.EMPTY
    assert outcome.status is not SourceStatus.UNAVAILABLE
    assert outcome.fallback_applied is False


async def test_oversupply_is_truncated_to_20_server_side_and_flagged():
    harness, context = await _harness_context(HistoryScenario.MORE_THAN_20_AVAILABLE)
    assert MockQuestionHistoryProvider.OVERSUPPLY_COUNT > 20
    assert context.question_history.count == 20
    assert context.question_history.truncated is True
    # The limit handed to the provider is the server's, not a caller's.
    assert harness.history.observed_limits == [20]


async def test_truncation_keeps_the_most_recent_questions():
    _, context = await _harness_context(HistoryScenario.MORE_THAN_20_AVAILABLE)
    timestamps = [item.asked_at for item in context.question_history.items]
    assert timestamps == sorted(timestamps, reverse=True)
    # The oldest of the 35 available records must not survive truncation.
    ids = {item.question_id for item in context.question_history.items}
    assert "q-0034" not in ids
    assert "q-0000" in ids


async def test_configured_limit_is_respected_and_still_server_side():
    harness, context = await _harness_context(
        HistoryScenario.MORE_THAN_20_AVAILABLE, question_history_limit=5
    )
    assert harness.history.observed_limits == [5]
    assert context.question_history.count == 5
    assert context.question_history.truncated is True


async def test_unavailable_history_defaults_to_empty_items():
    _, context = await _harness_context(HistoryScenario.UNAVAILABLE)
    assert context.question_history.items == ()
    outcome = context.source_status[SourceName.QUESTION_HISTORY]
    assert outcome.status is SourceStatus.UNAVAILABLE
    assert outcome.fallback_applied is True


async def test_malformed_record_does_not_crash_assembly():
    _, context = await _harness_context(HistoryScenario.MALFORMED_RECORD)
    # Three parsable records were returned alongside one unparsable one.
    assert context.question_history.count == 3
    assert context.question_history.dropped_malformed_count == 1
    outcome = context.source_status[SourceName.QUESTION_HISTORY]
    assert outcome.status is SourceStatus.PARTIAL
    # The rest of the context is intact.
    assert context.explanation_profile.template_id.value == "intermediate"
    assert context.personalization.available is True


async def test_history_text_is_kept_server_side_only_as_an_excerpt():
    _, context = await _harness_context(HistoryScenario.FEWER_THAN_20)
    item = context.question_history.items[0]
    assert item.text_excerpt  # retained in the domain object
    assert len(item.text_excerpt) <= 160
