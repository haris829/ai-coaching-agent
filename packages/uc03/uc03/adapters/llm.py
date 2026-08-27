"""Optional Claude-backed adapters for the classifier and answer generator.

These satisfy the same `QuestionClassifier` / `AnswerGenerator` contracts as the
rule-based adapters, so enabling them is a change in `uc03.factory` and nowhere
else. They are NOT the default: the shipped service and the whole test suite run
on the deterministic adapters so they stay offline and repeatable.

Requires the optional dependency:  pip install "uc03-legal-concept-qa[llm]"

Integrity notes that make this safe to use for UC-03:
  * The generator returns `GeneratedProse` - three prose fields, no authority
    field - so the model is structurally unable to supply the Authority
    Reference part. That part comes only from a `LegalAuthorityProvider`.
  * The system prompt forbids citations, and the service's citation guard
    redacts any that appear anyway. Model output is never treated as verified.
  * The classifier is constrained to the closed outcome set by a JSON schema and
    re-validated against the enum on the way out.
  * Prompts live here, server-side. Nothing in a request can alter them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..domain.enums import ClassificationKind, FramingStrategy
from ..domain.models import ClassificationResult, GeneratedProse, GenerationRequest
from ..explanation import profile_for_depth

MODEL = "claude-opus-5"

# Opus 5's safety classifiers can decline a request; `fallbacks: "default"`
# re-runs it server-side on Anthropic's recommended substitute, routed by
# refusal category. The scalar form is gated on this exact beta header.
FALLBACK_BETAS = ["server-side-fallback-2026-07-01"]

CLASSIFIER_SYSTEM_PROMPT = """\
You classify questions for a legal-learning assistant. Choose exactly one:

  LEGAL_CONCEPT - asks about a legal principle, doctrine, rule or distinction.
  PROCESS       - asks how something is done: steps, procedure, what happens next.
  DEFINITIONAL  - asks what a term means or for its definition.
  AMBIGUOUS     - genuinely could be two or more of the above. Do not guess.
  OUT_OF_SCOPE  - not a legal-learning question.

If and only if you choose AMBIGUOUS, supply exactly ONE short clarification
question (a single sentence ending in a single question mark) that would let you
decide. Otherwise leave it null. Never answer the question itself."""

GENERATOR_SYSTEM_PROMPT = """\
You write teaching material for a legal-learning assistant. You produce exactly
three parts, and nothing else:

  plain_english     - explain the concept at the requested depth, using the
                      requested framing strategy.
  formal_definition - the formal legal statement of it.
  practice_example  - a short worked example.

HARD RULES - these are not stylistic preferences:
  * Never cite a case, statute, statutory instrument, section number, article,
    regulation or URL. Not even one you are confident about. A separate verified
    authority service supplies references; anything you write would be unverified
    and will be stripped before the learner sees it.
  * Never state or imply the learner's qualifications or practice area beyond
    what you are told. If no practice area is given, write a general example and
    say it is general.
  * Do not invent facts about the law. Where the position is uncertain, say so.
  * Use the framing strategy you are given. If you are asked for a contrast,
    do not give an analogy. A reworded version of an earlier explanation is
    not a new framing and will be rejected.
"""

_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": [k.value for k in ClassificationKind],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "clarification_question": {"type": ["string", "null"]},
        "rationale": {"type": ["string", "null"]},
    },
    "required": ["kind", "confidence", "clarification_question", "rationale"],
    "additionalProperties": False,
}

_PROSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plain_english": {"type": "string"},
        "formal_definition": {"type": "string"},
        "practice_example": {"type": "string"},
    },
    "required": ["plain_english", "formal_definition", "practice_example"],
    "additionalProperties": False,
}


_FRAMING_INSTRUCTIONS: dict[FramingStrategy, str] = {
    FramingStrategy.ANALOGY: (
        "Explain through a concrete non-legal analogy, then map it back."
    ),
    FramingStrategy.WORKED_EXAMPLE: (
        "Walk through one specific fact pattern from start to conclusion."
    ),
    FramingStrategy.CONTRAST_NEAR_MISS: (
        "Explain by contrast with a neighbouring concept it is often confused with."
    ),
    FramingStrategy.FIRST_PRINCIPLES: (
        "Derive it from the underlying problem the law is solving."
    ),
    FramingStrategy.PROCEDURAL_WALKTHROUGH: (
        "Explain via the practical sequence of steps in which it arises."
    ),
    FramingStrategy.MISCONCEPTION_CORRECTION: (
        "Lead with the most common misunderstanding and correct it."
    ),
}


class LLMUnavailable(RuntimeError):
    """The optional `anthropic` dependency is not installed."""


def _client(explicit: Any | None = None) -> Any:
    if explicit is not None:
        return explicit
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise LLMUnavailable(
            'The Claude-backed adapters need the optional dependency. '
            'Install with: pip install "uc03-legal-concept-qa[llm]"'
        ) from exc
    return AsyncAnthropic()


def _first_json(response: Any) -> dict[str, Any]:
    """Extract the structured payload, checking for a refusal first."""
    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("Claude declined the request and no fallback accepted it.")
    text = next(block.text for block in response.content if block.type == "text")
    return json.loads(text)


@dataclass
class AnthropicClassifier:
    """`QuestionClassifier` backed by Claude with a constrained output schema."""

    client: Any | None = None
    model: str = MODEL
    max_tokens: int = 2_048
    effort: str = "low"

    def __post_init__(self) -> None:
        self._client = _client(self.client)

    async def classify(self, *, question: str) -> ClassificationResult:
        response = await self._client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            betas=FALLBACK_BETAS,
            fallbacks="default",
            system=CLASSIFIER_SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": _CLASSIFICATION_SCHEMA},
            },
            messages=[{"role": "user", "content": question}],
        )
        payload = _first_json(response)

        # Re-validate rather than trusting the model to have honoured the enum.
        try:
            kind = ClassificationKind(payload["kind"])
        except (KeyError, ValueError):
            kind = ClassificationKind.AMBIGUOUS

        clarification = payload.get("clarification_question")
        if kind is ClassificationKind.AMBIGUOUS and not clarification:
            clarification = (
                "Could you tell me whether you want a definition, an explanation "
                "of the concept, or the steps in the process?"
            )
        if kind is not ClassificationKind.AMBIGUOUS:
            clarification = None  # only ambiguity may carry a clarification

        confidence = payload.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            confidence = 0.5

        return ClassificationResult(
            kind=kind,
            confidence=float(confidence),
            clarification_question=clarification,
            rationale=payload.get("rationale"),
        )


@dataclass
class AnthropicAnswerGenerator:
    """`AnswerGenerator` backed by Claude.

    Returns the three prose parts only. The Authority Reference part is not
    reachable from here - see the module docstring.
    """

    client: Any | None = None
    model: str = MODEL
    max_tokens: int = 8_000
    effort: str = "medium"

    def __post_init__(self) -> None:
        self._client = _client(self.client)

    async def generate(self, request: GenerationRequest) -> GeneratedProse:
        profile = profile_for_depth(request.depth)
        if request.practice_area_available and request.practice_area:
            personalisation = (
                f"Write the practice example for a {request.practice_area} "
                f"practitioner."
            )
        else:
            personalisation = (
                "No practice area is available for this learner. Write a general "
                "example and state plainly that it is general rather than tailored. "
                "Do not guess a speciality."
            )

        instruction = (
            f"Question ({request.classification.value}): {request.question}\n\n"
            f"Explanation depth: {profile.depth.value}. {profile.instruction}\n\n"
            f"{personalisation}"
        )

        response = await self._client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            betas=FALLBACK_BETAS,
            fallbacks="default",
            system=GENERATOR_SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": _PROSE_SCHEMA},
            },
            messages=[{"role": "user", "content": instruction}],
        )
        payload = _first_json(response)
        return GeneratedProse(
            plain_english=payload["plain_english"],
            formal_definition=payload["formal_definition"],
            practice_example=payload["practice_example"],
        )


__all__ = ["AnthropicClassifier", "AnthropicAnswerGenerator", "LLMUnavailable", "MODEL"]
