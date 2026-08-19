"""UC-02 (Question Bank Management) — contract types consumed by UC-03.

UC-03 reads questions and snapshots them onto the attempt. It never authors,
edits, retires, or validates question *content* — that is UC-02's role. UC-03
validates only a learner's *answer* against the delivered question structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.attempt_delivery.domain.enums import QuestionType


@dataclass(frozen=True, slots=True)
class QuestionOption:
    """A selectable option for choice-based questions."""

    option_id: str
    text: str
    #: Retained in the bank and in the attempt snapshot for downstream grading.
    #: The API presenters strip this before anything reaches a learner.
    is_correct: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"optionId": self.option_id, "text": self.text, "isCorrect": self.is_correct}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuestionOption:
        return cls(option_id=raw["optionId"], text=raw["text"], is_correct=raw.get("isCorrect"))


@dataclass(frozen=True, slots=True)
class QuestionOrderItem:
    """An orderable item for ``DRAG_TO_ORDER`` questions."""

    item_id: str
    text: str
    #: 1-based position in the correct sequence. Stripped from learner responses.
    correct_position: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"itemId": self.item_id, "text": self.text, "correctPosition": self.correct_position}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuestionOrderItem:
        return cls(
            item_id=raw["itemId"], text=raw["text"], correct_position=raw.get("correctPosition")
        )


@dataclass(frozen=True, slots=True)
class ScenarioSubQuestion:
    """A sub-question inside a ``SCENARIO`` question.

    ``SCENARIO`` is modelled as a shared stem plus one or more sub-questions, each
    behaving as a primitive type. This keeps answer validation recursive and
    avoids inventing a bespoke scenario answer format. Nested scenarios are not
    permitted.
    """

    sub_question_id: str
    type: QuestionType
    prompt: str
    options: tuple[QuestionOption, ...] = ()
    order_items: tuple[QuestionOrderItem, ...] = ()
    #: ``MULTI_SELECT`` selection bounds.
    min_selections: int | None = None
    max_selections: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subQuestionId": self.sub_question_id,
            "type": str(self.type),
            "prompt": self.prompt,
            "options": [option.to_dict() for option in self.options],
            "orderItems": [item.to_dict() for item in self.order_items],
            "minSelections": self.min_selections,
            "maxSelections": self.max_selections,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ScenarioSubQuestion:
        return cls(
            sub_question_id=raw["subQuestionId"],
            type=QuestionType(raw["type"]),
            prompt=raw["prompt"],
            options=tuple(QuestionOption.from_dict(item) for item in raw.get("options") or []),
            order_items=tuple(
                QuestionOrderItem.from_dict(item) for item in raw.get("orderItems") or []
            ),
            min_selections=raw.get("minSelections"),
            max_selections=raw.get("maxSelections"),
        )


@dataclass(frozen=True, slots=True)
class BankQuestion:
    """A question as delivered by UC-02.

    ``version`` supports UC-02's historical-preservation requirement: the attempt
    snapshot records the exact version delivered, so later edits to the bank never
    alter a question a learner already saw.
    """

    question_id: str
    version: int
    type: QuestionType
    prompt: str

    quiz_id: str | None = None
    course_id: str | None = None
    topic_id: str | None = None

    #: ``SINGLE_CHOICE`` / ``MULTI_SELECT``.
    options: tuple[QuestionOption, ...] = ()
    #: ``DRAG_TO_ORDER``.
    order_items: tuple[QuestionOrderItem, ...] = ()
    #: ``SCENARIO``.
    scenario_text: str | None = None
    sub_questions: tuple[ScenarioSubQuestion, ...] = ()

    #: ``MULTI_SELECT`` selection bounds.
    min_selections: int | None = None
    max_selections: int | None = None

    #: Marks available. Carried through for downstream grading.
    points: float = 1.0

    #: True when UC-02 has retired the question. Retired questions must never be
    #: selected for a *new* attempt, but stay readable so in-flight attempts and
    #: historical records remain intact.
    retired: bool = False

    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the attempt's frozen question snapshot."""
        return {
            "questionId": self.question_id,
            "version": self.version,
            "type": str(self.type),
            "prompt": self.prompt,
            "quizId": self.quiz_id,
            "courseId": self.course_id,
            "topicId": self.topic_id,
            "options": [option.to_dict() for option in self.options],
            "orderItems": [item.to_dict() for item in self.order_items],
            "scenarioText": self.scenario_text,
            "subQuestions": [sub.to_dict() for sub in self.sub_questions],
            "minSelections": self.min_selections,
            "maxSelections": self.max_selections,
            "points": self.points,
            "retired": self.retired,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BankQuestion:
        return cls(
            question_id=raw["questionId"],
            version=int(raw["version"]),
            type=QuestionType(raw["type"]),
            prompt=raw["prompt"],
            quiz_id=raw.get("quizId"),
            course_id=raw.get("courseId"),
            topic_id=raw.get("topicId"),
            options=tuple(QuestionOption.from_dict(item) for item in raw.get("options") or []),
            order_items=tuple(
                QuestionOrderItem.from_dict(item) for item in raw.get("orderItems") or []
            ),
            scenario_text=raw.get("scenarioText"),
            sub_questions=tuple(
                ScenarioSubQuestion.from_dict(item) for item in raw.get("subQuestions") or []
            ),
            min_selections=raw.get("minSelections"),
            max_selections=raw.get("maxSelections"),
            points=float(raw.get("points", 1.0)),
            retired=bool(raw.get("retired", False)),
            extra=dict(raw.get("extra") or {}),
        )


@dataclass(frozen=True, slots=True)
class QuestionQuery:
    """Filter passed to the question bank when selecting an attempt's questions."""

    quiz_id: str
    course_id: str
    types: tuple[QuestionType, ...] = ()
    topic_ids: tuple[str, ...] = ()
    #: When True (the default), retired questions are excluded.
    exclude_retired: bool = True


@dataclass(frozen=True, slots=True)
class DeliveredQuestionRef:
    """One question as delivered, in the terms UC-02 needs to record its usage.

    Deliberately minimal: the id, the version and the position. UC-02 does not need the learner's
    answers, and UC-03 does not hand them over.
    """

    question_id: str
    question_version: int
    #: 1-based position within the attempt's locked question order.
    position: int
