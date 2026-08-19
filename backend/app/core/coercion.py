"""Input-coercion primitives, shared by every validator.

Both capabilities accept the same messy inputs — JSON numbers, the strings an HTML form produces,
and CSV cells — and both have to decide "is this blank?" and "is this a whole number?" identically.
One implementation means a `"10"` the question validator accepts cannot be a `"10"` the
configuration validator rejects.

Pure functions only: no HTTP, no persistence, no domain vocabulary. Anything that encodes a *rule*
belongs in the owning capability's domain package, not here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

_EnumT = TypeVar("_EnumT", bound=StrEnum)

#: Spellings of "true" accepted from a CSV cell or a form field.
TRUTHY_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "y", "correct", "t"})


def trimmed(value: object) -> str:
    """Whitespace-stripped string form. ``None`` becomes ``""``."""
    if isinstance(value, str):
        return value.strip()
    return "" if value is None else str(value).strip()


def optional_trimmed(value: object) -> str | None:
    """Trimmed string, or ``None`` when absent or empty."""
    if value is None:
        return None
    return trimmed(value) or None


def is_blank(value: object) -> bool:
    """True for ``None`` and for whitespace-only strings."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def to_number(value: object) -> float | None:
    """Parse a number from a JSON number or a string. ``None`` when unparseable.

    ``bool`` is rejected deliberately: ``True`` is not the number 1 in any payload we accept, and
    silently coercing it hides a client bug.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def to_int(value: object) -> int | None:
    """Parse a whole number. ``None`` when unparseable or fractional (``10.5`` is not an int)."""
    number = to_number(value)
    if number is None or number != int(number):
        return None
    return int(number)


def truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_VALUES
    return False


def round4(value: float) -> float:
    """Keep float arithmetic from producing 0.30000000000000004 point values."""
    return round(value + 0.0, 4)


def parse_enum(enum_cls: type[_EnumT], raw: object) -> _EnumT | None:
    """Case-insensitive, whitespace-tolerant enum lookup. ``None`` when invalid.

    Tries the value as given, then upper-case, then lower-case, so it works for vocabularies spelled
    either way — ``SINGLE_CHOICE`` and ``practice`` are both looked up by the same function.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    for candidate in (text, text.upper(), text.lower()):
        try:
            return enum_cls(candidate)
        except ValueError:
            continue
    return None


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]
