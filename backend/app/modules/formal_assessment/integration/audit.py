"""Outbound audit trail (§14).

An outbound stream: UC-09 writes events and never reads them back, which is why this is a port
rather than a repository. **There is no second audit framework here** — no audit table, no audit
model, no query API. The platform's existing audit pipeline is bound to :class:`FormalAuditLog` in
the composition root, and the twenty-odd event names UC-09 emits are the vocabulary in
``domain.enums.FormalAuditEvent``.

WHY AUDITING CANNOT FAIL AN OPERATION
-------------------------------------
:meth:`FormalAuditLog.record` must not raise. A formal assessment that was correctly submitted and
an audit line that did not reach the sink is strictly better than a learner losing their submission
because a log shipper was down. The default binding logs and suppresses; implementations must do the
same, and :func:`safe_record` guarantees it at the call site regardless — so a badly behaved adapter
cannot take down an assessment either.

WHAT AN AUDIT LINE MAY CONTAIN
------------------------------
Identifiers, states, reasons, counts, instants. **Never** the learner's name, their email address,
their answers, or a session token. Those are exactly the fields an audit sink tends to be broadly
readable, and UC-09 handles all four.
"""

from __future__ import annotations

import contextlib
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.modules.formal_assessment.domain.enums import FormalAuditEvent

logger = get_logger(__name__)

#: Field names that must never reach an audit sink from this module. Enforced by
#: :func:`sanitise_fields`, so a new call site cannot leak one by accident.
FORBIDDEN_AUDIT_FIELDS: frozenset[str] = frozenset(
    {
        "full_name",
        "name",
        "entered_name",
        "email",
        "entered_email",
        "session_token",
        "token",
        "answers",
        "response",
        "responses",
        "password",
    }
)


def sanitise_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop personal data and secrets, and drop ``None`` values.

    Redaction rather than rejection: an audit line missing a field is still useful, and raising here
    would mean a mistake in a call site could fail a formal assessment.
    """
    return {
        key: value
        for key, value in fields.items()
        if value is not None and key.lower() not in FORBIDDEN_AUDIT_FIELDS
    }


@runtime_checkable
class FormalAuditLog(Protocol):
    """The platform audit pipeline, as UC-09 needs it."""

    async def record(self, event: str, /, **fields: Any) -> None:
        """Record one auditable event. Must not raise.

        ``event`` is positional-only: UC-09 emits fields of its own such as ``event`` (a
        notification event) and an implementation whose parameter name could be shadowed by a field
        would fail on exactly those calls.
        """
        ...


class LoggingFormalAuditLog:
    """Default binding: a structured log line per event.

    Not a silent discard: an unwired deployment still leaves the full trail in the application log,
    which is the honest fallback. The platform's audit pipeline replaces this in ``container.py``.
    """

    async def record(self, event: str, /, **fields: Any) -> None:
        # Suppressed deliberately: a logging fault must never fail a formal assessment.
        with contextlib.suppress(Exception):
            logger.info(f"formal.audit.{event}", extra=sanitise_fields(fields))


async def safe_record(
    audit: FormalAuditLog, audit_event: FormalAuditEvent, /, **fields: Any
) -> None:
    """Emit an event through the port, sanitised, and swallow anything the adapter throws.

    Every audit call in UC-09 goes through this function rather than calling ``audit.record``
    directly, which is what makes "auditing cannot fail an operation" a property of the module
    rather than a promise about each adapter.

    The first two parameters are positional-only so that a caller may pass a field of its own called
    ``event`` — a notification event, for instance — without colliding with this function's own
    parameter name.
    """
    with contextlib.suppress(Exception):
        await audit.record(audit_event.value, **sanitise_fields(fields))
