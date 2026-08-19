"""Harness and port fakes for the results chain (UC-04, UC-05, UC-06).

Why UC-03 and UC-02 are faked, and why the adapters *between* UC-04, UC-05 and UC-06 are real
---------------------------------------------------------------------------------------------
The same reasoning UC-03's suite records for its own fakes applies here, and the split is deliberate:

* **UC-03 and UC-02 are faked.** These suites need attempts the real chain cannot produce -- one whose
  answer key has gone missing, one worth zero marks, one delivered no questions at all. UC-01 and
  UC-02 correctly refuse to publish incoherent data, so those states are unreachable through the real
  chain and the behaviour they exist to protect could not be exercised at all. The fakes hold no rules
  of their own: they store what a test puts in and hand it back.
* **UC-04 -> UC-05 -> UC-06 use the real adapters over a real database.** So "UC-05 gates on the score
  UC-04 actually wrote" and "UC-06 renders the rows UC-04 actually froze" are tested, not assumed.
* **The certificate service and the CPD system are controllable.** They are the two outbound
  boundaries and their failure modes are requirements, so a test switches them to failing rather than
  patching internals -- exactly how UC-03's suite reaches its pending-submission states.

The whole chain against the real UC-01/UC-02/UC-03 adapters over HTTP lives in
``tests/integration/test_results_chain.py``. Both exist because they prove different things.

Two levels of builder, because two kinds of test read differently
-----------------------------------------------------------------
``option`` / ``delivered`` / ``answer_key`` / ``submitted_attempt`` state one question inline, which is
what a test about *one marking rule* wants to say. ``single_choice`` / ``multi_select`` / ... return a
:class:`Built` -- a question, its key, and a right and a wrong answer for it -- which is what a test
about a *whole paper* wants. Both sit on the same fakes.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.composition import ResultsAppContext, ResultsPorts
from app.core.time import FixedClock, parse_instant
from app.db.session import create_db_engine, create_session_factory
from app.modules.certification.integration.attempt_delivery.port import AttemptPolicy
from app.modules.certification.integration.certificate.local_adapter import (
    certificate_number_for,
)
from app.modules.certification.integration.certificate.port import (
    CertificateIssued,
    CertificateRequest,
    TransientCertificateError,
)
from app.modules.certification.integration.cpd.port import (
    CpdSyncAck,
    CpdSyncRecord,
    TransientCpdError,
)
from app.modules.certification.integration.scoring.result_adapter import ScoringResultAdapter
from app.modules.feedback.integration.certification.outcome_adapter import (
    CertificationOutcomeAdapter,
)
from app.modules.feedback.integration.question_bank.port import QuestionContent
from app.modules.feedback.integration.question_bank.port import (
    QuestionVersionRef as ContentRef,
)
from app.modules.feedback.integration.scoring.score_adapter import ScoringDetailAdapter
from app.modules.scoring.domain.answer_key import AnswerKey, KeyOption, MarkingPolicy
from app.modules.scoring.domain.enums import AnswerKeySource, QuestionType
from app.modules.scoring.integration.attempt_delivery.types import (
    DeliveredOption,
    DeliveredQuestion,
    SubmittedAttempt,
)
from app.modules.scoring.integration.question_bank.port import QuestionVersionRef

ATTEMPT_ID = "attempt-1"
LEARNER_ID = "7001"
OTHER_LEARNER_ID = "7002"
COURSE_ID = "course-fire-safety"
QUIZ_ID = "quiz-fire-safety-final"
COURSE_NAME = "Fire Safety Awareness"
QUIZ_TITLE = "Fire Safety — final assessment"
CONFIGURATION_VERSION_ID = "42"

STARTED_AT = "2026-03-01T09:00:00Z"
SUBMITTED_AT = "2026-03-01T09:12:30Z"

#: The default authored explanation. A test that cares about a *missing* one passes ``None``.
EXPLANATION = "Because the alarm must be raised first."

#: Pass mark used by the whole-paper helpers unless a test names another.
PASS_MARK = 60.0


# ---------------------------------------------------------------------------
# Builders for the frozen data UC-03 would have handed over
# ---------------------------------------------------------------------------


def option(option_id: str, text: str, *, correct: bool = False) -> DeliveredOption:
    """One choice option, as UC-03 froze it onto the attempt."""
    return DeliveredOption(option_id=option_id, text=text, is_correct=correct)


def order_item(item_id: str, text: str, *, position: int | None) -> DeliveredOption:
    """One orderable item. ``position`` is its rank in the *correct* order, not the presented one."""
    return DeliveredOption(option_id=item_id, text=text, correct_position=position)


def delivered(
    question_type: QuestionType,
    *,
    position: int = 1,
    question_id: str | None = None,
    version: int = 1,
    points: float = 1.0,
    options: Sequence[DeliveredOption] = (),
    response: dict[str, Any] | None = None,
    answered: bool | None = None,
    prompt: str = "",
    scenario_text: str | None = None,
    sub_question_ids: Sequence[str] = (),
    extra: dict[str, Any] | None = None,
) -> DeliveredQuestion:
    """One frozen question of a submitted attempt, with the learner's final answer."""
    qid = question_id or f"q-{str(question_type).lower()}-{position}"
    return DeliveredQuestion(
        attempt_question_id=f"aq-{qid}",
        question_id=qid,
        question_version=version,
        question_type=question_type,
        position=position,
        max_marks=points,
        prompt=prompt or f"{question_type} question at position {position}",
        scenario_text=scenario_text,
        options=tuple(options),
        sub_question_ids=tuple(sub_question_ids),
        answered=(response is not None) if answered is None else answered,
        complete=response is not None,
        response=response,
        extra=dict(extra or {}),
    )


def submitted_attempt(
    questions: Sequence[DeliveredQuestion],
    *,
    attempt_id: str = ATTEMPT_ID,
    learner_id: str = LEARNER_ID,
    attempt_number: int = 1,
    pass_mark: float = 70.0,
    max_attempts: int | None = 3,
    locked: bool = True,
    status: str = "SUBMITTED",
    started_at: str = STARTED_AT,
    submitted_at: str | None = SUBMITTED_AT,
) -> SubmittedAttempt:
    """A submitted attempt, in the shape UC-03's adapter would produce."""
    return SubmittedAttempt(
        attempt_id=attempt_id,
        learner_id=learner_id,
        course_id=COURSE_ID,
        quiz_id=QUIZ_ID,
        attempt_number=attempt_number,
        status=status,
        locked=locked,
        configuration_version_id=CONFIGURATION_VERSION_ID,
        configuration_version_number=3,
        pass_mark_percentage=pass_mark,
        started_at=parse_instant(started_at),
        submitted_at=None if submitted_at is None else parse_instant(submitted_at),
        submission_id=f"submission-{attempt_id}",
        configuration_snapshot={
            "passMarkPercentage": pass_mark,
            "maxAttempts": max_attempts,
            "extra": {"courseTitle": COURSE_NAME, "quizTitle": QUIZ_TITLE},
        },
        questions=tuple(questions),
    )


def answer_key(
    question: DeliveredQuestion,
    *,
    correct_ids: Sequence[str] = (),
    correct_order: Sequence[str] = (),
    primary_id: str | None = None,
    policy: MarkingPolicy = MarkingPolicy.EXACT,
    deduction: float = 0.0,
    points: float | None = None,
    explanation: str | None = EXPLANATION,
    topics: Sequence[str] = ("Evacuation",),
) -> AnswerKey:
    """An answer key for a delivered question, as UC-02's version snapshot would yield it."""
    ranked = {item_id: index + 1 for index, item_id in enumerate(correct_order)}
    correct = set(correct_ids)
    options = tuple(
        KeyOption(
            option_id=item.option_id,
            text=item.text,
            is_correct=item.option_id in correct,
            is_primary=item.option_id == primary_id,
            correct_position=ranked.get(item.option_id),
        )
        for item in question.options
    )
    return AnswerKey(
        question_id=question.question_id,
        question_version=question.question_version,
        question_type=question.question_type,
        max_marks=question.max_marks if points is None else points,
        marking_policy=policy,
        deduction_per_incorrect=deduction,
        options=options,
        source=AnswerKeySource.QUESTION_BANK_SNAPSHOT,
        explanation=explanation,
        topics=tuple(topics),
        reference="Q-000001",
    )


# ---------------------------------------------------------------------------
# One ready-made question per type, for tests about a whole paper
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Built:
    """A delivered question, the key that marks it, and a right and a wrong answer for it."""

    delivered: DeliveredQuestion
    key: AnswerKey
    correct_response: Any
    wrong_response: Any


def single_choice(position: int, *, marks: float = 1.0, question_id: str | None = None) -> Built:
    question = delivered(
        QuestionType.SINGLE_CHOICE,
        position=position,
        question_id=question_id or f"q-sc-{position}",
        points=marks,
        prompt="What is the first action on hearing the alarm?",
        options=[
            option("A", "Raise the alarm", correct=True),
            option("B", "Collect belongings"),
            option("C", "Finish the task"),
            option("D", "Ask a colleague"),
        ],
    )
    return Built(
        delivered=question,
        key=answer_key(
            question,
            correct_ids=["A"],
            explanation="The alarm must be raised before anything else.",
        ),
        correct_response={"type": "SINGLE_CHOICE", "selectedOptionId": "A"},
        wrong_response={"type": "SINGLE_CHOICE", "selectedOptionId": "B"},
    )


def true_false(position: int, *, marks: float = 1.0, question_id: str | None = None) -> Built:
    question = delivered(
        QuestionType.TRUE_FALSE,
        position=position,
        question_id=question_id or f"q-tf-{position}",
        points=marks,
        prompt="A fire door may be propped open while carrying equipment.",
        options=[option("TRUE", "True"), option("FALSE", "False", correct=True)],
    )
    return Built(
        delivered=question,
        key=answer_key(
            question,
            correct_ids=["FALSE"],
            explanation="Fire doors must never be propped open.",
        ),
        correct_response={"type": "TRUE_FALSE", "value": False},
        wrong_response={"type": "TRUE_FALSE", "value": True},
    )


def multi_select(
    position: int,
    *,
    marks: float = 4.0,
    policy: MarkingPolicy = MarkingPolicy.PARTIAL_WITH_DEDUCTION,
    deduction: float = 1.0,
    question_id: str | None = None,
) -> Built:
    question = delivered(
        QuestionType.MULTI_SELECT,
        position=position,
        question_id=question_id or f"q-ms-{position}",
        points=marks,
        prompt="Select every correct evacuation action.",
        options=[
            option("A", "Close doors behind you", correct=True),
            option("B", "Use the nearest exit", correct=True),
            option("C", "Use the lift"),
            option("D", "Return for belongings"),
        ],
    )
    return Built(
        delivered=question,
        key=answer_key(
            question,
            correct_ids=["A", "B"],
            policy=policy,
            deduction=deduction,
            explanation="Close doors and use the nearest exit; never the lift.",
        ),
        correct_response={"type": "MULTI_SELECT", "selectedOptionIds": ["A", "B"]},
        wrong_response={"type": "MULTI_SELECT", "selectedOptionIds": ["C", "D"]},
    )


def scenario(position: int, *, marks: float = 2.0, question_id: str | None = None) -> Built:
    qid = question_id or f"q-sn-{position}"
    sub_id = f"{qid}:1"
    question = delivered(
        QuestionType.SCENARIO,
        position=position,
        question_id=qid,
        points=marks,
        prompt="What should you do first?",
        scenario_text="You are working alone when you notice smoke coming from a socket.",
        options=[
            option("A", "Evacuate and report to the assembly point", correct=True),
            option("B", "Investigate the smoke yourself", correct=True),
            option("C", "Wait for instructions"),
        ],
        sub_question_ids=[sub_id],
    )

    def response(option_id: str) -> dict[str, Any]:
        return {
            "type": "SCENARIO",
            "responses": [
                {
                    "subQuestionId": sub_id,
                    "answer": {"type": "SINGLE_CHOICE", "selectedOptionId": option_id},
                }
            ],
        }

    return Built(
        delivered=question,
        # Two options are correct and exactly one is primary, so "score only the configured primary
        # answer" is a rule these tests can actually observe.
        key=answer_key(
            question,
            correct_ids=["A", "B"],
            primary_id="A",
            explanation="Evacuating and reporting is the only safe first action.",
        ),
        correct_response=response("A"),
        wrong_response=response("B"),
    )


def drag_to_order(position: int, *, marks: float = 3.0, question_id: str | None = None) -> Built:
    question = delivered(
        QuestionType.DRAG_TO_ORDER,
        position=position,
        question_id=question_id or f"q-do-{position}",
        points=marks,
        prompt="Put the evacuation steps in order.",
        # Presented out of sequence on purpose: the key is `correct_position`, never the order shown.
        options=[
            order_item("S3", "Report to the assembly point", position=3),
            order_item("S1", "Raise the alarm", position=1),
            order_item("S2", "Evacuate the area", position=2),
        ],
    )
    return Built(
        delivered=question,
        key=answer_key(
            question,
            correct_order=["S1", "S2", "S3"],
            explanation="Alarm, then evacuate, then report.",
        ),
        correct_response={"type": "DRAG_TO_ORDER", "orderedItemIds": ["S1", "S2", "S3"]},
        wrong_response={"type": "DRAG_TO_ORDER", "orderedItemIds": ["S2", "S1", "S3"]},
    )


#: One builder per supported type, in the shared kernel's canonical order.
ALL_TYPES = (single_choice, true_false, multi_select, scenario, drag_to_order)


# ---------------------------------------------------------------------------
# The fakes
# ---------------------------------------------------------------------------


class FakeAttemptSource:
    """UC-03, as UC-04 and UC-05 see it.

    Serves both ports -- the submitted attempt and the attempt's rules -- from one stored attempt, so
    a test states the world once and both capabilities agree about it.
    """

    def __init__(self) -> None:
        self._attempts: dict[str, SubmittedAttempt] = {}
        #: Attempts the learner has used at the quiz, for the remaining-attempt arithmetic.
        self.attempts_used: dict[str, int] = {}
        #: Overridable, because "unlimited attempts" is a case UC-05 has to answer for.
        self.max_attempts: int | None = 3
        self.course_name: str = COURSE_NAME
        self.quiz_title: str | None = QUIZ_TITLE

    # ---- seeding -----------------------------------------------------------

    def add(self, attempt: SubmittedAttempt) -> SubmittedAttempt:
        self._attempts[attempt.attempt_id] = attempt
        self.attempts_used.setdefault(attempt.learner_id, attempt.attempt_number)
        stored = attempt.configuration_snapshot.get("maxAttempts", self.max_attempts)
        self.max_attempts = None if stored is None else int(stored)
        return attempt

    def replace(self, attempt: SubmittedAttempt) -> SubmittedAttempt:
        """Overwrite a stored attempt without touching the learner's attempt count."""
        self._attempts[attempt.attempt_id] = attempt
        return attempt

    # ---- AttemptSourcePort (UC-04) ----------------------------------------

    def get_attempt(
        self, attempt_id: str, *, learner_id: str | None = None
    ) -> SubmittedAttempt | None:
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            return None
        if learner_id is not None and attempt.learner_id != str(learner_id):
            return None
        return attempt

    def list_submitted_attempt_ids(
        self, *, learner_id: str, quiz_id: str | None = None
    ) -> list[str]:
        return [
            attempt.attempt_id
            for attempt in self._attempts.values()
            if attempt.learner_id == str(learner_id)
            and (quiz_id is None or attempt.quiz_id == quiz_id)
        ]

    # ---- AttemptPolicyPort (UC-05) ----------------------------------------

    def get_policy(self, attempt_id: str, *, learner_id: str | None = None) -> AttemptPolicy | None:
        attempt = self.get_attempt(attempt_id, learner_id=learner_id)
        if attempt is None:
            return None
        return AttemptPolicy(
            attempt_id=attempt.attempt_id,
            learner_id=attempt.learner_id,
            course_id=attempt.course_id,
            quiz_id=attempt.quiz_id,
            attempt_number=attempt.attempt_number,
            configuration_version_id=attempt.configuration_version_id,
            pass_mark_percentage=attempt.pass_mark_percentage,
            max_attempts=self.max_attempts,
            attempts_used=self.attempts_used.get(attempt.learner_id, attempt.attempt_number),
            course_name=self.course_name,
            quiz_title=self.quiz_title,
            submitted_at=attempt.submitted_at,
            started_at=attempt.started_at,
        )


class FakeAnswerKeys:
    """UC-02's answer keys, as UC-04 sees them."""

    def __init__(self) -> None:
        self._keys: dict[QuestionVersionRef, AnswerKey] = {}
        #: Set to simulate a question bank that cannot be reached at all.
        self.unavailable = False

    def add(self, key: AnswerKey) -> AnswerKey:
        self._keys[QuestionVersionRef(key.question_id, key.question_version)] = key
        return key

    def drop(self, question_id: str, version: int = 1) -> None:
        """Forget a key, so the attempt's own frozen copy has to carry the scoring."""
        self._keys.pop(QuestionVersionRef(question_id, version), None)

    def find_answer_keys(
        self, refs: Sequence[QuestionVersionRef]
    ) -> dict[QuestionVersionRef, AnswerKey]:
        if self.unavailable:
            return {}
        return {ref: self._keys[ref] for ref in refs if ref in self._keys}


class FakeQuestionContent:
    """UC-02's authored explanations and lesson references, as UC-06 sees them."""

    def __init__(self) -> None:
        self._content: dict[ContentRef, QuestionContent] = {}

    def add(
        self,
        question_id: str,
        *,
        version: int = 1,
        explanation: str | None = EXPLANATION,
        lesson_reference: str | None = "Topic: Evacuation",
        question_reference: str | None = "Q-000001",
        topics: Sequence[str] = ("Evacuation",),
        option_feedback: Sequence[tuple[str, str]] = (),
    ) -> QuestionContent:
        content = QuestionContent(
            question_id=question_id,
            version=version,
            explanation=explanation,
            lesson_reference=lesson_reference,
            question_reference=question_reference,
            topics=tuple(topics),
            option_feedback=tuple(option_feedback),
        )
        self._content[ContentRef(question_id, version)] = content
        return content

    def drop(self, question_id: str, version: int = 1) -> None:
        self._content.pop(ContentRef(question_id, version), None)

    def find_content(self, refs: Sequence[ContentRef]) -> dict[ContentRef, QuestionContent]:
        return {ref: self._content[ref] for ref in refs if ref in self._content}


class ControllableCertificateService:
    """A certificate service whose failure mode the test chooses.

    The port is a real dependency, so a test supplies an implementation that fails the way it wants
    to exercise rather than patching an internal.
    """

    def __init__(self) -> None:
        #: ``"ok"`` | ``"transient"`` | ``"permanent"``
        self.mode = "ok"
        #: Every request received, so a test can assert it was called once and with what.
        self.calls: list[CertificateRequest] = []

    def issue(self, request: CertificateRequest) -> CertificateIssued:
        self.calls.append(request)
        if self.mode == "transient":
            raise TransientCertificateError("Simulated certificate service outage.")
        if self.mode == "permanent":
            raise RuntimeError("Simulated permanent certificate rejection.")
        return CertificateIssued(
            certificate_number=certificate_number_for(request.attempt_id),
            document_reference=f"test://certificates/{request.attempt_id}",
            metadata={"courseName": request.course_name},
        )

    def fail_transiently(self) -> None:
        self.mode = "transient"

    def fail_permanently(self) -> None:
        self.mode = "permanent"

    def succeed(self) -> None:
        self.mode = "ok"


class ControllableCpdService:
    """A CPD system whose failure mode the test chooses."""

    def __init__(self) -> None:
        self.mode = "ok"
        #: Every record received.
        self.records: list[CpdSyncRecord] = []

    def synchronise(self, record: CpdSyncRecord) -> CpdSyncAck:
        self.records.append(record)
        if self.mode == "transient":
            raise TransientCpdError("Simulated CPD outage.")
        if self.mode == "permanent":
            raise RuntimeError("Simulated permanent CPD rejection.")
        return CpdSyncAck(external_reference=f"cpd-test-{len(self.records)}")

    def fail_transiently(self) -> None:
        self.mode = "transient"

    def fail_permanently(self) -> None:
        self.mode = "permanent"

    def succeed(self) -> None:
        self.mode = "ok"


#: The name the first UC-05 tests used for it. Kept so either reads naturally.
ControllableCpdSync = ControllableCpdService


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


@dataclass
class ResultsWorld:
    """A private database, a controlled clock, and every boundary in the test's hands."""

    context: ResultsAppContext
    engine: Any
    clock: FixedClock
    attempts: FakeAttemptSource
    answer_keys: FakeAnswerKeys
    content: FakeQuestionContent
    certificates: ControllableCertificateService
    cpd: ControllableCpdService
    #: Whatever a test chose to remember about the paper it seeded.
    built: list[Any] = field(default_factory=list)

    # ---- lifecycle ---------------------------------------------------------

    def unit_of_work(self) -> Any:
        return self.context.unit_of_work()

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A short-lived session, for asserting on committed rows and for raw SQL."""
        session = self.context.session_factory()
        try:
            yield session
        finally:
            session.close()

    def advance(self, **kwargs: float) -> None:
        self.clock.advance(**kwargs)

    def dispose(self) -> None:
        self.engine.dispose()

    # ---- seeding a whole paper --------------------------------------------

    def seed_attempt(
        self,
        *,
        attempt_id: str = ATTEMPT_ID,
        learner_id: str = LEARNER_ID,
        questions: Sequence[Built] | None = None,
        pass_mark: float = PASS_MARK,
        max_attempts: int | None = 3,
        attempts_used: int | None = None,
        attempt_number: int = 1,
        locked: bool = True,
        submitted: bool = True,
        register_keys: bool = True,
        register_content: bool = True,
    ) -> SubmittedAttempt:
        """Register one submitted attempt, its answer keys and its authored content."""
        built = (
            list(questions)
            if questions is not None
            else [builder(index + 1) for index, builder in enumerate(ALL_TYPES)]
        )
        self.built = built

        attempt = self.attempts.add(
            submitted_attempt(
                [item.delivered for item in built],
                attempt_id=attempt_id,
                learner_id=learner_id,
                attempt_number=attempt_number,
                pass_mark=pass_mark,
                max_attempts=max_attempts,
                locked=locked,
                status="SUBMITTED" if locked else "ACTIVE",
                submitted_at=SUBMITTED_AT if submitted else None,
            )
        )
        if attempts_used is not None:
            self.attempts.attempts_used[learner_id] = attempts_used

        if register_keys:
            for item in built:
                self.answer_keys.add(item.key)
        if register_content:
            for item in built:
                self.content.add(
                    item.key.question_id,
                    explanation=item.key.explanation,
                    lesson_reference=(
                        f"Topic: {', '.join(item.key.topics)}" if item.key.topics else None
                    ),
                    question_reference=item.key.reference,
                    topics=item.key.topics,
                )
        return attempt

    def answer(self, position: int, response: Any, *, attempt_id: str = ATTEMPT_ID) -> None:
        """Set one delivered question's stored answer, as UC-03 would have saved it."""
        attempt = self.attempts.get_attempt(attempt_id)
        assert attempt is not None, f"seed the attempt {attempt_id!r} first"
        self.attempts.replace(
            replace(
                attempt,
                questions=tuple(
                    replace(
                        question,
                        answered=response is not None,
                        complete=response is not None,
                        response=response,
                    )
                    if question.position == position
                    else question
                    for question in attempt.questions
                ),
            )
        )

    def answer_all(self, *, correctly: bool = True, attempt_id: str = ATTEMPT_ID) -> None:
        for item in self.built:
            self.answer(
                item.delivered.position,
                item.correct_response if correctly else item.wrong_response,
                attempt_id=attempt_id,
            )

    def leave_unanswered(self, position: int, *, attempt_id: str = ATTEMPT_ID) -> None:
        self.answer(position, None, attempt_id=attempt_id)

    # ---- driving the chain -------------------------------------------------

    def score(self, attempt_id: str = ATTEMPT_ID, **kwargs: Any) -> Any:
        with self.unit_of_work() as ctx:
            return ctx.scoring.score(attempt_id, **kwargs)

    def determine(self, attempt_id: str = ATTEMPT_ID, **kwargs: Any) -> Any:
        with self.unit_of_work() as ctx:
            return ctx.certification.determine(attempt_id, **kwargs)

    def generate_feedback(self, attempt_id: str = ATTEMPT_ID, **kwargs: Any) -> Any:
        with self.unit_of_work() as ctx:
            return ctx.feedback.generate(attempt_id, **kwargs)

    def run_chain(self, attempt_id: str = ATTEMPT_ID) -> Any:
        """Score, determine pass/fail, generate feedback -- the pipeline's three stages."""
        self.score(attempt_id)
        self.determine(attempt_id)
        return self.generate_feedback(attempt_id)


def build_world(*, clock: FixedClock | None = None) -> ResultsWorld:
    """Build a world on its own in-memory database created from the models."""
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    session_factory = create_session_factory(engine)

    from app.db.metadata import target_metadata

    target_metadata.create_all(engine)

    attempts = FakeAttemptSource()
    answer_keys = FakeAnswerKeys()
    content = FakeQuestionContent()
    certificates = ControllableCertificateService()
    cpd = ControllableCpdService()

    ports = ResultsPorts(
        # Outside the chain: faked.
        attempts=lambda _session: attempts,
        answer_keys=lambda _session: answer_keys,
        policies=lambda _session: attempts,
        content=lambda _session: content,
        certificates=certificates,
        cpd=cpd,
        # Inside the chain: the real adapters, reading the real rows the previous stage wrote.
        scores=ScoringResultAdapter,
        score_details=ScoringDetailAdapter,
        outcomes=CertificationOutcomeAdapter,
    )

    fixed = clock or FixedClock(SUBMITTED_AT)
    context = ResultsAppContext(session_factory=session_factory, clock=fixed, ports=ports)
    return ResultsWorld(
        context=context,
        engine=engine,
        clock=fixed,
        attempts=attempts,
        answer_keys=answer_keys,
        content=content,
        certificates=certificates,
        cpd=cpd,
    )


def world_fixture() -> Iterator[ResultsWorld]:
    """Generator body shared by the three suites' ``world`` fixtures."""
    built = build_world()
    try:
        yield built
    finally:
        built.dispose()


def elapsed_seconds(started: str = STARTED_AT, submitted: str = SUBMITTED_AT) -> int:
    """The time-taken figure a report should show for the default attempt."""
    delta: timedelta = parse_instant(submitted) - parse_instant(started)
    return int(delta.total_seconds())


#: The same figure, under the name the first UC-04 tests used for it.
def expected_time_taken() -> int:
    return elapsed_seconds()


def instant(value: str) -> datetime:
    return parse_instant(value)
