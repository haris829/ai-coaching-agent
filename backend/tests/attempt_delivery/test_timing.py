"""Server-authoritative timing, resynchronisation and time expiry.

The clock is fixed and advanced explicitly, so expiry behaviour is asserted exactly
rather than approximated by sleeping.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.time import FixedClock, parse_instant, to_iso
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.domain.enums import (
    AttemptStatus,
    SubmissionReason,
    SubmissionState,
)
from tests.support.client import ApiClient, assert_error, assert_ok
from tests.support.fixtures import QUIZ_ID, answer_for, seed_world


def _attempt(
    context: AppContext, api: ApiClient, *, time_limit: int | None = 600, count: int = 3
) -> tuple[str, list[dict]]:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"timeLimitSeconds": time_limit, "questionCount": count})
    attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    return attempt_id, assert_ok(api.questions(attempt_id))["questions"]


# ---------------------------------------------------------------------------
# Remaining time
# ---------------------------------------------------------------------------


def test_remaining_time_counts_down_with_the_server_clock(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, _ = _attempt(context, api, time_limit=600)

    timing = assert_ok(api.timing(attempt_id))["timing"]
    assert timing["timeLimitSeconds"] == 600
    assert timing["remainingSeconds"] == 600
    assert timing["elapsedSeconds"] == 0
    assert timing["expired"] is False
    assert timing["timed"] is True

    clock.advance(seconds=90)
    timing = assert_ok(api.timing(attempt_id))["timing"]
    assert timing["remainingSeconds"] == 510
    assert timing["elapsedSeconds"] == 90


def test_timing_exposes_everything_needed_to_resync(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, _ = _attempt(context, api, time_limit=1200)
    timing = assert_ok(api.timing(attempt_id))["timing"]

    # The four values a client needs to run its own countdown, plus the threshold at
    # which it must resync.
    assert parse_instant(timing["serverTime"]) == clock.now()
    assert timing["serverTimeEpochMs"] == int(clock.now().timestamp() * 1000)
    assert parse_instant(timing["startedAt"]) == clock.now()
    assert parse_instant(timing["expiresAt"]) == clock.now() + timedelta(seconds=1200)
    assert timing["timeLimitSeconds"] == 1200
    assert timing["remainingSeconds"] == 1200
    assert timing["clockResyncThresholdSeconds"] == 5
    assert timing["autosaveIntervalSeconds"] == 30


def test_reported_client_skew_is_advisory_only(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, _ = _attempt(context, api, time_limit=600)

    # A client whose clock is an hour fast.
    skewed = to_iso(clock.now() + timedelta(hours=1))
    timing = assert_ok(api.timing(attempt_id, client_time=skewed))["timing"]

    assert timing["reportedClientSkewSeconds"] == 3600
    # The skew exceeds the advertised threshold, so the client must resync...
    assert abs(timing["reportedClientSkewSeconds"]) > timing["clockResyncThresholdSeconds"]
    # ...and it has not bought a single extra second.
    assert timing["remainingSeconds"] == 600
    assert timing["expired"] is False


def test_a_backdated_client_clock_cannot_extend_the_attempt(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api, time_limit=60)

    # The learner sets their device clock back an hour and keeps working.
    backdated = to_iso(clock.now() - timedelta(hours=1))
    clock.advance(seconds=61)

    timing = assert_ok(api.timing(attempt_id, client_time=backdated))["timing"]
    assert timing["expired"] is True
    assert timing["remainingSeconds"] == 0

    # The write is refused on server time, regardless of what the client claims.
    assert_error(
        api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])),
        409,
        "ATTEMPT_ALREADY_SUBMITTED",
    )


def test_a_malformed_client_time_is_ignored(context: AppContext, api: ApiClient) -> None:
    attempt_id, _ = _attempt(context, api, time_limit=600)
    timing = assert_ok(api.timing(attempt_id, client_time="not-a-timestamp"))["timing"]
    assert "reportedClientSkewSeconds" not in timing
    assert timing["remainingSeconds"] == 600


def test_untimed_attempt_reports_no_deadline(context: AppContext, api: ApiClient, clock: FixedClock) -> None:
    attempt_id, questions = _attempt(context, api, time_limit=None)

    clock.advance(hours=48)
    timing = assert_ok(api.timing(attempt_id))["timing"]
    assert timing["timed"] is False
    assert timing["remainingSeconds"] is None
    assert timing["expiresAt"] is None
    assert timing["expired"] is False

    # Still writable two days later.
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expiry_submits_with_the_latest_saved_answers(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api, time_limit=300, count=3)

    # Two of three answered before time runs out.
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    clock.advance(seconds=30)
    assert_ok(api.save_answer(attempt_id, questions[1]["questionId"], answer_for(questions[1])))

    clock.advance(seconds=300)

    # The next access settles the expiry; no background job is needed.
    attempt = assert_ok(api.get_attempt(attempt_id))["attempt"]
    assert attempt["status"] == str(AttemptStatus.SUBMITTED)
    assert attempt["submissionReason"] == str(SubmissionReason.TIME_EXPIRED)

    submission = assert_ok(api.submission(attempt_id))
    assert submission["submission"]["state"] == str(SubmissionState.SUBMITTED)
    # Submitted with exactly what had been persisted.
    assert submission["submission"]["answeredCount"] == 2
    assert submission["submission"]["totalQuestions"] == 3


def test_expiry_records_the_deadline_as_the_submission_instant(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, _ = _attempt(context, api, time_limit=120)
    deadline = assert_ok(api.get_attempt(attempt_id))["attempt"]["expiresAt"]

    # Noticed long after the fact.
    clock.advance(hours=3)
    attempt = assert_ok(api.get_attempt(attempt_id))["attempt"]

    # The attempt ended at its deadline, not when the server happened to notice.
    assert attempt["submittedAt"] == deadline
    # Elapsed time is frozen at the limit rather than continuing to grow.
    assert attempt["timing"]["elapsedSeconds"] == 120
    assert attempt["timing"]["remainingSeconds"] == 0


def test_writes_are_rejected_after_expiry(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api, time_limit=60)
    clock.advance(seconds=61)

    # Answer, flag and cursor updates are all refused on an expired attempt.
    assert_error(
        api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])),
        409,
        "ATTEMPT_ALREADY_SUBMITTED",
    )
    assert_error(api.set_flag(attempt_id, questions[0]["questionId"], True), 409, "ATTEMPT_ALREADY_SUBMITTED")
    assert_error(api.set_cursor(attempt_id, 2), 409, "ATTEMPT_ALREADY_SUBMITTED")


def test_a_save_at_the_exact_deadline_is_rejected(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api, time_limit=60)

    # One second before the deadline: accepted.
    clock.advance(seconds=59)
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))

    # Exactly on the deadline: the attempt is over.
    clock.advance(seconds=1)
    assert_error(
        api.save_answer(attempt_id, questions[1]["questionId"], answer_for(questions[1])),
        409,
        "ATTEMPT_ALREADY_SUBMITTED",
    )


def test_reads_after_expiry_return_the_final_state_rather_than_an_error(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, questions = _attempt(context, api, time_limit=60)
    assert_ok(api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0])))
    clock.advance(seconds=120)

    # A learner reconnecting after the deadline sees the authoritative outcome.
    for response in (
        api.get_attempt(attempt_id),
        api.state(attempt_id),
        api.answers(attempt_id),
        api.flags(attempt_id),
        api.timing(attempt_id),
        api.questions(attempt_id),
        api.submission(attempt_id),
        api.preview_submission(attempt_id),
    ):
        assert response.status_code == 200, response.text

    assert assert_ok(api.answers(attempt_id))["answeredCount"] == 1


def test_expiry_is_settled_only_once(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    attempt_id, _ = _attempt(context, api, time_limit=60)
    clock.advance(seconds=61)

    # Many concurrent-ish accesses, each of which could try to settle the expiry.
    for _ in range(5):
        assert_ok(api.get_attempt(attempt_id))
        assert_ok(api.state(attempt_id))

    # A deterministic idempotency key collapses them into a single submission.
    submission = assert_ok(api.submission(attempt_id))
    assert len(submission["history"]) == 1
    assert submission["history"][0]["state"] == str(SubmissionState.SUBMITTED)


def test_expiry_during_a_grace_period_is_configurable(
    settings,
    clock: FixedClock,
    dispatcher,
    configurations,
    question_bank,
    enrolments,
) -> None:
    # An operator may allow a small window for an in-flight autosave to land after the deadline.
    # Everything except that one setting is wired exactly as the shared fixtures wire it.
    from tests.attempt_delivery.conftest import LEARNER_TOKEN, TOKENS, build_client, build_context
    from tests.support.client import ApiClient as Client
    from tests.support.fixtures import LEARNER_ID

    app_context = build_context(
        settings=settings.model_copy(update={"submission_grace_seconds": 10}),
        clock=clock,
        dispatcher=dispatcher,
        configurations=configurations,
        question_bank=question_bank,
        enrolments=enrolments,
    )
    try:
        with app_context.unit_of_work() as ctx:
            seed_world(ctx, rules={"timeLimitSeconds": 60, "questionCount": 2})

        with build_client(app_context) as raw:
            api = Client(raw, LEARNER_ID, LEARNER_TOKEN, tokens=TOKENS)
            attempt_id = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
            questions = assert_ok(api.questions(attempt_id))["questions"]

            # Five seconds past the deadline but inside the grace window.
            clock.advance(seconds=65)
            assert_ok(
                api.save_answer(attempt_id, questions[0]["questionId"], answer_for(questions[0]))
            )
            # The client is still told the deadline has passed.
            assert assert_ok(api.timing(attempt_id))["timing"]["expired"] is True

            # Past the grace window: refused.
            clock.advance(seconds=10)
            assert_error(
                api.save_answer(attempt_id, questions[1]["questionId"], answer_for(questions[1])),
                409,
                "ATTEMPT_ALREADY_SUBMITTED",
            )
    finally:
        app_context.dispose()


def test_expired_attempt_frees_the_allowance_for_a_new_attempt(
    context: AppContext, api: ApiClient, clock: FixedClock
) -> None:
    with context.unit_of_work() as ctx:
        seed_world(ctx, rules={"timeLimitSeconds": 60, "questionCount": 2, "maxAttempts": 3})

    first = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]["attemptId"]
    clock.advance(seconds=61)
    # Settle the expiry so the attempt is no longer open.
    assert_ok(api.get_attempt(first))

    second = assert_ok(api.create_attempt(QUIZ_ID), 201)["attempt"]
    assert second["attemptNumber"] == 2
