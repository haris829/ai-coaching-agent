"""Reusable contract suite for any InteractionProvider implementation.

Point it at a new adapter and it answers, in one command, whether the integration is
correct.  It asserts the *behavioural contract* -- types, error vocabulary, normalisation,
leakage, read-only shape, timeouts -- never any particular adapter's data.
"""

from __future__ import annotations

import inspect
import time
from datetime import UTC, datetime

import pytest

from uc10.domain.enums import (
    ExplanationProfile,
    NaricLevel,
    NaricLevelSource,
    ResponseCategory,
    SourceStatus,
    explanation_profile_for,
)
from uc10.domain.models import InteractionRecord
from uc10.ports.errors import (
    PortError,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    RecordNotFound,
)
from uc10.ports.interaction_provider import InteractionProvider

WRITE_VERBS = (
    "save",
    "write",
    "update",
    "delete",
    "remove",
    "create",
    "put",
    "post",
    "patch",
    "set",
    "insert",
    "upsert",
    "store",
    "persist",
    "annotate",
    "correct",
    "mutate",
    "supersede",
)

FAILURE_SCENARIOS = {
    "unavailable": ProviderUnavailable,
    "timeout": ProviderTimeout,
    "invalid": ProviderInvalidResponse,
}

#: A slow adapter must fail fast rather than hang. Generous, but finite.
TIMEOUT_BUDGET_SECONDS = 5.0


def _assert_no_leakage(case, rendered: str) -> None:
    for token in case.forbidden_tokens:
        assert token not in rendered, f"upstream vocabulary leaked past the boundary: {token}"


# ------------------------------------------------------------- shape of the port


def test_the_adapter_satisfies_the_port(case, provider):
    assert isinstance(provider, InteractionProvider)


def test_the_adapter_exposes_no_mutating_method(case, provider):
    """READ ONLY BY SHAPE. A provider that can write is a contract violation."""
    public = [
        name
        for name, member in inspect.getmembers(type(provider), callable)
        if not name.startswith("_")
    ]
    offenders = [
        name for name in public if any(name.lower().startswith(verb) for verb in WRITE_VERBS)
    ]
    assert offenders == [], f"InteractionProvider adapters must not write: {offenders}"


# --------------------------------------------------------------- happy path


def test_get_returns_a_platform_interaction_record(case, provider, scenario):
    if scenario in FAILURE_SCENARIOS:
        return  # failure scenarios have their own contract tests below
    record = provider.get(case.ids[scenario])

    assert isinstance(record, InteractionRecord)
    assert record.interaction_id == case.ids[scenario]
    assert isinstance(record.naric_level, NaricLevel)
    assert isinstance(record.naric_level_source, NaricLevelSource)
    assert isinstance(record.explanation_profile, ExplanationProfile)
    assert isinstance(record.naric_source_status, SourceStatus)
    assert isinstance(record.source_status, SourceStatus)
    assert isinstance(record.response_category, ResponseCategory)
    assert record.topic_tag == record.topic_tag.lower()
    assert record.session_mode == record.session_mode.lower()
    assert record.session_id and record.user_id
    if record.course_completion_percent is not None:
        assert isinstance(record.course_completion_percent, int)
        assert 0 <= record.course_completion_percent <= 100
    assert record.delivered_at.tzinfo is not None
    assert record.delivered_at.utcoffset() == UTC.utcoffset(None)


def test_values_are_normalised_to_the_platform_contract(case, provider, scenario):
    """Whatever the upstream sent, a NARIC level arrives as the platform enum and the
    explanation profile agrees with it."""
    if scenario in FAILURE_SCENARIOS:
        return
    record = provider.get(case.ids[scenario])
    assert record.naric_level in set(NaricLevel)
    assert record.explanation_profile is explanation_profile_for(record.naric_level)
    if record.naric_source_status in (SourceStatus.INVALID, SourceStatus.EMPTY):
        assert record.naric_level is NaricLevel.LEVEL_5
        assert record.naric_level_source is NaricLevelSource.DEFAULT


def test_an_unmappable_level_is_defaulted_and_never_guessed(case, provider):
    if "unmapped_level" not in case.ids:
        return
    record = provider.get(case.ids["unmapped_level"])
    assert record.naric_level is NaricLevel.LEVEL_5
    assert record.naric_level_source is NaricLevelSource.DEFAULT
    assert record.naric_source_status in (SourceStatus.INVALID, SourceStatus.EMPTY)


def test_delivered_at_agrees_with_the_record_and_is_utc(case, provider, scenario):
    if scenario in FAILURE_SCENARIOS:
        return
    interaction_id = case.ids[scenario]
    delivered = provider.delivered_at(interaction_id)
    assert isinstance(delivered, datetime)
    assert delivered.tzinfo is not None
    assert abs((delivered - provider.get(interaction_id).delivered_at).total_seconds()) < 1


def test_reads_are_side_effect_free(case, provider, scenario):
    if scenario in FAILURE_SCENARIOS:
        return
    first = provider.get(case.ids[scenario])
    second = provider.get(case.ids[scenario])
    assert first == second


def test_no_response_category_is_rejected_by_the_adapter(case, provider, scenario):
    """An unrecognised category maps to ``unknown`` -- never an error, never unrateable."""
    if scenario in FAILURE_SCENARIOS:
        return
    assert provider.get(case.ids[scenario]).response_category in set(ResponseCategory)


def test_no_learner_content_leaks_through_identifiers_or_metadata(case, provider, scenario):
    if scenario in FAILURE_SCENARIOS:
        return
    record = provider.get(case.ids[scenario])
    metadata = {
        "interaction_id": record.interaction_id,
        "topic_tag": record.topic_tag,
        "session_mode": record.session_mode,
        "naric_level": record.naric_level.value,
    }
    _assert_no_leakage(case, str(metadata))


# ------------------------------------------------------------- failure modes


@pytest.mark.parametrize("failure", sorted(FAILURE_SCENARIOS))
def test_every_documented_failure_mode_raises_the_correct_contract_error(
    case, provider, failure
):
    if failure not in case.ids:
        return
    expected = FAILURE_SCENARIOS[failure]
    for call in (provider.get, provider.delivered_at):
        with pytest.raises(expected) as raised:
            call(case.ids[failure])
        assert raised.value.reason_code == raised.value.reason_code.lower()
        assert " " not in raised.value.reason_code
        _assert_no_leakage(case, str(raised.value))


def test_an_unknown_identifier_raises_record_not_found(case, provider):
    for call in (provider.get, provider.delivered_at):
        with pytest.raises(RecordNotFound) as raised:
            call(case.unknown_id)
        assert raised.value.retryable is False
        _assert_no_leakage(case, str(raised.value))


def test_a_timeout_is_honoured_rather_than_hanging(case, provider):
    if "timeout" not in case.ids:
        return
    started = time.monotonic()
    with pytest.raises(ProviderTimeout) as raised:
        provider.get(case.ids["timeout"])
    assert time.monotonic() - started < TIMEOUT_BUDGET_SECONDS
    assert raised.value.retryable is True


def test_unavailable_is_retryable_and_distinct_from_empty(case, provider):
    if "unavailable" not in case.ids:
        return
    with pytest.raises(ProviderUnavailable) as raised:
        provider.get(case.ids["unavailable"])
    assert raised.value.retryable is True


def test_an_unmappable_payload_is_not_presented_as_retryable(case, provider):
    if "invalid" not in case.ids:
        return
    with pytest.raises(ProviderInvalidResponse) as raised:
        provider.get(case.ids["invalid"])
    assert raised.value.retryable is False


def test_the_adapter_raises_nothing_outside_the_contract_vocabulary(case, provider):
    """Every identifier the case knows about, plus an unknown one: any exception that
    escapes must be a typed contract error."""
    for interaction_id in [*case.ids.values(), case.unknown_id]:
        for call in (provider.get, provider.delivered_at):
            try:
                call(interaction_id)
            except PortError:
                pass
            except Exception as exc:
                raise AssertionError(
                    f"{type(exc).__name__} escaped the port boundary for {interaction_id}"
                ) from exc
