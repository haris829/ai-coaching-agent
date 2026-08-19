"""UC-05's service: the verdict, the certificate gate, duplicate prevention and CPD isolation.

The chain up to this point is real: UC-04 scores the attempt into its own tables and UC-05 reads
those rows through the real adapter. What is controlled is the certificate service and the CPD
system, so their failure modes can be exercised without patching anything.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.certification.domain import errors
from app.modules.certification.domain.enums import CertificateStatus, CpdSyncStatus, Outcome
from app.modules.scoring.domain.enums import QuestionType
from tests.support.results_world import (
    COURSE_NAME,
    LEARNER_ID,
    OTHER_LEARNER_ID,
    ResultsWorld,
    answer_key,
    delivered,
    option,
    submitted_attempt,
)


def _scored_attempt(
    world: ResultsWorld,
    *,
    correct: int,
    total: int = 4,
    pass_mark: float = 70.0,
    attempt_id: str = "attempt-1",
    attempt_number: int = 1,
    learner_id: str = LEARNER_ID,
) -> None:
    """Score an attempt with ``correct`` of ``total`` answers right, through the real UC-04
    service."""
    questions = []
    for position in range(1, total + 1):
        selected = "A" if position <= correct else "B"
        question = delivered(
            QuestionType.SINGLE_CHOICE,
            position=position,
            question_id=f"{attempt_id}-q{position}",
            options=[option("A", "Right", correct=True), option("B", "Wrong")],
            response={"type": "SINGLE_CHOICE", "selectedOptionId": selected},
        )
        questions.append(question)
        world.answer_keys.add(answer_key(question, correct_ids=["A"]))

    world.attempts.add(
        submitted_attempt(
            questions,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            learner_id=learner_id,
            pass_mark=pass_mark,
        )
    )
    world.score(attempt_id)


class TestPassAndFail:
    def test_a_score_at_or_above_the_pass_mark_passes(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=3, total=4, pass_mark=75.0)

        view = world.determine()

        assert view.outcome.outcome == str(Outcome.PASS)
        assert view.outcome.percentage == 75.0
        assert view.outcome.pass_mark_percentage == 75.0
        assert view.passed is True
        assert view.created is True

    def test_a_score_below_the_pass_mark_fails(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=2, total=4, pass_mark=70.0)

        view = world.determine()

        assert view.outcome.outcome == str(Outcome.FAIL)
        assert view.outcome.percentage == 50.0

    def test_the_pass_mark_comes_from_the_attempts_own_configuration_version(
        self, world: ResultsWorld
    ) -> None:
        """The bar is the one the learner sat under.

        UC-05 can only reach a pass mark through the attempt, whose configuration snapshot UC-03
        froze at creation -- there is no method on the port that could return the quiz's *current*
        pass mark. The stored outcome carries the figure it applied, so the decision stays
        explainable after the quiz is reconfigured. ``tests/integration/test_results_chain.py``
        proves the same thing through the real UC-01 adapter, by actually saving a new configuration
        version mid-course.
        """
        _scored_attempt(world, correct=3, total=4, pass_mark=75.0)

        view = world.determine()

        assert view.outcome.pass_mark_percentage == 75.0
        assert view.outcome.outcome == str(Outcome.PASS)
        assert view.outcome.configuration_version_id == "42"
        # UC-04 froze the same figure from the same snapshot; the two can never disagree.
        with world.unit_of_work() as ctx:
            assert ctx.scoring.find_result("attempt-1").pass_mark_percentage == 75.0

    def test_a_fail_reports_the_remaining_attempts(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=1, total=4)
        world.attempts.max_attempts = 3
        world.attempts.attempts_used[LEARNER_ID] = 1

        view = world.determine()

        assert view.outcome.outcome == str(Outcome.FAIL)
        assert view.attempts_used == 1
        assert view.max_attempts == 3
        assert view.attempts_remaining == 2

    def test_a_fail_on_the_last_allowed_attempt_reports_none_remaining(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=1, total=4, attempt_number=3)
        world.attempts.max_attempts = 3
        world.attempts.attempts_used[LEARNER_ID] = 3

        view = world.determine()

        assert view.attempts_remaining == 0

    def test_an_unlimited_allowance_reports_no_maximum(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=1, total=4)
        world.attempts.max_attempts = None

        view = world.determine()

        assert view.max_attempts is None
        assert view.attempts_remaining is None

    def test_pass_fail_cannot_be_determined_from_a_pending_score(self, world: ResultsWorld) -> None:
        # An attempt whose answer key is missing: UC-04 leaves it PENDING_SCORE.
        question = delivered(
            QuestionType.SINGLE_CHOICE,
            options=[option("A", "Right"), option("B", "Wrong")],
            response={"type": "SINGLE_CHOICE", "selectedOptionId": "A"},
        )
        world.attempts.add(submitted_attempt([question]))
        world.score()

        with pytest.raises(errors.AppError) as caught:
            world.determine()

        assert caught.value.code == "RESULT_NOT_CONFIRMED"
        assert caught.value.status == 409
        assert caught.value.retryable is True

    def test_an_attempt_with_no_score_at_all_cannot_be_determined(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=4, attempt_id="scored")
        world.attempts.add(submitted_attempt([], attempt_id="unscored"))

        with pytest.raises(errors.AppError) as caught:
            world.determine("unscored")

        assert caught.value.code == "RESULT_NOT_CONFIRMED"

    def test_another_learners_attempt_is_not_found(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4)

        with pytest.raises(errors.AppError) as caught:
            world.determine(learner_id=OTHER_LEARNER_ID)

        assert caught.value.code == "ATTEMPT_NOT_FOUND"


class TestOutcomeImmutability:
    def test_determining_twice_returns_the_same_outcome(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=3, total=4)

        first = world.determine()
        second = world.determine()

        assert second.created is False
        assert second.outcome.id == first.outcome.id
        assert second.outcome.determined_at == first.outcome.determined_at

    def test_there_is_exactly_one_outcome_row_per_attempt(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=3, total=4)

        world.determine()
        world.determine()
        world.determine()

        with world.session() as session:
            count = session.execute(text("SELECT COUNT(*) FROM qg_attempt_outcomes")).scalar()
        assert count == 1

    def test_the_database_refuses_to_update_an_outcome(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=3, total=4)
        world.determine()

        with world.session() as session, pytest.raises(Exception) as caught:
            session.execute(text("UPDATE qg_attempt_outcomes SET outcome = 'FAIL'"))
            session.commit()

        assert "IMMUTABLE_ATTEMPT_OUTCOME" in str(caught.value)

    def test_a_reconfigured_pass_mark_cannot_flip_a_determined_outcome(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=3, total=4, pass_mark=70.0)
        first = world.determine()
        assert first.outcome.outcome == str(Outcome.PASS)

        world.attempts.replace(
            submitted_attempt(
                world.attempts.get_attempt("attempt-1").questions,
                attempt_id="attempt-1",
                pass_mark=100.0,
            )
        )

        second = world.determine()

        assert second.outcome.outcome == str(Outcome.PASS)
        assert second.outcome.pass_mark_percentage == 70.0


class TestCertificateGating:
    def test_a_pass_issues_a_certificate(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)

        view = world.determine()

        assert view.certificate is not None
        assert view.certificate.status == str(CertificateStatus.ISSUED)
        assert view.certificate.certificate_number is not None
        assert view.certificate.issued_at is not None
        assert view.certificate.course_name == COURSE_NAME

    def test_a_fail_issues_nothing(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=1, total=4)

        view = world.determine()

        assert view.certificate is None
        assert world.certificates.calls == []

    def test_the_certificate_service_is_given_the_frozen_course_name_and_score(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=4, total=4)

        world.determine()

        request = world.certificates.calls[0]
        assert request.course_name == COURSE_NAME
        assert request.percentage == 100.0
        assert request.idempotency_key == "certificate:attempt-1"
        assert request.attempt_date.endswith("Z")

    def test_a_transient_certificate_failure_leaves_the_pass_intact(
        self, world: ResultsWorld
    ) -> None:
        """The requirement: if the certificate service is unavailable, the result is unchanged."""
        _scored_attempt(world, correct=4, total=4)
        world.certificates.fail_transiently()

        view = world.determine()

        assert view.outcome.outcome == str(Outcome.PASS)
        assert view.certificate.status == str(CertificateStatus.PENDING)
        assert view.certificate.failure_code == "CERTIFICATE_SERVICE_UNAVAILABLE"
        assert view.certificate.certificate_number is None
        # The score itself is untouched.
        with world.unit_of_work() as ctx:
            assert ctx.scoring.find_result("attempt-1").percentage == 100.0

    def test_a_pending_certificate_is_issued_on_retry(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)
        world.certificates.fail_transiently()
        world.determine()

        world.certificates.succeed()
        with world.unit_of_work() as ctx:
            view = ctx.certification.retry_certificate("attempt-1")

        assert view.certificate.status == str(CertificateStatus.ISSUED)
        assert view.certificate.certificate_number is not None
        assert view.certificate.generation_attempt_count == 2

    def test_a_retry_that_still_fails_reports_a_retryable_error(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)
        world.certificates.fail_transiently()
        world.determine()

        with world.unit_of_work() as ctx, pytest.raises(errors.AppError) as caught:
            ctx.certification.retry_certificate("attempt-1")

        assert caught.value.code == "CERTIFICATE_UNAVAILABLE"
        assert caught.value.retryable is True

    def test_a_permanent_certificate_failure_is_recorded_and_not_retried_forever(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=4, total=4)
        world.certificates.fail_permanently()

        view = world.determine()

        assert view.certificate.status == str(CertificateStatus.FAILED)
        assert view.certificate.failure_code == "CERTIFICATE_SERVICE_REJECTED"
        assert view.outcome.outcome == str(Outcome.PASS)

    def test_determining_again_does_not_issue_a_second_certificate(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=4, total=4)

        world.determine()
        world.determine()
        world.determine()

        assert len(world.certificates.calls) == 1
        with world.session() as session:
            count = session.execute(text("SELECT COUNT(*) FROM qg_certificates")).scalar()
        assert count == 1

    def test_retrying_an_issued_certificate_is_a_no_op(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)
        issued = world.determine().certificate

        with world.unit_of_work() as ctx:
            view = ctx.certification.retry_certificate("attempt-1")

        assert view.certificate.certificate_number == issued.certificate_number
        assert view.certificate.issued_at == issued.issued_at
        assert len(world.certificates.calls) == 1

    def test_passing_a_second_time_does_not_mint_a_second_certificate(
        self, world: ResultsWorld
    ) -> None:
        """One issued certificate per learner and quiz -- the duplicate-prevention guarantee."""
        _scored_attempt(world, correct=4, total=4, attempt_id="attempt-1", attempt_number=1)
        world.determine("attempt-1")

        _scored_attempt(world, correct=4, total=4, attempt_id="attempt-2", attempt_number=2)
        second = world.determine("attempt-2")

        assert second.outcome.outcome == str(Outcome.PASS)
        assert second.certificate.status == str(CertificateStatus.FAILED)
        assert second.certificate.failure_code == "CERTIFICATE_ALREADY_ISSUED"
        with world.session() as session:
            issued = session.execute(
                text("SELECT COUNT(*) FROM qg_certificates WHERE status = 'ISSUED'")
            ).scalar()
        assert issued == 1

    def test_the_database_refuses_a_second_issued_certificate_for_the_same_quiz(
        self, world: ResultsWorld
    ) -> None:
        """Belt and braces: even bypassing the service, the partial unique index refuses it."""
        _scored_attempt(world, correct=4, total=4)
        world.determine()

        with world.session() as session, pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO qg_certificates (id, attempt_id, outcome_id, learner_id, "
                    "course_id, quiz_id, course_name, percentage, status, certificate_number, "
                    "generation_attempt_count, requested_at, issued_at, created_at, updated_at) "
                    "SELECT 'dup', 'attempt-9', outcome_id, learner_id, course_id, quiz_id, "
                    "course_name, percentage, 'ISSUED', 'CERT-DUP', 1, requested_at, issued_at, "
                    "created_at, updated_at FROM qg_certificates"
                )
            )
            session.commit()

    def test_a_certificate_cannot_be_retried_for_a_failed_attempt(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=1, total=4)
        world.determine()

        with world.unit_of_work() as ctx, pytest.raises(errors.AppError) as caught:
            ctx.certification.retry_certificate("attempt-1")

        assert caught.value.code == "CERTIFICATE_NOT_APPLICABLE"


class TestCpdSynchronisation:
    def test_a_pass_is_synchronised_with_the_four_agreed_fields(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)

        view = world.determine()

        assert view.cpd_record.status == str(CpdSyncStatus.SYNCHRONISED)
        record = world.cpd.records[0]
        assert record.attempt_date.endswith("Z")
        assert record.score_percentage == 100.0
        assert record.passed is True
        assert record.course_name == COURSE_NAME

    def test_a_fail_is_synchronised_too(self, world: ResultsWorld) -> None:
        """CPD is a record of activity, not of achievement."""
        _scored_attempt(world, correct=1, total=4)

        view = world.determine()

        assert view.cpd_record.status == str(CpdSyncStatus.SYNCHRONISED)
        assert world.cpd.records[0].passed is False

    def test_a_transient_cpd_failure_never_changes_the_quiz_result(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=4, total=4)
        world.cpd.fail_transiently()

        view = world.determine()

        assert view.outcome.outcome == str(Outcome.PASS)
        assert view.certificate.status == str(CertificateStatus.ISSUED)
        assert view.cpd_record.status == str(CpdSyncStatus.PENDING)
        assert view.cpd_record.failure_code == "CPD_SERVICE_UNAVAILABLE"
        with world.unit_of_work() as ctx:
            assert ctx.scoring.find_result("attempt-1").percentage == 100.0

    def test_a_pending_cpd_record_synchronises_on_retry(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)
        world.cpd.fail_transiently()
        world.determine()

        world.cpd.succeed()
        with world.unit_of_work() as ctx:
            view = ctx.certification.retry_cpd("attempt-1")

        assert view.cpd_record.status == str(CpdSyncStatus.SYNCHRONISED)
        assert view.cpd_record.external_reference is not None
        assert view.cpd_record.sync_attempt_count == 2

    def test_a_cpd_retry_that_still_fails_reports_a_retryable_error(
        self, world: ResultsWorld
    ) -> None:
        _scored_attempt(world, correct=4, total=4)
        world.cpd.fail_transiently()
        world.determine()

        with world.unit_of_work() as ctx, pytest.raises(errors.AppError) as caught:
            ctx.certification.retry_cpd("attempt-1")

        assert caught.value.code == "CPD_SYNC_UNAVAILABLE"
        assert caught.value.retryable is True

    def test_synchronising_twice_does_not_log_a_second_activity(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)

        world.determine()
        world.determine()

        assert len(world.cpd.records) == 1
        with world.session() as session:
            count = session.execute(text("SELECT COUNT(*) FROM qg_cpd_records")).scalar()
        assert count == 1

    def test_a_permanent_cpd_failure_is_recorded(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)
        world.cpd.fail_permanently()

        view = world.determine()

        assert view.cpd_record.status == str(CpdSyncStatus.FAILED)
        assert view.cpd_record.failure_code == "CPD_SERVICE_REJECTED"
        assert view.outcome.outcome == str(Outcome.PASS)


class TestReading:
    def test_an_undetermined_attempt_has_no_outcome(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, total=4)

        with world.unit_of_work() as ctx, pytest.raises(errors.AppError) as caught:
            ctx.certification.find_outcome("attempt-1")

        assert caught.value.code == "OUTCOME_NOT_FOUND"

    def test_the_remaining_attempts_are_recomputed_on_read(self, world: ResultsWorld) -> None:
        """The row keeps the audit copy; the read answers for now."""
        _scored_attempt(world, correct=1, total=4)
        world.attempts.max_attempts = 3
        world.attempts.attempts_used[LEARNER_ID] = 1
        determined = world.determine()
        assert determined.attempts_remaining == 2

        # The learner sits another attempt.
        world.attempts.attempts_used[LEARNER_ID] = 2

        with world.unit_of_work() as ctx:
            view = ctx.certification.find_outcome("attempt-1")

        assert view.attempts_remaining == 1
        assert view.outcome.attempts_remaining_at_outcome == 2

    def test_a_learner_can_list_their_own_outcomes(self, world: ResultsWorld) -> None:
        _scored_attempt(world, correct=4, attempt_id="attempt-1", attempt_number=1)
        _scored_attempt(world, correct=1, attempt_id="attempt-2", attempt_number=2)
        world.determine("attempt-1")
        world.determine("attempt-2")

        with world.unit_of_work() as ctx:
            outcomes = ctx.certification.list_outcomes(LEARNER_ID)

        assert [outcome.attempt_number for outcome in outcomes] == [2, 1]
        assert [outcome.outcome for outcome in outcomes] == [str(Outcome.FAIL), str(Outcome.PASS)]
