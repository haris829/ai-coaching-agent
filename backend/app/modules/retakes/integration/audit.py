"""Outbound audit trail (§12 — grants must be traceable).

An outbound stream: UC-08 writes events and never reads them back, which is why it lives here
rather than with the persistence protocols. A grant already carries ``granted_by``, a reason and
an idempotency key on its own record; this port is what puts the same facts in front of the
platform's audit pipeline, where a compliance question gets answered without querying a
use-case's own store.

The default binding logs. It does not silently discard: an unwired deployment still leaves a
record of who granted what to whom, in the application log, which is the honest fallback.

Auditing must never be able to fail a business operation. Implementations should swallow their
own transport failures — a grant that succeeded and an audit line that did not is strictly better
than a learner denied an attempt because an audit sink was down.
"""

from __future__ import annotations

import contextlib
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class RetakeAuditLog(Protocol):
    async def record(self, event: str, **fields: Any) -> None:
        """Record one auditable event. Must not raise."""
        ...


class LoggingRetakeAuditLog:
    """Default binding: a structured log line per event."""

    async def record(self, event: str, **fields: Any) -> None:
        # Suppressed deliberately: a logging fault must never fail a grant that succeeded.
        with contextlib.suppress(Exception):
            logger.info(f"retake.audit.{event}", extra=fields)
