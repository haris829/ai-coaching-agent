"""The end-to-end scenario (§22), over HTTP, in the order §22 gives.

Twenty steps, from configuring a quiz to proving that granting an extra attempt did not change the
course-wide maximum. It runs against the real application through the real API, with the
neighbouring use cases standing in as the doubles described in ``tests/fakes.py``.

The scenario is a single test on purpose. Its value is in the *sequence* — that step 20 still holds
after everything steps 1 to 19 did — and splitting it into twenty independent tests would lose
exactly that.
"""

from __future__ import annotations

import pytest

from app.modules.retakes.integration.downstream import PassFailStatus
from tests.retakes.world import learner_auth_headers

pytestmark = pytest.mark.anyio

API = "/api/v1"
ADMIN = "/api/admin/retakes"
LEARNER = "learner-alice"
QUIZ = "quiz-1"


def _eligibility() -> str:
    return f"{API}/quizzes/{QUIZ}/retake-eligibility"


def _retakes() -> str:
    return f"{API}/quizzes/{QUIZ}/retakes"


def _history() -> str:
    return f"{API}/quizzes/{QUIZ}/attempt-history"


async def test_the_full_retake_journey(
    client,
    configurations,
    bank,
    attempts,
    scores,
    results,
    feedback,
    learner_headers,
    admin_headers,
):
    # -- 1. Configure the quiz: three questions, a maximum of two attempts. --------
    configurations.publish(
        configuration_version_id="cfg-v1", version=1, question_count=3, maximum_attempts=2
    )

    # -- 2. Stock the question bank, with one question retired. -------------------
    bank.add_many(9)
    bank.add("q10", retired=True)

    # -- 3/4/5. The learner's first attempt, completed and submitted (UC-03). -----
    first = attempts.start_attempt(question_ids=("q1", "q2", "q3"))
    attempts.submit(first.attempt_id)

    # -- 6. The result exists (UC-04 scored it, UC-05 decided it). ----------------
    scores.record(first.attempt_id, total=1.0, maximum=3.0, percentage=33.3)
    results.record(first.attempt_id, status=PassFailStatus.FAILED, pass_mark=80.0)
    feedback.available.add(first.attempt_id)

    # -- 7. Remaining attempts, from authoritative backend data. -----------------
    eligibility = (await client.get(_eligibility(), headers=learner_headers)).json()
    assert eligibility["state"] == "ELIGIBLE"
    assert eligibility["allowance"]["maximum_attempts"] == 2
    assert eligibility["allowance"]["attempts_used"] == 1
    assert eligibility["allowance"]["available_attempts"] == 1

    # -- 8. Request the retake. ---------------------------------------------------
    created = await client.post(_retakes(), headers=learner_headers, json={})
    assert created.status_code == 201
    retake = created.json()

    # -- 9. A new, independent attempt. ------------------------------------------
    second_id = retake["attempt"]["attempt_id"]
    assert second_id != first.attempt_id
    assert retake["attempt"]["attempt_number"] == 2
    assert retake["attempt"]["status"] == "ACTIVE"
    assert retake["retake"]["previous_attempt_id"] == first.attempt_id

    # -- 10. The applicable configuration version. -------------------------------
    assert retake["attempt"]["configuration_version_id"] == "cfg-v1"
    assert retake["retake"]["configuration_version_source"] == "CARRIED_FORWARD"

    # -- 11. A fresh question set, with the retired question never offered. ------
    delivered = set(retake["attempt"]["delivered_question_ids"])
    assert len(delivered) == 3
    assert delivered.isdisjoint({"q1", "q2", "q3"})
    assert "q10" not in delivered
    assert retake["question_set_difference"]["new_question_count"] == 3
    assert retake["question_set_difference"]["satisfied"] is True

    # -- 12. The previous attempt is untouched. ----------------------------------
    unchanged = await attempts.get_attempt(first.attempt_id)
    assert unchanged.status.value == "SUBMITTED"
    assert unchanged.attempt_number == 1
    assert unchanged.configuration_version_id == "cfg-v1"
    assert await attempts.get_delivered_question_ids(first.attempt_id) == ("q1", "q2", "q3")
    history_now = (await client.get(_history(), headers=learner_headers)).json()
    assert history_now["entries"][0]["percentage"] == 33.3
    assert history_now["entries"][0]["pass_fail_status"] == "FAILED"

    # -- 13. Complete the retake. ------------------------------------------------
    attempts.submit(second_id, submitted_at="2026-01-02T11:00:00.000Z")
    scores.record(second_id, total=3.0, maximum=3.0, percentage=100.0)
    results.record(second_id, status=PassFailStatus.PASSED, pass_mark=80.0)

    # -- 14. Attempt history: both attempts, with the relationship in both ways. --
    history = (await client.get(_history(), headers=learner_headers)).json()
    assert history["attempt_count"] == 2
    original, retake_entry = history["entries"]
    assert original["attempt_number"] == 1
    assert original["percentage"] == 33.3
    assert original["pass_fail_status"] == "FAILED"
    assert original["feedback_available"] is True
    assert original["retaken_by_attempt_id"] == second_id
    assert retake_entry["attempt_number"] == 2
    assert retake_entry["percentage"] == 100.0
    assert retake_entry["pass_fail_status"] == "PASSED"
    assert retake_entry["is_retake"] is True
    assert retake_entry["retake_of_attempt_id"] == first.attempt_id

    # -- 15/16. Attempts are exhausted, and another retake is rejected. ----------
    exhausted = (await client.get(_eligibility(), headers=learner_headers)).json()
    assert exhausted["state"] == "EXHAUSTED"
    assert exhausted["can_retake"] is False
    assert "administrator" in exhausted["guidance"]

    refused = await client.post(_retakes(), headers=learner_headers, json={})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "MAX_ATTEMPTS_REACHED"
    # And nothing was created by the refusal.
    assert len(await attempts.list_attempts(LEARNER, QUIZ)) == 2

    # -- 17. An administrator grants one additional attempt. ---------------------
    granted = await client.post(
        f"{ADMIN}/grants",
        headers={**admin_headers, "Idempotency-Key": "ticket-2201"},
        json={
            "learner_id": LEARNER,
            "quiz_id": QUIZ,
            "additional_attempts": 1,
            "reason": "Assessment interrupted by a fire alarm.",
        },
    )
    assert granted.status_code == 201
    assert granted.json()["grant"]["granted_by"] == "admin-jo"

    # -- 18. The retake becomes available, and says why. -------------------------
    after_grant = (await client.get(_eligibility(), headers=learner_headers)).json()
    assert after_grant["state"] == "ADDITIONAL_ATTEMPT_AVAILABLE"
    assert after_grant["can_retake"] is True
    assert after_grant["allowance"]["granted_attempts"] == 1
    assert after_grant["allowance"]["total_entitlement"] == 3
    assert after_grant["allowance"]["available_attempts"] == 1

    # -- 19. Create the third attempt. -------------------------------------------
    third = await client.post(_retakes(), headers=learner_headers, json={})
    assert third.status_code == 201
    third_body = third.json()
    assert third_body["attempt"]["attempt_number"] == 3
    assert third_body["retake"]["previous_attempt_id"] == second_id
    # Still avoiding everything the learner has seen: six unused questions remain.
    seen = {"q1", "q2", "q3"} | delivered
    assert set(third_body["attempt"]["delivered_question_ids"]).isdisjoint(seen)

    # -- 20. The course-wide configuration was never modified. -------------------
    assert configurations.versions["cfg-v1"].maximum_attempts == 2
    listing = (
        await client.get(
            f"{ADMIN}/learners/{LEARNER}/quizzes/{QUIZ}/grants", headers=admin_headers
        )
    ).json()
    assert listing["configured_maximum_attempts"] == 2
    assert listing["granted_attempts"] == 1

    # And no other learner gained anything from it.
    bob = attempts.start_attempt(learner_id="learner-bob", question_ids=("q1", "q2", "q3"))
    attempts.submit(bob.attempt_id)
    bob_second = attempts.start_attempt(learner_id="learner-bob", question_ids=("q4", "q5", "q6"))
    attempts.submit(bob_second.attempt_id)
    bob_eligibility = (
        await client.get(
            _eligibility(),
            headers=learner_auth_headers("learner-bob"),
        )
    ).json()
    assert bob_eligibility["state"] == "EXHAUSTED"
    assert bob_eligibility["allowance"]["granted_attempts"] == 0
