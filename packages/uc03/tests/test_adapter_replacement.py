"""Requirement 15 / integration guarantee.

The core UC-03 service must not change when mocks are replaced by company
adapters. These tests prove that three ways:

  1. `uc03.service` imports no concrete adapter at all.
  2. Adapters satisfy the contracts structurally - a company class need not
     import or subclass anything from UC-03.
  3. A complete set of "company" adapters, written here from scratch, drives
     the unmodified service to a correct answer.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

from uc03 import service as service_module
from uc03.adapters.mocks import (
    InMemoryQuestionLogger,
    MockContextProvider,
    MockLegalAuthorityProvider,
    StaticSessionAuthorizer,
    SystemClock,
)
from uc03.adapters.rule_based import (
    RuleBasedClassifier,
    RuleBasedTopicTagger,
    TemplateAnswerGenerator,
)
from uc03.contracts import (
    AnswerGenerator,
    Clock,
    ContextProvider,
    LegalAuthorityProvider,
    QuestionClassifier,
    QuestionLogger,
    SessionAuthorizer,
    TopicTagger,
)
from uc03.domain.enums import (
    AuthorityStatus,
    ExplanationDepth,
    FieldAvailability,
    NaricLevel,
    NaricLevelSource,
    ResponseStatus,
)
from uc03.domain.models import (
    AuthorityLookupResult,
    ClassificationResult,
    GeneratedProse,
    LearnerContext,
    Principal,
    VerifiedAuthority,
)
from uc03.domain.enums import ClassificationKind
from uc03.service import QAService

from .conftest import build_service


# --- 1. The core depends on contracts only -------------------------------


def test_service_module_imports_no_concrete_adapter():
    source = inspect.getsource(service_module)
    assert "from .adapters" not in source
    assert "import adapters" not in source
    assert "anthropic" not in source.lower()


def test_service_constructor_accepts_any_conforming_adapter():
    params = inspect.signature(QAService.__init__).parameters
    assert {
        "classifier",
        "generator",
        "context_provider",
        "authority_provider",
        "tagger",
        "logger",
        "authorizer",
        "clock",
    } <= set(params)


# --- 2. Structural conformance -------------------------------------------


def test_shipped_adapters_satisfy_the_contracts():
    assert isinstance(RuleBasedClassifier(), QuestionClassifier)
    assert isinstance(TemplateAnswerGenerator(), AnswerGenerator)
    assert isinstance(RuleBasedTopicTagger(), TopicTagger)
    assert isinstance(MockContextProvider(), ContextProvider)
    assert isinstance(MockLegalAuthorityProvider(), LegalAuthorityProvider)
    assert isinstance(InMemoryQuestionLogger(), QuestionLogger)
    assert isinstance(StaticSessionAuthorizer(), SessionAuthorizer)
    assert isinstance(SystemClock(), Clock)


# --- 3. A full "company" adapter set, written from scratch ----------------
#
# None of the classes below subclass or import a UC-03 base class. They only
# implement the documented method signatures.


class CompanyContextService:
    """Stands in for the company NARIC service + Legal Footprints."""

    async def get_context(self, *, user_id: str, session_id: str) -> LearnerContext:
        return LearnerContext(
            user_id=user_id,
            session_id=session_id,
            naric_level=NaricLevel.LEVEL_4,
            naric_level_source=NaricLevelSource.RETRIEVED,
            practice_area="immigration",
            practice_area_availability=FieldAvailability.PROVIDED,
        )


class CompanyAuthoritySource:
    """Stands in for the approved legal authority source."""

    async def lookup(self, *, question, topic_tag, practice_area) -> AuthorityLookupResult:
        return AuthorityLookupResult(
            status=AuthorityStatus.VERIFIED,
            authority=VerifiedAuthority(
                citation="Pepper v Hart [1992] UKHL 3",
                title="Pepper (Inspector of Taxes) v Hart",
                source="Company Approved Legal Library",
                url="https://www.bailii.org/uk/cases/UKHL/1992/3.html",
                verified_by="company-legal-library",
                verification_id="COMPANY-REF-42",
                retrieved_at=datetime(2026, 2, 2, tzinfo=timezone.utc),
            ),
        )


class CompanyEventLog:
    """Stands in for the company database / event log."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def log(self, record) -> None:
        self.rows.append(record.model_dump(mode="json"))


class CompanyAuth:
    """Stands in for the company authentication / session system."""

    async def authenticate(self, *, credential: str):
        return Principal(user_id="company-user") if credential == "sso-ticket" else None

    async def owns_session(self, *, user_id: str, session_id: str) -> bool:
        return session_id.startswith(f"{user_id}:")


class CompanyClassifier:
    async def classify(self, *, question: str) -> ClassificationResult:
        return ClassificationResult(kind=ClassificationKind.DEFINITIONAL, confidence=0.99)


class CompanyGenerator:
    async def generate(self, request) -> GeneratedProse:
        return GeneratedProse(
            plain_english=f"Plain answer at {request.depth.value} depth.",
            formal_definition="Formal statement of the rule.",
            practice_example=f"Example for {request.practice_area} work.",
        )


class CompanyTagger:
    async def propose_tag(self, *, question: str) -> str:
        return "LEGAL_SYSTEM"


class CompanyClock:
    def now(self) -> datetime:
        return datetime(2026, 3, 3, tzinfo=timezone.utc)


def test_company_adapters_satisfy_the_contracts_structurally():
    assert isinstance(CompanyContextService(), ContextProvider)
    assert isinstance(CompanyAuthoritySource(), LegalAuthorityProvider)
    assert isinstance(CompanyEventLog(), QuestionLogger)
    assert isinstance(CompanyAuth(), SessionAuthorizer)
    assert isinstance(CompanyClassifier(), QuestionClassifier)
    assert isinstance(CompanyGenerator(), AnswerGenerator)
    assert isinstance(CompanyTagger(), TopicTagger)
    assert isinstance(CompanyClock(), Clock)


async def test_unmodified_service_runs_on_a_full_company_adapter_set():
    event_log = CompanyEventLog()
    svc = QAService(
        classifier=CompanyClassifier(),
        generator=CompanyGenerator(),
        context_provider=CompanyContextService(),
        authority_provider=CompanyAuthoritySource(),
        tagger=CompanyTagger(),
        logger=event_log,
        authorizer=CompanyAuth(),
        clock=CompanyClock(),
    )

    principal = await svc.authenticate("sso-ticket")
    response = await svc.answer(
        question="What does ratio decidendi mean?",
        session_id="company-user:session-9",
        principal=principal,
    )

    assert response.status is ResponseStatus.ANSWERED
    assert response.classification.value == "definitional"
    assert response.meta.explanation_depth is ExplanationDepth.FOUNDATION
    assert response.parts.authority.status is AuthorityStatus.VERIFIED
    assert response.parts.authority.authority.verified_by == "company-legal-library"
    assert "immigration" in response.parts.practice_example
    assert response.meta.personalisation_applied is True
    assert len(event_log.rows) == 1
    assert event_log.rows[0]["rating_state"] == "pending"
    assert event_log.rows[0]["timestamp"].startswith("2026-03-03")


async def test_company_auth_still_blocks_cross_user_sessions():
    svc = QAService(
        classifier=CompanyClassifier(),
        generator=CompanyGenerator(),
        context_provider=CompanyContextService(),
        authority_provider=CompanyAuthoritySource(),
        tagger=CompanyTagger(),
        logger=CompanyEventLog(),
        authorizer=CompanyAuth(),
        clock=CompanyClock(),
    )
    principal = await svc.authenticate("sso-ticket")
    from uc03.errors import AuthorizationError
    import pytest

    with pytest.raises(AuthorizationError):
        await svc.answer(
            question="What does ratio decidendi mean?",
            session_id="someone-else:session-1",
            principal=principal,
        )


async def test_swapping_one_adapter_changes_nothing_else(alice):
    """Replacing only the authority provider leaves every other behaviour intact."""
    from .conftest import ALICE_SESSION

    baseline = await build_service().answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    swapped = await build_service(authority_provider=CompanyAuthoritySource()).answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )

    assert baseline.parts.plain_english == swapped.parts.plain_english
    assert baseline.classification == swapped.classification
    assert baseline.follow_up_actions == swapped.follow_up_actions
    # Only the authority differs.
    assert baseline.parts.authority.authority.verification_id != (
        swapped.parts.authority.authority.verification_id
    )
