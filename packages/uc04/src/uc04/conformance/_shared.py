"""Shared assertions for the conformance kit."""

from __future__ import annotations

import re

from ..domain.errors import ProviderError

#: Shapes that suggest an upstream payload or vendor identity has escaped the boundary.
_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://"),  # an upstream URL
    re.compile(r"\b\d{3}\s+(Bad|Internal|Service|Gateway)\b", re.I),  # raw HTTP status text
    re.compile(r"[{\[].*[:,].*[}\]]", re.S),  # a serialised payload fragment
    re.compile(r"\bTraceback\b"),
    re.compile(r"\bapi[_-]?key\b", re.I),
    re.compile(r"\bauthorization\b", re.I),
)


def assert_no_upstream_leakage(error: ProviderError) -> None:
    """A contract error may say what failed; it may never reproduce the upstream's own words."""
    message = str(error)
    for pattern in _LEAK_PATTERNS:
        assert not pattern.search(message), (
            f"error message leaks upstream detail matching {pattern.pattern!r}: {message!r}"
        )
    assert len(message) < 200, "contract error messages should be short and generic"
    assert error.port, "a ProviderError must name the port it came from"
