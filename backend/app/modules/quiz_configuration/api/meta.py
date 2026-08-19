"""Configuration vocabulary, published at runtime.

The admin UI mirrors the configuration rules so it can validate before saving. This endpoint lets
it read the authoritative enums and numeric limits instead of hardcoding them, so a limit change
in :mod:`app.modules.quiz_configuration.domain.rules` reaches the UI without a frontend release.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.question_types import PRESENTATION_LABELS, QuestionPresentation, QuestionStatus
from app.modules.quiz_configuration.domain.enums import (
    DELIVERY_MODE_LABELS,
    QUESTION_TYPE_LABELS,
    DeliveryMode,
    QuestionType,
)
from app.modules.quiz_configuration.domain.rules import LIMITS, MAX_CONFIGURATION_TOPICS

router = APIRouter(tags=["Quiz Configuration — Meta"])


@router.get(
    "/meta",
    summary="Question types, delivery and presentation modes, and the numeric configuration limits",
)
def meta() -> dict[str, Any]:
    return {
        # The single question-type vocabulary, owned by the question bank and selected from here.
        "questionTypes": [
            {"value": question_type.value, "label": QUESTION_TYPE_LABELS[question_type]}
            for question_type in QuestionType
        ],
        "deliveryModes": [
            {"value": mode.value, "label": DELIVERY_MODE_LABELS[mode]} for mode in DeliveryMode
        ],
        # How UC-03 hands the paper over. Published here because it is an *authoring* choice made on
        # this form, even though the capability that honours it is UC-03.
        "questionPresentations": [
            {"value": presentation.value, "label": PRESENTATION_LABELS[presentation]}
            for presentation in QuestionPresentation
        ],
        "limits": LIMITS,
        "maxConfigurationTopics": MAX_CONFIGURATION_TOPICS,
        # Which question statuses a future quiz can draw from, so the UI can explain why a
        # retired question stopped counting towards capacity.
        "deliverableQuestionStatuses": [QuestionStatus.ACTIVE.value],
    }
