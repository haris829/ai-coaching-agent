"""Downstream submission hand-off boundary.

Confirming a submission has two distinct halves:

1. **Local commit** — lock the attempt and freeze its answers. UC-03 owns this and
   it is always transactional.
2. **Downstream hand-off** — notify the grading/results capability that a completed
   attempt is ready. That capability lives outside UC-03 (a future use case), so it
   can fail transiently.

Modelling the hand-off as a port is what makes the *pending submission* state
meaningful and testable: a transient failure here leaves the attempt locked but the
submission retriable, exactly as required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class DispatchAnswer:
    """One frozen answer, in delivery order."""

    question_id: str
    position: int
    answered: bool
    response: Any


@dataclass(frozen=True, slots=True)
class SubmissionDispatchRequest:
    """The completed attempt handed to the downstream grading capability."""

    attempt_id: str
    submission_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    configuration_version_id: str
    submitted_at: str
    submission_reason: str
    answered_count: int
    total_questions: int
    answers: tuple[DispatchAnswer, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmissionDispatchResult:
    """Acknowledgement from the downstream system."""

    downstream_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TransientDispatchError(Exception):
    """Raised when the failure is transient and the submission should be retried.

    A dispatcher raising this leaves the submission ``PENDING`` and the attempt
    ``SUBMISSION_PENDING``. Any *other* exception is treated as permanent: the
    submission is marked ``FAILED`` and the attempt is released back to ``ACTIVE``
    so the learner is not stranded.
    """


class SubmissionDispatchPort(Protocol):
    """Hand-off of a completed attempt to the grading capability."""

    def dispatch(self, request: SubmissionDispatchRequest) -> SubmissionDispatchResult:
        ...


class NoopSubmissionDispatch:
    """Default dispatcher: succeeds immediately and records nothing.

    Until the grading use case exists, a confirmed submission is complete once
    UC-03 has locked the attempt, so a no-op is the honest default rather than a
    simulated integration.
    """

    __slots__ = ()

    def dispatch(self, request: SubmissionDispatchRequest) -> SubmissionDispatchResult:
        return SubmissionDispatchResult()
