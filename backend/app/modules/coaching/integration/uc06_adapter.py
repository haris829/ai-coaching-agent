"""UC-06's released feedback, seen through UC-07's port.

Two different things arrive through this one adapter, and keeping them apart is most of what it is
for.

**A gate.** The report must be ``GENERATED`` before any coaching may begin. Coaching is a
conversation *about the feedback*; offering it while the report is still pending would mean coaching
a learner on a result they have not been shown. UC-06's three statuses map onto UC-07's, and the
absence of a report maps to ``NOT_FOUND`` — deliberately distinct from ``PENDING``, because "no
report exists" and "the report is not ready" lead to different messages for the learner.

**Teaching context — some of which is poison.** A UC-06 feedback item exists to tell the learner
what the right answer was and why. That makes it simultaneously the richest source of lesson
information in the system and the most dangerous thing to forward to a model that must not know
the answer. So this adapter reports the record *as UC-06 actually produced it*, correct answer and
explanation included, and ``domain.sanitizer`` decides field by field what survives:

=========================  =========  =====================================================
Field                      Survives?  Why
=========================  =========  =====================================================
``lesson_reference``       yes        A pointer to material, not the answer to the question.
``learner_answer_summary`` yes        The learner's own answer, already known to them.
``explanation``            **no**     Written to state and justify the correct answer.
``correct_answer_text``    **no**     It is the answer key in prose.
``correct_option_ids``     **no**     It is the answer key in identifiers.
``metadata``               **no**     Dropped wholesale — see below.
=========================  =========  =====================================================

The last four are populated here on purpose, not by oversight. They are what the sanitiser builds
its forbidden-value list *from*; an adapter that helpfully stripped them would leave the security
tests asserting against material that never contained anything to leak.

TWO HONEST GAPS
---------------
**No topics.** ``qf_feedback_items`` has no topic column — UC-06 folds the frozen topic names into
its ``lesson_reference`` string instead. Rather than parse that back out, this adapter reports no
topics and lets ``domain.topics.resolve_topics`` fall through to UC-03's delivered topic names,
which are the same names from the same snapshot. When UC-06 gains a topic column, this is the one
line that changes.

**No misconception note.** UC-06 does not write one. The field stays ``None`` and coaching works
without it — the coach has the learner's answer and the question, which is what a first guiding
question is built from. Inventing a misconception here would be putting words in the author's mouth.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.time import iso_or_none
from app.modules.coaching.domain.errors import UpstreamProviderUnavailableError
from app.modules.coaching.integration.uc06 import (
    AttemptFeedback,
    FeedbackStatus,
    LessonReference,
    QuestionFeedback,
)
from app.modules.coaching.repositories.sqlalchemy import offload
from app.modules.feedback.domain.enums import ReportStatus
from app.modules.feedback.domain.fallbacks import (
    NO_ANSWER_GIVEN,
    NO_EXPLANATION,
    NO_LESSON_REFERENCE,
)
from app.modules.feedback.models import FeedbackItemRow, FeedbackReportRow

#: UC-06's report lifecycle as UC-07's gate names it. Only ``AVAILABLE`` permits coaching.
_STATUSES: dict[str, FeedbackStatus] = {
    ReportStatus.GENERATED.value: FeedbackStatus.AVAILABLE,
    ReportStatus.PENDING.value: FeedbackStatus.PENDING,
    ReportStatus.FAILED.value: FeedbackStatus.FAILED,
}


class FeedbackCoachingAdapter:
    """``FeedbackProvider`` over UC-06's tables."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- FeedbackProvider -------------------------------------------------

    async def get_attempt_feedback(self, attempt_id: str) -> AttemptFeedback | None:
        return await offload(self._get_attempt_feedback, attempt_id)

    # ---- synchronous body -------------------------------------------------

    def _get_attempt_feedback(self, attempt_id: str) -> AttemptFeedback | None:
        try:
            report = self._session.scalar(
                select(FeedbackReportRow).where(FeedbackReportRow.attempt_id == attempt_id)
            )
            if report is None:
                return None
            items = self._session.scalars(
                select(FeedbackItemRow)
                .where(FeedbackItemRow.report_id == report.id)
                .order_by(FeedbackItemRow.position)
            ).all()
        except SQLAlchemyError as exc:
            raise UpstreamProviderUnavailableError(
                "uc06", attempt_id=attempt_id, cause=exc
            ) from exc

        return AttemptFeedback(
            attempt_id=report.attempt_id,
            status=_STATUSES.get(report.status, FeedbackStatus.PENDING),
            learner_id=report.learner_id,
            course_id=report.course_id,
            generated_at=iso_or_none(report.generated_at),
            question_feedback=tuple(self._to_feedback(item) for item in items),
        )

    # ---- translation ------------------------------------------------------

    def _to_feedback(self, item: FeedbackItemRow) -> QuestionFeedback:
        return QuestionFeedback(
            question_id=item.question_id,
            # See "TWO HONEST GAPS" in the module docstring.
            topics=(),
            lesson_reference=self._lesson(item),
            misconception_note=None,
            learner_answer_summary=self._labels(item.learner_answer) or NO_ANSWER_GIVEN,
            # ---- Answer-bearing, carried so the sanitiser has something real to remove. ----
            explanation=self._authored(item.explanation, NO_EXPLANATION),
            correct_answer_text=self._labels(item.correct_answer),
            correct_option_ids=self._option_ids(item.correct_answer),
            # The per-option breakdown says which options were correct. Untrusted by definition and
            # dropped wholesale, but present, so the sanitiser guards its values too (§13).
            metadata={"option_breakdown": item.option_breakdown} if item.option_breakdown else {},
        )

    def _lesson(self, item: FeedbackItemRow) -> LessonReference | None:
        """UC-06's lesson reference, or ``None`` when it recorded the defined fallback.

        The stored value is a human-readable string (``"Topic: Reporting concerns"``), not an id —
        UC-06's question bank has no lesson table yet, and its own adapter is explicit that the
        reference stands in for one. It is carried verbatim as both id and title rather than parsed
        into parts, because inventing a lesson id from a topic name would be the kind of quiet
        fabrication this system avoids elsewhere.
        """
        reference = self._authored(item.lesson_reference, NO_LESSON_REFERENCE)
        if reference is None:
            return None
        return LessonReference(lesson_id=reference, title=reference)

    @staticmethod
    def _authored(value: str | None, fallback: str) -> str | None:
        """A field's authored content, or ``None`` when UC-06 stored its defined fallback.

        The fallbacks are statements that nothing was written — they are not answer-bearing and not
        teaching material, so forwarding one would only add noise to the sanitisation report and put
        a "no explanation was recorded" sentence in front of the coach as though it were context.
        """
        if not value or value == fallback:
            return None
        return value

    @staticmethod
    def _labels(display: dict[str, Any] | None) -> str | None:
        """A stored answer display (``{"optionIds": [...], "labels": [...]}``) as one line.

        Used for the learner's own answer, which is theirs and safe to show the coach, and for the
        correct answer, which is neither — that one goes only into the forbidden-value list.
        """
        if not isinstance(display, dict):
            return None
        labels = [str(label) for label in display.get("labels") or [] if str(label).strip()]
        return "; ".join(labels) if labels else None

    @staticmethod
    def _option_ids(display: dict[str, Any] | None) -> tuple[str, ...]:
        if not isinstance(display, dict):
            return ()
        ids = display.get("optionIds")
        if not isinstance(ids, list):
            return ()
        return tuple(str(item) for item in ids if str(item).strip())
