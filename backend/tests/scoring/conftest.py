"""Fixtures for the scoring suite.

``tests/support/results_world.py`` explains the split: the three capabilities' own tables and the
adapters between them are real, while UC-03, UC-02 and the two outbound services are fakes a test
controls.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.support.results_world import ResultsWorld, world_fixture


@pytest.fixture
def world() -> Iterator[ResultsWorld]:
    yield from world_fixture()
