"""FeedbackProvider conformance. Adapter-agnostic: parameterized by adapter."""

from __future__ import annotations

import pytest

from tests.conformance.adapters import FEEDBACK_CASES, AdapterCase
from tests.conformance.shared import (
    assert_error_is_opaque,
    assert_no_upstream_leakage,
    assert_read_only,
    assert_utc,
)
from uc07.domain.enums import Rating, SourceStatus
from uc07.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import FeedbackRecord
from uc07.ports.read_only import FeedbackProvider

pytestmark = pytest.mark.parametrize("case", FEEDBACK_CASES, ids=lambda case: case.id)


def _ids(case: AdapterCase) -> tuple[str, ...]:
    return tuple(case.extras["known_interaction_ids"])


def test_adapter_implements_the_port(case: AdapterCase):
    assert isinstance(case.build(), FeedbackProvider)


def test_adapter_is_read_only(case: AdapterCase):
    assert_read_only(case.build())


def test_returns_domain_records(case: AdapterCase):
    records = case.build().for_interactions(_ids(case))
    assert records
    for record in records:
        assert isinstance(record, FeedbackRecord)
        assert isinstance(record.rating, Rating)
        assert record.user_id == case.user_id
        assert_utc(record.rated_at)


def test_ratings_are_normalised_to_the_platform_vocabulary(case: AdapterCase):
    for record in case.build().for_interactions(_ids(case)):
        assert record.rating.value in {"up", "down"}


def test_only_requested_interactions_are_returned(case: AdapterCase):
    wanted = _ids(case)[:1]
    for record in case.build().for_interactions(wanted):
        assert record.interaction_id in wanted


def test_no_upstream_payload_leaks_into_domain_records(case: AdapterCase):
    for record in case.build().for_interactions(_ids(case)):
        assert_no_upstream_leakage(record, case.upstream_tokens)


def test_status_is_a_source_status(case: AdapterCase):
    assert isinstance(case.build().status_for_interactions(_ids(case)), SourceStatus)


def test_empty_source_is_reported_as_empty_not_unavailable(case: AdapterCase):
    adapter = case.build_empty()
    assert adapter.for_interactions(_ids(case)) == ()
    assert adapter.status_for_interactions(_ids(case)) is SourceStatus.EMPTY


def test_unavailable_source_raises_provider_unavailable(case: AdapterCase):
    with pytest.raises(ProviderUnavailable) as excinfo:
        case.build_unavailable().for_interactions(_ids(case))
    assert excinfo.value.port.value == "feedback"
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_timeout_raises_provider_timeout(case: AdapterCase):
    with pytest.raises(ProviderTimeout) as excinfo:
        case.build_timeout().for_interactions(_ids(case))
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_contract_breach_raises_provider_invalid_response(case: AdapterCase):
    with pytest.raises(ProviderInvalidResponse) as excinfo:
        case.build_invalid().for_interactions(_ids(case))
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_reads_are_repeatable_and_side_effect_free(case: AdapterCase):
    adapter = case.build()
    assert adapter.for_interactions(_ids(case)) == adapter.for_interactions(_ids(case))


def test_unknown_interaction_ids_yield_nothing(case: AdapterCase):
    assert case.build().for_interactions(["no-such-interaction"]) == ()
