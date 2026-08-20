"""UC-04, UC-05, UC-06 and UC-07, behind UC-08's four read-only history ports.

One file, because they serve one purpose: assembling attempt history. None of them is consulted
when deciding whether a retake may happen — a learner's eligibility does not depend on their
score — and none of them has a write method to bind. Every query here is a ``select``.

**Nothing is recomputed.** UC-04's totals and percentage are carried through verbatim; UC-05's
verdict is carried through verbatim. UC-08 does no arithmetic on a mark anywhere, because a
percentage computed here that disagreed with UC-04's would be a second answer to a question that
already has one.

**A missing fact is reported missing.** An attempt whose score UC-04 has not confirmed appears in
the history with a labelled gap, never a fabricated zero — the same choice UC-06 makes for a
missing explanation, and for the same reason.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.errors import ProviderUnavailableError
from app.core.time import iso_or_none
from app.modules.certification.models import AttemptOutcome
from app.modules.coaching.models import CoachingSessionRow
from app.modules.feedback.models import FeedbackReportRow
from app.modules.retakes.integration.downstream import (
    AttemptScore,
    CoachingAvailability,
    FeedbackAvailability,
    PassFailResult,
    PassFailStatus,
)
from app.modules.scoring.models import AttemptResult


class RetakeScoringAdapter:
    """``ScoringResultProvider`` over UC-04's ``qr_attempt_results``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        return await offload(self._get_score, attempt_id)

    def _get_score(self, attempt_id: str) -> AttemptScore | None:
        try:
            row = self._session.scalar(
                select(AttemptResult).where(AttemptResult.attempt_id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise ProviderUnavailableError("uc04", cause=exc) from exc
        if row is None:
            return None

        # PENDING_SCORE carries zeros in its columns because the row exists before the run does.
        # Reporting those as a score would tell a learner they got nothing right; reporting the
        # row as unconfirmed with no figures is the truth.
        confirmed = row.status == "SCORED"
        return AttemptScore(
            attempt_id=attempt_id,
            confirmed=confirmed,
            total_marks=row.total_marks if confirmed else None,
            maximum_marks=row.maximum_marks if confirmed else None,
            percentage=row.percentage if confirmed else None,
            scored_at=iso_or_none(row.scored_at) if confirmed else None,
        )


class RetakePassFailAdapter:
    """``PassFailResultProvider`` over UC-05's ``qg_attempt_outcomes``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_result(self, attempt_id: str) -> PassFailResult | None:
        return await offload(self._get_result, attempt_id)

    def _get_result(self, attempt_id: str) -> PassFailResult | None:
        try:
            row = self._session.scalar(
                select(AttemptOutcome).where(AttemptOutcome.attempt_id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise ProviderUnavailableError("uc05", cause=exc) from exc
        if row is None:
            # UC-05 records an outcome only once a confirmed score exists, so "no row" means
            # "not determined yet" — which is ``None``, not PENDING. PENDING is UC-05's own
            # state for a determination it made and could not complete, and inventing it here
            # would put an attempt in a state UC-05 never assigned.
            return None

        return PassFailResult(
            attempt_id=attempt_id,
            status=PassFailStatus.PASSED if row.outcome == "PASS" else PassFailStatus.FAILED,
            pass_mark_percentage=row.pass_mark_percentage,
            determined_at=iso_or_none(row.determined_at),
        )


class RetakeFeedbackAdapter:
    """``FeedbackProvider`` over UC-06's ``qf_feedback_reports``.

    Availability only. The report itself never crosses this boundary: history needs to know
    whether there is something to link to, and pulling the question-level content here would put
    UC-06's feedback in a second place where it could go stale.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_feedback_availability(self, attempt_id: str) -> FeedbackAvailability | None:
        return await offload(self._get_availability, attempt_id)

    def _get_availability(self, attempt_id: str) -> FeedbackAvailability | None:
        try:
            row = self._session.scalar(
                select(FeedbackReportRow).where(FeedbackReportRow.attempt_id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise ProviderUnavailableError("uc06", cause=exc) from exc
        if row is None:
            return None
        return FeedbackAvailability(
            attempt_id=attempt_id,
            # PENDING and FAILED reports exist as rows but have nothing to show, so only a
            # GENERATED report counts as available.
            available=row.status == "GENERATED",
            status=row.status,
            generated_at=iso_or_none(row.generated_at),
        )


class RetakeCoachingAdapter:
    """``CoachingProvider`` over UC-07's ``qk_coaching_sessions``.

    Counts sessions; reads no message. A retake never copies, moves, closes or continues a
    coaching conversation — the previous attempt's coaching stays attached to the attempt it is
    about — so nothing here needs the transcript, and not reading it is the guarantee.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_coaching_availability(self, attempt_id: str) -> CoachingAvailability | None:
        return await offload(self._get_availability, attempt_id)

    def _get_availability(self, attempt_id: str) -> CoachingAvailability | None:
        try:
            total = self._session.scalar(
                select(func.count())
                .select_from(CoachingSessionRow)
                .where(CoachingSessionRow.attempt_id == attempt_id)
            )
            completed = self._session.scalar(
                select(func.count())
                .select_from(CoachingSessionRow)
                .where(
                    CoachingSessionRow.attempt_id == attempt_id,
                    CoachingSessionRow.status == "COMPLETED",
                )
            )
            # A report is what makes coaching offerable at all (UC-07 §7), so availability is
            # read from UC-06's gate rather than guessed from whether sessions happen to exist.
            report_status = self._session.scalar(
                select(FeedbackReportRow.status).where(
                    FeedbackReportRow.attempt_id == attempt_id
                )
            )
        except SQLAlchemyError as exc:
            raise ProviderUnavailableError("uc07", cause=exc) from exc

        return CoachingAvailability(
            attempt_id=attempt_id,
            available=report_status == "GENERATED",
            coachable_question_count=int(total or 0),
            completed_session_count=int(completed or 0),
        )
