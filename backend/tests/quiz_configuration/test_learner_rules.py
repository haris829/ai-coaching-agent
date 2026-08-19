"""The learner rules summary — UC-01's own requirement.

Two properties matter here and are UC-01's to keep:

* the summary reflects the quiz's **active** configuration version, and follows it when it changes;
* it reports remaining attempt information — read from UC-03 through
  :class:`~app.modules.quiz_configuration.ports.AttemptStatisticsPort`, because UC-03 owns attempts.

**Reading the rules must never create an attempt.** That is asserted here by patching UC-03's attempt
creation to fail loudly and then viewing the summary repeatedly.

Starting a quiz is UC-03's (``POST /api/v1/attempts``) and is tested there and in
``tests/integration/``. UC-01 used to carry a provisional version of it; that endpoint is gone.
"""

from __future__ import annotations

from app.core.question_types import QuestionType
from tests.harness import LEARNER2_TOKEN, LEARNER_TOKEN, Ctx, valid_configuration


class TestRulesSummary:
    def test_shows_the_values_from_the_active_configuration(self, ctx: Ctx) -> None:
        ctx.save_configuration(
            valid_configuration(
                questionCount=20,
                timeLimitMinutes=30,
                passMark=70,
                maxAttempts=2,
                deliveryMode="assessment",
                questionTypes=[
                    {"type": "SINGLE_CHOICE", "quota": 12},
                    {"type": "TRUE_FALSE", "quota": 8},
                ],
            )
        )

        response = ctx.get_rules()

        assert response.status_code == 200
        body = response.json()
        assert body["questionCount"] == 20
        assert body["timeLimitMinutes"] == 30
        assert body["passMark"] == 70
        assert body["maxAttempts"] == 2
        assert body["attemptsUsed"] == 0
        assert body["remainingAttempts"] == 2
        assert body["canStart"] is True
        assert body["blockedReason"] is None
        assert body["attemptInProgress"] is None
        assert body["configurationVersionNumber"] == 1
        assert body["quiz"]["title"] == "Test Quiz"
        assert body["questionTypes"] == [
            {"type": "SINGLE_CHOICE", "quota": 12},
            {"type": "TRUE_FALSE", "quota": 8},
        ]

    def test_publishes_the_delivery_settings_uc03_will_obey(self, ctx: Ctx) -> None:
        ctx.save_configuration(
            valid_configuration(
                questionPresentation="ONE_AT_A_TIME",
                randomiseOptionOrder=True,
                allowIncompleteSubmission=False,
            )
        )
        body = ctx.get_rules().json()
        assert body["questionPresentation"] == "ONE_AT_A_TIME"
        assert body["randomiseOptionOrder"] is True
        assert body["allowIncompleteSubmission"] is False

    def test_follows_the_configuration_when_it_changes(self, ctx: Ctx) -> None:
        ctx.save_configuration(valid_configuration(passMark=60, questionCount=10))
        assert ctx.get_rules().json()["passMark"] == 60

        ctx.save_configuration(
            valid_configuration(passMark=85, questionCount=12, timeLimitMinutes=None)
        )
        body = ctx.get_rules().json()
        assert body["passMark"] == 85
        assert body["questionCount"] == 12
        assert body["timeLimitMinutes"] is None
        assert body["configurationVersionNumber"] == 2

    def test_remaining_attempts_reflect_uc03s_attempts(self, ctx: Ctx) -> None:
        """The attempt counts come from UC-03; UC-01 keeps no attempt table of its own."""
        ctx.save_configuration(valid_configuration(maxAttempts=3))
        assert ctx.get_rules().json()["remainingAttempts"] == 3

        for used, remaining in [(1, 2), (2, 1), (3, 0)]:
            attempt_id, _ = ctx.start_and_read_questions()
            assert ctx.submit_attempt(attempt_id).status_code == 200
            body = ctx.get_rules().json()
            assert body["attemptsUsed"] == used
            assert body["remainingAttempts"] == remaining

        final = ctx.get_rules().json()
        assert final["canStart"] is False
        assert final["blockedReason"] == "attempt_limit_reached"

    def test_attempts_are_counted_per_learner(self, ctx: Ctx) -> None:
        ctx.save_configuration(valid_configuration(maxAttempts=1))
        ctx.start_and_read_questions(LEARNER_TOKEN)

        other = ctx.get_rules(LEARNER2_TOKEN).json()
        assert other["attemptsUsed"] == 0
        assert other["remainingAttempts"] == 1
        assert other["canStart"] is True

    def test_surfaces_an_in_progress_attempt_to_resume(self, ctx: Ctx) -> None:
        ctx.save_configuration(valid_configuration(maxAttempts=3))
        attempt_id, _ = ctx.start_and_read_questions()

        body = ctx.get_rules().json()
        assert body["attemptInProgress"]["id"] == attempt_id
        assert body["canStart"] is False
        assert body["blockedReason"] == "attempt_in_progress"

    def test_blocks_start_when_the_bank_can_no_longer_satisfy_the_configuration(
        self, ctx: Ctx
    ) -> None:
        ctx.save_configuration(
            valid_configuration(
                questionCount=10, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}]
            )
        )
        ctx.retire(QuestionType.SINGLE_CHOICE)

        rules = ctx.get_rules().json()
        assert rules["canStart"] is False
        assert rules["blockedReason"] == "question_bank_insufficient"

    def test_unconfigured_quiz_gives_a_meaningful_error(self, ctx: Ctx) -> None:
        response = ctx.get_rules()
        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "CONFIGURATION_UNAVAILABLE"
        assert "Test Quiz" in body["error"]["message"]

    def test_requires_authentication(self, ctx: Ctx) -> None:
        ctx.save_configuration(valid_configuration())
        assert ctx.get_rules("bogus-token").status_code == 401

    # ----------------------------------------------------------------------
    # The critical guarantee: reading the rules must never create an attempt.
    # ----------------------------------------------------------------------
    def test_viewing_the_rules_does_not_create_an_attempt(self, ctx: Ctx, monkeypatch) -> None:
        from app.modules.attempt_delivery.repositories.attempt_repository import (
            AttemptRepository,
        )

        ctx.save_configuration(valid_configuration(maxAttempts=2))

        def forbidden_add(*_args, **_kwargs):
            raise AssertionError("viewing the rules must not create an attempt")

        # Patch UC-03's insert, not UC-01's: UC-01 has no way to create an attempt at all now, so
        # the guarantee worth asserting is that reading the rules does not reach UC-03's writer.
        monkeypatch.setattr(AttemptRepository, "add", forbidden_add)

        for _ in range(5):
            assert ctx.get_rules().status_code == 200

        monkeypatch.undo()
        assert ctx.attempt_count() == 0
        # The allowance is untouched by all that viewing.
        assert ctx.get_rules().json()["remainingAttempts"] == 2

    def test_reading_configuration_does_not_create_an_attempt(self, ctx: Ctx) -> None:
        ctx.save_configuration(valid_configuration())
        ctx.get_configuration()
        ctx.get_versions()
        ctx.get_question_bank()
        ctx.get_rules()
        assert ctx.attempt_count() == 0
        assert ctx.delivered_question_count() == 0
