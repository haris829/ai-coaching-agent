"""Mock question-history provider. Covers every scenario in the scope section 13.

The MALFORMED_RECORD scenario deliberately returns an item that is not a
``QuestionRecord``, simulating an adapter that passes an unparsable upstream row
through. The assembly service must drop it, record ``partial``, and still build a
context. This is the only place in the codebase that knowingly violates a type
annotation, and it does so to prove resilience.
"""

from __future__ import annotations

from datetime import timedelta
from enum import Enum
from typing import Any, cast

from uc02.domain.errors import ProviderUnavailable
from uc02.domain.models.enums import SourceName
from uc02.domain.models.provider_records import QuestionRecord
from uc02.domain.ports.providers import QuestionHistoryProvider
from uc02.infrastructure.providers.mocks.base import MOCK_EPOCH, RecordingMock

_TOPICS = (
    "offer-and-acceptance",
    "consideration",
    "remedies",
    "negligence",
    "evidence",
)


class HistoryScenario(str, Enum):
    EXACTLY_20 = "exactly_20"
    FEWER_THAN_20 = "fewer_than_20"
    ZERO = "zero"
    MORE_THAN_20_AVAILABLE = "more_than_20_available"
    UNAVAILABLE = "unavailable"
    MALFORMED_RECORD = "malformed_record"
    TIMEOUT = "timeout"


def build_questions(count: int) -> list[QuestionRecord]:
    """Deterministic question records, newest first, across several prior sessions."""
    return [
        QuestionRecord(
            question_id=f"q-{index:04d}",
            session_id=f"prior-session-{index // 4:02d}",
            asked_at=MOCK_EPOCH - timedelta(hours=index),
            topic_tag=_TOPICS[index % len(_TOPICS)],
            text=f"Mock question {index} body text that must never be logged or returned.",
        )
        for index in range(count)
    ]


class MockQuestionHistoryProvider(RecordingMock[HistoryScenario], QuestionHistoryProvider):
    #: How many records the MORE_THAN_20_AVAILABLE scenario hands back, ignoring
    #: ``limit`` on purpose so the server-side truncation is exercised.
    OVERSUPPLY_COUNT = 35

    def __init__(
        self,
        default_scenario: HistoryScenario = HistoryScenario.EXACTLY_20,
        overrides: dict[str, HistoryScenario] | None = None,
    ) -> None:
        super().__init__(default_scenario, overrides)
        self.observed_limits: list[int] = []

    async def get_recent_questions(self, user_id: str, limit: int) -> list[QuestionRecord]:
        scenario = self._record(user_id)
        self.observed_limits.append(limit)
        if scenario is HistoryScenario.TIMEOUT:
            await self._hang()
        if scenario is HistoryScenario.UNAVAILABLE:
            raise ProviderUnavailable(
                SourceName.QUESTION_HISTORY, "mock: history store connection reset"
            )
        if scenario is HistoryScenario.ZERO:
            return []
        if scenario is HistoryScenario.FEWER_THAN_20:
            return build_questions(7)
        if scenario is HistoryScenario.EXACTLY_20:
            return build_questions(20)
        if scenario is HistoryScenario.MALFORMED_RECORD:
            good = build_questions(3)
            malformed = cast(
                QuestionRecord, cast(Any, {"question_id": "q-bad", "asked_at": "not-a-date"})
            )
            return [good[0], malformed, good[1], good[2]]
        return build_questions(self.OVERSUPPLY_COUNT)
