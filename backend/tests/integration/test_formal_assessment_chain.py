"""UC-09 on the real chain, over HTTP, with every real adapter.

    configure formal (UC-01) -> conditions -> identity -> device lock (UC-09)
        -> attempt (UC-03) -> autosave -> submit
        -> score (UC-04) -> pass/fail (UC-05) -> PENDING_REVIEW (UC-09)
        -> assessor decision -> certificate released (UC-05)

``tests/formal_assessment/`` tests UC-09's rules against port fakes, for the reasons its conftest
sets out. This file tests the part those cannot: that the adapters onto UC-01, UC-03, UC-04 and
UC-05 line up with what those capabilities actually wrote, and that the two rules UC-09 imposes on
*existing* behaviour hold when the real UC-05 and UC-07 are the ones being restrained.

Nothing here is a double. Real rows, real transactions, real constraints.

WHAT THIS FILE IS REALLY CHECKING
---------------------------------
1. **The formal flag survives the trip.** UC-01 publishes it on an immutable version, UC-03 freezes
   it onto the attempt, and UC-09 reads the policy back from the version the attempt *locked* —
   not from whatever is active now.
2. **A passing formal assessment does not produce a certificate.** UC-05's real certificate flow
   runs, finds the gate closed, and leaves a PENDING row. This is the rule the whole use case
   exists for, and only a real chain can show it.
3. **An assessor's approval releases it.** Through UC-05's own service, with its own duplicate
   prevention — UC-09 does not create a certificate.
4. **A failing formal assessment never reaches a review at all**, and never a certificate.
5. **Coaching is refused while the exam runs** — on the learner's *other*, older, submitted,
   fully-coachable attempt, which is the case an attempt-scoped check would miss.
6. **A standard quiz is completely unaffected**: no formal record, gate has no opinion, certificate
   issues exactly as it did before UC-09 existed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.modules.question_bank.models import Question
from tests import bank
from tests.harness import ADMIN_TOKEN, ASSESSOR_TOKEN, LEARNER_TOKEN, Ctx, auth

#: Two single-choice questions, a pass mark of 50, so answering one correctly passes and answering
#: none fails. Kept small because this file is about the chain, not about scoring.
FORMAL_CONFIGURATION: dict[str, Any] = {
    "questionCount": 2,
    "timeLimitMinutes": 30,
    "passMark": 50,
    "questionTypes": [{"type": "SINGLE_CHOICE"}],
    "randomiseQuestions": False,
    "maxAttempts": 3,
    "deliveryMode": "assessment",
    "isFormalAssessment": True,
    "requiresHumanReview": True,
    "requiresAssessorApproval": True,
}

STANDARD_CONFIGURATION: dict[str, Any] = {
    **FORMAL_CONFIGURATION,
    "isFormalAssessment": False,
}

V1 = "/api/v1"
ASSESSOR = "/api/assessor"
SYSTEM = "/api/system/formal-assessments"

#: The seven conditions a learner acknowledges. Read from the domain rather than restated, so the
#: test cannot pass by acknowledging a list that has drifted from the one being shown.
from app.modules.formal_assessment.domain.conditions import (  # noqa: E402
    REQUIRED_CONDITION_CODES,
)

ALL_CONDITIONS = [code.value for code in REQUIRED_CONDITION_CODES]


# ---------------------------------------------------------------------------
# Driving the real chain
# ---------------------------------------------------------------------------


def _answer_for(ctx: Ctx, question_id: str, *, correctly: bool) -> dict[str, Any]:
    """An answer built from the bank's own key, so "passed" and "failed" are really that."""
    with ctx.session() as session:
        row = session.get(Question, question_id)
        assert row is not None
        options = sorted(row.options, key=lambda option: option.position)
        correct = [option.label for option in options if option.is_correct]
        wrong = [option.label for option in options if not option.is_correct]
    return {
        "type": "SINGLE_CHOICE",
        "selectedOptionId": correct[0] if correctly else wrong[0],
    }


def _configured(make_ctx: Any, formal: bool = True) -> Ctx:
    ctx = make_ctx(bank.DEFAULT_BANK)
    saved = ctx.save_configuration(
        FORMAL_CONFIGURATION if formal else STANDARD_CONFIGURATION
    )
    assert saved.status_code == 201, saved.text
    return ctx


def _start_formal(ctx: Ctx, token: str = LEARNER_TOKEN) -> tuple[str, str, str]:
    """Walk the whole pre-start sequence. Returns ``(formal_attempt_id, session_token, attempt_id)``.

    Every step is a real HTTP call through the real guards: the conditions must be acknowledged
    before identity may be confirmed, and identity before a device may claim the attempt. Skipping
    one is refused, which UC-09's own suite asserts; here the point is that the sequence produces a
    real UC-03 attempt at the end.
    """
    conditions = ctx.client.get(
        f"{V1}/quizzes/{ctx.quiz_id}/formal-conditions", headers=auth(token)
    )
    assert conditions.status_code == 200, conditions.text
    assert conditions.json()["is_formal_assessment"] is True

    acknowledged = ctx.client.post(
        f"{V1}/quizzes/{ctx.quiz_id}/conditions-acknowledgement",
        json={"acknowledged_condition_codes": ALL_CONDITIONS},
        headers=auth(token),
    )
    assert acknowledged.status_code in (200, 201), acknowledged.text
    formal_attempt_id = acknowledged.json()["formal_attempt_id"]

    identity = ctx.client.post(
        f"{V1}/quizzes/{ctx.quiz_id}/identity-confirmation",
        # Exactly the display name the platform directory holds. UC-09 matches it exactly, after
        # whitespace normalisation, and there is no configuration switch for that.
        json={"full_name": "Test Learner", "email": "learner@test.local"},
        headers=auth(token),
    )
    assert identity.status_code == 200, identity.text
    assert identity.json()["identity_check"]["confirmed"] is True

    started = ctx.client.post(
        f"{V1}/quizzes/{ctx.quiz_id}/formal-attempts",
        json={"device": {"fingerprint": "device-a", "platform": "test"}},
        headers=auth(token),
    )
    assert started.status_code in (200, 201), started.text
    body = started.json()
    session_token = body["session"]["session_token"]
    attempt_id = body["attempt_id"]
    assert session_token, "the registering device must be handed its credential once"
    assert attempt_id, "starting a formal attempt must produce a real UC-03 attempt"
    return formal_attempt_id, session_token, attempt_id


def _delivered_question_ids(ctx: Ctx, attempt_id: str) -> list[str]:
    with ctx.session() as session:
        return list(
            session.execute(
                text(
                    "SELECT question_id FROM qd_attempt_questions "
                    "WHERE attempt_id = :id ORDER BY position"
                ),
                {"id": attempt_id},
            ).scalars()
        )


def _sit_formal(
    ctx: Ctx,
    *,
    passing: bool,
    token: str = LEARNER_TOKEN,
) -> tuple[str, str]:
    """Start, answer through UC-09's autosave, and submit. Returns ``(formal_attempt_id, attempt_id)``."""
    formal_attempt_id, session_token, attempt_id = _start_formal(ctx, token)
    question_ids = _delivered_question_ids(ctx, attempt_id)
    assert len(question_ids) == 2

    saved = ctx.client.post(
        f"{V1}/formal-attempts/{formal_attempt_id}/autosave",
        json={
            "answers": [
                {
                    "question_id": question_id,
                    # One right, one wrong when passing (50% against a pass mark of 50); both
                    # wrong when failing.
                    "response": _answer_for(
                        ctx, question_id, correctly=passing and index == 0
                    ),
                }
                for index, question_id in enumerate(question_ids)
            ]
        },
        headers={**auth(token), "X-Formal-Session": session_token},
    )
    assert saved.status_code == 200, saved.text

    submitted = ctx.client.post(
        f"{V1}/formal-attempts/{formal_attempt_id}/submission",
        json={},
        headers={**auth(token), "X-Formal-Session": session_token},
    )
    assert submitted.status_code == 200, submitted.text
    return formal_attempt_id, attempt_id


def _certificate_row(ctx: Ctx, attempt_id: str) -> dict[str, Any] | None:
    with ctx.session() as session:
        row = (
            session.execute(
                text(
                    "SELECT status, certificate_number, issued_at "
                    "FROM qg_certificates WHERE attempt_id = :id"
                ),
                {"id": attempt_id},
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row else None


def _gate(ctx: Ctx, attempt_id: str) -> dict[str, Any]:
    """The certificate gate, asked the way UC-05's certificate service asks it — over HTTP."""
    response = ctx.client.get(
        f"{SYSTEM}/attempts/{attempt_id}/certificate-eligibility",
        headers=auth(ADMIN_TOKEN),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 1. The formal flag survives UC-01 -> UC-03 -> UC-09
# ---------------------------------------------------------------------------


def test_the_formal_flag_is_published_frozen_and_read_back_from_the_locked_version(
    make_ctx: Any,
) -> None:
    ctx = _configured(make_ctx)

    # UC-01 published it on the immutable version.
    version_id = ctx.active_version_id()
    assert (
        ctx.scalar(
            "SELECT is_formal_assessment FROM qc_configuration_versions WHERE id = :id",
            id=version_id,
        )
        == 1
    )

    _, _, attempt_id = _start_formal(ctx)

    # UC-03 recorded that this sitting was supervised, on the attempt row it owns.
    assert (
        ctx.scalar("SELECT is_formal_assessment FROM qd_attempts WHERE id = :id", id=attempt_id)
        == 1
    )

    # And UC-09 reads the policy back from the version the attempt locked. Proved by making the
    # quiz *informal* afterwards: the running assessment must not change underneath the learner.
    resaved = ctx.save_configuration(STANDARD_CONFIGURATION)
    assert resaved.status_code == 201, resaved.text
    assert ctx.version_count() == 2

    gate = _gate(ctx, attempt_id)
    assert gate["formal_assessment"] is True, (
        "a quiz made informal afterwards must not retroactively release a sitting already in flight"
    )


# ---------------------------------------------------------------------------
# 2 & 3. The certificate gate, against UC-05's real certificate flow
# ---------------------------------------------------------------------------


def test_a_passing_formal_assessment_withholds_the_certificate_until_an_assessor_approves(
    make_ctx: Any,
) -> None:
    """The rule UC-09 exists for, checked against the real UC-05 rather than a fake gate."""
    ctx = _configured(make_ctx)
    formal_attempt_id, attempt_id = _sit_formal(ctx, passing=True)

    # UC-04 scored it and UC-05 decided it — the ordinary chain, unchanged.
    outcome = ctx.scalar(
        "SELECT outcome FROM qg_attempt_outcomes WHERE attempt_id = :id", id=attempt_id
    )
    assert outcome == "PASS"

    # But no certificate has been issued. The obligation exists and is visible — that is what
    # makes it retryable — and its status is PENDING, not ISSUED.
    certificate = _certificate_row(ctx, attempt_id)
    assert certificate is not None, "the obligation must be durable, not skipped"
    assert certificate["status"] == "PENDING"
    assert certificate["certificate_number"] is None

    gate = _gate(ctx, attempt_id)
    assert gate["decision"] == "BLOCKED"
    assert gate["certificate_allowed"] is False
    assert gate["reason"] == "PENDING_HUMAN_REVIEW"

    # The assessor sees it in their queue.
    queue = ctx.client.get(f"{ASSESSOR}/pending-reviews", headers=auth(ASSESSOR_TOKEN))
    assert queue.status_code == 200, queue.text
    reviews = queue.json()["reviews"]
    assert len(reviews) == 1
    review = reviews[0]
    assert review["attempt_id"] == attempt_id
    assert review["state"] == "PENDING_REVIEW"

    # Approving releases it — through UC-05's own service.
    decided = ctx.client.post(
        f"{ASSESSOR}/reviews/{review['review_id']}/decision",
        json={"decision": "APPROVED", "notes": "Identity and conditions verified."},
        headers=auth(ASSESSOR_TOKEN),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["formal_attempt"]["certificate_allowed"] is True

    issued = _certificate_row(ctx, attempt_id)
    assert issued is not None
    assert issued["status"] == "ISSUED", "approval must release the certificate"
    assert issued["certificate_number"]

    after = _gate(ctx, attempt_id)
    assert after["decision"] == "ALLOWED"
    # The decision is attributable to a named human — the whole point of human review.
    assert after["approved_by"] == str(ctx.assessor_id)

    del formal_attempt_id  # the assertions above are all about the attempt, not the supervision id


def test_requiring_further_review_leaves_the_certificate_blocked(make_ctx: Any) -> None:
    ctx = _configured(make_ctx)
    _, attempt_id = _sit_formal(ctx, passing=True)

    queue = ctx.client.get(f"{ASSESSOR}/pending-reviews", headers=auth(ASSESSOR_TOKEN))
    review_id = queue.json()["reviews"][0]["review_id"]

    decided = ctx.client.post(
        f"{ASSESSOR}/reviews/{review_id}/decision",
        json={"decision": "REQUIRES_FURTHER_REVIEW", "notes": "Identity photograph unclear."},
        headers=auth(ASSESSOR_TOKEN),
    )
    assert decided.status_code == 200, decided.text

    certificate = _certificate_row(ctx, attempt_id)
    assert certificate is not None
    assert certificate["status"] == "PENDING", "an escalated review must not issue a certificate"

    gate = _gate(ctx, attempt_id)
    assert gate["decision"] == "BLOCKED"
    assert gate["certificate_allowed"] is False


def test_a_failing_formal_assessment_reaches_neither_a_review_nor_a_certificate(
    make_ctx: Any,
) -> None:
    ctx = _configured(make_ctx)
    _, attempt_id = _sit_formal(ctx, passing=False)

    assert (
        ctx.scalar("SELECT outcome FROM qg_attempt_outcomes WHERE attempt_id = :id", id=attempt_id)
        == "FAIL"
    )
    # UC-05 never requests a certificate for a fail, and UC-09 never queues a review for one:
    # human review exists to check a pass, not to reconsider a fail.
    assert _certificate_row(ctx, attempt_id) is None
    assert int(ctx.scalar("SELECT COUNT(*) FROM qs_formal_reviews") or 0) == 0

    queue = ctx.client.get(f"{ASSESSOR}/pending-reviews", headers=auth(ASSESSOR_TOKEN))
    assert queue.json()["reviews"] == []


# ---------------------------------------------------------------------------
# 5. The coaching restriction, against the real UC-07
# ---------------------------------------------------------------------------


def test_coaching_is_refused_on_an_older_attempt_while_a_formal_assessment_runs(
    make_ctx: Any,
) -> None:
    """The case an attempt-scoped check would miss: an exam in one tab, a coach in another.

    The learner first sits and fails an *ordinary* quiz, which leaves a perfectly coachable
    attempt. They then start a formal assessment at a second quiz. Coaching on the first attempt —
    submitted, scored, feedback released, nothing to do with the exam — must be refused, and must
    become available again once the exam is submitted.
    """
    ctx = _configured(make_ctx, formal=False)

    # A standard attempt, failed, so it has incorrect questions to coach.
    ordinary_attempt_id, questions = ctx.start_and_read_questions(LEARNER_TOKEN)
    for question in questions:
        saved = ctx.client.put(
            f"{V1}/attempts/{ordinary_attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": _answer_for(ctx, question["questionId"], correctly=False),
                "source": "MANUAL",
            },
            headers=auth(LEARNER_TOKEN),
        )
        assert saved.status_code == 200, saved.text
    submitted = ctx.client.post(
        f"{V1}/attempts/{ordinary_attempt_id}/submission",
        json={"confirmed": True},
        headers=auth(LEARNER_TOKEN),
    )
    assert submitted.status_code == 200, submitted.text

    def coaching_eligibility() -> dict[str, Any]:
        response = ctx.client.get(
            f"{V1}/attempts/{ordinary_attempt_id}/coaching/eligibility",
            headers=auth(LEARNER_TOKEN),
        )
        assert response.status_code == 200, response.text
        return response.json()

    before = coaching_eligibility()
    assert before["reason"] != "FORMAL_ASSESSMENT_IN_PROGRESS"

    # Now make the quiz formal and start a supervised sitting. A new version, so the finished
    # attempt above keeps its own rules.
    resaved = ctx.save_configuration(FORMAL_CONFIGURATION)
    assert resaved.status_code == 201, resaved.text
    formal_attempt_id, session_token, _ = _start_formal(ctx)

    during = coaching_eligibility()
    assert during["reason"] == "FORMAL_ASSESSMENT_IN_PROGRESS", (
        "coaching must be refused on every attempt while a formal assessment runs"
    )
    assert during["coachingAvailable"] is False

    # Attempting it anyway is refused, not merely hidden.
    forced = ctx.client.post(
        f"{V1}/attempts/{ordinary_attempt_id}/coaching/questions/{questions[0]['questionId']}",
        headers=auth(LEARNER_TOKEN),
    )
    assert forced.status_code == 403, forced.text
    assert forced.json()["error"]["code"] == "FORMAL_ASSESSMENT_IN_PROGRESS"

    # Submitting the exam lifts the restriction.
    finished = ctx.client.post(
        f"{V1}/formal-attempts/{formal_attempt_id}/submission",
        json={},
        headers={**auth(LEARNER_TOKEN), "X-Formal-Session": session_token},
    )
    assert finished.status_code == 200, finished.text

    after = coaching_eligibility()
    assert after["reason"] != "FORMAL_ASSESSMENT_IN_PROGRESS"


# ---------------------------------------------------------------------------
# 6. A standard quiz is untouched
# ---------------------------------------------------------------------------


def test_a_standard_quiz_issues_its_certificate_exactly_as_before(make_ctx: Any) -> None:
    """The gate must be invisible to the overwhelming majority of attempts."""
    ctx = _configured(make_ctx, formal=False)

    attempt_id, questions = ctx.start_and_read_questions(LEARNER_TOKEN)
    for index, question in enumerate(questions):
        ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": _answer_for(ctx, question["questionId"], correctly=index == 0),
                "source": "MANUAL",
            },
            headers=auth(LEARNER_TOKEN),
        )
    submitted = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/submission",
        json={"confirmed": True},
        headers=auth(LEARNER_TOKEN),
    )
    assert submitted.status_code == 200, submitted.text

    assert (
        ctx.scalar("SELECT outcome FROM qg_attempt_outcomes WHERE attempt_id = :id", id=attempt_id)
        == "PASS"
    )
    certificate = _certificate_row(ctx, attempt_id)
    assert certificate is not None
    assert certificate["status"] == "ISSUED", "a standard pass must not be delayed by UC-09"

    # No formal record exists, so the gate has no opinion rather than an allowing one.
    gate = _gate(ctx, attempt_id)
    assert gate["decision"] == "NOT_FORMAL_ASSESSMENT"
    assert gate["formal_assessment"] is False
    assert int(ctx.scalar("SELECT COUNT(*) FROM qs_formal_attempts") or 0) == 0


# ---------------------------------------------------------------------------
# The guards, over the real identity seam
# ---------------------------------------------------------------------------


def test_a_learner_cannot_reach_the_assessor_or_system_surfaces(make_ctx: Any) -> None:
    ctx = _configured(make_ctx)
    _, attempt_id = _sit_formal(ctx, passing=True)

    queue = ctx.client.get(f"{ASSESSOR}/pending-reviews", headers=auth(LEARNER_TOKEN))
    assert queue.status_code == 403

    # The system surface exists so a monitor can declare a disconnect. A learner able to reach it
    # could auto-submit somebody else's paper.
    disconnect = ctx.client.post(
        f"{SYSTEM}/formal-attempts/whatever/disconnect",
        json={},
        headers=auth(LEARNER_TOKEN),
    )
    assert disconnect.status_code == 403

    unauthenticated = ctx.client.get(f"{SYSTEM}/attempts/{attempt_id}/certificate-eligibility")
    assert unauthenticated.status_code == 401


def test_an_administrator_cannot_approve_a_formal_assessment(make_ctx: Any) -> None:
    """Configuring the quiz and signing off its passes are different authorities.

    Letting whoever can set the pass mark also approve the certificates would make human review a
    formality rather than a check.
    """
    ctx = _configured(make_ctx)
    _sit_formal(ctx, passing=True)

    queue = ctx.client.get(f"{ASSESSOR}/pending-reviews", headers=auth(ADMIN_TOKEN))
    assert queue.status_code == 403
