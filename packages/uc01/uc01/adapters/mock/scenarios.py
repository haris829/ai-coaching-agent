"""Mock scenario selection.

Every required mock state from the UC-01 brief is an explicit enum member here, so tests
and the developer scenario panel drive the same switch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class NaricScenario(str, Enum):
    SUCCESS = "success"
    """Successful assessment with a usable level."""

    INCOMPLETE = "incomplete"
    """Reachable, assessment started but no level yet."""

    CALIBRATING = "calibrating"
    """Reachable, level still being calibrated."""

    UNAVAILABLE = "unavailable"
    """Service error / timeout."""

    INVALID = "invalid"
    """Service answered with a payload UC-01 cannot normalise."""

    PER_USER = "per_user"
    """Use the per-user fixture state (default)."""


class CoursesScenario(str, Enum):
    AVAILABLE = "available"
    """Use the per-user fixture catalogue (default; may be empty for some users)."""

    EMPTY = "empty"
    """Reachable, but no accessible courses for anyone."""

    UNAVAILABLE = "unavailable"
    """Courses Agent unreachable."""

    INVALID = "invalid"
    """Courses Agent answered with an unusable payload."""


class CaseScenario(str, Enum):
    AVAILABLE = "available"
    """Use the per-user fixture case files (default; may be empty for some users)."""

    EMPTY = "empty"
    """Reachable, but no accessible case files for anyone."""

    UNAVAILABLE = "unavailable"
    """Case Prep service unreachable."""

    INVALID = "invalid"
    """Case Prep answered with an unusable payload."""


class ProfileScenario(str, Enum):
    AVAILABLE = "available"
    """Use the per-user fixture profile (default; may be incomplete for some users)."""

    INCOMPLETE = "incomplete"
    """Reachable, but the profile has no display name."""

    UNAVAILABLE = "unavailable"
    """Profile service unreachable."""


@dataclass(frozen=True)
class ScenarioSet:
    """The four mock scenarios in force for one request."""

    naric: NaricScenario = NaricScenario.PER_USER
    courses: CoursesScenario = CoursesScenario.AVAILABLE
    cases: CaseScenario = CaseScenario.AVAILABLE
    profile: ProfileScenario = ProfileScenario.AVAILABLE

    def describe(self) -> Mapping[str, str]:
        return {
            "naric": self.naric.value,
            "courses": self.courses.value,
            "cases": self.cases.value,
            "profile": self.profile.value,
        }

    def merged_with(self, overrides: Mapping[str, str]) -> ScenarioSet:
        """Apply string overrides (e.g. from a dev-only header). Unknown keys and
        unknown values are ignored rather than raising: a bad dev header must never
        become a 500."""
        return ScenarioSet(
            naric=_coerce(NaricScenario, overrides.get("naric"), self.naric),
            courses=_coerce(CoursesScenario, overrides.get("courses"), self.courses),
            cases=_coerce(CaseScenario, overrides.get("cases"), self.cases),
            profile=_coerce(ProfileScenario, overrides.get("profile"), self.profile),
        )


def _coerce(enum_cls, raw: str | None, fallback):
    if raw is None:
        return fallback
    for member in enum_cls:
        if member.value == raw:
            return member
    return fallback


def parse_scenario_header(raw: str | None) -> Mapping[str, str]:
    """Parse ``courses=unavailable,naric=incomplete`` into a mapping.

    Tolerant by design; the caller decides whether it is even allowed to apply it.
    """
    if not raw:
        return {}
    parsed: dict[str, str] = {}
    for chunk in raw.split(","):
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip().lower()
        value = value.strip().lower()
        if key and value:
            parsed[key] = value
    return parsed


__all__ = [
    "CaseScenario",
    "CoursesScenario",
    "NaricScenario",
    "ProfileScenario",
    "ScenarioSet",
    "parse_scenario_header",
]
