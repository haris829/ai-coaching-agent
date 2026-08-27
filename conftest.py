"""Root-level pytest guard.

Running ``pytest`` from the repository root would collect ten packages onto one
``sys.path``. Three of them import their fixtures as a top-level ``tests``
package (UC-02, UC-07), and several ship ``tests/__init__.py``, so one
component's ``tests`` package silently shadows another's. The result is not a
clean failure - it is some suites importing the wrong fixtures.

Rather than let that happen quietly, refuse the invocation and say where to go.
Per-package runs never load this file: each package under packages/ carries its
own pytest configuration, so pytest's rootdir is the package directory and
collection never walks up this far.

See docs/MERGE_NOTES.md, "Import model".
"""

from __future__ import annotations

import pytest

MESSAGE = """
pytest cannot run from the platform root - it would put ten packages on one
sys.path and shadow the `tests` package of three of them.

Run the suites instead:

    python scripts/test_all.py             # all ten components
    python scripts/test_all.py uc03 uc06   # named components
    cd packages/uc03 && python -m pytest   # one suite, as its author ran it

Why: docs/MERGE_NOTES.md, section "Import model".
"""


def pytest_configure(config: pytest.Config) -> None:
    raise pytest.UsageError(MESSAGE)
