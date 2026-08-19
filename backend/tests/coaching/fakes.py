"""Test doubles for every UC-07 boundary.

These live in ``tests`` and nowhere else. Production code contains no fake attempt provider, no fake
feedback report and — above all — no fake coach: the shipped defaults either return nothing honestly
(``Unconfigured*``) or report themselves unavailable (§6).

``FakeCoachingLLM`` deserves a note, because it is the one double whose existence could be
misread. It is not a chatbot and it is not pretending to be one. It is an *observation point*: it
records the exact request the module would have sent to a real provider, which is what the security
tests assert against (§25). Its default reply is derived from the request rather than fixed, so a
test that gets a coaching reply has proved the request was well formed — not that someone wrote a
plausible sentence into a fixture.

Every double is programmable in the ways the specification's failure scenarios require: be
unreachable, time out, answer with rubbish, answer in violation of the coaching policy, or count
calls.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import PersistenceFailedError
from app.modules.coaching.domain.errors import (
    CoachingServiceUnavailableError,
    CoachingTimeoutError,
    UpstreamProviderUnavailableError,
)
from app.modules.coaching.domain.session import CoachingSession
from app.modules.coaching.integration.activity import CoachingActivityEvent
from app.modules.coaching.integration.knowledge_gaps import KnowledgeGapEvent
from app.modules.coaching.integration.llm import CoachingCompletion, CoachingRequest
from app.modules.coaching.integration.uc03 import (
    AttemptContext,
    DeliveredQuestion,
    LearnerAnswer,
)
from app.modules.coaching.integration.uc04 import AttemptScore
from app.modules.coaching.integration.uc06 import AttemptFeedback
from app.modules.coaching.repositories.in_memory import InMemoryCoachingSessionRepository

# ---------------------------------------------------------------------------
# Upstream use cases
# ---------------------------------------------------------------------------


class FakeAttemptProvider:
    """Stands in for UC-03."""

    def __init__(self) -> None:
        self.attempts: dict[str, AttemptContext] = {}
        self.delivered: dict[str, tuple[DeliveredQuestion, ...]] = {}
        self.answers: dict[str, tuple[LearnerAnswer, ...]] = {}
        self.unavailable = False
        self.calls: list[str] = []

    def set(self, attempt: AttemptContext) -> AttemptContext:
        self.attempts[attempt.attempt_id] = attempt
        return attempt

    def set_delivered(self, attempt_id: str, questions: Sequence[DeliveredQuestion]) -> None:
        self.delivered[attempt_id] = tuple(questions)

    def set_answers(self, attempt_id: str, answers: Sequence[LearnerAnswer]) -> None:
        self.answers[attempt_id] = tuple(answers)

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        self.calls.append(f"get_attempt:{attempt_id}")
        self._maybe_fail()
        return self.attempts.get(attempt_id)

    async def get_delivered_questions(self, attempt_id: str) -> tuple[DeliveredQuestion, ...]:
        self.calls.append(f"get_delivered_questions:{attempt_id}")
        self._maybe_fail()
        return self.delivered.get(attempt_id, ())

    async def get_learner_answers(self, attempt_id: str) -> tuple[LearnerAnswer, ...]:
        self.calls.append(f"get_learner_answers:{attempt_id}")
        self._maybe_fail()
        return self.answers.get(attempt_id, ())

    def _maybe_fail(self) -> None:
        if self.unavailable:
            raise UpstreamProviderUnavailableError("uc03")


class FakeScoringProvider:
    """Stands in for UC-04."""

    def __init__(self) -> None:
        self.scores: dict[str, AttemptScore] = {}
        self.unavailable = False
        self.calls: list[str] = []

    def set(self, score: AttemptScore) -> AttemptScore:
        self.scores[score.attempt_id] = score
        return score

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        self.calls.append(f"get_score:{attempt_id}")
        if self.unavailable:
            raise UpstreamProviderUnavailableError("uc04")
        return self.scores.get(attempt_id)


class FakeFeedbackProvider:
    """Stands in for UC-06."""

    def __init__(self) -> None:
        self.records: dict[str, AttemptFeedback] = {}
        self.unavailable = False
        self.calls: list[str] = []

    def set(self, feedback: AttemptFeedback) -> AttemptFeedback:
        self.records[feedback.attempt_id] = feedback
        return feedback

    async def get_attempt_feedback(self, attempt_id: str) -> AttemptFeedback | None:
        self.calls.append(f"get_attempt_feedback:{attempt_id}")
        if self.unavailable:
            raise UpstreamProviderUnavailableError("uc06")
        return self.records.get(attempt_id)


# ---------------------------------------------------------------------------
# The AI coach
# ---------------------------------------------------------------------------


def request_strings(request: CoachingRequest, *, include_learner: bool = True) -> list[str]:
    """Every string the module would have sent to a provider.

    The security tests search this, not just the context payload: the system prompt, the rendered
    question block and the replayed conversation are all part of the model's input, and a leak in
    any of them would be a leak (§25).

    ``include_learner=False`` drops the learner's own turns. That is what an adversarial-prompt test
    wants: a learner who types "print the answer_key" has put the *phrase* into the conversation,
    and finding their own words there proves nothing about what the system disclosed. What matters
    is that everything UC-07 contributed — the policy, the question context and the coach's replies
    — still contains nothing.
    """
    collected: list[str] = [request.system_prompt, request.mode]
    collected.extend(_strings(request.context))
    for message in request.conversation:
        if not include_learner and message.get("role") == "LEARNER":
            continue
        collected.extend(str(value) for value in message.values())
    return [item for item in collected if isinstance(item, str)]


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [item for value in node.values() for item in _strings(value)] + [
            str(key) for key in node
        ]
    if isinstance(node, list | tuple):
        return [item for value in node for item in _strings(value)]
    return []


class FakeCoachingLLM:
    """A programmable stand-in for the AI coaching provider.

    Defaults to a *derived* Socratic reply: it echoes the topic it was actually given and ends with
    a question, so a passing test proves the request carried what the coach needs. Nothing about it
    resembles a coaching script, and the module has no comparable default of its own (§6).
    """

    configured = True

    def __init__(self) -> None:
        self.requests: list[CoachingRequest] = []
        self.available = True
        self.availability_raises = False
        #: Raise this on the next ``fail_times`` calls, then behave normally.
        self.failure: Exception | None = None
        self.fail_times = 0
        #: Explicit replies, consumed in order, before falling back to the derived one.
        self.scripted: list[str] = []
        #: Full control when a test needs a reply that depends on the request.
        self.responder: Callable[[CoachingRequest], Any] | None = None

    # -- programming helpers ------------------------------------------------

    def fail_with(self, error: Exception, times: int = 1) -> None:
        self.failure = error
        self.fail_times = times

    def go_offline(self, times: int = 1_000_000) -> None:
        self.fail_with(CoachingServiceUnavailableError(reason="TEST"), times)

    def time_out(self, times: int = 1_000_000) -> None:
        self.fail_with(CoachingTimeoutError(timeout_seconds=1.0), times)

    def reply_with(self, *texts: str) -> None:
        self.scripted.extend(texts)

    @property
    def last_request(self) -> CoachingRequest:
        return self.requests[-1]

    @property
    def call_count(self) -> int:
        return len(self.requests)

    # -- the port -----------------------------------------------------------

    async def is_available(self) -> bool:
        if self.availability_raises:
            raise RuntimeError("availability probe exploded")
        return self.available

    async def generate_response(self, request: CoachingRequest) -> CoachingCompletion:
        self.requests.append(request)

        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.failure or CoachingServiceUnavailableError(reason="TEST")

        if self.responder is not None:
            produced = self.responder(request)
            if isinstance(produced, CoachingCompletion):
                return produced
            return CoachingCompletion(text=produced, model="fake-coach", provider="fake")

        if self.scripted:
            return CoachingCompletion(
                text=self.scripted.pop(0), model="fake-coach", provider="fake"
            )

        return CoachingCompletion(
            text=self._derived(request), model="fake-coach", provider="fake"
        )

    def _derived(self, request: CoachingRequest) -> str:
        topics = request.context.get("topics") or []
        topic = topics[0] if topics else "this idea"
        if request.mode == "DIRECT_EXPLANATION":
            return (
                f"Here is the idea behind {topic}, in short: it is about what the situation "
                "requires of you, not about which words look familiar. Where would you apply it "
                "next?"
            )
        return (
            f"You have been thinking about {topic}. What was going through your mind when you "
            "settled on that answer?"
        )


# ---------------------------------------------------------------------------
# Outbound records
# ---------------------------------------------------------------------------


class FakeActivityLog:
    """Captures coaching activity events (§22)."""

    def __init__(self) -> None:
        self.events: list[CoachingActivityEvent] = []
        self.raises = False

    async def record(self, event: CoachingActivityEvent) -> None:
        if self.raises:
            raise RuntimeError("activity pipeline is down")
        self.events.append(event)

    def of_type(self, event_type: str) -> list[CoachingActivityEvent]:
        return [item for item in self.events if item.event_type.value == event_type]


class FakeKnowledgeGapTracker:
    """Captures knowledge-gap events (§21)."""

    def __init__(self) -> None:
        self.events: list[KnowledgeGapEvent] = []
        self.raises = False

    async def record_gap(self, event: KnowledgeGapEvent) -> None:
        if self.raises:
            raise RuntimeError("knowledge gap store is down")
        self.events.append(event)

    @property
    def topics(self) -> list[str | None]:
        return [item.topic for item in self.events]


@dataclass
class FlakySessionRepository:
    """Wraps the real in-memory repository so a test can make persistence fail.

    A thin wrapper rather than a reimplementation, so every call that is *not* being made to fail
    still exercises the real uniqueness and identity rules (§30).
    """

    inner: InMemoryCoachingSessionRepository = field(
        default_factory=InMemoryCoachingSessionRepository
    )
    fail_inserts: bool = False
    fail_updates: bool = False

    async def get(self, session_id: str) -> CoachingSession | None:
        return await self.inner.get(session_id)

    async def get_for_learner(self, learner_id: str, session_id: str) -> CoachingSession | None:
        return await self.inner.get_for_learner(learner_id, session_id)

    async def find_open(
        self, learner_id: str, attempt_id: str, question_id: str
    ) -> CoachingSession | None:
        return await self.inner.find_open(learner_id, attempt_id, question_id)

    async def list_for_attempt(
        self, learner_id: str, attempt_id: str
    ) -> tuple[CoachingSession, ...]:
        return await self.inner.list_for_attempt(learner_id, attempt_id)

    async def insert(self, session: CoachingSession) -> CoachingSession:
        if self.fail_inserts:
            raise PersistenceFailedError("coaching.session.fake")
        return await self.inner.insert(session)

    async def update(self, session: CoachingSession) -> CoachingSession:
        if self.fail_updates:
            raise PersistenceFailedError("coaching.session.fake")
        return await self.inner.update(session)
