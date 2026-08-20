"""Live-server verification for UC-08, UC-09, UC-10 — and the deployment posture.

Sections 30–34 of ``scripts.verify_e2e``, kept in their own module because that script was already
1600 lines and these add five more journeys. Same server, same migrated database, same helpers,
which are passed in rather than imported so there is no cycle between the two modules.

WHY THESE EXIST
---------------
UC-08, UC-09 and UC-10 shipped with substantial test suites and **no live-server coverage at all**.
Their tests drive UC-03 through port fakes, and a fake has no CHECK constraints, no triggers, no
migrated schema and no HTTP layer. Every defect UC-11 found late was in exactly that gap:

* F-16 — the disconnect auto-submit path failed at the flush on a CHECK constraint UC-09's fakes
  did not have. Every supervised sitting that lost its connection lost the learner's work.
* F-17 — every UC-09 provider-outage error raised ``TypeError``, turning a retryable 503 into an
  opaque 500 and discarding the cause.
* F-18 — UC-01's configuration immutability trigger was absent from every migrated database.

None was visible to a green suite. So these sections drive the three capabilities the way a
reviewer will: over HTTP, against a database Alembic built, asserting against rows read back
through an independent connection.

WHAT EACH SECTION IS FOR
------------------------
30. **UC-08** — fail, retake on a fresh paper, exhaust the allowance, be granted one more, pass.
    The previous attempt is byte-compared throughout, and the configuration version's
    ``max_attempts`` is read from the table after the grant to prove a grant is per-learner.
31. **UC-09** — conditions, identity, device session, a second device refused, autosave, coaching
    refused mid-exam, disconnect auto-submit, and a certificate that does not exist until a named
    assessor approves. This is the section that would have caught F-16 on the day it shipped.
32. **UC-10** — dashboard figures checked against the rows UC-04 and UC-05 actually wrote, the
    cohort filter, CSV export, the empty state, and the flag-and-review workflow.
33. **Authorization with the guards switched on.** Sections 1–32 run with ``ADMIN_API_TOKEN``
    unset, which is the documented local-development posture and *not* what a deployment runs. This
    starts a second server against the same database with both tokens set — the deployed
    configuration — and asserts the matrix that only then means anything.
34. **Database integrity**, asserted against the database rather than the services: the unique
    indexes that make retries idempotent, the CHECK constraint F-16 was about, foreign-key
    enforcement, and the immutability triggers.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# The seam back into verify_e2e
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    """What these sections need from ``verify_e2e``, passed in rather than imported.

    Keeps the dependency one-way: ``verify_e2e`` imports this module, and this module imports
    nothing from it. The counters and the failure list stay in one place, so the final tally is the
    whole run's.
    """

    check: Callable[..., None]
    call: Callable[..., tuple[int, Any]]
    section: Callable[[str], None]
    readonly_db: Callable[[Path], Any]
    db_path: Path
    base: str
    admin_token: str
    learner_token: str
    learner2_token: str
    assessor_token: str
    platform: dict[str, Any]
    tmp: Path
    env: dict[str, str]
    backend_dir: Path

    @property
    def api(self) -> str:
        return f"{self.base}/api"

    @property
    def v1(self) -> str:
        return f"{self.base}/api/v1"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

#: Two single-choice questions and two attempts, so the allowance arithmetic is observable in a
#: handful of calls rather than a dozen. Pass mark 60 with two questions means one right answer is
#: 50% — a fail — and two is a pass, so both outcomes are reachable without partial credit.
RETAKE_CONFIGURATION: dict[str, Any] = {
    "questionCount": 2,
    "timeLimitMinutes": 30,
    "passMark": 60,
    "questionTypes": [{"type": "SINGLE_CHOICE"}],
    "randomiseQuestions": False,
    "maxAttempts": 2,
    "deliveryMode": "assessment",
}

FORMAL_CONFIGURATION: dict[str, Any] = {
    "questionCount": 2,
    "timeLimitMinutes": 45,
    "passMark": 50,
    "questionTypes": [{"type": "SINGLE_CHOICE"}],
    "randomiseQuestions": False,
    "maxAttempts": 2,
    "deliveryMode": "assessment",
    "isFormalAssessment": True,
    "requiresHumanReview": True,
    "requiresAssessorApproval": True,
}


def _answer_key(h: Harness, question_id: str) -> tuple[list[str], list[str]]:
    """``(correct, wrong)`` option labels, read straight from UC-02's own table.

    Read from the database rather than guessed, so "answered correctly" is a fact about the answer
    key and not about which option the script happened to pick. The delivered option ids *are* the
    labels — that is the mapping UC-03's bank adapter makes — so these can be sent as answers.
    """
    with h.readonly_db(h.db_path) as conn:
        rows = conn.execute(
            "SELECT label, is_correct FROM qb_question_options "
            "WHERE question_id = ? ORDER BY position",
            (question_id,),
        ).fetchall()
    correct = [row[0] for row in rows if row[1]]
    wrong = [row[0] for row in rows if not row[1]]
    return correct, wrong


def _delivered(h: Harness, attempt_id: str) -> list[str]:
    """The frozen paper, from UC-03's own table, in delivery order."""
    with h.readonly_db(h.db_path) as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT question_id FROM qd_attempt_questions "
                "WHERE attempt_id = ? ORDER BY position",
                (attempt_id,),
            )
        ]


def _rows(h: Harness, sql: str, *params: Any) -> list[tuple]:
    with h.readonly_db(h.db_path) as conn:
        return conn.execute(sql, params).fetchall()


def _configure(h: Harness, quiz_id: int, payload: dict[str, Any]) -> tuple[int, Any]:
    return h.call(
        "PUT",
        f"{h.api}/admin/quizzes/{quiz_id}/configuration",
        payload,
        token=h.admin_token,
    )


def _answer_all(
    h: Harness, attempt_id: str, *, correct_count: int, token: str
) -> list[str]:
    """Answer every delivered question, the first ``correct_count`` of them correctly."""
    status, body = h.call("GET", f"{h.v1}/attempts/{attempt_id}/questions", token=token)
    assert status == 200, body
    delivered = body["questions"]
    for index, question in enumerate(delivered):
        correct, wrong = _answer_key(h, question["questionId"])
        chosen = correct[0] if index < correct_count else wrong[0]
        h.call(
            "PUT",
            f"{h.v1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            {"response": {"selectedOptionId": chosen}, "source": "MANUAL"},
            token=token,
        )
    return [question["questionId"] for question in delivered]


def _sit(h: Harness, quiz_id: int, *, correct_count: int, token: str) -> str:
    """Start, answer and submit a fresh attempt. Returns the attempt id."""
    status, created = h.call("POST", f"{h.v1}/attempts", {"quizId": str(quiz_id)}, token=token)
    assert status == 201, created
    attempt_id = created["attempt"]["attemptId"]
    _answer_all(h, attempt_id, correct_count=correct_count, token=token)
    h.call("POST", f"{h.v1}/attempts/{attempt_id}/submission", {"confirmed": True}, token=token)
    return attempt_id


def _sit_existing(h: Harness, attempt_id: str, *, correct_count: int, token: str) -> None:
    """Answer and submit an attempt UC-08 already created — the shape a retake arrives in."""
    _answer_all(h, attempt_id, correct_count=correct_count, token=token)
    h.call("POST", f"{h.v1}/attempts/{attempt_id}/submission", {"confirmed": True}, token=token)


def _run_chain(h: Harness, attempt_id: str, token: str) -> tuple[Any, Any]:
    """Drive UC-04 then UC-05 and return ``(result body, outcome body)``."""
    _, result = h.call("POST", f"{h.v1}/attempts/{attempt_id}/result", {}, token=token)
    _, outcome = h.call("POST", f"{h.v1}/attempts/{attempt_id}/outcome", {}, token=token)
    return result, outcome


# ---------------------------------------------------------------------------
# 30. UC-08 Retake Management
# ---------------------------------------------------------------------------


def section_30_retakes(h: Harness) -> None:
    h.section("30. UC-08 retakes: fail, retake a fresh paper, exhaust, be granted one more")

    quiz_id = h.platform["e2e-retake-quiz"]
    status, _ = _configure(h, quiz_id, RETAKE_CONFIGURATION)
    h.check("the retake quiz is configured (201)", status == 201)

    version_id = _rows(
        h, "SELECT active_configuration_version_id FROM qc_quizzes WHERE id = ?", quiz_id
    )[0][0]
    max_before = _rows(
        h, "SELECT max_attempts FROM qc_configuration_versions WHERE id = ?", version_id
    )[0][0]

    # ---- attempt 1: failed ------------------------------------------------
    first = _sit(h, quiz_id, correct_count=0, token=h.learner_token)
    _, outcome = _run_chain(h, first, h.learner_token)
    h.check("attempt 1 is a fail", outcome["outcome"]["outcome"] == "FAIL", json.dumps(outcome)[:200])
    h.check("no certificate for a failed attempt", outcome.get("certificate") is None)
    h.check("one attempt remains", outcome["attemptsRemaining"] == 1)

    first_paper = _delivered(h, first)
    h.check("attempt 1 froze a 2-question paper", len(first_paper) == 2, str(first_paper))

    # A byte-level snapshot of everything the first attempt produced. UC-08's central promise is
    # that a retake leaves this untouched, and a whole-row comparison is the only way to check it
    # rather than checking the one column somebody thought of.
    def snapshot() -> dict[str, list[tuple]]:
        rows: dict[str, list[tuple]] = {}
        # `qd_attempts` is keyed by `id`, and `qr_question_scores` hangs off the result rather than
        # the attempt, so each table is asked the question it can answer. Naming them individually
        # keeps the snapshot honest: a table that silently returned nothing would make the
        # "unchanged" assertion vacuously true, which is the failure this whole check exists to
        # prevent.
        rows["qd_attempts"] = _rows(h, "SELECT * FROM qd_attempts WHERE id = ?", first)
        for table in (
            "qd_attempt_questions",
            "qd_attempt_answers",
            "qd_attempt_submissions",
            "qr_attempt_results",
            "qg_attempt_outcomes",
        ):
            rows[table] = _rows(h, f"SELECT * FROM {table} WHERE attempt_id = ?", first)  # noqa: S608
        rows["qr_question_scores"] = _rows(
            h,
            "SELECT s.* FROM qr_question_scores s "
            "JOIN qr_attempt_results r ON r.id = s.result_id "
            "WHERE r.attempt_id = ? ORDER BY s.id",
            first,
        )
        return rows

    before = snapshot()

    # ---- eligibility and the retake --------------------------------------
    status, eligibility = h.call(
        "GET", f"{h.v1}/quizzes/{quiz_id}/retake-eligibility", token=h.learner_token
    )
    h.check("retake eligibility is ELIGIBLE", eligibility.get("state") == "ELIGIBLE", str(eligibility))
    h.check(
        "eligibility reports the real allowance",
        eligibility["allowance"]["maximum_attempts"] == max_before,
        json.dumps(eligibility.get("allowance")),
    )

    status, retake = h.call("POST", f"{h.v1}/quizzes/{quiz_id}/retakes", {}, token=h.learner_token)
    h.check("POST /retakes creates a retake (201)", status == 201, json.dumps(retake)[:200])
    second = retake["attempt"]["attempt_id"]
    h.check("the retake is a new attempt", second != first)

    second_paper = _delivered(h, second)
    plan = retake.get("question_plan") or {}
    fresh = [qid for qid in second_paper if qid not in first_paper]
    reused = [qid for qid in second_paper if qid in first_paper]

    # UC-08's rule is *prefer unseen*, not *guarantee unseen*: with a bank too small to avoid it,
    # a retake is still delivered in full and the reuse is recorded rather than the retake being
    # refused. Asserting bare disjointness here would be asserting the size of the seeded bank, and
    # would start failing the day someone retired a question — for a reason unrelated to UC-08.
    #
    # So the check is the contract: as many fresh questions as the unused pool allows, and any
    # reuse predicted in the plan rather than discovered afterwards.
    unused_pool = plan.get("unused_pool_size")
    expected_fresh = plan.get("expected_fresh_questions")
    h.check(
        "the retake reports the plan it drew against",
        unused_pool is not None and expected_fresh is not None,
        json.dumps(plan)[:250],
    )
    if unused_pool is not None:
        h.check(
            "the retake takes every fresh question the bank could offer",
            len(fresh) == min(len(second_paper), unused_pool) == expected_fresh,
            f"fresh={len(fresh)} unused_pool={unused_pool} expected={expected_fresh} "
            f"first={first_paper} second={second_paper}",
        )
        h.check(
            "reuse happens only when the pool cannot cover the paper, and is declared",
            (not reused and not plan.get("reuse_expected"))
            or (reused and plan.get("reuse_expected") and plan.get("reuse_reason")),
            f"reused={len(reused)} plan={json.dumps(plan)[:200]}",
        )
    h.check(
        "the retake is not merely a reordering of the same paper",
        second_paper != first_paper,
        f"first={first_paper} second={second_paper}",
    )
    h.check(
        "the retake is recorded in qt_retake_requests",
        len(_rows(h, "SELECT id FROM qt_retake_requests WHERE attempt_id = ?", second)) == 1,
    )

    # Idempotency is the database's, not a service's: a repeated request must not produce a second
    # attempt or a second row.
    status, repeat = h.call("POST", f"{h.v1}/quizzes/{quiz_id}/retakes", {}, token=h.learner_token)
    h.check(
        "a repeated retake request does not create a second attempt",
        repeat.get("attempt", {}).get("attempt_id") == second or status == 409,
        f"status={status} body={json.dumps(repeat)[:200]}",
    )
    h.check(
        "still exactly one open retake row",
        len(_rows(h, "SELECT id FROM qt_retake_requests WHERE attempt_id = ?", second)) == 1,
    )

    h.check("the previous attempt is byte-for-byte unchanged", snapshot() == before)

    # ---- attempt 2 fails too, so the allowance is spent -------------------
    _sit_existing(h, second, correct_count=0, token=h.learner_token)
    _run_chain(h, second, h.learner_token)

    status, eligibility = h.call(
        "GET", f"{h.v1}/quizzes/{quiz_id}/retake-eligibility", token=h.learner_token
    )
    h.check("the allowance is now EXHAUSTED", eligibility.get("state") == "EXHAUSTED", str(eligibility))
    h.check(
        "an exhausted learner is told who to contact",
        bool(eligibility.get("guidance") or eligibility.get("contact_guidance")),
        json.dumps(eligibility)[:250],
    )

    status, refused = h.call("POST", f"{h.v1}/quizzes/{quiz_id}/retakes", {}, token=h.learner_token)
    h.check("a retake beyond the allowance is refused with 409", status == 409, str(status))
    h.check("the refusal explains itself", "error" in refused and refused["error"].get("code"))

    # ---- the administrator grant ------------------------------------------
    status, granted = h.call(
        "POST",
        f"{h.api}/admin/retakes/grants",
        {
            "learner_id": str(h.platform["e2e-learner@example.com"]),
            "course_id": str(h.platform["course_id"]),
            "quiz_id": str(quiz_id),
            "additional_attempts": 1,
            "reason": "Verified technical fault during the second attempt.",
            "idempotency_key": "e2e-grant-1",
        },
        token=h.admin_token,
    )
    h.check("an administrator can grant one more attempt (201)", status == 201, json.dumps(granted)[:200])

    max_after = _rows(
        h, "SELECT max_attempts FROM qc_configuration_versions WHERE id = ?", version_id
    )[0][0]
    h.check(
        "the grant did NOT change the quiz's configured maximum",
        max_after == max_before,
        f"{max_before} -> {max_after}",
    )
    h.check(
        "and published no new configuration version",
        len(_rows(h, "SELECT id FROM qc_configuration_versions WHERE quiz_id = ?", quiz_id)) == 1,
    )

    status, learner_refused = h.call(
        "POST",
        f"{h.api}/admin/retakes/grants",
        {
            "learner_id": str(h.platform["e2e-learner@example.com"]),
            "course_id": str(h.platform["course_id"]),
            "quiz_id": str(quiz_id),
            "additional_attempts": 5,
            "reason": "Trying to grant myself more attempts.",
            "idempotency_key": "e2e-grant-learner",
        },
        token=h.learner_token,
    )
    h.check("a learner cannot grant themselves attempts (403)", learner_refused and status == 403, str(status))

    # A repeated grant with the same key must return the existing grant, not add a second.
    status, replay = h.call(
        "POST",
        f"{h.api}/admin/retakes/grants",
        {
            "learner_id": str(h.platform["e2e-learner@example.com"]),
            "course_id": str(h.platform["course_id"]),
            "quiz_id": str(quiz_id),
            "additional_attempts": 1,
            "reason": "Verified technical fault during the second attempt.",
            "idempotency_key": "e2e-grant-1",
        },
        token=h.admin_token,
    )
    h.check("the same idempotency key replays rather than granting twice", status == 200, str(status))
    h.check(
        "exactly one grant row exists",
        len(
            _rows(
                h,
                "SELECT id FROM qt_additional_attempt_grants WHERE quiz_id = ? AND revoked_at IS NULL",
                str(quiz_id),
            )
        )
        == 1,
    )

    status, eligibility = h.call(
        "GET", f"{h.v1}/quizzes/{quiz_id}/retake-eligibility", token=h.learner_token
    )
    h.check(
        "eligibility now distinguishes a granted attempt",
        eligibility.get("state") == "ADDITIONAL_ATTEMPT_AVAILABLE",
        str(eligibility),
    )

    # ---- attempt 3 passes --------------------------------------------------
    status, third_retake = h.call(
        "POST", f"{h.v1}/quizzes/{quiz_id}/retakes", {}, token=h.learner_token
    )
    h.check("the granted attempt can be taken (201)", status == 201, json.dumps(third_retake)[:200])
    third = third_retake["attempt"]["attempt_id"]
    _sit_existing(h, third, correct_count=2, token=h.learner_token)
    _, outcome = _run_chain(h, third, h.learner_token)
    h.check("attempt 3 passes", outcome["outcome"]["outcome"] == "PASS", json.dumps(outcome)[:200])
    h.check("attempt 3 is numbered 3", outcome["outcome"]["attemptNumber"] == 3)
    h.check(
        "a certificate is issued for the passing attempt",
        (outcome.get("certificate") or {}).get("status") == "ISSUED",
        json.dumps(outcome.get("certificate"))[:200],
    )
    h.check(
        "exactly one certificate exists for this learner and quiz",
        len(
            _rows(
                h,
                "SELECT id FROM qg_certificates WHERE quiz_id = ? AND status = 'ISSUED'",
                str(quiz_id),
            )
        )
        == 1,
    )
    h.check("the first attempt is still untouched", snapshot() == before)

    # ---- attempt history --------------------------------------------------
    status, history = h.call(
        "GET", f"{h.v1}/quizzes/{quiz_id}/attempt-history", token=h.learner_token
    )
    h.check("attempt history returns 200", status == 200)
    entries = history.get("entries", [])
    h.check("history lists all three attempts", len(entries) == 3, str(len(entries)))
    h.check(
        "history reports them in order with the right verdicts",
        [entry["attempt_number"] for entry in entries] == [1, 2, 3]
        and [entry["pass_fail_status"] for entry in entries] == ["FAILED", "FAILED", "PASSED"],
        json.dumps([(e["attempt_number"], e["pass_fail_status"]) for e in entries]),
    )
    h.check(
        "history marks which attempts were retakes",
        [entry["is_retake"] for entry in entries] == [False, True, True],
        json.dumps([e["is_retake"] for e in entries]),
    )

    status, other = h.call(
        "GET", f"{h.v1}/quizzes/{quiz_id}/attempt-history", token=h.learner2_token
    )
    h.check(
        "another learner's history does not contain this learner's attempts",
        status == 200 and other.get("entries", []) == [],
        json.dumps(other)[:200],
    )


# ---------------------------------------------------------------------------
# 31. UC-09 Formal Assessment Mode
# ---------------------------------------------------------------------------


def section_31_formal(h: Harness) -> None:
    h.section("31. UC-09 formal assessment: conditions, one device, disconnect, assessor approval")

    quiz_id = h.platform["e2e-formal-quiz"]
    status, _ = _configure(h, quiz_id, FORMAL_CONFIGURATION)
    h.check("the formal quiz is configured (201)", status == 201)

    status, published = h.call(
        "GET", f"{h.api}/admin/quizzes/{quiz_id}/configuration", token=h.admin_token
    )
    h.check(
        "the formal flag is on the published version",
        published["configuration"]["isFormalAssessment"] is True,
        json.dumps(published["configuration"])[:200],
    )

    # ---- the pre-start sequence, in order ---------------------------------
    status, conditions = h.call(
        "GET", f"{h.v1}/quizzes/{quiz_id}/formal-conditions", token=h.learner_token
    )
    h.check("the learner is served the formal conditions (200)", status == 200, str(status))
    h.check("and told this is a formal assessment", conditions.get("is_formal_assessment") is True)
    codes = [item["code"] for item in conditions.get("conditions", [])]
    h.check("the conditions are enumerated", len(codes) > 0, str(codes))

    # Starting before acknowledging must be refused. This is the gate, not a formality.
    status, premature = h.call(
        "POST",
        f"{h.v1}/quizzes/{quiz_id}/formal-attempts",
        {"device": {"fingerprint": "device-a", "platform": "e2e"}},
        token=h.learner_token,
    )
    h.check(
        "starting before acknowledging the conditions is refused",
        status in (403, 409, 422),
        f"status={status} body={json.dumps(premature)[:200]}",
    )

    status, acknowledged = h.call(
        "POST",
        f"{h.v1}/quizzes/{quiz_id}/conditions-acknowledgement",
        {"acknowledged_condition_codes": codes},
        token=h.learner_token,
    )
    h.check("the acknowledgement is accepted", status in (200, 201), json.dumps(acknowledged)[:200])
    formal_attempt_id = acknowledged["formal_attempt_id"]

    # Identity is matched against the platform directory, exactly, with no configuration switch.
    status, wrong_identity = h.call(
        "POST",
        f"{h.v1}/quizzes/{quiz_id}/identity-confirmation",
        {"full_name": "Someone Else Entirely", "email": "e2e-learner@example.com"},
        token=h.learner_token,
    )
    h.check(
        "a name that does not match the directory is refused",
        status != 200 or wrong_identity.get("identity_check", {}).get("confirmed") is not True,
        f"status={status} body={json.dumps(wrong_identity)[:200]}",
    )

    status, identity = h.call(
        "POST",
        f"{h.v1}/quizzes/{quiz_id}/identity-confirmation",
        {"full_name": "E2E Learner", "email": "e2e-learner@example.com"},
        token=h.learner_token,
    )
    h.check(
        "the learner's real identity is confirmed",
        status == 200 and identity["identity_check"]["confirmed"] is True,
        json.dumps(identity)[:200],
    )

    status, started = h.call(
        "POST",
        f"{h.v1}/quizzes/{quiz_id}/formal-attempts",
        {"device": {"fingerprint": "device-a", "platform": "e2e"}},
        token=h.learner_token,
    )
    h.check("the formal attempt starts (200/201)", status in (200, 201), json.dumps(started)[:250])
    session_token = started["session"]["session_token"]
    attempt_id = started["attempt_id"]
    h.check("it produced a real UC-03 attempt", bool(attempt_id))
    h.check(
        "the attempt is flagged formal in UC-03's own table",
        _rows(h, "SELECT is_formal_assessment FROM qd_attempts WHERE id = ?", attempt_id)[0][0]
        in (1, True),
    )

    # ---- one device, and only one -----------------------------------------
    status, second_device = h.call(
        "POST",
        f"{h.v1}/quizzes/{quiz_id}/formal-attempts",
        {"device": {"fingerprint": "device-b", "platform": "e2e"}},
        token=h.learner_token,
    )
    h.check(
        "a second device cannot claim the same formal attempt",
        status in (403, 409),
        f"status={status} body={json.dumps(second_device)[:200]}",
    )

    # ---- pausing is always refused ----------------------------------------
    status, paused = h.call(
        "POST", f"{h.v1}/formal-attempts/{formal_attempt_id}/pause", {}, token=h.learner_token
    )
    h.check("a formal assessment cannot be paused", status == 409, f"status={status}")

    # ---- coaching is refused while the exam runs --------------------------
    status, coaching = h.call(
        "GET", f"{h.v1}/attempts/{attempt_id}/coaching/eligibility", token=h.learner_token
    )
    # A 200 that says "unavailable, and here is why" is the better answer than a refusal: the
    # client needs to render a reason, and an error status would make it guess. What matters is
    # that `coachingAvailable` is false and the reason names the formal assessment rather than
    # something generic — a learner told only "unavailable" would reasonably retry.
    available = (coaching or {}).get("coachingAvailable")
    reason = (coaching or {}).get("reason") or ""
    h.check(
        "AI coaching is not available during a formal assessment",
        status in (403, 409) or available is False,
        f"status={status} body={json.dumps(coaching)[:250]}",
    )
    h.check(
        "and the refusal names the formal assessment as the reason",
        status in (403, 409) or "FORMAL" in reason.upper(),
        f"reason={reason!r}",
    )
    h.check(
        "and no coaching payload leaks an answer key",
        "isCorrect" not in json.dumps(coaching) and "correctAnswer" not in json.dumps(coaching),
        json.dumps(coaching)[:200],
    )

    # ---- autosave through UC-09's own endpoint ----------------------------
    delivered = _delivered(h, attempt_id)
    h.check("the formal paper has two questions", len(delivered) == 2, str(delivered))
    correct, _wrong = _answer_key(h, delivered[0])

    status, saved = h.call(
        "POST",
        f"{h.v1}/formal-attempts/{formal_attempt_id}/autosave",
        {
            "answers": [
                {"question_id": delivered[0], "response": {"selectedOptionId": correct[0]}}
            ]
        },
        token=h.learner_token,
        extra_headers={"X-Formal-Session": session_token},
    )
    h.check("an autosave from the registered device is accepted", status == 200, json.dumps(saved)[:200])

    status, hijacked = h.call(
        "POST",
        f"{h.v1}/formal-attempts/{formal_attempt_id}/autosave",
        {
            "answers": [
                {"question_id": delivered[0], "response": {"selectedOptionId": correct[0]}}
            ]
        },
        token=h.learner_token,
        extra_headers={"X-Formal-Session": "not-the-real-session-token"},
    )
    h.check(
        "an autosave with a forged session token is refused",
        status in (401, 403, 409),
        f"status={status} body={json.dumps(hijacked)[:200]}",
    )

    # ---- the disconnect: this is the path F-16 broke ----------------------
    status, dropped = h.call(
        "POST",
        f"{h.v1}/formal-attempts/{formal_attempt_id}/disconnect",
        {"reason": "NETWORK_LOSS"},
        token=h.learner_token,
        extra_headers={"X-Formal-Session": session_token},
    )
    h.check(
        "a disconnect auto-submits rather than failing (this is F-16's regression)",
        status in (200, 201),
        f"status={status} body={json.dumps(dropped)[:300]}",
    )
    h.check(
        "the submission is recorded with the disconnect reason",
        _rows(
            h,
            "SELECT submission_reason FROM qd_attempt_submissions WHERE attempt_id = ?",
            attempt_id,
        )
        == [("DISCONNECT_AUTO_SUBMIT",)],
        str(_rows(h, "SELECT submission_reason FROM qd_attempt_submissions WHERE attempt_id = ?", attempt_id)),
    )
    h.check(
        "the attempt is SUBMITTED in UC-03's table",
        _rows(h, "SELECT status FROM qd_attempts WHERE id = ?", attempt_id) == [("SUBMITTED",)],
    )

    status, again = h.call(
        "POST",
        f"{h.v1}/formal-attempts/{formal_attempt_id}/disconnect",
        {"reason": "NETWORK_LOSS"},
        token=h.learner_token,
        extra_headers={"X-Formal-Session": session_token},
    )
    h.check("a repeated disconnect produces one submission, not two", status in (200, 201))
    h.check(
        "still exactly one submission row",
        len(_rows(h, "SELECT id FROM qd_attempt_submissions WHERE attempt_id = ?", attempt_id)) == 1,
    )

    status, resumed = h.call(
        "POST", f"{h.v1}/formal-attempts/{formal_attempt_id}/resume", {}, token=h.learner_token
    )
    h.check("a disconnected formal attempt cannot be resumed", status in (403, 409), str(status))

    # ---- scored from the autosaved state ----------------------------------
    result, outcome = _run_chain(h, attempt_id, h.learner_token)
    h.check("the autosaved answer was scored", result["result"]["correctCount"] == 1, json.dumps(result["result"])[:250])
    h.check(
        "and nothing was invented for the question never reached",
        result["result"]["unansweredCount"] == 1,
        json.dumps(result["result"])[:250],
    )
    h.check("50% against a pass mark of 50 is a pass", outcome["outcome"]["outcome"] == "PASS")

    # ---- the certificate waits for a human -------------------------------
    issued = _rows(
        h, "SELECT status FROM qg_certificates WHERE attempt_id = ?", attempt_id
    )
    h.check(
        "a formal pass does NOT certificate before an assessor decides",
        all(row[0] != "ISSUED" for row in issued),
        str(issued),
    )

    status, retried = h.call(
        "POST", f"{h.v1}/attempts/{attempt_id}/outcome/certificate/retry", {}, token=h.learner_token
    )
    issued = _rows(h, "SELECT status FROM qg_certificates WHERE attempt_id = ?", attempt_id)
    h.check(
        "retrying the certificate does not smuggle one past the gate",
        all(row[0] != "ISSUED" for row in issued),
        f"status={status} rows={issued}",
    )

    # ---- only an assessor may approve ------------------------------------
    status, queue = h.call("GET", f"{h.api}/assessor/pending-reviews", token=h.assessor_token)
    h.check("the assessor sees the pending review (200)", status == 200, json.dumps(queue)[:200])
    reviews = queue.get("reviews", [])
    h.check("exactly one review is pending", len(reviews) == 1, str(len(reviews)))
    review_id = reviews[0]["review_id"]

    status, learner_peek = h.call(
        "GET", f"{h.api}/assessor/pending-reviews", token=h.learner_token
    )
    h.check("a learner cannot see the assessor queue (403)", status == 403, str(status))

    status, admin_decision = h.call(
        "POST",
        f"{h.api}/assessor/reviews/{review_id}/decision",
        {"decision": "APPROVED", "notes": "Administrator trying to self-approve."},
        token=h.admin_token,
    )
    h.check(
        "an administrator cannot approve a formal assessment (403)",
        status == 403,
        f"status={status} body={json.dumps(admin_decision)[:200]}",
    )
    issued = _rows(h, "SELECT status FROM qg_certificates WHERE attempt_id = ?", attempt_id)
    h.check("and the refusal left no certificate behind", all(row[0] != "ISSUED" for row in issued))

    h.call(
        "POST", f"{h.api}/assessor/reviews/{review_id}/review-start", {}, token=h.assessor_token
    )
    status, decided = h.call(
        "POST",
        f"{h.api}/assessor/reviews/{review_id}/decision",
        {"decision": "APPROVED", "notes": "Identity and session verified against the record."},
        token=h.assessor_token,
    )
    h.check("the assessor's decision is accepted", status in (200, 201), json.dumps(decided)[:250])

    status, workflow = h.call(
        "POST",
        f"{h.api}/assessor/reviews/{review_id}/certificate-workflow",
        {},
        token=h.assessor_token,
    )
    h.check("the certificate workflow runs after approval", status in (200, 201), json.dumps(workflow)[:250])

    issued = _rows(
        h,
        "SELECT status, certificate_number FROM qg_certificates WHERE attempt_id = ?",
        attempt_id,
    )
    h.check(
        "the certificate now exists, and only now",
        any(row[0] == "ISSUED" and row[1] for row in issued),
        str(issued),
    )
    h.check(
        "and there is exactly one of it",
        len([row for row in issued if row[0] == "ISSUED"]) == 1,
        str(issued),
    )

    # ---- the review record is the audit trail ----------------------------
    review_rows = _rows(
        h, "SELECT state, decided_by FROM qs_formal_reviews WHERE formal_attempt_id = ?", formal_attempt_id
    )
    h.check(
        "the review names who decided it",
        len(review_rows) == 1 and bool(review_rows[0][1]),
        str(review_rows),
    )

    # ---- a failing formal assessment reaches no review at all ------------
    status, ack2 = h.call(
        "POST",
        f"{h.v1}/quizzes/{quiz_id}/conditions-acknowledgement",
        {"acknowledged_condition_codes": codes},
        token=h.learner2_token,
    )
    if status in (200, 201):
        h.call(
            "POST",
            f"{h.v1}/quizzes/{quiz_id}/identity-confirmation",
            {"full_name": "E2E Learner Two", "email": "e2e-learner2@example.com"},
            token=h.learner2_token,
        )
        status, started2 = h.call(
            "POST",
            f"{h.v1}/quizzes/{quiz_id}/formal-attempts",
            {"device": {"fingerprint": "device-c", "platform": "e2e"}},
            token=h.learner2_token,
        )
        if status in (200, 201):
            formal2 = ack2["formal_attempt_id"]
            session2 = started2["session"]["session_token"]
            attempt2 = started2["attempt_id"]
            delivered2 = _delivered(h, attempt2)
            _, wrong2 = _answer_key(h, delivered2[0])
            h.call(
                "POST",
                f"{h.v1}/formal-attempts/{formal2}/autosave",
                {
                    "answers": [
                        {"question_id": qid, "response": {"selectedOptionId": _answer_key(h, qid)[1][0]}}
                        for qid in delivered2
                    ]
                },
                token=h.learner2_token,
                extra_headers={"X-Formal-Session": session2},
            )
            h.call(
                "POST",
                f"{h.v1}/formal-attempts/{formal2}/submission",
                {},
                token=h.learner2_token,
                extra_headers={"X-Formal-Session": session2},
            )
            _, outcome2 = _run_chain(h, attempt2, h.learner2_token)
            h.check(
                "a failing formal assessment is a fail",
                outcome2["outcome"]["outcome"] == "FAIL",
                json.dumps(outcome2["outcome"])[:200],
            )
            h.check(
                "a failing formal assessment creates no certificate",
                _rows(h, "SELECT id FROM qg_certificates WHERE attempt_id = ?", attempt2) == [],
            )
            h.check(
                "and reaches no assessor review",
                _rows(h, "SELECT id FROM qs_formal_reviews WHERE formal_attempt_id = ?", formal2)
                == [],
            )


# ---------------------------------------------------------------------------
# 32. UC-10 Analytics & Reporting
# ---------------------------------------------------------------------------


def section_32_analytics(h: Harness) -> None:
    h.section("32. UC-10 analytics: the chain's own figures, filters, export, flags")

    admin_analytics = f"{h.api}/admin/analytics"

    status, overall = h.call("GET", f"{admin_analytics}/overall", token=h.admin_token)
    h.check("GET /admin/analytics/overall returns 200", status == 200, json.dumps(overall)[:200])

    # Every headline figure is checked against the rows UC-04 and UC-05 actually wrote. A dashboard
    # that agreed with a fixture rather than with the database would pass a weaker test than this.
    scored = _rows(h, "SELECT percentage FROM qr_attempt_results WHERE status = 'SCORED'")
    outcomes = _rows(h, "SELECT outcome FROM qg_attempt_outcomes")
    attempts = _rows(h, "SELECT COUNT(*) FROM qd_attempts")[0][0]
    passes = len([row for row in outcomes if row[0] == "PASS"])

    h.check(
        "attempt volume matches UC-03's table",
        overall.get("attempt_volume") == attempts,
        f"reported={overall.get('attempt_volume')} actual={attempts}",
    )
    h.check(
        "scored attempts match UC-04's table",
        overall.get("scored_attempts") == len(scored),
        f"reported={overall.get('scored_attempts')} actual={len(scored)}",
    )
    h.check(
        "graded attempts match UC-05's table",
        overall.get("graded_attempts") == len(outcomes),
        f"reported={overall.get('graded_attempts')} actual={len(outcomes)}",
    )
    h.check(
        "passed attempts match UC-05's table",
        overall.get("passed_attempts") == passes,
        f"reported={overall.get('passed_attempts')} actual={passes}",
    )
    if scored:
        expected_average = sum(row[0] for row in scored) / len(scored)
        h.check(
            "the average score is the mean of UC-04's percentages",
            abs((overall.get("average_score") or 0) - expected_average) < 0.05,
            f"reported={overall.get('average_score')} actual={expected_average:.4f}",
        )
    h.check("the figures say when they were calculated", bool(overall.get("calculated_at")))
    h.check("and report their data state", overall.get("data_state") == "OK", str(overall.get("data_state")))

    # ---- question analytics ----------------------------------------------
    status, questions = h.call("GET", f"{admin_analytics}/questions", token=h.admin_token)
    h.check("GET /admin/analytics/questions returns 200", status == 200)
    items = questions.get("questions", questions.get("items", []))
    h.check("question analytics reports rows", len(items) > 0, json.dumps(questions)[:250])
    if items:
        sample = items[0]
        h.check(
            "each question reports a human-readable type, not an enum name",
            sample.get("display_type", sample.get("question_type", "")) not in ("SINGLE_CHOICE", ""),
            json.dumps(sample)[:250],
        )

    # ---- filters ---------------------------------------------------------
    status, cohort_a = h.call(
        "GET", f"{admin_analytics}/overall?cohort_id=cohort-a", token=h.admin_token
    )
    status_b, cohort_b = h.call(
        "GET", f"{admin_analytics}/overall?cohort_id=cohort-b", token=h.admin_token
    )
    h.check("the cohort filter is accepted", status == 200 and status_b == 200)
    h.check(
        "and it actually narrows the population",
        (cohort_a.get("unique_learners") or 0) <= 1 and (cohort_b.get("unique_learners") or 0) <= 1,
        f"a={cohort_a.get('unique_learners')} b={cohort_b.get('unique_learners')}",
    )
    h.check(
        "the two cohorts together account for every learner",
        (cohort_a.get("attempt_volume") or 0) + (cohort_b.get("attempt_volume") or 0)
        == overall.get("attempt_volume"),
        f"{cohort_a.get('attempt_volume')} + {cohort_b.get('attempt_volume')} "
        f"vs {overall.get('attempt_volume')}",
    )

    status, formal_only = h.call(
        "GET",
        f"{admin_analytics}/overall?assessment_type=FORMAL_ASSESSMENT",
        token=h.admin_token,
    )
    h.check("the assessment-type filter is accepted", status == 200, json.dumps(formal_only)[:200])
    formal_attempts = _rows(
        h, "SELECT COUNT(*) FROM qd_attempts WHERE is_formal_assessment = 1"
    )[0][0]
    h.check(
        "and reads UC-09's flag on the attempt",
        formal_only.get("attempt_volume") == formal_attempts,
        f"reported={formal_only.get('attempt_volume')} actual={formal_attempts}",
    )

    # ---- CSV export ------------------------------------------------------
    status, csv_body = h.call(
        "GET", f"{admin_analytics}/exports/overall.csv", token=h.admin_token
    )
    h.check("the overall CSV export returns 200", status == 200, str(status)[:100])
    h.check(
        "the export is CSV with a header row",
        isinstance(csv_body, str) and "," in csv_body.splitlines()[0],
        str(csv_body)[:150],
    )
    if isinstance(csv_body, str):
        parsed = list(csv.reader(io.StringIO(csv_body)))
        h.check("the CSV parses as CSV", len(parsed) >= 2, str(len(parsed)))

    status, questions_csv = h.call(
        "GET", f"{admin_analytics}/exports/questions.csv", token=h.admin_token
    )
    h.check("the questions CSV export returns 200", status == 200)

    # ---- the empty state is not a row of zeros ---------------------------
    status, empty = h.call(
        "GET", f"{admin_analytics}/courses/99999/overall", token=h.admin_token
    )
    h.check("a course with no attempts answers 200", status == 200, str(status))
    h.check(
        "and labels it as having no attempts rather than as a measurement",
        empty.get("data_state") == "NO_ATTEMPTS",
        json.dumps(empty)[:250],
    )
    h.check(
        "and reports null rates rather than zeros a reader would take as measured",
        empty.get("average_score") is None
        and empty.get("pass_rate") is None
        and empty.get("completion_rate") is None,
        json.dumps(empty)[:250],
    )

    # ---- flags and the review workflow -----------------------------------
    status, evaluated = h.call(
        "POST", f"{admin_analytics}/questions/flags/evaluate", {}, token=h.admin_token
    )
    h.check("flag evaluation runs (200/201)", status in (200, 201), json.dumps(evaluated)[:250])

    status, flagged = h.call(
        "GET", f"{admin_analytics}/questions/flagged", token=h.admin_token
    )
    h.check("the flagged panel returns 200", status == 200, json.dumps(flagged)[:200])
    flagged_items = flagged.get("questions", flagged.get("items", []))
    h.check(
        "the flagged panel is a list (empty is a valid answer)",
        isinstance(flagged_items, list),
        json.dumps(flagged)[:200],
    )

    if flagged_items:
        question_id = flagged_items[0].get("question_id") or flagged_items[0].get("questionId")
        status, action = h.call(
            "POST",
            f"{admin_analytics}/review/actions",
            {
                "question_id": question_id,
                "action": "NO_CHANGE",
                "note": "Checked during end-to-end verification.",
            },
            token=h.admin_token,
        )
        h.check("a review action is recorded", status in (200, 201), json.dumps(action)[:250])
        h.check(
            "and it is in the append-only audit table",
            len(_rows(h, "SELECT id FROM qy_review_actions WHERE question_id = ?", question_id)) >= 1,
        )
        status, history = h.call(
            "GET",
            f"{admin_analytics}/review/questions/{question_id}/history",
            token=h.admin_token,
        )
        h.check("the review history is readable", status == 200, json.dumps(history)[:200])

    # ---- analytics never exposes an answer key --------------------------
    blob = json.dumps(questions)
    h.check(
        "no analytics payload carries an answer-key field",
        '"isCorrect"' not in blob and '"is_correct"' not in blob and '"correctPosition"' not in blob,
        blob[:200],
    )

    # ---- a dangerous threshold needs explicit confirmation --------------
    status, sane = h.call(
        "POST",
        f"{admin_analytics}/config/validate",
        {"flag_wrong_answer_rate_threshold": 55, "flag_min_responses": 10},
        token=h.admin_token,
    )
    h.check("the configuration validator accepts a sane threshold", status == 200 and sane.get("valid") is True, json.dumps(sane)[:250])

    status, dangerous = h.call(
        "POST",
        f"{admin_analytics}/config/validate",
        {"flag_wrong_answer_rate_threshold": 0.1, "flag_min_responses": 1},
        token=h.admin_token,
    )
    h.check("the configuration validator answers", status == 200, json.dumps(dangerous)[:250])
    # A 0.1% threshold would flag virtually every question and make the review queue meaningless.
    # The requirement is not that it be refused outright — an administrator may have a reason — but
    # that it cannot be set *by accident*, so it must demand explicit confirmation.
    h.check(
        "and a 0.1% flag threshold requires explicit confirmation",
        dangerous.get("requires_confirmation") is True
        or "DANGEROUS" in json.dumps(dangerous).upper(),
        json.dumps(dangerous)[:350],
    )


# ---------------------------------------------------------------------------
# 33. Authorization with the guards switched ON
# ---------------------------------------------------------------------------


def section_33_authorization(h: Harness) -> None:
    """The deployed posture, which no other section here uses.

    Sections 1–32 run with ``ADMIN_API_TOKEN`` unset. That is the documented local-development
    mode, and it means ``require_admin`` admits an unauthenticated caller — so an authorization
    matrix run against that server would prove nothing about a deployment. A second server, same
    database, both tokens set, is the only honest way to assert it from outside the process.

    This is not a hypothetical distinction. F-02 in this merge was unauthenticated reads of the
    question bank, which serves ``isCorrect`` — the answer key — and the fix is inert while the
    token is unset.
    """
    h.section("33. Authorization, with ADMIN_API_TOKEN and SYSTEM_API_TOKEN configured")

    admin_token = "e2e-deployed-admin"
    system_token = "e2e-deployed-system"
    port = 8127
    base = f"http://127.0.0.1:{port}"

    env = dict(h.env)
    env.update(
        {
            "ADMIN_API_TOKEN": admin_token,
            "SYSTEM_API_TOKEN": system_token,
            # A genuinely deployed environment, not development with two extra variables. Both
            # tokens are set, so `Settings._require_credentials_outside_development` is satisfied —
            # and running as production is what makes the last check below mean anything: the demo
            # identity listing is keyed to `DEMO_IDENTITIES` *or* a development environment, so
            # asserting it is absent requires an environment that is neither.
            "ENVIRONMENT": "production",
            "DEMO_IDENTITIES": "false",
            # Same database as the rest of the run; this server exists only to change the guards.
            "AUTO_SEED": "false",
        }
    )

    log_path = h.tmp / "server-guarded.log"
    handle = log_path.open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=h.backend_dir,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        deadline = time.time() + 45
        up = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=3) as response:
                    if response.status == 200:
                        up = True
                        break
            except Exception:
                time.sleep(0.4)
        h.check("a second server starts with the guards configured", up)
        if not up:
            handle.flush()
            print(log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
            return

        def status_of(path: str, token: str | None) -> int:
            request = urllib.request.Request(f"{base}{path}", method="GET")
            if token:
                request.add_header("Authorization", f"Bearer {token}")
            # Deliberately also sent when unauthenticated: X-Admin-User is what the open
            # local-development path attributes writes to, so a guard that still honoured it would
            # be caught here rather than in production.
            request.add_header("X-Admin-User", "verify-script")
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                return exc.code

        # 1. Anonymous reaches nothing that carries data.
        for path in (
            "/api/question-bank/questions",
            "/api/question-bank/topics",
            "/api/admin/quizzes",
            "/api/admin/analytics/overall",
            "/api/admin/analytics/exports/overall.csv",
            "/api/assessor/pending-reviews",
            "/api/v1/results",
            "/api/v1/outcomes",
            "/api/v1/feedback",
            "/api/system/formal-assessments/review-queue/unpublished",
        ):
            code = status_of(path, None)
            h.check(f"anonymous GET {path} is refused", code == 401, f"got {code}")

        # 2. The health and metadata endpoints stay reachable — a probe has no credential.
        for path in ("/api/health", "/api/health/live", "/api/meta"):
            code = status_of(path, None)
            h.check(f"anonymous GET {path} still works (it is a probe)", code == 200, f"got {code}")

        # 3. A learner cannot cross into another role's surface.
        for path in (
            "/api/question-bank/questions",
            "/api/admin/quizzes",
            "/api/admin/analytics/overall",
            "/api/assessor/pending-reviews",
            "/api/system/formal-assessments/review-queue/unpublished",
        ):
            code = status_of(path, h.learner_token)
            h.check(f"learner GET {path} is forbidden", code == 403, f"got {code}")

        # 4. An administrator is not a learner. Results, outcomes and feedback belong to whoever
        #    sat the attempt, and an admin credential is refused rather than silently accepted.
        for path in ("/api/v1/results", "/api/v1/outcomes", "/api/v1/feedback"):
            code = status_of(path, admin_token)
            h.check(f"admin GET {path} is forbidden (learner-scoped)", code == 403, f"got {code}")

        # 5. An assessor is not an administrator.
        for path in ("/api/admin/analytics/overall", "/api/question-bank/questions"):
            code = status_of(path, h.assessor_token)
            h.check(f"assessor GET {path} is forbidden", code == 403, f"got {code}")

        # 6. Each role reaches its own surface.
        h.check(
            "the configured admin token reaches the question bank",
            status_of("/api/question-bank/questions", admin_token) == 200,
        )
        h.check(
            "the configured admin token reaches analytics",
            status_of("/api/admin/analytics/overall", admin_token) == 200,
        )
        h.check(
            "the assessor reaches the review queue",
            status_of("/api/assessor/pending-reviews", h.assessor_token) == 200,
        )
        h.check(
            "the system token reaches the system endpoints",
            status_of(
                "/api/system/formal-assessments/review-queue/unpublished", system_token
            )
            == 200,
        )
        h.check(
            "an assessor cannot reach the system endpoints",
            status_of(
                "/api/system/formal-assessments/review-queue/unpublished", h.assessor_token
            )
            == 403,
        )

        # 7. An unknown credential is unauthorized, not forbidden — the distinction matters to a
        #    client deciding whether to re-authenticate or give up.
        h.check(
            "an unknown token is 401, not 403",
            status_of("/api/question-bank/questions", "not-a-real-token") == 401,
        )

        # 8. The answer key is not reachable, and the refusal does not leak it either.
        request = urllib.request.Request(f"{base}/api/question-bank/questions", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
        h.check(
            "no answer-key field appears in the anonymous refusal",
            "isCorrect" not in body and "correctPosition" not in body,
            body[:200],
        )

        # 9. The demo identity listing is off unless it was deliberately switched on.
        request = urllib.request.Request(f"{base}/api/session", method="GET")
        with urllib.request.urlopen(request, timeout=15) as response:
            session = json.loads(response.read())
        h.check(
            "GET /api/session lists no tokens when DEMO_IDENTITIES is unset",
            "users" not in session or session.get("users") is None,
            json.dumps(session)[:200],
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        handle.close()


# ---------------------------------------------------------------------------
# 34. Database integrity, asserted against the database
# ---------------------------------------------------------------------------


def section_34_integrity(h: Harness) -> None:
    """The constraints, not the services that rely on them.

    Every rule here is one the application also enforces. That is the point: if the only thing
    stopping a second certificate is a service method, then a retry, a race or a future caller that
    forgets will produce one. These checks bypass the application entirely and ask the database.
    """
    h.section("34. Database integrity on the migrated database")

    def rejects(sql: str, params: tuple = (), *, expect: str = "") -> tuple[bool, str]:
        connection = sqlite3.connect(h.db_path, timeout=10)
        try:
            connection.execute(sql, params)
            connection.commit()
            return False, "the statement succeeded"
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            return (expect in message if expect else True), message
        finally:
            connection.close()

    # ---- foreign keys are actually enforced ------------------------------
    connection = sqlite3.connect(h.db_path, timeout=10)
    try:
        enforced = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        connection.close()
    # Off per connection by default in SQLite; the application turns it on. A raw connection
    # reporting 0 is expected and is exactly why this is checked with an explicit pragma below.
    h.check(
        "foreign keys are off on a bare connection (so the app's pragma is load-bearing)",
        enforced == 0,
        str(enforced),
    )

    connection = sqlite3.connect(h.db_path, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute(
                "INSERT INTO qd_attempt_questions "
                "(attempt_id, question_id, position, question_version, created_at) "
                "VALUES ('no-such-attempt', 'x', 1, 1, '2026-01-01 00:00:00')"
            )
            connection.commit()
            fk_rejected = False
        except sqlite3.DatabaseError:
            fk_rejected = True
    finally:
        connection.close()
    h.check("with the pragma on, a dangling attempt reference is rejected", fk_rejected)

    # ---- the CHECK constraint F-16 was about -----------------------------
    attempt = _rows(h, "SELECT id FROM qd_attempts LIMIT 1")
    if attempt:
        ok, message = rejects(
            "UPDATE qd_attempt_submissions SET submission_reason = 'NOT_A_REAL_REASON' "
            "WHERE attempt_id = ?",
            (attempt[0][0],),
            expect="CHECK",
        )
        h.check("an unknown submission reason is rejected by the database", ok, message[:150])

    h.check(
        "DISCONNECT_AUTO_SUBMIT is an accepted submission reason (F-16's regression)",
        len(
            _rows(
                h,
                "SELECT id FROM qd_attempt_submissions "
                "WHERE submission_reason = 'DISCONNECT_AUTO_SUBMIT'",
            )
        )
        >= 1,
    )

    # ---- immutability, per table ----------------------------------------
    # The expected marker is each trigger's own structured prefix, not a generic word. That
    # distinction is what stops a false pass: an UPDATE naming a column that does not exist also
    # raises a DatabaseError, and a check that accepted any error would report a missing trigger as
    # a working one. Every immutability trigger in this system carries an ``IMMUTABLE_<THING>:``
    # prefix precisely so it can be recognised without matching on prose.
    for table, column, marker in (
        ("qr_attempt_results", "percentage", "IMMUTABLE_ATTEMPT_RESULT"),
        ("qr_question_scores", "awarded_marks", "IMMUTABLE_QUESTION_SCORE"),
        ("qg_attempt_outcomes", "outcome", "IMMUTABLE_ATTEMPT_OUTCOME"),
        ("qf_feedback_reports", "status", "IMMUTABLE_FEEDBACK_REPORT"),
        ("qy_review_actions", "note", "IMMUTABLE_REVIEW_ACTION"),
        ("qc_configuration_versions", "pass_mark", "IMMUTABLE_CONFIGURATION_VERSION"),
    ):
        existing = _rows(h, f"SELECT COUNT(*) FROM {table}")[0][0]  # noqa: S608
        if not existing:
            h.check(f"{table} has rows to test immutability against", False, "table empty")
            continue
        ok, message = rejects(
            f"UPDATE {table} SET {column} = {column}",  # noqa: S608
            expect=marker,
        )
        h.check(
            f"the database refuses to edit {table}",
            ok,
            message[:150] if not ok else "",
        )

    # ---- uniqueness that carries a business rule ------------------------
    #
    # Searched in the table DDL as well as the index list, because SQLAlchemy renders some of these
    # as a named ``UNIQUE`` *table constraint* and others as a partial unique *index*. A named table
    # constraint is enforced by an implicit ``sqlite_autoindex_...`` that never appears under its own
    # name, so looking only at the index list reports a live constraint as missing — which is what
    # this check did on its first run, for ``ux_submission_idempotency``.
    with h.readonly_db(h.db_path) as conn:
        schema_text = "\n".join(
            row[0] or "" for row in conn.execute("SELECT sql FROM sqlite_master")
        )
    for constraint, rule in (
        ("ux_attempt_single_open", "one open attempt per learner and quiz"),
        ("ux_submission_single_success", "at most one successful submission per attempt"),
        ("ux_submission_idempotency", "a retried submission collapses onto the first"),
        ("ux_retake_attempt_slot", "one retake per attempt slot"),
        ("ux_retake_idempotency", "a retried retake request creates one retake"),
        ("ux_qg_certificate_single_issued", "one issued certificate per learner and quiz"),
        ("ux_formal_attempt_open", "one open formal attempt per learner and quiz"),
        ("ux_device_session_active", "one active device per formal attempt"),
        ("ux_formal_review_attempt", "one review per formal attempt"),
    ):
        h.check(
            f"the migrated schema enforces: {rule} ({constraint})",
            constraint in schema_text,
        )

    # And enforced, not merely declared. A duplicate idempotency key is the retry case, so it is
    # the one worth proving behaviourally rather than by reading the schema.
    submission = _rows(
        h, "SELECT attempt_id, idempotency_key FROM qd_attempt_submissions LIMIT 1"
    )
    if submission:
        attempt_id, key = submission[0]
        ok, message = rejects(
            "INSERT INTO qd_attempt_submissions "
            "(id, attempt_id, idempotency_key, request_fingerprint, state, submission_reason, "
            " attempt_count, answered_count, total_questions, requested_at, last_attempted_at) "
            "VALUES ('dup-probe', ?, ?, 'x', 'PENDING', 'LEARNER_CONFIRMED', 1, 0, 0, "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (attempt_id, key),
            expect="UNIQUE",
        )
        h.check("a duplicate submission idempotency key is rejected by the database", ok, message[:150])

    # ---- one certificate per learner and quiz, enforced below the service
    duplicates = _rows(
        h,
        "SELECT learner_id, quiz_id, COUNT(*) FROM qg_certificates "
        "WHERE status = 'ISSUED' GROUP BY learner_id, quiz_id HAVING COUNT(*) > 1",
    )
    h.check("no learner holds two certificates for one quiz", duplicates == [], str(duplicates))

    # ---- every attempt is locked to a version that still exists ---------
    orphans = _rows(
        h,
        "SELECT a.id FROM qd_attempts a "
        "LEFT JOIN qc_configuration_versions v ON v.id = a.configuration_version_id "
        "WHERE v.id IS NULL",
    )
    h.check("every attempt's locked configuration version still exists", orphans == [], str(orphans))

    # ---- results agree with the attempts they describe -------------------
    mismatched = _rows(
        h,
        "SELECT r.attempt_id FROM qr_attempt_results r "
        "JOIN qd_attempts a ON a.id = r.attempt_id "
        "WHERE r.configuration_version_id <> a.configuration_version_id",
    )
    h.check(
        "every result cites the same configuration version as its attempt",
        mismatched == [],
        str(mismatched),
    )
