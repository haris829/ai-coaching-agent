"""TEMPORARY DEVELOPMENT ADAPTERS — NOT PRODUCTION INTEGRATIONS.

Every adapter in this package returns fixture data from ``fixtures.py``. Nothing here
contacts a real service. They exist so UC-01 can be built, run and tested before the
real NARIC, Courses Agent and Case Prep integrations are available.

Each mock deliberately builds a *plausible external payload* first and then normalises
it into UC-01 domain types, so the normalisation step lives where the real adapter will
need it.

Replacement instructions: ``docs/ADAPTER_REPLACEMENT.md``.
"""

from .cases import MockCaseFileAdapter
from .courses import MockCoursesAdapter
from .naric import MockNaricAdapter
from .profile import MockProfileAdapter
from .scenarios import (
    CaseScenario,
    CoursesScenario,
    NaricScenario,
    ProfileScenario,
    ScenarioSet,
)

IS_MOCK = True
"""Flag surfaced in the API's /healthz payload so a mock deployment is never mistaken
for a real integration."""

__all__ = [
    "CaseScenario",
    "CoursesScenario",
    "IS_MOCK",
    "MockCaseFileAdapter",
    "MockCoursesAdapter",
    "MockNaricAdapter",
    "MockProfileAdapter",
    "NaricScenario",
    "ProfileScenario",
    "ScenarioSet",
]
