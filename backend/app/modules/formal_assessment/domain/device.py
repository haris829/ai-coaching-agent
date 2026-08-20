"""The device session — the single-device lock (§3).

    first valid session  ->  becomes authoritative  ->  attempt locked to it
    second session       ->  recorded, rejected, learner told to go back to the first device

WHY THE LOCK IS A RECORD AND NOT A FLAG
---------------------------------------
"The attempt is locked to one device" could be a boolean on the attempt. It is a record instead,
because three questions need answering afterwards and only a record answers them: *which* device
holds it, *when* it was last seen, and *what else* tried to join. The rejected sessions are kept for
the same reason — an assessor reviewing an approval wants to know a second device was turned away.

WHY A CLIENT-SUPPLIED DEVICE ID IS NOT THE LOCK
-----------------------------------------------
``device_fingerprint`` — user agent, screen size, whatever the client computes — is recorded as
**evidence, never as the credential**. Anything the client computes on device A, it can compute on
device B. So the session is identified by ``session_token``: 32 bytes of server-generated entropy,
returned once when the session is registered, and presented on every subsequent operation. Holding
the token is what proves a caller is the authoritative device; the fingerprint only ever describes.

WHY ``client_request_id`` EXISTS
-------------------------------
A start request that times out leaves the client not knowing whether it won. Retrying must not be
refused as "a second device", and must not hand out a *new* session either. So a client may supply
an unguessable ``client_request_id``; a retry carrying the same one is recognised as the same
request and replays the same session token. Without it, the safe answer to "another registration for
a locked attempt" is refusal — which is why the fallback is rejection rather than a fingerprint
comparison that a second device could satisfy by copying a user agent string.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.modules.formal_assessment.domain.enums import DeviceSessionState


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    """What the client says about itself. Descriptive only — never used to decide the lock."""

    #: The client's own device/browser identifier, if it has one. A claim, not a credential.
    fingerprint: str | None = None
    user_agent: str | None = None
    #: Recorded from the request, not from the body: a client cannot choose what address it came
    #: from.
    ip_address: str | None = None
    platform: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "user_agent": self.user_agent,
            "ip_address": self.ip_address,
            "platform": self.platform,
        }


@dataclass(frozen=True, slots=True)
class DeviceSession:
    """One device's session against one formal attempt.

    At most one row per ``formal_attempt_id`` may be ACTIVE, and that constraint is enforced by the
    persistence layer rather than by a read-then-write in a service — see
    ``repositories.protocols``. The write *is* the check, which is what makes two simultaneous
    registrations resolve to one winner instead of two authoritative sessions.
    """

    session_id: str
    formal_attempt_id: str
    learner_id: str
    state: DeviceSessionState
    registered_at: str
    #: Server-generated secret. Never logged, never returned by any read endpoint — only by the
    #: registration response that created it.
    session_token: str = ""
    device: DeviceDescriptor = DeviceDescriptor()
    #: The client's replay token for the registration request. See the module docstring.
    client_request_id: str | None = None
    last_seen_at: str | None = None
    closed_at: str | None = None
    #: Why the session ended, or why it was refused.
    closed_reason: str | None = None
    #: For a REJECTED session: the session that held the lock at the time.
    superseded_by_session_id: str | None = None
    version: int = 1

    @property
    def active(self) -> bool:
        return self.state is DeviceSessionState.ACTIVE

    def matches_token(self, token: str | None) -> bool:
        """Constant-ish comparison of the presented token against this session's.

        ``compare_digest`` because this value authorises actions on a live assessment, and a short-
        circuiting string comparison on a secret is a habit worth not having.
        """
        if not token or not self.session_token:
            return False
        from hmac import compare_digest

        return compare_digest(token, self.session_token)

    def seen(self, *, now: str) -> DeviceSession:
        """Record a heartbeat. Does not change the state; a session is either active or it is not.
        """
        return replace(self, last_seen_at=now, version=self.version + 1)

    def close(self, *, now: str, reason: str) -> DeviceSession:
        """End the session normally — the attempt was submitted."""
        return replace(
            self,
            state=DeviceSessionState.CLOSED,
            closed_at=now,
            closed_reason=reason,
            version=self.version + 1,
        )

    def disconnect(self, *, now: str, reason: str) -> DeviceSession:
        """End the session because it disconnected. Distinguished from CLOSED for the audit trail.
        """
        return replace(
            self,
            state=DeviceSessionState.DISCONNECTED,
            closed_at=now,
            closed_reason=reason,
            version=self.version + 1,
        )

    def as_dict(self, *, include_token: bool = False) -> dict[str, Any]:
        """Serialise. The token is omitted unless the caller is the response that issued it."""
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "formal_attempt_id": self.formal_attempt_id,
            "learner_id": self.learner_id,
            "state": self.state.value,
            "registered_at": self.registered_at,
            "last_seen_at": self.last_seen_at,
            "closed_at": self.closed_at,
            "closed_reason": self.closed_reason,
            "superseded_by_session_id": self.superseded_by_session_id,
            "device": self.device.as_dict(),
        }
        if include_token:
            payload["session_token"] = self.session_token
        return payload


def new_device_session(
    *,
    session_id: str,
    formal_attempt_id: str,
    learner_id: str,
    session_token: str,
    now: str,
    device: DeviceDescriptor | None = None,
    client_request_id: str | None = None,
) -> DeviceSession:
    """A session claiming the lock. Whether it gets it is decided by the repository's constraint."""
    return DeviceSession(
        session_id=session_id,
        formal_attempt_id=formal_attempt_id,
        learner_id=learner_id,
        state=DeviceSessionState.ACTIVE,
        registered_at=now,
        session_token=session_token,
        device=device or DeviceDescriptor(),
        client_request_id=client_request_id,
        last_seen_at=now,
    )


def rejected_device_session(
    *,
    session_id: str,
    formal_attempt_id: str,
    learner_id: str,
    now: str,
    holder_session_id: str | None,
    device: DeviceDescriptor | None = None,
    client_request_id: str | None = None,
) -> DeviceSession:
    """The evidence record for a device that was turned away (§3, §14).

    Written with no session token: a rejected device is never given anything it could present later.
    """
    return DeviceSession(
        session_id=session_id,
        formal_attempt_id=formal_attempt_id,
        learner_id=learner_id,
        state=DeviceSessionState.REJECTED,
        registered_at=now,
        session_token="",
        device=device or DeviceDescriptor(),
        client_request_id=client_request_id,
        closed_at=now,
        closed_reason="SECOND_DEVICE_REJECTED",
        superseded_by_session_id=holder_session_id,
    )
