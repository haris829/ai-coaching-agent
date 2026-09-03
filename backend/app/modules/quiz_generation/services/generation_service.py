"""Generate questions for a course, and store the ones that survive validation.

    course  ->  prompt  ->  model  ->  parse  ->  UC-02's validator  ->  DRAFT questions

The service is thin because every hard decision belongs somewhere else and is already made there:
the prompt and the parse are pure functions in ``domain/generation.py``, the model is behind a
port, and so is the question bank — see ``integration/question_bank.py``, which is also where the
reason for going through UC-02's validator rather than around it is set out.

EVERYTHING IS A DRAFT
---------------------
Questions are created with ``status=DRAFT``. UC-02 never delivers a DRAFT question — only ACTIVE
ones are drawn for an attempt — so generation cannot put an unreviewed question in front of a
learner. Activating them is a separate, deliberate administrative act.

For a professional qualification that is not optional caution. A model can produce a question that
is fluent, plausible and wrong about the law, and the learner who fails on it is being certified
against a mistake nobody read.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.modules.quiz_generation.domain.generation import (
    ANGLES,
    MAX_AVOID_STEMS,
    MAX_QUESTIONS_PER_REQUEST,
    CourseBrief,
    GeneratedQuestion,
    build_prompt,
    parse_questions,
)
from app.modules.quiz_generation.integration.llm import (
    QuestionGenerationFailedError,
    QuestionGeneratorLLM,
)
from app.modules.quiz_generation.integration.question_bank import (
    NoHistory,
    QuestionHistory,
    QuestionSink,
)

logger = get_logger(__name__)

#: Roughly what twenty four-option questions with explanations costs in output tokens, with room
#: for the model to be wordier than expected. Too small truncates the JSON and wastes the call.
TOKENS_PER_QUESTION = 320
MIN_OUTPUT_TOKENS = 1500

#: How many questions to ask for in one call.
#:
#: A model writes its output one token at a time, so asking for fifty questions in a single request
#: means waiting for around sixteen thousand sequential tokens — slow enough that the request has to
#: be given a two-minute timeout and slow enough to feel broken. Splitting into batches that run at
#: the same time turns that into roughly the cost of the slowest batch.
#:
#: Ten is a compromise. Smaller batches finish sooner but multiply the fixed cost of every call and
#: make cross-batch duplication more likely, since each batch is blind to what the others wrote.
QUESTIONS_PER_BATCH = 10

#: How many batches may be in flight at once. A ceiling, not a target: it exists so a fifty-question
#: request cannot open fifty simultaneous connections to the provider and earn a rate-limit refusal
#: instead of a quiz.
MAX_CONCURRENT_BATCHES = 5


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """What one generation run produced."""

    course_id: str
    requested: int
    #: Ids of the questions actually written, all of them DRAFT.
    question_ids: tuple[str, ...] = field(default_factory=tuple)
    #: Returned by the model but refused by the parser or by UC-02's validator.
    rejected: int = 0
    #: Why, in the parser's or the validator's words. Names and reasons only.
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def created(self) -> int:
        return len(self.question_ids)


class QuestionGenerationService:
    """Generates draft questions for one course."""

    __slots__ = ("_generator", "_sink", "_history")

    def __init__(
        self,
        generator: QuestionGeneratorLLM,
        sink: QuestionSink,
        history: QuestionHistory | None = None,
    ) -> None:
        self._generator = generator
        self._sink = sink
        # Optional: without one, every generation starts from a blank slate and may repeat itself.
        self._history = history or NoHistory()

    def generate(
        self,
        brief: CourseBrief,
        *,
        count: int,
        actor: str | None = None,
        topics: tuple[str, ...] = (),
    ) -> GenerationOutcome:
        """Ask for ``count`` questions, keep the ones that validate, report the rest.

        A large request is split into batches that call the model **concurrently**, because token
        generation is sequential: fifty questions in one request is fifty questions' worth of
        waiting, while five batches of ten is roughly one batch's worth.

        Only the model calls are concurrent. Storing is done afterwards, on this thread, because the
        sink writes through a SQLAlchemy session and a session is not safe to share between threads.
        """
        wanted = max(1, min(int(count), MAX_QUESTIONS_PER_REQUEST))
        # What this course has already been asked. Fetched once and shared by every batch, so all
        # of them avoid the same history rather than each discovering it separately.
        already = self._history.previous_stems(
            course_ref=brief.course_id or None,
            topic=brief.name,
            limit=MAX_AVOID_STEMS,
        )
        accepted, rejected, reasons = self._ask(brief, wanted, already)

        if not accepted:
            # Nothing usable came back. Raising rather than returning an empty success: a caller
            # that asked for twenty questions and silently got none would have no idea why.
            raise QuestionGenerationFailedError(
                reason="; ".join(reasons) or "no question survived parsing",
                accepted=0,
                wanted=wanted,
            )

        stored: list[str] = []
        for question in accepted:
            question_id, reason = self._sink.store(
                question, brief, actor=actor, topics=topics
            )
            if question_id is None:
                if reason and reason not in reasons:
                    reasons.append(reason)
                continue
            stored.append(question_id)

        total_rejected = rejected + (len(accepted) - len(stored))
        logger.info(
            "quiz_generation.completed",
            extra={
                "course_ref": brief.course_id,
                "requested": wanted,
                "created": len(stored),
                "rejected": total_rejected,
                "batches": len(self._plan(wanted)),
                "avoided": len(already),
            },
        )
        return GenerationOutcome(
            course_id=brief.course_id,
            requested=wanted,
            question_ids=tuple(stored),
            rejected=total_rejected,
            reasons=tuple(reasons),
        )

    # ---- asking the model -------------------------------------------------

    @staticmethod
    def _plan(wanted: int) -> tuple[int, ...]:
        """How to split ``wanted`` into batch sizes.

        Whole batches of :data:`QUESTIONS_PER_BATCH`, plus whatever is left over. A remainder of one
        is folded into the previous batch rather than sent as a batch of its own — a whole extra
        round trip for a single question is not worth its fixed cost.
        """
        if wanted <= QUESTIONS_PER_BATCH:
            return (wanted,)
        sizes = [QUESTIONS_PER_BATCH] * (wanted // QUESTIONS_PER_BATCH)
        remainder = wanted % QUESTIONS_PER_BATCH
        if remainder == 1:
            sizes[-1] += 1
        elif remainder:
            sizes.append(remainder)
        return tuple(sizes)

    def _ask(
        self, brief: CourseBrief, wanted: int, already: tuple[str, ...] = ()
    ) -> tuple[list[GeneratedQuestion], int, list[str]]:
        """Every question the model produced that could be vouched for, across all batches.

        Deduplicated **across** batches. Each batch is blind to what the others wrote — that is what
        makes them concurrent — so the same point can come back twice even though every batch was
        given a different angle. The parser already refuses a repeat inside one reply; this refuses
        one across replies, counting it as rejected so a low yield stays visible.

        A batch that fails outright costs only itself. Losing one batch of a five-batch run should
        cost forty questions out of fifty, not the whole request.

        ``already`` is what this course has been asked before. It goes into every batch's prompt and
        is also seeded into the duplicate check, so a question that repeats an earlier *run* is
        refused on the same footing as one that repeats an earlier batch.
        """
        plan = self._plan(wanted)

        def one(index: int, size: int) -> tuple[str | None, str | None]:
            prompt = build_prompt(
                brief,
                size,
                # No angle for a single-batch request: there is nothing to differentiate it from.
                angle=ANGLES[index % len(ANGLES)] if len(plan) > 1 else None,
                avoid=already,
            )
            try:
                text = self._generator.complete(
                    prompt, max_tokens=max(MIN_OUTPUT_TOKENS, size * TOKENS_PER_QUESTION)
                )
            except Exception as error:  # noqa: BLE001 - one batch must not sink the run
                return None, f"a batch failed: {type(error).__name__}"
            return text, None

        if len(plan) == 1:
            replies = [one(0, plan[0])]
        else:
            with ThreadPoolExecutor(
                max_workers=min(MAX_CONCURRENT_BATCHES, len(plan))
            ) as pool:
                replies = list(
                    pool.map(lambda pair: one(*pair), enumerate(plan))
                )

        accepted: list[GeneratedQuestion] = []
        reasons: list[str] = []
        rejected = 0
        # Seeded with what has already been asked, so an earlier run's question is refused exactly
        # as an earlier batch's would be. The prompt asks the model not to repeat itself; this is
        # what happens when it does anyway.
        seen: set[str] = {stem.strip().casefold() for stem in already if stem}

        for (text, failure), size in zip(replies, plan, strict=True):
            if failure is not None:
                rejected += size
                if failure not in reasons:
                    reasons.append(failure)
                continue
            report = parse_questions(text or "", wanted=size)
            rejected += report.rejected
            for reason in report.reasons:
                if reason not in reasons:
                    reasons.append(reason)
            for question in report.accepted:
                if len(accepted) >= wanted:
                    break
                key = question.question_text.strip().casefold()
                if key in seen:
                    rejected += 1
                    note = "a question repeated one already asked for this course"
                    if note not in reasons:
                        reasons.append(note)
                    continue
                seen.add(key)
                accepted.append(question)

        return accepted, rejected, reasons
