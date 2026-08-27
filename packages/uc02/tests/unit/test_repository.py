"""In-memory repository behaviour, including TTL expiry.

Expiry is tested with an injected clock, not by sleeping.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from uc02.domain.models.context import (
    CoursesContext,
    ExplanationProfile,
    LegalContext,
    NaricContext,
    PersonalizationStatus,
    QuestionHistoryContext,
    SessionContext,
)
from uc02.domain.models.enums import (
    AssumedPriorKnowledge,
    ExplanationDepth,
    ExplanationTemplateId,
    LevelSource,
    TerminologyLevel,
)
from uc02.infrastructure.repositories.in_memory_context_repository import (
    InMemorySessionContextRepository,
)

START = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)


class MovableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now = self.now + timedelta(**delta)


def _context(session_id: str = "sess-1", user_id: str = "learner-1") -> SessionContext:
    return SessionContext(
        session_id=session_id,
        user_id=user_id,
        naric=NaricContext(level=5, level_source=LevelSource.RETRIEVED),
        courses=CoursesContext(),
        legal_profile=LegalContext(),
        question_history=QuestionHistoryContext(),
        explanation_profile=ExplanationProfile(
            template_id=ExplanationTemplateId.INTERMEDIATE,
            depth=ExplanationDepth.PRACTITIONER_FOUNDATION,
            terminology_level=TerminologyLevel.MIXED,
            assumed_prior_knowledge=AssumedPriorKnowledge.FOUNDATIONAL,
            detail_level=2,
        ),
        personalization=PersonalizationStatus(available=True),
        source_status={},
        built_at=START,
    )


async def test_save_then_get_round_trips_the_context():
    repo = InMemorySessionContextRepository()
    await repo.save(_context())
    stored = await repo.get("sess-1")
    assert stored is not None
    assert stored.session_id == "sess-1"
    assert stored.user_id == "learner-1"


async def test_get_returns_none_for_an_unknown_session():
    repo = InMemorySessionContextRepository()
    assert await repo.get("never-stored") is None


async def test_delete_removes_the_context_and_reports_whether_it_existed():
    repo = InMemorySessionContextRepository()
    await repo.save(_context())
    assert await repo.delete("sess-1") is True
    assert await repo.get("sess-1") is None
    assert await repo.delete("sess-1") is False


async def test_entries_expire_after_the_configured_ttl():
    clock = MovableClock(START)
    repo = InMemorySessionContextRepository(ttl_hours=12, clock=clock)
    await repo.save(_context())

    clock.advance(hours=11, minutes=59)
    assert await repo.get("sess-1") is not None

    clock.advance(minutes=2)
    assert await repo.get("sess-1") is None


async def test_expired_entries_are_purged_so_the_store_does_not_grow():
    clock = MovableClock(START)
    repo = InMemorySessionContextRepository(ttl_hours=1, clock=clock)
    for index in range(5):
        await repo.save(_context(session_id=f"sess-{index}"))
    assert repo.size() == 5

    clock.advance(hours=2)
    await repo.save(_context(session_id="sess-new"))
    # The sweep on write removed the five expired entries.
    assert repo.size() == 1


async def test_save_overwrites_an_existing_session():
    repo = InMemorySessionContextRepository()
    await repo.save(_context(user_id="learner-1"))
    await repo.save(_context(user_id="learner-2"))
    stored = await repo.get("sess-1")
    assert stored is not None
    assert stored.user_id == "learner-2"
    assert repo.size() == 1
