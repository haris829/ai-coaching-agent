"""Adapter replacement: UC-01 business logic depends on contracts, not on mocks.

The proof has three parts:

1. The mock adapters and a completely independent set of adapters (``tests/stubs.py``,
   different data, different internals) both drive the same service with no change to it.
2. A third set of adapters written here — deliberately shaped around a *foreign* upstream
   payload format — also works, with all the mapping confined to the adapter.
3. Static checks that the application and domain layers do not import any adapter.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from uc01.adapters.mock import (
    MockCaseFileAdapter,
    MockCoursesAdapter,
    MockNaricAdapter,
    MockProfileAdapter,
)
from uc01.application import session_service
from uc01.application.dto import OpenSessionCommand
from uc01.application.session_service import SessionInitiationService
from uc01.contracts.exceptions import (
    DependencyUnavailableError,
    InvalidUpstreamResponseError,
    ResourceNotAccessibleError,
)
from uc01.contracts.services import (
    CaseFileService,
    CoursesService,
    GreetingGenerator,
    NaricService,
    ProfileService,
    UserContextProvider,
)
from uc01.domain.enums import NaricAssessmentState, NaricLevelSource, SessionMode, SessionStatus
from uc01.domain.greeting import LocalTemplateGreetingGenerator
from uc01.domain.models import (
    CaseFile,
    Course,
    Lesson,
    NaricAssessment,
    UserContext,
    UserProfile,
)
from uc01.persistence.memory_repository import InMemorySessionRepository

from .stubs import (
    StubCaseFileService,
    StubCoursesService,
    StubNaricService,
    StubProfileService,
)

USER = UserContext(user_id="u_alice")


# --------------------------------------------------------------------------- #
# 1. Contract conformance
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("adapter", "contract"),
    [
        (MockNaricAdapter(), NaricService),
        (MockCoursesAdapter(), CoursesService),
        (MockCaseFileAdapter(), CaseFileService),
        (MockProfileAdapter(), ProfileService),
        (StubNaricService(), NaricService),
        (StubCoursesService(), CoursesService),
        (StubCaseFileService(), CaseFileService),
        (StubProfileService(), ProfileService),
        (LocalTemplateGreetingGenerator(), GreetingGenerator),
    ],
)
def test_adapters_satisfy_their_contract(adapter, contract):
    assert isinstance(adapter, contract)


def test_dev_identity_provider_satisfies_its_contract():
    from uc01.adapters.dev_identity import DevHeaderUserContextProvider

    assert isinstance(DevHeaderUserContextProvider(), UserContextProvider)


# --------------------------------------------------------------------------- #
# 2. A foreign-shaped adapter set, swapped in with no service change
# --------------------------------------------------------------------------- #

FOREIGN_PAYLOAD: Mapping[str, Any] = {
    # Nothing here looks like a UC-01 domain object: different names, nesting, types.
    "data": {
        "attributes": {
            "learner_ref": "u_alice",
            "full_name": "Foreign Learner",
            "cefr_band": 3,  # a different scale entirely
        },
        "programmes": [
            {
                "ref": "prog-9",
                "name": "Foreign Programme",
                "units": [{"ref": "unit-1", "name": "Foreign Unit", "position": "1"}],
                "granted_to": ["u_alice"],
            }
        ],
        "dossiers": [{"ref": "dos-4", "label": "Foreign Dossier", "granted_to": ["u_alice"]}],
    }
}

# The foreign service uses a 1..4 band; the adapter maps it onto UC-01's 1..10 scale.
_BAND_TO_LEVEL = {1: 2, 2: 4, 3: 6, 4: 9}


class ForeignNaricAdapter:
    """All of the mapping work a real adapter does, in one place."""

    def get_assessment(self, user: UserContext) -> NaricAssessment:
        attributes = FOREIGN_PAYLOAD["data"]["attributes"]
        band = attributes.get("cefr_band")
        if not isinstance(band, int) or band not in _BAND_TO_LEVEL:
            raise InvalidUpstreamResponseError("naric", technical_detail="unmappable band")
        return NaricAssessment(
            state=NaricAssessmentState.COMPLETE, level=_BAND_TO_LEVEL[band]
        )


class ForeignCoursesAdapter:
    def list_accessible_courses(self, user: UserContext) -> Sequence[Course]:
        return tuple(
            Course(
                course_id=programme["ref"],
                title=programme["name"],
                lessons=tuple(
                    Lesson(
                        lesson_id=unit["ref"],
                        course_id=programme["ref"],
                        title=unit["name"],
                        ordinal=int(unit["position"]),
                    )
                    for unit in programme["units"]
                ),
            )
            for programme in FOREIGN_PAYLOAD["data"]["programmes"]
            if user.user_id in programme["granted_to"]
        )

    def get_accessible_course(self, user: UserContext, course_id: str) -> Course:
        for course in self.list_accessible_courses(user):
            if course.course_id == course_id:
                return course
        raise ResourceNotAccessibleError("courses", resource_id=course_id)


class ForeignCaseAdapter:
    def list_accessible_case_files(self, user: UserContext) -> Sequence[CaseFile]:
        return tuple(
            CaseFile(case_id=dossier["ref"], title=dossier["label"])
            for dossier in FOREIGN_PAYLOAD["data"]["dossiers"]
            if user.user_id in dossier["granted_to"]
        )

    def get_accessible_case_file(self, user: UserContext, case_id: str) -> CaseFile:
        for case_file in self.list_accessible_case_files(user):
            if case_file.case_id == case_id:
                return case_file
        raise ResourceNotAccessibleError("cases", resource_id=case_id)


class ForeignProfileAdapter:
    def get_profile(self, user: UserContext) -> UserProfile:
        attributes = FOREIGN_PAYLOAD["data"]["attributes"]
        return UserProfile(user_id=user.user_id, display_name=attributes.get("full_name"))


def _service(**adapters) -> SessionInitiationService:
    """Build the service. Note: identical construction for every adapter family."""
    return SessionInitiationService(
        greeting_generator=LocalTemplateGreetingGenerator(),
        repository=InMemorySessionRepository(),
        **adapters,
    )


ADAPTER_FAMILIES = {
    "mock": {
        "naric_service": MockNaricAdapter(),
        "courses_service": MockCoursesAdapter(),
        "case_service": MockCaseFileAdapter(),
        "profile_service": MockProfileAdapter(),
    },
    "stub": {
        "naric_service": StubNaricService(),
        "courses_service": StubCoursesService(),
        "case_service": StubCaseFileService(),
        "profile_service": StubProfileService(),
    },
    "foreign": {
        "naric_service": ForeignNaricAdapter(),
        "courses_service": ForeignCoursesAdapter(),
        "case_service": ForeignCaseAdapter(),
        "profile_service": ForeignProfileAdapter(),
    },
}

EXPECTED = {
    "mock": ("crs_contract_law", "lsn_offer", "case_alpha", 8),
    "stub": ("stub_course_1", "stub_lesson_1", "stub_case_1", 7),
    "foreign": ("prog-9", "unit-1", "dos-4", 6),
}


@pytest.mark.parametrize("family", sorted(ADAPTER_FAMILIES))
def test_the_same_service_code_runs_against_every_adapter_family(family):
    course_id, lesson_id, case_id, expected_level = EXPECTED[family]
    service = _service(**ADAPTER_FAMILIES[family])

    free_form = service.open_session(USER, OpenSessionCommand(mode=SessionMode.FREE_FORM))
    assert free_form.record.status is SessionStatus.ACTIVE
    assert free_form.record.naric_level == expected_level
    assert free_form.record.naric_level_source is NaricLevelSource.NARIC

    course_linked = service.open_session(
        USER,
        OpenSessionCommand(
            mode=SessionMode.COURSE_LINKED, course_id=course_id, lesson_id=lesson_id
        ),
    )
    assert course_linked.record.linked_resource.resource_id == course_id
    assert course_linked.record.linked_resource.secondary_id == lesson_id

    case_linked = service.open_session(
        USER, OpenSessionCommand(mode=SessionMode.CASE_LINKED, case_id=case_id)
    )
    assert case_linked.record.linked_resource.resource_id == case_id

    # Authorization is enforced identically whichever adapter answers.
    from uc01.domain.errors import SelectionNotAccessibleError

    with pytest.raises(SelectionNotAccessibleError):
        service.open_session(
            USER,
            OpenSessionCommand(
                mode=SessionMode.COURSE_LINKED, course_id="not-mine", lesson_id=lesson_id
            ),
        )


def test_swapping_an_adapter_requires_no_change_to_the_service_module():
    """The service module has no import of, or reference to, any adapter package."""
    source = inspect.getsource(session_service)
    assert "adapters" not in source
    assert "Mock" not in source
    for module in session_service.__dict__.values():
        module_name = getattr(module, "__module__", "")
        assert "uc01.adapters" not in str(module_name)


def test_a_failing_foreign_adapter_degrades_exactly_like_a_failing_mock():
    class BrokenForeignNaric:
        def get_assessment(self, user: UserContext) -> NaricAssessment:
            raise DependencyUnavailableError("naric", technical_detail="foreign outage")

    service = _service(
        naric_service=BrokenForeignNaric(),
        courses_service=ForeignCoursesAdapter(),
        case_service=ForeignCaseAdapter(),
        profile_service=ForeignProfileAdapter(),
    )
    result = service.open_session(USER, OpenSessionCommand(mode=SessionMode.FREE_FORM))
    assert result.record.status is SessionStatus.DEGRADED
    assert result.record.naric_level == 5
    assert result.record.naric_level_source is NaricLevelSource.DEFAULT


def test_container_reports_a_clear_error_for_an_unimplemented_real_adapter():
    from uc01.api.container import AppContainer
    from uc01.config import Settings

    settings = Settings(persistence="memory", naric_adapter="real", serve_frontend=False)
    container = AppContainer(settings)
    with pytest.raises(NotImplementedError) as excinfo:
        container.service()
    message = str(excinfo.value)
    assert "uc01/adapters/real/naric.py" in message
    assert "NaricService" in message
    assert "docs/ADAPTER_REPLACEMENT.md" in message
