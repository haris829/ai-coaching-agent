"""The generation run, from a course name to rows in the database.

The order of the steps is the whole design: resolve the course, read what it has already been
asked, ask for more than is needed, split the ask into concurrent batches with different angles,
refuse everything that cannot be vouched for, and only then write.

WHAT IS ASKED FOR AND WHAT IS STORED ARE DIFFERENT NUMBERS
-----------------------------------------------------------
Repeats are dropped and nothing backfills them. Ten asked for on a course with history can be
seven stored, so the ask carries headroom and :class:`RunOutcome` reports both numbers. Rounding
the shortfall away in the report would hide the one thing an operator needs to see.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import psycopg

from . import catalogue, storage
from .domain.generation import (
    ANGLES,
    MAX_QUESTIONS_PER_REQUEST,
    CourseBrief,
    GeneratedQuestion,
    batch_sizes,
    build_prompt,
    parse_questions,
    with_headroom,
)
from .errors import GenerationFailed, InvalidRequest
from .llm import QuestionLLM, max_tokens_for

#: Simultaneous model calls. Five is what this account tolerates; a sustained stream above it is
#: throttled, and a throttle costs more time than the concurrency saves.
MAX_CONCURRENCY = 5


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What actually happened, in the terms the report has to be written in."""

    run_id: int
    course_ref: str
    #: The code of the course that matched, or ``None``. A caller must be able to tell a match
    #: from a silent miss, because the miss means the questions came from four words.
    matched_code: str | None
    course_title: str
    grounding: str
    requested: int
    asked_for: int
    stored: int
    question_ids: list[int] = field(default_factory=list)
    refusals: tuple[tuple[str, int], ...] = ()
    #: Batches that never came back, by reason. Non-empty here and a short run below is one fact,
    #: not two: the shortfall is a provider problem, not a problem with the questions.
    batch_failures: tuple[tuple[str, int], ...] = ()

    @property
    def refused(self) -> int:
        return sum(count for _, count in self.refusals)

    @property
    def short(self) -> bool:
        return self.stored < self.requested

    @property
    def throttled(self) -> bool:
        return any("429" in reason for reason, _ in self.batch_failures)


def generate(
    conn: psycopg.Connection,
    llm: QuestionLLM,
    *,
    course_ref: str,
    count: int,
    model_name: str = "",
) -> RunOutcome:
    """Generate and store questions for one course.

    Raises rather than storing a partial result when the model cannot be reached at all, or when
    nothing it returned could be vouched for. The connection is rolled back by
    :func:`qgen.db.connect` in that case, so "nothing was saved" is true of the run row as well
    as the questions - a half-written run looks exactly like a complete short one to whoever
    reads the table next.
    """
    if count < 1 or count > MAX_QUESTIONS_PER_REQUEST:
        raise InvalidRequest(
            f"count must be between 1 and {MAX_QUESTIONS_PER_REQUEST}", reason="BAD_COUNT"
        )

    storage.ensure_schema(conn)

    course = catalogue.find_course(conn, course_ref)
    brief = catalogue.brief_for(course_ref, course)
    if not brief.name:
        raise InvalidRequest("no course reference was given", reason="NO_COURSE_REF")

    history = storage.previously_asked(conn, brief)
    asked_for = with_headroom(count, has_history=bool(history))

    run_id = storage.open_run(
        conn,
        course_ref=course_ref,
        brief=brief,
        requested=count,
        asked_for=asked_for,
        model=model_name,
    )

    texts, batch_failures = _ask(llm, brief, asked_for, history)
    if not texts:
        # Every batch failed. The first failure is re-raised as it was classified - a throttle
        # stays a 503 - so the caller is told to wait rather than to read the prompt.
        raise batch_failures[0][1]

    accepted, refusals = _collect(texts, asked_for, storage.history_keys(history))
    if not accepted:
        raise GenerationFailed(reason="NO_USABLE_QUESTIONS")

    keep = accepted[:count]
    question_ids = storage.store_questions(
        conn, run_id=run_id, course_ref=course_ref, brief=brief, questions=keep
    )
    failures = _tally(reason for reason, _ in batch_failures)
    storage.close_run(
        conn,
        run_id,
        stored=len(question_ids),
        refused=sum(n for _, n in refusals),
        refusals=refusals + failures,
        status="OK" if len(question_ids) >= count else "SHORT",
    )

    return RunOutcome(
        run_id=run_id,
        course_ref=course_ref,
        matched_code=brief.code,
        course_title=brief.name,
        grounding=brief.grounding,
        requested=count,
        asked_for=asked_for,
        stored=len(question_ids),
        question_ids=question_ids,
        refusals=refusals,
        batch_failures=failures,
    )


def _ask(
    llm: QuestionLLM,
    brief: CourseBrief,
    asked_for: int,
    history: tuple[str, ...],
) -> tuple[list[str], list[tuple[str, Exception]]]:
    """The model calls, run concurrently. Returns the replies, and the batches that failed.

    A batch that fails does not fail the run. Four batches out of five is a short paper, which is
    reported as short; discarding forty good questions because the fifth call was throttled would
    be worse output for the same money.
    """
    sizes = batch_sizes(asked_for)
    prompts = [
        build_prompt(
            brief,
            size,
            # One angle per batch, cycled. Without this, concurrent batches write the same
            # questions from the same obvious corner of the syllabus.
            angle=ANGLES[index % len(ANGLES)] if len(sizes) > 1 else None,
            avoid=history,
        )
        for index, size in enumerate(sizes)
    ]

    def call(item: tuple[int, str]) -> tuple[int, str | Exception]:
        index, prompt = item
        try:
            return index, llm.complete(prompt, max_tokens=max_tokens_for(sizes[index]))
        except Exception as exc:
            return index, exc

    if len(prompts) == 1:
        results = [call((0, prompts[0]))]
    else:
        with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENCY, len(prompts))) as pool:
            results = list(pool.map(call, enumerate(prompts)))

    texts: list[str] = []
    failures: list[tuple[str, Exception]] = []
    for _, result in sorted(results, key=lambda pair: pair[0]):
        if isinstance(result, Exception):
            reason = getattr(result, "reason", "") or type(result).__name__
            failures.append((f"a batch failed ({reason})", result))
        else:
            texts.append(result)
    return texts, failures


def _collect(
    texts: list[str],
    wanted: int,
    already_asked: frozenset[str],
) -> tuple[tuple[GeneratedQuestion, ...], tuple[tuple[str, int], ...]]:
    """Parse every reply into one set of questions, with no repeats between batches.

    The seen-set carries forward from batch to batch, which is the only place a cross-batch
    duplicate can be caught: each batch was written by a model that could not see the others.
    """
    accepted: list[GeneratedQuestion] = []
    reasons: list[str] = []
    seen = set(already_asked)

    for text in texts:
        remaining = wanted - len(accepted)
        if remaining <= 0:
            break
        report = parse_questions(text, wanted=remaining, already_asked=frozenset(seen))
        for question in report.accepted:
            seen.add(question.key)
            accepted.append(question)
        for reason, count in report.refusals:
            reasons.extend([reason] * count)

    return tuple(accepted), _tally(reasons)


def _tally(reasons) -> tuple[tuple[str, int], ...]:
    """Count reasons, keeping first-seen order so the report reads the way the run happened."""
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    return tuple(counts.items())
