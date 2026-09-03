"""Splitting a large request into concurrent batches.

Fifty questions in one call means waiting for fifty questions' worth of sequential tokens. Five
batches of ten, running at once, is roughly one batch's worth. These tests cover the three things
that split can get wrong: losing questions, duplicating them, or letting one bad batch sink the run.

The model is a stub throughout — the point here is the orchestration, and a real call would make
these tests slow and non-deterministic.
"""

from __future__ import annotations

import json
import threading

import pytest

from app.modules.quiz_generation.domain.generation import ANGLES, CourseBrief
from app.modules.quiz_generation.integration.llm import QuestionGenerationFailedError
from app.modules.quiz_generation.services.generation_service import (
    MAX_CONCURRENT_BATCHES,
    QUESTIONS_PER_BATCH,
    QuestionGenerationService,
)

BRIEF = CourseBrief(course_id="c-1", name="Contract Law")


def _reply(texts: list[str]) -> str:
    return json.dumps(
        {
            "questions": [
                {
                    "question": text,
                    "options": {
                        "A": f"{text} first",
                        "B": f"{text} second",
                        "C": f"{text} third",
                        "D": f"{text} fourth",
                    },
                    "answer": "B",
                    "explanation": "Because.",
                }
                for text in texts
            ]
        }
    )


class RecordingGenerator:
    """Distinct questions per call, recording the prompts and the concurrency actually reached."""

    configured = True

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self._lock = threading.Lock()
        self._calls = 0
        self._live = 0
        self.peak_concurrency = 0

    def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
        with self._lock:
            self.prompts.append(prompt)
            self._calls += 1
            call = self._calls
            self._live += 1
            self.peak_concurrency = max(self.peak_concurrency, self._live)
        try:
            # Text unique to this call, so nothing deduplicates by accident.
            return _reply([f"Batch {call} question {n}?" for n in range(QUESTIONS_PER_BATCH)])
        finally:
            with self._lock:
                self._live -= 1


class CollectingSink:
    """Stores everything and hands back an id, so the service's own accounting is what is tested."""

    def __init__(self) -> None:
        self.stored: list[str] = []

    def store(self, question, brief, *, actor=None, topics=()):  # noqa: ANN001, ANN201, ARG002
        self.stored.append(question.question_text)
        return f"id-{len(self.stored)}", None


# ---------------------------------------------------------------------------
# The split itself
# ---------------------------------------------------------------------------


class TestThePlan:
    @pytest.mark.parametrize("wanted", [1, 2, 5, 9, 10, 11, 12, 19, 20, 21, 25, 37, 50])
    def test_the_batches_always_add_up_to_what_was_asked_for(self, wanted: int) -> None:
        """The property that stops the split quietly losing or inventing questions."""
        plan = QuestionGenerationService._plan(wanted)

        assert sum(plan) == wanted
        assert all(size >= 1 for size in plan)

    def test_a_small_request_is_a_single_call(self) -> None:
        # Splitting five questions would pay the fixed cost of several calls for no gain.
        assert QuestionGenerationService._plan(QUESTIONS_PER_BATCH) == (QUESTIONS_PER_BATCH,)
        assert len(QuestionGenerationService._plan(3)) == 1

    def test_a_remainder_of_one_is_folded_into_the_previous_batch(self) -> None:
        """A whole extra round trip for one question is not worth its fixed cost."""
        assert QuestionGenerationService._plan(QUESTIONS_PER_BATCH + 1) == (
            QUESTIONS_PER_BATCH + 1,
        )
        assert QuestionGenerationService._plan(2 * QUESTIONS_PER_BATCH + 1) == (
            QUESTIONS_PER_BATCH,
            QUESTIONS_PER_BATCH + 1,
        )

    def test_fifty_becomes_five_batches(self) -> None:
        assert QuestionGenerationService._plan(50) == (10, 10, 10, 10, 10)


# ---------------------------------------------------------------------------
# Running them
# ---------------------------------------------------------------------------


class TestRunningTheBatches:
    def test_the_batches_really_run_at_the_same_time(self) -> None:
        """The whole reason the split exists. Sequential batches would be no faster.

        Proved with a barrier rather than by observing overlap: a stub that returns instantly
        finishes each batch before the next is dispatched, so a peak-concurrency counter reads 1
        even when the pool is working correctly. A barrier makes the batches *depend* on each other
        being in flight — five sequential calls can never all reach it, so the barrier times out and
        this test fails loudly instead of passing on a technicality.
        """
        barrier = threading.Barrier(5, timeout=10)

        class BlocksUntilAllFive:
            configured = True

            def __init__(self) -> None:
                self._lock = threading.Lock()
                self._calls = 0
                self.broke = False

            def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
                with self._lock:
                    self._calls += 1
                    call = self._calls
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    self.broke = True
                return _reply([f"Call {call} question {n}?" for n in range(10)])

        generator = BlocksUntilAllFive()

        outcome = QuestionGenerationService(generator, CollectingSink()).generate(
            BRIEF, count=50
        )

        assert generator.broke is False, "the batches did not run concurrently"
        assert generator._calls == 5
        assert outcome.created == 50

    def test_concurrency_is_capped(self) -> None:
        """A ceiling, so a large request cannot open a connection per batch and be rate-limited."""
        generator = RecordingGenerator()

        QuestionGenerationService(generator, CollectingSink()).generate(BRIEF, count=50)

        assert generator.peak_concurrency <= MAX_CONCURRENT_BATCHES

    def test_each_batch_is_asked_from_a_different_angle(self) -> None:
        """Identical prompts would produce near-identical questions, most of them discarded."""
        generator = RecordingGenerator()

        QuestionGenerationService(generator, CollectingSink()).generate(BRIEF, count=50)

        focuses = [
            line
            for prompt in generator.prompts
            for line in prompt.splitlines()
            if line.startswith("For these questions, focus on")
        ]
        assert len(focuses) == 5
        assert len(set(focuses)) == 5, "two batches were given the same angle"
        assert all(any(angle in focus for angle in ANGLES) for focus in focuses)

    def test_a_single_batch_request_is_given_no_angle(self) -> None:
        # There is nothing to differentiate it from, and narrowing it would only narrow the quiz.
        generator = RecordingGenerator()

        QuestionGenerationService(generator, CollectingSink()).generate(BRIEF, count=5)

        assert "focus on" not in generator.prompts[0]

    def test_no_more_than_what_was_asked_for_is_kept(self) -> None:
        """The stub returns a full batch every time; a 25-question request must still yield 25."""
        sink = CollectingSink()

        outcome = QuestionGenerationService(RecordingGenerator(), sink).generate(
            BRIEF, count=25
        )

        assert outcome.created == 25
        assert len(sink.stored) == 25


# ---------------------------------------------------------------------------
# The two ways a split run degrades
# ---------------------------------------------------------------------------


class TestDegrading:
    def test_a_question_repeated_across_batches_is_dropped_and_counted(self) -> None:
        """Each batch is blind to the others, so the same point can come back twice.

        The parser refuses a repeat inside one reply; this refuses one across replies. Counting it
        as rejected keeps a low yield visible rather than mysterious.
        """

        class SameEveryTime:
            configured = True

            def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
                return _reply([f"The identical question {n}?" for n in range(10)])

        sink = CollectingSink()

        outcome = QuestionGenerationService(SameEveryTime(), sink).generate(BRIEF, count=50)

        # Ten distinct questions across five identical batches.
        assert outcome.created == 10
        assert outcome.rejected == 40
        # One reason for both cases, because from the learner's side they are the same fault:
        # a paper containing a question this course has already been given.
        assert any("already asked for this course" in reason for reason in outcome.reasons)
        assert len(set(sink.stored)) == 10

    def test_one_failing_batch_costs_only_itself(self) -> None:
        """Losing one batch of five should cost forty questions, not the whole request."""

        class OneBatchFails:
            configured = True

            def __init__(self) -> None:
                self._lock = threading.Lock()
                self._calls = 0

            def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
                with self._lock:
                    self._calls += 1
                    call = self._calls
                if call == 2:
                    raise RuntimeError("the provider hung up")
                return _reply([f"Call {call} question {n}?" for n in range(10)])

        outcome = QuestionGenerationService(OneBatchFails(), CollectingSink()).generate(
            BRIEF, count=50
        )

        assert outcome.created == 40
        assert outcome.rejected == 10
        assert any("a batch failed" in reason for reason in outcome.reasons)

    def test_every_batch_failing_raises_rather_than_returning_nothing(self) -> None:
        """Asked for fifty and got none: the caller must be told why, not handed an empty success."""

        class AlwaysFails:
            configured = True

            def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
                raise RuntimeError("the provider is down")

        with pytest.raises(QuestionGenerationFailedError) as failure:
            QuestionGenerationService(AlwaysFails(), CollectingSink()).generate(
                BRIEF, count=50
            )

        assert "a batch failed" in str(failure.value.log_context)
