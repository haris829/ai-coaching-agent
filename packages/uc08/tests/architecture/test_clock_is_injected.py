"""Every time comparison goes through the injected clock.

If any module read the machine clock directly, the 23h59m / 24h01m tests could
not exist, so this is enforced rather than trusted.
"""

from __future__ import annotations

import ast
import pathlib

UC08_ROOT = pathlib.Path(__file__).resolve().parents[2] / "uc08"

#: The one module allowed to read the machine clock: the clock adapter.
CLOCK_ADAPTER = "uc08/adapters/clock/clocks.py"

#: Calls that read the machine clock.
SYSTEM_CLOCK_CALLS = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "monotonic"),
    ("time", "time_ns"),
}


def _paths() -> list[pathlib.Path]:
    paths = sorted(UC08_ROOT.rglob("*.py"))
    assert paths
    return paths


def test_only_the_clock_adapter_reads_the_machine_clock():
    offenders = []
    for path in _paths():
        relative = path.relative_to(UC08_ROOT.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = getattr(node.func.value, "id", None)
            if (owner, node.func.attr) in SYSTEM_CLOCK_CALLS and relative != CLOCK_ADAPTER:
                offenders.append(f"{relative}:{node.lineno} {owner}.{node.func.attr}()")
    assert not offenders, offenders


def test_the_clock_adapter_does_read_the_machine_clock():
    """The negative test above would also pass if nothing worked at all."""
    source = (UC08_ROOT / "adapters" / "clock" / "clocks.py").read_text(encoding="utf-8")
    assert "datetime.now(timezone.utc)" in source


def test_no_module_sleeps():
    offenders = []
    for path in _paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if name in {"sleep", "asleep"}:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


def test_no_module_uses_a_random_source():
    """Every identifier is derived from the account, the clock or the input."""
    offenders = []
    for path in _paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                names = {alias.name for alias in node.names}
                if module in {"random", "secrets", "uuid"} or names & {"random", "secrets", "uuid"}:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


def test_naive_datetimes_are_rejected_not_assumed():
    from uc08.domain.time_utils import ensure_utc
    from datetime import datetime, timezone
    import pytest as _pytest

    with _pytest.raises(ValueError):
        ensure_utc(datetime(2026, 3, 10, 12, 0))

    aware = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
    assert ensure_utc(aware) == aware


def test_every_service_takes_a_clock():
    import inspect

    from uc08.application.streak_persistence import StreakWriter
    from uc08.application.streak_service import StreakService
    from uc08.application.weekly_summary_service import WeeklySummaryService

    for service in (StreakService, WeeklySummaryService, StreakWriter):
        parameters = inspect.signature(service.__init__).parameters
        assert "clock" in parameters, service.__name__
