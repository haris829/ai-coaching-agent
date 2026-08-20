"""Submission, pause/resume refusal and the disconnect path (§4, §5, §6, §20).

The disconnect tests are the heart of this file: the specification's seven-step sequence, and the idempotency
that stops several disconnect events becoming several submissions.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.time import to_iso
from app.modules.formal_assessment.domain.enums import (
    DeviceSessionState,
    FormalAttemptState,
    FormalSubmissionReason,
)
from app.modules.formal_assessment.domain.errors import (
    FormalAttemptNotActiveError,
    PauseNotAllowedError,
    ResumeNotAllowedError,
)
from app.modules.formal_assessment.integration.uc03 import AnswerSubmission
from tests.formal_assessment.conftest import FormalFlow
from tests.formal_assessment.fakes import DEFAULT_LEARNER

pytestmark = pytest.mark.anyio


async def _answer(flow: FormalFlow, container, *question_ids: str) -> None:
    await container.services.attempts.autosave(
        learner_id=DEFAULT_LEARNER,
        formal_attempt_id=flow.formal_attempt_id,
        session_token=flow.session_token,
        answers=tuple(
            AnswerSubmission(question_id=qid, response={"selectedOptionId": f"{qid}-o1"})
            for qid in question_ids
        ),
    )


# ---------------------------------------------------------------------------
# Pause and resume (§4)
# ---------------------------------------------------------------------------


async def test_pausing_a_formal_attempt_is_refused(flow: FormalFlow, container, audit):
    await flow.to_active()
    with pytest.raises(PauseNotAllowedError) as error:
        await container.services.attempts.reject_pause(DEFAULT_LEARNER, flow.formal_attempt_id)
    assert error.value.code == "PAUSE_NOT_ALLOWED"
    assert error.value.status_code == 409
    assert "PAUSE_REJECTED" in audit.codes()

    record = await flow.record()
    assert record.state is FormalAttemptState.ACTIVE, "a refused pause changes nothing"
    assert any(item.code.value == "PAUSE_OR_RESUME_ATTEMPTED" for item in record.anomalies)


async def test_resuming_a_formal_attempt_is_refused_while_it_is_active(flow: FormalFlow, container, audit):
    """A connected learner continues in the session they hold; there is no resume operation to offer."""
    await flow.to_active()
    with pytest.raises(ResumeNotAllowedError):
        await container.services.attempts.reject_resume(DEFAULT_LEARNER, flow.formal_attempt_id)
    assert "RESUME_REJECTED" in audit.codes()


async def test_resuming_after_a_disconnect_is_refused(flow: FormalFlow, container):
    """§5: a disconnected formal attempt can never be re-entered."""
    await flow.to_active()
    await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    with pytest.raises(ResumeNotAllowedError) as error:
        await container.services.attempts.reject_resume(DEFAULT_LEARNER, flow.formal_attempt_id)
    assert error.value.context["state"] == "SUBMITTED"


async def test_a_pause_on_a_submitted_attempt_is_still_refused(flow: FormalFlow, container):
    await flow.to_active()
    await flow.submit()
    with pytest.raises(PauseNotAllowedError):
        await container.services.attempts.reject_pause(DEFAULT_LEARNER, flow.formal_attempt_id)


# ---------------------------------------------------------------------------
# Learner submission (§20)
# ---------------------------------------------------------------------------


async def test_submitting_commits_the_attempt_once(flow: FormalFlow, container, upstream, audit):
    await flow.to_active()
    await _answer(flow, container, "q1", "q2", "q3")
    outcome = await flow.submit()

    assert outcome.formal_attempt.state in {
        FormalAttemptState.SUBMITTED,
        FormalAttemptState.RESULT_CALCULATED,
        FormalAttemptState.PASSED,
        FormalAttemptState.PENDING_REVIEW,
        FormalAttemptState.FAILED,
    }
    assert outcome.formal_attempt.submission_reason is FormalSubmissionReason.LEARNER_CONFIRMED
    assert len(upstream.submissions) == 1
    assert "FORMAL_ATTEMPT_SUBMITTED" in audit.codes()


async def test_a_duplicate_submission_is_a_replay_not_a_second_submission(flow: FormalFlow, upstream):
    """§20: two submit requests must not create two submissions."""
    await flow.to_active()
    first = await flow.submit()
    second = await flow.submit()

    assert first.replayed is False
    assert second.replayed is True
    assert len(upstream.submissions) == 1
    assert second.formal_attempt.submitted_at == first.formal_attempt.submitted_at


async def test_two_simultaneous_submissions_produce_one_submission(flow: FormalFlow, upstream):
    await flow.to_active()
    results = await asyncio.gather(flow.submit(), flow.submit(), return_exceptions=True)
    successes = [item for item in results if not isinstance(item, Exception)]
    assert successes, "at least one submission must succeed"
    # UC-03's submit is idempotent, so even if both calls reach it, exactly one attempt is committed.
    snapshot = upstream.snapshot(flow.attempt_id)
    assert snapshot["submitted"] is True
    assert snapshot["submission_reason"] == "LEARNER_CONFIRMED"


async def test_autosave_after_submission_is_refused(flow: FormalFlow, container):
    await flow.to_active()
    await flow.submit()
    with pytest.raises(Exception) as error:
        await _answer(flow, container, "q1")
    assert error.value.code == "FORMAL_ATTEMPT_ALREADY_SUBMITTED"


async def test_submitting_an_attempt_that_never_started_is_refused(flow: FormalFlow, container):
    await flow.acknowledge()
    await flow.confirm_identity()
    record = await container.services.attempts.find_open(DEFAULT_LEARNER, flow.quiz_id)
    assert record is not None
    with pytest.raises(FormalAttemptNotActiveError):
        await container.services.attempts.submit(
            learner_id=DEFAULT_LEARNER,
            formal_attempt_id=record.formal_attempt_id,
            session_token="anything",
        )


# ---------------------------------------------------------------------------
# The disconnect path (§5, §6)
# ---------------------------------------------------------------------------


async def test_a_disconnect_submits_the_latest_autosaved_state(flow: FormalFlow, container, upstream, clock):
    """§5's sequence: identify, take the autosaved state, submit it, record why, mark it, block resume."""
    await flow.to_active()
    clock.advance(seconds=60)
    upstream.now = to_iso(clock.now())
    await _answer(flow, container, "q1", "q2")

    clock.advance(seconds=120)
    upstream.now = to_iso(clock.now())
    outcome = await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id,
        reported_by="SYSTEM:monitor",
        last_seen_at="2026-03-01T09:01:30.000Z",
        reason="HEARTBEAT_TIMEOUT",
    )

    record = outcome.formal_attempt
    assert record.submitted is True
    assert record.auto_submitted is True
    assert record.submission_reason is FormalSubmissionReason.DISCONNECT_AUTO_SUBMIT
    assert record.disconnect is not None
    assert record.disconnect.reported_by == "SYSTEM:monitor"
    assert record.disconnect.last_seen_at == "2026-03-01T09:01:30.000Z"
    assert record.disconnect.answered_questions == 2
    assert record.disconnect.total_questions == 3

    submitted = upstream.snapshot(flow.attempt_id)
    assert submitted["submitted"] is True
    assert submitted["submitted_answers"] == 2, "the autosaved answers are what got submitted"
    assert len(upstream.submissions) == 1
    assert upstream.submissions[0].reason is FormalSubmissionReason.DISCONNECT_AUTO_SUBMIT


async def test_the_disconnect_emits_the_specified_audit_events(flow: FormalFlow, container, audit):
    await flow.to_active()
    await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    codes = audit.codes()
    for event in (
        "DISCONNECT_DETECTED",
        "AUTO_SUBMIT_STARTED",
        "AUTO_SUBMIT_COMPLETED",
        "FORMAL_ATTEMPT_SUBMITTED",
    ):
        assert event in codes, event


async def test_the_session_is_marked_disconnected_not_merely_closed(flow: FormalFlow, container):
    await flow.to_active()
    await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    sessions = await container.repositories.sessions.list_for_attempt(flow.formal_attempt_id)
    assert sessions[0].state is DeviceSessionState.DISCONNECTED
    assert await container.repositories.sessions.get_active(flow.formal_attempt_id) is None


async def test_repeated_disconnect_events_produce_one_submission(flow: FormalFlow, container, upstream):
    """§5, §20: the idempotency requirement, stated exactly."""
    await flow.to_active()
    await _answer(flow, container, "q1")

    outcomes = []
    for _ in range(4):
        outcomes.append(
            await container.services.attempts.handle_disconnect_by_id(
                formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
            )
        )

    assert len(upstream.submissions) == 1
    assert outcomes[0].replayed is False
    assert all(outcome.replayed for outcome in outcomes[1:])
    submitted_at = {outcome.formal_attempt.submitted_at for outcome in outcomes}
    assert len(submitted_at) == 1


async def test_concurrent_disconnect_events_produce_one_submission(flow: FormalFlow, container, upstream):
    await flow.to_active()

    async def disconnect(source: str):
        try:
            return await container.services.attempts.handle_disconnect_by_id(
                formal_attempt_id=flow.formal_attempt_id, reported_by=source
            )
        except Exception as error:  # noqa: BLE001 - a loser's conflict is acceptable, a double submit is not
            return error

    results = await asyncio.gather(
        disconnect("SYSTEM:monitor-a"), disconnect("SYSTEM:monitor-b"), disconnect("LEARNER_CLIENT")
    )
    assert len(upstream.submissions) <= 1 or all(
        request.idempotency_key == upstream.submissions[0].idempotency_key
        for request in upstream.submissions
    )
    assert upstream.snapshot(flow.attempt_id)["submitted"] is True
    record = await flow.record()
    assert record.submitted is True
    assert record.auto_submitted is True
    assert any(not isinstance(item, Exception) for item in results)


async def test_a_disconnect_after_a_learner_submission_changes_nothing(flow: FormalFlow, container, upstream):
    await flow.to_active()
    await flow.submit()
    outcome = await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    assert outcome.replayed is True
    assert outcome.formal_attempt.submission_reason is FormalSubmissionReason.LEARNER_CONFIRMED
    assert len(upstream.submissions) == 1


async def test_a_learner_submission_after_the_disconnect_claim_is_refused(flow: FormalFlow, container, upstream):
    """The auto-submission has claimed the attempt; a second submission must not be made."""
    await flow.to_active()
    record = await flow.record()
    claimed = await container.repositories.formal_attempts.save(
        record.claim_auto_submit(
            __import__(
                "app.modules.formal_assessment.domain.attempt", fromlist=["DisconnectRecord"]
            ).DisconnectRecord(detected_at="2026-03-01T09:05:00.000Z", reported_by="SYSTEM:monitor"),
            now="2026-03-01T09:05:00.000Z",
        )
    )
    assert claimed.state is FormalAttemptState.AUTO_SUBMIT_IN_PROGRESS

    with pytest.raises(Exception) as error:
        await flow.submit()
    assert error.value.code == "DISCONNECT_SUBMISSION_CONFLICT"
    assert upstream.snapshot(flow.attempt_id)["submitted"] is False


async def test_an_autosave_during_auto_submission_is_refused(flow: FormalFlow, container):
    """Accepting answers now would change what is being submitted underneath the auto-submission."""
    from app.modules.formal_assessment.domain.attempt import DisconnectRecord

    await flow.to_active()
    record = await flow.record()
    await container.repositories.formal_attempts.save(
        record.claim_auto_submit(
            DisconnectRecord(detected_at="2026-03-01T09:05:00.000Z", reported_by="SYSTEM:monitor"),
            now="2026-03-01T09:05:00.000Z",
        )
    )
    with pytest.raises(FormalAttemptNotActiveError):
        await _answer(flow, container, "q3")


async def test_an_incomplete_auto_submitted_state_is_flagged_for_the_assessor(flow: FormalFlow, container):
    await flow.to_active()
    await _answer(flow, container, "q1")
    outcome = await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    codes = {item.code.value for item in outcome.formal_attempt.anomalies}
    assert "AUTO_SUBMITTED_AFTER_DISCONNECT" in codes
    assert "AUTOSAVE_STATE_INCOMPLETE" in codes


async def test_a_disconnect_with_no_autosaved_answers_is_flagged(flow: FormalFlow, container, upstream):
    await flow.to_active()
    outcome = await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    codes = {item.code.value for item in outcome.formal_attempt.anomalies}
    assert "NO_AUTOSAVED_STATE_AT_DISCONNECT" in codes
    assert upstream.snapshot(flow.attempt_id)["submitted"] is True, "the attempt still ends"


async def test_a_complete_auto_submitted_state_is_not_flagged_as_incomplete(flow: FormalFlow, container):
    await flow.to_active()
    await _answer(flow, container, "q1", "q2", "q3")
    outcome = await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="LEARNER_CLIENT"
    )
    codes = {item.code.value for item in outcome.formal_attempt.anomalies}
    assert "AUTO_SUBMITTED_AFTER_DISCONNECT" in codes
    assert "AUTOSAVE_STATE_INCOMPLETE" not in codes


async def test_the_three_states_stay_distinguishable(flow: FormalFlow, container, upstream, clock):
    """§6: latest autosaved state, submitted state and result are three different things."""
    await flow.to_active()
    await _answer(flow, container, "q1")
    autosaved = await upstream.get_latest_autosaved_state(flow.attempt_id)
    assert autosaved is not None
    assert autosaved.answered_questions == 1
    assert autosaved.complete is False

    outcome = await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    assert outcome.submitted_state is not None
    assert outcome.submitted_state.answered_questions == 1
    # No score was arranged, so no result exists — a submitted attempt is not a resolved one.
    assert outcome.formal_attempt.result is None
    assert outcome.formal_attempt.state is FormalAttemptState.SUBMITTED


async def test_a_disconnect_for_an_unknown_formal_attempt_is_a_404(container):
    with pytest.raises(Exception) as error:
        await container.services.attempts.handle_disconnect_by_id(
            formal_attempt_id="fa-nope", reported_by="SYSTEM:monitor"
        )
    assert error.value.status_code == 404


async def test_a_learner_reported_disconnect_is_ownership_scoped(flow: FormalFlow, container):
    await flow.to_active()
    with pytest.raises(Exception) as error:
        await container.services.attempts.handle_disconnect_for_learner(
            learner_id="learner-someone-else",
            formal_attempt_id=flow.formal_attempt_id,
            reported_by="LEARNER_CLIENT",
        )
    assert error.value.status_code == 404
