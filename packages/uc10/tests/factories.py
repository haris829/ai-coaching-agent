"""Test data builders."""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.canaries import canary_comment
from uc10.domain.enums import NaricLevel, RatingValue
from uc10.domain.ids import new_rating_id
from uc10.domain.models import RatingRecord

TOPIC = "contract_formation"


def rating(
    *,
    value: RatingValue | str,
    rated_at: datetime,
    topic_tag: str = TOPIC,
    user_id: str = "user_1",
    interaction_id: str | None = None,
    comment: str | None = None,
    rating_id: str | None = None,
    superseded_by: str | None = None,
) -> RatingRecord:
    rating_id = rating_id or new_rating_id()
    return RatingRecord(
        rating_id=rating_id,
        interaction_id=interaction_id or f"int_{rating_id[-8:]}",
        session_id="sess_1",
        user_id=user_id,
        rating=RatingValue(value),
        comment=comment,
        question_text="MOCK_QUESTION_TEXT_DO_NOT_LOG",
        response_text="MOCK_RESPONSE_TEXT_DO_NOT_LOG",
        naric_level=NaricLevel.LEVEL_7,
        session_mode="coaching",
        topic_tag=topic_tag,
        rated_at=rated_at,
        superseded_by=superseded_by,
    )


def rating_set(
    *,
    total: int,
    downs: int,
    now: datetime,
    topic_tag: str = TOPIC,
    step_minutes: int = 1,
    start_index: int = 0,
) -> list[RatingRecord]:
    """``total`` current ratings on one topic, ``downs`` of them thumbs down.

    Ratings are spread backwards from ``now`` but stay well inside a 7-day window, so the
    only thing under test is the rate and the sample size.
    """
    records = []
    for offset in range(total):
        index = start_index + offset
        value = RatingValue.DOWN if offset < downs else RatingValue.UP
        records.append(
            rating(
                value=value,
                rated_at=now - timedelta(minutes=offset * step_minutes),
                topic_tag=topic_tag,
                user_id=f"user_{index}",
                interaction_id=f"int_{topic_tag}_{index}",
                comment=canary_comment() if value is RatingValue.DOWN and index % 3 == 0 else None,
            )
        )
    return records
