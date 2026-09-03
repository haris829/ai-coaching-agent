"""Which AI coach, if any, this deployment binds.

One function, and it is the only place that knows more than one provider exists. The services depend
on the ``CoachingLLM`` port; the composition root asks here for an implementation of it.

    COACHING_LLM_PROVIDER=            ->  None  ->  UnconfiguredCoachingLLM  ->  "unavailable"
    COACHING_LLM_PROVIDER=anthropic   ->  AnthropicCoachingLLM   (api.anthropic.com)
    COACHING_LLM_PROVIDER=bedrock     ->  BedrockCoachingLLM     (the deployment's own AWS account)

``None`` is the important return value, and the default. A deployment that has not configured a
provider — or has misspelled one — gets ``UnconfiguredCoachingLLM``, which reports coaching as
unavailable and raises on generation. It tells learners the truth rather than serving invented
teaching, and the rest of the quiz chain is unaffected either way.

A misspelling is logged rather than silently ignored. "Coaching is unavailable and nobody knows why"
is a worse failure than a wrong value, and the log line names what was actually set.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.time import Clock
from app.modules.coaching.integration.llm import CoachingLLM
from app.modules.coaching.integration.llm_anthropic import (
    PROVIDER_ANTHROPIC,
    AnthropicCoachingLLM,
)
from app.modules.coaching.integration.llm_bedrock import (
    PROVIDER_BEDROCK,
    BedrockCoachingLLM,
)

logger = get_logger(__name__)

#: Every provider name this build understands, for the error message and for the docs.
KNOWN_PROVIDERS = (PROVIDER_ANTHROPIC, PROVIDER_BEDROCK)


def build_coaching_llm(
    settings: Settings, *, clock: Clock | None = None
) -> CoachingLLM | None:
    """The provider named in configuration, or ``None`` when none is bound."""
    provider = settings.coaching_llm_provider.strip().lower()
    if not provider:
        return None

    if not settings.coaching_llm_api_key:
        # Naming a provider without a credential would build an adapter that fails on every call.
        # Refusing here means coaching says it is unavailable up front, rather than after a learner
        # has typed a question.
        logger.warning(
            "coaching.provider_without_credential", extra={"provider": provider}
        )
        return None

    if provider == PROVIDER_ANTHROPIC:
        return AnthropicCoachingLLM(settings, clock=clock)
    if provider == PROVIDER_BEDROCK:
        return BedrockCoachingLLM(settings, clock=clock)

    logger.warning(
        "coaching.unknown_provider",
        extra={"provider": provider, "known": list(KNOWN_PROVIDERS)},
    )
    return None
