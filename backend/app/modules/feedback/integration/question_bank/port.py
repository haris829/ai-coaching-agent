"""The question-content boundary.

Two of the six things a feedback item must show are authored in the question bank rather than
derived from a score: the **explanation** and the **lesson reference**. This port resolves both
for the exact question *version* the attempt was delivered, so a later edit cannot change a report
that has already been generated -- and even that is belt-and-braces, because a generated report is
persisted in full.

On the lesson reference
-----------------------
The question bank has no lesson column today. What it does have, required on every question by its
own policy, is at least one **topic**, frozen by name into each question's version snapshot. The
topic is the closest truthful thing to a lesson reference the system holds, so that is what this
port returns, labelled as what it is. When the company's real lesson mapping arrives, it is this
adapter that changes
-- UC-06 asks for a lesson reference and does not care where one comes from.

Nothing here is generated. A question with no explanation resolves to ``None``, and the caller
substitutes the defined fallback."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple, Protocol

__all__ = ["QuestionContent", "QuestionContentPort", "QuestionVersionRef"]


class QuestionVersionRef(NamedTuple):
    """One question at one version."""

    question_id: str
    version: int


@dataclass(frozen=True, slots=True)
class QuestionContent:
    """The authored content a feedback item needs."""

    question_id: str
    version: int
    #: The authored explanation. ``None`` when the question carries none -- never a substitute.
    explanation: str | None = None
    #: A human-readable lesson/topic reference, or ``None`` when none can be resolved.
    lesson_reference: str | None = None
    #: The question bank's stable human reference (e.g. ``Q-000042``), for support and audit.
    question_reference: str | None = None
    #: Topic names frozen in the version snapshot.
    topics: tuple[str, ...] = ()
    #: Authored per-option feedback, keyed by option label. Shown alongside the option breakdown.
    option_feedback: tuple[tuple[str, str], ...] = ()


class QuestionContentPort(Protocol):
    """Resolution of authored explanations and lesson references."""

    def find_content(
        self, refs: Sequence[QuestionVersionRef]
    ) -> dict[QuestionVersionRef, QuestionContent]:
        """Content for the refs that could be resolved; absent refs are a normal outcome."""
        ...
