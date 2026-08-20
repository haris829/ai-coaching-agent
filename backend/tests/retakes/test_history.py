"""Attempt history (§9, §10).

The property under test is immutability: creating a retake appends to this list and changes
nothing already in it. The rest is assembly — that each fact comes from the module that owns it,
and that a fact nobody has produced is labelled rather than invented.
"""

from __future__ import annotations

import pytest

from app.modules.retakes.integration.downstream import PassFailStatus

pytestmark = pytest.mark.anyio


async def test_history_lists_attempts_oldest_first(container, quiz, attempts, scores, results):
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)
    scores.record(first.attempt_id, total=1.0, maximum=3.0, percentage=33.3)
    results.record(first.attempt_id, status=PassFailStatus.FAILED)

    history = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")

    assert history.attempt_count == 1
    entry = history.entries[0]
    assert entry.attempt_number == 1
    assert entry.attempt_id == first.attempt_id
    assert entry.configuration_version_id == "cfg-v1"
    assert entry.status == "SUBMITTED"
    assert entry.submitted_at == "2026-01-01T09:30:00.000Z"
    assert entry.total_questions == 3


async def test_upstream_facts_are_carried_through_untouched(
    container, quiz, attempts, scores, results, feedback, coaching
):
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)
    scores.record(attempt.attempt_id, total=2.0, maximum=3.0, percentage=66.7)
    results.record(attempt.attempt_id, status=PassFailStatus.FAILED, pass_mark=80.0)
    feedback.available.add(attempt.attempt_id)
    coaching.available[attempt.attempt_id] = 1

    entry = (
        await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    ).entries[0]

    # UC-04's numbers, exactly as UC-04 gave them — no recomputation anywhere in this module.
    assert entry.score_available is True
    assert (entry.total_marks, entry.maximum_marks, entry.percentage) == (2.0, 3.0, 66.7)
    # UC-05's decision, copied verbatim.
    assert entry.pass_fail_status == "FAILED"
    assert entry.pass_mark_percentage == 80.0
    assert entry.feedback_available is True
    assert entry.coaching_available is True


async def test_a_missing_score_is_labelled_not_invented(container, quiz, attempts):
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)

    entry = (
        await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    ).entries[0]

    assert entry.score_available is False
    assert entry.percentage is None  # not 0.0
    assert entry.pass_fail_available is False
    assert entry.pass_fail_status is None


async def test_an_unconfirmed_score_is_not_reported_as_available(container, quiz, attempts, scores):
    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)
    scores.record(attempt.attempt_id, total=2.0, maximum=3.0, percentage=66.7, confirmed=False)

    entry = (
        await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    ).entries[0]
    assert entry.score_available is False


async def test_one_failing_provider_degrades_one_field_not_the_listing(
    container, quiz, attempts, scores, results
):
    from app.core.errors import ProviderUnavailableError

    attempt = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(attempt.attempt_id)
    results.record(attempt.attempt_id, status=PassFailStatus.FAILED)
    scores.failure = ProviderUnavailableError("UC-04 is restarting.")

    history = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")

    assert history.attempt_count == 1
    assert history.entries[0].score_available is False
    # The rest of the entry is intact.
    assert history.entries[0].pass_fail_status == "FAILED"


# ---------------------------------------------------------------------------
# §3 / §9 — history is immutable across a retake
# ---------------------------------------------------------------------------


async def test_creating_a_retake_appends_and_changes_nothing(
    container, quiz, attempts, scores, results
):
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)
    scores.record(first.attempt_id, total=1.0, maximum=3.0, percentage=33.3)
    results.record(first.attempt_id, status=PassFailStatus.FAILED)

    before = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")
    after = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")

    assert before.attempt_count == 1
    assert after.attempt_count == 2
    # The first entry is byte-for-byte what it was, apart from the new backward link.
    original_before = before.entries[0].as_dict()
    original_after = after.entries[0].as_dict()
    linked = original_after.pop("retaken_by_attempt_id")
    original_before.pop("retaken_by_attempt_id")
    assert original_after == original_before
    assert linked == after.entries[1].attempt_id


async def test_the_retake_relationship_reads_in_both_directions(container, quiz, attempts):
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)
    outcome = await container.services.retakes.create(
        learner_id="learner-alice", quiz_id="quiz-1"
    )

    history = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    original, retake = history.entries

    assert original.is_retake is False
    assert original.retaken_by_attempt_id == retake.attempt_id
    assert retake.is_retake is True
    assert retake.retake_of_attempt_id == original.attempt_id
    assert retake.retake_id == outcome.retake.retake_id


async def test_each_attempt_keeps_its_own_configuration_version(
    container, quiz, attempts, configurations
):
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)
    configurations.publish(configuration_version_id="cfg-v2", version=2, maximum_attempts=3)

    await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    history = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    assert [entry.configuration_version_id for entry in history.entries] == ["cfg-v1", "cfg-v2"]
    assert [entry.configuration_version_number for entry in history.entries] == [1, 2]


async def test_a_failed_retake_creates_no_history_entry(container, quiz, attempts):
    from app.modules.retakes.domain.errors import AttemptCreationFailedError

    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)
    attempts.creation_failure = RuntimeError("boom")

    with pytest.raises(AttemptCreationFailedError):
        await container.services.retakes.create(learner_id="learner-alice", quiz_id="quiz-1")

    history = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    assert history.attempt_count == 1
    assert history.entries[0].retaken_by_attempt_id is None


async def test_history_is_scoped_to_one_learner_and_quiz(container, quiz, attempts):
    mine = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(mine.attempt_id)
    theirs = attempts.start_attempt(learner_id="learner-bob", question_ids=("q4", "q5", "q6"))
    attempts.submit(theirs.attempt_id)

    history = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    assert [entry.attempt_id for entry in history.entries] == [mine.attempt_id]
