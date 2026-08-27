"""The provider registry: one lookup, one line, and a loud failure."""

from __future__ import annotations

import ast
import pathlib

import pytest

from uc08.adapters.clock.clocks import FixedClock
from uc08.adapters.foreign.activity import ForeignActivityAdapter
from uc08.adapters.mock.activity import MockActivityProvider
from uc08.composition import build_container
from uc08.config import load_settings
from uc08.domain.errors import ProviderNotRegistered, ProviderRegistrationBroken
from uc08.ports.upstream import ActivityProvider, GapReportProvider
from uc08.registry import (
    ACTIVITY_PROVIDERS,
    GAP_REPORT_PROVIDERS,
    ProviderEntry,
    build_provider,
    registered_classes,
    registered_names,
    resolve_provider_class,
)
from tests.conftest import ANCHOR

REGISTRY_PATH = pathlib.Path(__file__).resolve().parents[2] / "uc08" / "registry.py"


def test_selection_is_a_lookup_and_returns_the_registered_class():
    assert resolve_provider_class("activity", "mock") is MockActivityProvider
    assert resolve_provider_class("activity", "foreign_lexicon") is ForeignActivityAdapter
    assert "mock" in registered_names("gap_report")


def test_building_a_provider_uses_the_shared_construction_signature():
    clock = FixedClock(ANCHOR)
    activity = build_provider("activity", "mock", clock, timeout_seconds=3.0)
    gap = build_provider("gap_report", "foreign_lexicon", clock, timeout_seconds=3.0)

    assert isinstance(activity, ActivityProvider)
    assert isinstance(gap, GapReportProvider)
    assert activity.timeout_seconds == 3.0
    assert gap.timeout_seconds == 3.0


def test_an_unknown_provider_name_fails_loudly_and_names_what_is_missing():
    with pytest.raises(ProviderNotRegistered) as caught:
        resolve_provider_class("activity", "company")

    message = str(caught.value)
    assert "ACTIVITY_PROVIDER='company'" in message
    assert "ACTIVITY_PROVIDERS" in message  # the table to edit
    assert "uc08/registry.py" in message  # the file to edit
    assert "uc08/adapters/real/_template.py" in message  # where to start
    assert "ActivityProvider" in message  # the interface to implement
    assert "no fallback" in message


def test_an_unknown_provider_name_stops_the_service_from_starting():
    with pytest.raises(ProviderNotRegistered):
        build_container(load_settings(ACTIVITY_PROVIDER="company"), clock=FixedClock(ANCHOR))

    with pytest.raises(ProviderNotRegistered):
        build_container(load_settings(GAP_REPORT_PROVIDER="company"), clock=FixedClock(ANCHOR))


def test_there_is_no_silent_fallback_to_a_mock():
    """The failure path must not hand back a working mock."""
    with pytest.raises(ProviderNotRegistered):
        build_container(load_settings(ACTIVITY_PROVIDER="not-registered"), clock=FixedClock(ANCHOR))

    source = REGISTRY_PATH.read_text(encoding="utf-8")
    # No default, no .get with a fallback value, no except that substitutes one.
    assert 'table.get(name, ' not in source
    assert '"mock")' not in source.replace('"mock": ProviderEntry(', "")


def test_a_broken_registry_entry_is_reported_precisely(monkeypatch):
    monkeypatch.setitem(
        ACTIVITY_PROVIDERS, "typo_module", ProviderEntry("uc08.adapters.real.nope:Whatever", "x")
    )
    with pytest.raises(ProviderRegistrationBroken) as caught:
        resolve_provider_class("activity", "typo_module")
    assert "uc08/adapters/real/nope.py" in str(caught.value)

    monkeypatch.setitem(
        ACTIVITY_PROVIDERS, "typo_class", ProviderEntry("uc08.adapters.mock.activity:Nope", "x")
    )
    with pytest.raises(ProviderRegistrationBroken) as caught:
        resolve_provider_class("activity", "typo_class")
    assert "has no 'Nope'" in str(caught.value)

    monkeypatch.setitem(ACTIVITY_PROVIDERS, "malformed", ProviderEntry("no-colon-here", "x"))
    with pytest.raises(ProviderRegistrationBroken):
        resolve_provider_class("activity", "malformed")

    monkeypatch.setitem(
        ACTIVITY_PROVIDERS, "wrong_port", ProviderEntry("uc08.adapters.mock.gap_report:MockGapReportProvider", "x")
    )
    with pytest.raises(ProviderRegistrationBroken) as caught:
        resolve_provider_class("activity", "wrong_port")
    assert "does not implement" in str(caught.value)


def test_registering_a_provider_is_one_line_in_one_file(monkeypatch):
    """The whole integration cost, demonstrated.

    A single dict entry makes a brand-new adapter reachable, and the conformance
    suite picks it up with no test change.
    """
    monkeypatch.setitem(
        ACTIVITY_PROVIDERS,
        "another_family",
        ProviderEntry("uc08.adapters.foreign.activity:ForeignActivityAdapter", "another"),
    )
    assert "another_family" in registered_names("activity")
    assert registered_classes("activity")["another_family"] is ForeignActivityAdapter

    # Nothing else changed, and the new name is immediately usable end to end.
    built = build_provider("activity", "another_family", FixedClock(ANCHOR), timeout_seconds=2.0)
    assert isinstance(built, ActivityProvider)
    assert built.timeout_seconds == 2.0


def test_the_registry_contains_no_provider_conditionals():
    tree = ast.parse(REGISTRY_PATH.read_text(encoding="utf-8"))
    provider_names = set(ACTIVITY_PROVIDERS) | set(GAP_REPORT_PROVIDERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            constants = {
                operand.value
                for operand in [node.left, *node.comparators]
                if isinstance(operand, ast.Constant) and isinstance(operand.value, str)
            }
            assert not constants & provider_names, (
                f"registry.py:{node.lineno} branches on a provider name; selection must be a lookup"
            )


def test_the_registry_tables_are_plain_one_line_entries():
    for table in (ACTIVITY_PROVIDERS, GAP_REPORT_PROVIDERS):
        assert table
        for name, entry in table.items():
            assert isinstance(name, str) and name
            assert isinstance(entry, ProviderEntry)
            assert ":" in entry.target
            assert entry.summary


def test_only_the_registry_names_adapter_classes():
    """No other file learns that a particular adapter exists."""
    root = pathlib.Path(__file__).resolve().parents[2] / "uc08"
    adapter_class_names = ("MockActivityProvider", "MockGapReportProvider", "ForeignActivityAdapter", "ForeignGapReportAdapter")
    for path in root.rglob("*.py"):
        relative = path.relative_to(root.parent).as_posix()
        if relative == "uc08/registry.py" or "/adapters/" in relative:
            continue
        source = path.read_text(encoding="utf-8")
        for class_name in adapter_class_names:
            assert class_name not in source, f"{relative} names {class_name}"
