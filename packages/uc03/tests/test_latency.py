"""Requirements 12 and 13 - thinking state, hard timeout, retryable response.

The two tests that exercise the *real* company thresholds (1.5s / 10s) take
real wall-clock time and are marked `slow`. They are not skipped by default:
the requirement is a real deadline, so the evidence should be a real one.
Scaled-down settings are used for the finer-grained assertions.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from uc03.adapters.mocks import InMemoryQuestionLogger, SlowAnswerGenerator
from uc03.adapters.rule_based import TemplateAnswerGenerator
from uc03.config import Settings
from uc03.domain.enums import ResponseStatus

from .conftest import ALICE_SESSION, build_service

QUESTION = "What is negligence in tort law?"

FAST_SETTINGS = Settings(thinking_after_ms=100, timeout_ms=400)


def test_default_thresholds_match_the_company_requirements():
    settings = Settings()
    assert settings.thinking_after_ms == 1_500
    assert settings.timeout_ms == 10_000
    assert settings.p95_target_ms == 3_000


async def test_fast_answer_emits_no_thinking_state(alice):
    svc = build_service(settings=FAST_SETTINGS)
    response = await svc.answer(
        question=QUESTION, session_id=ALICE_SESSION, principal=alice
    )
    assert response.status is ResponseStatus.ANSWERED
    assert response.meta.thinking_state_emitted is False
    assert response.meta.thinking_after_ms == 100


async def test_thinking_state_is_emitted_when_work_exceeds_the_threshold(alice):
    signals: list[float] = []

    async def on_thinking() -> None:
        signals.append(time.perf_counter())

    svc = build_service(
        generator=SlowAnswerGenerator(inner=TemplateAnswerGenerator(), delay=0.25),
        settings=FAST_SETTINGS,
    )
    response = await svc.answer(
        question=QUESTION,
        session_id=ALICE_SESSION,
        principal=alice,
        on_thinking=on_thinking,
    )
    assert response.status is ResponseStatus.ANSWERED
    assert response.meta.thinking_state_emitted is True
    assert len(signals) == 1, "thinking state signalled exactly once"


async def test_timeout_returns_a_safe_retryable_response(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(
        generator=SlowAnswerGenerator(inner=TemplateAnswerGenerator(), delay=5.0),
        logger=logger,
        settings=FAST_SETTINGS,
    )
    started = time.perf_counter()
    response = await svc.answer(
        question=QUESTION, session_id=ALICE_SESSION, principal=alice
    )
    elapsed = time.perf_counter() - started

    assert response.status is ResponseStatus.TIMEOUT
    assert response.retry_available is True
    assert response.parts is None, "no partial or hallucinated answer on timeout"
    assert response.clarification_question is None
    assert response.message
    assert response.follow_up_actions == ()
    # Cut off at the deadline rather than waiting for the slow dependency.
    assert elapsed < 1.0
    assert response.meta.thinking_state_emitted is True


async def test_timeout_is_logged_without_an_answer(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(
        generator=SlowAnswerGenerator(inner=TemplateAnswerGenerator(), delay=5.0),
        logger=logger,
        settings=FAST_SETTINGS,
    )
    await svc.answer(question=QUESTION, session_id=ALICE_SESSION, principal=alice)
    assert len(logger.records) == 1
    record = logger.last
    assert record.status is ResponseStatus.TIMEOUT
    assert record.answer is None
    assert record.question == QUESTION
    assert record.topic_tag is not None


async def test_slow_dependency_is_cancelled_at_the_deadline(alice):
    """The pipeline is actually cancelled, not left running in the background."""
    finished: list[str] = []

    class NeverFinishesGenerator:
        async def generate(self, request):  # noqa: ANN001, ANN202
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                finished.append("cancelled")
                raise
            finished.append("completed")

    svc = build_service(generator=NeverFinishesGenerator(), settings=FAST_SETTINGS)
    response = await svc.answer(
        question=QUESTION, session_id=ALICE_SESSION, principal=alice
    )
    assert response.status is ResponseStatus.TIMEOUT
    assert finished == ["cancelled"]


# --- Real company thresholds ---------------------------------------------


@pytest.mark.slow
async def test_real_1500ms_thinking_threshold(alice):
    """Evidence against the actual 1.5s requirement (takes ~2s)."""
    marks: list[float] = []
    started = time.perf_counter()

    async def on_thinking() -> None:
        marks.append(time.perf_counter() - started)

    svc = build_service(
        generator=SlowAnswerGenerator(inner=TemplateAnswerGenerator(), delay=2.0),
        settings=Settings(),
    )
    response = await svc.answer(
        question=QUESTION,
        session_id=ALICE_SESSION,
        principal=alice,
        on_thinking=on_thinking,
    )
    assert response.status is ResponseStatus.ANSWERED
    assert response.meta.thinking_state_emitted is True
    assert len(marks) == 1
    assert 1.4 <= marks[0] <= 1.9, f"thinking signal fired at {marks[0]:.3f}s"


@pytest.mark.slow
async def test_real_10_second_hard_timeout(alice):
    """Evidence against the actual 10s requirement (takes ~10s)."""
    logger = InMemoryQuestionLogger()
    svc = build_service(
        generator=SlowAnswerGenerator(inner=TemplateAnswerGenerator(), delay=30.0),
        logger=logger,
        settings=Settings(),
    )
    started = time.perf_counter()
    response = await svc.answer(
        question=QUESTION, session_id=ALICE_SESSION, principal=alice
    )
    elapsed = time.perf_counter() - started

    assert response.status is ResponseStatus.TIMEOUT
    assert response.retry_available is True
    assert response.parts is None
    assert 9.5 <= elapsed <= 11.0, f"cut off at {elapsed:.2f}s"
    assert response.meta.timeout_ms == 10_000
    assert logger.last.status is ResponseStatus.TIMEOUT
