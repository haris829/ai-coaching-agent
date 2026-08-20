"""The company database behind UC-09's three repository protocols.

``in_memory.py`` is still the standalone binding and still what the formal-assessment tests run
against; this is what the merged application binds instead. Neither the services nor the domain
know which one they were given.

**Every update is a compare-and-set.** The domain hands back a record whose ``version`` is already
incremented, and the write applies only where the stored version is one behind:

    UPDATE … SET …, version = :version WHERE id = :id AND version = :version - 1

Zero rows affected means somebody else wrote first, and that raises
:class:`ConcurrentModificationError` rather than overwriting them. This one condition is what makes
the duplicate-submission, duplicate-disconnect, duplicate-decision and duplicate-certificate races
resolve to a single winner. It is expressed as a ``WHERE`` clause and not as a read followed by a
write, because the gap between a read and a write *is* the race.

**Every uniqueness guarantee comes from an index**, never from a look-before-you-write: inserts
attempt the write and read the failure. See ``models.py`` for the four indexes and what each one
protects.

**No delete method exists here or in the protocols.** A rejected device session is kept, because
"which device tried to sit this assessment?" is exactly the question an integrity investigation
asks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.errors import PersistenceFailedError
from app.core.logging import get_logger
from app.core.time import parse_instant, to_iso
from app.modules.formal_assessment.domain.anomalies import FormalAnomaly
from app.modules.formal_assessment.domain.attempt import (
    ConditionsAcknowledgement,
    DisconnectRecord,
    FormalAttempt,
    FormalResult,
    IdentityConfirmation,
)
from app.modules.formal_assessment.domain.conditions import FormalConditionCode
from app.modules.formal_assessment.domain.device import DeviceDescriptor, DeviceSession
from app.modules.formal_assessment.domain.enums import (
    OPEN_FORMAL_STATES,
    AssessorDecision,
    DeviceSessionState,
    FormalAnomalyCode,
    FormalAttemptState,
    FormalSubmissionReason,
    QueuePublishState,
    ReviewState,
)
from app.modules.formal_assessment.domain.errors import (
    ConcurrentModificationError,
    DeviceSessionAlreadyHeldError,
    DuplicateFormalAttemptError,
    DuplicateReviewError,
    FormalAttemptNotFoundError,
    FormalReviewNotFoundError,
)
from app.modules.formal_assessment.domain.review import AssessorDecisionRecord, FormalReview
from app.modules.formal_assessment.models import (
    DeviceSessionRow,
    FormalAttemptRow,
    FormalReviewRow,
)

logger = get_logger(__name__)

_OPEN_STATE_VALUES = tuple(state.value for state in OPEN_FORMAL_STATES)


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _instant(value: str | None) -> datetime | None:
    return parse_instant(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return to_iso(value) if value else None


def _anomalies_to_json(anomalies: tuple[FormalAnomaly, ...]) -> list[dict[str, Any]] | None:
    return [anomaly.as_dict() for anomaly in anomalies] if anomalies else None


def _anomalies_from_json(raw: Any) -> tuple[FormalAnomaly, ...]:
    """Rebuild the anomaly list, dropping an entry this release cannot read.

    An anomaly is a note about something that already happened. A code a later release removed
    must not make a formal attempt unreadable — and therefore unreviewable, and therefore a
    certificate blocked forever — so an unparseable entry is logged and skipped.
    """
    if not isinstance(raw, list):
        return ()
    rebuilt: list[FormalAnomaly] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            rebuilt.append(
                FormalAnomaly(
                    code=FormalAnomalyCode(entry["code"]),
                    message=str(entry.get("message", "")),
                    detected_at=str(entry.get("detected_at") or entry.get("detectedAt") or ""),
                    details=dict(entry.get("details") or {}),
                )
            )
        except (KeyError, ValueError, TypeError):
            logger.warning("formal.unreadable_anomaly", extra={"entry": str(entry)[:200]})
    return tuple(rebuilt)


def _conditions_from_row(row: FormalAttemptRow) -> ConditionsAcknowledgement | None:
    if row.conditions_acknowledged_at is None or row.conditions_version is None:
        return None
    codes: list[FormalConditionCode] = []
    for raw in row.conditions_acknowledged_codes or []:
        try:
            codes.append(FormalConditionCode(raw))
        except ValueError:
            # A condition code this release no longer publishes. Dropped, which makes the stored
            # acknowledgement fail the completeness check and sends the learner back to re-read
            # the conditions — the safe direction, and the one FORMAL_CONDITIONS_VERSION exists
            # to make explicit.
            logger.warning("formal.unknown_condition_code", extra={"code": str(raw)[:64]})
    return ConditionsAcknowledgement(
        conditions_version=row.conditions_version,
        acknowledged_codes=tuple(codes),
        acknowledged_at=to_iso(row.conditions_acknowledged_at),
        user_agent=row.conditions_user_agent,
    )


def _identity_from_row(row: FormalAttemptRow) -> IdentityConfirmation | None:
    if row.identity_confirmed_at is None:
        return None
    return IdentityConfirmation(
        confirmed_at=to_iso(row.identity_confirmed_at),
        email_confirmed=bool(row.identity_email_confirmed),
        email_supplied=bool(row.identity_email_supplied),
        rejected_attempts=row.identity_rejected_attempts,
    )


def _result_from_row(row: FormalAttemptRow) -> FormalResult | None:
    if row.result_calculated_at is None or row.result_status is None:
        return None
    return FormalResult(
        result_status=row.result_status,
        passed=bool(row.result_passed),
        calculated_at=to_iso(row.result_calculated_at),
        percentage=row.result_percentage,
        pass_mark=row.result_pass_mark,
        total_marks=row.result_total_marks,
        maximum_marks=row.result_maximum_marks,
        score_status=row.result_score_status,
        result_id=row.result_id,
    )


def _disconnect_from_row(row: FormalAttemptRow) -> DisconnectRecord | None:
    if row.disconnect_detected_at is None:
        return None
    return DisconnectRecord(
        detected_at=to_iso(row.disconnect_detected_at),
        reported_by=row.disconnect_reported_by or "",
        last_seen_at=_iso(row.disconnect_last_seen_at),
        autosaved_at=_iso(row.disconnect_autosaved_at),
        answered_questions=row.disconnect_answered_questions,
        total_questions=row.disconnect_total_questions,
        reason=row.disconnect_reason,
    )


def to_domain_attempt(row: FormalAttemptRow) -> FormalAttempt:
    """One row as the immutable domain value the services work with.

    Public because UC-05's certificate gate adapter needs it: that adapter answers "may I issue?"
    by running UC-09's *own* gate function over UC-09's *own* record, rather than reimplementing
    the decision from raw columns. A second reading of these rows is a second gate.
    """
    return FormalAttempt(
        formal_attempt_id=row.id,
        learner_id=row.learner_id,
        course_id=row.course_id,
        quiz_id=row.quiz_id,
        state=FormalAttemptState(row.state),
        created_at=to_iso(row.created_at),
        updated_at=to_iso(row.updated_at),
        idempotency_key=row.idempotency_key,
        attempt_id=row.attempt_id,
        attempt_number=row.attempt_number,
        configuration_version_id=row.configuration_version_id,
        retake_of_attempt_id=row.retake_of_attempt_id,
        conditions=_conditions_from_row(row),
        identity=_identity_from_row(row),
        device_session_id=row.device_session_id,
        started_at=_iso(row.started_at),
        submitted_at=_iso(row.submitted_at),
        submission_reason=(
            FormalSubmissionReason(row.submission_reason) if row.submission_reason else None
        ),
        disconnect=_disconnect_from_row(row),
        auto_submit_started_at=_iso(row.auto_submit_started_at),
        result=_result_from_row(row),
        review_id=row.review_id,
        certificate_workflow_triggered_at=_iso(row.certificate_workflow_triggered_at),
        certificate_reference=row.certificate_reference,
        anomalies=_anomalies_from_json(row.anomalies),
        pending_identity_rejections=row.pending_identity_rejections,
        version=row.version,
    )


def _attempt_columns(attempt: FormalAttempt) -> dict[str, Any]:
    """The mutable half of a formal attempt, as column values.

    ``id``, ``learner_id``, ``course_id``, ``quiz_id`` and ``idempotency_key`` are absent: they
    identify the sitting, and a record that changed them would be a different sitting wearing the
    same id.
    """
    conditions = attempt.conditions
    identity = attempt.identity
    result = attempt.result
    disconnect = attempt.disconnect
    return {
        "state": attempt.state.value,
        "attempt_id": attempt.attempt_id,
        "attempt_number": attempt.attempt_number,
        "configuration_version_id": attempt.configuration_version_id,
        "retake_of_attempt_id": attempt.retake_of_attempt_id,
        "conditions_version": conditions.conditions_version if conditions else None,
        "conditions_acknowledged_codes": (
            [code.value for code in conditions.acknowledged_codes] if conditions else None
        ),
        "conditions_acknowledged_at": (
            _instant(conditions.acknowledged_at) if conditions else None
        ),
        "conditions_user_agent": conditions.user_agent if conditions else None,
        "identity_confirmed_at": _instant(identity.confirmed_at) if identity else None,
        "identity_email_confirmed": identity.email_confirmed if identity else None,
        "identity_email_supplied": identity.email_supplied if identity else None,
        "identity_rejected_attempts": identity.rejected_attempts if identity else 0,
        "pending_identity_rejections": attempt.pending_identity_rejections,
        "device_session_id": attempt.device_session_id,
        "started_at": _instant(attempt.started_at),
        "submitted_at": _instant(attempt.submitted_at),
        "submission_reason": (
            attempt.submission_reason.value if attempt.submission_reason else None
        ),
        "disconnect_detected_at": _instant(disconnect.detected_at) if disconnect else None,
        "disconnect_reported_by": disconnect.reported_by if disconnect else None,
        "disconnect_last_seen_at": _instant(disconnect.last_seen_at) if disconnect else None,
        "disconnect_autosaved_at": _instant(disconnect.autosaved_at) if disconnect else None,
        "disconnect_answered_questions": disconnect.answered_questions if disconnect else None,
        "disconnect_total_questions": disconnect.total_questions if disconnect else None,
        "disconnect_reason": disconnect.reason if disconnect else None,
        "auto_submit_started_at": _instant(attempt.auto_submit_started_at),
        "result_status": result.result_status if result else None,
        "result_passed": result.passed if result else None,
        "result_percentage": result.percentage if result else None,
        "result_pass_mark": result.pass_mark if result else None,
        "result_total_marks": result.total_marks if result else None,
        "result_maximum_marks": result.maximum_marks if result else None,
        "result_score_status": result.score_status if result else None,
        "result_id": result.result_id if result else None,
        "result_calculated_at": _instant(result.calculated_at) if result else None,
        "review_id": attempt.review_id,
        "certificate_workflow_triggered_at": _instant(
            attempt.certificate_workflow_triggered_at
        ),
        "certificate_reference": attempt.certificate_reference,
        "anomalies": _anomalies_to_json(attempt.anomalies),
        "updated_at": _instant(attempt.updated_at),
        "version": attempt.version,
    }


def _to_session(row: DeviceSessionRow) -> DeviceSession:
    return DeviceSession(
        session_id=row.id,
        formal_attempt_id=row.formal_attempt_id,
        learner_id=row.learner_id,
        state=DeviceSessionState(row.state),
        registered_at=to_iso(row.registered_at),
        session_token=row.session_token,
        device=DeviceDescriptor(
            fingerprint=row.device_fingerprint,
            user_agent=row.device_user_agent,
            ip_address=row.device_ip_address,
            platform=row.device_platform,
        ),
        client_request_id=row.client_request_id,
        last_seen_at=_iso(row.last_seen_at),
        closed_at=_iso(row.closed_at),
        closed_reason=row.closed_reason,
        superseded_by_session_id=row.superseded_by_session_id,
        version=row.version,
    )


def _session_row(session: DeviceSession) -> DeviceSessionRow:
    return DeviceSessionRow(
        id=session.session_id,
        formal_attempt_id=session.formal_attempt_id,
        learner_id=session.learner_id,
        state=session.state.value,
        session_token=session.session_token,
        registered_at=parse_instant(session.registered_at),
        last_seen_at=_instant(session.last_seen_at),
        closed_at=_instant(session.closed_at),
        closed_reason=session.closed_reason,
        superseded_by_session_id=session.superseded_by_session_id,
        client_request_id=session.client_request_id,
        device_fingerprint=session.device.fingerprint,
        device_user_agent=session.device.user_agent,
        device_ip_address=session.device.ip_address,
        device_platform=session.device.platform,
        version=session.version,
    )


def _to_review(row: FormalReviewRow) -> FormalReview:
    decision = None
    if row.decision is not None and row.decided_by is not None and row.decided_at is not None:
        decision = AssessorDecisionRecord(
            decision=AssessorDecision(row.decision),
            decided_by=row.decided_by,
            decided_at=to_iso(row.decided_at),
            notes=row.decision_notes,
        )
    return FormalReview(
        review_id=row.id,
        formal_attempt_id=row.formal_attempt_id,
        learner_id=row.learner_id,
        course_id=row.course_id,
        quiz_id=row.quiz_id,
        attempt_id=row.attempt_id,
        state=ReviewState(row.state),
        created_at=to_iso(row.created_at),
        updated_at=to_iso(row.updated_at),
        percentage=row.percentage,
        submitted_at=_iso(row.submitted_at),
        auto_submitted=bool(row.auto_submitted),
        anomaly_count=row.anomaly_count,
        assigned_to=row.assigned_to,
        review_started_at=_iso(row.review_started_at),
        decision=decision,
        publish_state=QueuePublishState(row.publish_state),
        publish_attempts=row.publish_attempts,
        published_at=_iso(row.published_at),
        last_publish_error=row.last_publish_error,
        last_publish_attempt_at=_iso(row.last_publish_attempt_at),
        version=row.version,
    )


def _review_columns(review: FormalReview) -> dict[str, Any]:
    decision = review.decision
    return {
        "state": review.state.value,
        "percentage": review.percentage,
        "submitted_at": _instant(review.submitted_at),
        "auto_submitted": review.auto_submitted,
        "anomaly_count": review.anomaly_count,
        "assigned_to": review.assigned_to,
        "review_started_at": _instant(review.review_started_at),
        "decision": decision.decision.value if decision else None,
        "decided_by": decision.decided_by if decision else None,
        "decided_at": _instant(decision.decided_at) if decision else None,
        "decision_notes": decision.notes if decision else None,
        "publish_state": review.publish_state.value,
        "publish_attempts": review.publish_attempts,
        "published_at": _instant(review.published_at),
        "last_publish_error": review.last_publish_error,
        "last_publish_attempt_at": _instant(review.last_publish_attempt_at),
        "updated_at": _instant(review.updated_at),
        "version": review.version,
    }


# ---------------------------------------------------------------------------
# Formal attempts
# ---------------------------------------------------------------------------


class SqlAlchemyFormalAttemptRepository:
    """``FormalAttemptRepository`` over ``qs_formal_attempts``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, formal_attempt_id: str) -> FormalAttempt | None:
        return await offload(self._get, formal_attempt_id)

    async def get_for_learner(
        self, learner_id: str, formal_attempt_id: str
    ) -> FormalAttempt | None:
        return await offload(self._get_for_learner, learner_id, formal_attempt_id)

    async def get_by_attempt_id(self, attempt_id: str) -> FormalAttempt | None:
        return await offload(self._get_by_attempt_id, attempt_id)

    async def find_open_for_quiz(self, learner_id: str, quiz_id: str) -> FormalAttempt | None:
        return await offload(self._find_open_for_quiz, learner_id, quiz_id)

    async def list_in_progress_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        return await offload(self._list_in_progress_for_learner, learner_id)

    async def list_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        return await offload(self._list_for_learner, learner_id)

    async def insert(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        return await offload(self._insert, formal_attempt)

    async def save(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        return await offload(self._save, formal_attempt)

    # ---- synchronous bodies ------------------------------------------------

    def _get(self, formal_attempt_id: str) -> FormalAttempt | None:
        try:
            row = self._session.get(FormalAttemptRow, formal_attempt_id)
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_attempts.get", exc) from exc
        return to_domain_attempt(row) if row else None

    def _get_for_learner(
        self, learner_id: str, formal_attempt_id: str
    ) -> FormalAttempt | None:
        # Ownership is a WHERE clause, not a check afterwards: a guessed id must return nothing
        # rather than another learner's assessment.
        try:
            row = self._session.scalar(
                select(FormalAttemptRow).where(
                    FormalAttemptRow.id == formal_attempt_id,
                    FormalAttemptRow.learner_id == learner_id,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_attempts.get_for_learner", exc) from exc
        return to_domain_attempt(row) if row else None

    def _get_by_attempt_id(self, attempt_id: str) -> FormalAttempt | None:
        try:
            row = self._session.scalar(
                select(FormalAttemptRow).where(FormalAttemptRow.attempt_id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_attempts.get_by_attempt_id", exc) from exc
        return to_domain_attempt(row) if row else None

    def _find_open_for_quiz(self, learner_id: str, quiz_id: str) -> FormalAttempt | None:
        try:
            row = self._session.scalar(
                select(FormalAttemptRow).where(
                    FormalAttemptRow.learner_id == learner_id,
                    FormalAttemptRow.quiz_id == quiz_id,
                    FormalAttemptRow.state.in_(_OPEN_STATE_VALUES),
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_attempts.find_open_for_quiz", exc) from exc
        return to_domain_attempt(row) if row else None

    def _list_in_progress_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        # The read behind the AI-coaching restriction, on every coaching request: it must be
        # learner-wide and not attempt-scoped, because the case that matters is an exam in one
        # tab and a coach in another.
        try:
            rows = self._session.scalars(
                select(FormalAttemptRow)
                .where(
                    FormalAttemptRow.learner_id == learner_id,
                    FormalAttemptRow.state.in_(_OPEN_STATE_VALUES),
                )
                .order_by(FormalAttemptRow.created_at)
            ).all()
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_attempts.list_in_progress", exc) from exc
        return tuple(to_domain_attempt(row) for row in rows)

    def _list_for_learner(self, learner_id: str) -> tuple[FormalAttempt, ...]:
        try:
            rows = self._session.scalars(
                select(FormalAttemptRow)
                .where(FormalAttemptRow.learner_id == learner_id)
                .order_by(FormalAttemptRow.created_at)
            ).all()
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_attempts.list_for_learner", exc) from exc
        return tuple(to_domain_attempt(row) for row in rows)

    def _insert(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        row = FormalAttemptRow(
            id=formal_attempt.formal_attempt_id,
            learner_id=formal_attempt.learner_id,
            course_id=formal_attempt.course_id,
            quiz_id=formal_attempt.quiz_id,
            idempotency_key=formal_attempt.idempotency_key,
            created_at=parse_instant(formal_attempt.created_at),
            **_attempt_columns(formal_attempt),
        )
        try:
            self._session.add(row)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DuplicateFormalAttemptError(
                formal_attempt.learner_id, formal_attempt.quiz_id
            ) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("formal_attempts.insert", exc) from exc
        return to_domain_attempt(row)

    def _save(self, formal_attempt: FormalAttempt) -> FormalAttempt:
        """Compare-and-set. See the module docstring — this is the concurrency guarantee."""
        try:
            result = self._session.execute(
                update(FormalAttemptRow)
                .where(
                    FormalAttemptRow.id == formal_attempt.formal_attempt_id,
                    FormalAttemptRow.version == formal_attempt.version - 1,
                )
                .values(**_attempt_columns(formal_attempt))
            )
        except IntegrityError as exc:
            self._session.rollback()
            # The open-attempt or upstream-attempt index refused the new state.
            raise DuplicateFormalAttemptError(
                formal_attempt.learner_id, formal_attempt.quiz_id
            ) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("formal_attempts.save", exc) from exc

        if result.rowcount == 0:
            self._session.rollback()
            # Either the record vanished or somebody wrote first. Distinguished by a read, so a
            # caller is told which — a lost race is retryable, a missing record is not.
            existing = self._session.get(FormalAttemptRow, formal_attempt.formal_attempt_id)
            if existing is None:
                raise FormalAttemptNotFoundError(formal_attempt.formal_attempt_id)
            raise ConcurrentModificationError(
                "formal attempt",
                formal_attempt.formal_attempt_id,
                expected_version=formal_attempt.version - 1,
                actual_version=existing.version,
            )

        self._session.commit()
        saved = self._session.get(FormalAttemptRow, formal_attempt.formal_attempt_id)
        assert saved is not None  # noqa: S101 - the update above just matched it
        return to_domain_attempt(saved)


# ---------------------------------------------------------------------------
# Device sessions
# ---------------------------------------------------------------------------


class SqlAlchemyDeviceSessionRepository:
    """``DeviceSessionRepository`` over ``qs_formal_device_sessions``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, session_id: str) -> DeviceSession | None:
        return await offload(self._get, session_id)

    async def get_active(self, formal_attempt_id: str) -> DeviceSession | None:
        return await offload(self._get_active, formal_attempt_id)

    async def find_by_client_request_id(
        self, formal_attempt_id: str, client_request_id: str
    ) -> DeviceSession | None:
        return await offload(self._find_by_client_request_id, formal_attempt_id, client_request_id)

    async def list_for_attempt(self, formal_attempt_id: str) -> tuple[DeviceSession, ...]:
        return await offload(self._list_for_attempt, formal_attempt_id)

    async def claim(self, session: DeviceSession) -> DeviceSession:
        return await offload(self._claim, session)

    async def record_rejected(self, session: DeviceSession) -> DeviceSession:
        return await offload(self._record_rejected, session)

    async def save(self, session: DeviceSession) -> DeviceSession:
        return await offload(self._save, session)

    # ---- synchronous bodies ------------------------------------------------

    def _get(self, session_id: str) -> DeviceSession | None:
        try:
            row = self._session.get(DeviceSessionRow, session_id)
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("device_sessions.get", exc) from exc
        return _to_session(row) if row else None

    def _get_active(self, formal_attempt_id: str) -> DeviceSession | None:
        try:
            row = self._session.scalar(
                select(DeviceSessionRow).where(
                    DeviceSessionRow.formal_attempt_id == formal_attempt_id,
                    DeviceSessionRow.state == DeviceSessionState.ACTIVE.value,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("device_sessions.get_active", exc) from exc
        return _to_session(row) if row else None

    def _find_by_client_request_id(
        self, formal_attempt_id: str, client_request_id: str
    ) -> DeviceSession | None:
        try:
            row = self._session.scalar(
                select(DeviceSessionRow).where(
                    DeviceSessionRow.formal_attempt_id == formal_attempt_id,
                    DeviceSessionRow.client_request_id == client_request_id,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("device_sessions.find_by_client_request", exc) from exc
        return _to_session(row) if row else None

    def _list_for_attempt(self, formal_attempt_id: str) -> tuple[DeviceSession, ...]:
        # Every session, including rejected ones: "which device tried to sit this?" is the
        # question an integrity investigation asks, and nothing here is ever deleted.
        try:
            rows = self._session.scalars(
                select(DeviceSessionRow)
                .where(DeviceSessionRow.formal_attempt_id == formal_attempt_id)
                .order_by(DeviceSessionRow.registered_at, DeviceSessionRow.id)
            ).all()
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("device_sessions.list_for_attempt", exc) from exc
        return tuple(_to_session(row) for row in rows)

    def _claim(self, session: DeviceSession) -> DeviceSession:
        """Take the single-device lock by inserting. The insert *is* the check."""
        row = _session_row(session)
        try:
            self._session.add(row)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DeviceSessionAlreadyHeldError(session.formal_attempt_id) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("device_sessions.claim", exc) from exc
        return _to_session(row)

    def _record_rejected(self, session: DeviceSession) -> DeviceSession:
        """Store a refused registration. Never raises on the uniqueness constraint.

        A rejected session is evidence, not a claim: it is REJECTED, so the partial ACTIVE index
        does not apply to it, and recording it must not fail just because a legitimate session
        already holds the lock. Losing this row would lose the record of the second device.
        """
        row = _session_row(session)
        try:
            self._session.add(row)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            logger.warning(
                "formal.rejected_session_not_recorded",
                extra={"formal_attempt_id": session.formal_attempt_id, "cause": str(exc)[:200]},
            )
            return session
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("device_sessions.record_rejected", exc) from exc
        return _to_session(row)

    def _save(self, session: DeviceSession) -> DeviceSession:
        try:
            result = self._session.execute(
                update(DeviceSessionRow)
                .where(
                    DeviceSessionRow.id == session.session_id,
                    DeviceSessionRow.version == session.version - 1,
                )
                .values(
                    state=session.state.value,
                    last_seen_at=_instant(session.last_seen_at),
                    closed_at=_instant(session.closed_at),
                    closed_reason=session.closed_reason,
                    superseded_by_session_id=session.superseded_by_session_id,
                    version=session.version,
                )
            )
        except IntegrityError as exc:
            self._session.rollback()
            raise DeviceSessionAlreadyHeldError(session.formal_attempt_id) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("device_sessions.save", exc) from exc

        if result.rowcount == 0:
            self._session.rollback()
            existing = self._session.get(DeviceSessionRow, session.session_id)
            raise ConcurrentModificationError(
                "device session",
                session.session_id,
                expected_version=session.version - 1,
                actual_version=existing.version if existing else None,
            )

        self._session.commit()
        saved = self._session.get(DeviceSessionRow, session.session_id)
        assert saved is not None  # noqa: S101
        return _to_session(saved)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


class SqlAlchemyFormalReviewRepository:
    """``FormalReviewRepository`` over ``qs_formal_reviews``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, review_id: str) -> FormalReview | None:
        return await offload(self._get, review_id)

    async def get_by_formal_attempt(self, formal_attempt_id: str) -> FormalReview | None:
        return await offload(self._get_by_formal_attempt, formal_attempt_id)

    async def insert(self, review: FormalReview) -> FormalReview:
        return await offload(self._insert, review)

    async def save(self, review: FormalReview) -> FormalReview:
        return await offload(self._save, review)

    async def list_pending(
        self,
        *,
        course_ids: tuple[str, ...] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[FormalReview, ...]:
        return await offload(self._list_pending, course_ids, limit, offset)

    async def count_pending(self, *, course_ids: tuple[str, ...] | None = None) -> int:
        return await offload(self._count_pending, course_ids)

    async def list_unpublished(self, *, limit: int = 100) -> tuple[FormalReview, ...]:
        return await offload(self._list_unpublished, limit)

    # ---- synchronous bodies ------------------------------------------------

    def _get(self, review_id: str) -> FormalReview | None:
        try:
            row = self._session.get(FormalReviewRow, review_id)
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_reviews.get", exc) from exc
        return _to_review(row) if row else None

    def _get_by_formal_attempt(self, formal_attempt_id: str) -> FormalReview | None:
        try:
            row = self._session.scalar(
                select(FormalReviewRow).where(
                    FormalReviewRow.formal_attempt_id == formal_attempt_id
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_reviews.get_by_formal_attempt", exc) from exc
        return _to_review(row) if row else None

    def _insert(self, review: FormalReview) -> FormalReview:
        row = FormalReviewRow(
            id=review.review_id,
            formal_attempt_id=review.formal_attempt_id,
            learner_id=review.learner_id,
            course_id=review.course_id,
            quiz_id=review.quiz_id,
            attempt_id=review.attempt_id,
            created_at=parse_instant(review.created_at),
            **_review_columns(review),
        )
        try:
            self._session.add(row)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DuplicateReviewError(review.formal_attempt_id) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("formal_reviews.insert", exc) from exc
        return _to_review(row)

    def _save(self, review: FormalReview) -> FormalReview:
        try:
            result = self._session.execute(
                update(FormalReviewRow)
                .where(
                    FormalReviewRow.id == review.review_id,
                    FormalReviewRow.version == review.version - 1,
                )
                .values(**_review_columns(review))
            )
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("formal_reviews.save", exc) from exc

        if result.rowcount == 0:
            self._session.rollback()
            existing = self._session.get(FormalReviewRow, review.review_id)
            if existing is None:
                raise FormalReviewNotFoundError(review.review_id)
            # Two assessors deciding at once: one wins, the other is told the record moved.
            raise ConcurrentModificationError(
                "formal review",
                review.review_id,
                expected_version=review.version - 1,
                actual_version=existing.version,
            )

        self._session.commit()
        saved = self._session.get(FormalReviewRow, review.review_id)
        assert saved is not None  # noqa: S101
        return _to_review(saved)

    def _pending_query(self, course_ids: tuple[str, ...] | None):
        query = select(FormalReviewRow).where(
            FormalReviewRow.state.in_(
                (ReviewState.PENDING_REVIEW.value, ReviewState.IN_REVIEW.value)
            )
        )
        if course_ids is not None:
            # An empty scope returns nothing. An assessor authorised for no courses has an empty
            # queue, not the whole queue — the difference between the two is the whole point of
            # authorising them per course.
            query = query.where(FormalReviewRow.course_id.in_(course_ids))
        return query

    def _list_pending(
        self, course_ids: tuple[str, ...] | None, limit: int, offset: int
    ) -> tuple[FormalReview, ...]:
        try:
            rows = self._session.scalars(
                self._pending_query(course_ids)
                # Oldest first: a queue that surfaced the newest pass first would let an old
                # assessment wait indefinitely.
                .order_by(FormalReviewRow.created_at, FormalReviewRow.id)
                .limit(limit)
                .offset(offset)
            ).all()
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_reviews.list_pending", exc) from exc
        return tuple(_to_review(row) for row in rows)

    def _count_pending(self, course_ids: tuple[str, ...] | None) -> int:
        try:
            count = self._session.scalar(
                select(func.count()).select_from(self._pending_query(course_ids).subquery())
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_reviews.count_pending", exc) from exc
        return int(count or 0)

    def _list_unpublished(self, limit: int) -> tuple[FormalReview, ...]:
        # PENDING or FAILED publication. This read is what turns a queue outage into a work list
        # instead of a silent loss — the reviews themselves were persisted before the queue was
        # ever touched.
        try:
            rows = self._session.scalars(
                select(FormalReviewRow)
                .where(
                    FormalReviewRow.publish_state.in_(
                        (QueuePublishState.PENDING.value, QueuePublishState.FAILED.value)
                    )
                )
                .order_by(FormalReviewRow.created_at, FormalReviewRow.id)
                .limit(limit)
            ).all()
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("formal_reviews.list_unpublished", exc) from exc
        return tuple(_to_review(row) for row in rows)
