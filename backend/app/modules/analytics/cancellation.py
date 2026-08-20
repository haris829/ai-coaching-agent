"""Query cancellation, deadlines and cooperative timeout handling.

UC-10 must survive long-running aggregations over an external provider. Three
mechanisms are provided. All are optional for a repository implementation, and
all are honoured by the services:

``deadline``
    An absolute instant after which work must stop. Services check it between
    pages, so a slow provider cannot hold a request open indefinitely.

``cancellation``
    A caller-triggered stop (client disconnect, or the user refining filters
    while a query is still running). Cooperative: work stops at the next
    checkpoint rather than being killed mid-operation.

``request_id``
    Correlation id propagated into logs and error payloads.

A repository implementation SHOULD:

* pass ``context.remaining_seconds()`` to its driver as a statement timeout,
* call ``context.raise_if_stopped()`` between batches,
* translate driver-level cancellation into :class:`QueryCancelledError`.

A repository that ignores the context stays correct: the service still enforces
the deadline around every repository call via :func:`run_with_deadline`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, TypeVar

from app.core.time import Clock, SystemClock
from app.modules.analytics.errors import QueryCancelledError, QueryTimeoutError

__all__ = ["QueryContext", "run_with_deadline"]

T = TypeVar("T")

_TIMEOUT_MESSAGE = (
    "The analytics query exceeded its time budget. "
    "Narrow the date range, cohort or course filter and retry."
)


@dataclass
class QueryContext:
    """Per-request execution context carrying deadline and cancellation state.

    Instances are cheap and single-request scoped. Never share one across
    requests: a shared context would turn cancellation into a global kill
    switch.
    """

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    deadline: datetime | None = None
    _clock: Clock = field(default_factory=SystemClock, repr=False)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _cancel_reason: str | None = field(default=None, repr=False)
    #: Async predicates consulted at each checkpoint, e.g. request.is_disconnected
    _stop_checks: list[Callable[[], Awaitable[bool]]] = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        *,
        timeout_seconds: float | None = None,
        clock: Clock | None = None,
        request_id: str | None = None,
    ) -> QueryContext:
        clock = clock or SystemClock()
        deadline: datetime | None = None
        if timeout_seconds is not None:
            if timeout_seconds <= 0:
                raise ValueError("timeout_seconds must be > 0")
            deadline = clock.now() + timedelta(seconds=timeout_seconds)
        return cls(
            request_id=request_id or uuid.uuid4().hex,
            deadline=deadline,
            _clock=clock,
        )

    # ------------------------------------------------------------------ state

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def cancel_reason(self) -> str | None:
        return self._cancel_reason

    def cancel(self, reason: str = "cancelled by caller") -> None:
        """Request cooperative cancellation. Idempotent."""
        if not self._cancel_event.is_set():
            self._cancel_reason = reason
            self._cancel_event.set()

    def add_stop_check(self, check: Callable[[], Awaitable[bool]]) -> None:
        """Register an async predicate reporting whether work should stop."""
        self._stop_checks.append(check)

    def remaining_seconds(self) -> float | None:
        """Seconds left before the deadline, or ``None`` when unbounded."""
        if self.deadline is None:
            return None
        return (self.deadline - self._clock.now()).total_seconds()

    def expired(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0

    # ------------------------------------------------------------ checkpoints

    def raise_if_stopped(self) -> None:
        """Synchronous checkpoint: raises if cancelled or past the deadline."""
        if self.cancelled:
            raise QueryCancelledError(
                self._cancel_reason or "Query was cancelled.",
                details={"request_id": self.request_id},
            )
        if self.expired():
            raise QueryTimeoutError(
                _TIMEOUT_MESSAGE,
                details={"request_id": self.request_id},
            )

    async def acheck(self) -> None:
        """Async checkpoint: also consults registered stop checks."""
        for check in self._stop_checks:
            if await check():
                self.cancel("client disconnected")
                break
        self.raise_if_stopped()

    def child(self) -> QueryContext:
        """Derive a context sharing this deadline, for a nested query."""
        ctx = QueryContext(
            request_id=self.request_id,
            deadline=self.deadline,
            _clock=self._clock,
        )
        ctx._stop_checks = list(self._stop_checks)
        return ctx

    def describe(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "remaining_seconds": self.remaining_seconds(),
            "cancelled": self.cancelled,
        }


async def run_with_deadline(awaitable: Awaitable[T], context: QueryContext) -> T:
    """Await ``awaitable`` under the context deadline and cancellation flag.

    Hard enforcement layer: a repository that blocks without ever consulting
    the context is still cut off here.
    """
    remaining = context.remaining_seconds()
    if context.cancelled or (remaining is not None and remaining <= 0):
        # Close the coroutine so it is never left un-awaited.
        _close_unstarted(awaitable)
        context.raise_if_stopped()

    task: asyncio.Task[T] = asyncio.ensure_future(awaitable)
    waiter = asyncio.ensure_future(context._cancel_event.wait())
    try:
        done, _pending = await asyncio.wait(
            {task, waiter},
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            return task.result()

        task.cancel()
        if waiter in done:
            raise QueryCancelledError(
                context.cancel_reason or "Query was cancelled.",
                details={"request_id": context.request_id},
            )
        raise QueryTimeoutError(
            _TIMEOUT_MESSAGE,
            details={"request_id": context.request_id},
        )
    finally:
        waiter.cancel()
        if task.done() and not task.cancelled():
            # Retrieve any repository failure so it is not reported as
            # "exception was never retrieved" on garbage collection.
            task.exception()


def _close_unstarted(awaitable: Awaitable[Any]) -> None:
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()
