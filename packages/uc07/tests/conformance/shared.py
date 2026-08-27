"""Checks shared by every port's conformance suite."""

from __future__ import annotations

import inspect
from datetime import timezone
from typing import Any

from pydantic import BaseModel

MUTATING_PREFIXES = (
    "create",
    "update",
    "delete",
    "patch",
    "save",
    "write",
    "put",
    "post",
    "insert",
    "upsert",
    "store",
    "persist",
    "push",
    "send",
    "mutate",
    "set_",
    "add_",
    "remove_",
    "submit",
)

#: Words that would betray which provider produced the data.
PROVIDER_NAME_HINTS = ("mock", "foreign", "nexus", "acme", "adapter", "provider")


def assert_read_only(adapter: Any) -> None:
    """The adapter must expose no write operation of its own."""
    own: set[str] = set()
    for klass in type(adapter).__mro__:
        if klass.__module__.startswith("uc07"):
            own.update(klass.__dict__)
    offenders = [
        name
        for name in sorted(own)
        if not name.startswith("__") and name.lower().startswith(MUTATING_PREFIXES)
    ]
    assert offenders == [], offenders


def assert_no_upstream_leakage(value: Any, tokens: tuple[str, ...]) -> None:
    """Domain data must not carry upstream field names or vocabularies."""
    rendered = (
        value.model_dump_json() if isinstance(value, BaseModel) else repr(value)
    )
    for token in tokens:
        assert token not in rendered, f"upstream token '{token}' leaked: {rendered[:200]}"


def assert_error_is_opaque(exc: Exception, tokens: tuple[str, ...]) -> None:
    """Typed errors expose a port label only - no upstream text, no provider name."""
    rendered = f"{exc} {exc!r}"
    for token in tokens:
        assert token not in rendered, f"upstream token '{token}' leaked into an error"
    lowered = rendered.lower()
    for hint in PROVIDER_NAME_HINTS:
        if hint in ("adapter", "provider"):
            # 'Provider'/'ProviderUnavailable' is the *port* vocabulary, allowed.
            continue
        assert hint not in lowered, f"provider name hint '{hint}' leaked into an error"


def assert_utc(moment: Any) -> None:
    assert moment.tzinfo is not None
    assert moment.utcoffset() == timezone.utc.utcoffset(None)


def public_methods(adapter: Any) -> list[str]:
    return sorted(
        name
        for name, member in inspect.getmembers(adapter, callable)
        if not name.startswith("_")
    )
