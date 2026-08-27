"""Reusable contract-conformance kit, parameterised on the adapter under test.

This is the piece that makes the integration guarantee real. It asserts the
**behavioural contract** of a port, never the data of any particular
implementation, so the same suite validates the mock, the deliberately foreign
adapter, and a real company adapter that does not exist yet.

How a new adapter is enrolled
-----------------------------

By adding its one line to :data:`uc09_summary.registry.REGISTRY`. Nothing else.
:func:`conformance_targets` reads the registry, so the suite discovers the new
implementation automatically and there is **no new test to write**.

What the adapter must supply is a ``conformance_profile()`` classmethod naming
the identifiers that reproduce each scenario in its environment. The template
in ``adapters/real/_template.py`` has it stubbed. An adapter without one fails
the suite with an explanatory message - it is never skipped, because a skipped
contract test looks identical to a passing one in a summary line.

Narrowing a run
---------------

``UC09_CONFORMANCE_ONLY=company pytest tests/conformance`` restricts every port
to the named implementation, which is what an integration engineer wants when
their environment can reach only their own upstream.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pytest

from uc09_summary.config import Settings, load_settings
from uc09_summary.domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from uc09_summary.registry import load_implementation, registered_names

#: Restrict every port to these implementation names, comma-separated.
ONLY_ENV = "UC09_CONFORMANCE_ONLY"


def is_offline_testable(port: str, name: str) -> bool:
    """Whether this adapter can be contract-tested without its live upstream.

    An adapter declares ``"offline": False`` in its conformance profile when it
    genuinely cannot answer without a configured service. That is a statement
    the adapter makes about itself, not a decision this kit makes for it, and
    :mod:`tests.test_registry` asserts that such an adapter also names the
    configuration it needs - so nothing escapes contract testing quietly.
    """
    implementation = load_implementation(port, name)
    getter = getattr(implementation, "conformance_profile", None)
    if getter is None:
        # No profile at all: include it, so the suite fails loudly rather than
        # passing over an adapter nobody has verified.
        return True
    return bool(getter().get("offline", True))


def conformance_targets(port: str) -> list[str]:
    """Return the implementation names to exercise for ``port``.

    Reads the registry, so one registry line enrols a new adapter here.

    By default this is every registered implementation that can be driven
    without a live upstream. An integration engineer whose environment *can*
    reach a real service names it explicitly::

        UC09_CONFORMANCE_ONLY=larrycore pytest tests/conformance

    which runs the full contract suite against that adapter and nothing else.
    """
    names = list(registered_names(port))
    only = os.environ.get(ONLY_ENV, "").strip()
    if only:
        wanted = {n.strip() for n in only.split(",") if n.strip()}
        return [n for n in names if n in wanted]
    return [n for n in names if is_offline_testable(port, n)]


def settings_for_conformance() -> Settings:
    return load_settings(log_json=True)


def build_adapter(port: str, name: str) -> Any:
    """Construct the registered adapter exactly as the composition root would."""
    implementation = load_implementation(port, name)
    factory = getattr(implementation, "from_settings", None)
    assert factory is not None, (
        f"{implementation.__qualname__} is registered for port {port!r} but has no "
        "from_settings(settings) classmethod. Copy adapters/real/_template.py, "
        "which provides one."
    )
    return factory(settings_for_conformance())


def profile_for(adapter: Any, port: str, name: str) -> dict[str, Any]:
    """Return the adapter conformance profile, failing loudly if it has none."""
    getter = getattr(adapter, "conformance_profile", None)
    assert getter is not None, (
        f"Adapter {type(adapter).__name__!r} (port {port!r}, registered as {name!r}) "
        "has no conformance_profile() classmethod, so the contract suite cannot "
        "drive it. Add one - see the stub in "
        "uc09_summary/adapters/real/_template.py. This is a failure rather than "
        "a skip on purpose: an unverified adapter must not look like a passing one."
    )
    profile = getter()
    assert isinstance(profile, dict), "conformance_profile() must return a dict"
    return profile


def require(profile: dict[str, Any], key: str, port: str) -> Any:
    """Fetch a required profile key, failing with a message that says what to add."""
    assert key in profile and profile[key] is not None, (
        f"conformance_profile() for port {port!r} is missing {key!r}. The "
        "contract suite needs it to reproduce that scenario against your "
        "upstream. See uc09_summary/adapters/real/_template.py."
    )
    return profile[key]


# --------------------------------------------------------------------------
# Shared assertions
# --------------------------------------------------------------------------


def assert_no_upstream_leak(value: Any, tokens: tuple[str, ...], *, what: str) -> None:
    """Assert no upstream token appears anywhere in ``value``.

    The boundary rule: no upstream field name, nesting, error string, hostname
    or provider name may cross an adapter. This checks the text of whatever the
    adapter handed back or raised, so a mapping that quietly passes a payload
    through is caught here rather than in production.
    """
    haystack = _stringify(value).casefold()
    leaked = [token for token in tokens if token and token.casefold() in haystack]
    assert not leaked, (
        f"{what} leaked upstream detail past the adapter boundary: {leaked}. "
        "Nothing upstream-specific may escape the adapter - not a field name, "
        "not an error string, not a provider name."
    )


def assert_error_is_opaque(exc: Exception, tokens: tuple[str, ...]) -> None:
    """A raised contract error must carry no upstream identity, detail included."""
    assert_no_upstream_leak(str(exc), tokens, what="the exception message")
    assert_no_upstream_leak(repr(exc), tokens, what="the exception repr")
    detail = getattr(exc, "detail", "")
    assert_no_upstream_leak(
        detail,
        tokens,
        what="the exception detail (which is written to application logs)",
    )


def assert_utc(value: datetime, what: str) -> None:
    """Timestamps cross the boundary timezone-aware. A naive one is ambiguous."""
    assert isinstance(value, datetime), f"{what} must be a datetime"
    assert value.tzinfo is not None and value.utcoffset() is not None, (
        f"{what} must be timezone-aware; convert it to UTC inside the adapter."
    )


def assert_platform_naric(record: Any) -> None:
    """The platform enum arrives whatever the upstream sent."""
    assert isinstance(record.naric_level, NaricLevel), (
        "naric_level must be normalised to the platform NaricLevel enum by the "
        "adapter, whatever representation the upstream used. Never an integer "
        "scale and never an upstream code."
    )
    assert isinstance(record.naric_level_source, NaricLevelSource)
    assert isinstance(record.naric_level_status, SourceStatus)
    assert record.naric_level.value == record.naric_level.value.lower()


def assert_read_only_surface(adapter: Any, allowed: tuple[str, ...]) -> None:
    """No mutating method may exist on an upstream adapter."""
    from tests.support.readonly import mutating_methods

    offenders = mutating_methods(type(adapter), allowed)
    assert not offenders, (
        f"{type(adapter).__name__} exposes mutating method(s) {offenders}. "
        "Upstream ports are read-only by shape: this component reads a session, "
        "it never changes one."
    )


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify(v) for v in value)
    dump = getattr(value, "model_dump", None)
    if dump is not None:
        return str(dump(mode="json"))
    return str(value)


def parametrized_over(port: str):
    """Decorator applying the registry-driven parameterisation for ``port``.

    Raises at collection time if nothing is selected, rather than producing an
    empty or skipped test. A contract suite that quietly exercises no adapter
    is indistinguishable from one that passed.
    """
    targets = conformance_targets(port)
    if not targets:
        raise RuntimeError(
            f"No conformance target for port {port!r}. "
            f"{ONLY_ENV}={os.environ.get(ONLY_ENV, '')!r} selected nothing from "
            f"the registered names {registered_names(port)}."
        )
    return pytest.mark.parametrize("adapter_name", targets)
