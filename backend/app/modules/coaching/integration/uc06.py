"""UC-06 (Detailed Feedback Report) — the release gate and the teaching context (§2, §7, §11).

Two different things arrive through this one port, and keeping them apart is most of what this
file is for.

**A gate.** ``FeedbackStatus`` must be ``AVAILABLE`` before any coaching may begin (§7). Coaching
is a conversation *about the feedback*; offering it while the report is still pending would mean
coaching a learner on a result they have not been shown.

**Teaching context — some of which is poison.** A UC-06 question feedback record is built to tell
the learner what the right answer was and why. That makes it the single richest source of topic and
lesson information in the system, and simultaneously the most dangerous thing to forward to a model
that must not know the answer.

So the port models the record *as UC-06 actually produces it*, correct answer and explanation
included, and the sanitiser decides field by field what survives:

==========================  =========  =====================================================
Field                        Survives?  Why
==========================  =========  =====================================================
``topics``                   yes        The concept being taught. The point of the coaching.
``lesson_reference``         yes        A pointer to material, not the answer to the question.
``misconception_note``       yes        Written to describe the *error*, not the answer.
``learner_answer_summary``   yes        The learner's own answer, already known to them (§11).
``explanation``              **no**     Written to state and justify the correct answer (§12).
``correct_answer_text``      **no**     It is the answer key in prose (§12).
``correct_option_ids``       **no**     It is the answer key in identifiers (§12).
==========================  =========  =====================================================

``explanation`` is the interesting refusal. §11 lists "relevant feedback context" as something the
coaching context may contain, and §12 forbids the correct answer; a UC-06 explanation is both at
once. The tie is broken in favour of §12, which the specification calls a critical security
requirement — and in favour of the teaching design, since a coach handed the written explanation
would paraphrase it instead of asking anything (§14). The learner can already read it in their
feedback report; the coach has to work without it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class FeedbackStatus(StrEnum):
    """Whether UC-06 has released the feedback report for an attempt (§7)."""

    #: Generated and readable by the learner. The only status that permits coaching.
    AVAILABLE = "AVAILABLE"
    #: Not generated yet — upstream was not ready, or generation has not run.
    PENDING = "PENDING"
    #: Generation was attempted and failed. Retriable by UC-06, not by UC-07.
    FAILED = "FAILED"
    #: No feedback record exists for this attempt at all.
    NOT_FOUND = "NOT_FOUND"


#: The single feedback state in which coaching may begin (§7).
ELIGIBLE_FEEDBACK_STATUSES: frozenset[FeedbackStatus] = frozenset({FeedbackStatus.AVAILABLE})


@dataclass(frozen=True, slots=True)
class LessonReference:
    """A pointer to course material for a question's topic (§11).

    Safe to carry into the coaching context: it names where to go and learn the concept, which is
    what a coach should be steering towards anyway. It does not say which option was right.
    """

    lesson_id: str
    title: str | None = None
    url: str | None = None
    topic: str | None = None
    module_title: str | None = None


@dataclass(frozen=True, slots=True)
class QuestionFeedback:
    """UC-06's feedback for one question, exactly as UC-06 produced it.

    Everything from ``explanation`` downwards is answer-bearing and is listed here so the sanitiser
    has a named thing to remove — see the table in the module docstring. Nothing in the domain or
    the services reads those fields.
    """

    question_id: str
    topics: tuple[str, ...] = field(default_factory=tuple)
    lesson_reference: LessonReference | None = None

    #: A description of *why the learner's answer went wrong* — a misconception, not an answer.
    #: Optional, and frequently absent; coaching does not depend on it.
    misconception_note: str | None = None

    #: A rendering of what the learner submitted, for display. Carried, never annotated with
    #: correctness (§12).
    learner_answer_summary: str | None = None

    # ---- Answer-bearing. Removed at the sanitisation boundary (§12, §13). ----
    explanation: str | None = None
    correct_answer_text: str | None = None
    correct_option_ids: tuple[str, ...] = field(default_factory=tuple)
    #: Anything else UC-06's record happened to carry. Untrusted; dropped wholesale.
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AttemptFeedback:
    """The feedback report's availability for one attempt, plus its per-question records."""

    attempt_id: str
    status: FeedbackStatus
    learner_id: str | None = None
    course_id: str | None = None
    generated_at: str | None = None
    question_feedback: tuple[QuestionFeedback, ...] = field(default_factory=tuple)

    @property
    def available(self) -> bool:
        return self.status in ELIGIBLE_FEEDBACK_STATUSES

    def feedback_for(self, question_id: str) -> QuestionFeedback | None:
        return next(
            (item for item in self.question_feedback if item.question_id == question_id), None
        )


@runtime_checkable
class FeedbackProvider(Protocol):
    """Read-only port onto UC-06.

    An implementation that cannot reach UC-06 must raise
    ``app.modules.coaching.domain.errors.UpstreamProviderUnavailableError`` rather than returning a
    ``PENDING`` placeholder:
    "we could not ask" and "the report is not ready" lead to different messages for the learner.
    """

    async def get_attempt_feedback(self, attempt_id: str) -> AttemptFeedback | None:
        """Return the feedback record for the attempt, or ``None`` when none exists."""
        ...
