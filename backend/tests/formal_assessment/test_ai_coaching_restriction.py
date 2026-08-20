"""The AI coaching restriction (§7, §19).

    ordinary quiz                  ->  Larry allowed
    formal assessment in progress  ->  Larry forbidden, for this learner, on any attempt

The case worth reading is ``test_coaching_on_an_older_attempt_is_blocked_during_a_formal_assessment``: a
per-attempt check would pass it, and it is the scenario that actually matters.
"""

from __future__ import annotations

import pytest

from app.modules.formal_assessment.domain.errors import AiCoachingForbiddenError
from tests.formal_assessment.conftest import FormalFlow
from tests.formal_assessment.fakes import DEFAULT_LEARNER

pytestmark = pytest.mark.anyio


async def test_coaching_is_allowed_when_no_formal_assessment_is_running(container):
    permission = await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)
    assert permission.allowed is True
    assert permission.reason is None


async def test_coaching_is_blocked_during_an_active_formal_assessment(flow: FormalFlow, container, audit):
    await flow.to_active()
    permission = await container.services.coaching.is_ai_coaching_allowed(
        learner_id=DEFAULT_LEARNER, attempt_id=flow.attempt_id
    )
    assert permission.allowed is False
    assert permission.reason is not None
    assert permission.reason.value == "FORMAL_ATTEMPT_IN_PROGRESS"
    assert permission.formal_attempt_id == flow.formal_attempt_id
    assert "AI_COACHING_BLOCKED" in audit.codes()


async def test_coaching_on_an_older_attempt_is_blocked_during_a_formal_assessment(flow: FormalFlow, container):
    """§7: the restriction is learner-scoped. This is the case a per-attempt check would let through."""
    await flow.to_active()
    permission = await container.services.coaching.is_ai_coaching_allowed(
        learner_id=DEFAULT_LEARNER, attempt_id="attempt-from-last-month"
    )
    assert permission.allowed is False
    assert permission.reason is not None
    assert permission.reason.value == "FORMAL_ASSESSMENT_IN_PROGRESS"
    assert permission.formal_attempt_id == flow.formal_attempt_id


async def test_coaching_is_blocked_with_no_attempt_named_at_all(flow: FormalFlow, container):
    await flow.to_active()
    permission = await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)
    assert permission.allowed is False


async def test_another_learner_is_unaffected(flow: FormalFlow, container):
    await flow.to_active()
    permission = await container.services.coaching.is_ai_coaching_allowed(learner_id="learner-bob")
    assert permission.allowed is True


async def test_coaching_is_allowed_before_the_assessment_starts(flow: FormalFlow, container):
    """Acknowledging the conditions is not sitting the assessment; blocking then would be a rule nobody asked for."""
    await flow.acknowledge()
    assert (await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)).allowed is True
    await flow.confirm_identity()
    assert (await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)).allowed is True


async def test_the_restriction_lifts_when_the_attempt_is_submitted(flow: FormalFlow, container):
    await flow.to_active()
    assert (await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)).allowed is False
    await flow.submit()
    assert (await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)).allowed is True


async def test_the_restriction_lifts_after_an_auto_submission(flow: FormalFlow, container):
    await flow.to_active()
    await container.services.attempts.handle_disconnect_by_id(
        formal_attempt_id=flow.formal_attempt_id, reported_by="SYSTEM:monitor"
    )
    assert (await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)).allowed is True


async def test_requiring_permission_raises_for_a_direct_caller(flow: FormalFlow, container):
    """§7, §19: the restriction must hold even if a client calls the coaching API directly."""
    await flow.to_active()
    with pytest.raises(AiCoachingForbiddenError) as error:
        await container.services.coaching.require_allowed(
            learner_id=DEFAULT_LEARNER, attempt_id=flow.attempt_id
        )
    assert error.value.code == "AI_COACHING_FORBIDDEN"
    assert error.value.status_code == 403
    assert error.value.context["formal_attempt_id"] == flow.formal_attempt_id


async def test_requiring_permission_passes_when_nothing_is_running(container):
    permission = await container.services.coaching.require_allowed(learner_id=DEFAULT_LEARNER)
    assert permission.allowed is True


async def test_a_blocked_request_is_flagged_on_the_assessment_for_the_assessor(flow: FormalFlow, container):
    """§10: a learner who tried to open Larry mid-assessment is something the approver should see."""
    await flow.to_active()
    await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)
    record = await flow.record()
    codes = [item.code.value for item in record.anomalies]
    assert "AI_COACHING_ATTEMPTED" in codes


async def test_repeated_attempts_are_counted_on_one_flag(flow: FormalFlow, container):
    await flow.to_active()
    for _ in range(3):
        await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)
    record = await flow.record()
    flag = next(item for item in record.anomalies if item.code.value == "AI_COACHING_ATTEMPTED")
    assert flag.occurrences == 3


async def test_the_check_reads_persisted_state_not_the_request(flow: FormalFlow, container):
    """Nothing a caller passes can change the verdict — only the stored formal attempt can."""
    await flow.to_active()
    for attempt_id in (None, "", flow.attempt_id, "attempt-someone-elses", "../../etc/passwd"):
        permission = await container.services.coaching.is_ai_coaching_allowed(
            learner_id=DEFAULT_LEARNER, attempt_id=attempt_id
        )
        assert permission.allowed is False, attempt_id


async def test_a_second_formal_assessment_on_another_quiz_also_blocks(flow: FormalFlow, container, policies):
    """A learner enrolled on two courses cannot slip through a check that only looked at one."""
    policies.publish("quiz-formal-2", course_id="course-2")
    await flow.to_active()
    await flow.submit()
    assert (await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)).allowed is True

    second = FormalFlow(container=container, quiz_id="quiz-formal-2")
    await second.to_active()
    assert (await container.services.coaching.is_ai_coaching_allowed(learner_id=DEFAULT_LEARNER)).allowed is False
