"""Architecture test: the case file port and every adapter are read-only.

Read-only is a property of the SHAPE of the interface, not of anyone's
discipline. This test fails the build if a future engineer adds a mutating method
to the port or to any implementation of it - including one that has not been
written yet, because it walks the registry rather than a hard-coded list.
"""

from __future__ import annotations

import inspect

import pytest

from uc06.composition import PROVIDER_REGISTRY, REGISTRY
from uc06.config import Settings
from uc06.ports import FORBIDDEN_MUTATION_PREFIXES
from uc06.ports.case_file import CaseFileProvider

#: The complete, intended surface of the read-only port.
ALLOWED_METHODS = {"verify_read_access", "get_case_file"}

#: Attributes an adapter may expose beyond the port: test-visible counters and
#: private state. Anything else must justify itself in review.
ALLOWED_EXTRA = {"access_checks", "reads"}


def _public_methods(obj) -> set[str]:
    return {
        name
        for name, value in inspect.getmembers(obj)
        if not name.startswith("_") and (inspect.isfunction(value) or inspect.ismethod(value))
    }


def _case_file_adapters():
    """Every registered implementation of the case file port, resolved from the
    registry - so a newly registered adapter is covered automatically."""
    settings = Settings()
    return [
        (name, REGISTRY.resolve("case_file_provider", name, settings))
        for name in PROVIDER_REGISTRY["case_file_provider"]
    ]


class TestThePortItself:
    def test_the_port_declares_exactly_two_read_methods(self):
        assert _public_methods(CaseFileProvider) == ALLOWED_METHODS

    def test_the_port_declares_no_mutating_method(self):
        offenders = [
            name
            for name in _public_methods(CaseFileProvider)
            if name.lower().startswith(FORBIDDEN_MUTATION_PREFIXES)
        ]
        assert offenders == []

    def test_there_is_nothing_to_call(self):
        """Not "must not call" - cannot call. The names do not exist."""
        for verb in ("create", "update", "delete", "patch", "write", "save"):
            assert not hasattr(CaseFileProvider, verb)
            assert not hasattr(CaseFileProvider, f"{verb}_case_file")


@pytest.mark.parametrize("name,adapter", _case_file_adapters(), ids=[n for n, _ in _case_file_adapters()])
class TestEveryRegisteredAdapter:
    def test_exposes_no_mutating_method(self, name, adapter):
        offenders = [
            method
            for method in _public_methods(adapter)
            if method.lower().startswith(FORBIDDEN_MUTATION_PREFIXES)
        ]
        assert offenders == [], f"{name} exposes mutating methods: {offenders}"

    def test_exposes_nothing_beyond_the_port_and_allowed_extras(self, name, adapter):
        extra = _public_methods(adapter) - ALLOWED_METHODS
        assert extra == set(), f"{name} exposes unexpected methods: {sorted(extra)}"

    def test_public_attributes_are_limited_to_test_counters(self, name, adapter):
        attributes = {
            attribute
            for attribute in vars(adapter)
            if not attribute.startswith("_")
        }
        assert attributes <= ALLOWED_EXTRA, f"{name} exposes unexpected state: {sorted(attributes)}"

    def test_satisfies_the_port_protocol(self, name, adapter):
        assert isinstance(adapter, CaseFileProvider)


class TestTheTemplateSetsTheSameExample:
    def test_the_adapter_template_has_no_mutating_method(self):
        from uc06.adapters.real._template import TemplateCaseFileAdapter

        offenders = [
            method
            for method in _public_methods(TemplateCaseFileAdapter)
            if method.lower().startswith(FORBIDDEN_MUTATION_PREFIXES)
        ]
        assert offenders == []

    def test_the_template_implements_exactly_the_port(self):
        from uc06.adapters.real._template import TemplateCaseFileAdapter

        assert _public_methods(TemplateCaseFileAdapter) == ALLOWED_METHODS


class TestTheDomainModelIsAlsoImmutable:
    def test_a_loaded_case_file_cannot_be_edited(self, container):
        import dataclasses

        from uc06.adapters.mock.case_file import CASE_FULL

        case = container.case_files.get_case_file(CASE_FULL)
        with pytest.raises(dataclasses.FrozenInstanceError):
            case.practice_area = "changed"
        with pytest.raises(dataclasses.FrozenInstanceError):
            case.facts[0].text = "changed"

    def test_collections_on_a_case_file_are_tuples_not_lists(self, container):
        from uc06.adapters.mock.case_file import CASE_FULL

        case = container.case_files.get_case_file(CASE_FULL)
        assert isinstance(case.facts, tuple)
        assert isinstance(case.charges, tuple)
        assert isinstance(case.evidence, tuple)
        assert isinstance(case.legislation_notes, tuple)
