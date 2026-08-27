"""Shared fixtures. Every test composes the service from explicit adapters so
each scenario is visible at the call site."""

from __future__ import annotations

import pytest

from uc03.adapters.mocks import (
    FixedClock,
    InMemoryFramingRegistry,
    InMemoryQuestionLogger,
    MockContextProvider,
    MockLegalAuthorityProvider,
    StaticSessionAuthorizer,
    full_context,
)
from uc03.adapters.rule_based import (
    RuleBasedClassifier,
    RuleBasedTopicTagger,
    TemplateAnswerGenerator,
)
from uc03.config import Settings
from uc03.domain.models import Principal
from uc03.service import QAService

ALICE = Principal(user_id="user-alice")
ALICE_SESSION = "session-alice-1"
BOB_SESSION = "session-bob-1"


def build_service(
    *,
    classifier=None,
    generator=None,
    context_provider=None,
    authority_provider=None,
    tagger=None,
    logger=None,
    authorizer=None,
    framing_registry=None,
    interaction_reader=None,
    settings=None,
) -> QAService:
    # Explicit `is None` checks, never `or`: an empty InMemoryQuestionLogger is
    # falsy (it defines __len__), so `or` would silently discard it.
    question_log = InMemoryQuestionLogger() if logger is None else logger
    return QAService(
        classifier=RuleBasedClassifier() if classifier is None else classifier,
        generator=TemplateAnswerGenerator() if generator is None else generator,
        context_provider=(
            MockContextProvider(builder=full_context)
            if context_provider is None
            else context_provider
        ),
        authority_provider=(
            MockLegalAuthorityProvider() if authority_provider is None else authority_provider
        ),
        tagger=RuleBasedTopicTagger() if tagger is None else tagger,
        logger=question_log,
        authorizer=StaticSessionAuthorizer() if authorizer is None else authorizer,
        framing_registry=(
            InMemoryFramingRegistry() if framing_registry is None else framing_registry
        ),
        interaction_reader=(
            question_log if interaction_reader is None else interaction_reader
        ),
        clock=FixedClock(),
        settings=Settings() if settings is None else settings,
    )


@pytest.fixture
def logger() -> InMemoryQuestionLogger:
    return InMemoryQuestionLogger()


@pytest.fixture
def service(logger: InMemoryQuestionLogger) -> QAService:
    return build_service(logger=logger)


@pytest.fixture
def alice() -> Principal:
    return ALICE
