"""Quiz answer protection - two independent signals, one converging output.

Signal 1: **known-item matching** (here, deterministic). Where the lesson carries quiz items,
the incoming question is matched against their text. A close match is strong evidence and needs
no model.

Signal 2: **intent classification** (the ``QuizIntentClassifier`` port). Detects answer-seeking
phrasing, direct and indirect.

The two are combined below into a ``QuizAssessment``. What the assessment does *not* do is
decide whether the learner gets help: the protected path and the normal path converge on the
same output - explain the concept the question tests. A false positive therefore costs a
differently-framed explanation, never a blocked response, which is what makes it safe to tune
detection toward caution.

``quiz_detection_confirmed`` semantics (an assumption, A-13):
* ``True``  - a known lesson quiz item matched, so detection is deterministically confirmed.
* ``False`` - intent fired but no known item matched and the question reads as genuine learning:
              a suspected false positive, logged for tuning.
* ``None``  - no detection fired, or the lesson exposes no quiz items so confirmation was
              impossible either way.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import QuizIntentLabel
from ..domain.models import (
    KnownItemMatch,
    LessonContent,
    QuizAssessment,
    QuizIntentResult,
)
from .text import content_tokens, jaccard


@dataclass(frozen=True)
class KnownItemMatcher:
    """Deterministic overlap match of a question against the lesson's own quiz items."""

    threshold: float

    def match(self, question: str, lesson: LessonContent | None) -> KnownItemMatch | None:
        if lesson is None or not lesson.quiz_items:
            return None
        asked = content_tokens(question)
        if not asked:
            return None

        best: KnownItemMatch | None = None
        for item in lesson.quiz_items:
            score = jaccard(asked, content_tokens(item.question_text))
            if score < self.threshold:
                continue
            if best is None or score > best.score or (score == best.score and item.quiz_item_id < best.quiz_item_id):
                best = KnownItemMatch(
                    quiz_item_id=item.quiz_item_id,
                    score=round(score, 4),
                    concept_tag=item.concept_tag,
                )
        return best


def assess(
    intent: QuizIntentResult,
    known_item: KnownItemMatch | None,
    lesson_exposes_quiz_items: bool,
) -> QuizAssessment:
    """Combine the two signals. Either one firing is enough to protect the answer."""
    intent_fired = intent.label == QuizIntentLabel.QUIZ_ANSWER_REQUEST.value
    ambiguous = intent.label == QuizIntentLabel.AMBIGUOUS.value
    detected = intent_fired or ambiguous or known_item is not None

    confirmed: bool | None
    suspected_false_positive = False
    if known_item is not None:
        confirmed = True
    elif not detected:
        confirmed = None
    elif not lesson_exposes_quiz_items:
        # Nothing to confirm against. Not evidence either way - never recorded as refuted.
        confirmed = None
    else:
        # Detection fired, the lesson does carry quiz items, and none of them matched.
        confirmed = False
        suspected_false_positive = True

    # Learning intent alongside detection is the other false-positive tell.
    if detected and "learning_intent" in intent.signals and known_item is None:
        suspected_false_positive = True

    return QuizAssessment(
        intent_detected=detected,
        detection_confirmed=confirmed,
        known_item=known_item,
        intent=intent,
        suspected_false_positive=suspected_false_positive,
    )
