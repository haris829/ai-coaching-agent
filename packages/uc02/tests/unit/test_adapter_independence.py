"""Adapter independence.

Swap a mock for a second implementation of the same port and
``ContextAssemblyService`` requires zero changes. This is the property that makes
the real integration a config change plus one class per provider.
"""

from __future__ import annotations

import inspect

from uc02.application.context_assembly_service import ContextAssemblyService
from uc02.domain.errors import ProviderNotImplemented
from uc02.domain.models.enums import SourceName, SourceStatus
from uc02.domain.ports.providers import (
    CoursesProvider,
    LegalFootprintsProvider,
    NaricProvider,
    QuestionHistoryProvider,
)
from uc02.infrastructure.config.settings import Settings
from uc02.infrastructure.providers.company import (
    CompanyCoursesProvider,
    CompanyLegalFootprintsProvider,
    CompanyNaricProvider,
    CompanyQuestionHistoryProvider,
)
from uc02.infrastructure.providers.factory import build_providers
from uc02.infrastructure.repositories.in_memory_context_repository import (
    InMemorySessionContextRepository,
)
from tests.fixtures.alternative_adapters import (
    DuckTypedCoursesProvider,
    FixtureHistoryProvider,
    FixtureLegalProvider,
    FixtureNaricProvider,
)
from tests.fixtures.factories import make_identity, make_settings


def _service(settings: Settings, **providers) -> ContextAssemblyService:
    """Constructed with exactly the same call the production wiring uses."""
    return ContextAssemblyService(
        repository=InMemorySessionContextRepository(),
        settings=settings,
        **providers,
    )


async def test_all_four_ports_can_be_swapped_for_different_implementations():
    settings = make_settings()
    naric = FixtureNaricProvider({"learner-1": 7})
    courses = DuckTypedCoursesProvider(course_name="Swapped Course")
    legal = FixtureLegalProvider(speciality="Family law")
    history = FixtureHistoryProvider(count=3)

    service = _service(settings, naric=naric, courses=courses, legal=legal, history=history)
    context = (await service.initialize(make_identity())).context

    assert context.naric.level == 7
    assert context.explanation_profile.template_id.value == "advanced"
    assert context.courses.enrolments[0].course_name == "Swapped Course"
    assert context.legal_profile.speciality_areas == ("Family law",)
    assert context.question_history.count == 3
    assert all(
        outcome.status is SourceStatus.AVAILABLE for outcome in context.source_status.values()
    )


async def test_a_duck_typed_adapter_works_without_inheriting_the_port():
    """The service depends on the call signature, not on an inheritance chain."""
    assert not isinstance(DuckTypedCoursesProvider(), CoursesProvider)
    settings = make_settings()
    service = _service(
        settings,
        naric=FixtureNaricProvider({"learner-1": 3}),
        courses=DuckTypedCoursesProvider(),
        legal=FixtureLegalProvider(),
        history=FixtureHistoryProvider(),
    )
    context = (await service.initialize(make_identity())).context
    assert context.courses.enrolments[0].course_id == "fixture-course"


async def test_a_replacement_adapters_failures_are_handled_by_the_same_matrix():
    """A new adapter raising the port's declared errors needs no service change."""
    settings = make_settings()
    service = _service(
        settings,
        naric=FixtureNaricProvider({}),  # every user raises ProviderUnavailable
        courses=DuckTypedCoursesProvider(),
        legal=FixtureLegalProvider(),
        history=FixtureHistoryProvider(),
    )
    context = (await service.initialize(make_identity())).context
    assert context.source_status[SourceName.NARIC].status is SourceStatus.UNAVAILABLE
    assert context.naric.level == 5
    assert context.naric.level_source.value == "default"
    assert context.personalization.available is True


async def test_a_replacement_history_adapter_still_receives_the_server_side_limit():
    settings = make_settings(question_history_limit=20)
    history = FixtureHistoryProvider(count=2)
    service = _service(
        settings,
        naric=FixtureNaricProvider({"learner-1": 5}),
        courses=DuckTypedCoursesProvider(),
        legal=FixtureLegalProvider(),
        history=history,
    )
    await service.initialize(make_identity())
    assert history.observed_limits == [20]


def test_the_assembly_service_never_imports_a_concrete_adapter():
    """Structural guard: the service module depends only on ports."""
    import pathlib

    source = pathlib.Path("uc02/application/context_assembly_service.py").read_text(
        encoding="utf-8"
    )
    assert "infrastructure.providers" not in source
    assert "Mock" not in source
    assert "uc02.domain.ports.providers" in source


def test_the_service_constructor_takes_ports_not_implementations():
    parameters = inspect.signature(ContextAssemblyService.__init__).parameters
    annotations = {
        name: param.annotation for name, param in parameters.items() if name != "self"
    }
    assert annotations["naric"] == "NaricProvider"
    assert annotations["courses"] == "CoursesProvider"
    assert annotations["legal"] == "LegalFootprintsProvider"
    assert annotations["history"] == "QuestionHistoryProvider"
    assert annotations["repository"] == "SessionContextRepository"


def test_company_stubs_implement_the_ports_and_fail_loudly():
    """The stubs mark where real adapters go; they never silently serve mock data."""
    assert issubclass(CompanyNaricProvider, NaricProvider)
    assert issubclass(CompanyCoursesProvider, CoursesProvider)
    assert issubclass(CompanyLegalFootprintsProvider, LegalFootprintsProvider)
    assert issubclass(CompanyQuestionHistoryProvider, QuestionHistoryProvider)


async def test_selecting_the_company_provider_raises_a_pointed_error():
    settings = make_settings(naric_provider="company")
    bundle = build_providers(settings)
    assert isinstance(bundle.naric, CompanyNaricProvider)
    try:
        await bundle.naric.get_qualification_level("learner-1")
    except ProviderNotImplemented as exc:
        assert "docs/integration.md" in str(exc)
    else:  # pragma: no cover - the stub must not succeed
        raise AssertionError("company stub should raise ProviderNotImplemented")
