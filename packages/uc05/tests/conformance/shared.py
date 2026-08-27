"""Assertions shared by every port's conformance suite."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

import pytest

from uc05.domain.errors import (
    ProviderError,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)

from .harness import PortHarness

#: Names of implementation classes that must never appear in an error a caller
#: can see.  Operators attribute failure by *port*, never by vendor.
PROVIDER_CLASS_MARKERS = (
    "Fake",
    "Mock",
    "Acme",
    "Company",
    "Configured",
    "httpx",
    "Traceback",
)


def skip_unless(factory: Callable[[], Any] | None, state: str) -> Any:
    if factory is None:
        pytest.skip(f"this implementation cannot be driven into the {state!r} state")
    return factory()


def assert_no_leak(value: Any, harness: PortHarness) -> None:
    """Nothing upstream-shaped may appear in what crosses the boundary."""
    rendered = repr(value)
    for marker in harness.leak_markers:
        assert marker not in rendered, (
            f"{harness.name}: upstream marker {marker!r} escaped the adapter"
        )


def assert_error_is_contract_shaped(exc: ProviderError, harness: PortHarness) -> None:
    """A typed error, attributed to the port, carrying no upstream text."""
    assert isinstance(exc, ProviderError), type(exc)
    assert exc.port == harness.port, f"error attributed to {exc.port!r}"
    rendered = f"{exc} {getattr(exc, 'detail', '')}"
    for marker in (*harness.leak_markers, *PROVIDER_CLASS_MARKERS):
        assert marker not in rendered, (
            f"{harness.name}: {marker!r} leaked in the error surface"
        )


async def assert_raises_category(
    call: Callable[[], Awaitable[Any]],
    expected: type[ProviderError],
    harness: PortHarness,
) -> ProviderError:
    with pytest.raises(expected) as caught:
        await call()
    assert_error_is_contract_shaped(caught.value, harness)
    return caught.value


async def assert_honours_timeout(call: Callable[[], Awaitable[Any]]) -> None:
    """A hanging adapter must be cancellable inside the caller's budget.

    The service wraps every provider call in ``asyncio.wait_for``; this asserts
    the adapter does not defeat that by blocking the event loop.
    """
    started = time.perf_counter()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(call(), timeout=0.05)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 3000, f"blocked for {elapsed_ms:.0f}ms on a 50ms budget"


def assert_only_contract_errors_raised(exc: BaseException, harness: PortHarness) -> None:
    assert isinstance(
        exc, (ProviderUnavailable, ProviderTimeout, ProviderInvalidResponse)
    ), (
        f"{harness.name} raised {type(exc).__name__}; an adapter may raise only "
        f"ProviderUnavailable, ProviderTimeout or ProviderInvalidResponse"
    )
