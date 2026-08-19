"""Persistence for UC-05.

A ``Protocol`` per aggregate plus today's SQLAlchemy implementation, so the service depends on the
contract and the company database is a change to this file alone.

Every transition is a compare-and-set:

* ``insert_outcome`` leans on ``uq_qg_attempt_outcomes_attempt_id``, so two concurrent
  determinations produce one verdict and the loser adopts it;
* ``mark_certificate_issued`` carries ``WHERE status = 'PENDING'``, so a second issuance cannot
  overwrite an issued certificate -- and if it somehow reached the insert, the partial unique index
  ``ux_qg_certificate_single_issued`` refuses it;
* ``mark_cpd_synchronised`` is the same shape, so a duplicated CPD push cannot double-log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.certification.domain.enums import CertificateStatus, CpdSyncStatus
from app.modules.certification.models import AttemptOutcome, Certificate, CpdRecord


class CertificationRepository(Protocol):
    """What the certification service needs from persistence."""

    # ---- outcomes ---------------------------------------------------------
    def get_outcome(self, attempt_id: str) -> AttemptOutcome | None: ...

    def list_outcomes(
        self, learner_id: str, *, quiz_id: str | None = None
    ) -> list[AttemptOutcome]: ...

    def insert_outcome(self, **fields: Any) -> AttemptOutcome: ...

    def find_issued_certificate_for_quiz(
        self, learner_id: str, quiz_id: str
    ) -> Certificate | None: ...

    # ---- certificates -----------------------------------------------------
    def get_certificate(self, attempt_id: str) -> Certificate | None: ...

    def insert_certificate(self, **fields: Any) -> Certificate: ...

    def record_certificate_run(self, certificate_id: str, now: datetime) -> None: ...

    def mark_certificate_issued(
        self,
        certificate_id: str,
        *,
        certificate_number: str,
        document_reference: str | None,
        metadata: Any,
        now: datetime,
    ) -> bool: ...

    def mark_certificate_failure(
        self,
        certificate_id: str,
        *,
        status: CertificateStatus,
        failure_code: str,
        failure_message: str,
        now: datetime,
    ) -> bool: ...

    # ---- CPD --------------------------------------------------------------
    def get_cpd_record(self, attempt_id: str) -> CpdRecord | None: ...

    def insert_cpd_record(self, **fields: Any) -> CpdRecord: ...

    def record_cpd_run(self, record_id: str, now: datetime) -> None: ...

    def mark_cpd_synchronised(
        self, record_id: str, *, external_reference: str | None, now: datetime
    ) -> bool: ...

    def mark_cpd_failure(
        self,
        record_id: str,
        *,
        status: CpdSyncStatus,
        failure_code: str,
        failure_message: str,
        now: datetime,
    ) -> bool: ...


class SqlAlchemyCertificationRepository:
    """Today's implementation: SQLAlchemy over the shared metadata."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- outcomes ---------------------------------------------------------

    def get_outcome(self, attempt_id: str) -> AttemptOutcome | None:
        return self._session.scalar(
            select(AttemptOutcome).where(AttemptOutcome.attempt_id == attempt_id)
        )

    def list_outcomes(self, learner_id: str, *, quiz_id: str | None = None) -> list[AttemptOutcome]:
        statement = select(AttemptOutcome).where(AttemptOutcome.learner_id == str(learner_id))
        if quiz_id is not None:
            statement = statement.where(AttemptOutcome.quiz_id == str(quiz_id))
        return list(
            self._session.scalars(statement.order_by(AttemptOutcome.attempt_number.desc())).all()
        )

    def insert_outcome(self, **fields: Any) -> AttemptOutcome:
        row = AttemptOutcome(**fields)
        self._session.add(row)
        self._session.flush()
        return row

    def find_issued_certificate_for_quiz(self, learner_id: str, quiz_id: str) -> Certificate | None:
        """The learner's issued certificate for this quiz, if they already have one.

        Read by the service before requesting another, so a second pass reports the certificate the
        learner already holds instead of asking for a duplicate.
        """
        return self._session.scalar(
            select(Certificate).where(
                Certificate.learner_id == str(learner_id),
                Certificate.quiz_id == str(quiz_id),
                Certificate.status == CertificateStatus.ISSUED.value,
            )
        )

    # ---- certificates -----------------------------------------------------

    def get_certificate(self, attempt_id: str) -> Certificate | None:
        return self._session.scalar(select(Certificate).where(Certificate.attempt_id == attempt_id))

    def insert_certificate(self, **fields: Any) -> Certificate:
        row = Certificate(status=CertificateStatus.PENDING.value, **fields)
        self._session.add(row)
        self._session.flush()
        return row

    def record_certificate_run(self, certificate_id: str, now: datetime) -> None:
        self._session.execute(
            update(Certificate)
            .where(
                Certificate.id == certificate_id,
                Certificate.status != CertificateStatus.ISSUED.value,
            )
            .values(
                generation_attempt_count=Certificate.generation_attempt_count + 1,
                last_attempted_at=now,
                updated_at=now,
            )
        )

    def mark_certificate_issued(
        self,
        certificate_id: str,
        *,
        certificate_number: str,
        document_reference: str | None,
        metadata: Any,
        now: datetime,
    ) -> bool:
        outcome = self._session.execute(
            update(Certificate)
            .where(
                Certificate.id == certificate_id,
                Certificate.status != CertificateStatus.ISSUED.value,
            )
            .values(
                status=CertificateStatus.ISSUED.value,
                certificate_number=certificate_number,
                document_reference=document_reference,
                metadata_payload=metadata,
                issued_at=now,
                updated_at=now,
                failure_code=None,
                failure_message=None,
            )
        )
        return bool(outcome.rowcount)

    def mark_certificate_failure(
        self,
        certificate_id: str,
        *,
        status: CertificateStatus,
        failure_code: str,
        failure_message: str,
        now: datetime,
    ) -> bool:
        outcome = self._session.execute(
            update(Certificate)
            .where(
                Certificate.id == certificate_id,
                Certificate.status != CertificateStatus.ISSUED.value,
            )
            .values(
                status=status.value,
                failure_code=failure_code,
                failure_message=failure_message,
                updated_at=now,
            )
        )
        return bool(outcome.rowcount)

    # ---- CPD --------------------------------------------------------------

    def get_cpd_record(self, attempt_id: str) -> CpdRecord | None:
        return self._session.scalar(select(CpdRecord).where(CpdRecord.attempt_id == attempt_id))

    def insert_cpd_record(self, **fields: Any) -> CpdRecord:
        row = CpdRecord(status=CpdSyncStatus.PENDING.value, **fields)
        self._session.add(row)
        self._session.flush()
        return row

    def record_cpd_run(self, record_id: str, now: datetime) -> None:
        self._session.execute(
            update(CpdRecord)
            .where(
                CpdRecord.id == record_id,
                CpdRecord.status != CpdSyncStatus.SYNCHRONISED.value,
            )
            .values(
                sync_attempt_count=CpdRecord.sync_attempt_count + 1,
                last_attempted_at=now,
                updated_at=now,
            )
        )

    def mark_cpd_synchronised(
        self, record_id: str, *, external_reference: str | None, now: datetime
    ) -> bool:
        outcome = self._session.execute(
            update(CpdRecord)
            .where(
                CpdRecord.id == record_id,
                CpdRecord.status != CpdSyncStatus.SYNCHRONISED.value,
            )
            .values(
                status=CpdSyncStatus.SYNCHRONISED.value,
                external_reference=external_reference,
                synchronised_at=now,
                updated_at=now,
                failure_code=None,
                failure_message=None,
            )
        )
        return bool(outcome.rowcount)

    def mark_cpd_failure(
        self,
        record_id: str,
        *,
        status: CpdSyncStatus,
        failure_code: str,
        failure_message: str,
        now: datetime,
    ) -> bool:
        outcome = self._session.execute(
            update(CpdRecord)
            .where(
                CpdRecord.id == record_id,
                CpdRecord.status != CpdSyncStatus.SYNCHRONISED.value,
            )
            .values(
                status=status.value,
                failure_code=failure_code,
                failure_message=failure_message,
                updated_at=now,
            )
        )
        return bool(outcome.rowcount)
