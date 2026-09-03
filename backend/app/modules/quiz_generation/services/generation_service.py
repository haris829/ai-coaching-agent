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

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.modules.quiz_generation.domain.generation import (
    MAX_QUESTIONS_PER_REQUEST,
    CourseBrief,
    build_prompt,
    parse_questions,
)
from app.modules.quiz_generation.integration.llm import (
    QuestionGenerationFailedError,
    QuestionGeneratorLLM,
)
from app.modules.quiz_generation.integration.question_bank import QuestionSink

logger = get_logger(__name__)

#: Roughly what twenty four-option questions with explanations costs in output tokens, with room
#: for the model to be wordier than expected. Too small truncates the JSON and wastes the call.
TOKENS_PER_QUESTION = 320
MIN_OUTPUT_TOKENS = 1500


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

    __slots__ = ("_generator", "_sink")

    def __init__(self, generator: QuestionGeneratorLLM, sink: QuestionSink) -> None:
        self._generator = generator
        self._sink = sink

    def generate(
        self,
        brief: CourseBrief,
        *,
        count: int,
        actor: str | None = None,
        topics: tuple[str, ...] = (),
    ) -> GenerationOutcome:
        """Ask for ``count`` questions, keep the ones that validate, report the rest."""
        wanted = max(1, min(int(count), MAX_QUESTIONS_PER_REQUEST))
        prompt = build_prompt(brief, wanted)
        max_tokens = max(MIN_OUTPUT_TOKENS, wanted * TOKENS_PER_QUESTION)

        text = self._generator.complete(prompt, max_tokens=max_tokens)
        report = parse_questions(text, wanted=wanted)
        if report.count == 0:
            # Nothing usable came back. Raising rather than returning an empty success: a caller
            # that asked for twenty questions and silently got none would have no idea why.
            raise QuestionGenerationFailedError(
                reason="; ".join(report.reasons) or "no question survived parsing",
                accepted=0,
                wanted=wanted,
            )

        stored: list[str] = []
        reasons = list(report.reasons)
        for question in report.accepted:
            question_id, reason = self._sink.store(
                question, brief, actor=actor, topics=topics
            )
            if question_id is None:
                if reason and reason not in reasons:
                    reasons.append(reason)
                continue
            stored.append(question_id)

        logger.info(
            "quiz_generation.completed",
            extra={
                "course_ref": brief.course_id,
                "requested": wanted,
                "created": len(stored),
                "rejected": report.rejected + (report.count - len(stored)),
            },
        )
        return GenerationOutcome(
            course_id=brief.course_id,
            requested=wanted,
            question_ids=tuple(stored),
            rejected=report.rejected + (report.count - len(stored)),
            reasons=tuple(reasons),
        )
