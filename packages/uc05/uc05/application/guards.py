"""Output guards.

"Socratic mode must never produce a direct answer except through: confirmed
exit on request, frustration exit, cap, or loop detection."  A generator does
not get to decide that.  Everything it returns passes through
``GuidingQuestionGuard`` first, and anything that is not a guiding question is
rejected as ``ProviderInvalidResponse`` -- not sanitised, not passed through
with a warning.

Four mechanical checks, all deterministic so they are testable without a model
(A-GQ-GUARD):

1.  **Interrogative.**  A guiding question contains a question mark.  A
    declarative answer does not.
2.  **No answer disclosure.**  A configured marker list catches the generator
    that wraps an answer in a question ("The answer is X -- see?").
3.  **No praise.**  Checked against the same explicit praise list the
    acknowledgement vocabulary is checked against, so a praise regression is
    caught mechanically wherever it originates.
4.  **Advances rather than restates.**  A question that is near-identical to
    the learner's own question has not moved them anywhere.

Check 3 deserves a note: praise arriving from a *generator* is the same defect
as praise in our own acknowledgement set, and the brief asks for it to be
caught mechanically.  Rejecting is the right response rather than stripping,
because a generator that praises has misunderstood its instructions and its
next output is not to be trusted either.
"""

from __future__ import annotations

from ..domain.errors import ProviderInvalidResponse
from ..domain.models import Dialogue, GuidingQuestionResult
from ..domain.normalisation import similarity
from ..domain.vocabulary import praise_terms_in

PORT = "guiding_question_generator"

#: A-GQ-RESTATEMENT: above this, the "guiding question" is the learner's own
#: question in different clothes.  Set higher than the loop threshold because a
#: legitimate first guiding question necessarily shares subject matter with the
#: question asked.
RESTATEMENT_THRESHOLD = 0.85

#: A-GQ-DISCLOSURE: declarative markers that betray an answer wearing a
#: question mark.
ANSWER_DISCLOSURE_MARKERS: tuple[str, ...] = (
    "the answer is",
    "the correct answer",
    "the rule is that",
    "the position is that",
    "in short, the answer",
    "to summarise, the",
    "you should conclude that",
    "the test is satisfied because",
    "which means that the outcome is",
)


class GuidingQuestionGuard:
    def __init__(self, restatement_threshold: float = RESTATEMENT_THRESHOLD) -> None:
        self.restatement_threshold = restatement_threshold

    def validate(self, result: GuidingQuestionResult, dialogue: Dialogue) -> None:
        text = (result.question or "").strip()

        if not text:
            raise ProviderInvalidResponse(PORT, "empty guiding question")

        if "?" not in text:
            raise ProviderInvalidResponse(
                PORT, "generator returned a statement where a question was requested"
            )

        lowered = text.lower()
        for marker in ANSWER_DISCLOSURE_MARKERS:
            if marker in lowered:
                raise ProviderInvalidResponse(
                    PORT, "generator disclosed the answer in a guiding question"
                )

        praise = praise_terms_in(text)
        if praise:
            raise ProviderInvalidResponse(
                PORT, f"generator output contained praise: {praise[0]!r}"
            )

        if similarity(text, dialogue.question_text) >= self.restatement_threshold:
            raise ProviderInvalidResponse(
                PORT, "generator restated the learner's question"
            )

        if not (result.probing_focus or "").strip():
            raise ProviderInvalidResponse(
                PORT, "guiding question carried no probing focus"
            )


class AnswerGuard:
    """The four-part answer is validated by its model; this adds the praise check.

    ``FourPartAnswer`` already refuses a blank part, so a missing part is a
    ``ValidationError`` at the adapter boundary.  What the model cannot check
    is tone, and the neutrality rule applies to every learner-facing string
    UC-05 emits, not only to acknowledgements.
    """

    def validate(self, answer_parts: dict[str, str]) -> None:
        for field, value in answer_parts.items():
            praise = praise_terms_in(value)
            if praise:
                raise ProviderInvalidResponse(
                    "answer_generator",
                    f"answer field {field!r} contained praise: {praise[0]!r}",
                )
