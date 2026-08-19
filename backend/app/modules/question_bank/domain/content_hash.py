"""Deterministic content hashing.

The hash covers only *semantic* content — the things that make two questions the same
question. Presentation order, ids, timestamps, topics, difficulty and authorship are excluded,
so re-ordering the options of a question does not make it look like a different question.

Used for:
* duplicate detection on create and on CSV import;
* deciding whether an edit is content-changing and therefore needs a new snapshot version.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from app.modules.question_bank.domain.drafts import ValidatedQuestion
from app.modules.question_bank.domain.enums import QuestionType

_WHITESPACE = re.compile(r"\s+")


def normalise_text(value: str | None) -> str:
    """Collapse whitespace, strip case and normalise unicode for comparison purposes."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    return _WHITESPACE.sub(" ", text).strip().lower()


def compute_content_hash(question: ValidatedQuestion) -> str:
    """SHA-256 over the canonical semantic content of a question."""
    if question.type is QuestionType.DRAG_TO_ORDER:
        # For an ordering question the answer key IS the sequence, so it must be included in
        # sequence order rather than sorted.
        answer_part: object = [
            normalise_text(option.text)
            for option in sorted(
                question.options,
                key=lambda o: (o.correct_position is None, o.correct_position or 0),
            )
        ]
    else:
        # For choice types the option set is unordered; sort so that a re-ordered but otherwise
        # identical question hashes the same.
        answer_part = sorted(
            [
                [normalise_text(option.text), bool(option.is_correct)]
                for option in question.options
            ]
        )

    canonical = {
        "type": question.type.value,
        "questionText": normalise_text(question.question_text),
        "scenarioText": normalise_text(question.scenario_text),
        "answer": answer_part,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
