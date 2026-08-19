"""Snapshot payload construction and reading.

A snapshot is the frozen, self-contained representation of one version of a question. It is
written once, never updated, and is what historical reporting reads — which is precisely why
retiring or editing a question cannot break a completed attempt's report (UC-02 §16).

Crucially the payload keeps ``position`` (default presentation order) and ``correct_position``
(correct answer order) as separate fields, so a drag-to-order answer key survives even though
delivery is free to shuffle presentation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.time import utcnow
from app.modules.question_bank.domain.drafts import ValidatedQuestion
from app.modules.question_bank.domain.enums import QuestionType

SNAPSHOT_SCHEMA_VERSION = 1


def build_snapshot_payload(
    question: ValidatedQuestion,
    *,
    reference: str,
    topics: list[str],
    snapshot_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the JSON-serialisable frozen representation of a validated question."""
    stamp = (snapshot_at or utcnow()).isoformat()
    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "reference": reference,
        "type": question.type.value,
        "status": question.status.value,
        "questionText": question.question_text,
        "scenarioText": question.scenario_text,
        "explanation": question.explanation,
        "difficulty": question.difficulty.value if question.difficulty else None,
        "scoring": {
            "points": question.points,
            "scoringStrategy": question.scoring_strategy.value,
            "penaltyPerIncorrect": question.penalty_per_incorrect,
        },
        "options": [
            {
                "label": option.label,
                "text": option.text,
                # Default presentation order at snapshot time.
                "position": option.position,
                "isCorrect": option.is_correct,
                "isPrimary": option.is_primary,
                # Correct answer order — independent of `position`.
                "correctPosition": option.correct_position,
                "feedback": option.feedback,
            }
            for option in question.options
        ],
        "correctLabels": question.correct_labels,
        "correctOrder": question.correct_order,
        "primaryLabel": question.primary_label,
        # Topic NAMES are frozen here so a report survives a topic rename or deletion.
        "topics": topics,
        "snapshotAt": stamp,
    }


def dump_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def load_payload(raw: str) -> dict[str, Any]:
    """Parse a stored payload defensively — a corrupt row must not crash a report."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Reading a snapshot back for grading / reporting
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotOption:
    label: str
    text: str
    position: int
    is_correct: bool
    is_primary: bool
    correct_position: int | None
    feedback: str | None


@dataclass(frozen=True, slots=True)
class SnapshotView:
    """A parsed snapshot payload with the answer key resolved."""

    question_type: QuestionType
    question_text: str
    scenario_text: str | None
    explanation: str | None
    points: float
    scoring_strategy: str
    penalty_per_incorrect: float
    options: tuple[SnapshotOption, ...]

    @property
    def correct_labels(self) -> tuple[str, ...]:
        return tuple(o.label for o in self.options if o.is_correct)

    @property
    def correct_order(self) -> tuple[str, ...]:
        ordered = sorted(
            (o for o in self.options if o.correct_position is not None),
            key=lambda o: o.correct_position or 0,
        )
        return tuple(o.label for o in ordered)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(o.label for o in self.options)


def parse_snapshot_view(payload: dict[str, Any]) -> SnapshotView | None:
    """Turn a stored payload into a :class:`SnapshotView`, or ``None`` if unusable."""
    raw_type = payload.get("type")
    try:
        question_type = QuestionType(str(raw_type))
    except ValueError:
        return None

    raw_options = payload.get("options")
    if not isinstance(raw_options, list):
        return None

    options: list[SnapshotOption] = []
    for index, item in enumerate(raw_options):
        if not isinstance(item, dict):
            continue
        correct_position = item.get("correctPosition")
        options.append(
            SnapshotOption(
                label=str(item.get("label", "")),
                text=str(item.get("text", "")),
                position=int(item.get("position") or index + 1),
                is_correct=bool(item.get("isCorrect")),
                is_primary=bool(item.get("isPrimary")),
                correct_position=int(correct_position)
                if isinstance(correct_position, int)
                else None,
                feedback=item.get("feedback") if isinstance(item.get("feedback"), str) else None,
            )
        )

    if not options:
        return None

    scoring = payload.get("scoring") if isinstance(payload.get("scoring"), dict) else {}
    return SnapshotView(
        question_type=question_type,
        question_text=str(payload.get("questionText", "")),
        scenario_text=payload.get("scenarioText")
        if isinstance(payload.get("scenarioText"), str)
        else None,
        explanation=payload.get("explanation")
        if isinstance(payload.get("explanation"), str)
        else None,
        points=float(scoring.get("points", 1) or 1),
        scoring_strategy=str(scoring.get("scoringStrategy", "ALL_OR_NOTHING")),
        penalty_per_incorrect=float(scoring.get("penaltyPerIncorrect", 0) or 0),
        options=tuple(options),
    )
