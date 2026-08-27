"""CaseFileProvider conformance. Adapter-agnostic.

Point it at any registered adapter with --adapter-family. Nothing here is
hard-coded to the mock: identifiers come from the scenario map, and every
assertion is about the platform contract.
"""

from __future__ import annotations

import inspect

import pytest

from uc06.domain.enums import SourceStatus
from uc06.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc06.domain.models import CASE_PREP_AGENT_ORIGIN, AccessRecord, CaseFile
from uc06.ports import FORBIDDEN_MUTATION_PREFIXES
from uc06.ports.case_file import CaseFileProvider

from .conftest import scenario

ACTOR = "conformance-user"

#: Upstream vocabulary that must never survive the mapping.
LEAK_MARKERS = (
    "envelope",
    "matterRef",
    "particulars",
    "narrative",
    "producedBy",
    "practiceGroup",
    "TODO_",
    "MatterSphere",
    "UPSTREAM_",
)


class TestShape:
    def test_it_satisfies_the_port(self, case_file_adapter, case_scenarios):
        assert isinstance(case_file_adapter, CaseFileProvider)

    def test_the_read_only_surface_is_intact(self, case_file_adapter, case_scenarios):
        methods = [
            name
            for name, value in inspect.getmembers(case_file_adapter)
            if not name.startswith("_") and callable(value)
        ]
        offenders = [m for m in methods if m.lower().startswith(FORBIDDEN_MUTATION_PREFIXES)]
        assert offenders == [], f"read-only port exposes mutating methods: {offenders}"

    def test_the_signatures_match_the_port(self, case_file_adapter, case_scenarios):
        assert list(inspect.signature(case_file_adapter.get_case_file).parameters) == ["case_file_id"]
        assert list(inspect.signature(case_file_adapter.verify_read_access).parameters) == [
            "user_id",
            "case_file_id",
        ]


class TestReturnTypes:
    def test_get_case_file_returns_the_platform_type(self, case_file_adapter, case_scenarios):
        case = case_file_adapter.get_case_file(scenario(case_scenarios, "readable"))
        assert isinstance(case, CaseFile)
        assert isinstance(case.source_status, SourceStatus)
        assert isinstance(case.facts, tuple)

    def test_verify_read_access_returns_the_platform_type(self, case_file_adapter, case_scenarios):
        record = case_file_adapter.verify_read_access(ACTOR, scenario(case_scenarios, "readable"))
        assert isinstance(record, AccessRecord)
        assert isinstance(record.granted, bool)
        assert record.checked_at is not None
        assert record.case_file_id == scenario(case_scenarios, "readable")

    def test_every_fact_carries_a_stable_identifier(self, case_file_adapter, case_scenarios):
        case = case_file_adapter.get_case_file(scenario(case_scenarios, "readable"))
        ids = [fact.fact_id for fact in case.facts]
        assert all(isinstance(i, str) and i for i in ids)
        assert len(ids) == len(set(ids))

    def test_reading_twice_returns_the_same_identifiers(self, case_file_adapter, case_scenarios):
        """Identifiers must be stable: explanations are verified against them."""
        first = case_file_adapter.get_case_file(scenario(case_scenarios, "readable"))
        second = case_file_adapter.get_case_file(scenario(case_scenarios, "readable"))
        assert first.fact_ids() == second.fact_ids()

    def test_origin_is_normalised_to_the_platform_value(self, case_file_adapter, case_scenarios):
        case = case_file_adapter.get_case_file(scenario(case_scenarios, "readable"))
        assert case.origin_system == CASE_PREP_AGENT_ORIGIN
        assert case.from_case_prep_agent is True

    def test_a_case_file_from_elsewhere_is_not_marked_as_case_prep(self, case_file_adapter, case_scenarios):
        case = case_file_adapter.get_case_file(scenario(case_scenarios, "foreign_origin"))
        assert case.from_case_prep_agent is False


class TestSourceStatusVocabulary:
    def test_a_complete_case_file_is_available(self, case_file_adapter, case_scenarios):
        case = case_file_adapter.get_case_file(scenario(case_scenarios, "readable"))
        assert case.source_status is SourceStatus.AVAILABLE

    def test_a_case_file_missing_sections_is_partial_not_empty(self, case_file_adapter, case_scenarios):
        case = case_file_adapter.get_case_file(scenario(case_scenarios, "partial"))
        assert case.source_status is SourceStatus.PARTIAL
        assert case.facts, "partial means some sections are missing, not that it is empty"

    def test_an_empty_case_file_is_empty_not_unavailable(self, case_file_adapter, case_scenarios):
        """`empty` and `unavailable` are different states and must never be
        conflated: empty means the upstream answered and held nothing."""
        case = case_file_adapter.get_case_file(scenario(case_scenarios, "empty"))
        assert case.source_status is SourceStatus.EMPTY
        assert case.facts == ()


class TestFailureModes:
    def test_an_unreachable_upstream_raises_provider_unavailable(self, case_file_adapter, case_scenarios):
        with pytest.raises(ProviderUnavailable):
            case_file_adapter.get_case_file(scenario(case_scenarios, "unavailable"))

    def test_a_slow_upstream_raises_provider_timeout(self, case_file_adapter, case_scenarios):
        with pytest.raises(ProviderTimeout):
            case_file_adapter.get_case_file(scenario(case_scenarios, "timeout"))

    def test_an_unmappable_payload_raises_provider_invalid_response(self, case_file_adapter, case_scenarios):
        with pytest.raises(ProviderInvalidResponse):
            case_file_adapter.get_case_file(scenario(case_scenarios, "invalid"))

    def test_access_denial_is_a_returned_decision_not_an_exception(self, case_file_adapter, case_scenarios):
        record = case_file_adapter.verify_read_access(
            ACTOR, scenario(case_scenarios, "access_denied")
        )
        assert record.granted is False
        assert record.reason_code

    def test_an_access_check_against_an_unreachable_upstream_raises(self, case_file_adapter, case_scenarios):
        with pytest.raises((ProviderUnavailable, ProviderTimeout)):
            case_file_adapter.verify_read_access(ACTOR, scenario(case_scenarios, "unavailable"))

    def test_no_other_exception_type_escapes(self, case_file_adapter, case_scenarios):
        """Every failure must be one of the three contract exceptions."""
        from uc06.domain.errors import ProviderError

        for key in ("unavailable", "invalid", "timeout"):
            identifier = scenario(case_scenarios, key)
            try:
                case_file_adapter.get_case_file(identifier)
            except ProviderError:
                pass
            except Exception as exc:  # noqa: BLE001 - that is the point of the test
                pytest.fail(f"{key}: uncontracted exception escaped the adapter: {type(exc).__name__}")


class TestBoundaryHygiene:
    def test_no_upstream_detail_escapes_in_a_loaded_case_file(self, case_file_adapter, case_scenarios):
        case = case_file_adapter.get_case_file(scenario(case_scenarios, "readable"))
        blob = repr(case)
        for marker in LEAK_MARKERS:
            assert marker not in blob, f"upstream vocabulary {marker!r} escaped the adapter"

    def test_no_upstream_error_text_escapes_in_a_contract_exception(self, case_file_adapter, case_scenarios):
        from uc06.domain.errors import ProviderError

        for key in ("unavailable", "invalid", "timeout"):
            identifier = scenario(case_scenarios, key)
            try:
                case_file_adapter.get_case_file(identifier)
            except ProviderError as exc:
                message = str(exc)
                for marker in LEAK_MARKERS:
                    assert marker not in message, f"{marker!r} leaked in an exception message"
                assert type(case_file_adapter).__name__ not in message, "provider name leaked"

    def test_the_port_name_not_the_provider_name_is_reported(self, case_file_adapter, case_scenarios):
        from uc06.domain.errors import ProviderError

        try:
            case_file_adapter.get_case_file(scenario(case_scenarios, "unavailable"))
        except ProviderError as exc:
            assert exc.port == "case_file_provider"
