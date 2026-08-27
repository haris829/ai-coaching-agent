"""FakeAnswerGenerator - fully deterministic. No network, no API key, no clock.

The entire test suite runs against this. It produces, on demand:

  well_formed          a fact-linked explanation calibrated to the profile
  fabricated_fact      a reference to a fact identifier not in the case file
  outcome_prediction   generated text that predicts an outcome
  self_disclaimer      generated text carrying its own, wrong, disclaimer
  missing_field        a result with empty content
  malformed            a result of the wrong shape entirely
  timeout              raises ProviderTimeout
  unavailable          raises ProviderUnavailable

Scenario selection is an attribute on the instance, not a config key and not
anything the learner can send: `generator.scenario = "fabricated_fact"`. Nothing
in the request body reaches it.
"""

from __future__ import annotations

from typing import Any, Final

from ...config import Settings
from ...domain.enums import ExplanationProfile
from ...domain.errors import ProviderTimeout, ProviderUnavailable
from ...domain.legal_tests import get_test, resolve_topic
from ...domain.models import GenerationRequest, GenerationResult

WELL_FORMED: Final = "well_formed"
FABRICATED_FACT: Final = "fabricated_fact"
OUTCOME_PREDICTION: Final = "outcome_prediction"
SELF_DISCLAIMER: Final = "self_disclaimer"
MISSING_FIELD: Final = "missing_field"
MALFORMED: Final = "malformed"
TIMEOUT: Final = "timeout"
UNAVAILABLE: Final = "unavailable"

SCENARIOS: Final[tuple[str, ...]] = (
    WELL_FORMED,
    FABRICATED_FACT,
    OUTCOME_PREDICTION,
    SELF_DISCLAIMER,
    MISSING_FIELD,
    MALFORMED,
    TIMEOUT,
    UNAVAILABLE,
)

#: The wrong disclaimer text a drifting model might emit. Held only in the fake,
#: to prove that generated disclaimer text is discarded rather than used.
MODEL_SUPPLIED_DISCLAIMER: Final = (
    "Disclaimer: this is general information and not legal advice. Consult a lawyer."
)

#: A fact identifier that is never present in any mock case file.
GHOST_FACT_ID: Final = "F-999"


class _Malformed:
    """Not a GenerationResult. Stands in for a provider returning junk."""

    def __init__(self) -> None:
        self.content = None


class FakeAnswerGenerator:
    """Implements AnswerGenerator."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self.scenario: str = WELL_FORMED
        self.scenarios_by_case_file: dict[str, str] = {}
        self.calls: list[GenerationRequest] = []

    # -- port ---------------------------------------------------------------
    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        scenario = self.scenarios_by_case_file.get(request.case_file_id or "", self.scenario)

        if scenario == TIMEOUT:
            raise ProviderTimeout("answer_generator", "generation_deadline_exceeded")
        if scenario == UNAVAILABLE:
            raise ProviderUnavailable("answer_generator", "generation_service_unreachable")
        if scenario == MALFORMED:
            return _Malformed()  # type: ignore[return-value]
        if scenario == MISSING_FIELD:
            return GenerationResult(content="", fact_ids_referenced=(), model_id="fake")

        profile = _profile(request.profile)
        test = _test_for(request)
        body = _explain(request, test, profile)

        if scenario == FABRICATED_FACT:
            return GenerationResult(
                content=body
                + f"\n\nThe account recorded at [[fact:{GHOST_FACT_ID}]] bears directly on the third element.",
                fact_ids_referenced=tuple(request.available_fact_ids[:2]) + (GHOST_FACT_ID,),
                model_id="fake",
                prompt_version=request.prompt_version,
            )
        if scenario == OUTCOME_PREDICTION:
            return GenerationResult(
                content=body
                + "\n\nOn this material your client will win at trial, and the court will find that the "
                "defence is made out.",
                fact_ids_referenced=tuple(request.available_fact_ids[:3]),
                model_id="fake",
                prompt_version=request.prompt_version,
            )
        if scenario == SELF_DISCLAIMER:
            return GenerationResult(
                content=body + "\n\n" + MODEL_SUPPLIED_DISCLAIMER,
                fact_ids_referenced=tuple(request.available_fact_ids[:3]),
                supplied_disclaimer=MODEL_SUPPLIED_DISCLAIMER,
                model_id="fake",
                prompt_version=request.prompt_version,
            )

        return GenerationResult(
            content=body,
            fact_ids_referenced=tuple(request.available_fact_ids[:3]),
            model_id="fake",
            prompt_version=request.prompt_version,
        )


def _profile(raw: str) -> ExplanationProfile:
    try:
        return ExplanationProfile(raw)
    except ValueError:
        return ExplanationProfile.INTERMEDIATE


def _test_for(request: GenerationRequest) -> Any:
    return resolve_topic(request.question_text, request.practice_area, request.charges) if request.question_text else get_test("general")


def _explain(request: GenerationRequest, test: Any, profile: ExplanationProfile) -> str:
    """Build a fact-linked explanation, calibrated to the profile.

    Calibration is on measurable properties, asserted in
    tests/test_explanation.py: basic carries no authorities and no doctrinal
    note and uses short sentences; advanced carries authorities, a doctrinal
    note and statutory references, and is materially longer.
    """
    facts = request.fact_digest[:3]
    parts: list[str] = []

    if profile is ExplanationProfile.BASIC:
        parts.append(f"This question is about {test.name}. Here is how the law works on these facts.")
        parts.append("The court asks a set of questions in order. Each one has to be answered before the next.")
        for index, element in enumerate(test.elements[:3], 1):
            parts.append(f"{index}. {element.split(':')[-1].strip().capitalize()}.")
        if facts:
            parts.append("Now look at what is in the case file.")
            for fact_id, text in facts:
                parts.append(f"[[fact:{fact_id}]] {text} This is the kind of material the first question is about.")
        parts.append("Nothing here says what the result should be. It shows what the court has to decide.")
        return "\n\n".join(parts)

    if profile is ExplanationProfile.INTERMEDIATE:
        parts.append(
            f"The relevant framework here is {test.name}. The material in the case file is set against its "
            "elements below, which is how the law is applied to facts of this kind."
        )
        parts.append("Elements: " + "; ".join(test.elements[:4]) + ".")
        parts.append("How the court approaches it: " + test.court_approach)
        if facts:
            parts.append("Applying that framework to the material in the case file:")
            for fact_id, text in facts:
                parts.append(
                    f"- [[fact:{fact_id}]] {text} This goes to whether the corresponding element is "
                    "supported, and to what the court would want evidence of."
                )
        parts.append("Burden and standard: " + test.burden)
        parts.append(
            "The purpose of this analysis is to show how the elements are engaged by material of this kind, "
            "not to state where the balance falls."
        )
        return "\n\n".join(parts)

    parts.append(
        f"The governing framework is {test.name}. What follows sets the material in the case file against "
        "each element, and identifies where the authorities place the line."
    )
    parts.append("Elements: " + "; ".join(test.elements) + ".")
    parts.append("How the court approaches it: " + test.court_approach)
    parts.append("Burden and standard: " + test.burden)
    parts.append("Authorities: " + "; ".join(test.authorities) + ".")
    parts.append("Doctrinal note: " + test.doctrinal_note)
    if request.legislation:
        parts.append("Legislation noted on the file: " + "; ".join(request.legislation) + ".")
    if facts:
        parts.append("Applying the framework to the material in the case file:")
        for fact_id, text in facts:
            parts.append(
                f"- [[fact:{fact_id}]] {text} On the authorities above, material of this kind bears on the "
                "element it is set against, and the question for analysis is what further evidence would be "
                "required before that element could be treated as established."
            )
    parts.append(
        "Each element remains a question of fact and degree for the tribunal. The analysis above shows how the "
        "framework operates on material of this kind; it does not indicate how the tribunal would resolve it."
    )
    return "\n\n".join(parts)
