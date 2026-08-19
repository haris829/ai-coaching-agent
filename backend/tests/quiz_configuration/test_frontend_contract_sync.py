"""Guard against the test UI's mirrored rules drifting from the backend's.

The admin form validates before saving so an administrator gets instant feedback. Those rules live
in ``frontend/src/lib/configurationRules.ts`` and mirror
:mod:`app.modules.quiz_configuration.domain.rules`, which is authoritative. Nothing breaks
functionally if they drift — the API still rejects bad input — but the form would start showing the
wrong limits or the wrong vocabulary, so the two are pinned together here.

``/api/meta`` publishes the same values at runtime, which is the other half of the guarantee: the
UI can read the limits instead of trusting its copy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core.question_types import QUESTION_TYPE_LABELS, QuestionType
from app.modules.quiz_configuration.domain.enums import DELIVERY_MODE_LABELS, DeliveryMode
from app.modules.quiz_configuration.domain.rules import LIMITS, MAX_CONFIGURATION_TOPICS

FRONTEND_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
RULES_TS = FRONTEND_SRC / "lib" / "configurationRules.ts"
TYPES_TS = FRONTEND_SRC / "api" / "types.ts"

pytestmark = pytest.mark.skipif(
    not RULES_TS.exists(), reason="the frontend test UI is not present"
)


def _rules_source() -> str:
    return RULES_TS.read_text(encoding="utf-8")


def _types_source() -> str:
    return TYPES_TS.read_text(encoding="utf-8")


def test_question_types_match() -> None:
    """The single question-type vocabulary, declared once on each side."""
    match = re.search(r"QUESTION_TYPES\s*=\s*\[(.*?)\]", _types_source(), re.S)
    assert match is not None
    ts_types = re.findall(r"'([A-Z_]+)'", match.group(1))
    assert ts_types == [question_type.value for question_type in QuestionType]


def test_delivery_modes_match() -> None:
    match = re.search(r"DELIVERY_MODES\s*=\s*\[(.*?)\]", _rules_source(), re.S)
    assert match is not None
    ts_modes = re.findall(r"'([a-z_]+)'", match.group(1))
    assert ts_modes == [mode.value for mode in DeliveryMode]


def test_numeric_limits_match() -> None:
    match = re.search(r"CONFIGURATION_LIMITS\s*=\s*\{(.*?)\n\}", _rules_source(), re.S)
    assert match is not None
    ts_limits = {
        key: {"min": int(minimum), "max": int(maximum)}
        for key, minimum, maximum in re.findall(
            r"(\w+):\s*\{\s*min:\s*(\d+),\s*max:\s*(\d+)\s*\}", match.group(1)
        )
    }
    assert ts_limits == LIMITS


def test_topic_scope_limit_matches() -> None:
    match = re.search(r"MAX_CONFIGURATION_TOPICS\s*=\s*(\d+)", _rules_source())
    assert match is not None
    assert int(match.group(1)) == MAX_CONFIGURATION_TOPICS


def test_labels_match() -> None:
    source = _rules_source()
    for question_type, label in QUESTION_TYPE_LABELS.items():
        assert f"{question_type.value}: '{label}'" in source, question_type.value

    for mode, label in DELIVERY_MODE_LABELS.items():
        assert f"{mode.value}: '{label}'" in source, mode.value


def test_error_field_names_are_declared_on_both_sides() -> None:
    """Field names in validation errors are part of the UI contract."""
    source = _rules_source()
    for field_name in (
        "questionCount",
        "timeLimitMinutes",
        "passMark",
        "maxAttempts",
        "deliveryMode",
        "questionTypes",
        "randomiseQuestions",
        "topicIds",
    ):
        assert f"'{field_name}'" in source, field_name


def test_error_codes_are_declared_on_both_sides() -> None:
    """The mirror emits the same machine-readable codes, so a form keys off one set."""
    from app.modules.quiz_configuration.domain.rules import ValidationCode

    source = _rules_source()
    for code in ValidationCode:
        assert f"'{code.value}'" in source, code.value


def test_meta_endpoint_publishes_the_limits(ctx) -> None:
    """The UI can always read the authoritative values at runtime."""
    body = ctx.client.get("/api/meta").json()
    assert body["limits"] == json.loads(json.dumps(LIMITS))
    assert body["maxConfigurationTopics"] == MAX_CONFIGURATION_TOPICS
    assert [item["value"] for item in body["questionTypes"]] == [
        question_type.value for question_type in QuestionType
    ]
    assert [item["value"] for item in body["deliveryModes"]] == [
        mode.value for mode in DeliveryMode
    ]


def test_meta_labels_match_the_domain(ctx) -> None:
    body = ctx.client.get("/api/meta").json()
    assert {item["value"]: item["label"] for item in body["questionTypes"]} == {
        question_type.value: label for question_type, label in QUESTION_TYPE_LABELS.items()
    }
    assert {item["value"]: item["label"] for item in body["deliveryModes"]} == {
        mode.value: label for mode, label in DELIVERY_MODE_LABELS.items()
    }
