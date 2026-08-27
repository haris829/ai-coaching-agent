"""Requirements 9, 10, 11 - question logging, topic tagging, rating state."""

from __future__ import annotations

import pytest

from uc03.adapters.mocks import (
    FailingAnswerGenerator,
    InMemoryQuestionLogger,
    StaticTopicTagger,
)
from uc03.domain.enums import (
    ClassificationKind,
    LogStatus,
    RatingState,
    ResponseStatus,
)
from uc03.domain.topics import TOPIC_VOCABULARY, TopicTag, validate_topic_tag
from uc03.errors import AuthorizationError, InputValidationError
from uc03.service import DEGRADED_LOG, DEGRADED_TOPIC_TAG

from .conftest import ALICE_SESSION, BOB_SESSION, build_service

REQUIRED_LOG_FIELDS = {
    "question_id",
    "session_id",
    "user_id",
    "question",
    "classification",
    "answer",
    "topic_tag",
    "timestamp",
    "rating_state",
}


def test_log_record_carries_every_required_field():
    from uc03.domain.models import QuestionLogRecord

    assert REQUIRED_LOG_FIELDS <= set(QuestionLogRecord.model_fields)


@pytest.mark.parametrize(
    ("question", "expected_status"),
    [
        ("What is negligence in tort law?", ResponseStatus.ANSWERED),
        ("Tell me about consideration", ResponseStatus.CLARIFICATION_NEEDED),
        ("What is the weather tomorrow?", ResponseStatus.OUT_OF_SCOPE),
    ],
)
async def test_every_outcome_is_logged_once(question, expected_status, alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    response = await svc.answer(
        question=question, session_id=ALICE_SESSION, principal=alice
    )
    assert response.status is expected_status
    assert len(logger.records) == 1
    record = logger.last
    assert record.question == question
    assert record.status is expected_status
    assert record.question_id == response.question_id
    assert record.session_id == ALICE_SESSION
    assert record.user_id == alice.user_id
    assert record.rating_state is RatingState.PENDING
    assert record.timestamp is not None


async def test_answer_is_none_rather_than_faked_when_unavailable(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    await svc.answer(
        question="Tell me about consideration", session_id=ALICE_SESSION, principal=alice
    )
    record = logger.last
    assert record.answer is None
    assert record.status is ResponseStatus.CLARIFICATION_NEEDED
    assert record.classification is ClassificationKind.AMBIGUOUS


async def test_successful_answer_is_stored_on_the_record(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    await svc.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    record = logger.last
    assert record.answer is not None
    assert record.answer.plain_english
    assert record.answer.authority is not None


async def test_generator_failure_is_logged_as_error_without_an_answer(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(generator=FailingAnswerGenerator(), logger=logger)
    response = await svc.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert response.status is ResponseStatus.ERROR
    assert response.parts is None, "no partial answer on failure"
    assert response.retry_available is True
    assert len(logger.records) == 1
    assert logger.last.status is ResponseStatus.ERROR
    assert logger.last.answer is None


async def test_unauthorized_access_is_logged(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    with pytest.raises(AuthorizationError):
        await svc.answer(
            question="What is negligence in tort law?",
            session_id=BOB_SESSION,
            principal=alice,
        )
    assert len(logger.records) == 1
    assert logger.last.status is ResponseStatus.ERROR
    assert logger.last.error == "unauthorized_session"


async def test_oversized_input_is_logged(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    with pytest.raises(InputValidationError):
        await svc.answer(
            question="x" * 5000, session_id=ALICE_SESSION, principal=alice
        )
    assert len(logger.records) == 1
    assert logger.last.error == "input_too_long"


async def test_logging_failure_degrades_but_does_not_fail_the_request(alice):
    svc = build_service(logger=InMemoryQuestionLogger(fail=True))
    response = await svc.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert response.status is ResponseStatus.ANSWERED
    assert response.meta.log_status is LogStatus.FAILED
    assert DEGRADED_LOG in response.meta.degraded


# --- topic tagging -------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is negligence in tort law?", TopicTag.NEGLIGENCE),
        ("What is consideration in contract law?", TopicTag.CONTRACT_FORMATION),
        ("What are the steps to apply for probate?", TopicTag.WILLS_AND_PROBATE),
        ("What is the test for unfair dismissal?", TopicTag.EMPLOYMENT),
    ],
)
async def test_questions_receive_a_controlled_topic_tag(question, expected, alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    response = await svc.answer(
        question=question, session_id=ALICE_SESSION, principal=alice
    )
    assert response.meta.topic_tag is expected
    assert logger.last.topic_tag is expected
    assert logger.last.topic_tag.value in TOPIC_VOCABULARY


async def test_arbitrary_tag_proposals_are_rejected(alice):
    """An LLM tagger cannot invent a new analytics dimension."""
    logger = InMemoryQuestionLogger()
    svc = build_service(tagger=StaticTopicTagger(tag="DEFINITELY_NOT_A_REAL_TAG"), logger=logger)
    response = await svc.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert response.meta.topic_tag is TopicTag.UNCLASSIFIED
    assert response.meta.topic_tag_accepted is False
    assert DEGRADED_TOPIC_TAG in response.meta.degraded
    assert logger.last.topic_tag is TopicTag.UNCLASSIFIED


async def test_tagger_failure_does_not_fail_the_answer(alice):
    class ExplodingTagger:
        async def propose_tag(self, *, question: str):  # noqa: ANN202
            raise RuntimeError("tagger down")

    svc = build_service(tagger=ExplodingTagger())
    response = await svc.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert response.status is ResponseStatus.ANSWERED
    assert response.meta.topic_tag is TopicTag.UNCLASSIFIED


@pytest.mark.parametrize(
    ("raw", "expected_tag", "expected_ok"),
    [
        ("NEGLIGENCE", TopicTag.NEGLIGENCE, True),
        ("negligence", TopicTag.NEGLIGENCE, True),
        ("land-and-property", TopicTag.LAND_AND_PROPERTY, True),
        ("nonsense", TopicTag.UNCLASSIFIED, False),
        (None, TopicTag.UNCLASSIFIED, False),
    ],
)
def test_topic_tag_validation(raw, expected_tag, expected_ok):
    tag, accepted = validate_topic_tag(raw)
    assert tag is expected_tag
    assert accepted is expected_ok


async def test_rating_state_pending_on_every_log_record(alice):
    logger = InMemoryQuestionLogger()
    svc = build_service(logger=logger)
    for question in (
        "What is negligence in tort law?",
        "Tell me about consideration",
        "What is the weather tomorrow?",
    ):
        await svc.answer(question=question, session_id=ALICE_SESSION, principal=alice)
    assert len(logger.records) == 3
    assert all(r.rating_state is RatingState.PENDING for r in logger.records)
