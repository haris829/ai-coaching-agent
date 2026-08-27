"""Shared pytest fixtures.

The suite never touches a ``.env`` file and never relies on the process-wide
singletons in ``uc02.composition``: every test builds its own harness.
"""

from __future__ import annotations

import pytest

from tests.fixtures.factories import Harness, make_harness, make_settings


@pytest.fixture()
def settings():
    return make_settings()


@pytest.fixture()
def harness(settings) -> Harness:
    """All four sources healthy, default scenarios."""
    return make_harness(settings=settings)
