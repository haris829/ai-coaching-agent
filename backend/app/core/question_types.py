"""The question vocabulary both capabilities speak.

This is the *shared kernel*: the small set of names UC-01 and UC-02 must agree on for the system to
make sense. It lives in ``app.core`` and belongs to neither of them, so:

* UC-02 (the question bank) authors, validates and scores questions of these types;
* UC-01 (quiz configuration) selects from these types and explains which statuses are deliverable;
* neither capability has to import the other's domain package to name a question type.

**Vocabulary only — no rules.** How many options a `SINGLE_CHOICE` needs, which scoring strategies a
`MULTI_SELECT` may use, which statuses are deliverable: those are the question bank's rules and stay
in ``app.modules.question_bank.domain``. Putting them here would make this a dumping ground and give
UC-01 opinions it has no business holding.
"""

from __future__ import annotations

from enum import StrEnum


class QuestionType(StrEnum):
    """The five supported question structures. One definition for the whole system."""

    SINGLE_CHOICE = "SINGLE_CHOICE"
    TRUE_FALSE = "TRUE_FALSE"
    MULTI_SELECT = "MULTI_SELECT"
    SCENARIO = "SCENARIO"
    DRAG_TO_ORDER = "DRAG_TO_ORDER"


class QuestionStatus(StrEnum):
    """A question's lifecycle state.

    UC-02 owns the transitions; UC-01 reads it to explain why a retired question stopped counting
    towards capacity.
    """

    #: Authored but not publishable — never delivered.
    DRAFT = "DRAFT"
    #: Publishable; the only status eligible for future quiz delivery.
    ACTIVE = "ACTIVE"
    #: Withdrawn from future delivery, fully preserved for historical reporting.
    RETIRED = "RETIRED"


#: Human-readable labels, used in error messages, the admin UI and the CSV documentation.
QUESTION_TYPE_LABELS: dict[QuestionType, str] = {
    QuestionType.SINGLE_CHOICE: "Single choice",
    QuestionType.TRUE_FALSE: "True / False",
    QuestionType.MULTI_SELECT: "Multi-select",
    QuestionType.SCENARIO: "Scenario",
    QuestionType.DRAG_TO_ORDER: "Drag-to-order",
}

#: Canonical ordering. Every capability iterates types in this order, which is what makes a
#: configuration fingerprint stable and a capacity report predictable.
QUESTION_TYPE_ORDER: list[QuestionType] = list(QuestionType)


class QuestionPresentation(StrEnum):
    """How an attempt's questions are handed to the learner.

    Shared because UC-01 *configures* it and UC-03 *obeys* it.

    Deliberately **not** called "delivery mode": UC-01 already has a ``DeliveryMode``
    (``practice`` / ``assessment`` / ``exam``) describing how an attempt is graded and what feedback
    is given. The two were independently named the same thing in separate workspaces and mean
    entirely different things; conflating them would have quietly mis-configured every quiz.
    """

    #: One question per request; the server tracks a persisted cursor.
    ONE_AT_A_TIME = "ONE_AT_A_TIME"
    #: The whole paper is handed over at once.
    ALL_AT_ONCE = "ALL_AT_ONCE"


PRESENTATION_LABELS: dict[QuestionPresentation, str] = {
    QuestionPresentation.ONE_AT_A_TIME: "One question at a time",
    QuestionPresentation.ALL_AT_ONCE: "All questions at once",
}
