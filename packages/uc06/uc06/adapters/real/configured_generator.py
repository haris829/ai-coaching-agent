"""ConfiguredAnswerGenerator - wired, and disabled by default.

ANSWER_GENERATOR=fake is the default and is what the entire test suite runs
against. This adapter exists so the path is built and reviewable, not so it can
be switched on quietly.

CONFIDENTIALITY - REQUIRES COMPANY SIGN-OFF BEFORE ENABLING
-----------------------------------------------------------
Enabling this adapter transmits case file content to a third-party model
provider. Case files contain confidential and potentially privileged client
information. That is a confidentiality decision for the company, not an
engineering default.

Exactly what would leave the process on each call, and nothing else:

  * the learner's question text, verbatim
  * the system instructions from uc06/application/prompt_registry.py
  * every fact identifier in the case file
  * the TEXT of every fact in the case file (fact_digest)
  * the charge labels on the case file
  * the legislation citations noted on the case file
  * the practice area, the explanation profile, and the case file identifier

Not transmitted: user_id, session_id, evidence items, interaction history,
audit records.

Before this is enabled the company must confirm, in writing: the provider and
region; whether the provider trains on, retains or logs submitted content, and
for how long; that transmission is compatible with the retainer and with legal
professional privilege for every matter type that can reach this component; and
who owns the decision for a matter where privilege has not been waived. Until
then this adapter raises on construction.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.errors import ConfigurationError, ProviderUnavailable
from ...domain.models import GenerationRequest, GenerationResult

PORT_NAME = "answer_generator"

#: Flipped only alongside a documented sign-off. It is not an environment
#: variable on purpose: enabling third-party transmission of privileged material
#: should require a code change and a review, not a deploy-time string.
CONFIDENTIALITY_SIGN_OFF_RECORDED = False


class ConfiguredAnswerGenerator:
    """Implements AnswerGenerator against a configured provider and model."""

    def __init__(self, settings: Settings) -> None:
        if not CONFIDENTIALITY_SIGN_OFF_RECORDED:
            raise ConfigurationError(
                "ANSWER_GENERATOR=configured is refused: transmitting case file content to a third-party "
                "model provider has not been signed off. See the confidentiality note at the top of "
                "uc06/adapters/real/configured_generator.py and docs/INTEGRATION.md."
            )
        if not settings.answer_generator_provider or not settings.answer_generator_model:
            raise ConfigurationError(
                "ANSWER_GENERATOR=configured requires ANSWER_GENERATOR_PROVIDER and ANSWER_GENERATOR_MODEL."
            )
        self._settings = settings

    def generate(self, request: GenerationRequest) -> GenerationResult:
        # TODO (integration): issue the provider call using request.timeout_ms,
        # request.system_instructions and the controlled fields listed above.
        # Translate transport failures into ProviderUnavailable / ProviderTimeout
        # and unusable answers into ProviderInvalidResponse. Never let the
        # provider SDK's exception text escape: it echoes the prompt, and the
        # prompt contains case facts.
        raise ProviderUnavailable(PORT_NAME, "configured_generator_not_implemented")

    @staticmethod
    def transmitted_fields() -> tuple[str, ...]:
        """The exact fields that would leave the process. Used by
        tests/test_confidentiality_flag.py and reproduced in docs/INTEGRATION.md
        so the list cannot drift away from the documentation unnoticed."""
        return (
            "question_text",
            "system_instructions",
            "available_fact_ids",
            "fact_digest",
            "charges",
            "legislation",
            "practice_area",
            "profile",
            "case_file_id",
        )
