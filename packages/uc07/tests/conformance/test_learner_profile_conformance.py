"""LearnerProfileProvider conformance. Adapter-agnostic."""

from __future__ import annotations

import pytest

from tests.conformance.adapters import PROFILE_CASES, AdapterCase
from tests.conformance.shared import (
    assert_error_is_opaque,
    assert_no_upstream_leakage,
    assert_read_only,
)
from uc07.domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from uc07.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import LearnerProfile
from uc07.ports.read_only import LearnerProfileProvider

pytestmark = pytest.mark.parametrize("case", PROFILE_CASES, ids=lambda case: case.id)


def test_adapter_implements_the_port(case: AdapterCase):
    assert isinstance(case.build(), LearnerProfileProvider)


def test_adapter_is_read_only(case: AdapterCase):
    assert_read_only(case.build())


def test_returns_a_domain_profile(case: AdapterCase):
    profile = case.build().get_profile(case.user_id)
    assert isinstance(profile, LearnerProfile)
    assert profile.user_id == case.user_id
    assert isinstance(profile.speciality_status, SourceStatus)
    assert profile.speciality_areas


def test_naric_values_are_normalised(case: AdapterCase):
    profile = case.build().get_profile(case.user_id)
    if profile.naric_level is not None:
        assert isinstance(profile.naric_level, NaricLevel)
        assert isinstance(profile.naric_level_source, NaricLevelSource)
        assert profile.naric_level.value.startswith("LEVEL_")


def test_no_upstream_payload_leaks_into_the_profile(case: AdapterCase):
    assert_no_upstream_leakage(
        case.build().get_profile(case.user_id), case.upstream_tokens
    )


def test_no_speciality_is_reported_as_empty_not_unavailable(case: AdapterCase):
    profile = case.build_empty().get_profile(case.user_id)
    assert profile.speciality_status is SourceStatus.EMPTY
    assert profile.speciality_areas == ()


def test_unavailable_source_raises_provider_unavailable(case: AdapterCase):
    with pytest.raises(ProviderUnavailable) as excinfo:
        case.build_unavailable().get_profile(case.user_id)
    assert excinfo.value.port.value == "learner_profile"
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_timeout_raises_provider_timeout(case: AdapterCase):
    with pytest.raises(ProviderTimeout) as excinfo:
        case.build_timeout().get_profile(case.user_id)
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_contract_breach_raises_provider_invalid_response(case: AdapterCase):
    with pytest.raises(ProviderInvalidResponse) as excinfo:
        case.build_invalid().get_profile(case.user_id)
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_reads_are_repeatable_and_side_effect_free(case: AdapterCase):
    adapter = case.build()
    assert adapter.get_profile(case.user_id) == adapter.get_profile(case.user_id)


def test_unknown_learner_yields_an_empty_speciality_not_an_invented_one(
    case: AdapterCase,
):
    profile = case.build().get_profile("learner-who-does-not-exist")
    assert profile.speciality_areas == ()
    assert profile.speciality_status is SourceStatus.EMPTY
