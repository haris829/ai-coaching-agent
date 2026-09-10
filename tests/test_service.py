"""Orchestration: the batches, what happens when one of them fails, and cross-batch repeats.

These exercise the run without a database. The end-to-end path - resolve, generate, store, read
back - is in ``test_storage.py``, which needs a real PostgreSQL.
"""

from __future__ import annotations

import threading
import time

import pytest

from qgen.domain.generation import ANGLES, CourseBrief, stem_key
from qgen.errors import GenerationUnavailable
from qgen.service import MAX_CONCURRENCY, _ask, _collect
from tests.fakes import FakeLLM, questions_reply

BRIEF = CourseBrief(code="LL-34590", name="Criminology (Postgraduate)")


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


def test_one_small_request_is_one_call_with_no_angle():
    llm = FakeLLM(questions_reply("a?"))

    texts, failures = _ask(llm, BRIEF, 5, ())

    assert len(texts) == 1
    assert failures == []
    assert "focus on" not in llm.prompts[0]


def test_a_large_request_is_split_and_each_batch_gets_a_different_angle():
    """Identical prompts running at once produce near-identical questions."""
    llm = FakeLLM(*[questions_reply(f"q{n}?") for n in range(5)])

    _ask(llm, BRIEF, 50, ())

    assert len(llm.prompts) == 5
    used = {angle for angle in ANGLES if any(angle in prompt for prompt in llm.prompts)}
    assert len(used) == 5


def test_the_history_is_put_in_every_batch_prompt():
    llm = FakeLLM(*[questions_reply(f"q{n}?") for n in range(3)])

    _ask(llm, BRIEF, 30, ("What is actus reus?",))

    assert all("What is actus reus?" in prompt for prompt in llm.prompts)


def test_one_failed_batch_does_not_discard_the_others():
    """Throwing away twenty good questions because the third call was throttled is worse output
    for the same money. The shortfall is reported instead."""
    llm = FakeLLM(questions_reply("a?"), GenerationUnavailable(reason="HTTP_429"), questions_reply("c?"))

    texts, failures = _ask(llm, BRIEF, 30, ())

    assert len(texts) == 2
    assert [reason for reason, _ in failures] == ["a batch failed (HTTP_429)"]


def test_when_every_batch_fails_nothing_comes_back_and_the_errors_are_kept():
    llm = FakeLLM(
        GenerationUnavailable(reason="HTTP_429"),
        GenerationUnavailable(reason="TIMEOUT"),
    )

    texts, failures = _ask(llm, BRIEF, 20, ())

    assert texts == []
    assert len(failures) == 2
    # The exception itself is carried, not just its text, so the caller can re-raise it with its
    # own status code intact rather than flattening a throttle into a generic error.
    assert isinstance(failures[0][1], GenerationUnavailable)
    assert failures[0][1].status_code == 503


def test_no_more_than_five_calls_are_in_flight_at_once():
    """Five simultaneous requests are within this account's limit; a stream above it is
    throttled, and a throttle costs more time than the extra concurrency saves."""
    live = 0
    peak = 0
    lock = threading.Lock()

    class Counting(FakeLLM):
        def complete(self, prompt: str, *, max_tokens: int) -> str:
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.02)
            with lock:
                live -= 1
            return questions_reply("a?")

    _ask(Counting(), BRIEF, 50, ())

    assert peak <= MAX_CONCURRENCY


def test_the_batches_do_run_concurrently():
    class Slow(FakeLLM):
        def complete(self, prompt: str, *, max_tokens: int) -> str:
            time.sleep(0.15)
            return questions_reply("a?")

    started = time.monotonic()
    _ask(Slow(), BRIEF, 50, ())
    elapsed = time.monotonic() - started

    # Five sequential calls would be 0.75s; five concurrent ones are a little over 0.15s.
    assert elapsed < 0.5


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------


def test_questions_from_every_batch_are_kept():
    accepted, refusals = _collect([questions_reply("a?"), questions_reply("b?")], 10, frozenset())

    assert [q.question_text for q in accepted] == ["a?", "b?"]
    assert refusals == ()


def test_a_question_two_batches_both_wrote_is_kept_once():
    """The only place a cross-batch duplicate can be caught: neither model call saw the other."""
    accepted, refusals = _collect(
        [questions_reply("same?"), questions_reply("same?", "different?")], 10, frozenset()
    )

    assert [q.question_text for q in accepted] == ["same?", "different?"]
    assert dict(refusals) == {"a question repeated one already asked": 1}


def test_a_question_the_course_has_already_been_asked_is_dropped():
    history = frozenset({stem_key("Old question?")})

    accepted, _ = _collect([questions_reply("Old question?", "New question?")], 10, history)

    assert [q.question_text for q in accepted] == ["New question?"]


def test_collecting_stops_once_enough_have_been_accepted():
    accepted, _ = _collect([questions_reply("a?", "b?", "c?"), questions_reply("d?")], 2, frozenset())

    assert len(accepted) == 2


def test_refusals_from_every_batch_are_totalled_by_reason():
    # Answer "Z" names no option, so every one of these is refused rather than repaired.
    broken = questions_reply("a?", "b?", answer="Z")

    _, refusals = _collect([broken, broken], 10, frozenset())

    assert dict(refusals) == {"a question's answer was not one of A-D": 4}


@pytest.mark.parametrize("garbage", ["", "not json", '{"questions": []}'])
def test_a_batch_that_returned_nothing_usable_is_counted_not_ignored(garbage):
    accepted, refusals = _collect([garbage, questions_reply("good?")], 10, frozenset())

    assert len(accepted) == 1
    assert refusals != ()
