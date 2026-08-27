"""Fixture builders shared across the test suite.

These build :class:`SessionData` directly, so a test about grounding does not
have to route through the providers, and a test about the service can still use
the same shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.domain.enums import (
    NaricLevel,
    NaricLevelSource,
    SessionStatus,
    SourceStatus,
)
from uc09_summary.domain.grounding import SessionData
from uc09_summary.domain.models import (
    InteractionRecord,
    Resource,
    SessionRecord,
    Suggestion,
)

UTC = UTC
BASE = S.BASE


def make_session(
    session_id: str = "sess-test",
    *,
    user_id: str = S.OWNER_USER_ID,
    status: SessionStatus = SessionStatus.COMPLETED,
    naric: NaricLevel = NaricLevel.LEVEL_7,
    duration_minutes: int | None = 47,
    display_name: str = "Amara Osei",
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        user_id=user_id,
        user_display_name=display_name,
        started_at=BASE,
        ended_at=BASE + timedelta(minutes=duration_minutes) if duration_minutes else None,
        status=status,
        naric_level=naric,
        naric_level_source=NaricLevelSource.RETRIEVED,
        naric_level_status=SourceStatus.AVAILABLE,
        course_completion_percent=62,
        course_title="Employment Law Practice",
    )


def make_session_data(
    *,
    session: SessionRecord | None = None,
    interactions: tuple[InteractionRecord, ...] = (),
    citations: tuple[Resource, ...] = (),
    gap_suggestions: tuple[Suggestion, ...] | None = None,
    covers_through: datetime | None = None,
) -> SessionData:
    session = session or make_session()
    return SessionData(
        session=session,
        interactions=interactions,
        citations=citations,
        gap_suggestions=gap_suggestions,
        covers_interactions_through=covers_through
        or (session.ended_at or BASE + timedelta(hours=1)),
    )


def multi_topic_session_data() -> SessionData:
    """The multi-topic scenario, with citations and a gap report."""
    return make_session_data(
        session=make_session(S.SESSION_COMPLETE),
        interactions=S.INTERACTIONS[S.SESSION_COMPLETE],
        citations=S.CITATIONS[S.SESSION_COMPLETE],
        gap_suggestions=S.GAP_SUGGESTIONS[S.OWNER_USER_ID],
    )


def single_topic_session_data() -> SessionData:
    """One topic, four concepts. Depth, not breadth."""
    return make_session_data(
        session=make_session(S.SESSION_SINGLE_TOPIC, duration_minutes=31),
        interactions=S.INTERACTIONS[S.SESSION_SINGLE_TOPIC],
        citations=S.CITATIONS[S.SESSION_SINGLE_TOPIC],
        gap_suggestions=(),
    )


def no_citation_session_data() -> SessionData:
    """A session in which nothing was cited."""
    return make_session_data(
        session=make_session(S.SESSION_NO_CITATIONS, duration_minutes=22),
        interactions=S.INTERACTIONS[S.SESSION_NO_CITATIONS],
        citations=(),
        gap_suggestions=(),
    )


def one_interaction_session_data() -> SessionData:
    """A session with a single logged interaction and one concept."""
    return make_session_data(
        session=make_session(S.SESSION_ONE_INTERACTION, duration_minutes=6),
        interactions=S.INTERACTIONS[S.SESSION_ONE_INTERACTION],
        citations=S.CITATIONS[S.SESSION_ONE_INTERACTION],
        gap_suggestions=None,
    )
