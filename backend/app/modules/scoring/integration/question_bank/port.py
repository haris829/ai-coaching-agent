"""The answer-key boundary. UC-04 needs the marking data for the exact question *version* an attempt
was delivered: the configured marking policy, the deduction per incorrect selection, which
options are correct, the correct sequence, and which answer is a scenario's primary one. Asking
for a version rather than a question is the whole point. UC-02 keeps an immutable snapshot per
version, so a key resolved this way cannot change when the question is later edited or retired --
and that is what makes a score reproducible and a historical result stable. The port answers with
UC-04's own :class:`AnswerKey`, so no UC-04 service or domain rule sees UC-02's model or its
authoring vocabulary."""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple, Protocol

from app.modules.scoring.domain.answer_key import AnswerKey

__all__ = ["AnswerKeyPort", "QuestionVersionRef"]


class QuestionVersionRef(NamedTuple):
    """One question at one version -- the identity an answer key is resolved by."""

    question_id: str
    version: int


class AnswerKeyPort(Protocol):
    """Resolution of answer keys for delivered question versions."""

    def find_answer_keys(
        self, refs: Sequence[QuestionVersionRef]
    ) -> dict[QuestionVersionRef, AnswerKey]:
        """Keys for the refs that could be resolved. Refs with no snapshot are simply absent from
        the result. That is a normal outcome, not an error: UC-04 falls back to the copy frozen
        onto the attempt, and reports ``MISSING_ANSWER_KEY`` only when neither source yields a
        usable key."""
        ...
