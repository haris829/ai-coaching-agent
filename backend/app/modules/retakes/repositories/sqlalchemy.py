"""The company database behind UC-08's two repository protocols.

``in_memory.py`` is still the standalone binding and still what the retake tests run against;
this is what the merged application binds instead. Neither the services nor the domain know
which one they were given — that is the whole point of the protocols, and it is why nothing in
either changed when this file was written.

**Uniqueness comes from the database, never from a check in here.** Every mutating method
attempts the write and reads the failure, rather than looking first and writing second: the
window between a look and a write is exactly the race a retake reservation exists to close. So
:meth:`SqlAlchemyRetakeRequestRepository.reserve` inserts and catches ``IntegrityError``, and
decides from the stored rows which of the two constraints was hit.

Every call runs through :func:`app.core.async_db.offload`, because the services are asynchronous
and the session is not — see that module for why.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.errors import PersistenceFailedError
from app.core.logging import get_logger
from app.core.time import parse_instant, to_iso
from app.modules.retakes.domain.anomalies import RetakeAnomaly
from app.modules.retakes.domain.enums import (
    AnomalySeverity,
    ConfigurationVersionSource,
    GrantStatus,
    RetakeAnomalyCode,
    RetakeRequestStatus,
)
from app.modules.retakes.domain.errors import (
    AttemptSlotTakenError,
    DuplicateGrantError,
    DuplicateRetakeRequestError,
    GrantNotFoundError,
    RetakeRequestNotFoundError,
)
from app.modules.retakes.domain.grants import AdditionalAttemptGrant
from app.modules.retakes.domain.requests import RetakeRequest
from app.modules.retakes.models import AdditionalAttemptGrantRow, RetakeRequestRow

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _instant(value: str | None) -> datetime | None:
    return parse_instant(value) if value else None


def _anomalies_to_json(anomalies: tuple[RetakeAnomaly, ...]) -> list[dict[str, Any]] | None:
    return [anomaly.as_dict() for anomaly in anomalies] if anomalies else None


def _anomalies_from_json(raw: Any) -> tuple[RetakeAnomaly, ...]:
    """Rebuild the anomaly list, tolerating a shape an older release wrote.

    A stored anomaly is a record of something already noted, so a row this reader cannot parse
    must not take a learner's retake history down with it — the unreadable entry is dropped and
    the rest of the record is served.
    """
    if not isinstance(raw, list):
        return ()
    rebuilt: list[RetakeAnomaly] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            rebuilt.append(
                RetakeAnomaly(
                    code=RetakeAnomalyCode(entry["code"]),
                    severity=AnomalySeverity(entry.get("severity", AnomalySeverity.WARNING)),
                    message=str(entry.get("message", "")),
                    details=dict(entry.get("details") or {}),
                )
            )
        except (KeyError, ValueError):
            # A code this release no longer knows. Dropped rather than raised: the anomaly is a
            # note about something that already happened, and it must not take a learner's
            # retake history down with it.
            logger.warning("retakes.unreadable_anomaly", extra={"entry": str(entry)[:200]})
    return tuple(rebuilt)


def _to_request(row: RetakeRequestRow) -> RetakeRequest:
    return RetakeRequest(
        retake_id=row.id,
        idempotency_key=row.idempotency_key,
        learner_id=row.learner_id,
        course_id=row.course_id,
        quiz_id=row.quiz_id,
        previous_attempt_id=row.previous_attempt_id,
        attempt_number=row.attempt_number,
        configuration_version_id=row.configuration_version_id,
        configuration_version_number=row.configuration_version_number,
        configuration_version_source=ConfigurationVersionSource(
            row.configuration_version_source
        ),
        status=RetakeRequestStatus(row.status),
        attempt_id=row.attempt_id,
        requested_at=to_iso(row.requested_at),
        updated_at=to_iso(row.updated_at),
        completed_at=to_iso(row.completed_at) if row.completed_at else None,
        question_plan=row.question_plan,
        question_set_difference=row.question_set_difference,
        anomalies=_anomalies_from_json(row.anomalies),
        failure_code=row.failure_code,
        failure_message=row.failure_message,
        attempt_count=row.attempt_count,
    )


def _apply_request(row: RetakeRequestRow, request: RetakeRequest) -> None:
    """Copy the mutable half of a request onto its row.

    ``retake_id``, ``idempotency_key`` and ``previous_attempt_id`` are deliberately absent: the
    protocol forbids them changing, because a request that moved to a different previous attempt
    would be a different retake wearing the same id.
    """
    row.attempt_number = request.attempt_number
    row.configuration_version_id = request.configuration_version_id
    row.configuration_version_number = request.configuration_version_number
    row.configuration_version_source = request.configuration_version_source.value
    row.status = request.status.value
    row.attempt_id = request.attempt_id
    row.updated_at = parse_instant(request.updated_at)
    row.completed_at = _instant(request.completed_at)
    row.question_plan = request.question_plan
    row.question_set_difference = request.question_set_difference
    row.anomalies = _anomalies_to_json(request.anomalies)
    row.failure_code = request.failure_code
    row.failure_message = request.failure_message
    row.attempt_count = request.attempt_count


def _to_grant(row: AdditionalAttemptGrantRow) -> AdditionalAttemptGrant:
    return AdditionalAttemptGrant(
        grant_id=row.id,
        learner_id=row.learner_id,
        course_id=row.course_id,
        quiz_id=row.quiz_id,
        additional_attempts=row.additional_attempts,
        granted_by=row.granted_by,
        idempotency_key=row.idempotency_key,
        granted_at=to_iso(row.granted_at),
        status=GrantStatus(row.status),
        reason=row.reason,
        revoked_at=to_iso(row.revoked_at) if row.revoked_at else None,
        revoked_by=row.revoked_by,
    )


# ---------------------------------------------------------------------------
# Retake requests
# ---------------------------------------------------------------------------


class SqlAlchemyRetakeRequestRepository:
    """``RetakeRequestRepository`` over ``qt_retake_requests``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, retake_id: str) -> RetakeRequest | None:
        return await offload(self._get, retake_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> RetakeRequest | None:
        return await offload(self._get_by_key, idempotency_key)

    async def get_for_learner(self, learner_id: str, retake_id: str) -> RetakeRequest | None:
        return await offload(self._get_for_learner, learner_id, retake_id)

    async def reserve(self, request: RetakeRequest) -> RetakeRequest:
        return await offload(self._reserve, request)

    async def save(self, request: RetakeRequest) -> RetakeRequest:
        return await offload(self._save, request)

    async def count_active_reservations(self, learner_id: str, quiz_id: str) -> int:
        return await offload(self._count_active_reservations, learner_id, quiz_id)

    async def list_for_learner_quiz(
        self, learner_id: str, quiz_id: str
    ) -> tuple[RetakeRequest, ...]:
        return await offload(self._list_for_learner_quiz, learner_id, quiz_id)

    # ---- synchronous bodies ------------------------------------------------

    def _row(self, retake_id: str) -> RetakeRequestRow | None:
        return self._session.get(RetakeRequestRow, retake_id)

    def _get(self, retake_id: str) -> RetakeRequest | None:
        try:
            row = self._row(retake_id)
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("retakes.get", exc) from exc
        return _to_request(row) if row else None

    def _get_by_key(self, idempotency_key: str) -> RetakeRequest | None:
        try:
            row = self._session.scalar(
                select(RetakeRequestRow).where(
                    RetakeRequestRow.idempotency_key == idempotency_key
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("retakes.get_by_idempotency_key", exc) from exc
        return _to_request(row) if row else None

    def _get_for_learner(self, learner_id: str, retake_id: str) -> RetakeRequest | None:
        # Ownership is a WHERE clause, not a check on the way out: a guessed retake id must
        # return nothing rather than someone else's record.
        try:
            row = self._session.scalar(
                select(RetakeRequestRow).where(
                    RetakeRequestRow.id == retake_id,
                    RetakeRequestRow.learner_id == learner_id,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("retakes.get_for_learner", exc) from exc
        return _to_request(row) if row else None

    def _reserve(self, request: RetakeRequest) -> RetakeRequest:
        row = RetakeRequestRow(
            id=request.retake_id,
            idempotency_key=request.idempotency_key,
            learner_id=request.learner_id,
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            previous_attempt_id=request.previous_attempt_id,
            attempt_number=request.attempt_number,
            configuration_version_id=request.configuration_version_id,
            configuration_version_number=request.configuration_version_number,
            configuration_version_source=request.configuration_version_source.value,
            status=request.status.value,
            attempt_id=request.attempt_id,
            requested_at=parse_instant(request.requested_at),
            updated_at=parse_instant(request.updated_at),
            completed_at=_instant(request.completed_at),
            question_plan=request.question_plan,
            question_set_difference=request.question_set_difference,
            anomalies=_anomalies_to_json(request.anomalies),
            failure_code=request.failure_code,
            failure_message=request.failure_message,
            attempt_count=request.attempt_count,
        )
        try:
            self._session.add(row)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise self._reservation_conflict(request, exc) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("retakes.reserve", exc) from exc
        return _to_request(row)

    def _reservation_conflict(self, request: RetakeRequest, exc: Exception) -> Exception:
        """Decide which of the two constraints rejected the insert.

        Read from the stored rows rather than parsed out of the driver's message, which differs
        between SQLite and PostgreSQL and is not a contract. The idempotency key is checked
        first because it is the more specific answer: a replayed request has both a duplicate
        key *and* a taken slot, and telling the caller "your retry found the existing retake"
        is more useful than "somebody has that slot".
        """
        try:
            duplicate = self._session.scalar(
                select(RetakeRequestRow.id).where(
                    RetakeRequestRow.idempotency_key == request.idempotency_key
                )
            )
            if duplicate is not None:
                return DuplicateRetakeRequestError(request.idempotency_key)
            holder = self._session.scalar(
                select(RetakeRequestRow.id).where(
                    RetakeRequestRow.learner_id == request.learner_id,
                    RetakeRequestRow.quiz_id == request.quiz_id,
                    RetakeRequestRow.attempt_number == request.attempt_number,
                    RetakeRequestRow.status != RetakeRequestStatus.FAILED.value,
                )
            )
            if holder is not None:
                return AttemptSlotTakenError(
                    request.learner_id, request.quiz_id, request.attempt_number
                )
        except SQLAlchemyError:  # pragma: no cover - the read after a rollback failing too
            logger.warning("retakes.reservation_conflict_unreadable")
        # A constraint fired that neither read explains — a CHECK, most likely. Reported as a
        # persistence fault rather than guessed at, because inventing a reason here would send
        # a client down a recovery path that does not apply.
        return PersistenceFailedError("retakes.reserve", exc)

    def _save(self, request: RetakeRequest) -> RetakeRequest:
        row = self._row(request.retake_id)
        if row is None:
            # ``save`` never creates. A record that vanished is a fault, not a new reservation.
            raise RetakeRequestNotFoundError(request.retake_id)

        _apply_request(row, request)
        try:
            self._session.commit()
        except IntegrityError as exc:
            # Reopening a FAILED request re-acquires the slot under the same partial index, so
            # a retry that lost a race to another request is refused here exactly as a fresh
            # reservation would have been.
            self._session.rollback()
            raise AttemptSlotTakenError(
                request.learner_id, request.quiz_id, request.attempt_number
            ) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("retakes.save", exc) from exc
        return _to_request(row)

    def _count_active_reservations(self, learner_id: str, quiz_id: str) -> int:
        try:
            count = self._session.scalar(
                select(func.count())
                .select_from(RetakeRequestRow)
                .where(
                    RetakeRequestRow.learner_id == learner_id,
                    RetakeRequestRow.quiz_id == quiz_id,
                    RetakeRequestRow.status == RetakeRequestStatus.RESERVED.value,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("retakes.count_active_reservations", exc) from exc
        return int(count or 0)

    def _list_for_learner_quiz(
        self, learner_id: str, quiz_id: str
    ) -> tuple[RetakeRequest, ...]:
        try:
            rows = self._session.scalars(
                select(RetakeRequestRow)
                .where(
                    RetakeRequestRow.learner_id == learner_id,
                    RetakeRequestRow.quiz_id == quiz_id,
                )
                .order_by(RetakeRequestRow.requested_at, RetakeRequestRow.id)
            ).all()
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("retakes.list_for_learner_quiz", exc) from exc
        return tuple(_to_request(row) for row in rows)


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


class SqlAlchemyGrantRepository:
    """``GrantRepository`` over ``qt_additional_attempt_grants``.

    No delete method, here or in the protocol. Revocation is a status transition.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get(self, grant_id: str) -> AdditionalAttemptGrant | None:
        return await offload(self._get, grant_id)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> AdditionalAttemptGrant | None:
        return await offload(self._get_by_key, idempotency_key)

    async def list_for_learner_quiz(
        self, learner_id: str, course_id: str, quiz_id: str
    ) -> tuple[AdditionalAttemptGrant, ...]:
        return await offload(self._list_for_learner_quiz, learner_id, course_id, quiz_id)

    async def insert(self, grant: AdditionalAttemptGrant) -> AdditionalAttemptGrant:
        return await offload(self._insert, grant)

    async def save(self, grant: AdditionalAttemptGrant) -> AdditionalAttemptGrant:
        return await offload(self._save, grant)

    # ---- synchronous bodies ------------------------------------------------

    def _get(self, grant_id: str) -> AdditionalAttemptGrant | None:
        try:
            row = self._session.get(AdditionalAttemptGrantRow, grant_id)
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("grants.get", exc) from exc
        return _to_grant(row) if row else None

    def _get_by_key(self, idempotency_key: str) -> AdditionalAttemptGrant | None:
        try:
            row = self._session.scalar(
                select(AdditionalAttemptGrantRow).where(
                    AdditionalAttemptGrantRow.idempotency_key == idempotency_key
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("grants.get_by_idempotency_key", exc) from exc
        return _to_grant(row) if row else None

    def _list_for_learner_quiz(
        self, learner_id: str, course_id: str, quiz_id: str
    ) -> tuple[AdditionalAttemptGrant, ...]:
        # All three ids in the WHERE clause: this read decides an entitlement, and one that
        # dropped the course or the quiz would confer an attempt nobody granted.
        try:
            rows = self._session.scalars(
                select(AdditionalAttemptGrantRow)
                .where(
                    AdditionalAttemptGrantRow.learner_id == learner_id,
                    AdditionalAttemptGrantRow.course_id == course_id,
                    AdditionalAttemptGrantRow.quiz_id == quiz_id,
                )
                .order_by(AdditionalAttemptGrantRow.granted_at, AdditionalAttemptGrantRow.id)
            ).all()
        except SQLAlchemyError as exc:
            raise PersistenceFailedError("grants.list_for_learner_quiz", exc) from exc
        return tuple(_to_grant(row) for row in rows)

    def _insert(self, grant: AdditionalAttemptGrant) -> AdditionalAttemptGrant:
        row = AdditionalAttemptGrantRow(
            id=grant.grant_id,
            idempotency_key=grant.idempotency_key,
            learner_id=grant.learner_id,
            course_id=grant.course_id,
            quiz_id=grant.quiz_id,
            additional_attempts=grant.additional_attempts,
            granted_by=grant.granted_by,
            granted_at=parse_instant(grant.granted_at),
            status=grant.status.value,
            reason=grant.reason,
            revoked_at=_instant(grant.revoked_at),
            revoked_by=grant.revoked_by,
        )
        try:
            self._session.add(row)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DuplicateGrantError(grant.idempotency_key) from exc
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("grants.insert", exc) from exc
        return _to_grant(row)

    def _save(self, grant: AdditionalAttemptGrant) -> AdditionalAttemptGrant:
        row = self._session.get(AdditionalAttemptGrantRow, grant.grant_id)
        if row is None:
            raise GrantNotFoundError(grant.grant_id)
        if (
            row.additional_attempts != grant.additional_attempts
            or row.learner_id != grant.learner_id
            or row.course_id != grant.course_id
            or row.quiz_id != grant.quiz_id
        ):
            # The number of attempts a grant conferred is audit trail, not editable state.
            raise ValueError(
                "A grant's scope and attempt count are immutable; only its status may change."
            )

        row.status = grant.status.value
        row.revoked_at = _instant(grant.revoked_at)
        row.revoked_by = grant.revoked_by
        try:
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise PersistenceFailedError("grants.save", exc) from exc
        return _to_grant(row)
