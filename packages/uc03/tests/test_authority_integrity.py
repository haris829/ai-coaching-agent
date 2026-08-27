"""Requirement 5 - legal authority integrity.

Never fabricate legislation, cases, citations, URLs, sections or authorities.
Three independent defences are tested here:
  1. Structural  - the generator contract carries no authority field at all.
  2. Provider    - only a LegalAuthorityProvider can mint a VERIFIED result.
  3. Guard       - citation-shaped prose is redacted unless it was verified.
"""

from __future__ import annotations

import inspect

from uc03.adapters.mocks import (
    MockLegalAuthorityProvider,
    RogueCitationGenerator,
    StaticTopicTagger,
)
from uc03.citation_guard import REDACTION, contains_citation, scrub
from uc03.config import Settings
from uc03.domain.enums import AuthorityStatus, ResponseStatus
from uc03.domain.models import GeneratedProse, GenerationRequest
from uc03.service import DEGRADED_AUTHORITY, DEGRADED_CITATIONS

from .conftest import ALICE_SESSION, build_service

VERIFIED_QUESTION = "What is negligence in tort law?"
UNCOVERED_QUESTION = "What is the test for unfair dismissal in employment law?"


async def _answer(service, alice, question=VERIFIED_QUESTION):
    return await service.answer(
        question=question, session_id=ALICE_SESSION, principal=alice
    )


# --- 1. Structural -------------------------------------------------------


def test_generator_contract_cannot_express_an_authority():
    """The generator is structurally incapable of writing the authority part."""
    assert "authority" not in GeneratedProse.model_fields
    assert "authority" not in GenerationRequest.model_fields
    assert set(GeneratedProse.model_fields) == {
        "plain_english",
        "formal_definition",
        "practice_example",
    }


def test_verified_authority_requires_verification_provenance():
    from uc03.domain.models import VerifiedAuthority

    for required in ("verified_by", "verification_id", "source"):
        assert required in VerifiedAuthority.model_fields
        assert VerifiedAuthority.model_fields[required].is_required()


# --- 2. Provider ---------------------------------------------------------


async def test_verified_authority_is_returned_with_provenance(service, alice):
    response = await _answer(service, alice)
    authority = response.parts.authority
    assert authority.status is AuthorityStatus.VERIFIED
    assert authority.authority is not None
    assert authority.authority.citation
    assert authority.authority.verified_by
    assert authority.authority.verification_id


async def test_mock_provider_labels_itself_as_a_mock(service, alice):
    """A development fixture must never look like a company verification."""
    response = await _answer(service, alice)
    assert "MOCK" in response.parts.authority.authority.verified_by.upper()


async def test_no_authority_returns_defined_message_and_verification_routes(alice):
    svc = build_service(authority_provider=MockLegalAuthorityProvider(force_no_authority=True))
    response = await _answer(svc, alice)
    authority = response.parts.authority
    assert authority.status is AuthorityStatus.NO_VERIFIED_AUTHORITY
    assert authority.authority is None
    assert authority.message == Settings().no_authority_message
    assert set(authority.verification_routes) == {"Westlaw", "BAILII"}
    assert "westlaw" in authority.message.lower()
    assert "bailii" in authority.message.lower()


async def test_topic_without_catalogue_entry_yields_no_authority(alice):
    """The provider does not invent a citation to fill a gap."""
    svc = build_service()
    response = await _answer(svc, alice, UNCOVERED_QUESTION)
    assert response.status is ResponseStatus.ANSWERED
    assert response.parts.authority.status is AuthorityStatus.NO_VERIFIED_AUTHORITY


async def test_authority_provider_failure_yields_no_authority_not_a_guess(alice):
    svc = build_service(authority_provider=MockLegalAuthorityProvider(fail=True))
    response = await _answer(svc, alice)
    assert response.status is ResponseStatus.ANSWERED
    assert response.parts.authority.status is AuthorityStatus.NO_VERIFIED_AUTHORITY
    assert response.parts.authority.message
    assert DEGRADED_AUTHORITY in response.meta.degraded


async def test_invalid_topic_tag_never_produces_a_verified_authority(alice):
    svc = build_service(tagger=StaticTopicTagger(tag="TOTALLY_MADE_UP"))
    response = await _answer(svc, alice)
    assert response.parts.authority.status is AuthorityStatus.NO_VERIFIED_AUTHORITY


# --- 3. Citation guard ---------------------------------------------------


async def test_fabricated_citations_are_stripped_from_prose(alice):
    """A misbehaving generator must not get fabricated citations to the user."""
    svc = build_service(
        generator=RogueCitationGenerator(),
        authority_provider=MockLegalAuthorityProvider(force_no_authority=True),
    )
    response = await _answer(svc, alice)
    parts = response.parts
    blob = " ".join(
        [parts.plain_english, parts.formal_definition, parts.practice_example]
    )

    for fabrication in (
        "Smith v Jones",
        "[2021] UKSC 99",
        "example.com/fake-case",
        "Imaginary Legal Practice Act 2019",
        "R v Nobody",
    ):
        assert fabrication not in blob, f"{fabrication!r} leaked to the user"

    assert REDACTION in blob
    assert response.meta.citation_guard_violations > 0
    assert DEGRADED_CITATIONS in response.meta.degraded
    # The authority section still reports honestly rather than borrowing prose.
    assert parts.authority.status is AuthorityStatus.NO_VERIFIED_AUTHORITY


async def test_verified_citation_may_appear_in_prose(alice):
    """Allow-listing is by exact verified string, not by shape."""
    verified = "Donoghue v Stevenson [1932] UKHL 100"

    class QuotingGenerator:
        async def generate(self, request):  # noqa: ANN001, ANN202
            return GeneratedProse(
                plain_english=f"The leading authority is {verified}, which set the test.",
                formal_definition="A duty is owed to one's neighbour in law.",
                practice_example="An adviser identifies the element in dispute.",
            )

    svc = build_service(generator=QuotingGenerator())
    response = await _answer(svc, alice)
    assert verified in response.parts.plain_english
    assert response.meta.citation_guard_violations == 0


def test_guard_detects_common_uk_citation_shapes():
    for text in (
        "See Donoghue v Stevenson [1932] UKHL 100.",
        "Under s. 12(3) of the Sale of Goods Act 1979 the term is implied.",
        "Refer to section 6 of the statute.",
        "See https://www.bailii.org/something.",
        "Regulation (EU) 2016/679 applies.",
        "As held in (1932) AC 562.",
    ):
        assert contains_citation(text), text
        assert scrub(text).violations >= 1


def test_guard_leaves_clean_prose_untouched():
    clean = (
        "A duty of care arises where harm to a neighbour is reasonably "
        "foreseeable, and the claimant must prove each element."
    )
    result = scrub(clean)
    assert result.violations == 0
    assert result.text == clean


def test_default_generator_emits_no_citations():
    """The shipped template generator is clean by construction."""
    from uc03.adapters.rule_based import TemplateAnswerGenerator

    source = inspect.getsource(TemplateAnswerGenerator)
    assert not contains_citation(source)
