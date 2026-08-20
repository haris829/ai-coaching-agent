"""The single-device session lock (§3, §20).

The lock is a uniqueness constraint plus a server-issued token. These tests check both halves: a second device
cannot take the session, and a caller who is the right learner but does not hold the token cannot act on the
attempt either.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.time import to_iso
from app.modules.formal_assessment.domain.device import DeviceDescriptor
from app.modules.formal_assessment.domain.enums import DeviceSessionState, FormalAttemptState
from app.modules.formal_assessment.domain.errors import (
    DeviceSessionConflictError,
    FormalAttemptAlreadyStartedError,
    SecondDeviceRejectedError,
)
from app.modules.formal_assessment.integration.uc03 import AnswerSubmission
from tests.formal_assessment.conftest import FormalFlow
from tests.formal_assessment.fakes import CLIENT_REQUEST_ID, DEFAULT_LEARNER, DEFAULT_QUIZ

pytestmark = pytest.mark.anyio


async def test_the_first_session_becomes_authoritative(flow: FormalFlow, container, audit):
    outcome = await flow.to_active()
    assert outcome.formal_attempt.state is FormalAttemptState.ACTIVE
    assert outcome.session.state is DeviceSessionState.ACTIVE
    assert outcome.formal_attempt.device_session_id == outcome.session.session_id
    assert "DEVICE_SESSION_REGISTERED" in audit.codes()

    active = await container.repositories.sessions.get_active(outcome.formal_attempt.formal_attempt_id)
    assert active is not None
    assert active.session_id == outcome.session.session_id


async def test_the_session_token_is_server_generated_and_not_the_client_fingerprint(flow: FormalFlow):
    outcome = await flow.to_active()
    assert outcome.session.session_token
    assert outcome.session.session_token != outcome.session.device.fingerprint
    assert outcome.session.device.fingerprint == "device-a"


async def test_a_second_device_is_rejected(flow: FormalFlow, container, audit):
    await flow.to_active()
    with pytest.raises(SecondDeviceRejectedError) as error:
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            device=DeviceDescriptor(fingerprint="device-b"),
        )
    assert error.value.code == "SECOND_DEVICE_REJECTED"
    assert error.value.status_code == 409
    assert "SECOND_DEVICE_REJECTED" in audit.codes()


async def test_a_rejected_device_is_recorded_as_evidence(flow: FormalFlow, container):
    """§10: an assessor reviewing the pass must be able to see that another device tried to join."""
    outcome = await flow.to_active()
    with pytest.raises(SecondDeviceRejectedError):
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            device=DeviceDescriptor(fingerprint="device-b"),
        )

    sessions = await container.repositories.sessions.list_for_attempt(
        outcome.formal_attempt.formal_attempt_id
    )
    states = [session.state for session in sessions]
    assert states.count(DeviceSessionState.ACTIVE) == 1
    assert states.count(DeviceSessionState.REJECTED) == 1

    rejected = next(s for s in sessions if s.state is DeviceSessionState.REJECTED)
    assert rejected.device.fingerprint == "device-b"
    assert rejected.session_token == "", "a refused device is never given a credential"
    assert rejected.superseded_by_session_id == outcome.session.session_id

    record = await flow.record()
    assert [item.code.value for item in record.anomalies] == ["SECOND_DEVICE_ATTEMPTED"]


async def test_a_second_device_does_not_create_a_second_upstream_attempt(flow: FormalFlow, container, upstream):
    """The session claim happens before the attempt is created, which is why this holds."""
    await flow.to_active()
    with pytest.raises(SecondDeviceRejectedError):
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            device=DeviceDescriptor(fingerprint="device-b"),
        )
    assert len(upstream.attempts) == 1
    assert len(upstream.created) == 1


async def test_two_simultaneous_starts_produce_one_active_session(flow: FormalFlow, container, upstream):
    """§20: the device registration race. One insert wins; the other is refused."""
    await flow.acknowledge()
    await flow.confirm_identity()

    async def attempt_start(fingerprint: str):
        try:
            return await container.services.attempts.start(
                learner_id=DEFAULT_LEARNER,
                quiz_id=DEFAULT_QUIZ,
                device=DeviceDescriptor(fingerprint=fingerprint),
            )
        except Exception as error:  # noqa: BLE001 - the loser's refusal is the point
            return error

    results = await asyncio.gather(attempt_start("device-a"), attempt_start("device-b"))
    winners = [item for item in results if not isinstance(item, Exception)]
    losers = [item for item in results if isinstance(item, Exception)]

    assert len(winners) == 1
    assert len(losers) == 1
    assert isinstance(losers[0], (SecondDeviceRejectedError, FormalAttemptAlreadyStartedError))
    assert len(upstream.attempts) == 1

    active = await container.repositories.sessions.get_active(
        winners[0].formal_attempt.formal_attempt_id
    )
    assert active is not None


async def test_a_retry_with_the_same_client_request_id_replays_the_session(flow: FormalFlow, container):
    """A timeout must not be punished as a second device, and must not hand out a second lock."""
    await flow.acknowledge()
    await flow.confirm_identity()
    first = await flow.start(client_request_id=CLIENT_REQUEST_ID)
    second = await container.services.attempts.start(
        learner_id=DEFAULT_LEARNER,
        quiz_id=DEFAULT_QUIZ,
        device=DeviceDescriptor(fingerprint="device-a"),
        client_request_id=CLIENT_REQUEST_ID,
    )
    assert second.replayed is True
    assert second.session.session_id == first.session.session_id
    assert second.session.session_token == first.session.session_token
    assert second.formal_attempt.attempt_id == first.formal_attempt.attempt_id


async def test_a_second_device_supplying_a_different_token_is_still_rejected(flow: FormalFlow, container):
    await flow.to_active()
    with pytest.raises(SecondDeviceRejectedError):
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            device=DeviceDescriptor(fingerprint="device-b"),
            client_request_id="a-different-client-request-token",
        )


async def test_a_short_client_request_id_is_not_trusted_as_a_replay_token(flow: FormalFlow, container):
    """A guessable token would let a second device replay someone else's session."""
    await flow.acknowledge()
    await flow.confirm_identity()
    await flow.start(client_request_id="short")
    with pytest.raises(SecondDeviceRejectedError):
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            device=DeviceDescriptor(fingerprint="device-b"),
            client_request_id="short",
        )


async def test_autosave_requires_the_session_token(flow: FormalFlow, container):
    await flow.to_active()
    with pytest.raises(DeviceSessionConflictError) as error:
        await container.services.attempts.autosave(
            learner_id=DEFAULT_LEARNER,
            formal_attempt_id=flow.formal_attempt_id,
            session_token=None,
            answers=(AnswerSubmission(question_id="q1", response={"selectedOptionId": "q1-o1"}),),
        )
    assert error.value.code == "DEVICE_SESSION_CONFLICT"


async def test_a_wrong_session_token_is_refused_and_recorded(flow: FormalFlow, container, audit):
    """Being the right learner is not the same as being the device sitting the assessment."""
    await flow.to_active()
    with pytest.raises(DeviceSessionConflictError):
        await container.services.attempts.autosave(
            learner_id=DEFAULT_LEARNER,
            formal_attempt_id=flow.formal_attempt_id,
            session_token="not-the-real-token",
            answers=(AnswerSubmission(question_id="q1", response={"selectedOptionId": "q1-o1"}),),
        )
    assert "SECOND_DEVICE_REJECTED" in audit.codes()
    record = await flow.record()
    assert any(item.code.value == "SECOND_DEVICE_ATTEMPTED" for item in record.anomalies)


async def test_the_holder_can_autosave_and_the_heartbeat_is_recorded(flow: FormalFlow, container, upstream, clock):
    await flow.to_active()
    clock.advance(seconds=30)
    result = await container.services.attempts.autosave(
        learner_id=DEFAULT_LEARNER,
        formal_attempt_id=flow.formal_attempt_id,
        session_token=flow.session_token,
        answers=(
            AnswerSubmission(question_id="q1", response={"selectedOptionId": "q1-o1"}),
            AnswerSubmission(question_id="q2", response={"selectedOptionId": "q2-o1"}),
        ),
    )
    assert result.saved_count == 2
    assert result.answered_questions == 2
    session = await container.repositories.sessions.get_active(flow.formal_attempt_id)
    assert session is not None
    assert session.last_seen_at == to_iso(clock.now())


async def test_the_session_closes_when_the_attempt_is_submitted(flow: FormalFlow, container):
    await flow.to_active()
    await flow.submit()
    assert await container.repositories.sessions.get_active(flow.formal_attempt_id) is None
    sessions = await container.repositories.sessions.list_for_attempt(flow.formal_attempt_id)
    assert sessions[0].state is DeviceSessionState.CLOSED
    assert sessions[0].closed_reason == "SUBMITTED"


async def test_a_closed_session_cannot_be_used_again(flow: FormalFlow, container):
    """No resume: a session that ended is not a way back into the assessment."""
    await flow.to_active()
    token = flow.session_token
    await flow.submit()
    with pytest.raises(Exception) as error:
        await container.services.attempts.autosave(
            learner_id=DEFAULT_LEARNER,
            formal_attempt_id=flow.formal_attempt_id,
            session_token=token,
            answers=(AnswerSubmission(question_id="q3", response={"selectedOptionId": "q3-o1"}),),
        )
    assert error.value.code == "FORMAL_ATTEMPT_ALREADY_SUBMITTED"


async def test_a_retry_after_the_session_ended_is_not_given_a_new_lock(flow: FormalFlow, container):
    await flow.acknowledge()
    await flow.confirm_identity()
    await flow.start(client_request_id=CLIENT_REQUEST_ID)
    await flow.submit()

    # The same client retrying its registration after the attempt is over.
    with pytest.raises(Exception) as error:
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            device=DeviceDescriptor(fingerprint="device-a"),
            client_request_id=CLIENT_REQUEST_ID,
        )
    assert error.value.code in {"CONDITIONS_NOT_ACKNOWLEDGED", "DEVICE_SESSION_CONFLICT"}


async def test_a_learner_cannot_start_a_formal_attempt_without_confirming_identity(flow: FormalFlow, container):
    await flow.acknowledge()
    with pytest.raises(Exception) as error:
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, device=DeviceDescriptor()
        )
    assert error.value.code == "IDENTITY_NOT_CONFIRMED"


async def test_a_learner_cannot_start_without_acknowledging_the_conditions(container):
    with pytest.raises(Exception) as error:
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, device=DeviceDescriptor()
        )
    assert error.value.code == "CONDITIONS_NOT_ACKNOWLEDGED"
    assert error.value.status_code == 409


async def test_a_failed_attempt_creation_releases_the_lock(flow: FormalFlow, container, upstream):
    """Nothing was delivered, so the learner must be able to try again — from any device."""
    await flow.acknowledge()
    await flow.confirm_identity()
    upstream.fail_create = RuntimeError("UC-03 unavailable")

    with pytest.raises(RuntimeError):
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, device=DeviceDescriptor()
        )

    record = await container.services.attempts.find_open(DEFAULT_LEARNER, DEFAULT_QUIZ)
    assert record is not None
    assert record.state is FormalAttemptState.IDENTITY_CONFIRMED
    assert await container.repositories.sessions.get_active(record.formal_attempt_id) is None

    upstream.fail_create = None
    retried = await flow.start(fingerprint="device-b")
    assert retried.formal_attempt.state is FormalAttemptState.ACTIVE


async def test_an_existing_upstream_attempt_blocks_a_formal_start(flow: FormalFlow, container, upstream):
    """UC-03 permits one open attempt per quiz; UC-09 reports that before writing anything."""
    await flow.acknowledge()
    await flow.confirm_identity()
    from app.modules.formal_assessment.integration.uc03 import CreateAttemptRequest

    await upstream.create_attempt(
        CreateAttemptRequest(
            learner_id=DEFAULT_LEARNER, course_id="course-1", quiz_id=DEFAULT_QUIZ
        )
    )
    with pytest.raises(FormalAttemptAlreadyStartedError):
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER, quiz_id=DEFAULT_QUIZ, device=DeviceDescriptor()
        )
