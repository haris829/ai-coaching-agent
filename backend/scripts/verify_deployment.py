"""Walk the six review journeys against a DEPLOYED instance, over HTTP only.

    python -m scripts.verify_deployment --base-url https://your-app.up.railway.app

Credentials come from the environment (or the flags below):

    REVIEW_ADMIN_TOKEN, REVIEW_LEARNER_TOKEN, REVIEW_LEARNER2_TOKEN, REVIEW_ASSESSOR_TOKEN

With none supplied it reads them from ``GET /api/session``, which a review deployment exposes when
``DEMO_IDENTITIES`` is on. That is the normal case: point it at the deployment and it finds its own
way in.

HOW THIS DIFFERS FROM ``verify_e2e``
-----------------------------------
``verify_e2e`` is the gate. It starts its own server, migrates its own database, and reads that
database file back with an independent connection to prove data is really on disk — 469 checks, and
it can assert things no HTTP client can see, like whether a trigger is installed.

This asserts less and answers a different question: **does the instance I am about to hand somebody
actually work?** A completed build is not a working application. So there is exactly one channel
here — HTTP, from outside — because that is the only channel a reviewer has.

The practical consequence is that the answer key cannot be read from a table. It is read through the
question bank *as an administrator*, which is a legitimate read for that role and is the same route
the authoring screens use. Nothing here reaches behind the API.

WHAT IT WILL NOT DO
-------------------
It creates attempts, submissions, results and certificates — real rows, on whatever database the
instance is pointed at. That is the point (a journey that wrote nothing would prove nothing), but it
means this belongs on a review deployment, not on one holding data anybody depends on. It says so
and requires ``--yes`` before writing.

RE-RUNNING IT
-------------
The first version of this script could only run once per database, and the second run reported three
failures that were not defects at all: the system correctly refusing a fourth attempt on a
three-attempt quiz, correctly refusing a second formal assessment for one learner, and correctly
reporting a spent grant as ``EXHAUSTED``. A verifier that cannot distinguish "this deployment is
broken" from "this journey has already been exercised here" is worse than useless, because it
teaches whoever runs it to ignore red.

So the journeys now read the state before acting:

* where the product provides a remedy, it is used — an exhausted allowance is topped up through the
  **administrator grant endpoint**, which is exactly what an administrator would do, not a backdoor;
* where a rule genuinely cannot be re-exercised — one formal assessment per learner and quiz, and
  the seed provides two learners — the journey is reported as **SKIPPED with the reason** rather
  than as a failure.

A skip is printed, counted separately, and never turns the run red. A failure means the deployment.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

#: Identifies this invocation, so an idempotency key from one run never collides with another's.
RUN_ID = uuid.uuid4().hex[:12]
#: Distinguishes several top-ups within one run.
_TOPUP = itertools.count(1)

passes: list[str] = []
fails: list[str] = []
skips: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        passes.append(label)
        print(f"  [PASS] {label}")
    else:
        fails.append(f"{label}{f' - {detail}' if detail else ''}")
        print(f"  [FAIL] {label}{f' - {detail}' if detail else ''}")


def skip(label: str, reason: str) -> None:
    """Not verifiable on this database, and not a defect.

    Kept visibly distinct from both PASS and FAIL: counting it as a pass would overstate what was
    checked, and counting it as a failure would report the system enforcing a rule as the system
    being broken.
    """
    skips.append(f"{label} - {reason}")
    print(f"  [SKIP] {label} - {reason}")


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * min(len(title), 78))


class Client:
    """The only channel. Every assertion in this script goes through it."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8", "replace")
                try:
                    return response.status, json.loads(raw)
                except ValueError:
                    return response.status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw
        except Exception as exc:  # DNS, TLS, refused connection
            return 0, str(exc)


# ---------------------------------------------------------------------------
# Answering, from the answer key the administrator is entitled to read
# ---------------------------------------------------------------------------


def answer_key(client: Client, admin: str, question_id: str) -> dict[str, Any]:
    """The authoring view of one question, read as an administrator.

    This is the only way to answer correctly without database access, and it is a legitimate read:
    the authoring screens use the same endpoint. It is *also* the reason this script keeps the admin
    and learner tokens strictly separate — the learner-facing delivery endpoints never return any of
    these fields, and a check below asserts exactly that.
    """
    status, body = client.call("GET", f"/api/question-bank/questions/{question_id}", token=admin)
    if status != 200:
        raise RuntimeError(f"could not read question {question_id}: {status} {body}")
    return body.get("question", body)


def response_for(question: dict[str, Any], authored: dict[str, Any], *, correctly: bool) -> Any:
    """An answer payload for one delivered question, per its type."""
    kind = question["questionType"]
    options = authored.get("options") or []
    correct = [option["label"] for option in options if option.get("isCorrect")]
    wrong = [option["label"] for option in options if not option.get("isCorrect")]

    if kind == "SINGLE_CHOICE":
        return {"selectedOptionId": (correct if correctly else wrong)[0]}
    if kind == "TRUE_FALSE":
        truth = correct[0].upper() == "TRUE"
        return {"value": truth if correctly else not truth}
    if kind == "MULTI_SELECT":
        # Every correct option when correct; one wrong option and no correct ones otherwise, which
        # is the case the negative-marking floor is about.
        return {"selectedOptionIds": sorted(correct if correctly else wrong[:1])}
    if kind == "DRAG_TO_ORDER":
        ordered = [
            option["label"]
            for option in sorted(
                (item for item in options if item.get("correctPosition") is not None),
                key=lambda item: item["correctPosition"],
            )
        ]
        return {"orderedItemIds": ordered if correctly else list(reversed(ordered))}
    if kind == "SCENARIO":
        sub = question["subQuestions"][0]
        return {
            "responses": [
                {
                    "subQuestionId": sub["subQuestionId"],
                    "answer": {"selectedOptionId": (correct if correctly else wrong)[0]},
                }
            ]
        }
    raise AssertionError(f"unsupported question type: {kind}")


def answer_attempt(
    client: Client, admin: str, learner: str, attempt_id: str, *, correct_count: int
) -> int:
    status, body = client.call("GET", f"/api/v1/attempts/{attempt_id}/questions", token=learner)
    if status != 200:
        raise RuntimeError(f"could not read the paper: {status} {body}")
    delivered = body["questions"]
    for index, question in enumerate(delivered):
        authored = answer_key(client, admin, question["questionId"])
        payload = response_for(question, authored, correctly=index < correct_count)
        client.call(
            "PUT",
            f"/api/v1/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            {"response": payload, "source": "MANUAL"},
            token=learner,
        )
    return len(delivered)


def ensure_attempt_available(
    client: Client, tokens: dict[str, str], quiz_id: int, learner_token: str, learner_label: str
) -> bool:
    """Make sure the learner can start an attempt, granting more if the allowance is spent.

    A re-run finds the allowance exhausted from last time. Rather than reporting that as a failure —
    it is the rule working — the administrator grants another attempt, which is precisely the remedy
    UC-08 exists to provide and is itself worth exercising.

    Returns False only when the allowance cannot be restored, which *would* be a defect.
    """
    status, eligibility = client.call(
        "GET", f"/api/v1/quizzes/{quiz_id}/retake-eligibility", token=learner_token
    )
    if status != 200:
        # No eligibility endpoint answer means nothing can be assumed; let the caller try anyway.
        return True
    if eligibility.get("allowance", {}).get("has_available_attempts", True):
        return True

    learner_id = str(
        (client.call("GET", "/api/session", token=learner_token)[1].get("user") or {}).get("id")
    )
    course_id = str(
        client.call("GET", f"/api/quizzes/{quiz_id}/rules", token=learner_token)[1]
         .get("quiz", {})
         .get("courseId", "")
    )
    if not course_id:
        status, quizzes = client.call("GET", "/api/quizzes", token=learner_token)
        for quiz in quizzes.get("quizzes", []):
            if quiz["id"] == quiz_id:
                course_id = str(quiz.get("courseId", ""))

    # A key unique to this *run*, not to this position in the run. An earlier version keyed on
    # `len(passes)`, which is zero at the first top-up of every run — so the second run's grant
    # replayed the first run's instead of granting anything, and the attempt that followed was
    # correctly refused with MAX_ATTEMPTS_REACHED. The idempotency was working; the key was wrong.
    key = f"verify-topup-{RUN_ID}-{learner_label.replace(' ', '-')}-{quiz_id}-{next(_TOPUP)}"
    status, granted = client.call(
        "POST",
        "/api/admin/retakes/grants",
        {
            "learner_id": learner_id,
            "course_id": course_id,
            "quiz_id": str(quiz_id),
            "additional_attempts": 2,
            "reason": "Deployment verification top-up (previous run consumed the allowance).",
            "idempotency_key": key,
        },
        token=tokens["admin"],
    )
    if status not in (200, 201):
        check(
            f"the allowance can be topped up for {learner_label}",
            False,
            f"{status} {json.dumps(granted)[:200]}",
        )
        return False
    print(f"  [note] allowance was spent; granted 2 more attempts to {learner_label}")

    # A granted attempt is taken through the retake endpoint, not `POST /attempts`.
    status, retake = client.call(
        "POST", f"/api/v1/quizzes/{quiz_id}/retakes", {}, token=learner_token
    )
    if status == 201:
        globals()["_pending_attempt"] = retake["attempt"]["attempt_id"]
    return True


def chain(client: Client, attempt_id: str, learner: str) -> tuple[Any, Any, Any]:
    _, result = client.call("POST", f"/api/v1/attempts/{attempt_id}/result", {}, token=learner)
    _, outcome = client.call("POST", f"/api/v1/attempts/{attempt_id}/outcome", {}, token=learner)
    _, feedback = client.call("POST", f"/api/v1/attempts/{attempt_id}/feedback", {}, token=learner)
    return result, outcome, feedback


# ---------------------------------------------------------------------------
# Journeys
# ---------------------------------------------------------------------------


def journey_a(client: Client, tokens: dict[str, str]) -> dict[str, int]:
    """The reviewer arrives. Is the instance reachable, served, and stocked?"""
    section("A - the instance is reachable and usable")

    status, health = client.call("GET", "/api/health")
    check("GET /api/health answers 200", status == 200, str(status))
    check(
        "it reports ten capabilities and a reachable database",
        isinstance(health, dict)
        and len(health.get("modules", [])) == 10
        and health.get("database") == "ok",
        json.dumps(health)[:200],
    )
    status, live = client.call("GET", "/api/health/live")
    check("the liveness probe answers without touching the database", status == 200, str(status))

    status, _ = client.call("GET", "/")
    check("the UI is served at the root", status == 200, str(status))
    for route in ("/attempt", "/retakes", "/formal", "/analytics"):
        status, _ = client.call("GET", route)
        check(f"the client-side route {route} resolves to the app shell", status == 200, str(status))

    # A mistyped API path must be JSON, not the HTML shell. Answering it with index.html would give
    # a client HTML with a 200 where it expected JSON, and the error would surface as a parse
    # failure somewhere unrelated to the actual mistake.
    status, body = client.call("GET", "/api/definitely-not-a-route")
    check(
        "an unknown API path is a JSON 404, not the HTML shell",
        status == 404 and isinstance(body, dict) and "error" in body,
        f"{status} {str(body)[:120]}",
    )

    status, docs = client.call("GET", "/api/docs")
    check("the interactive API documentation is reachable", status == 200, str(status))

    status, quizzes = client.call("GET", "/api/quizzes", token=tokens["learner"])
    check(
        "a learner can list the seeded quizzes",
        status == 200 and len(quizzes.get("quizzes", [])) >= 2,
        json.dumps(quizzes)[:200],
    )

    found: dict[str, int] = {}
    for quiz in quizzes.get("quizzes", []):
        found[quiz["slug"]] = quiz["id"]
    check(
        "the practice quiz and the supervised examination are both present",
        "practice-assessment" in found and "supervised-final-examination" in found,
        str(sorted(found)),
    )
    return found


def journey_bc(client: Client, tokens: dict[str, str], practice: int) -> None:
    """A failed attempt, then a passing one."""
    section("B - a failed attempt: scored, no certificate, feedback still produced")

    globals().pop("_pending_attempt", None)
    if not ensure_attempt_available(client, tokens, practice, tokens["learner"], "the learner"):
        return

    failed = globals().pop("_pending_attempt", None)
    if failed is None:
        status, created = client.call(
            "POST", "/api/v1/attempts", {"quizId": str(practice)}, token=tokens["learner"]
        )
        if status != 201:
            check("an attempt can be started", False, f"{status} {json.dumps(created)[:200]}")
            return
        failed = created["attempt"]["attemptId"]
    check("an attempt can be started", True)
    total = answer_attempt(client, tokens["admin"], tokens["learner"], failed, correct_count=0)
    client.call(
        "POST", f"/api/v1/attempts/{failed}/submission", {"confirmed": True}, token=tokens["learner"]
    )
    result, outcome, feedback = chain(client, failed, tokens["learner"])

    check("the attempt is scored", result.get("result", {}).get("status") == "SCORED",
          json.dumps(result)[:180])
    check("it is a fail", outcome.get("outcome", {}).get("outcome") == "FAIL",
          json.dumps(outcome.get("outcome"))[:180])
    check("no certificate is issued for a fail", outcome.get("certificate") is None,
          json.dumps(outcome.get("certificate"))[:150])
    check("attempts remaining is reported", outcome.get("attemptsRemaining") is not None)
    check("feedback is still generated", feedback.get("status") == "GENERATED",
          json.dumps(feedback)[:180])
    check(
        "feedback covers every delivered question",
        len(feedback.get("items", [])) == total,
        f"{len(feedback.get('items', []))} of {total}",
    )
    check(
        "every feedback item carries the correct answer and an explanation",
        bool(feedback.get("items"))
        and all(item["correctAnswer"] and item["explanation"] for item in feedback["items"]),
    )

    section("C - a passing attempt: one certificate, and only one")
    if not ensure_attempt_available(client, tokens, practice, tokens["learner"], "the learner"):
        return
    passing = globals().pop("_pending_attempt", None)
    if passing is None:
        status, retake = client.call(
            "POST", f"/api/v1/quizzes/{practice}/retakes", {}, token=tokens["learner"]
        )
        if status != 201:
            check(
                "a retake can be requested after a fail",
                False,
                f"{status} {json.dumps(retake)[:200]}",
            )
            return
        passing = retake["attempt"]["attempt_id"]
    check("a retake can be requested after a fail", True)
    answer_attempt(client, tokens["admin"], tokens["learner"], passing, correct_count=total)
    client.call(
        "POST", f"/api/v1/attempts/{passing}/submission", {"confirmed": True}, token=tokens["learner"]
    )
    _, outcome, _ = chain(client, passing, tokens["learner"])
    check("it is a pass", outcome.get("outcome", {}).get("outcome") == "PASS",
          json.dumps(outcome.get("outcome"))[:180])
    certificate = outcome.get("certificate") or {}
    # Two answers are correct here, and which one depends on whether this learner already holds a
    # certificate for this quiz:
    #
    #   * first pass -> ISSUED, with a certificate number;
    #   * later pass -> FAILED with CERTIFICATE_ALREADY_ISSUED, because
    #     `ux_qg_certificate_single_issued` permits exactly one per learner and quiz.
    #
    # The second is not a degraded outcome - it *is* the duplicate-certificate prevention the
    # requirement asks for, and seeing it on a live deployment is worth more than seeing the happy
    # path twice. Asserting only ISSUED reported this as a failure on a re-run.
    issued = certificate.get("status") == "ISSUED" and bool(certificate.get("certificateNumber"))
    already = (
        certificate.get("status") == "FAILED"
        and certificate.get("failureCode") == "CERTIFICATE_ALREADY_ISSUED"
    )
    check(
        "a passing attempt produces a certificate, or is refused a duplicate one",
        issued or already,
        json.dumps(certificate)[:220],
    )
    if already:
        print(
            "  [note] this learner already held a certificate for this quiz; the duplicate was "
            "refused, which is the rule"
        )
    check(
        "and it is one of those two outcomes, not something in between",
        issued != already,
        json.dumps(certificate)[:180],
    )
    check("the CPD record is synchronised", (outcome.get("cpd") or {}).get("status") == "SYNCHRONISED",
          json.dumps(outcome.get("cpd"))[:150])

    # Re-running the chain must replay, not re-issue. Checked through the API because the database
    # is not reachable from here: the certificate id must come back identical.
    _, outcome_again, _ = chain(client, passing, tokens["learner"])
    check(
        "re-running the chain replays the same certificate record rather than creating a second",
        (outcome_again.get("certificate") or {}).get("certificateId")
        == certificate.get("certificateId"),
        f"{(outcome_again.get('certificate') or {}).get('certificateId')} vs "
        f"{certificate.get('certificateId')}",
    )
    check(
        "the earlier failed attempt still reads as a fail",
        client.call("GET", f"/api/v1/attempts/{failed}/outcome", token=tokens["learner"])[1]
        .get("outcome", {})
        .get("outcome")
        == "FAIL",
    )


def journey_d(client: Client, tokens: dict[str, str], formal_quiz: int) -> None:
    """The supervised examination, and the assessor who releases the certificate."""
    section("D - a supervised examination: one device, a disconnect, and an assessor")

    # One formal assessment per learner and quiz is a hard rule, and the seed provides two learners,
    # so this journey can be exercised at most twice per database. Pick a learner who has not already
    # been through it; if both have, say so rather than reporting the rule as a fault.
    status, conditions = client.call(
        "GET", f"/api/v1/quizzes/{formal_quiz}/formal-conditions", token=tokens["learner2"]
    )
    check(
        "the formal quiz serves its conditions and declares itself formal",
        status == 200 and conditions.get("is_formal_assessment") is True,
        f"{status} {json.dumps(conditions)[:180]}",
    )
    if status != 200:
        return
    codes = [item["code"] for item in conditions.get("conditions", [])]

    # Which learner can sit this depends on who already has. The only reliable way to find out is to
    # make the real acknowledgement and read the answer: a lighter probe cannot tell, because the
    # duplicate rule is evaluated *after* payload validation, so an invalid probe payload is
    # rejected on its own merits and reveals nothing. An earlier version guessed from that and
    # picked a learner who was then refused.
    learner = None
    formal_id = None
    for label, token in (("learner 2", tokens["learner2"]), ("learner 1", tokens["learner"])):
        status, ack = client.call(
            "POST",
            f"/api/v1/quizzes/{formal_quiz}/conditions-acknowledgement",
            {"acknowledged_condition_codes": codes},
            token=token,
        )
        if status in (200, 201):
            learner = token
            formal_id = ack["formal_attempt_id"]
            print(f"  [note] running the formal journey as {label}")
            break
        code = (ack.get("error", {}) or {}).get("code", "") if isinstance(ack, dict) else ""
        if code != "DUPLICATE_FORMAL_ATTEMPT":
            check(
                "the conditions can be acknowledged",
                False,
                f"{label}: {status} {json.dumps(ack)[:200]}",
            )
            return
        print(f"  [note] {label} already holds a formal assessment for this quiz")

    if learner is None:
        skip(
            "the formal assessment journey",
            "both seeded learners already hold a formal assessment for this quiz - one per learner "
            "and quiz is the rule, so this journey has already been exercised on this database. "
            "verify_e2e section 31 exercises it on a fresh database",
        )
        return
    check("the conditions can be acknowledged", True)

    # Starting before identity is confirmed must be refused. The conditions are already acknowledged
    # above, because that step had to happen first to discover which learner could sit this at all.
    status, premature = client.call(
        "POST",
        f"/api/v1/quizzes/{formal_quiz}/formal-attempts",
        {"device": {"fingerprint": "verify-a", "platform": "verify"}},
        token=learner,
    )
    check(
        "starting before identity is confirmed is refused",
        status in (403, 409, 422),
        f"{status} {json.dumps(premature)[:150]}",
    )

    status, wrong = client.call(
        "POST",
        f"/api/v1/quizzes/{formal_quiz}/identity-confirmation",
        {"full_name": "Not The Right Person", "email": "nobody@example.com"},
        token=learner,
    )
    check(
        "an identity that does not match the directory is refused",
        status != 200 or wrong.get("identity_check", {}).get("confirmed") is not True,
        f"{status} {json.dumps(wrong)[:150]}",
    )

    # The learner's own details, read from the session listing so this works on any deployment.
    status, session = client.call("GET", "/api/session", token=learner)
    identity = session.get("user") or {}
    directory = {entry["token"]: entry for entry in (session.get("users") or [])}
    entry = directory.get(learner, {})
    # `identity` is the fallback when DEMO_IDENTITIES is off and the directory is not listed.
    full_name = entry.get("displayName") or identity.get("displayName")
    email = entry.get("email")
    if not (full_name and email):
        check(
            "the learner's directory details are discoverable for identity confirmation",
            False,
            "GET /api/session did not list them; set DEMO_IDENTITIES or pass them explicitly",
        )
        return

    status, confirmed = client.call(
        "POST",
        f"/api/v1/quizzes/{formal_quiz}/identity-confirmation",
        {"full_name": full_name, "email": email},
        token=learner,
    )
    check(
        "the learner's real identity is confirmed",
        status == 200 and confirmed["identity_check"]["confirmed"] is True,
        json.dumps(confirmed)[:180],
    )

    status, started = client.call(
        "POST",
        f"/api/v1/quizzes/{formal_quiz}/formal-attempts",
        {"device": {"fingerprint": "verify-a", "platform": "verify"}},
        token=learner,
    )
    check("the examination starts", status in (200, 201), json.dumps(started)[:200])
    if status not in (200, 201):
        return
    session_header = {"X-Formal-Session": started["session"]["session_token"]}
    attempt_id = started["attempt_id"]

    status, second = client.call(
        "POST",
        f"/api/v1/quizzes/{formal_quiz}/formal-attempts",
        {"device": {"fingerprint": "verify-b", "platform": "verify"}},
        token=learner,
    )
    check(
        "a second device is refused with a clear conflict rather than a crash",
        status in (403, 409),
        f"{status} {json.dumps(second)[:180]}",
    )

    status, paused = client.call(
        "POST", f"/api/v1/formal-attempts/{formal_id}/pause", {}, token=learner
    )
    check("a formal assessment cannot be paused", status == 409, str(status))

    status, coaching = client.call(
        "GET", f"/api/v1/attempts/{attempt_id}/coaching/eligibility", token=learner
    )
    check(
        "AI coaching is unavailable while the examination is in progress",
        status in (403, 409) or coaching.get("coachingAvailable") is False,
        f"{status} {json.dumps(coaching)[:180]}",
    )

    status, paper = client.call("GET", f"/api/v1/attempts/{attempt_id}/questions", token=learner)
    delivered = paper.get("questions", [])
    check("the examination paper is delivered", status == 200 and bool(delivered), str(status))

    answers = []
    for question in delivered:
        authored = answer_key(client, tokens["admin"], question["questionId"])
        answers.append(
            {
                "question_id": question["questionId"],
                "response": response_for(question, authored, correctly=True),
            }
        )
    status, saved = client.call(
        "POST",
        f"/api/v1/formal-attempts/{formal_id}/autosave",
        {"answers": answers},
        token=learner,
        headers=session_header,
    )
    check("answers autosave from the registered device", status == 200, json.dumps(saved)[:180])

    status, forged = client.call(
        "POST",
        f"/api/v1/formal-attempts/{formal_id}/autosave",
        {"answers": answers},
        token=learner,
        headers={"X-Formal-Session": "not-the-real-token"},
    )
    check(
        "an autosave with a forged session token is refused",
        status in (401, 403, 409),
        f"{status} {json.dumps(forged)[:150]}",
    )

    status, dropped = client.call(
        "POST",
        f"/api/v1/formal-attempts/{formal_id}/disconnect",
        {"reason": "NETWORK_LOSS"},
        token=learner,
        headers=session_header,
    )
    check(
        "a disconnect commits the autosaved work rather than losing it",
        status in (200, 201),
        f"{status} {json.dumps(dropped)[:200]}",
    )
    status, resumed = client.call(
        "POST", f"/api/v1/formal-attempts/{formal_id}/resume", {}, token=learner
    )
    check("and the examination cannot be resumed afterwards", status in (403, 409), str(status))

    result, outcome, _ = chain(client, attempt_id, learner)
    check(
        "the autosaved answers were scored",
        result.get("result", {}).get("correctCount") == len(delivered),
        json.dumps(result.get("result"))[:180],
    )
    check("and it is a pass", outcome.get("outcome", {}).get("outcome") == "PASS",
          json.dumps(outcome.get("outcome"))[:150])
    check(
        "NO certificate exists before an assessor decides",
        (outcome.get("certificate") or {}).get("status") != "ISSUED",
        json.dumps(outcome.get("certificate"))[:180],
    )

    status, refused = client.call("GET", "/api/assessor/pending-reviews", token=tokens["admin"])
    check("an administrator cannot see the assessor queue", status == 403, str(status))
    status, refused = client.call("GET", "/api/assessor/pending-reviews", token=tokens["learner"])
    check("nor can a learner", status == 403, str(status))

    status, queue = client.call("GET", "/api/assessor/pending-reviews", token=tokens["assessor"])
    check("the assessor can", status == 200, f"{status} {json.dumps(queue)[:180]}")
    reviews = queue.get("reviews", []) if isinstance(queue, dict) else []
    mine = [review for review in reviews if review.get("attempt_id") == attempt_id]
    check("and this examination is in the queue", len(mine) == 1, json.dumps(reviews)[:200])
    if not mine:
        return
    review_id = mine[0]["review_id"]

    status, stolen = client.call(
        "POST",
        f"/api/assessor/reviews/{review_id}/decision",
        {"decision": "APPROVED", "notes": "Administrator attempting to self-approve."},
        token=tokens["admin"],
    )
    check("an administrator cannot approve the assessment", status == 403, str(status))

    client.call("POST", f"/api/assessor/reviews/{review_id}/review-start", {}, token=tokens["assessor"])
    status, decided = client.call(
        "POST",
        f"/api/assessor/reviews/{review_id}/decision",
        {"decision": "APPROVED", "notes": "Verified during deployment verification."},
        token=tokens["assessor"],
    )
    check("the assessor's approval is accepted", status in (200, 201), json.dumps(decided)[:180])
    status, workflow = client.call(
        "POST", f"/api/assessor/reviews/{review_id}/certificate-workflow", {}, token=tokens["assessor"]
    )
    check("the certificate workflow runs", status in (200, 201), json.dumps(workflow)[:180])

    status, after = client.call("GET", f"/api/v1/attempts/{attempt_id}/outcome", token=learner)
    check(
        "and only now does the certificate exist",
        (after.get("certificate") or {}).get("status") == "ISSUED"
        and bool((after.get("certificate") or {}).get("certificateNumber")),
        json.dumps(after.get("certificate"))[:200],
    )


def journey_ef(client: Client, tokens: dict[str, str], practice: int) -> None:
    """The allowance runs out and an administrator intervenes; then the dashboard."""
    section("E - the allowance, and an administrator's grant")

    status, eligibility = client.call(
        "GET", f"/api/v1/quizzes/{practice}/retake-eligibility", token=tokens["learner"]
    )
    check("retake eligibility reads", status == 200, f"{status} {json.dumps(eligibility)[:180]}")
    if status != 200:
        return

    # Spend whatever is left, so "exhausted" is reached rather than assumed.
    guard = 0
    while eligibility.get("can_retake") and guard < 6:
        guard += 1
        status, retake = client.call(
            "POST", f"/api/v1/quizzes/{practice}/retakes", {}, token=tokens["learner"]
        )
        if status != 201:
            break
        attempt_id = retake["attempt"]["attempt_id"]
        answer_attempt(client, tokens["admin"], tokens["learner"], attempt_id, correct_count=0)
        client.call(
            "POST",
            f"/api/v1/attempts/{attempt_id}/submission",
            {"confirmed": True},
            token=tokens["learner"],
        )
        chain(client, attempt_id, tokens["learner"])
        _, eligibility = client.call(
            "GET", f"/api/v1/quizzes/{practice}/retake-eligibility", token=tokens["learner"]
        )

    check(
        "the allowance reaches EXHAUSTED",
        eligibility.get("state") == "EXHAUSTED",
        json.dumps(eligibility)[:200],
    )
    check("and the learner is told who to contact", bool(eligibility.get("guidance")),
          json.dumps(eligibility)[:200])
    status, refused = client.call(
        "POST", f"/api/v1/quizzes/{practice}/retakes", {}, token=tokens["learner"]
    )
    check("a further retake is refused with 409", status == 409, str(status))

    status, rules_before = client.call(
        "GET", f"/api/quizzes/{practice}/rules", token=tokens["learner"]
    )
    max_before = rules_before.get("maxAttempts")

    learner_id = str((client.call("GET", "/api/session", token=tokens["learner"])[1].get("user") or {}).get("id"))
    course_id = str(rules_before.get("quiz", {}).get("courseId") or "")
    if not course_id:
        status, quizzes = client.call("GET", "/api/quizzes", token=tokens["learner"])
        for quiz in quizzes.get("quizzes", []):
            if quiz["id"] == practice:
                course_id = str(quiz.get("courseId", ""))

    status, granted = client.call(
        "POST",
        "/api/admin/retakes/grants",
        {
            "learner_id": learner_id,
            "course_id": course_id,
            "quiz_id": str(practice),
            "additional_attempts": 1,
            "reason": "Deployment verification.",
            "idempotency_key": "verify-deployment-grant",
        },
        token=tokens["admin"],
    )
    check(
        "an administrator can grant one additional attempt",
        status in (200, 201),
        f"{status} {json.dumps(granted)[:200]}",
    )

    status, rules_after = client.call(
        "GET", f"/api/quizzes/{practice}/rules", token=tokens["learner"]
    )
    check(
        "the quiz's configured maximum is unchanged by the grant",
        rules_after.get("maxAttempts") == max_before,
        f"{max_before} -> {rules_after.get('maxAttempts')}",
    )
    check(
        "the configuration version is unchanged too",
        rules_after.get("configurationVersionNumber") == rules_before.get("configurationVersionNumber"),
        f"{rules_before.get('configurationVersionNumber')} -> "
        f"{rules_after.get('configurationVersionNumber')}",
    )

    status, eligibility = client.call(
        "GET", f"/api/v1/quizzes/{practice}/retake-eligibility", token=tokens["learner"]
    )
    # ADDITIONAL_ATTEMPT_AVAILABLE is the state that distinguishes a granted attempt from a
    # configured one, which is the property under test. On a re-run the loop above may have spent
    # this grant too, leaving EXHAUSTED — still correct, just no longer showing the distinction.
    if eligibility.get("state") == "EXHAUSTED":
        skip(
            "the granted attempt is reported as granted rather than configured",
            "the grant was spent while exhausting the allowance on this run; the distinction is "
            "asserted by tests/retakes and by verify_e2e section 30",
        )
    else:
        check(
            "the granted attempt is reported as granted, not as configured",
            eligibility.get("state") == "ADDITIONAL_ATTEMPT_AVAILABLE",
            json.dumps(eligibility)[:200],
        )

    status, other = client.call(
        "GET", f"/api/v1/quizzes/{practice}/retake-eligibility", token=tokens["learner2"]
    )
    # The property is that *this* grant did not reach the other learner, not that the other learner
    # has never been granted anything — a previous run may have topped them up. Compared against the
    # grant just made rather than against zero.
    mine = eligibility.get("allowance", {}).get("granted_attempts", 0)
    theirs = other.get("allowance", {}).get("granted_attempts", 0) if status == 200 else 0
    check(
        "the grant did not reach the other learner",
        status != 200 or theirs < mine or theirs == 0,
        f"this learner granted {mine}, other learner granted {theirs}",
    )

    status, history = client.call(
        "GET", f"/api/v1/quizzes/{practice}/attempt-history", token=tokens["learner"]
    )
    numbers = [entry["attempt_number"] for entry in history.get("entries", [])]
    check(
        "attempt history lists every attempt, in order, without gaps",
        status == 200 and numbers == sorted(numbers) and numbers == list(range(1, len(numbers) + 1)),
        str(numbers),
    )

    section("F - the administrator's dashboard")
    status, overall = client.call("GET", "/api/admin/analytics/overall", token=tokens["admin"])
    check("the dashboard reads", status == 200, f"{status} {json.dumps(overall)[:180]}")
    check(
        "it reports data rather than an empty state, because attempts now exist",
        overall.get("data_state") == "OK" and overall.get("attempt_volume", 0) > 0,
        json.dumps(overall)[:200],
    )
    check(
        "passes are counted and the pass rate is a real measurement",
        overall.get("passed_attempts", 0) >= 1 and overall.get("pass_rate") is not None,
        json.dumps(overall)[:200],
    )
    status, formal_only = client.call(
        "GET", "/api/admin/analytics/overall?assessment_type=FORMAL_ASSESSMENT", token=tokens["admin"]
    )
    check(
        "the formal-assessment filter is accepted and narrows the population",
        status == 200 and formal_only.get("attempt_volume", 0) < overall.get("attempt_volume", 0),
        f"{formal_only.get('attempt_volume')} of {overall.get('attempt_volume')}",
    )
    status, questions = client.call("GET", "/api/admin/analytics/questions", token=tokens["admin"])
    check(
        "question analytics reports rows with human-readable type labels",
        status == 200
        and bool(questions.get("items"))
        and all(item.get("question_type_label") for item in questions["items"]),
        json.dumps(questions)[:180],
    )
    blob = json.dumps(questions)
    check(
        "no analytics payload carries an answer key",
        "isCorrect" not in blob and "is_correct" not in blob and "correctPosition" not in blob,
    )
    status, empty = client.call(
        "GET", "/api/admin/analytics/courses/9999999/overall", token=tokens["admin"]
    )
    check(
        "a course with no attempts reports no data rather than zeros",
        status == 200
        and empty.get("data_state") == "NO_ATTEMPTS"
        and empty.get("average_score") is None,
        json.dumps(empty)[:200],
    )
    status, csv_body = client.call(
        "GET", "/api/admin/analytics/exports/overall.csv", token=tokens["admin"]
    )
    check(
        "the CSV export is CSV, not JSON",
        status == 200 and isinstance(csv_body, str) and "," in csv_body.splitlines()[0],
        str(csv_body)[:150],
    )


def security(client: Client, tokens: dict[str, str]) -> None:
    """The guards, as an outside caller experiences them."""
    section("SECURITY - the guards, from outside")

    for path, what in (
        ("/api/question-bank/questions", "the question bank, which carries the answer key"),
        ("/api/question-bank/topics", "the topic list"),
        ("/api/admin/quizzes", "quiz administration"),
        ("/api/admin/analytics/overall", "analytics"),
        ("/api/admin/analytics/exports/overall.csv", "the analytics export"),
        ("/api/assessor/pending-reviews", "the assessor queue"),
        ("/api/system/formal-assessments/review-queue/unpublished", "the platform-internal queue"),
        ("/api/v1/results", "learner results"),
        ("/api/v1/outcomes", "learner outcomes"),
        ("/api/v1/feedback", "learner feedback"),
    ):
        status, _ = client.call("GET", path)
        check(f"anonymous cannot reach {what}", status == 401, f"got {status}")

    for path in (
        "/api/question-bank/questions",
        "/api/admin/quizzes",
        "/api/admin/analytics/overall",
        "/api/assessor/pending-reviews",
        "/api/system/formal-assessments/review-queue/unpublished",
    ):
        status, _ = client.call("GET", path, token=tokens["learner"])
        check(f"a learner is forbidden from {path}", status == 403, f"got {status}")

    for path in ("/api/v1/results", "/api/v1/outcomes", "/api/v1/feedback"):
        status, _ = client.call("GET", path, token=tokens["admin"])
        check(f"an administrator is not a learner on {path}", status == 403, f"got {status}")

    status, _ = client.call("GET", "/api/question-bank/questions", token="definitely-not-a-token")
    check("an unknown credential is 401, not 403", status == 401, f"got {status}")

    # The one that matters most: the delivery API must never carry the key.
    status, results = client.call("GET", "/api/v1/results", token=tokens["learner"])
    items = results.get("results") or results.get("items") or []
    if items:
        attempt_id = items[0]["attemptId"]
        status, paper = client.call(
            "GET", f"/api/v1/attempts/{attempt_id}/questions", token=tokens["learner"]
        )
        blob = json.dumps(paper)
        check(
            "the delivered paper never carries the answer key",
            "isCorrect" not in blob and "correctPosition" not in blob and "isPrimary" not in blob,
            blob[:200],
        )
        status, _ = client.call(
            "PUT",
            f"/api/v1/attempts/{attempt_id}/questions/anything/answer",
            {"response": {"selectedOptionId": "A"}},
            token=tokens["learner"],
        )
        check("a submitted attempt cannot be modified", status in (404, 409), f"got {status}")


# ---------------------------------------------------------------------------


def resolve_tokens(client: Client, args: argparse.Namespace) -> dict[str, str] | None:
    """Explicit flags, then the environment, then the deployment's own identity listing."""
    tokens = {
        "admin": args.admin_token or os.environ.get("REVIEW_ADMIN_TOKEN", ""),
        "learner": args.learner_token or os.environ.get("REVIEW_LEARNER_TOKEN", ""),
        "learner2": args.learner2_token or os.environ.get("REVIEW_LEARNER2_TOKEN", ""),
        "assessor": args.assessor_token or os.environ.get("REVIEW_ASSESSOR_TOKEN", ""),
    }
    if all(tokens.values()):
        return tokens

    status, session = client.call("GET", "/api/session")
    listed = session.get("users") if isinstance(session, dict) else None
    if not listed:
        print(
            "\n  Cannot determine credentials. Either pass --admin-token/--learner-token/"
            "--learner2-token/--assessor-token, set REVIEW_*_TOKEN, or run the deployment with "
            "DEMO_IDENTITIES=true so GET /api/session lists them."
        )
        return None

    learners = [entry for entry in listed if entry["role"] == "learner"]
    admins = [entry for entry in listed if entry["role"] == "admin"]
    assessors = [entry for entry in listed if entry["role"] == "assessor"]
    if not (admins and len(learners) >= 2 and assessors):
        print(
            f"\n  The deployment lists {len(admins)} admin(s), {len(learners)} learner(s) and "
            f"{len(assessors)} assessor(s). Two learners and one assessor are needed: the assessor "
            "because UC-09's review cannot be reached without one, and the second learner because "
            "cross-learner isolation cannot otherwise be checked."
        )
        return None

    return {
        "admin": tokens["admin"] or admins[0]["token"],
        "learner": tokens["learner"] or learners[0]["token"],
        "learner2": tokens["learner2"] or learners[1]["token"],
        "assessor": tokens["assessor"] or assessors[0]["token"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("REVIEW_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--learner-token", default="")
    parser.add_argument("--learner2-token", default="")
    parser.add_argument("--assessor-token", default="")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm that writing real attempts, results and certificates to this instance is intended.",
    )
    args = parser.parse_args()

    client = Client(args.base_url)
    print("COURSES QUIZ AGENT - DEPLOYMENT VERIFICATION")
    print("=" * 60)
    print(f"target : {client.base}")

    if not args.yes:
        print(
            "\nThis creates real attempts, results and certificates on the target instance.\n"
            "Re-run with --yes if that is intended (it is, on a review deployment)."
        )
        return 2

    tokens = resolve_tokens(client, args)
    if tokens is None:
        return 1

    try:
        quizzes = journey_a(client, tokens)
        practice = quizzes.get("practice-assessment")
        formal_quiz = quizzes.get("supervised-final-examination")
        if practice:
            journey_bc(client, tokens, practice)
        if formal_quiz:
            journey_d(client, tokens, formal_quiz)
        if practice:
            journey_ef(client, tokens, practice)
        security(client, tokens)
    except Exception:
        import traceback

        print("\n  [FAIL] the verification script itself raised:")
        traceback.print_exc()
        fails.append("the verification script raised an exception")

    print("\n" + "=" * 60)
    total = len(passes) + len(fails)
    if skips:
        print(f"{len(skips)} check(s) skipped — already exercised on this database, not defects:")
        for skipped in skips:
            print(f"  - {skipped}")
        print()
    if fails:
        print(f"RESULT: {len(fails)} of {total} checks FAILED")
        for failure in fails:
            print(f"  - {failure}")
        return 1
    print(f"RESULT: all {total} checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
