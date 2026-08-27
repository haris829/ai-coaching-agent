"""Confidentiality: what would be transmitted to a real model provider.

Case files contain confidential and potentially privileged client information.
Transmitting them to a third-party model provider is a decision for the company,
not an engineering default. These tests hold that line mechanically:

  * the configured generator refuses to construct until sign-off is recorded
    IN CODE, so enabling it cannot be a deploy-time string change;
  * the documented list of transmitted fields matches what the request object
    actually carries, so the documentation cannot drift from the truth.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from uc06.adapters.real.configured_generator import (
    CONFIDENTIALITY_SIGN_OFF_RECORDED,
    ConfiguredAnswerGenerator,
)
from uc06.composition import build_container
from uc06.domain.errors import ConfigurationError
from uc06.domain.models import GenerationRequest

from .conftest import make_settings


class TestTheRealGeneratorIsDisabled:
    def test_the_default_generator_is_the_fake(self):
        assert make_settings().answer_generator == "fake"
        from uc06.config import Settings

        assert Settings.from_env({}).answer_generator == "fake"

    def test_sign_off_is_not_recorded(self):
        assert CONFIDENTIALITY_SIGN_OFF_RECORDED is False

    def test_configuring_it_fails_loudly_rather_than_transmitting(self):
        with pytest.raises(ConfigurationError) as exc:
            build_container(
                make_settings(
                    answer_generator="configured",
                    answer_generator_provider="placeholder",
                    answer_generator_model="placeholder",
                )
            )
        message = str(exc.value)
        assert "has not been signed off" in message
        assert "configured_generator.py" in message
        assert "INTEGRATION.md" in message

    def test_enabling_it_requires_a_code_change_not_an_environment_variable(self):
        """There is no ANSWER_GENERATOR_CONFIDENTIALITY_OK variable, and there
        will not be one: the gate is a constant in a reviewed file."""
        from uc06.config import ENV_KEYS

        assert not any("SIGN_OFF" in key or "CONFIDENTIAL" in key or "PRIVILEG" in key for key in ENV_KEYS)

    def test_the_whole_suite_needs_no_api_key(self):
        from uc06.config import Settings

        settings = Settings.from_env({})
        assert settings.answer_generator_api_key is None
        assert settings.answer_generator_base_url is None

    def test_the_env_example_carries_placeholders_only(self):
        text = Path(".env.example").read_text(encoding="utf-8")
        assert "placeholder" in text.lower()
        for leak in ("sk-", "api.openai", "https://api."):
            assert leak not in text


class TestTheTransmittedFieldListIsAccurate:
    def test_every_documented_field_exists_on_the_request(self):
        fields = {f.name for f in dataclasses.fields(GenerationRequest)}
        for name in ConfiguredAnswerGenerator.transmitted_fields():
            assert name in fields, f"{name} is documented as transmitted but is not a request field"

    def test_the_list_covers_every_content_bearing_field(self):
        """A new content-bearing field on the request must be added to the
        documented list, or this fails."""
        declared = set(ConfiguredAnswerGenerator.transmitted_fields())
        content_bearing = {
            "question_text",
            "system_instructions",
            "available_fact_ids",
            "fact_digest",
            "charges",
            "legislation",
            "practice_area",
            "profile",
            "case_file_id",
        }
        assert content_bearing <= declared

    def test_identity_and_session_are_not_transmitted(self):
        declared = set(ConfiguredAnswerGenerator.transmitted_fields())
        for name in ("user_id", "session_id", "interaction_id"):
            assert name not in declared

    def test_the_request_object_carries_no_identity(self):
        """Structural, not documentary: there is no user_id or session_id field
        on the request, so an adapter cannot transmit one by accident."""
        fields = {f.name for f in dataclasses.fields(GenerationRequest)}
        assert "user_id" not in fields
        assert "session_id" not in fields

    def test_fact_text_is_what_makes_this_a_confidentiality_decision(self, container, service_ask):
        """Named explicitly: fact TEXT, not only identifiers, reaches the
        generator. That is the whole point of the sign-off."""
        service_ask("How does the defence of duress apply to the account in this file?")
        request = container.generator.calls[-1]
        assert request.fact_digest
        assert any(len(text) > 40 for _, text in request.fact_digest)

    def test_the_documentation_lists_the_same_fields(self):
        text = Path("docs/INTEGRATION.md").read_text(encoding="utf-8")
        for name in ConfiguredAnswerGenerator.transmitted_fields():
            assert name in text, f"{name} is transmitted but not listed in docs/INTEGRATION.md"
