"""The port UC-03 uses to reach UC-02 (Question Bank Management).

Reads are the bulk of it: UC-03 selects from the bank and snapshots the result onto the attempt, and
it never edits a question — authoring belongs to UC-02.

There is one write, and it is not an exception to that rule.
:meth:`QuestionBankPort.record_delivery`
tells the bank *that a question was delivered*, which is a fact about UC-02's own content and drives
three of its behaviours: per-question usage counts, its refusal to hard-delete a question that has
been used, and its historical attempt report. UC-03's own snapshot answers a different question —
"what exactly did this learner see" — so the two records are not duplicates of each other; each
capability keeps the record its own rules depend on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.modules.attempt_delivery.integration.uc02.types import (
    BankQuestion,
    DeliveredQuestionRef,
    QuestionQuery,
)


class QuestionBankPort(Protocol):
    """Access to the question bank owned by UC-02."""

    def find_eligible_questions(self, query: QuestionQuery) -> list[BankQuestion]:
        """Return every question matching the filter.

        Implementations must exclude retired questions unless
        ``query.exclude_retired`` is explicitly False. Selection — count, quotas,
        randomisation — is UC-03's responsibility and is applied to the returned
        pool; the bank is not asked to shuffle or limit.
        """
        ...

    def get_questions_by_ids(self, question_ids: Sequence[str]) -> list[BankQuestion]:
        """Fetch specific questions by id, *including* retired ones.

        This is what lets an in-flight or historical attempt always be
        reconstructed. Missing ids are simply absent from the result.
        """
        ...

    def record_delivery(
        self,
        attempt_ref: str,
        delivered: Sequence[DeliveredQuestionRef],
        learner_ref: str | None = None,
    ) -> None:
        """Tell the bank which questions this attempt was given.

        Called inside the same transaction that creates the attempt, so an attempt and the bank's
        record of it are never half-written.

        Implementations must be **idempotent per attempt**: a retried creation must not double-count
        usage. They must also not fail the attempt for a reason of their own — the learner
        already has
        a valid attempt, and the bank's bookkeeping is not worth taking that away.
        """
        ...
