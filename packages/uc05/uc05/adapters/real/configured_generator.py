"""Config-driven generator adapters.

Wired, registered and **disabled by default** (``GENERATOR=fake``).  Provider
and model come from configuration; there is no default that could reach a
network and no API key in the repository.

Selecting ``GENERATOR=configured`` without supplying the provider settings
fails at composition time with a message naming what is missing.  It does not
fall back to the fake -- silently serving fake coaching to a real learner is
the failure mode this whole design exists to prevent.

The actual upstream call is deliberately left as a single ``NotImplemented``
seam.  UC-05 has been given no API specification for a generator, so writing a
call here would mean inventing an external API, which the brief forbids.  The
class exists so that the wiring, the guardrail application and the prompt
registry usage are all real and reviewable now, and the integration engineer
fills in one method.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.errors import ProviderUnavailable
from ...domain.models import (
    Dialogue,
    FourPartAnswer,
    GuidingQuestionResult,
    LearnerContext,
)
from ...registry import ANSWER_REGISTRY, GUIDING_QUESTION_REGISTRY

PORT_GUIDING = "guiding_question_generator"
PORT_ANSWER = "answer_generator"


class _ConfiguredBase:
    def __init__(self, settings: Settings, port: str) -> None:
        missing = [
            name
            for name, value in (
                ("GENERATOR_PROVIDER", settings.generator_provider),
                ("GENERATOR_MODEL", settings.generator_model),
                ("GENERATOR_API_KEY", settings.generator_api_key),
            )
            if not value
        ]
        if missing:
            raise ProviderUnavailable(
                port, f"generator selected but unconfigured: {', '.join(missing)}"
            )
        self.provider = settings.generator_provider
        self.model = settings.generator_model
        self._api_key = settings.generator_api_key
        self._base_url = settings.generator_base_url
        self._timeout_seconds = settings.generation_timeout_seconds


@GUIDING_QUESTION_REGISTRY.register("configured")
class ConfiguredGuidingQuestionGenerator(_ConfiguredBase):
    def __init__(self, settings: Settings, **_: object) -> None:
        super().__init__(settings, PORT_GUIDING)

    async def generate(
        self,
        dialogue_state: Dialogue,
        question: str,
        context: LearnerContext,
    ) -> GuidingQuestionResult:
        # The system instruction comes from the server-side prompt registry and
        # the learner's text is fenced as data.  Both happen in the application
        # layer before this point; the adapter never composes a prompt from
        # learner input.
        raise NotImplementedError(
            "TODO(integration): call the configured provider. "
            "Translate failures into ProviderUnavailable / ProviderTimeout / "
            "ProviderInvalidResponse and return a GuidingQuestionResult."
        )


@ANSWER_REGISTRY.register("configured")
class ConfiguredAnswerGenerator(_ConfiguredBase):
    def __init__(self, settings: Settings, **_: object) -> None:
        super().__init__(settings, PORT_ANSWER)

    async def generate(self, question: str, context: LearnerContext) -> FourPartAnswer:
        raise NotImplementedError(
            "TODO(integration): call the configured provider and map its output "
            "onto the four discrete fields. A response missing any part is a "
            "ProviderInvalidResponse, never a partial answer."
        )
