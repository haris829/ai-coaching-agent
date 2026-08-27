"""Failure isolation, defaults, timeouts and the assembly budget (scope section 7).

The governing rule: no single source failure -- and no combination of failures --
may prevent a valid ``SessionContext`` from being returned.
"""

from __future__ import annotations

import time

from uc02.domain.models.context import (
    PERSONALIZATION_UNAVAILABLE_NOTICE,
    SessionContext,
)
from uc02.domain.models.enums import ExplanationDomain, LevelSource, SourceName, SourceStatus
from uc02.infrastructure.providers.mocks import (
    CoursesScenario,
    HistoryScenario,
    LegalScenario,
    NaricScenario,
)
from tests.fixtures.factories import make_harness, make_identity, make_settings

FAST = dict(provider_timeout_ms=100, context_assembly_budget_ms=1000)


async def test_all_sources_available_produces_a_fully_populated_context():
    harness = make_harness()
    context = (await harness.service.initialize(make_identity())).context
    assert isinstance(context, SessionContext)
    assert all(
        outcome.status is SourceStatus.AVAILABLE for outcome in context.source_status.values()
    )
    assert context.personalization.available is True
    assert context.personalization.notice is None
    assert len(context.personalization.contributing_sources) == 4


async def test_one_source_down_still_returns_a_valid_context():
    harness = make_harness(courses=CoursesScenario.UNAVAILABLE)
    context = (await harness.service.initialize(make_identity())).context
    assert context.source_status[SourceName.COURSES].status is SourceStatus.UNAVAILABLE
    assert context.personalization.available is True
    assert context.question_history.count == 20
    assert context.naric.level_source is LevelSource.RETRIEVED


async def test_two_sources_down_still_returns_a_valid_context():
    harness = make_harness(naric=NaricScenario.UNAVAILABLE, legal=LegalScenario.UNAVAILABLE)
    context = (await harness.service.initialize(make_identity())).context
    assert context.naric.level == 5
    assert context.naric.level_source is LevelSource.DEFAULT
    assert context.legal_profile.explanation_domain is ExplanationDomain.GENERAL_LEGAL
    assert context.courses.enrolments
    assert context.question_history.count == 20
    assert context.personalization.available is True
    assert set(context.personalization.contributing_sources) == {
        SourceName.COURSES,
        SourceName.QUESTION_HISTORY,
    }


async def test_three_sources_down_still_returns_a_valid_context():
    harness = make_harness(
        naric=NaricScenario.UNAVAILABLE,
        courses=CoursesScenario.UNAVAILABLE,
        legal=LegalScenario.UNAVAILABLE,
    )
    context = (await harness.service.initialize(make_identity())).context
    assert context.question_history.count == 20
    assert context.personalization.available is True
    assert context.personalization.contributing_sources == (SourceName.QUESTION_HISTORY,)


async def test_all_four_down_returns_a_valid_default_context_with_the_notice():
    harness = make_harness(
        naric=NaricScenario.UNAVAILABLE,
        courses=CoursesScenario.UNAVAILABLE,
        legal=LegalScenario.UNAVAILABLE,
        history=HistoryScenario.UNAVAILABLE,
    )
    context = (await harness.service.initialize(make_identity())).context

    assert isinstance(context, SessionContext)
    assert context.naric.level == 5
    assert context.naric.level_source is LevelSource.DEFAULT
    assert context.courses.enrolments == ()
    assert context.legal_profile.speciality_areas == ()
    assert context.legal_profile.case_type_preferences == ()
    assert context.legal_profile.practice_area is None
    assert context.legal_profile.explanation_domain is ExplanationDomain.GENERAL_LEGAL
    assert context.question_history.items == ()
    assert context.explanation_profile.template_id.value == "intermediate"

    assert context.personalization.available is False
    assert context.personalization.notice == PERSONALIZATION_UNAVAILABLE_NOTICE
    assert context.personalization.contributing_sources == ()

    assert all(
        outcome.status is SourceStatus.UNAVAILABLE for outcome in context.source_status.values()
    )
    assert all(outcome.fallback_applied for outcome in context.source_status.values())


async def test_mixed_failure_categories_are_recorded_separately():
    harness = make_harness(
        naric=NaricScenario.INVALID_RESPONSE,
        courses=CoursesScenario.UNAVAILABLE,
        legal=LegalScenario.EMPTY,
        history=HistoryScenario.ZERO,
    )
    context = (await harness.service.initialize(make_identity())).context
    statuses = {name: outcome.status for name, outcome in context.source_status.items()}
    assert statuses == {
        SourceName.NARIC: SourceStatus.INVALID,
        SourceName.COURSES: SourceStatus.UNAVAILABLE,
        SourceName.LEGAL_PROFILE: SourceStatus.EMPTY,
        SourceName.QUESTION_HISTORY: SourceStatus.EMPTY,
    }


async def test_all_sources_empty_is_reported_differently_from_all_sources_down():
    harness = make_harness(
        naric=NaricScenario.MISSING_QUALIFICATION,
        courses=CoursesScenario.EMPTY,
        legal=LegalScenario.EMPTY,
        history=HistoryScenario.ZERO,
    )
    context = (await harness.service.initialize(make_identity())).context
    assert context.personalization.available is False
    # A brand-new learner is not an outage: the notice must not claim one.
    assert context.personalization.notice != PERSONALIZATION_UNAVAILABLE_NOTICE
    assert context.personalization.notice is not None
    assert "temporarily unavailable" not in context.personalization.notice


async def test_context_is_bound_to_the_supplied_session_and_resolved_user():
    harness = make_harness()
    identity = make_identity(session_id="sess-abc-123", user_id="learner-99")
    context = (await harness.service.initialize(identity)).context
    assert context.session_id == "sess-abc-123"
    assert context.user_id == "learner-99"
    stored = await harness.repository.get("sess-abc-123")
    assert stored is not None
    assert stored.session_id == "sess-abc-123"
    assert stored.user_id == "learner-99"


async def test_providers_receive_the_resolved_user_id():
    harness = make_harness()
    await harness.service.initialize(make_identity(user_id="learner-77"))
    assert harness.naric.calls == ["learner-77"]
    assert harness.courses.calls == ["learner-77"]
    assert harness.legal.calls == ["learner-77"]
    assert harness.history.calls == ["learner-77"]


async def test_a_hanging_provider_is_treated_as_unavailable():
    harness = make_harness(naric=NaricScenario.TIMEOUT, settings=make_settings(**FAST))
    context = (await harness.service.initialize(make_identity())).context
    outcome = context.source_status[SourceName.NARIC]
    assert outcome.status is SourceStatus.UNAVAILABLE
    assert outcome.error_category.value == "timeout"
    assert context.naric.level_source is LevelSource.DEFAULT
    # The healthy sources are unaffected.
    assert context.courses.enrolments
    assert context.question_history.count == 20


async def test_all_four_hanging_completes_within_budget():
    settings = make_settings(provider_timeout_ms=100, context_assembly_budget_ms=1000)
    harness = make_harness(
        naric=NaricScenario.TIMEOUT,
        courses=CoursesScenario.TIMEOUT,
        legal=LegalScenario.TIMEOUT,
        history=HistoryScenario.TIMEOUT,
        settings=settings,
    )
    started = time.perf_counter()
    context = (await harness.service.initialize(make_identity())).context
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < settings.context_assembly_budget_ms
    assert all(
        outcome.error_category.value == "timeout" for outcome in context.source_status.values()
    )
    assert context.personalization.available is False
    assert context.personalization.notice == PERSONALIZATION_UNAVAILABLE_NOTICE


async def test_providers_are_called_concurrently_not_serially():
    """Four providers each hanging to a 200ms timeout must not take 800ms."""
    settings = make_settings(provider_timeout_ms=200, context_assembly_budget_ms=2000)
    harness = make_harness(
        naric=NaricScenario.TIMEOUT,
        courses=CoursesScenario.TIMEOUT,
        legal=LegalScenario.TIMEOUT,
        history=HistoryScenario.TIMEOUT,
        settings=settings,
    )
    started = time.perf_counter()
    await harness.service.initialize(make_identity())
    elapsed_ms = (time.perf_counter() - started) * 1000
    # Serial execution would need ~800ms; concurrent execution needs ~200ms.
    assert elapsed_ms < 600


async def test_budget_shorter_than_provider_timeout_is_still_enforced():
    """The total budget is a hard ceiling even when a provider timeout exceeds it."""
    settings = make_settings(provider_timeout_ms=5000, context_assembly_budget_ms=200)
    harness = make_harness(
        naric=NaricScenario.TIMEOUT,
        courses=CoursesScenario.TIMEOUT,
        legal=LegalScenario.TIMEOUT,
        history=HistoryScenario.TIMEOUT,
        settings=settings,
    )
    started = time.perf_counter()
    context = (await harness.service.initialize(make_identity())).context
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 2000  # nowhere near the 5s provider timeout
    assert all(
        outcome.status is SourceStatus.UNAVAILABLE for outcome in context.source_status.values()
    )
    assert all(
        outcome.error_category.value == "budget_exceeded"
        for outcome in context.source_status.values()
    )
    assert context.personalization.available is False


async def test_partial_results_survive_a_budget_overrun():
    """Sources that answered before the budget elapsed are still used."""
    settings = make_settings(provider_timeout_ms=5000, context_assembly_budget_ms=200)
    harness = make_harness(
        naric=NaricScenario.LEVEL_7,
        courses=CoursesScenario.SINGLE_ENROLMENT,
        legal=LegalScenario.TIMEOUT,
        history=HistoryScenario.TIMEOUT,
        settings=settings,
    )
    context = (await harness.service.initialize(make_identity())).context
    assert context.naric.level == 7
    assert context.explanation_profile.template_id.value == "advanced"
    assert len(context.courses.enrolments) == 1
    assert context.source_status[SourceName.LEGAL_PROFILE].error_category.value == "budget_exceeded"
    assert (
        context.source_status[SourceName.QUESTION_HISTORY].error_category.value
        == "budget_exceeded"
    )
    assert context.personalization.available is True


async def test_an_adapter_raising_an_undeclared_error_is_contained():
    """A misbehaving adapter must not take the whole assembly down."""

    class BrokenNaric:
        async def get_qualification_level(self, user_id: str):
            raise ValueError("adapter bug: forgot to translate the transport error")

    harness = make_harness()
    harness.service._naric = BrokenNaric()  # type: ignore[assignment]
    context = (await harness.service.initialize(make_identity())).context
    outcome = context.source_status[SourceName.NARIC]
    assert outcome.status is SourceStatus.INVALID
    assert outcome.error_category.value == "unexpected"
    assert context.naric.level == 5
    assert context.question_history.count == 20


async def test_every_context_carries_a_version_and_build_timestamp():
    harness = make_harness()
    context = (await harness.service.initialize(make_identity())).context
    assert context.context_version == "uc02.context.v1"
    assert context.built_at is not None
    assert context.built_at.tzinfo is not None
