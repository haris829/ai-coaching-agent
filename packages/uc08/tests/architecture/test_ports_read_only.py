"""Upstream ports and adapters expose no mutating method.

``ActivityProvider`` and ``GapReportProvider`` are read-only *by shape*: the
guarantee is not a convention or a comment, it is that no write method exists to
call. Checked against the interfaces, against every registered adapter, and
against every module in the adapter packages that implements either port.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from uc08.ports.upstream import (
    MUTATING_NAME_FRAGMENTS,
    READ_ONLY_PORTS,
    ActivityProvider,
    GapReportProvider,
)
from uc08.registry import registered_classes

UC08_ROOT = pathlib.Path(__file__).resolve().parents[2] / "uc08"

PORT_CLASSES = {"ActivityProvider": ActivityProvider, "GapReportProvider": GapReportProvider}


def _public_callables(target: type) -> set[str]:
    return {
        attribute
        for attribute in dir(target)
        if not attribute.startswith("_") and callable(getattr(target, attribute, None))
    }


@pytest.mark.parametrize("port_name", sorted(PORT_CLASSES))
def test_the_interface_declares_only_its_documented_reads(port_name):
    port = PORT_CLASSES[port_name]
    abstract = set(getattr(port, "__abstractmethods__", frozenset()))
    assert abstract == READ_ONLY_PORTS[port_name], (
        f"{port_name} abstract methods drifted from the documented read set"
    )
    extra = _public_callables(port) - READ_ONLY_PORTS[port_name]
    assert not extra, f"{port_name} exposes {sorted(extra)} beyond its reads"


@pytest.mark.parametrize("port_name", sorted(PORT_CLASSES))
def test_no_interface_method_name_suggests_a_write(port_name):
    for method in READ_ONLY_PORTS[port_name]:
        assert not any(fragment in method.lower() for fragment in MUTATING_NAME_FRAGMENTS), method


@pytest.mark.parametrize("port", ["activity", "gap_report"])
def test_every_registered_adapter_is_read_only(port):
    adapters = registered_classes(port)
    assert adapters, f"no adapters registered for {port}"
    port_class_name = {"activity": "ActivityProvider", "gap_report": "GapReportProvider"}[port]
    allowed = READ_ONLY_PORTS[port_class_name] | {"conformance_scenarios", "timeout_seconds"}

    for name, adapter_class in adapters.items():
        public = _public_callables(adapter_class)
        extra = public - allowed
        assert not extra, f"{port}/{name} exposes {sorted(extra)}"
        for attribute in public | {
            item for item in dir(adapter_class) if not item.startswith("_")
        }:
            assert not any(
                fragment in attribute.lower() for fragment in MUTATING_NAME_FRAGMENTS
            ), f"{port}/{name}.{attribute} reads as a write"


def test_no_upstream_adapter_module_defines_a_write_method():
    """Source-level sweep: a private write helper would not show up in ``dir``."""
    packages = [UC08_ROOT / "adapters" / "mock", UC08_ROOT / "adapters" / "foreign", UC08_ROOT / "adapters" / "real"]
    # The mock ledger and the foreign transport are *fixtures* standing in for an
    # upstream system, not adapters. They are excluded by name and asserted to
    # implement neither port.
    fixture_modules = {"ledger.py", "transport.py", "scenarios.py"}

    checked = 0
    for package in packages:
        for path in sorted(package.rglob("*.py")):
            if path.name in fixture_modules or path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {getattr(base, "id", getattr(base, "attr", "")) for base in node.bases}
                if not bases & {"ActivityProvider", "GapReportProvider"}:
                    continue
                checked += 1
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        assert not any(
                            fragment in member.name.lower() for fragment in MUTATING_NAME_FRAGMENTS
                        ), f"{path}:{member.lineno} defines {member.name}"
    assert checked >= 3, f"expected the mock, foreign and template adapters to be scanned, saw {checked}"


def test_the_upstream_fixtures_do_not_implement_a_port():
    from uc08.adapters.foreign import transport
    from uc08.adapters.mock import ledger

    for module in (ledger, transport):
        for _name, member in inspect.getmembers(module, inspect.isclass):
            assert not issubclass(member, (ActivityProvider, GapReportProvider))


def test_repository_ports_are_the_only_write_surface():
    """Writes exist -- for the records UC-08 owns -- and only there."""
    from uc08.ports import repositories, sinks

    write_methods = {
        "StreakRepository": {"save"},
        "BadgeRepository": {"award"},
        "WeeklySummaryRepository": {"save"},
        "FreezeOfferRepository": {"save"},
        "ProcessedInteractionStore": {"mark_processed"},
    }
    for class_name, expected in write_methods.items():
        port = getattr(repositories, class_name)
        found = {
            name
            for name in getattr(port, "__abstractmethods__", frozenset())
            if any(fragment in name.lower() for fragment in MUTATING_NAME_FRAGMENTS)
        }
        assert found == expected, (class_name, found)

    # And no repository port offers a delete of any kind.
    for class_name in write_methods:
        port = getattr(repositories, class_name)
        for name in getattr(port, "__abstractmethods__", frozenset()):
            assert not any(word in name.lower() for word in ("delete", "remove", "revoke", "purge"))

    assert set(sinks.NotificationSink.__abstractmethods__) == {"badge_awarded", "weekly_summary"}
    assert set(sinks.EngineeringAlertSink.__abstractmethods__) == {"streak_write_failed"}
