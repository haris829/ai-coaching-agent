"""Legal Foot Prints behaviour end to end through the assembly service."""

from __future__ import annotations

from uc02.domain.models.enums import ExplanationDomain, SourceName, SourceStatus
from uc02.infrastructure.providers.mocks import LegalScenario
from tests.fixtures.factories import make_harness, make_identity


async def _context(scenario: LegalScenario):
    harness = make_harness(legal=scenario)
    outcome = await harness.service.initialize(make_identity())
    return outcome.context


async def test_complete_profile_yields_speciality_domain():
    context = await _context(LegalScenario.COMPLETE)
    legal = context.legal_profile
    assert legal.speciality_areas == ("Commercial contracts", "Consumer protection")
    assert legal.case_type_preferences == ("Breach of contract", "Unfair terms")
    assert legal.practice_area == "Commercial litigation"
    assert legal.explanation_domain is ExplanationDomain.SPECIALITY
    assert context.source_status[SourceName.LEGAL_PROFILE].status is SourceStatus.AVAILABLE


async def test_missing_speciality_falls_back_to_general_legal_and_records_status():
    context = await _context(LegalScenario.MISSING_SPECIALITY)
    legal = context.legal_profile
    assert legal.speciality_areas == ()
    assert legal.explanation_domain is ExplanationDomain.GENERAL_LEGAL
    # The practice area that *was* returned is kept; nothing is guessed.
    assert legal.practice_area == "Commercial litigation"
    assert context.source_status[SourceName.LEGAL_PROFILE].status is SourceStatus.PARTIAL


async def test_missing_practice_area_is_partial_and_left_as_none():
    context = await _context(LegalScenario.MISSING_PRACTICE_AREA)
    legal = context.legal_profile
    assert legal.practice_area is None
    assert legal.speciality_areas  # speciality still present
    assert legal.explanation_domain is ExplanationDomain.SPECIALITY
    assert context.source_status[SourceName.LEGAL_PROFILE].status is SourceStatus.PARTIAL


async def test_empty_profile_is_empty_not_unavailable():
    context = await _context(LegalScenario.EMPTY)
    legal = context.legal_profile
    assert legal.speciality_areas == ()
    assert legal.case_type_preferences == ()
    assert legal.practice_area is None
    assert legal.explanation_domain is ExplanationDomain.GENERAL_LEGAL
    outcome = context.source_status[SourceName.LEGAL_PROFILE]
    assert outcome.status is SourceStatus.EMPTY
    assert outcome.fallback_applied is False


async def test_unavailable_legal_applies_documented_defaults():
    context = await _context(LegalScenario.UNAVAILABLE)
    legal = context.legal_profile
    assert legal.speciality_areas == ()
    assert legal.case_type_preferences == ()
    assert legal.practice_area is None
    assert legal.explanation_domain is ExplanationDomain.GENERAL_LEGAL
    outcome = context.source_status[SourceName.LEGAL_PROFILE]
    assert outcome.status is SourceStatus.UNAVAILABLE
    assert outcome.fallback_applied is True


async def test_no_practice_area_is_ever_fabricated():
    """A missing speciality means general legal explanations, never a guess."""
    for scenario in (LegalScenario.EMPTY, LegalScenario.UNAVAILABLE, LegalScenario.TIMEOUT):
        harness = make_harness(
            legal=scenario, settings=_fast_timeout_settings()
        )
        context = (await harness.service.initialize(make_identity())).context
        assert context.legal_profile.practice_area is None
        assert context.legal_profile.speciality_areas == ()
        assert context.legal_profile.explanation_domain is ExplanationDomain.GENERAL_LEGAL


def _fast_timeout_settings():
    from tests.fixtures.factories import make_settings

    return make_settings(provider_timeout_ms=50, context_assembly_budget_ms=500)
