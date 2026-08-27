"""Self-test for the conformance kit.

Runs every suite against the shipped adapters. This proves the kit works and
that the mocks themselves conform - so a company engineer comparing their
adapter against it is comparing against a passing baseline.

It is also the worked example referenced from INTEGRATION.md: each class below
is exactly what an integrator writes for their own adapter.
"""

from __future__ import annotations

import pytest

from uc03.adapters.mocks import (
    InMemoryFramingRegistry,
    InMemoryQuestionLogger,
    MockContextProvider,
    MockLegalAuthorityProvider,
    StaticSessionAuthorizer,
)
from uc03.adapters.rule_based import (
    RuleBasedClassifier,
    RuleBasedTopicTagger,
    TemplateAnswerGenerator,
)
from uc03.conformance import (
    AnswerGeneratorConformance,
    ContextProviderConformance,
    FramingRegistryConformance,
    InteractionReaderConformance,
    LegalAuthorityProviderConformance,
    QuestionClassifierConformance,
    QuestionLoggerConformance,
    SessionAuthorizerConformance,
    TopicTaggerConformance,
)


class TestMockContextProvider(ContextProviderConformance):
    @pytest.fixture
    def adapter(self):
        return MockContextProvider()


class TestMockLegalAuthorityProvider(LegalAuthorityProviderConformance):
    @pytest.fixture
    def adapter(self):
        return MockLegalAuthorityProvider()


class TestRuleBasedClassifier(QuestionClassifierConformance):
    @pytest.fixture
    def adapter(self):
        return RuleBasedClassifier()


class TestTemplateAnswerGenerator(AnswerGeneratorConformance):
    @pytest.fixture
    def adapter(self):
        return TemplateAnswerGenerator()


class TestRuleBasedTopicTagger(TopicTaggerConformance):
    @pytest.fixture
    def adapter(self):
        return RuleBasedTopicTagger()


class TestInMemoryQuestionLogger(QuestionLoggerConformance):
    @pytest.fixture
    def adapter(self):
        return InMemoryQuestionLogger()


class TestInMemoryInteractionReader(InteractionReaderConformance):
    @pytest.fixture
    def adapter(self):
        return InMemoryQuestionLogger()


class TestStaticSessionAuthorizer(SessionAuthorizerConformance):
    @pytest.fixture
    def adapter(self):
        return StaticSessionAuthorizer()


class TestInMemoryFramingRegistry(FramingRegistryConformance):
    @pytest.fixture
    def adapter(self):
        return InMemoryFramingRegistry()
