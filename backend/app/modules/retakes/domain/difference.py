"""Meaningful difference (§7).

A retake that shows the same five questions in a different order is not a retake, it is the same
paper with the furniture moved. So the check compares **sets of question ids**, not sequences::

    Attempt 1:  Q1 Q2 Q3 Q4 Q5
    Retake:     Q3 Q1 Q5 Q2 Q4      →  identical set, zero new questions  →  not different

The rule has to account for the small-bank case, or it would report a defect every time a bank
of six questions produced a five-question paper twice. So the comparison is against what was
*achievable*, which the plan already computed:

* ``expected_fresh_questions`` — the most questions that could have differed from the previous
  attempt, given the eligible pool and the configured type quotas;
* ``new_question_count`` — how many actually did.

``satisfied`` is ``new_question_count >= expected_fresh_questions``. When the bank could supply a
completely new paper, anything less fails the check. When it could supply only two new questions,
two new questions passes — and ``reuse_unavoidable`` records why, so nobody later reads the
partial overlap as a bug.

A failed check never destroys the attempt. By the time it runs, UC-03 has created a real attempt
the learner can sit, and deleting it to signal a defect would break the immutability the module
rests on. The finding is recorded as an anomaly on the retake and returned in the response.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QuestionSetDifference:
    """How the retake's paper compares with the paper it followed."""

    previous_question_count: int
    retake_question_count: int
    #: Ids in the retake that were not in the previous attempt.
    new_question_count: int
    #: Ids in the retake that were also in the previous attempt.
    repeated_question_count: int
    #: Ids in the retake the learner had never seen at this quiz in *any* attempt.
    unseen_question_count: int
    #: The most that could have been new, from the plan.
    expected_fresh_questions: int
    #: True when the two papers are the same set of questions, however ordered.
    identical_question_set: bool
    #: True when the retake achieved every new question the bank could supply.
    satisfied: bool
    #: True when the bank could not supply a wholly new paper.
    reuse_unavoidable: bool
    #: Bounded sample for diagnostics; a full list of a 200-question paper helps nobody.
    repeated_question_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "previous_question_count": self.previous_question_count,
            "retake_question_count": self.retake_question_count,
            "new_question_count": self.new_question_count,
            "repeated_question_count": self.repeated_question_count,
            "unseen_question_count": self.unseen_question_count,
            "expected_fresh_questions": self.expected_fresh_questions,
            "identical_question_set": self.identical_question_set,
            "satisfied": self.satisfied,
            "reuse_unavoidable": self.reuse_unavoidable,
            "repeated_question_ids": list(self.repeated_question_ids),
        }


#: How many repeated ids are reported. Enough to diagnose, not enough to become a data dump.
REPEATED_SAMPLE_LIMIT = 20


def compare_question_sets(
    *,
    previous_question_ids: Sequence[str],
    retake_question_ids: Sequence[str],
    expected_fresh_questions: int,
    historical_question_ids: Sequence[str] = (),
) -> QuestionSetDifference:
    """Compare a delivered retake against the attempt it followed."""
    previous = frozenset(previous_question_ids)
    retake = frozenset(retake_question_ids)
    history = frozenset(historical_question_ids) | previous

    new_ids = retake - previous
    repeated = retake & previous
    required = max(0, int(expected_fresh_questions))

    return QuestionSetDifference(
        previous_question_count=len(previous),
        retake_question_count=len(retake),
        new_question_count=len(new_ids),
        repeated_question_count=len(repeated),
        unseen_question_count=len(retake - history),
        expected_fresh_questions=required,
        identical_question_set=bool(retake) and retake == previous,
        satisfied=len(new_ids) >= required,
        reuse_unavoidable=required < len(retake),
        repeated_question_ids=tuple(sorted(repeated))[:REPEATED_SAMPLE_LIMIT],
    )
