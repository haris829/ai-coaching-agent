"""Composition root.

The only place adapters are chosen. Swapping a mock for a company adapter is a
one-line change here - `QAService` itself never mentions a concrete adapter.
"""

from __future__ import annotations

from .adapters.mocks import (
    InMemoryFramingRegistry,
    InMemoryQuestionLogger,
    MockContextProvider,
    MockLegalAuthorityProvider,
    StaticSessionAuthorizer,
    SystemClock,
)
from .adapters.rule_based import (
    RuleBasedClassifier,
    RuleBasedTopicTagger,
    TemplateAnswerGenerator,
)
from .config import Settings
from .service import QAService


def build_default_service(settings: Settings | None = None) -> QAService:
    """Development wiring: rule-based logic plus mock integrations.

    Replace the four mock adapters below with company adapters at integration
    time; see INTEGRATION.md.
    """
    question_log = InMemoryQuestionLogger()
    return QAService(
        classifier=RuleBasedClassifier(),
        generator=TemplateAnswerGenerator(),
        context_provider=MockContextProvider(),          # -> company NARIC / Legal Footprints
        authority_provider=MockLegalAuthorityProvider(),  # -> approved legal authority source
        tagger=RuleBasedTopicTagger(),
        logger=question_log,                              # -> company database / event log
        interaction_reader=question_log,                  # -> read path over the same store
        framing_registry=InMemoryFramingRegistry(),       # -> company framing storage
        authorizer=StaticSessionAuthorizer(),             # -> company auth / session system
        clock=SystemClock(),
        settings=settings,
    )
