"""Field validation — the domain rules and the API gate that enforces them."""

from __future__ import annotations

import pytest

from app.modules.quiz_configuration.domain.enums import DeliveryMode, QuestionType
from app.modules.quiz_configuration.domain.rules import (
    LIMITS,
    ValidationCode,
    validate_configuration,
)
from tests.harness import (
    LEARNER_TOKEN,
    Ctx,
    error_codes,
    field_errors,
    valid_configuration,
)


class TestDomainRules:
    def test_accepts_and_normalises_a_valid_configuration(self) -> None:
        result = validate_configuration(
            {
                "questionCount": "10",
                "timeLimitMinutes": "30",
                "passMark": "70",
                "maxAttempts": "2",
                "deliveryMode": "assessment",
                "randomiseQuestions": "true",
                # deliberately out of canonical order, mixing shapes and casing
                "questionTypes": [{"type": "TRUE_FALSE", "quota": None}, "single_choice"],
            }
        )

        assert result.valid
        assert result.value is not None
        assert result.value.question_count == 10
        assert result.value.time_limit_minutes == 30
        assert result.value.pass_mark == 70
        assert result.value.max_attempts == 2
        assert result.value.delivery_mode is DeliveryMode.ASSESSMENT
        assert result.value.randomise_questions is True
        # Canonical order, regardless of submitted order — this is what makes the version
        # fingerprint stable.
        assert [entry.type for entry in result.value.question_types] == [
            QuestionType.SINGLE_CHOICE,
            QuestionType.TRUE_FALSE,
        ]

    def test_blank_time_limit_means_no_limit(self) -> None:
        result = validate_configuration(valid_configuration(timeLimitMinutes=None))
        assert result.valid
        assert result.value is not None
        assert result.value.time_limit_minutes is None

    def test_exam_delivery_requires_a_time_limit(self) -> None:
        result = validate_configuration(
            valid_configuration(deliveryMode="exam", timeLimitMinutes=None)
        )
        assert not result.valid
        assert "timeLimitMinutes" in {error.field for error in result.errors}
        assert ValidationCode.TIME_LIMIT_REQUIRED in {error.code for error in result.errors}

    def test_quotas_must_add_up_to_the_question_count(self) -> None:
        result = validate_configuration(
            valid_configuration(
                questionCount=20,
                questionTypes=[
                    {"type": "SINGLE_CHOICE", "quota": 10},
                    {"type": "TRUE_FALSE", "quota": 5},
                ],
            )
        )
        assert not result.valid
        assert "add up to 15" in result.errors[0].message
        assert result.errors[0].code == ValidationCode.QUOTA_SUM_MISMATCH

    def test_quotas_are_all_or_nothing(self) -> None:
        result = validate_configuration(
            valid_configuration(
                questionCount=10,
                questionTypes=[
                    {"type": "SINGLE_CHOICE", "quota": 10},
                    {"type": "TRUE_FALSE", "quota": None},
                ],
            )
        )
        assert not result.valid
        assert result.errors[0].field == "questionTypes"
        assert result.errors[0].code == ValidationCode.QUOTA_SHAPE

    def test_duplicate_question_types_are_rejected(self) -> None:
        result = validate_configuration(
            valid_configuration(
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": None}, "SINGLE_CHOICE"]
            )
        )
        assert not result.valid
        assert "selected more than once" in result.errors[0].message

    def test_non_object_payload_is_rejected(self) -> None:
        result = validate_configuration("not-a-configuration")
        assert not result.valid
        assert result.errors[0].field == "_root"

    def test_all_five_question_types_are_configurable(self) -> None:
        """The configuration vocabulary is the question bank's, in full."""
        result = validate_configuration(
            valid_configuration(
                questionCount=5,
                questionTypes=[{"type": item.value, "quota": 1} for item in QuestionType],
            )
        )
        assert result.valid, [error.message for error in result.errors]
        assert result.value is not None
        assert set(result.value.selected_types) == set(QuestionType)

    def test_topic_scope_is_optional_and_deduplicated(self) -> None:
        result = validate_configuration(valid_configuration(topicIds=["a", "b", "a"]))
        assert result.valid
        assert result.value is not None
        assert result.value.topic_ids == ("a", "b")

    def test_retired_question_types_of_a_previous_vocabulary_are_rejected(self) -> None:
        """``mcq`` and ``short_answer`` were UC-01's private vocabulary before the merge."""
        for legacy in ("mcq", "short_answer"):
            result = validate_configuration(
                valid_configuration(questionTypes=[{"type": legacy, "quota": None}])
            )
            assert not result.valid, legacy
            assert ValidationCode.INVALID_QUESTION_TYPE in {e.code for e in result.errors}


class TestApiValidatesIndependentlyOfTheUi:
    def test_saves_a_valid_configuration(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(valid_configuration())
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["configuration"]["versionNumber"] == 1
        assert body["created"] is True

    @pytest.mark.parametrize(
        ("label", "override", "expected_field"),
        [
            ("question count of 0", {"questionCount": 0}, "questionCount"),
            ("question count above maximum", {"questionCount": 500}, "questionCount"),
            ("non-integer question count", {"questionCount": 10.5}, "questionCount"),
            ("pass mark of 0", {"passMark": 0}, "passMark"),
            ("negative pass mark", {"passMark": -10}, "passMark"),
            ("pass mark above 100", {"passMark": 101}, "passMark"),
            ("maximum attempts of 0", {"maxAttempts": 0}, "maxAttempts"),
            ("negative maximum attempts", {"maxAttempts": -1}, "maxAttempts"),
            ("time limit of 0", {"timeLimitMinutes": 0}, "timeLimitMinutes"),
            ("unsupported delivery mode", {"deliveryMode": "telepathy"}, "deliveryMode"),
            (
                "unsupported question type",
                {"questionTypes": [{"type": "essay", "quota": None}]},
                "questionTypes",
            ),
            ("no question types", {"questionTypes": []}, "questionTypes"),
        ],
    )
    def test_rejects_invalid_values(
        self, ctx: Ctx, label: str, override: dict, expected_field: str
    ) -> None:
        response = ctx.save_configuration(valid_configuration(**override))

        assert response.status_code == 422, label
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_FAILED"
        assert expected_field in field_errors(body)
        # Nothing may be persisted by a rejected save.
        assert ctx.version_count() == 0

    def test_pass_mark_error_states_the_exact_bounds(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(valid_configuration(passMark=101))
        assert field_errors(response.json())["passMark"] == [
            f"Pass mark must be between {LIMITS['passMark']['min']} "
            f"and {LIMITS['passMark']['max']}%."
        ]
        assert error_codes(response.json()) == {ValidationCode.OUT_OF_RANGE}

    def test_max_attempts_error_states_the_bounds(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(valid_configuration(maxAttempts=0))
        assert field_errors(response.json())["maxAttempts"] == [
            f"Maximum attempts must be between {LIMITS['maxAttempts']['min']} "
            f"and {LIMITS['maxAttempts']['max']}."
        ]

    def test_accepts_the_pass_mark_boundaries(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(valid_configuration(passMark=1)).status_code == 201
        assert ctx.save_configuration(valid_configuration(passMark=100)).status_code == 201

    def test_collects_every_field_error_in_one_response(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(
            {
                "questionCount": 0,
                "passMark": 400,
                "maxAttempts": 0,
                "deliveryMode": "nope",
                "questionTypes": [],
                "timeLimitMinutes": 30,
                "randomiseQuestions": False,
            }
        )
        assert response.status_code == 422
        assert sorted(field_errors(response.json())) == [
            "deliveryMode",
            "maxAttempts",
            "passMark",
            "questionCount",
            "questionTypes",
        ]

    def test_every_field_error_carries_a_machine_readable_code(self, ctx: Ctx) -> None:
        """Same ``{field, code, message}`` shape as the question validator."""
        response = ctx.save_configuration(valid_configuration(passMark=0, maxAttempts=0))
        for issue in response.json()["error"]["details"]:
            assert set(issue) == {"field", "code", "message"}
            assert issue["code"]

    def test_rejects_a_non_object_payload(self, ctx: Ctx) -> None:
        response = ctx.save_configuration("not-a-configuration")
        assert response.status_code >= 400
        assert ctx.version_count() == 0

    def test_rejects_an_unknown_topic_scope(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(valid_configuration(topicIds=["no-such-topic"]))
        assert response.status_code == 422
        assert "topicIds" in field_errors(response.json())
        assert ctx.version_count() == 0

    def test_never_leaks_a_stack_trace(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(valid_configuration(passMark=0))
        assert "Traceback" not in response.text
        assert 'File "' not in response.text


class TestAuthorisation:
    def test_rejects_an_unauthenticated_save(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(valid_configuration(), token="nope")
        assert response.status_code == 401
        assert ctx.version_count() == 0

    def test_rejects_a_learner_configuring_a_quiz(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(valid_configuration(), token=LEARNER_TOKEN)
        assert response.status_code == 403
        assert ctx.version_count() == 0

    def test_unknown_quiz_returns_404(self, ctx: Ctx) -> None:
        response = ctx.save_configuration(valid_configuration(), quiz_id=9999)
        assert response.status_code == 404
