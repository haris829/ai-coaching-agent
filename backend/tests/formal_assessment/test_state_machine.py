"""The formal attempt state machine (§15).

These tests are about what the machine *cannot* do. Every negative requirement in UC-09 — no pause, no resume,
no certificate without approval, no second submission — is an absence in the transition table, and an absence is
only worth anything if something checks it.
"""

from __future__ import annotations

import pytest

from app.modules.formal_assessment.domain.anomalies import anomaly
from app.modules.formal_assessment.domain.attempt import (
    ConditionsAcknowledgement,
    DisconnectRecord,
    FormalResult,
    IdentityConfirmation,
    new_formal_attempt,
)
from app.modules.formal_assessment.domain.conditions import REQUIRED_CONDITION_CODES
from app.modules.formal_assessment.domain.enums import (
    FormalAnomalyCode,
    FormalAttemptState,
    FormalSubmissionReason,
)
from app.modules.formal_assessment.domain.errors import InvalidStateTransitionError
from app.modules.formal_assessment.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    can_transition,
    is_terminal,
    reachable_from,
)

NOW = "2026-03-01T09:00:00.000Z"
S = FormalAttemptState


def _record(state: S = S.NOT_STARTED):
    """A formal attempt walked to ``state`` through the real transitions, never constructed at it."""
    record = new_formal_attempt(
        formal_attempt_id="fa-1",
        learner_id="learner-alice",
        course_id="course-1",
        quiz_id="quiz-formal-1",
        idempotency_key="formal-attempt:learner-alice:quiz-formal-1",
        now=NOW,
    )
    if state is S.NOT_STARTED:
        return record

    record = record.acknowledge_conditions(
        ConditionsAcknowledgement(
            conditions_version="2026.1",
            acknowledged_codes=tuple(REQUIRED_CONDITION_CODES),
            acknowledged_at=NOW,
        ),
        now=NOW,
    )
    if state is S.CONDITIONS_ACKNOWLEDGED:
        return record

    record = record.confirm_identity(
        IdentityConfirmation(confirmed_at=NOW, email_confirmed=True), now=NOW
    )
    if state is S.IDENTITY_CONFIRMED:
        return record

    record = record.start(attempt_id="attempt-1", session_id="session-1", now=NOW)
    if state is S.ACTIVE:
        return record

    if state is S.AUTO_SUBMIT_IN_PROGRESS:
        return record.claim_auto_submit(
            DisconnectRecord(detected_at=NOW, reported_by="TEST"), now=NOW
        )

    record = record.submit(reason=FormalSubmissionReason.LEARNER_CONFIRMED, now=NOW)
    if state is S.SUBMITTED:
        return record

    record = record.record_result(
        FormalResult(result_status="PASSED", passed=True, calculated_at=NOW, percentage=90.0),
        now=NOW,
    )
    if state is S.RESULT_CALCULATED:
        return record

    if state is S.FAILED:
        return record.mark_failed(now=NOW)

    record = record.mark_passed(now=NOW)
    if state is S.PASSED:
        return record

    record = record.await_review(review_id="review-1", now=NOW)
    if state is S.PENDING_REVIEW:
        return record

    if state is S.REQUIRES_FURTHER_REVIEW:
        return record.require_further_review(now=NOW)

    record = record.approve(now=NOW)
    if state is S.APPROVED:
        return record

    return record.allow_certificate(now=NOW, certificate_reference="cert-1")


def test_the_happy_path_is_the_specified_lifecycle():
    record = _record(S.CERTIFICATE_ALLOWED)
    assert record.state is S.CERTIFICATE_ALLOWED
    assert record.certificate_allowed is True


def test_every_state_is_reachable_from_not_started():
    reachable = reachable_from(S.NOT_STARTED)
    for state in S:
        if state is S.NOT_STARTED:
            continue
        assert state in reachable, f"{state} is unreachable — a state nothing can enter is a bug"


def test_terminal_states_are_exactly_the_three_ends():
    assert frozenset(
        {S.FAILED, S.REQUIRES_FURTHER_REVIEW, S.CERTIFICATE_ALLOWED}
    ) == TERMINAL_STATES
    for state in TERMINAL_STATES:
        assert is_terminal(state)


def test_there_is_no_paused_state_at_all():
    """§4: a formal assessment cannot be paused. Not "the pause is refused" — there is nowhere to pause to."""
    assert not any(state.value == "PAUSED" for state in S)


def test_auto_submit_has_exactly_one_exit_so_a_disconnect_cannot_be_resumed():
    """§4, §5: the only way out of AUTO_SUBMIT_IN_PROGRESS is SUBMITTED."""
    assert ALLOWED_TRANSITIONS[S.AUTO_SUBMIT_IN_PROGRESS] == frozenset({S.SUBMITTED})
    assert not can_transition(S.AUTO_SUBMIT_IN_PROGRESS, S.ACTIVE)


def test_a_submitted_attempt_cannot_become_active_again():
    for state in (S.SUBMITTED, S.RESULT_CALCULATED, S.PASSED, S.PENDING_REVIEW, S.APPROVED):
        assert not can_transition(state, S.ACTIVE)


def test_a_pass_can_only_go_to_pending_review():
    """§8: a passing formal assessment must not produce a certificate."""
    assert ALLOWED_TRANSITIONS[S.PASSED] == frozenset({S.PENDING_REVIEW})
    assert not can_transition(S.PASSED, S.CERTIFICATE_ALLOWED)
    assert not can_transition(S.PASSED, S.APPROVED)


def test_pending_review_cannot_reach_a_certificate_without_approval():
    """§11: the certificate gate, stated in the transition table."""
    assert not can_transition(S.PENDING_REVIEW, S.CERTIFICATE_ALLOWED)
    assert ALLOWED_TRANSITIONS[S.PENDING_REVIEW] == frozenset(
        {S.APPROVED, S.REQUIRES_FURTHER_REVIEW}
    )


def test_escalation_is_terminal_and_can_never_become_an_approval():
    assert ALLOWED_TRANSITIONS[S.REQUIRES_FURTHER_REVIEW] == frozenset()
    assert not can_transition(S.REQUIRES_FURTHER_REVIEW, S.APPROVED)
    assert not can_transition(S.REQUIRES_FURTHER_REVIEW, S.CERTIFICATE_ALLOWED)


def test_a_failed_result_is_terminal():
    assert ALLOWED_TRANSITIONS[S.FAILED] == frozenset()
    assert not can_transition(S.FAILED, S.PENDING_REVIEW)


def test_submitting_an_attempt_that_never_started_is_refused():
    record = _record(S.IDENTITY_CONFIRMED)
    with pytest.raises(InvalidStateTransitionError) as error:
        record.submit(reason=FormalSubmissionReason.LEARNER_CONFIRMED, now=NOW)
    assert error.value.code == "INVALID_STATE_TRANSITION"
    assert error.value.status_code == 409


def test_submitting_twice_is_refused_by_the_machine():
    """§20: there is no SUBMITTED -> SUBMITTED edge for a duplicate submission to abuse."""
    record = _record(S.SUBMITTED)
    with pytest.raises(InvalidStateTransitionError):
        record.submit(reason=FormalSubmissionReason.LEARNER_CONFIRMED, now=NOW)


def test_approving_an_attempt_that_has_not_passed_is_refused():
    record = _record(S.SUBMITTED)
    with pytest.raises(InvalidStateTransitionError):
        record.approve(now=NOW)


def test_allowing_a_certificate_without_approval_is_refused():
    for state in (S.PASSED, S.PENDING_REVIEW, S.REQUIRES_FURTHER_REVIEW, S.FAILED):
        with pytest.raises(InvalidStateTransitionError):
            _record(state).allow_certificate(now=NOW)


def test_an_invalid_transition_reports_what_was_allowed():
    record = _record(S.PENDING_REVIEW)
    with pytest.raises(InvalidStateTransitionError) as error:
        record.allow_certificate(now=NOW)
    context = error.value.context
    assert context["current_state"] == "PENDING_REVIEW"
    assert context["target_state"] == "CERTIFICATE_ALLOWED"
    assert sorted(context["allowed_target_states"]) == ["APPROVED", "REQUIRES_FURTHER_REVIEW"]


def test_a_refused_transition_leaves_the_record_untouched():
    """Invalid transitions must fail safely: the record is frozen, so nothing is half-changed."""
    record = _record(S.PENDING_REVIEW)
    before = record.as_dict()
    with pytest.raises(InvalidStateTransitionError):
        record.allow_certificate(now="2026-03-02T00:00:00.000Z")
    assert record.as_dict() == before


def test_each_step_produces_a_strictly_higher_version_for_compare_and_set():
    """§20: the version is what makes concurrent writes resolve to one winner."""
    versions = [
        _record(state).version
        for state in (
            S.NOT_STARTED,
            S.CONDITIONS_ACKNOWLEDGED,
            S.IDENTITY_CONFIRMED,
            S.ACTIVE,
            S.SUBMITTED,
            S.PENDING_REVIEW,
            S.CERTIFICATE_ALLOWED,
        )
    ]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_re_acknowledging_conditions_resets_a_prior_identity_confirmation():
    """If the conditions are re-versioned after identity was confirmed, the gate starts again."""
    record = _record(S.IDENTITY_CONFIRMED)
    assert record.identity_confirmed is True
    reacknowledged = record.acknowledge_conditions(
        ConditionsAcknowledgement(
            conditions_version="2026.2",
            acknowledged_codes=tuple(REQUIRED_CONDITION_CODES),
            acknowledged_at=NOW,
        ),
        now=NOW,
    )
    assert reacknowledged.state is S.CONDITIONS_ACKNOWLEDGED
    assert reacknowledged.identity_confirmed is False


def test_recording_an_anomaly_never_changes_the_state():
    record = _record(S.ACTIVE)
    flagged = record.with_anomaly(
        anomaly(FormalAnomalyCode.SECOND_DEVICE_ATTEMPTED, observed_at=NOW), now=NOW
    )
    assert flagged.state is S.ACTIVE
    assert len(flagged.anomalies) == 1


def test_repeated_anomalies_of_one_code_are_counted_not_duplicated():
    record = _record(S.ACTIVE)
    for _ in range(3):
        record = record.with_anomaly(
            anomaly(FormalAnomalyCode.SECOND_DEVICE_ATTEMPTED, observed_at=NOW), now=NOW
        )
    assert len(record.anomalies) == 1
    assert record.anomalies[0].occurrences == 3


def test_in_progress_is_only_active_and_auto_submitting():
    """The AI-coaching restriction keys on ``in_progress``, so its exact membership matters (§7)."""
    for state in S:
        expected = state in {S.ACTIVE, S.AUTO_SUBMIT_IN_PROGRESS}
        assert _record(state).in_progress is expected, state


def test_a_learner_who_has_only_acknowledged_is_not_yet_in_an_assessment():
    """§7: coaching is blocked while an assessment is being sat, not from the moment of acknowledgement."""
    assert _record(S.CONDITIONS_ACKNOWLEDGED).in_progress is False
    assert _record(S.IDENTITY_CONFIRMED).in_progress is False
    assert _record(S.ACTIVE).in_progress is True
    assert _record(S.SUBMITTED).in_progress is False
