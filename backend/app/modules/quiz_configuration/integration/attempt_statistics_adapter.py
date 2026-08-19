"""Attempt counts, read from UC-03.

UC-01 keeps two of its own requirements that need attempt data — "remaining attempt information" in
the learner rules summary, and the attempt count shown against each configuration version — without
owning an attempt table. This adapter is the only file in UC-01 that knows UC-03 exists.

Read-only by construction: it issues aggregate ``SELECT``s and nothing else. Creating, answering,
timing and submitting an attempt are UC-03's, and there is no path from here to any of them.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.attempt_delivery.domain.enums import OPEN_ATTEMPT_STATUSES
from app.modules.attempt_delivery.models import QuizAttempt
from app.modules.quiz_configuration.ports import OpenAttempt

_OPEN = [status.value for status in OPEN_ATTEMPT_STATUSES]


class AttemptStatisticsAdapter:
    """:class:`~app.modules.quiz_configuration.ports.AttemptStatisticsPort` over UC-03's tables."""

    __slots__ = ("_db",)

    def __init__(self, db: Session) -> None:
        self._db = db

    def count_by_configuration_version(self, version_ids: Sequence[int]) -> dict[int, int]:
        if not version_ids:
            return {}
        # UC-03 stores the reference as an opaque string, as it does every cross-boundary id.
        wanted = {str(version_id): int(version_id) for version_id in version_ids}
        rows = self._db.execute(
            select(QuizAttempt.configuration_version_id, func.count(QuizAttempt.id))
            .where(QuizAttempt.configuration_version_id.in_(list(wanted)))
            .group_by(QuizAttempt.configuration_version_id)
        ).all()

        counts = {int(version_id): 0 for version_id in version_ids}
        for raw_version_id, total in rows:
            numeric = wanted.get(str(raw_version_id))
            if numeric is not None:
                counts[numeric] = int(total)
        return counts

    def count_for_learner(self, quiz_id: int, learner_id: str) -> int:
        total = self._db.scalar(
            select(func.count(QuizAttempt.id)).where(
                QuizAttempt.quiz_id == str(quiz_id),
                QuizAttempt.learner_id == str(learner_id),
            )
        )
        return int(total or 0)

    def find_open_for_learner(self, quiz_id: int, learner_id: str) -> OpenAttempt | None:
        attempt = self._db.scalar(
            select(QuizAttempt)
            .where(
                QuizAttempt.quiz_id == str(quiz_id),
                QuizAttempt.learner_id == str(learner_id),
                QuizAttempt.status.in_(_OPEN),
            )
            .order_by(QuizAttempt.created_at.desc())
            .limit(1)
        )
        if attempt is None:
            return None
        return OpenAttempt(
            id=attempt.id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            configuration_version_id=attempt.configuration_version_id,
        )
