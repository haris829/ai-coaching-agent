"""Deterministic mock adapters (development and test only).

These adapters are read-only, hold no network or filesystem dependency, and
produce identical output for identical scenario input.
"""

from dataclasses import dataclass

from uc07.adapters.mock.courses import MockCoursesPayload, MockCoursesProvider
from uc07.adapters.mock.feedback import MockFeedbackPayload, MockFeedbackProvider
from uc07.adapters.mock.interaction_log import (
    MockInteractionLogProvider,
    MockInteractionPayload,
)
from uc07.adapters.mock.profile import MockLearnerProfileProvider, MockProfilePayload
from uc07.adapters.mock.scenarios import (
    DEFAULT_SCENARIO,
    LEARNER,
    SCENARIOS,
    MockScenario,
    get_scenario,
)

__all__ = [
    "DEFAULT_SCENARIO",
    "LEARNER",
    "SCENARIOS",
    "MockCoursesPayload",
    "MockCoursesProvider",
    "MockFeedbackPayload",
    "MockFeedbackProvider",
    "MockInteractionLogProvider",
    "MockInteractionPayload",
    "MockLearnerProfileProvider",
    "MockProfilePayload",
    "MockScenario",
    "MockSourceSet",
    "get_scenario",
    "providers_for",
]


@dataclass(frozen=True, slots=True)
class MockSourceSet:
    """The four read-only providers built from one scenario."""

    interactions: MockInteractionLogProvider
    feedback: MockFeedbackProvider
    profiles: MockLearnerProfileProvider
    courses: MockCoursesProvider


def providers_for(scenario: MockScenario) -> MockSourceSet:
    """Build the four mock providers for a scenario."""
    return MockSourceSet(
        interactions=MockInteractionLogProvider(scenario.interactions),
        feedback=MockFeedbackProvider(scenario.feedback),
        profiles=MockLearnerProfileProvider(scenario.profiles),
        courses=MockCoursesProvider(scenario.courses),
    )
