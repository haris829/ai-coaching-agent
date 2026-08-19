"""Assembling the coaching context (§11, §13).

Reads the delivered question and the learner's answer from UC-03, pairs them with UC-04's outcome
and UC-06's feedback record, and hands the whole untrusted bundle to the sanitiser.

The class is thin on purpose. It performs no filtering, no field selection and no redaction of its
own — every one of those decisions belongs to ``domain.sanitizer``, where it can be reviewed in one
place and tested without any I/O. What this service adds is the reading, the pairing, and one
refusal: a question that is not in the delivered paper never becomes coaching material (§20).

**The context is rebuilt on every turn.** Nothing sanitised is cached and nothing is stored. That
costs three upstream reads per exchange and buys two things worth more: a coaching conversation
always reflects the current state of the attempt, and no representation of a question — safe or
otherwise — is sitting in UC-07's storage waiting to be found (§22).
"""

from __future__ import annotations

from app.modules.coaching.domain.errors import QuestionNotInAttemptError
from app.modules.coaching.domain.sanitizer import (
    CoachingContextSanitizer,
    RawCoachingMaterial,
    SanitizedCoachingContext,
)
from app.modules.coaching.integration.uc03 import AttemptContext, AttemptProvider
from app.modules.coaching.integration.uc04 import QuestionResult
from app.modules.coaching.integration.uc06 import AttemptFeedback


class CoachingContextBuilder:
    """Turns upstream records into a ``SafeCoachingContext``, or refuses."""

    def __init__(
        self,
        *,
        attempts: AttemptProvider,
        sanitizer: CoachingContextSanitizer | None = None,
    ) -> None:
        self._attempts = attempts
        self._sanitizer = sanitizer or CoachingContextSanitizer()

    async def build(
        self,
        *,
        attempt: AttemptContext,
        result: QuestionResult,
        feedback: AttemptFeedback | None = None,
    ) -> SanitizedCoachingContext:
        """Build the safe context for one incorrectly answered question.

        Raises ``QuestionNotInAttemptError`` when UC-03 has no delivered record of the question,
        and ``AnswerKeyContaminationError`` when the sanitiser's final check finds anything (§25).
        Neither is recoverable by trying a bit harder, so neither is caught here.
        """
        questions = await self._attempts.get_delivered_questions(attempt.attempt_id)
        question = next(
            (item for item in questions if item.question_id == result.question_id), None
        )
        if question is None:
            # UC-04 scored a question UC-03 has no delivery record for. Coaching about a question
            # nobody can show the learner would be coaching about nothing.
            raise QuestionNotInAttemptError(attempt.attempt_id, result.question_id)

        answers = await self._attempts.get_learner_answers(attempt.attempt_id)
        answer = next(
            (item for item in answers if item.question_id == result.question_id), None
        )

        material = RawCoachingMaterial(
            attempt=attempt,
            question=question,
            result=result,
            answer=answer,
            feedback=feedback.feedback_for(result.question_id) if feedback else None,
        )
        return self._sanitizer.sanitize(material)
