"""The defined fallbacks.

UC-06 must report an explanation and a lesson reference for every question, and the question bank
does not guarantee either for every question in every deployment. When one is missing the report
says so, in these exact words, and **nothing is generated to fill the gap** -- no summarised
question text, no model-written explanation. A learner reading feedback has to be able to trust that
an explanation was written by whoever authored the question.

They are constants rather than string literals at the point of use so that the wording is consistent
across the API, the persisted report and the test UI, and so a test can assert on the fallback
rather than on a copy of it.
"""

from __future__ import annotations

#: Used when the question carries no authored explanation.
NO_EXPLANATION = "No explanation was recorded for this question."

#: Used when no lesson or topic can be resolved for the question.
NO_LESSON_REFERENCE = "No lesson reference is recorded for this question."

#: Used when a question's text could not be resolved from the frozen score row.
NO_QUESTION_TEXT = "(question text unavailable)"

#: Used when the learner left the question unanswered.
NO_ANSWER_GIVEN = "No answer given."

#: Used when the answer key yields nothing displayable -- only reachable for a question whose score
#: was recorded with an anomaly, and stated rather than shown as an empty space.
NO_CORRECT_ANSWER = "The correct answer is not available for this question."
