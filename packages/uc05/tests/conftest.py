"""Shared test fixtures.

Everything here is offline and deterministic: no network, no API key, no cost,
no sleeps that a slow machine could turn into a flake.

``build_service`` is the workhorse.  It composes a real ``SocraticService``
over real in-memory repositories and whichever fakes a test needs, so service
tests exercise the actual orchestration rather than a stub of it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

import pytest

from uc05.adapters.fake.generators import (
    FakeAnswerGenerator,
    FakeGuidingQuestionGenerator,
)
from uc05.adapters.fake.intent import MockIntentClassifier
from uc05.adapters.fake.learner_context import MockLearnerContextProvider
from uc05.adapters.memory.repositories import (
    InMemoryDialogueRepository,
    InMemoryInteractionLogRepository,
    InMemorySessionModeRepository,
)
from uc05.application.logging_config import LOGGER_NAME, configure_logging
from uc05.application.socratic_service import SocraticService
from uc05.config import Settings, load_settings

USER = "learner-1"
OTHER_USER = "learner-2"
SESSION = "session-abc"

QUESTION = "When is a contract formed, and what does consideration require?"


@dataclass
class Harness:
    """A composed service plus direct handles on everything behind it."""

    service: SocraticService
    settings: Settings
    guiding: FakeGuidingQuestionGenerator
    answers: FakeAnswerGenerator
    intents: MockIntentClassifier
    context: MockLearnerContextProvider
    dialogues: InMemoryDialogueRepository
    modes: InMemorySessionModeRepository
    interactions: InMemoryInteractionLogRepository

    async def enable(self, session_id: str = SESSION, user_id: str = USER) -> None:
        await self.service.set_mode(session_id, user_id, True)

    async def start(
        self,
        question: str = QUESTION,
        session_id: str = SESSION,
        user_id: str = USER,
    ):
        return await self.service.ask(
            session_id=session_id, user_id=user_id, question_text=question
        )

    async def say(self, dialogue_id: str, message: str, user_id: str = USER):
        return await self.service.reply(
            dialogue_id=dialogue_id, user_id=user_id, message=message
        )

    async def records(self, session_id: str = SESSION):
        return await self.interactions.list_for_session(session_id)


def build_service(
    *,
    guiding_scenario: str = "normal",
    guiding_script: list[str] | None = None,
    answer_scenario: str = "well_formed",
    answer_script: list[str] | None = None,
    context_scenario: str = "level_5",
    context_script: list[str] | None = None,
    intent_force: Any = None,
    intent_script: list[Any] | None = None,
    intent_failure: str | None = None,
    **setting_overrides: Any,
) -> Harness:
    settings = load_settings(**setting_overrides)

    guiding = FakeGuidingQuestionGenerator(
        scenario=guiding_scenario, script=guiding_script
    )
    answers = FakeAnswerGenerator(scenario=answer_scenario, script=answer_script)
    intents = MockIntentClassifier(
        force=intent_force, script=intent_script, failure=intent_failure
    )
    context = MockLearnerContextProvider(
        scenario=context_scenario, script=context_script
    )
    dialogues = InMemoryDialogueRepository()
    modes = InMemorySessionModeRepository()
    interactions = InMemoryInteractionLogRepository()

    service = SocraticService(
        settings=settings,
        learner_context=context,
        guiding_generator=guiding,
        answer_generator=answers,
        intent_classifier=intents,
        dialogues=dialogues,
        modes=modes,
        interactions=interactions,
    )
    return Harness(
        service=service,
        settings=settings,
        guiding=guiding,
        answers=answers,
        intents=intents,
        context=context,
        dialogues=dialogues,
        modes=modes,
        interactions=interactions,
    )


@pytest.fixture
def harness() -> Harness:
    return build_service()


@dataclass
class CapturedLogs:
    """Every structured log line emitted during a block, as dicts."""

    records: list[logging.LogRecord] = field(default_factory=list)

    @property
    def payloads(self) -> list[dict[str, Any]]:
        return [
            record.uc05
            for record in self.records
            if isinstance(getattr(record, "uc05", None), dict)
        ]

    def rendered(self) -> str:
        """Everything a log aggregator would receive, as one blob to search."""
        import json

        return "\n".join(
            json.dumps(payload, default=str) for payload in self.payloads
        ) + "\n" + "\n".join(record.getMessage() for record in self.records)


@pytest.fixture
def captured_logs() -> Iterator[CapturedLogs]:
    """Capture UC-05's structured logs without touching the root logger."""
    configure_logging("DEBUG")
    logger = logging.getLogger(LOGGER_NAME)
    captured = CapturedLogs()

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.records.append(record)

    handler = _Collector()
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
