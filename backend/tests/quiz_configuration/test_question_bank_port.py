"""The question-bank port is genuinely substitutable.

Every other UC-01 test runs against the real question bank, which is what proves the integration
works. This file proves the *other* half of the claim: that the configuration rules depend on the
:class:`~app.modules.quiz_configuration.ports.QuestionBankPort` abstraction and nothing else, so a
different implementation — the company's adapter tomorrow, an HTTP client if the bank ever moves out
of process — can be dropped in without touching a business rule.

If any rule quietly reached past the port into ``qb_*`` tables, these tests would fail: the fake has
no database behind it at all.

Question *selection* is no longer covered here: UC-03 owns drawing an attempt's questions, and tests
it against its own port. What remains is UC-01's side — capacity arithmetic, topic scope and
versioning — proven to run with no question bank behind it.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.core.question_types import QuestionType
from app.modules.quiz_configuration.context import build_context
from app.modules.quiz_configuration.domain.rules import validate_configuration
from app.modules.quiz_configuration.models import Course, Quiz
from app.modules.quiz_configuration.ports import BankScope, FakeQuestionBank, TopicRef
from app.modules.quiz_configuration.services import configuration_service
from tests.harness import valid_configuration


@pytest.fixture
def fake_bank() -> FakeQuestionBank:
    return FakeQuestionBank(
        counts={QuestionType.SINGLE_CHOICE: 12, QuestionType.TRUE_FALSE: 8},
        topics={"topic-1": TopicRef(id="topic-1", slug="scoped", name="Scoped")},
    )


@pytest.fixture
def quiz_id(db) -> int:
    """A quiz with no questions anywhere — the bank is entirely the fake's business."""
    course = Course(code="PORT-1", title="Port Course")
    db.add(course)
    db.flush()
    quiz = Quiz(course_id=course.id, slug="port-quiz", title="Port Quiz")
    db.add(quiz)
    db.commit()
    return quiz.id


def test_capacity_arithmetic_runs_against_a_fake_bank(db, quiz_id, fake_bank) -> None:
    ctx = build_context(db, bank=fake_bank)

    config = validate_configuration(
        valid_configuration(
            questionCount=20,
            questionTypes=[
                {"type": "SINGLE_CHOICE", "quota": 12},
                {"type": "TRUE_FALSE", "quota": 8},
            ],
        )
    ).value
    assert config is not None

    report = configuration_service.evaluate_bank_capacity(ctx, config)
    assert report.satisfiable is True
    assert report.available_total == 20


def test_a_shortfall_is_reported_from_whatever_the_port_returns(db, quiz_id, fake_bank) -> None:
    ctx = build_context(db, bank=fake_bank)

    config = validate_configuration(
        valid_configuration(
            questionCount=20,
            questionTypes=[{"type": "SINGLE_CHOICE", "quota": 20}],
        )
    ).value
    assert config is not None

    report = configuration_service.evaluate_bank_capacity(ctx, config)
    assert report.satisfiable is False
    assert report.breakdown[0].available == 12
    assert report.breakdown[0].shortfall == 8


def test_a_configuration_can_be_saved_and_versioned_with_no_real_question_bank(
    db, quiz_id, fake_bank
) -> None:
    """The whole save path — validation, capacity, versioning — over a fake bank."""
    ctx = build_context(db, bank=fake_bank)

    body, created = configuration_service.save_configuration(
        ctx,
        quiz_id,
        valid_configuration(
            questionCount=10, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}]
        ),
        actor_user_id=None,
        actor="port-test",
    )
    assert created is True
    assert body["configuration"]["versionNumber"] == 1
    assert body["capacity"]["satisfiable"] is True

    # A second, different save produces version 2 — versioning is unaffected by the bank behind it.
    body, created = configuration_service.save_configuration(
        ctx,
        quiz_id,
        valid_configuration(
            questionCount=10, passMark=80, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}]
        ),
        actor_user_id=None,
        actor="port-test",
    )
    assert created is True
    assert body["configuration"]["versionNumber"] == 2


def test_an_unsatisfiable_configuration_is_refused_with_the_ports_numbers(
    db, quiz_id, fake_bank
) -> None:
    ctx = build_context(db, bank=fake_bank)

    with pytest.raises(ValidationError) as raised:
        configuration_service.save_configuration(
            ctx,
            quiz_id,
            valid_configuration(
                questionCount=50, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 50}]
            ),
            actor_user_id=None,
            actor="port-test",
        )

    error = raised.value
    assert error.code == "QUESTION_BANK_INSUFFICIENT"
    assert error.extra["capacity"]["breakdown"][0]["available"] == 12


def test_the_topic_scope_is_resolved_through_the_port(db, quiz_id, fake_bank) -> None:
    ctx = build_context(db, bank=fake_bank)

    body, created = configuration_service.save_configuration(
        ctx,
        quiz_id,
        valid_configuration(
            questionCount=10,
            questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}],
            topicIds=["topic-1"],
        ),
        actor_user_id=None,
        actor="port-test",
    )
    assert created is True
    assert body["configuration"]["topics"] == [
        {"id": "topic-1", "slug": "scoped", "name": "Scoped"}
    ]

    # An id the port does not know is a configuration error, not a silently dropped scope.
    with pytest.raises(ValidationError) as raised:
        configuration_service.save_configuration(
            ctx,
            quiz_id,
            valid_configuration(
                questionCount=10,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}],
                topicIds=["topic-1", "nope"],
            ),
            actor_user_id=None,
            actor="port-test",
        )
    assert raised.value.details[0].code == "UNKNOWN_TOPIC"




def test_a_type_the_port_says_nothing_about_counts_as_zero(fake_bank) -> None:
    """A missing key must not read as "unconstrained" — that would let an impossible quiz save."""
    counts = fake_bank.available_by_type(BankScope(types=(QuestionType.DRAG_TO_ORDER,)))
    assert counts == {QuestionType.DRAG_TO_ORDER: 0}
