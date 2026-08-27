"""Clock and id generation, injected so tests stay deterministic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class SystemClock:
    name = "system"

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Monotonic and deterministic: one second per call."""

    name = "fixed"

    def __init__(self, base: datetime | None = None) -> None:
        self._base = base or datetime(2026, 1, 1, tzinfo=UTC)
        self._tick = 0

    def now(self) -> datetime:
        value = self._base + timedelta(seconds=self._tick)
        self._tick += 1
        return value


class SequentialIdGenerator:
    name = "sequential"

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next_id(self, prefix: str) -> str:
        nxt = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = nxt
        return f"{prefix}_{nxt:06d}"
