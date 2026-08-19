"""Configuration vocabulary.

``QuestionType`` is deliberately **not** redefined here. There is exactly one question-type
vocabulary in the system, and it lives in the shared kernel (:mod:`app.core.question_types`) —
not in UC-02, and not copied into UC-01. Both capabilities read the same names from the same
place, so neither has to import the other to talk about a question type.

Delivery mode *is* UC-01's own, so it is defined here. The attempt lifecycle is not: attempts
belong to UC-03 (:mod:`app.modules.attempt_delivery.domain.enums`).
"""

from __future__ import annotations

from enum import StrEnum

from app.core.question_types import (
    QUESTION_TYPE_LABELS,
    QUESTION_TYPE_ORDER,
    QuestionType,
)


class DeliveryMode(StrEnum):
    PRACTICE = "practice"
    ASSESSMENT = "assessment"
    EXAM = "exam"


DELIVERY_MODE_LABELS: dict[DeliveryMode, str] = {
    DeliveryMode.PRACTICE: "Practice (immediate feedback, untimed friendly)",
    DeliveryMode.ASSESSMENT: "Assessment (graded, feedback after submission)",
    DeliveryMode.EXAM: "Exam (graded, no feedback, timed)",
}

def delivery_mode_requires_time_limit(mode: DeliveryMode) -> bool:
    """``exam`` delivery is always time-boxed; a missing limit is a configuration error."""
    return mode is DeliveryMode.EXAM


__all__ = [
    "DELIVERY_MODE_LABELS",
    "QUESTION_TYPE_LABELS",
    "QUESTION_TYPE_ORDER",
    "DeliveryMode",
    "QuestionType",
    "delivery_mode_requires_time_limit",
]
