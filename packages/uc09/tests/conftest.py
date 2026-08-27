"""Shared fixtures.

The whole suite runs against the deterministic fake generator with no API key
and no network. ``UC09_SUMMARY_GENERATOR`` is pinned to ``fake`` here so that a
developer with a real generator configured in their environment still gets the
deterministic run, and any ambient ``UC09_*`` variable is cleared so a local
``.env`` cannot change what the suite is testing.
"""

from __future__ import annotations

import os

import pytest

from tests.support.harness import Harness, build_harness
from uc09_summary.adapters.mock import scenarios as S


@pytest.fixture(autouse=True, scope="session")
def _pinned_environment() -> None:
    for key in [k for k in os.environ if k.startswith("UC09_")]:
        del os.environ[key]
    os.environ["UC09_SUMMARY_GENERATOR"] = "fake"


@pytest.fixture
def harness() -> Harness:
    """Default harness: mock upstreams, fake generator, deterministic renderer."""
    return build_harness()


@pytest.fixture
def owner() -> str:
    return S.OWNER_USER_ID


@pytest.fixture
def other_user() -> str:
    return S.OTHER_USER_ID
