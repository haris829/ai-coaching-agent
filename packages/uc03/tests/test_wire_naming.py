"""Phase 0.2 - field names and enum values against the platform contract.

These names are read by components this repository has never seen, so a drift
must fail loudly here rather than silently at runtime. Anything asserted below
is either specified by the company or recorded in docs/assumptions.md.
"""

from __future__ import annotations

from uc03.domain.enums import (
    Classification,
    ClassificationKind,
    FollowUpAction,
    FramingStrategy,
    NaricLevelSource,
    RatingState,
    ResponseStatus,
)
from uc03.domain.models import QuestionLogRecord, QuestionResponse, ResponseMeta
from uc03.domain.topics import TOPIC_VOCABULARY, TopicTag

from .conftest import ALICE_SESSION


# --- specified by the company --------------------------------------------


def test_rating_field_is_rating_state_with_contract_values():
    assert "rating_state" in QuestionResponse.model_fields
    assert "rating_status" not in QuestionResponse.model_fields
    assert "rating_state" in QuestionLogRecord.model_fields
    assert "rating_status" not in QuestionLogRecord.model_fields
    assert {s.value for s in RatingState} == {"pending", "rated"}


def test_unclassified_tag_is_lowercase():
    assert TopicTag.UNCLASSIFIED.value == "unclassified"


def test_every_topic_tag_is_lowercase():
    for tag in TopicTag:
        assert tag.value == tag.value.lower(), tag
        assert " " not in tag.value


def test_naric_fields_are_named_per_contract():
    for model in (ResponseMeta, QuestionLogRecord):
        assert "naric_level" in model.model_fields
        assert "naric_level_source" in model.model_fields
        assert "naric_availability" not in model.model_fields
    assert {s.value for s in NaricLevelSource} == {"retrieved", "default"}


# --- assumed by us (recorded in docs/assumptions.md) ----------------------


def test_assumed_uppercase_enums_are_stable():
    assert {c.value for c in Classification} == {
        "legal_concept",
        "process",
        "definitional",
    }
    assert {s.value for s in ResponseStatus} == {
        "answered",
        "clarification_needed",
        "out_of_scope",
        "timeout",
        "error",
        "framings_exhausted",
    }
    assert {a.value for a in FollowUpAction} == {
        "explain_differently",
        "another_example",
        "go_deeper",
    }
    assert {k.value for k in ClassificationKind} >= {c.value for c in Classification}


def test_assumed_lowercase_framing_values():
    for framing in FramingStrategy:
        assert framing.value == framing.value.lower()


def test_response_field_names_are_pinned():
    assert set(QuestionResponse.model_fields) == {
        "question_id",
        "session_id",
        "classification",
        "status",
        "parts",
        "clarification_question",
        "message",
        "follow_up_actions",
        "rating_state",
        "retry_available",
        "follow_up_of",
        "meta",
    }


def test_log_record_field_names_are_pinned():
    assert set(QuestionLogRecord.model_fields) == {
        "question_id",
        "session_id",
        "user_id",
        "question",
        "classification",
        "status",
        "answer",
        "topic_tag",
        "topic_tag_accepted",
        "timestamp",
        "rating_state",
        "naric_level",
        "naric_level_source",
        "concept_key",
        "framing",
        "follow_up_of",
        "follow_up_action",
        "elapsed_ms",
        "citation_guard_violations",
        "degraded",
        "error",
    }


async def test_serialised_values_use_the_contract_casing(service, alice):
    response = await service.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    payload = response.model_dump(mode="json")
    assert payload["rating_state"] == "pending"
    assert payload["meta"]["topic_tag"] in TOPIC_VOCABULARY
    assert payload["meta"]["naric_level_source"] in {"retrieved", "default"}
    assert payload["meta"]["framing"] == payload["meta"]["framing"].lower()
    # Lowercased per integration brief §4.2, which instructs the rename across
    # status, classification, follow_up_actions, AuthorityStatus,
    # ExplanationDepth, FieldAvailability and LogStatus.
    assert payload["status"].islower()
    assert payload["classification"].islower()
    # NaricLevel is deliberately NOT in that instructed list and stays uppercase.
    # It is the open platform-wide row: six components emit LEVEL_5, two emit
    # level_5 (PLATFORM_CONTRACT.md §2). Asserted so the exception is visible
    # rather than silent, and so this test fails loudly when that row closes.
    assert payload["meta"]["naric_level"].isupper()
