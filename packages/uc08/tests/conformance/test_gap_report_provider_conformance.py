"""GapReportProvider contract conformance. Adapter-agnostic.

    python -m pytest tests/conformance/test_gap_report_provider_conformance.py -q
"""

from __future__ import annotations

import pytest

from uc08.domain.enums import ExplanationProfile, NaricLevel, NaricLevelSource, SourceStatus
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import Topic
from uc08.ports.conformance import (
    BEHAVIOURAL_GAP_REPORT_SCENARIOS,
    CONFORMANCE_USER_ID,
    REQUIRED_CONFORMANCE_SCENARIOS,
)
from uc08.ports.upstream import MUTATING_NAME_FRAGMENTS, READ_ONLY_PORTS
from tests.conformance.conftest import adapters_for, assert_no_leakage, build, scenarios_of

PORT = "gap_report"
ADAPTERS = adapters_for(PORT)
USER = CONFORMANCE_USER_ID


def test_at_least_two_independent_adapter_families_are_registered():
    assert len(ADAPTERS) >= 2, f"registered gap report adapters: {[name for name, _ in ADAPTERS]}"


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_declares_every_required_scenario(name, adapter_class):
    builders = scenarios_of(adapter_class)
    for scenario in REQUIRED_CONFORMANCE_SCENARIOS:
        assert callable(builders[scenario])


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_exposes_only_the_read_method_of_the_port(name, adapter_class):
    allowed = READ_ONLY_PORTS["GapReportProvider"]
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
    _provider, clock = build(adapter_class, "available")
    instance = adapter_class(clock, timeout_seconds=2.5)
    assert instance.timeout_seconds == pytest.approx(2.5)


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_available_returns_a_platform_topic(name, adapter_class):
    provider, _clock = build(adapter_class, "available")

    topic = provider.suggested_topic(USER)

    assert isinstance(topic, Topic)
    assert topic.topic_id and topic.name
    # Normalised to the platform contract whatever the upstream sent.
    assert isinstance(topic.naric_level, NaricLevel)
    assert topic.naric_level_source is NaricLevelSource.RETRIEVED
    assert topic.naric_level_status is SourceStatus.AVAILABLE
    assert isinstance(topic.explanation_profile, ExplanationProfile)
    assert topic.course_progress_percent is None or 0 <= topic.course_progress_percent <= 100
    assert isinstance(topic.course_progress_percent, (int, type(None)))
    # Serialised values are lowercase.
    dumped = topic.model_dump(mode="json")
    for key in ("naric_level", "naric_level_source", "naric_level_status", "explanation_profile"):
        assert dumped[key] == dumped[key].lower()


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_no_suggestion_is_none_not_an_error_and_not_an_invention(name, adapter_class):
    provider, _clock = build(adapter_class, "empty")
    assert provider.suggested_topic(USER) is None


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_unavailable_raises_the_contract_error(name, adapter_class):
    provider, _clock = build(adapter_class, "unavailable")
    with pytest.raises(ProviderUnavailable) as caught:
        provider.suggested_topic(USER)
    assert_no_leakage(caught.value, expected_port=PORT)


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_timeout_raises_the_contract_error_without_waiting(name, adapter_class):
    provider, clock = build(adapter_class, "timeout")
    before = clock.now()
    with pytest.raises(ProviderTimeout) as caught:
        provider.suggested_topic(USER)
    assert_no_leakage(caught.value, expected_port=PORT)
    assert clock.now() == before


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_invalid_raises_the_contract_error(name, adapter_class):
    provider, _clock = build(adapter_class, "invalid")
    with pytest.raises(ProviderInvalidResponse) as caught:
        provider.suggested_topic(USER)
    assert_no_leakage(caught.value, expected_port=PORT)


@pytest.mark.parametrize(("name", "adapter_class"), ADAPTERS)
def test_reads_are_repeatable_and_free_of_side_effects(name, adapter_class):
    provider, _clock = build(adapter_class, "available")
    assert provider.suggested_topic(USER) == provider.suggested_topic(USER)


def test_an_unmappable_level_degrades_to_the_platform_default_rather_than_guessing():
    """A value mapping to no enum member is an invalid response, not a level.

    Runs over every family that declares the scenario; at least two must.
    """
    declaring = [
        (name, adapter_class)
        for name, adapter_class in ADAPTERS
        if "unmappable_level" in adapter_class.conformance_scenarios()
    ]
    assert len(declaring) >= 2, f"families declaring unmappable_level: {[n for n, _ in declaring]}"

    for name, adapter_class in declaring:
        provider, _clock = build(adapter_class, "unmappable_level")
        topic = provider.suggested_topic(USER)
        assert topic is not None, name
        assert topic.naric_level is NaricLevel.LEVEL_5, name
        assert topic.naric_level_source is NaricLevelSource.DEFAULT, name
        assert topic.naric_level_status is SourceStatus.INVALID, name
        assert topic.explanation_profile is ExplanationProfile.INTERMEDIATE, name
        # An ambiguous 0-1 completion fraction is invalid, never multiplied by 100.
        assert topic.course_progress_percent is None, name
        assert topic.course_progress_status is SourceStatus.INVALID, name


def test_every_family_agrees_on_the_normalised_suggestion():
    """The same topic, sent in different upstream shapes, arrives identically."""
    declaring = [
        (name, adapter_class)
        for name, adapter_class in ADAPTERS
        if set(BEHAVIOURAL_GAP_REPORT_SCENARIOS) <= set(adapter_class.conformance_scenarios())
    ]
    assert len(declaring) >= 2

    normalised = {}
    for name, adapter_class in declaring:
        provider, _clock = build(adapter_class, "suggestion_available")
        normalised[name] = provider.suggested_topic(USER)

    distinct = {topic.model_dump_json() for topic in normalised.values()}
    assert len(distinct) == 1, f"families disagree after normalisation: {normalised}"
