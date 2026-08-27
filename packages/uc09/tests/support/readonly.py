"""Shared helper for the read-only architecture assertions."""

from __future__ import annotations

import inspect
from typing import Any

#: Verbs that indicate a method changes state somewhere. Matched on word
#: boundaries within the method name.
MUTATING_VERBS: frozenset[str] = frozenset(
    {
        "add",
        "append",
        "archive",
        "clear",
        "close",
        "create",
        "delete",
        "destroy",
        "drop",
        "edit",
        "erase",
        "insert",
        "mark",
        "merge",
        "modify",
        "mutate",
        "patch",
        "persist",
        "post",
        "publish",
        "purge",
        "push",
        "put",
        "record",
        "remove",
        "replace",
        "reset",
        "save",
        "send",
        "set",
        "store",
        "submit",
        "sync",
        "truncate",
        "update",
        "upsert",
        "write",
    }
)


#: Prepositions that turn the word after them into a noun. ``_to_record`` is a
#: conversion *into a record*, not an act of recording; ``_from_payload`` reads.
#: Without this, the template's own ``_to_record`` would fail the check, and an
#: engineer who copied the template exactly would be told their adapter writes.
_CONVERSION_PREFIXES = frozenset({"to", "as", "from", "into"})


def _words(name: str) -> set[str]:
    parts = [part for part in name.strip("_").lower().split("_") if part]
    if parts and parts[0] in _CONVERSION_PREFIXES:
        # Drop the preposition and the noun it governs; keep everything after,
        # so `_to_record_and_save` is still caught on `save`.
        parts = parts[2:]
    return set(parts)


def looks_like_mutation(name: str) -> bool:
    """Whether a method name reads as a write.

    The single definition of the rule. Both :func:`mutating_methods` and the
    architecture test use it, so the two cannot drift apart.
    """
    return bool(_words(name) & MUTATING_VERBS)


def mutating_methods(cls: type, allowed: tuple[str, ...] = ()) -> list[str]:
    """Return the names of methods on ``cls`` that look like they mutate state.

    Args:
        cls: the port protocol or adapter class to inspect.
        allowed: method names exempted, typically the port read methods plus
            the construction hooks.

    Returns:
        Offending method names, sorted. Empty means the class is read-only by
        shape.
    """
    exempt = set(allowed) | {
        "from_settings",
        "conformance_profile",
        "__init__",
        "__init_subclass__",
        "__subclasshook__",
        "__class_getitem__",
    }
    offenders: list[str] = []
    for name, member in inspect.getmembers(cls):
        if name in exempt:
            continue
        if name.startswith("__") and name.endswith("__"):
            continue
        if not (inspect.isfunction(member) or inspect.ismethod(member)):
            continue
        if looks_like_mutation(name):
            offenders.append(name)
    return sorted(offenders)


def public_methods(cls: type) -> list[str]:
    """Public, non-dunder callables declared on ``cls`` or its bases."""
    names: list[str] = []
    for name, member in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if inspect.isfunction(member) or inspect.ismethod(member):
            names.append(name)
    return sorted(names)


def instance_attributes_of_interest(instance: Any) -> list[str]:
    """Public callables on an instance, for adapters that attach methods late."""
    return sorted(
        name
        for name in dir(instance)
        if not name.startswith("_") and callable(getattr(instance, name, None))
    )
