"""Running one synchronous database call from an ``async def``.

Four capabilities have asynchronous service layers — UC-07's coach talks to an HTTP provider,
UC-08 and UC-09 fan out across several modules per request, and UC-10 aggregates over pages —
while the persistence layer underneath all of them is a synchronous SQLAlchemy ``Session``.
Calling a synchronous session directly from a coroutine blocks the event loop for the duration
of the query, which under load is the whole server rather than one request.

:func:`offload` is the one sanctioned bridge: it moves a single synchronous call onto a worker
thread. Keeping it in one place makes the pattern a documented decision instead of a habit
copied from adapter to adapter, and gives one obvious seam to change if the project ever moves
to an async engine — every call site would then drop the wrapper and nothing else.

A session must not be shared across threads, and it is not: each request builds its adapters
around its own session, and ``offload`` runs one call at a time within that request.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

import anyio.to_thread

_T = TypeVar("_T")


async def offload(function: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Run one synchronous database call on a worker thread."""
    return await anyio.to_thread.run_sync(partial(function, *args, **kwargs))
