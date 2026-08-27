"""ActivityProvider contract conformance. Adapter-agnostic.

Point this at a new adapter by adding one line to
``ACTIVITY_PROVIDERS`` in ``uc08/registry.py``. No test changes.

    python -m pytest tests/conformance/test_activity_provider_conformance.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from uc08.domain.enums import SourceStatus
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import ActivityWindowRead, QuestionCountRead, TopicsRead
from uc08.ports.conformance import (
    BEHAVIOURAL_ACTIVITY_SCENARIOS,
    CONFORMANCE_USER_ID,
    REQUIRED_CONFORMANCE_SCENARIOS,
)
from uc08.ports.upstream import MUTATING_NAME_FRAGMENTS, READ_ONLY_PORTS
from tests.conformance.conftest import (
    adapters_for,
    assert_no_leakage,
    assert_utc,
    build,
    scenarios_of,
)

PORT = "activity"
ADAPTERS = adapters_for(PORT)
USER = CONFORMANCE_USER_ID


def test_at_least_two_independent_adapter_families_are_registered():
    """The suite is only meaningful if it is not testing one implementation."""
    assert len(ADAPTERS) >= 2, f"registered activity adapters: {[name for name, _ in ADAPTERS]}"


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_declares_every_required_scenario(name, adapter_class):
    builders = scenarios_of(adapter_class)
    for scenario in REQUIRED_CONFORMANCE_SCENARIOS:
        assert callable(builders[scenario])


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_exposes_only_the_read_methods_of_the_port(name, adapter_class):
    allowed = READ_ONLY_PORTS["ActivityProvider"]
    public = {
        attribute
        for attribute in dir(adapter_class)
        if not attribute.startswith("_") and callable(getattr(adapter_class, attribute, None))
    }
    extra = public - allowed - {"conformance_scenarios"}
    assert not extra, f"{name} exposes non-read methods: {sorted(extra)}"
    for attribute in public:
        assert not any(fragment in attribute.lower() for fragment in MUTATING_NAME_FRAGMENTS), attribute


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_honours_the_configured_deadline(name, adapter_class):
    clock = build(adapter_class, "available")[1]
    instance = adapter_class(clock, timeout_seconds=1.25)
    assert instance.timeout_seconds == pytest.approx(1.25), (
        "the deadline must come from configuration, not a literal inside the adapter"
    )


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_available_returns_platform_types(name, adapter_class):
    provider, clock = build(adapter_class, "available")

    last = provider.last_activity_at(USER)
    assert last is not None
    assert_utc(last)
    assert last <= clock.now()

    window = provider.interactions_in_window(USER, clock.now() - timedelta(hours=24))
    assert isinstance(window, ActivityWindowRead)
    assert window.status is SourceStatus.AVAILABLE
    assert window.interactions
    for interaction in window.interactions:
        assert isinstance(interaction.interaction_id, str) and interaction.interaction_id
        assert_utc(interaction.occurred_at)

    count = provider.question_count(USER)
    assert isinstance(count, QuestionCountRead)
    assert isinstance(count.count, int) and count.count >= 0
    assert count.status is SourceStatus.AVAILABLE

    topics = provider.topics_in_window(USER, clock.now() - timedelta(days=7))
    assert isinstance(topics, TopicsRead)
    for mention in topics.topics:
        assert isinstance(mention.name, str) and mention.name
        assert_utc(mention.first_mentioned_at)


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_empty_is_reported_as_empty_and_never_as_a_failure(name, adapter_class):
    provider, clock = build(adapter_class, "empty")

    assert provider.last_activity_at(USER) is None

    window = provider.interactions_in_window(USER, clock.now() - timedelta(hours=24))
    assert window.interactions == ()
    assert window.status is SourceStatus.EMPTY

    count = provider.question_count(USER)
    assert count.status is SourceStatus.EMPTY
    assert count.count == 0

    topics = provider.topics_in_window(USER, clock.now() - timedelta(days=7))
    assert topics.topics == ()
    assert topics.status is SourceStatus.EMPTY


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_unavailable_raises_the_contract_error_on_every_read(name, adapter_class):
    provider, clock = build(adapter_class, "unavailable")
    for call in (
        lambda: provider.last_activity_at(USER),
        lambda: provider.interactions_in_window(USER, clock.now() - timedelta(hours=24)),
        lambda: provider.question_count(USER),
        lambda: provider.topics_in_window(USER, clock.now() - timedelta(days=7)),
    ):
        with pytest.raises(ProviderUnavailable) as caught:
            call()
        assert_no_leakage(caught.value, expected_port=PORT)


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_timeout_raises_the_contract_error_without_waiting(name, adapter_class):
    provider, clock = build(adapter_class, "timeout")
    before = clock.now()
    with pytest.raises(ProviderTimeout) as caught:
        provider.question_count(USER)
    assert_no_leakage(caught.value, expected_port=PORT)
    # The deadline is honoured by failing, not by sleeping.
    assert clock.now() == before


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_invalid_raises_the_contract_error(name, adapter_class):
    provider, clock = build(adapter_class, "invalid")
    with pytest.raises(ProviderInvalidResponse) as caught:
        provider.interactions_in_window(USER, clock.now() - timedelta(hours=24))
    assert_no_leakage(caught.value, expected_port=PORT)


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_a_window_never_returns_interactions_older_than_since(name, adapter_class):
    provider, clock = build(adapter_class, "available")
    since = clock.now() - timedelta(hours=24)
    window = provider.interactions_in_window(USER, since)
    assert all(interaction.occurred_at >= since for interaction in window.interactions)

    narrow = provider.interactions_in_window(USER, clock.now() - timedelta(minutes=1))
    assert narrow.interactions == ()
    assert narrow.status is SourceStatus.EMPTY


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_reads_are_repeatable_and_free_of_side_effects(name, adapter_class):
    provider, clock = build(adapter_class, "available")
    since = clock.now() - timedelta(hours=24)
    first = provider.interactions_in_window(USER, since)
    second = provider.interactions_in_window(USER, since)
    assert first == second
    assert provider.question_count(USER) == provider.question_count(USER)


def test_the_boundary_scenarios_are_positioned_exactly():
    """The 23h59m / 24h01m pair exists for boundary testing, in every family
    that declares it."""
    declaring = [
        (name, adapter_class)
        for name, adapter_class in ADAPTERS
        if set(BEHAVIOURAL_ACTIVITY_SCENARIOS) <= set(adapter_class.conformance_scenarios())
    ]
    assert len(declaring) >= 2, f"families declaring the behavioural set: {[n for n, _ in declaring]}"

    for name, adapter_class in declaring:
        inside, clock = build(adapter_class, "activity_23h59m_ago")
        last_inside = inside.last_activity_at(USER)
        assert isinstance(last_inside, datetime)
        assert clock.now() - last_inside == timedelta(hours=23, minutes=59), name

        outside, clock = build(adapter_class, "activity_24h01m_ago")
        last_outside = outside.last_activity_at(USER)
        assert isinstance(last_outside, datetime)
        assert clock.now() - last_outside == timedelta(hours=24, minutes=1), name

        same_day, clock = build(adapter_class, "multiple_interactions_same_day")
        window = same_day.interactions_in_window(USER, clock.now() - timedelta(hours=24))
        assert len(window.interactions) == 12, name
        assert {item.occurred_at.date() for item in window.interactions} == {clock.now().date()}, name


def test_the_question_count_scenarios_report_exactly_their_counts():
    from uc08.ports.conformance import BEHAVIOURAL_QUESTION_COUNTS

    declaring = [
        (name, adapter_class)
        for name, adapter_class in ADAPTERS
        if set(BEHAVIOURAL_ACTIVITY_SCENARIOS) <= set(adapter_class.conformance_scenarios())
    ]
    assert len(declaring) >= 2

    for name, adapter_class in declaring:
        for expected in BEHAVIOURAL_QUESTION_COUNTS:
            provider, _clock = build(adapter_class, f"question_count_{expected}")
            read = provider.question_count(USER)
            assert read.count == expected, (name, expected)
            assert read.status is SourceStatus.AVAILABLE
