"""Translating the system's question types into UC-10's reporting vocabulary.

Two vocabularies, deliberately kept separate.

The system has exactly five question types (``app.core.question_types``) — the shared kernel UC-01
and UC-02 agree on. UC-10 has a broader, more generic set, because it was written to report on
*any* assessment system's data: ``MULTIPLE_CHOICE``, ``SHORT_ANSWER``, ``NUMERIC`` and so on.

Neither should be bent to fit the other. Widening UC-10's enum would make analytics carry this
system's specific type names, and narrowing the kernel's would lose a distinction UC-02 validates
and scores against. So the mapping lives here, at the boundary, and the *exact* name travels in
``question_type_label`` — which UC-10 already has for precisely this, and which is what a dashboard
actually displays.

The result: analytics groups by a generic type it understands, and shows the name this system uses.
Nothing is lost, and no dashboard shows "OTHER" where it means "Scenario".
"""

from __future__ import annotations

from app.core.question_types import QuestionType as SystemQuestionType
from app.modules.analytics.domain.enums import ReportingQuestionType

#: The system's five types, mapped onto the nearest faithful reporting type.
#:
#: ``SCENARIO`` maps to ``OTHER`` because it is a container for sub-questions rather than a
#: question shape UC-10's vocabulary has a word for — and ``OTHER`` with an exact label is more
#: honest than claiming it is a multiple choice. ``DRAG_TO_ORDER`` maps to ``MATCHING``, which is
#: the closest genuine equivalent: both ask a learner to relate items rather than pick one.
_TYPE_MAP: dict[str, ReportingQuestionType] = {
    SystemQuestionType.SINGLE_CHOICE.value: ReportingQuestionType.MULTIPLE_CHOICE,
    SystemQuestionType.TRUE_FALSE.value: ReportingQuestionType.TRUE_FALSE,
    SystemQuestionType.MULTI_SELECT.value: ReportingQuestionType.MULTI_SELECT,
    SystemQuestionType.SCENARIO.value: ReportingQuestionType.OTHER,
    SystemQuestionType.DRAG_TO_ORDER.value: ReportingQuestionType.MATCHING,
}

#: The exact name to display, so the generic mapping above is never what an administrator reads.
QUESTION_TYPE_LABELS: dict[str, str] = {
    SystemQuestionType.SINGLE_CHOICE.value: "Single choice",
    SystemQuestionType.TRUE_FALSE.value: "True / False",
    SystemQuestionType.MULTI_SELECT.value: "Multi-select",
    SystemQuestionType.SCENARIO.value: "Scenario",
    SystemQuestionType.DRAG_TO_ORDER.value: "Drag to order",
}


def map_question_type(raw: str | None) -> ReportingQuestionType:
    """The reporting type for one of the system's question types.

    An unrecognised value becomes ``OTHER`` rather than raising: a question authored by a later
    release must still be countable, and analytics refusing to load a dashboard because it met a
    type it had no word for would be the wrong failure.
    """
    if raw is None:
        return ReportingQuestionType.OTHER
    return _TYPE_MAP.get(raw, ReportingQuestionType.OTHER)


def question_type_label(raw: str | None) -> str | None:
    return QUESTION_TYPE_LABELS.get(raw) if raw else None
