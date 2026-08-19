"""Fixtures for UC-07's suite.

The builders and the ``World`` type live in ``world.py``; this file is only the pytest wiring, so a
test module can import a builder without importing a fixture and vice versa.

``anyio_backend`` is what makes ``pytestmark = pytest.mark.anyio`` work. UC-07's services are
asynchronous because the AI provider they were shaped around is, and anyio's pytest plugin — which
arrives with starlette rather than as a separate dependency — runs them.

The root ``conftest.py``'s ``_clean_tables`` fixture still applies to these tests. It is harmless
here: nothing in this package touches the database, because everything below the services is a port
fake. The tests that *do* exercise the real ``qk_`` tables live in ``tests/integration/``.
"""

from __future__ import annotations

import pytest

from tests.coaching.world import World, build_world


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def world() -> World:
    return build_world()
