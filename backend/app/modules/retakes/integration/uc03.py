"""UC-03 (Quiz Attempt Delivery) — the contract UC-08 consumes.

This is the only port UC-08 *writes* through, and the shape of the write is the whole design of
this module.

WHY UC-08 DOES NOT CREATE ATTEMPTS ITSELF
-----------------------------------------
UC-03 already owns attempt creation: the configuration lock, the question selection, the frozen
question snapshot, the delivery mode, the timer, the attempt-number uniqueness and the
one-open-attempt-per-quiz index. Rebuilding any of that here would be a second implementation of
the same rules, and the two would drift. So UC-08 decides **whether** a retake may happen, **which
configuration version** it runs under and **which questions should be avoided**, and then asks
UC-03 to do what it already does.

WHAT UC-08 CONTRIBUTES TO SELECTION
-----------------------------------
``RetakeAttemptRequest.deprioritised_question_ids`` — the questions the learner has already been
delivered. It is a **preference, never a filter**:

* UC-03 orders its candidate pool so unseen questions come first, then applies its existing
  count, quota, randomisation and eligibility rules unchanged;
* if the unseen questions do not fill the paper, the remainder comes from the seen ones;
* a retired or ineligible question is never reached for in order to avoid reuse — the pool UC-03
  selects from is the same eligible pool it always uses (§8).

That keeps every existing selection invariant intact and confines UC-08's contribution to the one
thing it actually knows: what this learner has seen before.

At integration this is one additive parameter on UC-03's existing
``QuestionSelectionService.select``. ``docs/INTEGRATION.md`` gives the exact change.

FAILURE SEMANTICS
-----------------
``create_retake_attempt`` must be all-or-nothing: either an attempt exists, or nothing was
written. A partially created attempt would leave UC-08's reservation and UC-03's records
disagreeing about how many attempts the learner has used. Transient failures should raise
``ProviderUnavailableError``; a refusal by UC-03's own rules should raise the matching UC-08
error so the caller sees one taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class AttemptStatus(StrEnum):
    """The attempt lifecycle values UC-08 cares about. UC-03 owns the full lifecycle."""

    ACTIVE = "ACTIVE"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    SUBMITTED = "SUBMITTED"


#: The only state from which a retake may be requested. A SUBMISSION_PENDING attempt is committed
#: but not finished end to end; retaking it would let a learner start a second attempt while the
#: first is still being handed downstream.
RETAKEABLE_ATTEMPT_STATUSES: frozenset[AttemptStatus] = frozenset({AttemptStatus.SUBMITTED})

#: Statuses that count as an attempt still open, which UC-03 permits only one of per quiz.
OPEN_ATTEMPT_STATUSES: frozenset[AttemptStatus] = frozenset(
    {AttemptStatus.ACTIVE, AttemptStatus.SUBMISSION_PENDING}
)


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """One attempt as recorded by UC-03.

    ``configuration_version_id`` is the version *locked to this attempt*, which is what makes an
    attempt's own rules stable no matter what UC-01 publishes afterwards.
    """

    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    #: 1-based attempt number for this learner on this quiz.
    attempt_number: int
    status: AttemptStatus
    configuration_version_id: str
    configuration_version_number: int | None = None
    started_at: str | None = None
    submitted_at: str | None = None
    total_questions: int | None = None
    course_name: str | None = None

    @property
    def retakeable(self) -> bool:
        return self.status in RETAKEABLE_ATTEMPT_STATUSES

    @property
    def open(self) -> bool:
        return self.status in OPEN_ATTEMPT_STATUSES


@dataclass(frozen=True, slots=True)
class RetakeAttemptRequest:
    """Everything UC-03 needs to deliver a retake, and nothing it does not.

    ``idempotency_key`` is UC-08's reservation key. Passing it through means a UC-03 adapter that
    supports idempotent creation converges on one attempt even if the call is retried after a
    timeout, rather than relying on UC-08 never retrying.
    """

    learner_id: str
    course_id: str
    quiz_id: str
    #: The version UC-08 resolved. UC-03 locks *this* version rather than re-resolving one, so
    #: the version cannot change between the eligibility decision and the delivery.
    configuration_version_id: str
    #: The slot UC-08 reserved. UC-03's own ``(learner, quiz, attempt_number)`` uniqueness is the
    #: second line of defence behind UC-08's reservation.
    attempt_number: int
    #: The attempt this retake follows. Establishes the lineage without a new structure.
    retake_of_attempt_id: str
    idempotency_key: str
    #: Preference, not a filter. See the module docstring.
    deprioritised_question_ids: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "configuration_version_id": self.configuration_version_id,
            "attempt_number": self.attempt_number,
            "retake_of_attempt_id": self.retake_of_attempt_id,
            "idempotency_key": self.idempotency_key,
            "deprioritised_question_ids": list(self.deprioritised_question_ids),
        }


@dataclass(frozen=True, slots=True)
class DeliveredAttempt:
    """The attempt UC-03 created, as UC-08 needs to see it.

    ``delivered_question_ids`` is in delivery order and is what UC-08 compares against the
    previous attempt to decide whether the retake is meaningfully different (§7).
    """

    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    attempt_number: int
    status: AttemptStatus
    configuration_version_id: str
    delivered_question_ids: tuple[str, ...]
    configuration_version_number: int | None = None
    started_at: str | None = None
    delivery_mode: str | None = None
    time_limit_seconds: int | None = None

    @property
    def total_questions(self) -> int:
        return len(self.delivered_question_ids)


@runtime_checkable
class AttemptProvider(Protocol):
    """Port onto UC-03. Read-only apart from :meth:`create_retake_attempt`."""

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        """The attempt, or ``None`` when it does not exist."""
        ...

    async def list_attempts(self, learner_id: str, quiz_id: str) -> tuple[AttemptContext, ...]:
        """Every attempt this learner has at this quiz, oldest first.

        The source of attempt history (§9) and of the "what has this learner already seen?"
        question. Implementations must not omit submitted attempts, however old.
        """
        ...

    async def count_used_attempts(self, learner_id: str, course_id: str, quiz_id: str) -> int:
        """How many attempts the learner has already used on this quiz.

        "Used" is UC-03's definition — every attempt that consumed an allowance, including one
        still in progress. UC-08 does not recount attempts itself; it adds only its own in-flight
        reservations, which are attempts UC-03 has not created yet.
        """
        ...

    async def find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        """The learner's attempt currently in progress at this quiz, if any."""
        ...

    async def get_delivered_question_ids(self, attempt_id: str) -> tuple[str, ...]:
        """The ids of the questions delivered in an attempt, in delivery order.

        Ids only. UC-08 compares sets of identifiers (§7) and has no use for the content, so the
        content does not cross this boundary.
        """
        ...

    async def create_retake_attempt(self, request: RetakeAttemptRequest) -> DeliveredAttempt:
        """Create the retake attempt. All-or-nothing. See the module docstring."""
        ...
