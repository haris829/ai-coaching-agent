"""The safe coaching context — the only thing the model is ever given (§11, §12).

``SafeCoachingContext`` is an *allow-list expressed as a type*. There is no field on it that can
hold a correct answer, an answer key, a scoring key or a UC-06 explanation, so the question "did
the answer key reach the model?" is answered by reading this file rather than by auditing every
call site (§26).

What it carries, and why each item earns its place:

===========================  ====================================================================
Field                         Why the coach needs it
===========================  ====================================================================
``course_name``, ``topics``   The concept under discussion. Coaching is about the topic (§14).
``question_prompt``           The coach cannot ask a guiding question about an unseen question.
``options``                   The same choices the learner saw, in the same order, unlabelled.
                              A complete option set discriminates nothing (see below).
``learner_response``          The learner's own answer — theirs already, and the raw material of
                              a misconception (§11).
``misconception_note``        UC-06's description of *the error*, where one exists.
``lesson``                    Where to send the learner to learn the concept (§11).
``outcome``                   That this question went wrong. The learner knows; the coach must.
===========================  ====================================================================

**Why showing every option is not a leak.** The delivered option set is the question. What would
leak is anything that *distinguishes* one option from the others — a flag, a re-ordering, a
per-option mark, a "primary" marker. None of those has a field here, and ``position`` is copied
from the delivered order so the presentation cannot itself encode the answer (§12).

**Why the learner's answer carries no correctness annotation.** Telling the coach "two of your four
selections were right" hands over half the answer key, and telling it "your answer was wrong on
option B" hands over the rest. ``LearnerResponseView`` therefore has no field for it. The coach
knows the question went wrong as a whole, and nothing finer (§12).

**No learner identity.** There is no ``learner_id``, no name and no email in the model payload.
The session record links the conversation to a learner; the model does not need to know who it is
talking to in order to ask a good question (§22).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.coaching.integration.uc03 import QuestionType


@dataclass(frozen=True, slots=True)
class ContextOption:
    """One selectable option as the learner saw it.

    Three fields, and there will never be a fourth that says anything about correctness.
    """

    option_id: str
    text: str | None = None
    position: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"option_id": self.option_id, "text": self.text, "position": self.position}


@dataclass(frozen=True, slots=True)
class ContextOrderItem:
    """One orderable item, in the shuffled order it was presented in.

    ``position`` is the *delivered* position, never the solution position — carrying the latter
    would be handing over the answer to a DRAG_TO_ORDER question in full (§12).
    """

    item_id: str
    text: str | None = None
    position: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "text": self.text, "position": self.position}


@dataclass(frozen=True, slots=True)
class LearnerResponseView:
    """What the learner submitted, described without any correctness annotation (§11, §12)."""

    answered: bool
    #: Free-form, human-readable rendering of the submission, e.g. "selected: Record what you saw".
    summary: str | None = None
    selected_option_ids: tuple[str, ...] = field(default_factory=tuple)
    selected_option_labels: tuple[str, ...] = field(default_factory=tuple)
    ordered_item_ids: tuple[str, ...] = field(default_factory=tuple)
    ordered_item_labels: tuple[str, ...] = field(default_factory=tuple)
    boolean_value: bool | None = None
    free_text: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "answered": self.answered,
            "summary": self.summary,
            "selected_option_ids": list(self.selected_option_ids),
            "selected_option_labels": list(self.selected_option_labels),
            "ordered_item_ids": list(self.ordered_item_ids),
            "ordered_item_labels": list(self.ordered_item_labels),
            "boolean_value": self.boolean_value,
            "free_text": self.free_text,
        }


@dataclass(frozen=True, slots=True)
class LessonPointer:
    """Where the learner can go and learn the concept (§11)."""

    lesson_id: str
    title: str | None = None
    url: str | None = None
    topic: str | None = None
    module_title: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "url": self.url,
            "topic": self.topic,
            "module_title": self.module_title,
        }


@dataclass(frozen=True, slots=True)
class SafeCoachingContext:
    """Everything the AI coach is told about the question, and nothing else.

    Produced only by ``app.modules.coaching.domain.sanitizer.CoachingContextSanitizer``. No other
    code constructs one in production, which is what makes the sanitiser a boundary rather than a
    convention.
    """

    attempt_id: str
    course_id: str
    question_id: str
    question_type: QuestionType
    #: 1-based position in the delivered paper, so the coach can say "question 3".
    question_position: int

    course_name: str | None = None
    question_prompt: str | None = None
    scenario_text: str | None = None
    topics: tuple[str, ...] = field(default_factory=tuple)
    options: tuple[ContextOption, ...] = field(default_factory=tuple)
    order_items: tuple[ContextOrderItem, ...] = field(default_factory=tuple)
    learner_response: LearnerResponseView | None = None
    misconception_note: str | None = None
    lesson: LessonPointer | None = None
    #: Always "INCORRECT" in practice — coaching is offered for nothing else (§20). Named
    #: ``outcome`` rather than anything containing "correct" so the log deny-list and the
    #: contamination scanner never have to special-case it.
    outcome: str = "INCORRECT"

    @property
    def topic(self) -> str | None:
        """The primary topic, used for knowledge-gap tracking (§21)."""
        return self.topics[0] if self.topics else None

    def as_dict(self) -> dict[str, Any]:
        """The payload form. This is what the contamination scanner walks and what the prompt
        renderer reads — there is no second, richer representation anywhere (§26).
        """
        return {
            "attempt_id": self.attempt_id,
            "course_id": self.course_id,
            "course_name": self.course_name,
            "question_id": self.question_id,
            "question_type": self.question_type.value,
            "question_position": self.question_position,
            "question_prompt": self.question_prompt,
            "scenario_text": self.scenario_text,
            "topics": list(self.topics),
            "options": [item.as_dict() for item in self.options],
            "order_items": [item.as_dict() for item in self.order_items],
            "learner_response": (
                self.learner_response.as_dict() if self.learner_response else None
            ),
            "misconception_note": self.misconception_note,
            "lesson": self.lesson.as_dict() if self.lesson else None,
            "outcome": self.outcome,
        }
