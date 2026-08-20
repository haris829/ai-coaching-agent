"""The single-device session lock (§3, §19, §20).

    register -> claim (a database constraint)
                  |                 |
              first wins        second refused, recorded as evidence
                  |
            authoritative session; every later operation must present its token

WHY THIS IS SERVER-SIDE, AND WHAT THAT ACTUALLY REQUIRES
--------------------------------------------------------
Three things, and the first two are the ones a frontend-shaped implementation gets wrong:

1. **The lock is a uniqueness constraint, not a check.** ``claim`` inserts an ACTIVE session and the
   persistence layer refuses a second one. Two simultaneous registrations both call it; one insert
   succeeds. A service that asked "is there an active session?" and then inserted would have a
   window between the question and the answer, and that window is the race in §20.

2. **The credential is server-generated.** The client presents a ``session_token`` that this module
   created with 32 bytes of entropy. A browser-generated device id is a claim anyone can copy; a
   token the server issued once is something only the device that registered can hold.

3. **A rejected device is recorded, not just refused.** The evidence an assessor reads — "another
device
   tried to join at 10:42" — exists because the rejection writes a REJECTED session row before
   raising.

WHY A CLOSED SESSION CANNOT BE REOPENED
---------------------------------------
``authorise`` accepts only an ACTIVE session whose token matches. Once a session is CLOSED or
DISCONNECTED there is no operation that returns it to ACTIVE — the repository refuses the transition
— so "no resume after disconnect" is enforced by the session layer as well as by the state machine.
Two independent mechanisms for the same rule is not redundancy here: they fail in different
directions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.formal_assessment.domain.anomalies import anomaly
from app.modules.formal_assessment.domain.attempt import FormalAttempt
from app.modules.formal_assessment.domain.device import (
    DeviceDescriptor,
    DeviceSession,
    new_device_session,
    rejected_device_session,
)
from app.modules.formal_assessment.domain.enums import (
    FormalAnomalyCode,
    FormalAuditEvent,
)
from app.modules.formal_assessment.domain.errors import (
    ConcurrentModificationError,
    DeviceSessionAlreadyHeldError,
    DeviceSessionConflictError,
    SecondDeviceRejectedError,
)
from app.modules.formal_assessment.domain.idempotency import is_usable_client_request_id
from app.modules.formal_assessment.ids import IdGenerator, TokenGenerator
from app.modules.formal_assessment.integration.audit import FormalAuditLog, safe_record
from app.modules.formal_assessment.repositories.protocols import (
    DeviceSessionRepository,
    FormalAttemptRepository,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SessionRegistration:
    """A registered session. ``replayed`` is True when a retry found the session it already made."""

    session: DeviceSession
    replayed: bool = False

    def as_dict(self, *, include_token: bool = False) -> dict[str, Any]:
        return {**self.session.as_dict(include_token=include_token), "replayed": self.replayed}


class DeviceSessionService:
    def __init__(
        self,
        *,
        sessions: DeviceSessionRepository,
        attempts: FormalAttemptRepository,
        audit: FormalAuditLog,
        clock: Clock,
        new_id: IdGenerator,
        new_token: TokenGenerator,
        heartbeat_timeout_seconds: int,
    ) -> None:
        self._sessions = sessions
        self._attempts = attempts
        self._audit = audit
        self._clock = clock
        self._new_id = new_id
        self._new_token = new_token
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds

    @property
    def heartbeat_timeout_seconds(self) -> int:
        """Published so the platform's session monitor and this module agree on one threshold."""
        return self._heartbeat_timeout_seconds

    async def register(
        self,
        formal_attempt: FormalAttempt,
        *,
        device: DeviceDescriptor | None = None,
        client_request_id: str | None = None,
    ) -> SessionRegistration:
        """Claim the authoritative session for a formal attempt (§3).

        Raises :class:`SecondDeviceRejectedError` when another device already holds it — after
        recording the rejected device on the attempt and in the audit trail.
        """
        now = to_iso(self._clock.now())

        replay_token = client_request_id if is_usable_client_request_id(client_request_id) else None
        if replay_token:
            existing = await self._sessions.find_by_client_request_id(
                formal_attempt.formal_attempt_id, replay_token
            )
            if existing is not None:
                if existing.active:
                    # The same request arriving twice — a client retrying after a timeout. Replay
                    # the
                    # session it already has rather than refusing it as a second device.
                    return SessionRegistration(session=existing, replayed=True)
                # The session this request created has since ended. A new lock is not the answer:
                # the
                # assessment is over for this device.
                raise DeviceSessionConflictError(
                    formal_attempt_id=formal_attempt.formal_attempt_id,
                    session_state=existing.state.value,
                )

        session = new_device_session(
            session_id=self._new_id(),
            formal_attempt_id=formal_attempt.formal_attempt_id,
            learner_id=formal_attempt.learner_id,
            session_token=self._new_token(),
            now=now,
            device=device,
            client_request_id=replay_token,
        )

        try:
            claimed = await self._sessions.claim(session)
        except DeviceSessionAlreadyHeldError as conflict:
            holder_id = conflict.context.get("active_session_id")
            await self._reject_second_device(
                formal_attempt,
                device=device,
                client_request_id=replay_token,
                holder_session_id=holder_id if isinstance(holder_id, str) else None,
                now=now,
            )
            raise SecondDeviceRejectedError(
                formal_attempt_id=formal_attempt.formal_attempt_id,
                active_session_id=holder_id if isinstance(holder_id, str) else None,
            ) from conflict

        await safe_record(
            self._audit,
            FormalAuditEvent.DEVICE_SESSION_REGISTERED,
            formal_attempt_id=formal_attempt.formal_attempt_id,
            learner_id=formal_attempt.learner_id,
            quiz_id=formal_attempt.quiz_id,
            session_id=claimed.session_id,
            device_fingerprint=claimed.device.fingerprint,
            ip_address=claimed.device.ip_address,
            replayed=False,
        )
        return SessionRegistration(session=claimed, replayed=False)

    async def authorise(
        self, formal_attempt: FormalAttempt, *, session_token: str | None
    ) -> DeviceSession:
        """Confirm the caller holds the authoritative session (§3, §19).

        Called by every operation on a live formal attempt — autosave, submit, heartbeat. A caller
        with the right learner identity but the wrong session is refused: authenticating as the
        learner is not the same as being the device sitting the assessment.
        """
        active = await self._sessions.get_active(formal_attempt.formal_attempt_id)
        if active is None:
            raise DeviceSessionConflictError(
                formal_attempt_id=formal_attempt.formal_attempt_id, session_state=None
            )
        if not active.matches_token(session_token):
            # Recorded as a second-device observation: presenting the wrong token for a live
            # assessment
            # is either another device or a replayed one, and both are worth an assessor seeing.
            await self._note_anomaly(
                formal_attempt,
                FormalAnomalyCode.SECOND_DEVICE_ATTEMPTED,
                now=to_iso(self._clock.now()),
                detail="INVALID_SESSION_TOKEN",
            )
            await safe_record(
                self._audit,
                FormalAuditEvent.SECOND_DEVICE_REJECTED,
                formal_attempt_id=formal_attempt.formal_attempt_id,
                learner_id=formal_attempt.learner_id,
                quiz_id=formal_attempt.quiz_id,
                active_session_id=active.session_id,
                reason="INVALID_SESSION_TOKEN",
            )
            raise DeviceSessionConflictError(
                formal_attempt_id=formal_attempt.formal_attempt_id,
                session_state=active.state.value,
            )
        return active

    async def heartbeat(self, session: DeviceSession) -> DeviceSession:
        """Record that the session is still alive.

        Best effort: a heartbeat that cannot be written must not fail the operation that carried it.
        The cost of losing one is that the session monitor may call a disconnect slightly early, and
        the disconnect path is safe.
        """
        try:
            return await self._sessions.save(session.seen(now=to_iso(self._clock.now())))
        except (ConcurrentModificationError, Exception):  # noqa: B014 - see the docstring
            logger.info(
                "formal.session.heartbeat_not_recorded",
                extra={"session_id": session.session_id},
            )
            return session

    async def close(self, formal_attempt: FormalAttempt, *, reason: str) -> DeviceSession | None:
        """Close the authoritative session because the attempt ended normally."""
        active = await self._sessions.get_active(formal_attempt.formal_attempt_id)
        if active is None:
            return None
        return await self._save_session(active.close(now=to_iso(self._clock.now()), reason=reason))

    async def mark_disconnected(
        self, formal_attempt: FormalAttempt, *, reason: str
    ) -> DeviceSession | None:
        """Close the authoritative session because it disconnected (§5).

        Idempotent: a second disconnect event finds no active session and does nothing, which is one
        of the several places the disconnect path refuses to act twice.
        """
        active = await self._sessions.get_active(formal_attempt.formal_attempt_id)
        if active is None:
            return None
        return await self._save_session(
            active.disconnect(now=to_iso(self._clock.now()), reason=reason)
        )

    async def list_for_attempt(self, formal_attempt: FormalAttempt) -> tuple[DeviceSession, ...]:
        """Every session against this attempt, for the assessor's review payload (§10)."""
        return await self._sessions.list_for_attempt(formal_attempt.formal_attempt_id)

    async def _reject_second_device(
        self,
        formal_attempt: FormalAttempt,
        *,
        device: DeviceDescriptor | None,
        client_request_id: str | None,
        holder_session_id: str | None,
        now: str,
    ) -> None:
        """Write the evidence for a device that was turned away, then let the caller refuse it."""
        rejected = rejected_device_session(
            session_id=self._new_id(),
            formal_attempt_id=formal_attempt.formal_attempt_id,
            learner_id=formal_attempt.learner_id,
            now=now,
            holder_session_id=holder_session_id,
            device=device,
            client_request_id=client_request_id,
        )
        try:
            await self._sessions.record_rejected(rejected)
        except Exception:  # noqa: BLE001 - the refusal matters more than the evidence
            logger.warning(
                "formal.session.rejection_not_recorded",
                extra={"formal_attempt_id": formal_attempt.formal_attempt_id},
            )

        await self._note_anomaly(
            formal_attempt,
            FormalAnomalyCode.SECOND_DEVICE_ATTEMPTED,
            now=now,
            detail="REGISTRATION_REFUSED",
        )
        await safe_record(
            self._audit,
            FormalAuditEvent.SECOND_DEVICE_REJECTED,
            formal_attempt_id=formal_attempt.formal_attempt_id,
            learner_id=formal_attempt.learner_id,
            quiz_id=formal_attempt.quiz_id,
            rejected_session_id=rejected.session_id,
            active_session_id=holder_session_id,
            device_fingerprint=rejected.device.fingerprint,
            ip_address=rejected.device.ip_address,
            reason="REGISTRATION_REFUSED",
        )

    async def _note_anomaly(
        self,
        formal_attempt: FormalAttempt,
        code: FormalAnomalyCode,
        *,
        now: str,
        detail: str | None = None,
    ) -> None:
        """Record an observation on the attempt. Never fails the operation that prompted it."""
        try:
            fresh = await self._attempts.get(formal_attempt.formal_attempt_id)
            if fresh is None:  # pragma: no cover - there is no delete
                return
            await self._attempts.save(
                fresh.with_anomaly(anomaly(code, observed_at=now, detail=detail), now=now)
            )
        except Exception:  # noqa: BLE001 - an anomaly flag must never break a refusal or a save
            logger.warning(
                "formal.anomaly_not_recorded",
                extra={"formal_attempt_id": formal_attempt.formal_attempt_id, "code": code.value},
            )

    async def _save_session(self, session: DeviceSession) -> DeviceSession:
        try:
            return await self._sessions.save(session)
        except ConcurrentModificationError:
            # Another writer changed the session — a heartbeat, or a concurrent close. Re-read and,
            # if it
            # is still active, apply the close onto the winner; otherwise it is already closed and
            # there
            # is nothing to do.
            fresh = await self._sessions.get(session.session_id)
            if fresh is None or not fresh.active:  # pragma: no cover - defensive
                return fresh or session
            return await self._sessions.save(
                fresh.close(now=to_iso(self._clock.now()), reason=session.closed_reason or "CLOSED")
                if session.closed_reason != "DISCONNECT"
                else fresh.disconnect(now=to_iso(self._clock.now()), reason="DISCONNECT")
            )
