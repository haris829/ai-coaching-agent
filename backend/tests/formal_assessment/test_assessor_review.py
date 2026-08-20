"""Assessor review and the decisions (§10, §19, §20).

Two themes: an assessor sees everything §10 asks for, and only an authorised assessor can do anything at all.
The authorisation tests matter more than they look — a valid token is not authorisation, and the check has to be
performed on every operation rather than once at the start of a session.
"""

from __future__ import annotations

import asyncio

import pytest

from app.modules.formal_assessment.domain.enums import FormalAttemptState, ReviewState
from app.modules.formal_assessment.domain.errors import (
    AssessorNotAuthorizedError,
    InvalidReviewDecisionError,
    ReviewAlreadyDecidedError,
)
from app.modules.formal_assessment.integration.uc03 import AnswerSubmission
from tests.formal_assessment.conftest import FormalFlow
from tests.formal_assessment.fakes import (
    DEFAULT_ASSESSOR,
    DEFAULT_COURSE,
    DEFAULT_LEARNER,
    DEFAULT_NAME,
)

pytestmark = pytest.mark.anyio


async def _passed(flow: FormalFlow, container, passing, *, answers: int = 3):
    await flow.to_active()
    await container.services.attempts.autosave(
        learner_id=DEFAULT_LEARNER,
        formal_attempt_id=flow.formal_attempt_id,
        session_token=flow.session_token,
        answers=tuple(
            AnswerSubmission(question_id=f"q{index}", response={"selectedOptionId": f"q{index}-o1"})
            for index in range(1, answers + 1)
        ),
    )
    passing(flow.attempt_id)
    await flow.submit()
    review = await flow.review()
    assert review is not None
    return review


# ---------------------------------------------------------------------------
# The queue (§9, §10)
# ---------------------------------------------------------------------------


async def test_a_pending_review_appears_in_the_assessors_queue(flow: FormalFlow, container, passing):
    review = await _passed(flow, container, passing)
    page = await container.services.reviews.list_pending(assessor_id=DEFAULT_ASSESSOR)
    assert [item.review_id for item in page.reviews] == [review.review_id]
    assert page.total_pending == 1


async def test_the_queue_is_scoped_to_the_assessors_courses(flow: FormalFlow, container, passing, assessors):
    await _passed(flow, container, passing)
    assessors.add("assessor-other", courses=("course-other",))
    page = await container.services.reviews.list_pending(assessor_id="assessor-other")
    assert page.reviews == ()
    assert page.total_pending == 0


async def test_an_assessor_authorised_for_no_courses_sees_nothing(flow: FormalFlow, container, passing, assessors):
    """An empty scope must mean an empty queue, never the whole table."""
    await _passed(flow, container, passing)
    assessors.add("assessor-empty", courses=())
    page = await container.services.reviews.list_pending(assessor_id="assessor-empty")
    assert page.reviews == ()


async def test_a_platform_wide_assessor_sees_every_course(flow: FormalFlow, container, passing, assessors):
    await _passed(flow, container, passing)
    assessors.add("assessor-all", courses=(), all_courses=True)
    page = await container.services.reviews.list_pending(assessor_id="assessor-all")
    assert len(page.reviews) == 1
    assert page.course_ids is None


async def test_an_unknown_caller_is_not_an_assessor(flow: FormalFlow, container, passing):
    await _passed(flow, container, passing)
    with pytest.raises(AssessorNotAuthorizedError) as error:
        await container.services.reviews.list_pending(assessor_id="not-an-assessor")
    assert error.value.code == "ASSESSOR_NOT_AUTHORIZED"
    assert error.value.status_code == 403


async def test_a_deactivated_assessor_loses_access_immediately(flow: FormalFlow, container, passing, assessors):
    """Authorisation is re-checked on every operation, not cached from the start of a session."""
    review = await _passed(flow, container, passing)
    await container.services.reviews.list_pending(assessor_id=DEFAULT_ASSESSOR)
    assessors.deactivate()
    with pytest.raises(AssessorNotAuthorizedError):
        await container.services.reviews.get_detail(
            assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id
        )


# ---------------------------------------------------------------------------
# The review payload (§10)
# ---------------------------------------------------------------------------


async def test_the_review_payload_carries_everything_the_specification_asks_for(
    flow: FormalFlow, container, passing
):
    review = await _passed(flow, container, passing)
    detail = await container.services.reviews.get_detail(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id
    )

    assert detail.learner["learner_id"] == DEFAULT_LEARNER
    assert detail.learner["full_name"] == DEFAULT_NAME, "an assessor must see who sat the assessment"
    assert detail.assessment["quiz_id"] == flow.quiz_id
    assert detail.assessment["conditions"]["acknowledged"] is True
    assert detail.assessment["identity_confirmation"]["email_confirmed"] is True
    assert detail.score is not None and detail.score["percentage"] == 90.0
    assert len(detail.responses) == 3
    assert detail.attempt is not None and detail.attempt["status"] == "SUBMITTED"
    assert detail.submission["submission_reason"] == "LEARNER_CONFIRMED"
    assert detail.disconnect is None
    assert detail.supervision["device_sessions"]
    assert detail.supervision["rejected_device_count"] == 0


async def test_the_payload_surfaces_disconnect_and_anomaly_information(flow: FormalFlow, container, passing):
    await flow.to_active()
    passing(flow.attempt_id)
    await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id,
        reported_by="SYSTEM:monitor",
        reason="HEARTBEAT_TIMEOUT",
    )
    review = await flow.review()
    assert review is not None
    detail = await container.services.reviews.get_detail(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id
    )
    assert detail.disconnect is not None
    assert detail.disconnect["reported_by"] == "SYSTEM:monitor"
    assert detail.submission["auto_submitted"] is True
    codes = {item["code"] for item in detail.anomalies}
    assert "AUTO_SUBMITTED_AFTER_DISCONNECT" in codes


async def test_the_payload_shows_a_device_that_was_turned_away(flow: FormalFlow, container, passing):
    from app.modules.formal_assessment.domain.device import DeviceDescriptor
    from app.modules.formal_assessment.domain.errors import SecondDeviceRejectedError

    await flow.to_active()
    with pytest.raises(SecondDeviceRejectedError):
        await container.services.attempts.start(
            learner_id=DEFAULT_LEARNER,
            quiz_id=flow.quiz_id,
            device=DeviceDescriptor(fingerprint="device-b"),
        )
    passing(flow.attempt_id)
    await flow.submit()
    review = await flow.review()
    assert review is not None

    detail = await container.services.reviews.get_detail(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id
    )
    assert detail.supervision["rejected_device_count"] == 1
    assert {item["code"] for item in detail.anomalies} >= {"SECOND_DEVICE_ATTEMPTED"}


async def test_an_unreachable_profile_source_does_not_block_the_review(flow: FormalFlow, container, passing, profiles):
    review = await _passed(flow, container, passing)
    profiles.break_provider()
    detail = await container.services.reviews.get_detail(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id
    )
    assert detail.learner == {"learner_id": DEFAULT_LEARNER}
    assert detail.score is not None


# ---------------------------------------------------------------------------
# Starting and deciding (§10, §20)
# ---------------------------------------------------------------------------


async def test_starting_a_review_records_who_is_looking(flow: FormalFlow, container, passing, audit):
    review = await _passed(flow, container, passing)
    started = await container.services.reviews.start_review(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id
    )
    assert started.state is ReviewState.IN_REVIEW
    assert started.assigned_to == DEFAULT_ASSESSOR
    assert "ASSESSOR_REVIEW_STARTED" in audit.codes()


async def test_approving_moves_the_attempt_to_approved(flow: FormalFlow, container, passing, audit):
    review = await _passed(flow, container, passing)
    outcome = await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR,
        review_id=review.review_id,
        decision="APPROVED",
        notes="Identity and responses verified.",
    )
    assert outcome.review.state is ReviewState.APPROVED
    assert outcome.review.decision is not None
    assert outcome.review.decision.decided_by == DEFAULT_ASSESSOR
    assert outcome.review.decision.notes == "Identity and responses verified."
    assert outcome.formal_attempt.state is FormalAttemptState.CERTIFICATE_ALLOWED
    assert "ASSESSOR_APPROVED" in audit.codes()


async def test_referring_for_further_review_leaves_the_certificate_blocked(
    flow: FormalFlow, container, passing, audit, certificates
):
    review = await _passed(flow, container, passing)
    outcome = await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR,
        review_id=review.review_id,
        decision="REQUIRES_FURTHER_REVIEW",
        notes="Answers inconsistent with the response times.",
    )
    assert outcome.review.state is ReviewState.REQUIRES_FURTHER_REVIEW
    assert outcome.formal_attempt.state is FormalAttemptState.REQUIRES_FURTHER_REVIEW
    assert outcome.formal_attempt.certificate_allowed is False
    assert outcome.certificate is None
    assert certificates.certificate_count == 0
    assert "REQUIRES_FURTHER_REVIEW" in audit.codes()


async def test_an_escalated_assessment_can_never_be_approved_afterwards(flow: FormalFlow, container, passing):
    review = await _passed(flow, container, passing)
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR,
        review_id=review.review_id,
        decision="REQUIRES_FURTHER_REVIEW",
    )
    with pytest.raises(ReviewAlreadyDecidedError):
        await container.services.reviews.decide(
            assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="APPROVED"
        )
    record = await flow.record()
    assert record.state is FormalAttemptState.REQUIRES_FURTHER_REVIEW
    assert record.certificate_allowed is False


async def test_a_second_decision_is_refused_and_names_the_first_assessor(
    flow: FormalFlow, container, passing, assessors
):
    review = await _passed(flow, container, passing)
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="APPROVED"
    )
    assessors.add("assessor-second")
    with pytest.raises(ReviewAlreadyDecidedError) as error:
        await container.services.reviews.decide(
            assessor_id="assessor-second",
            review_id=review.review_id,
            decision="REQUIRES_FURTHER_REVIEW",
        )
    assert error.value.context["decided_by"] == DEFAULT_ASSESSOR
    assert error.value.status_code == 409


async def test_two_simultaneous_decisions_resolve_to_one(flow: FormalFlow, container, passing, assessors, certificates):
    """§20: the assessor decision race must not corrupt the review state."""
    review = await _passed(flow, container, passing)
    assessors.add("assessor-second")

    results = await asyncio.gather(
        container.services.reviews.decide(
            assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="APPROVED"
        ),
        container.services.reviews.decide(
            assessor_id="assessor-second",
            review_id=review.review_id,
            decision="REQUIRES_FURTHER_REVIEW",
        ),
        return_exceptions=True,
    )
    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ReviewAlreadyDecidedError)

    stored = await container.repositories.reviews.get(review.review_id)
    assert stored is not None
    assert stored.decision is not None
    assert stored.state in {ReviewState.APPROVED, ReviewState.REQUIRES_FURTHER_REVIEW}
    # And at most one certificate, whichever decision won.
    assert certificates.certificate_count <= 1


async def test_an_unrecognised_decision_is_refused(flow: FormalFlow, container, passing):
    review = await _passed(flow, container, passing)
    with pytest.raises(InvalidReviewDecisionError) as error:
        await container.services.reviews.decide(
            assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="LOOKS_FINE_TO_ME"
        )
    assert error.value.code == "INVALID_REVIEW_DECISION"
    assert error.value.status_code == 422
    assert sorted(error.value.context["allowed_decisions"]) == [
        "APPROVED",
        "REQUIRES_FURTHER_REVIEW",
    ]


async def test_an_unauthorised_assessor_cannot_decide(flow: FormalFlow, container, passing, assessors):
    review = await _passed(flow, container, passing)
    assessors.add("assessor-wrong-course", courses=("course-elsewhere",))
    with pytest.raises(AssessorNotAuthorizedError) as error:
        await container.services.reviews.decide(
            assessor_id="assessor-wrong-course", review_id=review.review_id, decision="APPROVED"
        )
    assert error.value.context["course_id"] == DEFAULT_COURSE
    record = await flow.record()
    assert record.state is FormalAttemptState.PENDING_REVIEW


async def test_a_decision_on_an_unknown_review_is_a_404(container, assessors):
    with pytest.raises(Exception) as error:
        await container.services.reviews.decide(
            assessor_id=DEFAULT_ASSESSOR, review_id="review-nope", decision="APPROVED"
        )
    assert error.value.status_code == 404


async def test_a_decided_review_leaves_the_queue(flow: FormalFlow, container, passing):
    review = await _passed(flow, container, passing)
    await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR, review_id=review.review_id, decision="APPROVED"
    )
    page = await container.services.reviews.list_pending(assessor_id=DEFAULT_ASSESSOR)
    assert page.reviews == ()
    assert page.total_pending == 0


async def test_notes_are_bounded_and_trimmed(flow: FormalFlow, container, passing):
    review = await _passed(flow, container, passing)
    outcome = await container.services.reviews.decide(
        assessor_id=DEFAULT_ASSESSOR,
        review_id=review.review_id,
        decision="APPROVED",
        notes="  " + "x" * 5000 + "  ",
    )
    assert outcome.review.decision is not None
    assert len(outcome.review.decision.notes or "") == 4000
