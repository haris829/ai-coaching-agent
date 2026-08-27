"""ConfiguredAnswerGenerator - wired, and disabled by default.

Provider and model come from configuration. It is never exercised by the test suite: the suite
runs entirely against ``FakeAnswerGenerator``, needs no API key and costs nothing.

The network call itself is deliberately not written. Writing one would mean inventing an
external API, which the brief forbids. What is fixed here is the shape a real implementation
must honour - prompt selection stays server-side, the learner's question is passed as data, and
anything unmappable becomes ``ProviderInvalidResponse``.
"""

from __future__ import annotations

import os

from ...domain.errors import ProviderUnavailable
from ...domain.models import GenerationRequest, GenerationResult


class ConfiguredAnswerGenerator:
    name = "configured"

    def __init__(self) -> None:
        self.provider = os.environ.get("GENERATION_PROVIDER", "")
        self.model = os.environ.get("GENERATION_MODEL", "")
        self.timeout_ms = int(os.environ.get("GENERATION_TIMEOUT_MS", "10000"))

    def generate(self, request: GenerationRequest) -> GenerationResult:
        # TODO(integration): call the configured provider with
        #   - the server-side prompt identified by request.prompt_id / prompt_version
        #   - request.question passed as a delimited DATA value, never concatenated into the
        #     instruction block
        #   - request.quotable_spans as the ONLY lesson material included
        #   - request.candidate_cross_lesson_refs as the only referenceable lessons
        # then map the reply onto GenerationResult, raising ProviderInvalidResponse if the
        # shape does not fit and ProviderTimeout if the call exceeds self.timeout_ms.
        raise ProviderUnavailable(
            "answer_generator",
            "ConfiguredAnswerGenerator has no provider wired; set ANSWER_GENERATOR=fake",
        )
