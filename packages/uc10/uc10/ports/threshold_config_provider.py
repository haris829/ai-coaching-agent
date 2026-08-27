"""Flagging policy, read at evaluation time.

This is a port precisely because the threshold is admin-configurable: no business rule
may hold a numeric threshold as a constant.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ThresholdConfigProvider(Protocol):
    def down_rate_threshold(self) -> float:
        """Thumbs-down rate at or above which a topic is flagged. SPECIFIED default 0.30."""
        ...

    def minimum_sample_size(self) -> int:
        """Minimum number of current ratings on a topic in the window before any flag can
        be raised. ASSUMED BY US (A-01) -- the specification leaves this open."""
        ...

    def window_days(self) -> int:
        """Rolling window length in days. SPECIFIED as 7; read through the port (A-14)
        so no business rule holds it as a constant either."""
        ...

    def historical_rating_window_hours(self) -> int:
        """How long after delivery a response may still be rated. SPECIFIED as 24 hours;
        read through the port (A-14) so no business rule holds it as a constant."""
        ...
