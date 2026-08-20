"""The remaining failure modes §17 lists, and the degradation rules.

Two themes:

* **a guard at the boundary** — an incoherent UC-01 configuration is refused before a reservation
  exists, so a learner can never hold a slot for an attempt that could not be delivered;
* **degrade, don't fail** — an unreadable *historical* record costs variety or a display field, but
  never the retake itself, whereas an unreadable *previous paper* does stop it, because without it
  there is nothing to make the retake different from.
"""

from __future__ import annotations

import pytest

from app.core.errors import ProviderUnavailableError
from app.core.time import to_iso
from app.modules.retakes.domain.configuration import validate_configuration_for_retake
from app.modules.retakes.domain.errors import (
    ConfigurationUnavailableError,
    InvalidConfigurationError,
    PreviousAttemptNotRetakeableError,
    QuestionBankUnavailableError,
    RetakeInProgressError,
)
from app.modules.retakes.domain.idempotency import grant_key, retake_key
from app.modules.retakes.integration.uc01 import QuestionTypeQuota, QuizConfigurationVersion
from app.modules.retakes.integration.uc03 import AttemptStatus

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# The UC-01 boundary guard
# ---------------------------------------------------------------------------


def _config(**overrides) -> QuizConfigurationVersion:
    base = {
        "configuration_version_id": "cfg-v1",
        "quiz_id": "quiz-1",
        "course_id": "course-1",
        "version": 1,
        "question_count": 3,
        "maximum_attempts": 2,
    }
    return QuizConfigurationVersion(**{**base, **overrides})


def test_a_coherent_configuration_passes():
    validate_configuration_for_retake(_config())
    validate_configuration_for_retake(
        _config(
            question_count=3,
            question_type_quotas=(
                QuestionTypeQuota(type="SINGLE_CHOICE", count=2),
                QuestionTypeQuota(type="TRUE_FALSE", count=1),
            ),
        )
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"configuration_version_id": ""},
        {"quiz_id": ""},
        {"course_id": ""},
        {"question_count": 0},
        {"question_count": -2},
        {"question_count": "three"},
    ],
)
def test_an_undeliverable_configuration_is_refused(overrides):
    with pytest.raises(InvalidConfigurationError):
        validate_configuration_for_retake(_config(**overrides))


def test_duplicate_quotas_are_refused():
    with pytest.raises(InvalidConfigurationError):
        validate_configuration_for_retake(
            _config(
                question_count=2,
                question_type_quotas=(
                    QuestionTypeQuota(type="TRUE_FALSE", count=1),
                    QuestionTypeQuota(type="TRUE_FALSE", count=1),
                ),
            )
        )


def test_a_negative_quota_is_refused():
    with pytest.raises(InvalidConfigurationError):
        validate_configuration_for_retake(
            _config(question_count=1, question_type_quotas=(
                QuestionTypeQuota(type="TRUE_FALSE", count=-1),
            ))
        )


def test_quotas_that_do_not_sum_to_the_question_count_are_refused():
    """UC-01's own rule. Checked because a mismatch makes "the configured count" ambiguous."""
    with pytest.raises(InvalidConfigurationError) as raised:
        validate_configuration_for_retake(
            _config(
                question_count=5,
                question_type_quotas=(QuestionTypeQuota(type="TRUE_FALSE", count=2),),
            )
        )
    assert raised.value.context["quota_total"] == 2


async def test_an_incoherent_configuration_refuses_before_reserving(
    container, quiz, attempts, configurations
):
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)
    # An active version whose quotas do not add up to its question count.
    configurations.publish(
        configuration_version_id="cfg-broken",
        version=2,
        question_count=3,
        maximum_attempts=3,
        quotas=(("SINGLE_CHOICE", 1),),
    )

    with pytest.raises(InvalidConfigurationError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert await container.repositories.retakes.count_active_reservations(
        "learner-alice", "quiz-1"
    ) == 0


async def test_no_active_configuration_refuses_the_retake(
    container, first_attempt, configurations
):
    configurations.deactivate()
    with pytest.raises(ConfigurationUnavailableError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")
    assert raised.value.retryable is True


# ---------------------------------------------------------------------------
# Idempotency key construction
# ---------------------------------------------------------------------------


def test_a_retake_key_needs_every_component():
    assert retake_key("learner-alice", "quiz-1", "attempt-1") == (
        "retake:learner-alice:quiz-1:attempt-1"
    )
    with pytest.raises(ValueError):
        retake_key("learner-alice", "quiz-1", "   ")


def test_an_oversized_grant_key_is_refused():
    """Bounded so a caller-supplied token cannot become an unbounded database key."""
    with pytest.raises(ValueError):
        grant_key("learner-alice", "quiz-1", "x" * 200)


# ---------------------------------------------------------------------------
# In-flight and non-retakeable states
# ---------------------------------------------------------------------------


async def test_a_reserved_retake_for_a_different_attempt_blocks_a_new_one(
    container, configurations, attempts, bank, clock
):
    """Two different previous attempts, one in-flight reservation: the second waits.

    Reaching this through ``_raise_blocker`` rather than through the idempotency key, because a
    learner should not be handed a second concurrent retake by naming a different attempt.
    """
    from app.modules.retakes.domain.enums import ConfigurationVersionSource, RetakeRequestStatus
    from app.modules.retakes.domain.requests import RetakeRequest

    configurations.publish(question_count=3, maximum_attempts=4)
    bank.add_many(12)
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)

    now = to_iso(clock.now())
    await container.repositories.retakes.reserve(
        RetakeRequest(
            retake_id="retake-other",
            idempotency_key="retake:learner-alice:quiz-1:attempt-elsewhere",
            learner_id="learner-alice",
            course_id="course-1",
            quiz_id="quiz-1",
            previous_attempt_id="attempt-elsewhere",
            attempt_number=3,
            configuration_version_id="cfg-v1",
            configuration_version_source=ConfigurationVersionSource.CARRIED_FORWARD,
            status=RetakeRequestStatus.RESERVED,
            requested_at=now,
            updated_at=now,
        )
    )

    with pytest.raises(RetakeInProgressError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")
    assert raised.value.context["retake_id"] == "retake-other"


async def test_naming_an_unsubmitted_attempt_is_refused(container, quiz, attempts):
    active = attempts.start_attempt(question_ids=("q1", "q2", "q3"))

    with pytest.raises(PreviousAttemptNotRetakeableError) as raised:
        await container.services.retakes.create(
            learner_id="learner-alice", quiz_id="quiz-1", previous_attempt_id=active.attempt_id
        )
    assert raised.value.context["status"] == AttemptStatus.ACTIVE.value


# ---------------------------------------------------------------------------
# Degrade, don't fail
# ---------------------------------------------------------------------------


async def test_an_unreadable_previous_paper_stops_the_retake(container, first_attempt, attempts):
    """Without the paper being retaken there is nothing to make the new one different from."""
    attempts.unreadable_question_ids.add(first_attempt.attempt_id)

    with pytest.raises(ProviderUnavailableError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    assert await container.repositories.retakes.count_active_reservations(
        "learner-alice", "quiz-1"
    ) == 0


async def test_an_unreadable_older_paper_only_narrows_the_exclusion(
    container, configurations, attempts, bank
):
    """A record from three attempts ago that cannot be read costs variety, not the retake."""
    configurations.publish(question_count=3, maximum_attempts=4)
    bank.add_many(12)
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)
    second = attempts.start_attempt(question_ids=("q4", "q5", "q6"))
    attempts.submit(second.attempt_id)
    attempts.unreadable_question_ids.add(first.attempt_id)

    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1", previous_attempt_id=second.attempt_id
    )

    # The paper being retaken is still fully avoided.
    assert set(outcome.attempt.delivered_question_ids).isdisjoint({"q4", "q5", "q6"})
    assert outcome.retake.status.value == "COMPLETED"


async def test_a_question_bank_failure_is_surfaced_not_read_as_no_alternatives(
    container, first_attempt, bank
):
    """An empty pool and an unavailable one mean opposite things (§8 vs §17)."""
    bank.failure = ProviderUnavailableError("UC-02 is restarting.")

    with pytest.raises(ProviderUnavailableError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")


async def test_a_non_apperror_bank_failure_becomes_a_controlled_refusal(
    container, first_attempt, bank
):
    bank.failure = RuntimeError("socket closed by peer at 10.0.0.7:5432")

    with pytest.raises(QuestionBankUnavailableError) as raised:
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    # The adapter's message does not become the client's message.
    assert "10.0.0.7" not in raised.value.message


async def test_history_reads_are_bounded(container, configurations, attempts, bank):
    """The lookback bound exists so a learner on their fortieth attempt is not an unbounded fan-out.

    The paper being retaken is always read regardless, so the §7 guarantee is unaffected by it.
    """
    from app.modules.retakes.services.question_plan_service import RetakeQuestionPlanService

    configurations.publish(question_count=2, maximum_attempts=20)
    bank.add_many(30)
    for index in range(6):
        attempt = attempts.start_attempt(
            question_ids=(f"q{index * 2 + 1}", f"q{index * 2 + 2}")
        )
        attempts.submit(attempt.attempt_id)
    latest = (await attempts.list_attempts("learner-alice", "quiz-1"))[-1]
    latest_ids = await attempts.get_delivered_question_ids(latest.attempt_id)

    plans = RetakeQuestionPlanService(
        attempts=attempts, question_bank=bank, history_lookback=2
    )
    planned = await plans.build(
        config=configurations.versions["cfg-v1"],
        course_id="course-1",
        previous_attempt=latest,
        attempts=await attempts.list_attempts("learner-alice", "quiz-1"),
    )

    # Two attempts' worth of history were consulted, not six.
    assert len(planned.plan.excluded_question_ids) == 4
    # And the paper being retaken is among them regardless of the bound.
    assert set(latest_ids) <= set(planned.plan.excluded_question_ids)
