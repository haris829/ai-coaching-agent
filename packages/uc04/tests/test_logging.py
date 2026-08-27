"""Interaction logging: one record per answered question, in the published shape."""

from __future__ import annotations

from conftest import IN_LESSON_QUESTION, OFF_LESSON_QUESTION, build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.core.privacy import REDACTED
from uc04.domain.enums import UNCLASSIFIED, Grounding, QuestionClass, RatingState
from uc04.domain.models import InteractionRecord
from uc04.domain.vocabularies import TOPIC_VOCABULARY, is_known_concept

CONTRACT_FIELDS = [
    "interaction_id", "session_id", "user_id", "asked_at",
    "question_text", "topic_tag", "question_class",
    "naric_level", "response_id",
    "course_id", "lesson_id", "lesson_section_id",
    "concept_tag", "grounding",
    "quiz_intent_detected", "quiz_detection_confirmed",
    "framing_used", "explain_differently_count",
    "follow_up_of", "rating_state",
]


def test_the_record_carries_exactly_the_contract_fields() -> None:
    assert list(InteractionRecord.model_fields) == CONTRACT_FIELDS


def test_one_record_is_written_per_answered_question(harness) -> None:
    harness.ask(IN_LESSON_QUESTION)
    harness.ask(OFF_LESSON_QUESTION)
    records = harness.interactions.list_for_session(fx.SESSION_MAIN)
    assert len(records) == 2


def test_a_follow_up_writes_its_own_record(harness) -> None:
    first = harness.ask(IN_LESSON_QUESTION)
    harness.explain_differently(first)
    assert len(harness.interactions.list_for_session(fx.SESSION_MAIN)) == 2


def test_rating_state_is_pending_and_untouched(harness) -> None:
    """UC-04 writes ``pending`` and never changes it. Rating belongs to another component."""
    response = harness.ask(IN_LESSON_QUESTION)
    record = harness.interactions.get(response.interaction_id)
    assert record.rating_state is RatingState.PENDING
    assert response.rating_state is RatingState.PENDING

    follow_up = harness.explain_differently(response)
    assert harness.interactions.get(follow_up.interaction_id).rating_state is RatingState.PENDING
    # The original record is not mutated by anything that happens later either.
    assert harness.interactions.get(response.interaction_id).rating_state is RatingState.PENDING


def test_the_record_identifies_concept_lesson_framing_and_signal(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    record = harness.interactions.get(response.interaction_id)
    assert record.concept_tag == "hearsay"
    assert record.lesson_id == fx.LESSON_HEARSAY
    assert record.lesson_section_id == "sec_hearsay_definition"
    assert record.framing_used is not None
    assert record.explain_differently_count == 0
    assert record.grounding is Grounding.LESSON
    assert record.response_id and record.response_id != record.interaction_id


def test_tags_come_from_the_closed_vocabularies(harness) -> None:
    for question in (IN_LESSON_QUESTION, OFF_LESSON_QUESTION, "What is legal advice privilege?"):
        response = harness.ask(question)
        record = harness.interactions.get(response.interaction_id)
        assert record.concept_tag == UNCLASSIFIED or is_known_concept(record.concept_tag)
        assert record.topic_tag == UNCLASSIFIED or record.topic_tag in TOPIC_VOCABULARY


def test_an_unmatched_question_is_tagged_unclassified(harness) -> None:
    response = harness.ask(OFF_LESSON_QUESTION)
    record = harness.interactions.get(response.interaction_id)
    assert record.concept_tag == UNCLASSIFIED
    assert record.topic_tag == UNCLASSIFIED


def test_the_unclassified_rate_is_available_as_a_metric(harness) -> None:
    harness.ask(IN_LESSON_QUESTION)
    harness.ask(OFF_LESSON_QUESTION)
    assert harness.interactions.unclassified_rate() == 0.5


def test_question_text_is_absent_from_the_log(harness) -> None:
    """The learner's words are not persisted; the contract field carries a marker."""
    distinctive = "What does hearsay mean for my zebra-shaped disclosure schedule?"
    response = harness.ask(distinctive)
    record = harness.interactions.get(response.interaction_id)
    assert record.question_text == REDACTED
    assert "zebra" not in record.model_dump_json()


def test_question_class_reflects_how_the_question_was_read(harness) -> None:
    assert harness.interactions.get(
        harness.ask(IN_LESSON_QUESTION).interaction_id
    ).question_class is QuestionClass.CONCEPT_EXPLANATION

    assert harness.interactions.get(
        harness.ask(OFF_LESSON_QUESTION).interaction_id
    ).question_class is QuestionClass.OUT_OF_LESSON

    assert harness.interactions.get(
        harness.ask("Tell me the answer.").interaction_id
    ).question_class is QuestionClass.QUIZ_ANSWER_SEEKING


def test_a_logging_outage_does_not_fail_the_learners_turn() -> None:
    harness = build_harness()
    harness.interactions.always_fail = True
    response = harness.ask(IN_LESSON_QUESTION)
    assert response.explanation.strip()
    assert response.interaction_id


def test_records_are_scoped_to_their_session(harness) -> None:
    harness.ask(IN_LESSON_QUESTION, session_id=fx.SESSION_MAIN)
    harness.ask(IN_LESSON_QUESTION, session_id=fx.SESSION_SECOND)
    assert len(harness.interactions.list_for_session(fx.SESSION_MAIN)) == 1
    assert len(harness.interactions.list_for_session(fx.SESSION_SECOND)) == 1


def test_records_are_chronological_with_unique_ids(harness) -> None:
    first = harness.ask(IN_LESSON_QUESTION)
    second = harness.explain_differently(first)
    harness.ask(OFF_LESSON_QUESTION)

    records = harness.interactions.list_for_session(fx.SESSION_MAIN)
    ids = [r.interaction_id for r in records]
    response_ids = [r.response_id for r in records]
    assert len(set(ids)) == len(ids)
    assert len(set(response_ids)) == len(response_ids)
    assert not set(ids) & set(response_ids), "interaction and response ids must not collide"

    timestamps = [r.asked_at for r in records]
    assert timestamps == sorted(timestamps)
    assert second.interaction_id in ids


def test_no_aggregation_or_reporting_is_performed(harness) -> None:
    """UC-04 writes the record and stops. Gap analysis belongs to another component."""
    service = harness.service
    for forbidden in ("gap", "report", "summar", "streak", "aggregate"):
        assert not [name for name in dir(service) if forbidden in name.lower()]
