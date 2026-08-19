"""Feedback generation, end to end. Generation reads three frozen things -- UC-04's confirmed score,
UC-05's determination, UC-02's authored content for the delivered question versions -- assembles
the report, and persists it twice over: as rows, one per question, and as the rendered payload
the API serves. Two requirements shape the control flow. **Generation failure must not remove the
score or the pass/fail result.** Nothing in this service writes to UC-04's or UC-05's tables; it
cannot. A failure is recorded on the report row, which stays ``PENDING`` and retryable, and the
caller is told. The learner keeps their score and their verdict. **Historical feedback must stay
consistent.** A generated report is served from its stored payload and never rebuilt: the trigger
on ``qf_feedback_reports`` refuses to update it, and every input it was built from was already
immutable. Regenerating is only possible while a report is pending."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import Clock
from app.modules.feedback.domain import errors
from app.modules.feedback.domain.enums import ReportStatus
from app.modules.feedback.domain.report import FeedbackItem, build_report
from app.modules.feedback.integration.certification.port import OutcomePort
from app.modules.feedback.integration.question_bank.port import (
    QuestionContentPort,
    QuestionVersionRef,
)
from app.modules.feedback.integration.scoring.port import ScoredAttempt, ScoreDetailPort
from app.modules.feedback.models import FeedbackItemRow, FeedbackReportRow
from app.modules.feedback.repositories import FeedbackRepository

logger = get_logger(__name__)

#: Recorded on a report whose assembly raised. Stable, so an operator can filter on it.
GENERATION_ERROR = "FEEDBACK_ASSEMBLY_FAILED"


@dataclass(frozen=True, slots=True)
class FeedbackOutcome:
    """What one call to :meth:`FeedbackService.generate` did."""

    report: FeedbackReportRow
    items: list[FeedbackItemRow]
    created: bool
    #: True when an already-generated report was returned unchanged.
    replayed: bool

    @property
    def generated(self) -> bool:
        return self.report.status == str(ReportStatus.GENERATED)


class FeedbackService:
    """Generate and read one attempt's feedback report."""

    __slots__ = ("_session", "_reports", "_scores", "_outcomes", "_content", "_clock")

    def __init__(
        self,
        *,
        session: Session,
        reports: FeedbackRepository,
        scores: ScoreDetailPort,
        outcomes: OutcomePort,
        content: QuestionContentPort,
        clock: Clock,
    ) -> None:
        self._session = session
        self._reports = reports
        self._scores = scores
        self._outcomes = outcomes
        self._content = content
        self._clock = clock

    # ------------------------------------------------------------------ read

    def find_report(
        self, attempt_id: str, *, learner_id: str | None = None
    ) -> tuple[FeedbackReportRow, list[FeedbackItemRow]]:
        """The stored report and its items, scoped to the attempt's owner."""
        self._require_owned_score(attempt_id, learner_id)
        report = self._reports.get_by_attempt(attempt_id)
        if report is None:
            raise errors.feedback_not_found(attempt_id)
        return report, self._reports.list_items(report.id)

    def list_reports(
        self, learner_id: str, *, quiz_id: str | None = None
    ) -> list[FeedbackReportRow]:
        return self._reports.list_for_learner(learner_id, quiz_id=quiz_id)

    # -------------------------------------------------------------- generate

    def generate(
        self,
        attempt_id: str,
        *,
        learner_id: str | None = None,
        raise_on_failure: bool = True,
    ) -> FeedbackOutcome:
        """Generate the report, or return the one that already exists. ``raise_on_failure`` is False
        when the submission pipeline calls this: a feedback failure must not turn into a failed
        submission. A learner asking for their feedback explicitly does want to be told, so the
        API passes True."""
        scored = self._require_owned_score(attempt_id, learner_id)
        if not scored.confirmed:
            raise errors.score_not_confirmed(attempt_id, scored.status)

        existing = self._reports.get_by_attempt(attempt_id)
        if existing is not None and existing.status == str(ReportStatus.GENERATED):
            # Frozen: served as it was generated, never rebuilt.
            return FeedbackOutcome(
                report=existing,
                items=self._reports.list_items(existing.id),
                created=False,
                replayed=True,
            )

        report, created = self._claim(scored, existing)
        now = self._clock.now()
        # Committed before assembly begins, so an attempt that fails still counts as an attempt. The
        # failure path rolls back, and a run counter that a rollback erased would make a report look
        # as though nobody had ever tried to generate it.
        self._reports.record_run(report.id, now)
        self._session.commit()

        try:
            outcome = self._outcomes.get_outcome(attempt_id)
            refs = [
                QuestionVersionRef(question.question_id, question.question_version)
                for question in scored.questions
            ]
            content = self._content.find_content(refs) if refs else {}
            built = build_report(scored, outcome, content)

            self._reports.replace_items(
                report.id, [_item_fields(item) for item in built.items], now=now
            )
            stored = self._reports.mark_generated(
                report.id,
                now=now,
                outcome_id=outcome.outcome_id if outcome is not None else None,
                total_marks=built.total_marks,
                maximum_marks=built.maximum_marks,
                percentage=built.percentage,
                pass_mark_percentage=built.pass_mark_percentage,
                passed=built.passed,
                time_taken_seconds=built.time_taken_seconds,
                total_questions=built.total_questions,
                correct_count=built.correct_count,
                incorrect_count=built.incorrect_count,
                unanswered_count=built.unanswered_count,
                payload=built.to_dict(),
            )
            if not stored:
                # Another run generated it first; its report is authoritative.
                self._session.rollback()
                winner = self._reports.get_by_attempt(attempt_id)
                if winner is None:
                    # pragma: no cover - defensive
                    raise errors.internal_error()
                return FeedbackOutcome(
                    report=winner,
                    items=self._reports.list_items(winner.id),
                    created=False,
                    replayed=True,
                )
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            logger.error(
                "feedback.persistence_failed", extra={"attemptId": attempt_id}, exc_info=exc
            )
            raise errors.persistence_failed("generate_feedback") from exc
        except errors.AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded on the row, never escapes raw
            self._session.rollback()
            message = str(exc) or exc.__class__.__name__
            self._reports.mark_failure(
                report.id,
                status=ReportStatus.PENDING,
                failure_code=GENERATION_ERROR,
                failure_message=message,
                now=self._clock.now(),
            )
            self._session.commit()
            logger.error(
                "feedback.generation_failed",
                extra={"attemptId": attempt_id, "reason": message},
            )
            if raise_on_failure:
                raise errors.generation_failed(
                    attempt_id,
                    "The feedback report could not be generated. The score and the pass/fail "
                    "outcome are unchanged, and generation can be retried.",
                    reason=message,
                ) from exc
            pending = self._reports.get_by_attempt(attempt_id)
            if pending is None:
                # pragma: no cover - defensive
                raise errors.internal_error() from exc
            return FeedbackOutcome(
                report=pending,
                items=self._reports.list_items(pending.id),
                created=created,
                replayed=False,
            )

        final = self._reports.get_by_attempt(attempt_id)
        if final is None:
            # pragma: no cover - defensive
            raise errors.internal_error()
        return FeedbackOutcome(
            report=final,
            items=self._reports.list_items(final.id),
            created=created,
            replayed=False,
        )

    # ------------------------------------------------------------- internals

    def _require_owned_score(self, attempt_id: str, learner_id: str | None) -> ScoredAttempt:
        """Resolve the score, enforcing ownership. Ownership is checked against the learner recorded
        on the score rather than through a fourth port to UC-03: UC-04 copied the learner id off
        the attempt when it scored, and a result that is not yours is indistinguishable from one
        that does not exist."""
        scored = self._scores.get_scored_attempt(attempt_id)
        if scored is None:
            raise errors.attempt_not_found(attempt_id)
        if learner_id is not None and str(scored.learner_id) != str(learner_id):
            raise errors.attempt_not_found(attempt_id)
        return scored

    def _claim(
        self, scored: ScoredAttempt, existing: FeedbackReportRow | None
    ) -> tuple[FeedbackReportRow, bool]:
        """Get or create the attempt's single report row."""
        if existing is not None:
            return existing, False

        now = self._clock.now()
        try:
            with self._session.begin_nested():
                created = self._reports.insert_pending(
                    attempt_id=scored.attempt_id,
                    result_id=scored.result_id,
                    learner_id=scored.learner_id,
                    course_id=scored.course_id,
                    quiz_id=scored.quiz_id,
                    attempt_number=scored.attempt_number,
                    pass_mark_percentage=scored.pass_mark_percentage,
                    created_at=now,
                    updated_at=now,
                )
            return created, True
        except IntegrityError:
            winner = self._reports.get_by_attempt(scored.attempt_id)
            if winner is None:  # pragma: no cover - the unique index is the only cause
                raise
            return winner, False


def _item_fields(item: FeedbackItem) -> dict[str, Any]:
    """Flatten one built item into the columns ``qf_feedback_items`` stores."""
    return {
        "position": item.position,
        "question_id": item.question_id,
        "question_version": item.question_version,
        "question_reference": item.question_reference,
        "question_type": item.question_type,
        "question_text": item.question_text,
        "scenario_text": item.scenario_text,
        "explanation": item.explanation,
        "lesson_reference": item.lesson_reference,
        "learner_answer": item.learner_answer or None,
        "correct_answer": item.correct_answer or None,
        "option_breakdown": [dict(option) for option in item.option_breakdown] or None,
        "question_score": item.question_score,
        "maximum_marks": item.maximum_marks,
        "deduction": item.deduction,
        "outcome": item.outcome,
        "answered": item.answered,
    }
