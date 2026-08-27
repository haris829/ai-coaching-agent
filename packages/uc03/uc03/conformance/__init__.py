"""Adapter conformance kit for UC-03.

Point these suites at your own adapter and run pytest. They assert the
*behavioural* contract of each port - return types, normalisation to the
platform contract, typed failure behaviour, and that no upstream payload shape
or error string escapes the adapter boundary - without depending on any
particular mock's fixtures.

Usage: subclass the suite for the port you implemented and provide the
`adapter` fixture.

    # tests/test_our_context_adapter.py
    from uc03.conformance import ContextProviderConformance
    from ourco.uc03 import CompanyContextAdapter

    class TestCompanyContext(ContextProviderConformance):
        @pytest.fixture
        def adapter(self):
            return CompanyContextAdapter(base_url="http://localhost:9999")

        @pytest.fixture
        def known_user(self):
            return ("user-1", "session-1")

Run:  pytest tests/test_our_context_adapter.py

See INTEGRATION.md for the per-port command list.
"""

from .suites import (
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

__all__ = [
    "ContextProviderConformance",
    "LegalAuthorityProviderConformance",
    "QuestionClassifierConformance",
    "AnswerGeneratorConformance",
    "TopicTaggerConformance",
    "QuestionLoggerConformance",
    "InteractionReaderConformance",
    "SessionAuthorizerConformance",
    "FramingRegistryConformance",
]
