"""Privacy guarantees enforced structurally, not by convention."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.conftest import build_client, auth, build_harness
from uc07.domain.models import (
    FORBIDDEN_FIELD_NAMES,
    FeedbackRecord,
    Gap,
    GapReport,
    InteractionRecord,
)

#: Files allowed to mention question text at all - and only to forbid it.
QUESTION_TEXT_ALLOWLIST = {
    Path("uc07/domain/models.py"),
    Path("uc07/adapters/real/_template.py"),
    Path("uc07/observability.py"),
}


def test_no_domain_model_has_a_question_text_field():
    for model in (InteractionRecord, FeedbackRecord, Gap, GapReport):
        for field_name in model.model_fields:
            assert field_name not in FORBIDDEN_FIELD_NAMES, (model.__name__, field_name)


@pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELD_NAMES))
def test_interaction_record_rejects_forbidden_fields(field):
    payload = {
        "interaction_id": "i1",
        "session_id": "s1",
        "user_id": "u1",
        "asked_at": "2026-01-01T00:00:00+00:00",
        "topic_tag": "alpha",
        "question_class": "concept",
        "naric_level": "LEVEL_6",
        "response_id": "r1",
        field: "should never be accepted",
    }
    with pytest.raises(ValidationError):
        InteractionRecord(**payload)


def test_question_text_is_only_ever_mentioned_in_order_to_forbid_it():
    for path in sorted(Path("uc07").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "question_text" not in text:
            continue
        assert path in QUESTION_TEXT_ALLOWLIST, path


def test_no_code_reads_a_question_text_key():
    """Even in the allowlisted files, no expression may subscript question text."""
    for path in sorted(Path("uc07").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                assert node.slice.value not in FORBIDDEN_FIELD_NAMES, path
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_FIELD_NAMES, path


def test_feedback_comments_never_reach_a_report():
    harness = build_harness("struggle_mixed")
    report = harness.service.current_report(harness.user_id).report
    assert report is not None
    dumped = report.model_dump_json()
    for raw in harness.scenario.feedback.records:
        comment = raw.get("comment")
        if comment:
            assert comment not in dumped


def test_openapi_schema_exposes_no_question_or_identity_fields():
    spec = build_client("struggle_mixed").get("/openapi.json").json()
    rendered = str(spec)
    for forbidden in sorted(FORBIDDEN_FIELD_NAMES) + ["user_id"]:
        assert forbidden not in rendered


def test_report_payload_carries_no_learner_identity():
    response = build_client("struggle_mixed").get("/api/v1/gap-report", headers=auth())
    report = response.json()["report"]
    assert "user_id" not in report
    assert "learner" not in str(report.keys())
