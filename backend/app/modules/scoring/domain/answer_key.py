"""The answer key, in UC-04's own terms. An answer key is everything needed to mark one delivered
question: the marks available, how the marks are apportioned, the deduction per incorrect
selection, which options are correct, the correct sequence, and — for a scenario — which single
answer is the *primary* one. **Why UC-04 names the marking policy itself.** UC-02 owns the
authoring vocabulary (``ALL_OR_NOTHING`` / ``PARTIAL_CREDIT`` / ``PARTIAL_CREDIT_WITH_PENALTY``)
and, as its own enums module says, has no interest in another capability holding opinions about
it. So UC-04 does not import that enum and does not declare a rival copy of it either: it
declares the *policy* it applies when marking, and the translation from UC-02's authoring value
happens once, in ``integration/question_bank/answer_key_adapter.py``. That is the same
anti-corruption shape UC-03 uses for UC-01's configuration. Pure data and pure functions: no
persistence, no HTTP, no other capability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.coercion import round4
from app.modules.scoring.domain.enums import AnswerKeySource, QuestionType
from app.modules.scoring.integration.attempt_delivery.types import DeliveredQuestion


class MarkingPolicy(StrEnum):
    """How marks are apportioned within one question. Applies only where a question can be *partly*
    right, which in practice means ``MULTI_SELECT``. Every other type is all-or-nothing by its
    own rule, whatever policy the key carries — see :mod:`app.modules.scoring.domain.scoring`."""

    #: Full marks only for a completely correct response.
    EXACT = "EXACT"
    #: Marks pro-rata for the correct part of the response, with no deduction.
    PARTIAL = "PARTIAL"
    #: Pro-rata marks, minus :attr:`AnswerKey.deduction_per_incorrect` for each incorrect selection.
    PARTIAL_WITH_DEDUCTION = "PARTIAL_WITH_DEDUCTION"


@dataclass(frozen=True, slots=True)
class KeyOption:
    """One option as the answer key sees it. ``option_id`` is the question bank's stable
    within-question label, which is also the id UC-03 delivered to the learner — that shared
    identity is what lets a key and a response be compared at all."""

    option_id: str
    text: str = ""
    is_correct: bool = False
    #: SCENARIO only: marks the single configured primary answer.
    is_primary: bool = False
    #: DRAG_TO_ORDER only: 1-based rank in the correct sequence. Never the presented order.
    correct_position: int | None = None


@dataclass(frozen=True, slots=True)
class AnswerKey:
    """The marking data for one delivered question version."""

    question_id: str
    question_version: int
    question_type: QuestionType
    #: Marks available for this question. Frozen with the attempt, so a later re-pointing of the
    #: question's marks cannot change a delivered attempt.
    max_marks: float
    marking_policy: MarkingPolicy
    #: Subtracted per incorrect selection under :attr:`MarkingPolicy.PARTIAL_WITH_DEDUCTION`.
    deduction_per_incorrect: float
    options: tuple[KeyOption, ...]
    source: AnswerKeySource
    #: UC-02's authored explanation, carried through for UC-06. Never shown by UC-04 itself.
    explanation: str | None = None
    #: Topic names frozen at snapshot time, used by UC-06 to resolve a lesson reference.
    topics: tuple[str, ...] = ()
    reference: str | None = None

    # ---- derived views ----------------------------------------------------

    @property
    def option_ids(self) -> tuple[str, ...]:
        return tuple(option.option_id for option in self.options)

    @property
    def correct_option_ids(self) -> tuple[str, ...]:
        return tuple(option.option_id for option in self.options if option.is_correct)

    @property
    def correct_order(self) -> tuple[str, ...]:
        """The correct sequence, from ``correct_position`` — never the presented order."""
        ranked = sorted(
            (option for option in self.options if option.correct_position is not None),
            key=lambda option: option.correct_position or 0,
        )
        return tuple(option.option_id for option in ranked)

    @property
    def primary_option_id(self) -> str | None:
        """The scenario's single primary answer. ``None`` when the key does not designate exactly
        one — which the scorer treats as an anomaly rather than guessing, because "score the
        primary answer" would otherwise be undefined."""
        primaries = [option.option_id for option in self.options if option.is_primary]
        if len(primaries) == 1:
            return primaries[0]
        if not primaries:
            correct = self.correct_option_ids
            # A single correct option is unambiguously the primary answer; this mirrors the
            # convenience UC-02's own validator applies when authoring a scenario.
            if len(correct) == 1:
                return correct[0]
        return None

    def option(self, option_id: str) -> KeyOption | None:
        for option in self.options:
            if option.option_id == option_id:
                return option
        return None

    def text_for(self, option_id: str) -> str:
        found = self.option(option_id)
        return found.text if found is not None and found.text else option_id

    def is_usable(self) -> bool:
        """Whether this key can mark its question at all. A key with no options, or with no correct
        answer of the kind its type needs, cannot produce a defensible mark. Saying so explicitly
        is what turns a silent zero into a reported ``MISSING_ANSWER_KEY``."""
        if not self.options:
            return False
        if self.question_type is QuestionType.DRAG_TO_ORDER:
            return len(self.correct_order) == len(self.options)
        return bool(self.correct_option_ids)

    def with_max_marks(self, max_marks: float) -> AnswerKey:
        """Return the key with the marks the attempt actually froze for this question. UC-03 stores
        ``points`` on the delivered question; that is the authority for how much the question was
        worth *in this attempt*, so it wins over the bank's current value."""
        return AnswerKey(
            question_id=self.question_id,
            question_version=self.question_version,
            question_type=self.question_type,
            max_marks=round4(max(0.0, max_marks)),
            marking_policy=self.marking_policy,
            deduction_per_incorrect=self.deduction_per_incorrect,
            options=self.options,
            source=self.source,
            explanation=self.explanation,
            topics=self.topics,
            reference=self.reference,
        )


# ---------------------------------------------------------------------------
# The fallback key
# ---------------------------------------------------------------------------


def derive_answer_key(
    delivered: DeliveredQuestion,
    *,
    marking_policy: MarkingPolicy = MarkingPolicy.EXACT,
    deduction_per_incorrect: float = 0.0,
    explanation: str | None = None,
    topics: tuple[str, ...] = (),
) -> AnswerKey:
    """Build an answer key from the copy UC-03 froze onto the attempt. Used when UC-02 has no
    snapshot for the exact version delivered. The frozen copy always carries ``isCorrect`` and
    ``correctPosition`` -- UC-03 keeps them precisely so an attempt can be scored against what
    the learner saw -- but it does not carry the authored marking policy or the scenario primary
    flag, which is why those are passed in by the adapter that resolved them. Scoring from this
    key is not a degraded guess: it is the same answer key, read from the other frozen copy of
    it. Which copy was used is recorded on every question score."""
    return AnswerKey(
        question_id=delivered.question_id,
        question_version=delivered.question_version,
        question_type=delivered.question_type,
        max_marks=round4(max(0.0, delivered.max_marks)),
        marking_policy=marking_policy,
        deduction_per_incorrect=max(0.0, deduction_per_incorrect),
        options=tuple(
            KeyOption(
                option_id=option.option_id,
                text=option.text,
                is_correct=bool(option.is_correct),
                # The frozen copy does not distinguish a scenario's primary answer; a single correct
                # option resolves it, and anything else is reported as ambiguous rather than
                # guessed.
                is_primary=False,
                correct_position=option.correct_position,
            )
            for option in delivered.options
        ),
        source=AnswerKeySource.ATTEMPT_SNAPSHOT,
        explanation=explanation,
        topics=topics,
    )
