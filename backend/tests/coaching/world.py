"""UC-07's world: every boundary faked and controllable.

What a coaching test needs is a complete, controllable world — a submitted attempt from UC-03,
confirmed outcomes from UC-04, released feedback from UC-06, a programmable AI coach, and doubles for
the activity and knowledge-gap pipelines. ``build_world`` assembles exactly that.

WHY THESE RUN AGAINST PORT FAKES AND NOT THE REAL ADAPTERS
----------------------------------------------------------
The same reason UC-03's suite fakes UC-01 and UC-02, and UC-04/05/06's fake UC-03: each tests its own
logic, and several required behaviours are otherwise unreachable. UC-04 correctly refuses to confirm a
score it could not compute, so "coaching is refused while the score is unconfirmed" could not be
exercised through the real chain at all; nor could "UC-06 withdrew the feedback report mid-session",
"the answer key arrived with a metadata blob full of the correct answers", or an AI provider that
times out on the third exchange and recovers on the fourth.

The **real** adapters — onto UC-03, UC-04, UC-06 and the ``qk_`` tables — are covered by
``tests/integration/test_coaching_chain.py``, which drives the whole chain over HTTP against a real
database. Between them: the rules are tested here, the wiring is tested there.

THREE DELIBERATE CHOICES
------------------------
* **A fixed clock and sequential ids.** ``started_at`` and ``session_id`` are then predictable, so
  idempotency can be asserted with plain equality.
* **One realistic standard paper.** ``given_standard_quiz`` builds a five-question attempt with
  three incorrect answers, one correct and one unanswered — enough to prove the review queue
  contains what it should and, more importantly, what it should not (§20).
* **The answer key is real and it is everywhere.** UC-04's results carry answer keys, UC-06's
  feedback carries the correct answers and the explanations, and both carry metadata blobs with
  more of the same. The security tests are only worth running against material that actually
  contains what must not leak (§13, §25).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.time import FixedClock
from app.modules.coaching.container import Container, create_container
from app.modules.coaching.domain.enums import CoachingMode
from app.modules.coaching.ids import SequentialIdGenerator
from app.modules.coaching.integration.uc03 import (
    AttemptContext,
    AttemptStatus,
    DeliveredOption,
    DeliveredOrderItem,
    DeliveredQuestion,
    LearnerAnswer,
    QuestionType,
)
from app.modules.coaching.integration.uc04 import (
    AttemptScore,
    QuestionOutcome,
    QuestionResult,
    ScoreStatus,
)
from app.modules.coaching.integration.uc06 import (
    AttemptFeedback,
    FeedbackStatus,
    LessonReference,
    QuestionFeedback,
)
from app.modules.coaching.repositories.in_memory import (
    InMemoryCoachingSessionRepository,
    InMemoryCoachingTranscriptRepository,
)
from tests.coaching.fakes import (
    FakeActivityLog,
    FakeAttemptProvider,
    FakeCoachingLLM,
    FakeFeedbackProvider,
    FakeKnowledgeGapTracker,
    FakeScoringProvider,
)

LEARNER = "learner-1"
OTHER_LEARNER = "learner-2"
COURSE = "course-1"
COURSE_NAME = "Safeguarding Level 2"
QUIZ = "quiz-1"
ATTEMPT_1 = "attempt-1"
OTHER_ATTEMPT = "attempt-2"

STARTED_AT = "2026-01-15T10:00:00Z"
SUBMITTED_AT = "2026-01-15T10:12:30Z"
NOW = "2026-02-01T08:00:00Z"

#: Question ids used by the standard paper.
Q_SINGLE = "q-single"  # CORRECT      — must never be coachable
Q_MULTI = "q-multi"  # INCORRECT    — the main coaching subject
Q_TRUE_FALSE = "q-true-false"  # INCORRECT
Q_ORDER = "q-order"  # UNANSWERED   — must never enter the review queue
Q_SCENARIO = "q-scenario"  # INCORRECT

#: The questions the review queue must contain, in delivery order (§19, §20).
INCORRECT_QUESTIONS = (Q_MULTI, Q_TRUE_FALSE, Q_SCENARIO)

# ---------------------------------------------------------------------------
# Answer-key material. Every string below is something the model must never see.
# ---------------------------------------------------------------------------

#: Deliberately *not* equal to any single delivered option's text, so it stays in the sanitiser's
#: forbidden-value set rather than being exempted as presented material.
MULTI_CORRECT_ANSWER_TEXT = "Record what you saw and report it promptly to the safeguarding lead"
MULTI_EXPLANATION = (
    "Options A and C are correct because recording and prompt reporting protect the learner; "
    "investigating yourself can compromise a later enquiry."
)
MULTI_RATIONALE = "Marking rationale: award marks only for the pair A and C, deduct for B and D."

TRUE_FALSE_CORRECT_ANSWER_TEXT = (
    "False, because confidentiality never prevents sharing with the safeguarding lead"
)
TRUE_FALSE_EXPLANATION = (
    "The statement is false: confidentiality does not prevent sharing a concern with the "
    "safeguarding lead."
)

SCENARIO_CORRECT_ANSWER_TEXT = "Escalate to the designated safeguarding lead the same working day"
SCENARIO_EXPLANATION = "Same-day escalation is required because delay increases risk to the child."

#: Everything a security test asserts is absent from the model's input (§25).
ANSWER_KEY_SECRETS: tuple[str, ...] = (
    MULTI_CORRECT_ANSWER_TEXT,
    MULTI_EXPLANATION,
    MULTI_RATIONALE,
    TRUE_FALSE_CORRECT_ANSWER_TEXT,
    TRUE_FALSE_EXPLANATION,
    SCENARIO_CORRECT_ANSWER_TEXT,
    SCENARIO_EXPLANATION,
)


# ---------------------------------------------------------------------------
# Builders — UC-03
# ---------------------------------------------------------------------------


def make_attempt(
    *,
    attempt_id: str = ATTEMPT_1,
    learner_id: str = LEARNER,
    course_id: str = COURSE,
    course_name: str = COURSE_NAME,
    quiz_id: str = QUIZ,
    status: AttemptStatus = AttemptStatus.SUBMITTED,
    attempt_number: int = 1,
    started_at: str | None = STARTED_AT,
    submitted_at: str | None = SUBMITTED_AT,
) -> AttemptContext:
    return AttemptContext(
        attempt_id=attempt_id,
        learner_id=learner_id,
        course_id=course_id,
        course_name=course_name,
        quiz_id=quiz_id,
        status=status,
        attempt_number=attempt_number,
        started_at=started_at,
        submitted_at=submitted_at if status is AttemptStatus.SUBMITTED else None,
    )


def make_delivered_question(
    *,
    question_id: str,
    position: int,
    question_type: QuestionType = QuestionType.SINGLE_CHOICE,
    prompt: str | None = None,
    options: Sequence[tuple[str, str]] = (),
    order_items: Sequence[tuple[str, str]] = (),
    topics: tuple[str, ...] = (),
    maximum_marks: float | None = 1.0,
    scenario_text: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> DeliveredQuestion:
    return DeliveredQuestion(
        question_id=question_id,
        position=position,
        question_type=question_type,
        prompt=prompt or f"Delivered prompt for {question_id}",
        scenario_text=scenario_text,
        options=tuple(
            DeliveredOption(option_id=option_id, text=text, position=index)
            for index, (option_id, text) in enumerate(options, start=1)
        ),
        order_items=tuple(
            DeliveredOrderItem(item_id=item_id, text=text, position=index)
            for index, (item_id, text) in enumerate(order_items, start=1)
        ),
        topics=topics,
        maximum_marks=maximum_marks,
        question_reference=f"REF-{question_id}",
        metadata=metadata or {},
    )


def make_answer(
    *,
    question_id: str,
    response: dict[str, Any] | None = None,
    answered: bool = True,
) -> LearnerAnswer:
    return LearnerAnswer(
        question_id=question_id,
        answered=answered and response is not None,
        response=response,
        saved_at="2026-01-15T10:05:00Z",
    )


# ---------------------------------------------------------------------------
# Builders — UC-04
# ---------------------------------------------------------------------------


def make_result(
    *,
    question_id: str,
    position: int,
    question_type: QuestionType = QuestionType.SINGLE_CHOICE,
    outcome: QuestionOutcome = QuestionOutcome.INCORRECT,
    awarded_marks: float | None = 0.0,
    maximum_marks: float | None = 1.0,
    answer_key: dict[str, Any] | None = None,
) -> QuestionResult:
    return QuestionResult(
        question_id=question_id,
        position=position,
        question_type=question_type,
        outcome=outcome,
        awarded_marks=awarded_marks,
        maximum_marks=maximum_marks,
        answer_key=answer_key,
    )


def make_score(
    *,
    attempt_id: str = ATTEMPT_1,
    learner_id: str = LEARNER,
    course_id: str = COURSE,
    quiz_id: str = QUIZ,
    status: ScoreStatus = ScoreStatus.CONFIRMED,
    results: Sequence[QuestionResult] = (),
    percentage: float | None = 30.0,
) -> AttemptScore:
    return AttemptScore(
        attempt_id=attempt_id,
        learner_id=learner_id,
        course_id=course_id,
        quiz_id=quiz_id,
        status=status,
        question_results=tuple(results),
        percentage=percentage if status is ScoreStatus.CONFIRMED else None,
        confirmed_at="2026-01-15T10:12:35Z" if status is ScoreStatus.CONFIRMED else None,
    )


# ---------------------------------------------------------------------------
# Builders — UC-06
# ---------------------------------------------------------------------------


def make_question_feedback(
    *,
    question_id: str,
    topics: tuple[str, ...] = (),
    lesson_id: str | None = None,
    lesson_title: str | None = None,
    misconception_note: str | None = None,
    explanation: str | None = None,
    correct_answer_text: str | None = None,
    correct_option_ids: tuple[str, ...] = (),
    learner_answer_summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> QuestionFeedback:
    return QuestionFeedback(
        question_id=question_id,
        topics=topics,
        lesson_reference=(
            LessonReference(
                lesson_id=lesson_id,
                title=lesson_title,
                url=f"https://courses.example/lessons/{lesson_id}",
                topic=topics[0] if topics else None,
            )
            if lesson_id
            else None
        ),
        misconception_note=misconception_note,
        learner_answer_summary=learner_answer_summary,
        explanation=explanation,
        correct_answer_text=correct_answer_text,
        correct_option_ids=correct_option_ids,
        metadata=metadata or {},
    )


def make_feedback(
    *,
    attempt_id: str = ATTEMPT_1,
    status: FeedbackStatus = FeedbackStatus.AVAILABLE,
    learner_id: str = LEARNER,
    course_id: str = COURSE,
    question_feedback: Sequence[QuestionFeedback] = (),
) -> AttemptFeedback:
    return AttemptFeedback(
        attempt_id=attempt_id,
        status=status,
        learner_id=learner_id,
        course_id=course_id,
        generated_at="2026-01-15T10:13:00Z",
        question_feedback=tuple(question_feedback),
    )


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


@dataclass
class World:
    """A fully wired UC-07 with every boundary faked and controllable."""

    container: Container
    attempts: FakeAttemptProvider
    scores: FakeScoringProvider
    feedback: FakeFeedbackProvider
    llm: FakeCoachingLLM
    activity: FakeActivityLog
    gaps: FakeKnowledgeGapTracker
    sessions: InMemoryCoachingSessionRepository
    transcripts: InMemoryCoachingTranscriptRepository
    clock: FixedClock

    # ---- Convenience accessors ----

    @property
    def coaching(self):  # noqa: ANN201 - the container's types are the contract
        return self.container.services.coaching

    @property
    def review(self):  # noqa: ANN201
        return self.container.services.review

    @property
    def sanitizer(self):  # noqa: ANN201
        return self.container.sanitizer

    # ---- Scenario helpers ----

    def given_standard_quiz(
        self,
        *,
        attempt_id: str = ATTEMPT_1,
        learner_id: str = LEARNER,
        attempt_status: AttemptStatus = AttemptStatus.SUBMITTED,
        score_status: ScoreStatus = ScoreStatus.CONFIRMED,
        feedback_status: FeedbackStatus = FeedbackStatus.AVAILABLE,
        with_feedback_records: bool = True,
    ) -> AttemptContext:
        """A realistic five-question attempt.

        ==================  ==================  ============  ===============================
        Question            Type                Outcome       Role in the tests
        ==================  ==================  ============  ===============================
        ``q-single``        SINGLE_CHOICE       CORRECT       must never be coachable (§20)
        ``q-multi``         MULTI_SELECT        INCORRECT     the main coaching subject
        ``q-true-false``    TRUE_FALSE          INCORRECT     second in the review queue
        ``q-order``         DRAG_TO_ORDER       UNANSWERED    must never enter the queue (§20)
        ``q-scenario``      SCENARIO            INCORRECT     third in the review queue
        ==================  ==================  ============  ===============================
        """
        attempt = self.attempts.set(
            make_attempt(
                attempt_id=attempt_id, learner_id=learner_id, status=attempt_status
            )
        )

        self.attempts.set_delivered(
            attempt_id,
            [
                make_delivered_question(
                    question_id=Q_SINGLE,
                    position=1,
                    question_type=QuestionType.SINGLE_CHOICE,
                    prompt="What should you do first if you have a safeguarding concern?",
                    options=(("A", "Tell the safeguarding lead"), ("B", "Do nothing")),
                    topics=("Safeguarding basics",),
                ),
                make_delivered_question(
                    question_id=Q_MULTI,
                    position=2,
                    question_type=QuestionType.MULTI_SELECT,
                    prompt="Which actions are appropriate when you have a concern?",
                    options=(
                        ("A", "Record what you saw"),
                        ("B", "Investigate yourself"),
                        ("C", "Report it promptly"),
                        ("D", "Discuss it with parents first"),
                    ),
                    topics=("Reporting concerns",),
                    maximum_marks=2.0,
                    # An untrusted upstream blob carrying the key — dropped wholesale (§13).
                    metadata={
                        "answer_key": {"correct_option_ids": ["A", "C"]},
                        "marking_notes": MULTI_RATIONALE,
                    },
                ),
                make_delivered_question(
                    question_id=Q_TRUE_FALSE,
                    position=3,
                    question_type=QuestionType.TRUE_FALSE,
                    prompt="Confidentiality means you must never share a concern.",
                    topics=("Confidentiality",),
                    maximum_marks=0.5,
                ),
                make_delivered_question(
                    question_id=Q_ORDER,
                    position=4,
                    question_type=QuestionType.DRAG_TO_ORDER,
                    prompt="Put the reporting steps in order.",
                    order_items=(("s1", "Observe"), ("s2", "Record"), ("s3", "Report")),
                    topics=("Reporting concerns",),
                    maximum_marks=0.5,
                ),
                make_delivered_question(
                    question_id=Q_SCENARIO,
                    position=5,
                    question_type=QuestionType.SCENARIO,
                    prompt="What should the teaching assistant do next?",
                    scenario_text=(
                        "A child mentions in passing that they are frightened to go home."
                    ),
                    options=(
                        ("A", "Wait and see whether it happens again"),
                        ("B", "Raise it with the designated lead today"),
                        ("C", "Ask the parents at pick-up"),
                    ),
                    topics=("Escalation",),
                ),
            ],
        )

        self.attempts.set_answers(
            attempt_id,
            [
                make_answer(
                    question_id=Q_SINGLE,
                    response={"type": "SINGLE_CHOICE", "selected_option_id": "A"},
                ),
                make_answer(
                    question_id=Q_MULTI,
                    response={"type": "MULTI_SELECT", "selected_option_ids": ["A", "B"]},
                ),
                make_answer(
                    question_id=Q_TRUE_FALSE, response={"type": "TRUE_FALSE", "value": True}
                ),
                make_answer(question_id=Q_ORDER, response=None, answered=False),
                make_answer(
                    question_id=Q_SCENARIO,
                    response={"type": "SINGLE_CHOICE", "selected_option_id": "C"},
                ),
            ],
        )

        self.scores.set(
            make_score(
                attempt_id=attempt_id,
                learner_id=learner_id,
                status=score_status,
                results=[
                    make_result(
                        question_id=Q_SINGLE,
                        position=1,
                        question_type=QuestionType.SINGLE_CHOICE,
                        outcome=QuestionOutcome.CORRECT,
                        awarded_marks=1.0,
                        answer_key={"type": "SINGLE_CHOICE", "correct_option_id": "A"},
                    ),
                    make_result(
                        question_id=Q_MULTI,
                        position=2,
                        question_type=QuestionType.MULTI_SELECT,
                        outcome=QuestionOutcome.INCORRECT,
                        awarded_marks=0.5,
                        maximum_marks=2.0,
                        answer_key={
                            "type": "MULTI_SELECT",
                            "correct_option_ids": ["A", "C"],
                            "rationale": MULTI_RATIONALE,
                        },
                    ),
                    make_result(
                        question_id=Q_TRUE_FALSE,
                        position=3,
                        question_type=QuestionType.TRUE_FALSE,
                        outcome=QuestionOutcome.INCORRECT,
                        awarded_marks=0.0,
                        maximum_marks=0.5,
                        answer_key={"type": "TRUE_FALSE", "correct_value": False},
                    ),
                    make_result(
                        question_id=Q_ORDER,
                        position=4,
                        question_type=QuestionType.DRAG_TO_ORDER,
                        outcome=QuestionOutcome.UNANSWERED,
                        awarded_marks=0.0,
                        maximum_marks=0.5,
                        answer_key={
                            "type": "DRAG_TO_ORDER",
                            "correct_sequence": ["s1", "s2", "s3"],
                        },
                    ),
                    make_result(
                        question_id=Q_SCENARIO,
                        position=5,
                        question_type=QuestionType.SCENARIO,
                        outcome=QuestionOutcome.INCORRECT,
                        awarded_marks=0.0,
                        answer_key={"type": "SCENARIO", "correct_option_id": "B"},
                    ),
                ],
            )
        )

        records = (
            [
                make_question_feedback(
                    question_id=Q_MULTI,
                    topics=("Reporting concerns",),
                    lesson_id="lesson-rc",
                    lesson_title="Reporting a concern",
                    misconception_note=(
                        "The learner treated investigating the concern themselves as part of "
                        "responding to it."
                    ),
                    learner_answer_summary="Selected: Record what you saw; Investigate yourself",
                    explanation=MULTI_EXPLANATION,
                    correct_answer_text=MULTI_CORRECT_ANSWER_TEXT,
                    correct_option_ids=("A", "C"),
                    metadata={"answer_key_hash": "sha256:deadbeef", "rationale": MULTI_RATIONALE},
                ),
                make_question_feedback(
                    question_id=Q_TRUE_FALSE,
                    topics=("Confidentiality",),
                    explanation=TRUE_FALSE_EXPLANATION,
                    correct_answer_text=TRUE_FALSE_CORRECT_ANSWER_TEXT,
                ),
                make_question_feedback(
                    question_id=Q_SCENARIO,
                    topics=("Escalation",),
                    lesson_id="lesson-esc",
                    lesson_title="Escalating a concern",
                    explanation=SCENARIO_EXPLANATION,
                    correct_answer_text=SCENARIO_CORRECT_ANSWER_TEXT,
                    correct_option_ids=("B",),
                ),
            ]
            if with_feedback_records
            else []
        )

        self.feedback.set(
            make_feedback(
                attempt_id=attempt_id,
                learner_id=learner_id,
                status=feedback_status,
                question_feedback=records,
            )
        )
        return attempt

    # ---- Actions ----

    async def start(
        self,
        question_id: str = Q_MULTI,
        *,
        learner_id: str = LEARNER,
        attempt_id: str = ATTEMPT_1,
    ):  # noqa: ANN201
        return await self.coaching.start_coaching(
            learner_id=learner_id, attempt_id=attempt_id, question_id=question_id
        )

    async def say(self, session_id: str, text: str, *, learner_id: str = LEARNER):  # noqa: ANN201
        return await self.coaching.send_message(
            learner_id=learner_id, session_id=session_id, text=text
        )

    async def exchange_n_times(self, session_id: str, count: int) -> None:
        """Complete ``count`` full exchanges on a session."""
        for index in range(count):
            await self.say(session_id, f"Here is my thinking, attempt {index + 1}.")

    async def choose(self, session_id: str, mode: CoachingMode, *, learner_id: str = LEARNER):  # noqa: ANN201
        return await self.coaching.select_mode(
            learner_id=learner_id, session_id=session_id, mode=mode
        )


def build_world(*, settings: Settings | None = None) -> World:
    attempts = FakeAttemptProvider()
    scores = FakeScoringProvider()
    feedback = FakeFeedbackProvider()
    llm = FakeCoachingLLM()
    activity = FakeActivityLog()
    gaps = FakeKnowledgeGapTracker()
    sessions = InMemoryCoachingSessionRepository()
    transcripts = InMemoryCoachingTranscriptRepository()
    clock = FixedClock(NOW)

    container = create_container(
        settings=settings,
        clock=clock,
        new_id=SequentialIdGenerator("session"),
        attempts=attempts,
        scores=scores,
        feedback=feedback,
        llm=llm,
        activity=activity,
        knowledge_gaps=gaps,
        sessions_repository=sessions,
        transcripts_repository=transcripts,
    )

    return World(
        container=container,
        attempts=attempts,
        scores=scores,
        feedback=feedback,
        llm=llm,
        activity=activity,
        gaps=gaps,
        sessions=sessions,
        transcripts=transcripts,
        clock=clock,
    )
