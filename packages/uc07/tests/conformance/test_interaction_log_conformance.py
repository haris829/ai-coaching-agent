"""InteractionLogProvider conformance. Adapter-agnostic: parameterized by adapter."""

from __future__ import annotations

import pytest

from tests.conformance.adapters import INTERACTION_LOG_CASES, AdapterCase
from tests.conformance.shared import (
    assert_error_is_opaque,
    assert_no_upstream_leakage,
    assert_read_only,
    assert_utc,
)
from uc07.domain.enums import NaricLevel, RatingState, SourceStatus
from uc07.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import InteractionRecord
from uc07.ports.read_only import InteractionLogProvider

pytestmark = pytest.mark.parametrize(
    "case", INTERACTION_LOG_CASES, ids=lambda case: case.id
)


def test_adapter_implements_the_port(case: AdapterCase):
    assert isinstance(case.build(), InteractionLogProvider)


def test_adapter_is_read_only(case: AdapterCase):
    assert_read_only(case.build())


def test_returns_domain_records(case: AdapterCase):
    records = case.build().for_user(case.user_id)
    assert records
    for record in records:
        assert isinstance(record, InteractionRecord)
        assert isinstance(record.naric_level, NaricLevel)
        assert isinstance(record.rating_state, RatingState)
        assert isinstance(record.explain_differently_count, int)
        assert record.user_id == case.user_id
        assert_utc(record.asked_at)


def test_values_are_normalised_not_upstream_spellings(case: AdapterCase):
    for record in case.build().for_user(case.user_id):
        assert record.naric_level.value.startswith("LEVEL_")
        assert record.rating_state.value in {"pending", "rated"}
        assert record.follow_up_of is None or record.follow_up_of.strip()


def test_no_upstream_payload_leaks_into_domain_records(case: AdapterCase):
    for record in case.build().for_user(case.user_id):
        assert_no_upstream_leakage(record, case.upstream_tokens)


def test_count_is_a_non_negative_integer(case: AdapterCase):
    assert case.build().count_for_user(case.user_id) >= 0


def test_status_is_a_source_status(case: AdapterCase):
    assert isinstance(case.build().status_for_user(case.user_id), SourceStatus)


def test_empty_source_is_reported_as_empty_not_unavailable(case: AdapterCase):
    assert case.build_empty is not None, "every case must supply an empty source"
    adapter = case.build_empty()
    assert adapter.for_user(case.user_id) == ()
    assert adapter.status_for_user(case.user_id) is SourceStatus.EMPTY
    assert adapter.count_for_user(case.user_id) == 0


def test_unavailable_source_raises_provider_unavailable(case: AdapterCase):
    with pytest.raises(ProviderUnavailable) as excinfo:
        case.build_unavailable().for_user(case.user_id)
    assert excinfo.value.port.value == "interaction_log"
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_timeout_raises_provider_timeout(case: AdapterCase):
    with pytest.raises(ProviderTimeout) as excinfo:
        case.build_timeout().for_user(case.user_id)
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_contract_breach_raises_provider_invalid_response(case: AdapterCase):
    with pytest.raises(ProviderInvalidResponse) as excinfo:
        case.build_invalid().for_user(case.user_id)
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_reads_are_repeatable_and_side_effect_free(case: AdapterCase):
    adapter = case.build()
    first = adapter.for_user(case.user_id)
    second = adapter.for_user(case.user_id)
    assert first == second
    assert adapter.status_for_user(case.user_id) is adapter.status_for_user(case.user_id)


def test_unknown_learner_is_empty_not_a_failure(case: AdapterCase):
    adapter = case.build()
    assert adapter.for_user("learner-who-does-not-exist") == ()
