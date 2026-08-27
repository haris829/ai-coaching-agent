"""Normalisation of upstream values onto the platform contract.

Adapters own the knowledge of *where* a value sits in an upstream payload.
This module owns the knowledge of *what the platform accepts*. An adapter pulls
the raw value out of its own payload shape and hands it here; the platform enum
comes back. That keeps the mapping identical for every provider family, and it
keeps the invalid-value rule in exactly one place.

Contract rules implemented here:

* A value that maps to no ``NaricLevel`` member is an **invalid response**, not
  a level: ``LEVEL_5`` default, source ``default``, status ``invalid``, logged.
* An absent value is ``empty``, not ``invalid`` -- the two are never conflated.
* Completion percentage is an integer 0-100. An ambiguous representation is
  reported ``invalid`` rather than guessed (A-08).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from uc08.domain.enums import (
    DEFAULT_NARIC_LEVEL,
    EXPLANATION_PROFILE_BY_LEVEL,
    ExplanationProfile,
    NaricLevel,
    NaricLevelSource,
    SourceStatus,
)
from uc08.logging_setup import get_logger

_log = get_logger(__name__)

#: Canonical spellings accepted for each level. Everything is compared after
#: lowercasing and stripping every character that is not a letter, a digit or a
#: ``+``, so "Level 7 (plus)" and "LEVEL_7_PLUS" both reduce to "level7plus".
_LEVEL_ALIASES: dict[str, NaricLevel] = {}


def _register(level: NaricLevel, *aliases: str) -> None:
    for alias in aliases:
        _LEVEL_ALIASES[_squash(alias)] = level


def _squash(raw: str) -> str:
    return re.sub(r"[^a-z0-9+]", "", raw.strip().lower())


_register(NaricLevel.LEVEL_3, "level_3", "level3", "3", "three", "level three", "naric3", "l3")
_register(NaricLevel.LEVEL_4, "level_4", "level4", "4", "four", "level four", "naric4", "l4")
_register(NaricLevel.LEVEL_5, "level_5", "level5", "5", "five", "level five", "naric5", "l5")
_register(NaricLevel.LEVEL_6, "level_6", "level6", "6", "six", "level six", "naric6", "l6")
_register(NaricLevel.LEVEL_7, "level_7", "level7", "7", "seven", "level seven", "naric7", "l7")
_register(
    NaricLevel.LEVEL_7_PLUS,
    "level_7_plus",
    "level7plus",
    "level7+",
    "7+",
    "7plus",
    "sevenplus",
    "level seven plus",
    "naric7+",
    "naric 7 plus",
    "l7+",
)


@dataclass(frozen=True)
class NaricLevelReading:
    """A NARIC level that is always a platform enum member."""

    level: NaricLevel
    source: NaricLevelSource
    status: SourceStatus

    @property
    def explanation_profile(self) -> ExplanationProfile:
        return explanation_profile_for(self.level)


def explanation_profile_for(level: NaricLevel) -> ExplanationProfile:
    """LEVEL_3/4 -> basic, LEVEL_5/6 -> intermediate, LEVEL_7/7_PLUS -> advanced.

    Levels 4 and 6 are an assumption (A-06).
    """
    return EXPLANATION_PROFILE_BY_LEVEL[level]


def normalise_naric_level(raw: object, *, port: str = "unknown") -> NaricLevelReading:
    """Map any upstream representation onto the platform enum.

    ``port`` names the abstract port for the log line only; no vendor name and
    no upstream payload text is propagated to callers.
    """
    if isinstance(raw, NaricLevel):
        return NaricLevelReading(raw, NaricLevelSource.RETRIEVED, SourceStatus.AVAILABLE)

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        # The source answered, and the answer carried no level.
        return NaricLevelReading(DEFAULT_NARIC_LEVEL, NaricLevelSource.DEFAULT, SourceStatus.EMPTY)

    if isinstance(raw, bool):
        # A bool is never a level; guard before the int branch (bool is an int).
        candidate = repr(raw)
    elif isinstance(raw, (int, float)):
        candidate = str(int(raw)) if float(raw).is_integer() else str(raw)
    elif isinstance(raw, str):
        candidate = raw
    else:
        candidate = repr(raw)

    level = _LEVEL_ALIASES.get(_squash(candidate))
    if level is None:
        _log.warning(
            "naric_level_invalid",
            extra={
                "port": port,
                "naric_level_status": SourceStatus.INVALID.value,
                "applied_naric_level": DEFAULT_NARIC_LEVEL.value,
                "naric_level_source": NaricLevelSource.DEFAULT.value,
                "rejected_value_kind": type(raw).__name__,
            },
        )
        return NaricLevelReading(DEFAULT_NARIC_LEVEL, NaricLevelSource.DEFAULT, SourceStatus.INVALID)

    return NaricLevelReading(level, NaricLevelSource.RETRIEVED, SourceStatus.AVAILABLE)


@dataclass(frozen=True)
class CompletionReading:
    """Course completion as an integer 0-100, or nothing at all."""

    percent: int | None
    status: SourceStatus


_PERCENT_PATTERN = re.compile(r"^\s*(\d{1,3})(?:\.\d+)?\s*%?\s*$")


def normalise_completion_percent(raw: object, *, port: str = "unknown") -> CompletionReading:
    """Map an upstream completion value onto an integer 0-100.

    Accepted: ``int``/``float`` in 0-100, and strings such as ``"72"``,
    ``"72%"``, ``"72.4 %"`` (truncated toward zero).

    Deliberately rejected as ``invalid``: a fractional 0-1 representation. A
    value of ``0.5`` could mean 50% or half a percent, and this component never
    guesses (A-08). Values ``0`` and ``1`` are read as percentages, being valid
    integers in range.
    """
    if raw is None:
        return CompletionReading(None, SourceStatus.EMPTY)

    if isinstance(raw, bool):
        return _invalid_completion(raw, port)

    if isinstance(raw, int):
        return CompletionReading(raw, SourceStatus.AVAILABLE) if 0 <= raw <= 100 else _invalid_completion(raw, port)

    if isinstance(raw, float):
        if raw.is_integer() and 0 <= raw <= 100:
            return CompletionReading(int(raw), SourceStatus.AVAILABLE)
        if 0 < raw < 1:
            # Ambiguous fraction. Not a guess, not a level of confidence: invalid.
            return _invalid_completion(raw, port)
        if 0 <= raw <= 100:
            return CompletionReading(int(raw), SourceStatus.AVAILABLE)
        return _invalid_completion(raw, port)

    if isinstance(raw, str):
        if not raw.strip():
            return CompletionReading(None, SourceStatus.EMPTY)
        match = _PERCENT_PATTERN.match(raw)
        if match is None:
            return _invalid_completion(raw, port)
        value = int(match.group(1))
        return CompletionReading(value, SourceStatus.AVAILABLE) if value <= 100 else _invalid_completion(raw, port)

    return _invalid_completion(raw, port)


def _invalid_completion(raw: object, port: str) -> CompletionReading:
    _log.warning(
        "course_progress_invalid",
        extra={
            "port": port,
            "course_progress_status": SourceStatus.INVALID.value,
            "rejected_value_kind": type(raw).__name__,
        },
    )
    return CompletionReading(None, SourceStatus.INVALID)
