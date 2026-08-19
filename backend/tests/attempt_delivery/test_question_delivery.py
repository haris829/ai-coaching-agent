"""Question selection, randomisation, stability and retirement."""

from __future__ import annotations

from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import QuestionType
from app.modules.attempt_delivery.integration.uc02.types import QuestionQuery
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import LEARNER_ID, QUIZ_ID, seed_world


def _questions(api: ApiClient, attempt_id: str) -> list[dict]:
    return assert_ok(api.questions(attempt_id))["questions"]


def start_and_read(api: ApiClient) -> tuple[str, list[dict]]:
    """Create an attempt and read its paper — creation returns a descriptor, not the questions."""
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    return attempt_id, _questions(api, attempt_id)


# ---------------------------------------------------------------------------
# Question count and type filters
# ---------------------------------------------------------------------------


def test_question_count_is_honoured(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 7})

    attempt = assert_ok(api.create_attempt(QUIZ_ID), 201)
    assert attempt["attempt"]["totalQuestions"] == 7
    assert len(_questions(api, attempt["attempt"]["attemptId"])) == 7


def test_type_quotas_are_honoured(context: AppContext, api: ApiClient) -> None:
    # The specification's worked example: 20 questions = 12 single choice + 8 true/false.
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionCount": 20,
                "questionTypeQuotas": [
                    {"type": str(QuestionType.SINGLE_CHOICE), "count": 12},
                    {"type": str(QuestionType.TRUE_FALSE), "count": 8},
                ],
                "randomiseQuestionOrder": True,
            },
            counts={QuestionType.SINGLE_CHOICE: 30, QuestionType.TRUE_FALSE: 20},
        )

    body = assert_ok(api.create_attempt(QUIZ_ID), 201)
    assert body["delivery"]["questionTypeCounts"] == {
        str(QuestionType.SINGLE_CHOICE): 12,
        str(QuestionType.TRUE_FALSE): 8,
    }

    delivered = _questions(api, body["attempt"]["attemptId"])
    assert len(delivered) == 20
    types = [question["questionType"] for question in delivered]
    assert types.count(str(QuestionType.SINGLE_CHOICE)) == 12
    assert types.count(str(QuestionType.TRUE_FALSE)) == 8


def test_allowed_types_restrict_the_pool(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionCount": 5,
                "allowedQuestionTypes": [str(QuestionType.DRAG_TO_ORDER)],
            },
        )

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    delivered = _questions(api, attempt_id)
    assert {question["questionType"] for question in delivered} == {
        str(QuestionType.DRAG_TO_ORDER)
    }


def test_insufficient_questions_is_reported_and_nothing_persisted(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"questionCount": 50},
            counts={
                QuestionType.SINGLE_CHOICE: 3,
                QuestionType.TRUE_FALSE: 0,
                QuestionType.MULTI_SELECT: 0,
                QuestionType.SCENARIO: 0,
                QuestionType.DRAG_TO_ORDER: 0,
            },
        )

    error = assert_error(api.create_attempt(QUIZ_ID), 422, "INSUFFICIENT_QUESTIONS")
    assert error["context"]["requestedQuestionCount"] == 50
    assert error["context"]["availableQuestionCount"] == 3

    with context.unit_of_work() as ctx:
        assert ctx.attempts_repo.count_for_learner_and_quiz(LEARNER_ID, QUIZ_ID) == 0


def test_quota_shortfall_names_the_type(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionCount": 12,
                "questionTypeQuotas": [
                    {"type": str(QuestionType.SINGLE_CHOICE), "count": 2},
                    {"type": str(QuestionType.SCENARIO), "count": 10},
                ],
            },
            counts={QuestionType.SCENARIO: 2},
        )

    error = assert_error(api.create_attempt(QUIZ_ID), 422, "INSUFFICIENT_QUESTIONS")
    shortfalls = error["context"]["shortfalls"]
    assert shortfalls == [
        {"type": str(QuestionType.SCENARIO), "required": 10, "available": 2}
    ]


# ---------------------------------------------------------------------------
# Retirement
# ---------------------------------------------------------------------------


def test_retired_questions_are_never_selected(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"questionCount": 3, "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)]},
            counts={QuestionType.SINGLE_CHOICE: 5},
        )
        # UC-02 retires the first two, leaving exactly three usable.
        ctx.seedable_question_bank.set_retired("q-sc-01", True)
        ctx.seedable_question_bank.set_retired("q-sc-02", True)

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    delivered = {question["questionId"] for question in _questions(api, attempt_id)}
    assert delivered == {"q-sc-03", "q-sc-04", "q-sc-05"}


def test_retiring_a_question_mid_attempt_does_not_disturb_the_attempt(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"questionCount": 3, "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)]},
        )

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    before = _questions(api, attempt_id)
    delivered_id = before[0]["questionId"]

    # UC-02 retires a question the learner is already looking at.
    with context.unit_of_work() as ctx:
        ctx.seedable_question_bank.set_retired(delivered_id, True)

    # The frozen snapshot means the attempt is unchanged and still answerable.
    after = _questions(api, attempt_id)
    assert [question["questionId"] for question in after] == [
        question["questionId"] for question in before
    ]
    assert_ok(
        api.save_answer(
            attempt_id, delivered_id, {"selectedOptionId": after[0]["options"][0]["optionId"]}
        )
    )


def test_editing_a_question_mid_attempt_does_not_change_what_was_delivered(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"questionCount": 1, "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)]},
        )

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    original = _questions(api, attempt_id)[0]

    # UC-02 rewrites the question and bumps its version.
    with context.unit_of_work() as ctx:
        from tests.support.fixtures import single_choice_question

        edited = single_choice_question(1, question_id=original["questionId"], version=9)
        ctx.seedable_question_bank.upsert_question(edited)
        ctx.session.commit()

    delivered = _questions(api, attempt_id)[0]
    assert delivered["questionVersion"] == original["questionVersion"] == 1
    assert delivered["prompt"] == original["prompt"]


# ---------------------------------------------------------------------------
# Randomisation and stability
# ---------------------------------------------------------------------------


def test_question_set_is_stable_across_repeated_reads(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 8, "randomiseQuestionOrder": True})

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    first = [(q["position"], q["questionId"]) for q in _questions(api, attempt_id)]
    # A refresh must not reshuffle or reselect.
    for _ in range(4):
        assert [(q["position"], q["questionId"]) for q in _questions(api, attempt_id)] == first


def test_option_order_is_stable_across_reads(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionCount": 4,
                "randomiseOptionOrder": True,
                "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)],
            },
        )

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    first = [[o["optionId"] for o in q["options"]] for q in _questions(api, attempt_id)]
    second = [[o["optionId"] for o in q["options"]] for q in _questions(api, attempt_id)]
    assert first == second


def test_randomisation_produces_different_papers_for_different_attempts(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionCount": 6,
                "randomiseQuestionOrder": True,
                "maxAttempts": None,
                "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)],
            },
            counts={QuestionType.SINGLE_CHOICE: 15},
        )

    papers: list[tuple[str, ...]] = []
    for index in range(6):
        attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
        papers.append(tuple(q["questionId"] for q in _questions(api, attempt_id)))
        assert_ok(api.submit(attempt_id, idempotency_key=f"key-{index}"))

    # The seed is per-attempt, so distinct attempts must not all receive an identical
    # paper. (Any single pair could coincide; all six being identical would not.)
    assert len(set(papers)) > 1


def test_randomisation_disabled_is_deterministic(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={
                "questionCount": 4,
                "randomiseQuestionOrder": False,
                "randomiseOptionOrder": False,
                "maxAttempts": None,
                "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)],
            },
            counts={QuestionType.SINGLE_CHOICE: 10},
        )

    papers = []
    for index in range(3):
        attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
        questions = _questions(api, attempt_id)
        papers.append(
            (
                tuple(q["questionId"] for q in questions),
                tuple(o["optionId"] for o in questions[0]["options"]),
            )
        )
        assert_ok(api.submit(attempt_id, idempotency_key=f"key-{index}"))

    # With randomisation off the same configuration always produces the same paper.
    assert len(set(papers)) == 1


def test_selection_is_reproducible_from_the_persisted_seed(
    context: AppContext, api: ApiClient
) -> None:
    with context.unit_of_work() as ctx:
        configuration = seed_world(
            ctx, rules={"questionCount": 6, "randomiseQuestionOrder": True}
        )

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    delivered = [q["questionId"] for q in _questions(api, attempt_id)]

    # Re-running selection with the stored seed reproduces the same paper, which is what
    # makes a randomised attempt auditable after the fact.
    with context.unit_of_work() as ctx:
        attempt = ctx.attempts_repo.get(attempt_id)
        assert attempt is not None
        assert attempt.selection_seed == attempt_id
        pool = ctx.question_bank.find_eligible_questions(
            QuestionQuery(quiz_id=QUIZ_ID, course_id=attempt.course_id)
        )
        replayed = ctx.selection.select(configuration, pool, attempt.selection_seed)

    assert [question.question_id for question in replayed.questions] == delivered


# ---------------------------------------------------------------------------
# Answer confidentiality
# ---------------------------------------------------------------------------


def test_correct_answers_are_never_exposed(context: AppContext, api: ApiClient) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 20, "randomiseQuestionOrder": True})

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    # Check the raw response text, so a leak anywhere in the payload is caught.
    for response in (
        api.questions(attempt_id),
        api.question_at(attempt_id, 1),
        api.current_question(attempt_id),
        api.state(attempt_id),
    ):
        assert response.status_code == 200
        assert "isCorrect" not in response.text
        assert "correctPosition" not in response.text


def test_snapshot_retains_grading_data_for_downstream_use(
    context: AppContext, api: ApiClient
) -> None:
    # Stripped from responses, but kept in the snapshot so a future grading capability
    # can score against exactly what the learner saw.
    with context.unit_of_work() as ctx:
        seed_world(
            ctx,
            rules={"questionCount": 1, "allowedQuestionTypes": [str(QuestionType.SINGLE_CHOICE)]},
        )

    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]

    with context.unit_of_work() as ctx:
        stored = ctx.attempt_questions_repo.list_for_attempt(attempt_id)[0]
        options = stored.question_snapshot["options"]
        assert any(option["isCorrect"] for option in options)


def test_the_bank_is_told_what_was_delivered(context: AppContext, api: ApiClient, question_bank) -> None:
    """UC-03 reports the delivery back to UC-02 through the port.

    Not a detail of UC-02's storage — a contract UC-03 owes it. UC-02's usage counts, its refusal to
    hard-delete used content and its historical report all read that record, so an attempt that
    silently skipped it would leave the bank's history wrong.
    """
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 3})

    attempt_id, questions = start_and_read(api)

    reported = question_bank.deliveries[attempt_id]
    assert [entry.question_id for entry in reported] == [q["questionId"] for q in questions]
    assert [entry.position for entry in reported] == [1, 2, 3]
    assert all(entry.question_version >= 1 for entry in reported)
    assert question_bank.delivery_learners[attempt_id] == LEARNER_ID


def test_a_refused_second_creation_reports_no_second_delivery(
    context: AppContext, api: ApiClient, question_bank
) -> None:
    """A rejected creation must not leave the bank thinking a second paper went out."""
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"questionCount": 2})

    attempt_id, _ = start_and_read(api)
    assert api.create_attempt(QUIZ_ID).status_code == 409

    assert list(question_bank.deliveries) == [attempt_id]
