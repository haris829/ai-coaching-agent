"""Normalisation of an upstream NARIC value onto the platform's closed enum.

This is the single place the platform contract's rule is implemented:

    "A value mapping to no enum member is an *invalid response*, not a level: apply the
     LEVEL_5 default, mark source `default`, record status `invalid`, log it."

Adapters translate their own upstream vocabulary into a platform token first, then call
:func:`normalise_naric_level`.  Nothing here knows any upstream field name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from uc10.domain.enums import (
    DEFAULT_NARIC_LEVEL,
    ExplanationProfile,
    NaricLevel,
    NaricLevelSource,
    SourceStatus,
    explanation_profile_for,
)

_SEPARATORS = re.compile(r"[\s\-./]+")


@dataclass(frozen=True, slots=True)
class NormalisedNaricLevel:
    """Result of normalising one upstream NARIC value."""

    level: NaricLevel
    source: NaricLevelSource
    status: SourceStatus
    raw_kind: str  # a *shape* description for logging, never the raw upstream payload

    @property
    def explanation_profile(self) -> ExplanationProfile:
        return explanation_profile_for(self.level)


def _canonical_token(raw: str) -> str:
    token = _SEPARATORS.sub("_", raw.strip().lower())
    token = token.replace("+", "_plus")
    token = re.sub(r"_+", "_", token).strip("_")
    if token.startswith("level") and not token.startswith("level_"):
        token = "level_" + token[len("level") :].lstrip("_")
    return token


def normalise_naric_level(raw: object) -> NormalisedNaricLevel:
    """Map an upstream value onto the closed platform enum.

    Missing values are ``empty`` (the upstream answered and had nothing).  Unrecognised
    values -- including bare integers, which are never a valid NARIC representation on
    this platform -- are ``invalid``.  Both fall back to the documented default and are
    marked ``default`` at source; neither ever produces a plausible-looking guess.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return NormalisedNaricLevel(
            level=DEFAULT_NARIC_LEVEL,
            source=NaricLevelSource.DEFAULT,
            status=SourceStatus.EMPTY,
            raw_kind="missing",
        )

    if isinstance(raw, NaricLevel):
        return NormalisedNaricLevel(raw, NaricLevelSource.RETRIEVED, SourceStatus.AVAILABLE, "enum")

    if isinstance(raw, bool) or not isinstance(raw, str):
        # Integers and floats are an integer scale. The platform contract forbids one, so
        # this is an invalid response rather than a level to be guessed at.
        return NormalisedNaricLevel(
            level=DEFAULT_NARIC_LEVEL,
            source=NaricLevelSource.DEFAULT,
            status=SourceStatus.INVALID,
            raw_kind=f"non_string:{type(raw).__name__}",
        )

    token = _canonical_token(raw)
    for member in NaricLevel:
        if token in (member.value, member.name.lower()):
            return NormalisedNaricLevel(
                level=member,
                source=NaricLevelSource.RETRIEVED,
                status=SourceStatus.AVAILABLE,
                raw_kind="token",
            )

    return NormalisedNaricLevel(
        level=DEFAULT_NARIC_LEVEL,
        source=NaricLevelSource.DEFAULT,
        status=SourceStatus.INVALID,
        raw_kind="unrecognised_token",
    )
