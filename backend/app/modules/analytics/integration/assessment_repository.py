"""The real assessment data behind UC-10's ``AnalyticsRepository`` — read-only, and only reads.

UC-10 shipped with the assessment side as an abstract repository and an in-memory reference
implementation, because standalone it had no assessment data to read. This is that repository
implemented over the merged system's own rows:

    AttemptRecord      <- UC-03 ``qd_attempts``  + UC-04 ``qr_attempt_results``
                          + UC-05 ``qg_attempt_outcomes`` + the platform's ``qa_enrolments``
    ResponseRecord     <- UC-03 ``qd_attempt_answers`` + UC-04 ``qr_question_scores``
    QuestionMetadata   <- UC-02 ``qb_questions``

**Read-only is structural, not a promise.** Every method here issues a ``select``. There is no
``insert``, ``update`` or ``delete`` anywhere in this file, the protocol it implements declares
none, and ``tests/analytics/`` asserts that the class exposes no mutating method name. Analytics
cannot change an attempt because there is no code path by which it could.

**Nothing is recomputed.** The score is UC-04's, the pass/fail is UC-05's, the correctness is
UC-04's per-question outcome. UC-10 aggregates; it does not re-decide. A percentage computed here
that disagreed with UC-04's would be a second answer to a question that already has one.

**Filtering happens in the query, never after it.** UC-10's contract is explicit about this: the
service pages over what the provider returns and never post-filters, so a provider that filtered
in Python would silently make ``max_scanned_records`` and the query deadline meaningless. The
half-open date range, the cohort join and the assessment-type split are all ``WHERE`` clauses.

ON PER-QUESTION TIMING
----------------------
The company requirement asks for the average time spent per question, and nothing in the system
records it directly: UC-03 stores when an answer was *first saved*, not when the question was
first shown. Rather than leave the metric empty or invent a number, it is **derived** — see
:func:`_derive_time_spent`. That is a real server-side measurement with a stated limitation, not a
client-reported one, and the limitation is documented where the value is produced rather than in a
release note.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.logging import get_logger
from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.enums import (
    AssessmentType,
    AttemptStatus,
    FlagStatus,
)
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.domain.records import (
    AttemptRecord,
    Page,
    PageRequest,
    QuestionFlagRecord,
    QuestionMetadata,
    ResponseRecord,
)
from app.modules.analytics.errors import RepositoryUnavailableError
from app.modules.analytics.integration.question_types import (
    map_question_type,
    question_type_label,
)
from app.modules.analytics.repositories.base import AnalyticsRepository
from app.modules.analytics.repositories.sqlalchemy_review import flag_from_row
from app.modules.attempt_delivery.models import AttemptAnswer, QuizAttempt
from app.modules.certification.models import AttemptOutcome
from app.modules.identity.models import Enrolment
from app.modules.question_bank.models import Question
from app.modules.scoring.models import AttemptResult, QuestionScoreRow

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Cursors
# ---------------------------------------------------------------------------


def _encode_cursor(values: dict[str, Any]) -> str:
    """An opaque cursor over the last row's ordering key.

    Keyset, not offset: UC-10's contract requires that paging over a moving dataset cannot skip or
    repeat a record, and an ``OFFSET`` cannot promise that while attempts are still being
    submitted. The payload is base64 only so a client cannot be tempted to construct one.
    """
    return base64.urlsafe_b64encode(json.dumps(values).encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError) as exc:
        # A cursor this reader cannot parse is a client error, not a server one — but it is also
        # not worth failing a dashboard over, so the page restarts from the beginning.
        logger.warning("analytics.unreadable_cursor", extra={"cause": str(exc)[:120]})
        return None


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

#: UC-03's attempt statuses mapped onto UC-10's three. ``SUBMISSION_PENDING`` is committed but not
#: finished end to end, so it counts as in progress: the completion rate must not credit an attempt
#: whose downstream hand-off has not happened.
_STATUS_MAP = {
    "ACTIVE": AttemptStatus.IN_PROGRESS,
    "SUBMISSION_PENDING": AttemptStatus.IN_PROGRESS,
    "SUBMITTED": AttemptStatus.COMPLETED,
}


def _attempt_status(raw: str) -> AttemptStatus:
    return _STATUS_MAP.get(raw, AttemptStatus.IN_PROGRESS)


def _derive_time_spent(
    saved_at: datetime | None,
    previous_saved_at: datetime | None,
    attempt_started_at: datetime | None,
) -> float | None:
    """Seconds spent on one question, derived from UC-03's save timestamps.

    Measured as the interval between the learner arriving at the question and first saving an
    answer to it: for the first answered question, from the attempt's start; thereafter, from the
    previous answer's save instant.

    **What this is and is not.** It is a server-side measurement — no client reports it, so it
    cannot be inflated or spoofed. It is an *approximation* of dwell time: a learner who answers
    question 3, then goes back to question 1, then returns has that wandering attributed to
    whichever question they saved next. For the one-question-at-a-time delivery mode UC-01 offers
    it is close to exact; for all-at-once it is a lower bound on the session and an approximation
    per question.

    Recording exact per-question dwell time would need UC-03 to store a view event per question,
    which is a change to the attempt lifecycle rather than to analytics. Until then this is the
    honest number, and ``None`` — not zero — is returned when there is nothing to measure from,
    so an unanswered question is excluded from the average rather than dragging it down.
    """
    if saved_at is None:
        return None
    anchor = previous_saved_at or attempt_started_at
    if anchor is None:
        return None
    elapsed = (saved_at - anchor).total_seconds()
    # A negative interval means the rows are out of order relative to the anchor — possible if a
    # clock moved. Excluded rather than clamped to zero: a fabricated zero would pull an average
    # down, and "we could not measure this" is the truth.
    return elapsed if elapsed >= 0 else None


def _selected_answer(score: QuestionScoreRow | None, answer: AttemptAnswer | None) -> str | None:
    """What the learner chose, as the label the "most common wrong answer" groups by.

    UC-04's ``learner_answer_display`` is preferred because it is the human-readable rendering the
    feedback report already shows — so the answer an administrator sees in analytics is the same
    string the learner saw. The raw payload is a fallback for an attempt scored before that column
    was populated.
    """
    if score is not None and score.learner_answer_display:
        return score.learner_answer_display
    if answer is not None and answer.response is not None:
        raw = answer.response
        if isinstance(raw, dict):
            for key in ("selectedOptionId", "value", "selectedOptionIds", "orderedItemIds"):
                if key in raw and raw[key] is not None:
                    value = raw[key]
                    return ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        return str(raw)[:200]
    return None


# ---------------------------------------------------------------------------
# The repository
# ---------------------------------------------------------------------------


class SqlAlchemyAnalyticsRepository(AnalyticsRepository):
    """``AnalyticsRepository`` over the merged system's assessment tables. Reads only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- the read-only assessment surface ----------------------------------

    async def count_attempts(self, filters: AnalyticsFilters, context: QueryContext) -> int:
        context.raise_if_stopped()
        return await offload(self._count_attempts, filters)

    async def fetch_attempts_page(
        self, filters: AnalyticsFilters, page: PageRequest, context: QueryContext
    ) -> Page[AttemptRecord]:
        context.raise_if_stopped()
        return await offload(self._fetch_attempts_page, filters, page)

    async def fetch_responses_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        context: QueryContext,
        *,
        question_ids: Sequence[str] | None = None,
    ) -> Page[ResponseRecord]:
        context.raise_if_stopped()
        return await offload(self._fetch_responses_page, filters, page, question_ids)

    async def fetch_question_metadata(
        self, question_ids: Sequence[str], context: QueryContext
    ) -> Mapping[str, QuestionMetadata]:
        context.raise_if_stopped()
        return await offload(self._fetch_question_metadata, question_ids)

    async def get_flags(
        self,
        question_ids: Sequence[str],
        context: QueryContext,
        *,
        statuses: Sequence[FlagStatus] | None = None,
    ) -> Mapping[str, QuestionFlagRecord]:
        context.raise_if_stopped()
        return await offload(self._get_flags, question_ids, statuses)

    async def get_flag(
        self, question_id: str, context: QueryContext
    ) -> QuestionFlagRecord | None:
        flags = await self.get_flags([question_id], context)
        return flags.get(question_id)

    async def health_check(self, context: QueryContext) -> bool:
        return await offload(self._health_check)

    # ---- query construction ------------------------------------------------

    def _attempt_filter(self, filters: AnalyticsFilters) -> list[Any]:
        """The filter, as ``WHERE`` clauses. Applied in the database, never in Python."""
        clauses: list[Any] = []

        if filters.course_id is not None:
            clauses.append(QuizAttempt.course_id == filters.course_id)

        if filters.assessment_type is not None:
            # The split the requirement asks for: a formal assessment is an attempt UC-09
            # supervised, recorded on UC-03's own row at creation.
            clauses.append(
                QuizAttempt.is_formal_assessment.is_(
                    filters.assessment_type is AssessmentType.FORMAL_ASSESSMENT
                )
            )

        # Half-open on the attempt's start, so consecutive periods tile exactly and a January
        # report and a February report can never double-count the same attempt.
        if filters.start_date is not None:
            clauses.append(QuizAttempt.started_at >= filters.start_date)
        if filters.end_date is not None:
            clauses.append(QuizAttempt.started_at < filters.end_date)

        if filters.cohort_id is not None:
            # The cohort lives on the platform's enrolment row — a grouping within a course — so
            # this is a correlated existence check rather than a join that could duplicate an
            # attempt for a learner enrolled twice.
            clauses.append(
                select(Enrolment.learner_id)
                .where(
                    Enrolment.learner_id == QuizAttempt.learner_id,
                    Enrolment.course_id == QuizAttempt.course_id,
                    Enrolment.cohort_id == filters.cohort_id,
                )
                .exists()
            )

        return clauses

    def _attempts_query(self, filters: AnalyticsFilters) -> Select[Any]:
        return select(QuizAttempt).where(*self._attempt_filter(filters))

    # ---- synchronous bodies ------------------------------------------------

    def _count_attempts(self, filters: AnalyticsFilters) -> int:
        try:
            count = self._session.scalar(
                select(func.count())
                .select_from(QuizAttempt)
                .where(*self._attempt_filter(filters))
            )
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "The assessment data could not be read.", cause=exc
            ) from exc
        return int(count or 0)

    def _fetch_attempts_page(
        self, filters: AnalyticsFilters, page: PageRequest
    ) -> Page[AttemptRecord]:
        cursor = _decode_cursor(page.cursor)
        query = (
            self._attempts_query(filters)
            # Ordered by an immutable pair — the start instant, then the id as a tie-break — so the
            # keyset cursor below is stable even when two attempts start in the same millisecond.
            .order_by(QuizAttempt.started_at, QuizAttempt.id)
            .limit(page.limit + 1)
        )
        if cursor:
            anchor = datetime.fromisoformat(cursor["started_at"])
            query = query.where(
                or_(
                    QuizAttempt.started_at > anchor,
                    and_(QuizAttempt.started_at == anchor, QuizAttempt.id > cursor["id"]),
                )
            )

        try:
            rows = list(self._session.scalars(query).all())
            attempt_ids = [row.id for row in rows[: page.limit]]
            results = {
                row.attempt_id: row
                for row in self._session.scalars(
                    select(AttemptResult).where(AttemptResult.attempt_id.in_(attempt_ids))
                ).all()
            }
            outcomes = {
                row.attempt_id: row
                for row in self._session.scalars(
                    select(AttemptOutcome).where(AttemptOutcome.attempt_id.in_(attempt_ids))
                ).all()
            }
            cohorts = {
                (row.learner_id, row.course_id): row.cohort_id
                for row in self._session.scalars(
                    select(Enrolment).where(
                        Enrolment.learner_id.in_({row.learner_id for row in rows[: page.limit]})
                    )
                ).all()
            }
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "The assessment data could not be read.", cause=exc
            ) from exc

        has_more = len(rows) > page.limit
        items: list[AttemptRecord] = []
        for row in rows[: page.limit]:
            result = results.get(row.id)
            outcome = outcomes.get(row.id)
            items.append(
                AttemptRecord(
                    attempt_id=row.id,
                    course_id=row.course_id,
                    learner_id=row.learner_id,
                    cohort_id=cohorts.get((row.learner_id, row.course_id)),
                    assessment_type=(
                        AssessmentType.FORMAL_ASSESSMENT
                        if row.is_formal_assessment
                        else AssessmentType.STANDARD_QUIZ
                    ),
                    status=_attempt_status(row.status),
                    started_at=row.started_at,
                    completed_at=row.submitted_at,
                    # UC-04's percentage, carried through. Only a confirmed score counts: a
                    # PENDING_SCORE row holds zeros because it exists before the run does, and
                    # reporting those as a score would tell an administrator the cohort failed.
                    score=(
                        result.percentage
                        if result is not None and result.status == "SCORED"
                        else None
                    ),
                    # UC-05's verdict, carried through. ``None`` when undetermined, which excludes
                    # the attempt from the pass-rate denominator rather than counting it as a fail.
                    passed=(None if outcome is None else outcome.outcome == "PASS"),
                )
            )

        last = rows[page.limit - 1] if has_more and rows else None
        return Page(
            items=tuple(items),
            next_cursor=(
                _encode_cursor({"started_at": last.started_at.isoformat(), "id": last.id})
                if last is not None
                else None
            ),
        )

    def _fetch_responses_page(
        self,
        filters: AnalyticsFilters,
        page: PageRequest,
        question_ids: Sequence[str] | None,
    ) -> Page[ResponseRecord]:
        cursor = _decode_cursor(page.cursor)
        query = (
            select(AttemptAnswer, QuizAttempt.started_at)
            .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.attempt_id)
            .where(*self._attempt_filter(filters))
            .order_by(AttemptAnswer.attempt_id, AttemptAnswer.saved_at, AttemptAnswer.id)
            .limit(page.limit + 1)
        )
        if question_ids is not None:
            # Narrowed in the query, as the contract requires: single-question analytics must not
            # scan the whole response set.
            query = query.where(AttemptAnswer.question_id.in_(list(question_ids)))
        if cursor:
            query = query.where(AttemptAnswer.id > cursor["id"])

        try:
            rows = list(self._session.execute(query).all())
            answer_ids = [row[0].id for row in rows[: page.limit]]
            attempt_ids = {row[0].attempt_id for row in rows[: page.limit]}
            scores = {
                (row.question_id, row.result_id): row
                for row in self._session.scalars(
                    select(QuestionScoreRow).where(
                        QuestionScoreRow.question_id.in_(
                            {row[0].question_id for row in rows[: page.limit]}
                        )
                    )
                ).all()
            }
            # Scores hang off UC-04's result, not the attempt, so the result id is the bridge.
            result_by_attempt = {
                row.attempt_id: row.id
                for row in self._session.scalars(
                    select(AttemptResult).where(AttemptResult.attempt_id.in_(attempt_ids))
                ).all()
            }
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "The assessment data could not be read.", cause=exc
            ) from exc

        has_more = len(rows) > page.limit
        items: list[ResponseRecord] = []
        previous_saved: dict[str, datetime | None] = {}
        for answer, attempt_started_at in rows[: page.limit]:
            score = scores.get((answer.question_id, result_by_attempt.get(answer.attempt_id)))
            previous = previous_saved.get(answer.attempt_id)
            items.append(
                ResponseRecord(
                    response_id=answer.id,
                    attempt_id=answer.attempt_id,
                    question_id=answer.question_id,
                    selected_answer=_selected_answer(score, answer),
                    # UC-04's outcome, never recomputed. ``None`` when the attempt has not been
                    # scored, which excludes the response from the accuracy denominator.
                    is_correct=(None if score is None else score.outcome == "CORRECT"),
                    time_spent_seconds=_derive_time_spent(
                        answer.saved_at, previous, attempt_started_at
                    ),
                    answered_at=answer.saved_at if answer.answered else None,
                )
            )
            previous_saved[answer.attempt_id] = answer.saved_at

        del answer_ids  # the cursor uses the last row directly
        last = rows[page.limit - 1][0] if has_more and rows else None
        return Page(
            items=tuple(items),
            next_cursor=_encode_cursor({"id": last.id}) if last is not None else None,
        )

    def _fetch_question_metadata(
        self, question_ids: Sequence[str]
    ) -> Mapping[str, QuestionMetadata]:
        if not question_ids:
            return {}
        try:
            rows = self._session.scalars(
                select(Question).where(Question.id.in_(list(question_ids)))
            ).all()
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "The question catalogue could not be read.", cause=exc
            ) from exc

        # Missing ids are omitted, not raised: a question can be retired or removed while its
        # historical responses remain, and analytics must still report on those responses.
        metadata: dict[str, QuestionMetadata] = {}
        for row in rows:
            metadata[row.id] = QuestionMetadata(
                question_id=row.id,
                question_type=map_question_type(row.type),
                question_type_label=question_type_label(row.type),
                # The question bank is global in this system — a question is not owned by a course;
                # the *quiz* that draws it belongs to one. Reporting a course here would mean
                # inventing one, so it is left unset and course scoping happens on the attempt,
                # which is where the course genuinely lives.
                course_id=None,
                text=row.question_text,
                # UC-02's own status. A retired question stays reportable: its historical responses
                # are still real, and "we retired this because analytics flagged it" is a story the
                # dashboard has to be able to tell after the fact.
                active=row.status == "ACTIVE",
            )
        return metadata

    def _get_flags(
        self, question_ids: Sequence[str], statuses: Sequence[FlagStatus] | None
    ) -> Mapping[str, QuestionFlagRecord]:
        from app.modules.analytics.models import QuestionFlagRow

        if not question_ids:
            return {}
        query = select(QuestionFlagRow).where(
            QuestionFlagRow.question_id.in_(list(question_ids))
        )
        if statuses is not None:
            query = query.where(
                QuestionFlagRow.status.in_([status.value for status in statuses])
            )
        try:
            rows = self._session.scalars(query).all()
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "The review store could not be read.", cause=exc
            ) from exc
        return {row.question_id: flag_from_row(row) for row in rows}

    def _health_check(self) -> bool:
        try:
            self._session.execute(select(func.count()).select_from(QuizAttempt).limit(1))
        except SQLAlchemyError:
            return False
        return True


def _no_mutating_methods() -> None:
    """Assert at import time that nothing mutating has crept onto the repository.

    ``tests/analytics/`` checks this too, but a class this important should refuse to load with a
    ``save`` on it rather than wait for a test run. UC-10's whole read-only claim is that no such
    method exists.
    """
    forbidden = ("insert", "update", "delete", "save", "upsert", "write", "record", "set_")
    offenders = [
        name
        for name in dir(SqlAlchemyAnalyticsRepository)
        if not name.startswith("_") and name.startswith(forbidden)
    ]
    if offenders:  # pragma: no cover - a guard against a future edit
        raise RuntimeError(
            f"SqlAlchemyAnalyticsRepository must stay read-only; found {offenders}"
        )


_no_mutating_methods()
