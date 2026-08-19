"""The contract UC-01 has with the question bank.

This is the seam that makes UC-01 + UC-02 one workflow instead of two codebases that happen to
share a database. The configuration service never queries questions, never learns how a question
is stored, and never has to *remember* to exclude retired ones — it asks for eligible counts and
eligible questions, and the adapter answers from the single bank.

Why a port at all
-----------------
* **One exclusion rule.** "Retired questions do not count and are never delivered" is enforced in
  one place (the question bank's delivery query) rather than restated in the capacity check, the
  rules summary and the start-quiz path.
* **Replaceable.** Tomorrow the company database sits behind the same adapter; the day the
  question bank moves out of process, an HTTP adapter implements this protocol instead. Neither
  changes a business rule.
* **Testable.** A fake implementation exercises the configuration rules without a question bank.

Nothing in this file imports SQLAlchemy or the question bank's models — the DTOs below are the
whole vocabulary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.modules.quiz_configuration.domain.enums import QuestionType


@dataclass(frozen=True, slots=True)
class TopicRef:
    """A question-bank topic, as UC-01 needs to see it."""

    id: str
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class BankScope:
    """Which slice of the bank a configuration considers eligible.

    Empty ``topic_ids`` means the whole active bank. ``types`` narrows the counts to the types a
    configuration actually selected, so the adapter never counts more than it needs to.
    """

    types: tuple[QuestionType, ...] = ()
    topic_ids: tuple[str, ...] = ()


class QuestionBankPort(Protocol):
    """What UC-01 requires of a question bank — which is only ever *counting* and *naming*.

    Deliberately narrow. UC-01 validates a configuration against the bank's capacity and freezes a
    topic scope onto a version; it never draws questions and never delivers them, because it does
    not own attempts. Selecting a paper is UC-03's job, through its own port.
    """

    def available_by_type(self, scope: BankScope) -> dict[QuestionType, int]:
        """Count questions eligible for **future** delivery, grouped by type.

        Must exclude anything not deliverable — retired and draft questions above all. Types with
        no eligible questions must appear with a count of ``0`` rather than being omitted.
        """
        ...

    def resolve_topics(self, topic_ids: Sequence[str]) -> list[TopicRef]:
        """Resolve topic ids to references, skipping ids that do not exist."""
        ...


@dataclass(slots=True)
class FakeQuestionBank:
    """In-memory :class:`QuestionBankPort` for testing configuration rules in isolation.

    Lives beside the protocol rather than in the test tree so the protocol and its reference
    behaviour stay together, and so a future adapter has something to diff against.
    """

    counts: dict[QuestionType, int] = field(default_factory=dict)
    topics: dict[str, TopicRef] = field(default_factory=dict)

    def available_by_type(self, scope: BankScope) -> dict[QuestionType, int]:
        types = scope.types or tuple(QuestionType)
        return {question_type: int(self.counts.get(question_type, 0)) for question_type in types}

    def resolve_topics(self, topic_ids: Sequence[str]) -> list[TopicRef]:
        return [self.topics[topic_id] for topic_id in topic_ids if topic_id in self.topics]


@dataclass(frozen=True, slots=True)
class OpenAttempt:
    """An attempt this learner has in flight, as UC-01's rules summary needs to see it."""

    id: str
    attempt_number: int
    status: str
    configuration_version_id: str | None


class AttemptStatisticsPort(Protocol):
    """What UC-01 needs to know about attempts — and nothing more.

    UC-01 does **not** own attempts; UC-03 does. But two of UC-01's own requirements depend on
    counting them: the learner rules summary reports remaining attempts, and the version history
    reports how many attempts locked onto each version.

    Rather than keep a second attempt table for that (which is what UC-01 did before UC-03 existed),
    it asks through this port. Read-only by construction: there is no method here that creates,
    modifies or submits an attempt.
    """

    def count_by_configuration_version(self, version_ids: Sequence[int]) -> dict[int, int]:
        """How many attempts locked onto each version. One query, not one per version."""
        ...

    def count_for_learner(self, quiz_id: int, learner_id: str) -> int:
        """Attempts this learner has started, in any state.

        Every started attempt counts against the allowance — otherwise abandoning and restarting
        would bypass the maximum-attempts rule.
        """
        ...

    def find_open_for_learner(self, quiz_id: int, learner_id: str) -> OpenAttempt | None:
        """The learner's in-flight attempt, when there is one."""
        ...
