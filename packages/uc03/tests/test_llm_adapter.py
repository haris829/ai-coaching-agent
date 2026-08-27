"""The optional Claude-backed adapters.

Driven with an injected fake client, so these run offline with no `anthropic`
package and no API key. They cover the request shape and - more importantly -
the defensive re-validation applied to model output.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from uc03.adapters.llm import (
    FALLBACK_BETAS,
    MODEL,
    AnthropicAnswerGenerator,
    AnthropicClassifier,
)
from uc03.citation_guard import contains_citation
from uc03.contracts import AnswerGenerator, QuestionClassifier
from uc03.domain.enums import Classification, ClassificationKind, ExplanationDepth
from uc03.domain.models import GenerationRequest


class FakeMessages:
    def __init__(self, payload: dict | str, stop_reason: str = "end_turn") -> None:
        self._payload = payload
        self._stop_reason = stop_reason
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return SimpleNamespace(
            stop_reason=self._stop_reason,
            content=[SimpleNamespace(type="text", text=text)],
        )


def fake_client(payload, stop_reason: str = "end_turn"):
    messages = FakeMessages(payload, stop_reason)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return client, messages


GENERATION_REQUEST = GenerationRequest(
    question="What is negligence in tort law?",
    classification=Classification.LEGAL_CONCEPT,
    depth=ExplanationDepth.INTERMEDIATE,
    practice_area="employment",
    practice_area_available=True,
)


def test_adapters_satisfy_the_contracts():
    client, _ = fake_client({})
    assert isinstance(AnthropicClassifier(client=client), QuestionClassifier)
    assert isinstance(AnthropicAnswerGenerator(client=client), AnswerGenerator)


async def test_classifier_request_shape():
    client, messages = fake_client(
        {
            "kind": "legal_concept",
            "confidence": 0.9,
            "clarification_question": None,
            "rationale": "doctrinal question",
        }
    )
    result = await AnthropicClassifier(client=client).classify(question="What is negligence?")

    assert result.kind is ClassificationKind.LEGAL_CONCEPT
    call = messages.calls[0]
    assert call["model"] == MODEL == "claude-opus-5"
    assert call["betas"] == FALLBACK_BETAS
    assert call["fallbacks"] == "default"
    assert call["thinking"] == {"type": "adaptive"}
    # Output is schema-constrained to the closed outcome set.
    schema = call["output_config"]["format"]["schema"]
    assert set(schema["properties"]["kind"]["enum"]) == {k.value for k in ClassificationKind}
    assert schema["additionalProperties"] is False
    # The prompt is server-side, not derived from the question.
    assert "classify" in call["system"].lower()


async def test_classifier_rejects_an_invented_class():
    """A model that returns a class outside the enum must not widen the contract."""
    client, _ = fake_client(
        {
            "kind": "TOTALLY_NEW_CLASS",
            "confidence": 0.99,
            "clarification_question": None,
            "rationale": None,
        }
    )
    result = await AnthropicClassifier(client=client).classify(question="anything")
    assert result.kind is ClassificationKind.AMBIGUOUS
    assert result.clarification_question, "ambiguity must still carry one question"


async def test_ambiguous_without_a_question_gets_one():
    client, _ = fake_client(
        {
            "kind": "ambiguous",
            "confidence": 0.4,
            "clarification_question": None,
            "rationale": None,
        }
    )
    result = await AnthropicClassifier(client=client).classify(question="consideration")
    assert result.kind is ClassificationKind.AMBIGUOUS
    assert result.clarification_question.count("?") == 1


async def test_non_ambiguous_class_never_carries_a_clarification():
    client, _ = fake_client(
        {
            "kind": "process",
            "confidence": 0.8,
            "clarification_question": "Did you mean something else?",
            "rationale": None,
        }
    )
    result = await AnthropicClassifier(client=client).classify(question="How do I file?")
    assert result.kind is ClassificationKind.PROCESS
    assert result.clarification_question is None


@pytest.mark.parametrize("confidence", [2.5, -1, "high", None])
async def test_out_of_range_confidence_is_normalised(confidence):
    client, _ = fake_client(
        {
            "kind": "legal_concept",
            "confidence": confidence,
            "clarification_question": None,
            "rationale": None,
        }
    )
    result = await AnthropicClassifier(client=client).classify(question="q")
    assert 0.0 <= result.confidence <= 1.0


async def test_refusal_is_surfaced_not_silently_parsed():
    client, _ = fake_client("", stop_reason="refusal")
    with pytest.raises(RuntimeError, match="declined"):
        await AnthropicClassifier(client=client).classify(question="q")


async def test_generator_returns_only_the_three_prose_parts():
    client, messages = fake_client(
        {
            "plain_english": "Plain.",
            "formal_definition": "Formal.",
            "practice_example": "Example.",
        }
    )
    prose = await AnthropicAnswerGenerator(client=client).generate(GENERATION_REQUEST)

    assert prose.plain_english == "Plain."
    assert prose.formal_definition == "Formal."
    assert prose.practice_example == "Example."
    assert not hasattr(prose, "authority")

    schema = messages.calls[0]["output_config"]["format"]["schema"]
    assert set(schema["properties"]) == {
        "plain_english",
        "formal_definition",
        "practice_example",
    }
    assert "authority" not in schema["properties"]


async def test_generator_prompt_forbids_citations_and_carries_depth():
    client, messages = fake_client(
        {"plain_english": "a", "formal_definition": "b", "practice_example": "c"}
    )
    await AnthropicAnswerGenerator(client=client).generate(GENERATION_REQUEST)
    call = messages.calls[0]

    assert "Never cite" in call["system"]
    assert "URL" in call["system"]
    user_text = call["messages"][0]["content"]
    assert "intermediate" in user_text
    assert "employment" in user_text


async def test_generator_prompt_forbids_inventing_a_speciality():
    client, messages = fake_client(
        {"plain_english": "a", "formal_definition": "b", "practice_example": "c"}
    )
    request = GENERATION_REQUEST.model_copy(
        update={"practice_area": None, "practice_area_available": False}
    )
    await AnthropicAnswerGenerator(client=client).generate(request)
    user_text = messages.calls[0]["messages"][0]["content"]
    assert "general" in user_text.lower()
    assert "Do not guess a speciality" in user_text


def test_prompts_contain_no_citations_themselves():
    from uc03.adapters import llm

    assert not contains_citation(llm.GENERATOR_SYSTEM_PROMPT)
    assert not contains_citation(llm.CLASSIFIER_SYSTEM_PROMPT)


async def test_llm_generator_output_is_still_citation_guarded(alice):
    """End-to-end: even an LLM generator's citations are stripped by the service."""
    from .conftest import ALICE_SESSION, build_service

    client, _ = fake_client(
        {
            "plain_english": "See Fabricated v Invented [2024] UKSC 7 for the rule.",
            "formal_definition": "Per s. 4(2) of the Nonexistent Act 2001.",
            "practice_example": "An adviser reviews the file.",
        }
    )
    svc = build_service(generator=AnthropicAnswerGenerator(client=client))
    response = await svc.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    blob = " ".join(
        [
            response.parts.plain_english,
            response.parts.formal_definition,
            response.parts.practice_example,
        ]
    )
    assert "Fabricated v Invented" not in blob
    assert "Nonexistent Act 2001" not in blob
    assert response.meta.citation_guard_violations > 0
