"""Refresh policy and idempotency (scope section 9).

Context is built once, at session start. The provider-invocation count is the
evidence: a second initialize must not increase it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from uc02.domain.models.enums import ContextStatus
from uc02.infrastructure.repositories.in_memory_context_repository import (
    InMemorySessionContextRepository,
)
from tests.fixtures.factories import make_harness, make_identity, make_settings


async def test_first_initialize_builds_and_reports_created():
    harness = make_harness()
    outcome = await harness.service.initialize(make_identity())
    assert outcome.status is ContextStatus.CREATED
    assert harness.provider_call_count == 4


async def test_second_initialize_returns_the_stored_context_unchanged():
    harness = make_harness()
    identity = make_identity()
    first = await harness.service.initialize(identity)
    second = await harness.service.initialize(identity)

    assert second.status is ContextStatus.EXISTING
    assert second.context == first.context
    assert second.context.built_at == first.context.built_at


async def test_second_initialize_triggers_no_provider_calls():
    harness = make_harness()
    identity = make_identity()
    await harness.service.initialize(identity)
    calls_after_first = harness.provider_call_count
    assert calls_after_first == 4

    await harness.service.initialize(identity)
    await harness.service.initialize(identity)
    assert harness.provider_call_count == calls_after_first


async def test_different_sessions_for_the_same_user_each_build_once():
    harness = make_harness()
    await harness.service.initialize(make_identity(session_id="sess-a"))
    await harness.service.initialize(make_identity(session_id="sess-b"))
    assert harness.provider_call_count == 8
    assert await harness.repository.get("sess-a") is not None
    assert await harness.repository.get("sess-b") is not None


async def test_force_refresh_rebuilds_and_requeries_providers():
    """Only reachable from the config-gated internal path."""
    harness = make_harness()
    identity = make_identity()
    await harness.service.initialize(identity)
    harness.reset_call_counts()

    outcome = await harness.service.initialize(identity, force_refresh=True)
    assert outcome.status is ContextStatus.REFRESHED
    assert harness.provider_call_count == 4


async def test_an_expired_context_is_rebuilt_on_the_next_initialize():
    clock_now = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)

    class Clock:
        def __init__(self) -> None:
            self.now = clock_now

        def __call__(self) -> datetime:
            return self.now

    clock = Clock()
    settings = make_settings(context_ttl_hours=12)
    repository = InMemorySessionContextRepository(ttl_hours=12, clock=clock)
    harness = make_harness(settings=settings, repository=repository, clock=clock)

    identity = make_identity()
    await harness.service.initialize(identity)
    assert harness.provider_call_count == 4

    clock.now = clock_now + timedelta(hours=13)
    outcome = await harness.service.initialize(identity)
    assert outcome.status is ContextStatus.CREATED
    assert harness.provider_call_count == 8
