"""Fake generators.  Fully deterministic: no network, no API key, no sleep-based
flakiness, no randomness.  The entire test suite runs against these.

Scenario control
----------------

Each fake takes a default ``scenario`` and an optional ``script``: a list of
scenario names consumed one per call, falling back to the default once
exhausted.  That is what makes "four normal questions then a reworded repeat"
expressible without any timing or probability.

The misbehaviour scenarios exist because the brief requires UC-05 to *reject*
misbehaviour rather than pass it through.  A fake that only ever behaves is a
fake that cannot prove the guard works.
"""

from __future__ import annotations

import asyncio

from ...domain.enums import ExplanationProfile
from ...domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ...domain.models import (
    Dialogue,
    FourPartAnswer,
    GuidingQuestionResult,
    LearnerContext,
)
from ...registry import ANSWER_REGISTRY, GUIDING_QUESTION_REGISTRY
from .question_bank import BANK, entry_for

PORT_GUIDING = "guiding_question_generator"
PORT_ANSWER = "answer_generator"

#: Long enough that any realistic ``GENERATION_TIMEOUT_MS`` expires first, so
#: the ``slow`` scenario is decided by the timeout, never by a race.
SLOW_SECONDS = 30.0


class _Scripted:
    def __init__(self, scenario: str, script: list[str] | None) -> None:
        self.scenario = scenario
        self._script = list(script or [])
        self.calls = 0

    def next_scenario(self) -> str:
        self.calls += 1
        if self._script:
            return self._script.pop(0)
        return self.scenario


@GUIDING_QUESTION_REGISTRY.register("fake")
class FakeGuidingQuestionGenerator(_Scripted):
    """Deterministic guiding-question generator.

    Scenarios:

    ``normal``
        The next distinct question from the bank.
    ``verbatim_repeat``
        The most recent question, character for character.
    ``reworded_repeat``
        The most recent question reworded -- same probe, different words.
        Raw string equality does not catch this; the normalised comparison does.
    ``direct_answer``
        A declarative answer where a guiding question was requested.  The
        application must reject this as ``ProviderInvalidResponse``.
    ``praise``
        A guiding question carrying praise.  Must also be rejected: praise is
        a specified prohibition, not a style preference.
    ``restating``
        The learner's own question handed back.  A guiding question must move
        the learner toward the answer, not restate the question.
    ``malformed``
        Structurally invalid output (empty question).
    ``timeout`` / ``unavailable``
        The two retryable failure categories.
    ``slow``
        Answers eventually; used to prove the timeout budget is enforced.
    """

    def __init__(
        self,
        scenario: str = "normal",
        script: list[str] | None = None,
        **_: object,
    ) -> None:
        super().__init__(scenario, script)

    async def generate(
        self,
        dialogue_state: Dialogue,
        question: str,
        context: LearnerContext,
    ) -> GuidingQuestionResult:
        scenario = self.next_scenario()
        prompt_version = dialogue_state.prompt_version
        previous = dialogue_state.previous_questions()
        index = len(previous)

        if scenario == "timeout":
            raise ProviderTimeout(PORT_GUIDING, "scripted timeout")
        if scenario == "unavailable":
            raise ProviderUnavailable(PORT_GUIDING, "scripted outage")
        if scenario == "slow":
            await asyncio.sleep(SLOW_SECONDS)

        if scenario == "malformed":
            # An adapter must never let a malformed payload into the domain.
            raise ProviderInvalidResponse(
                PORT_GUIDING, "generator returned no question text"
            )

        if scenario == "direct_answer":
            return GuidingQuestionResult(
                question=(
                    "A contract is formed once offer, acceptance, consideration "
                    "and an intention to create legal relations are all present."
                ),
                probing_focus="none",
                prompt_version=prompt_version,
            )

        if scenario == "praise":
            return GuidingQuestionResult(
                question=(
                    "Excellent work, that is exactly right! Now, which party "
                    "carries the burden of proving that element?"
                ),
                probing_focus="where the evidential burden sits",
                prompt_version=prompt_version,
            )

        if scenario == "restating":
            return GuidingQuestionResult(
                question=dialogue_state.question_text,
                probing_focus="none",
                prompt_version=prompt_version,
            )

        if scenario == "verbatim_repeat" and previous:
            return GuidingQuestionResult(
                question=previous[-1],
                probing_focus=dialogue_state.exchanges[-1].probing_focus,
                prompt_version=prompt_version,
            )

        if scenario == "reworded_repeat" and previous:
            # Reword the bank entry that produced the most recent question.
            for entry in BANK:
                if entry.question == previous[-1]:
                    return GuidingQuestionResult(
                        question=entry.reworded,
                        probing_focus=entry.probing_focus,
                        prompt_version=prompt_version,
                    )
            raise AssertionError(  # pragma: no cover - bank/scenario mismatch
                "reworded_repeat requires the previous question to come from the bank"
            )

        entry = entry_for(index)
        return GuidingQuestionResult(
            question=entry.question,
            probing_focus=entry.probing_focus,
            prompt_version=prompt_version,
        )


_PROFILE_PREFIX: dict[ExplanationProfile, str] = {
    ExplanationProfile.BASIC: "In everyday terms",
    ExplanationProfile.INTERMEDIATE: "In practical terms",
    ExplanationProfile.ADVANCED: "Doctrinally",
}


@ANSWER_REGISTRY.register("fake")
class FakeAnswerGenerator(_Scripted):
    """Deterministic four-part answer generator.

    Scenarios: ``well_formed``, ``missing_part``, ``malformed``, ``timeout``,
    ``unavailable``, ``slow``.

    The well-formed answer varies with the explanation profile and practice
    area, which is how the tests observe that learner context actually reached
    the answer path rather than being fetched and discarded.
    """

    def __init__(
        self,
        scenario: str = "well_formed",
        script: list[str] | None = None,
        **_: object,
    ) -> None:
        super().__init__(scenario, script)

    async def generate(self, question: str, context: LearnerContext) -> FourPartAnswer:
        scenario = self.next_scenario()

        if scenario == "timeout":
            raise ProviderTimeout(PORT_ANSWER, "scripted timeout")
        if scenario == "unavailable":
            raise ProviderUnavailable(PORT_ANSWER, "scripted outage")
        if scenario == "slow":
            await asyncio.sleep(SLOW_SECONDS)
        if scenario in ("malformed", "missing_part"):
            # Both are the same category at the boundary: the payload cannot
            # be turned into a complete four-part answer.  The adapter is the
            # only place that knows *why*, and that detail stays here.
            raise ProviderInvalidResponse(
                PORT_ANSWER,
                "four-part answer incomplete"
                if scenario == "missing_part"
                else "unparseable payload",
            )

        prefix = _PROFILE_PREFIX[context.explanation_profile]
        area = context.practice_area or "general practice"
        return FourPartAnswer(
            plain_english_explanation=(
                f"{prefix}: the point turns on whether every element the rule "
                f"requires is actually present on these facts."
            ),
            formal_legal_definition=(
                "The doctrine requires each constituent element to be "
                "established on the balance of probabilities before the "
                "obligation arises."
            ),
            practical_example=(
                f"Worked example drawn from {area}: where one element is "
                f"missing, the obligation does not arise however clear the "
                f"parties' intentions were."
            ),
            authority_reference=(
                "See the leading appellate authority on the point and the "
                "statutory provision it construes."
            ),
        )
