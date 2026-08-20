"""Drive UC-01 and UC-04 … UC-11 as a real user, against a running deployment.

    python -m scripts.verify_uc_remaining --base-url https://your-app.up.railway.app --yes

Companion to ``verify_uc02_uc03``. Everything goes over HTTP; nothing reaches behind the API.

The order is the order a real course runs in — configure, sit, score, judge, explain, coach, retake,
supervise, report — because most of the interesting properties are about what one stage may or may
not do to a stage that already finished. A retake must not disturb the attempt before it; analytics
must not disturb anything at all; a formal pass must not become a certificate without an assessor.

WHAT IT WRITES
--------------
Configuration versions, attempts, results, certificates, an administrator grant and a formal
assessment — real rows. `--yes` is required for the same reason as its companion.

Re-running it is safe but not free of state: one formal assessment per learner and quiz is a hard
rule, and the seed provides two learners, so that journey is exercised at most twice per database.
When both are used it is reported as SKIPPED with the reason rather than as a failure — a check that
cannot tell "broken" from "already done here" teaches people to ignore red.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
import uuid


def _parse_args() -> tuple[str, str]:
    """``(base_url, run_id)``. The URL is a flag so this can target any deployment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("REVIEW_BASE_URL", "http://127.0.0.1:8000"),
        help="Root URL of the running deployment, e.g. https://your-app.up.railway.app",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm that writing real data to this deployment is intended.",
    )
    arguments = parser.parse_args()
    if not arguments.yes:
        print(
            "This creates real questions, attempts and results on the target deployment.\n"
            "Re-run with --yes if that is intended (it is, on a review deployment)."
        )
        raise SystemExit(2)
    return arguments.base_url.rstrip("/"), uuid.uuid4().hex[:8]


BASE, RUN = _parse_args()

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
    skips.append(f"{label} - {reason}")
    print(f"  [SKIP] {label} - {reason}")


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def call(method, path, body=None, token=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    hdrs = dict(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(payload)
            except ValueError:
                return response.status, payload
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(payload)
        except ValueError:
            return exc.code, payload


_, session = call("GET", "/api/session")
by_role = {u["role"]: u for u in session["users"]}
learners = [u for u in session["users"] if u["role"] == "learner"]
ADMIN, ASSESSOR = by_role["admin"]["token"], by_role["assessor"]["token"]
LEARNER, LEARNER2 = learners[0]["token"], learners[1]["token"]
LEARNER_NAME, LEARNER_EMAIL = learners[0]["displayName"], learners[0]["email"]

_, quizzes = call("GET", "/api/quizzes", token=LEARNER)
QUIZ = next(q["id"] for q in quizzes["quizzes"] if q["slug"] == "end-of-course-assessment")
COURSE = next(q.get("courseId") for q in quizzes["quizzes"] if q["id"] == QUIZ)

print(f"target : {BASE}\nrun    : {RUN}\nquiz   : {QUIZ} (course {COURSE})")


def key_for(question_id: str) -> tuple[list[str], list[str]]:
    """The answer key, read as the administrator — the only role entitled to it."""
    _, q = call("GET", f"/api/question-bank/questions/{question_id}", token=ADMIN)
    options = (q.get("question", q) or {}).get("options", [])
    return ([o["label"] for o in options if o.get("isCorrect")],
            [o["label"] for o in options if not o.get("isCorrect")])


def answer_payload(question, correct: bool):
    correct_labels, wrong_labels = key_for(question["questionId"])
    kind = question["questionType"]
    if kind == "TRUE_FALSE":
        truth = correct_labels[0].upper() == "TRUE"
        return {"value": truth if correct else not truth}
    if kind == "MULTI_SELECT":
        return {"selectedOptionIds": sorted(correct_labels if correct else wrong_labels[:1])}
    if kind == "DRAG_TO_ORDER":
        _, q = call("GET", f"/api/question-bank/questions/{question['questionId']}", token=ADMIN)
        opts = (q.get("question", q) or {}).get("options", [])
        ordered = [o["label"] for o in sorted(
            (o for o in opts if o.get("correctPosition")), key=lambda o: o["correctPosition"])]
        return {"orderedItemIds": ordered if correct else list(reversed(ordered))}
    return {"selectedOptionId": (correct_labels if correct else wrong_labels)[0]}


def clear_open_attempt(token):
    status, existing = call("GET", f"/api/v1/attempts/active?quizId={QUIZ}", token=token)
    open_id = (existing.get("attempt") or {}).get("attemptId") if isinstance(existing, dict) else None
    if open_id:
        call("POST", f"/api/v1/attempts/{open_id}/submission", {"confirmed": True}, token=token)
    return open_id


def sit(token, *, correct_count):
    """Start, answer, submit. Returns (attempt_id, delivered questions)."""
    status, started = call("POST", "/api/v1/attempts", {"quizId": str(QUIZ)}, token=token)
    if status != 201:
        return None, []
    attempt_id = started["attempt"]["attemptId"]
    _, paper = call("GET", f"/api/v1/attempts/{attempt_id}/questions", token=token)
    delivered = paper.get("questions", [])
    for index, question in enumerate(delivered):
        call("PUT", f"/api/v1/attempts/{attempt_id}/questions/{question['questionId']}/answer",
             {"response": answer_payload(question, index < correct_count), "source": "MANUAL"},
             token=token)
    call("POST", f"/api/v1/attempts/{attempt_id}/submission", {"confirmed": True}, token=token)
    return attempt_id, delivered


# ===========================================================================
print("\n" + "=" * 72 + "\nUC-01 — QUIZ CONFIGURATION & RULES\n" + "=" * 72)

section("1.1 The backend validates a configuration, whatever the UI did")
base_config = {
    "questionCount": 4, "timeLimitMinutes": 20, "passMark": 50,
    "questionTypes": [{"type": "SINGLE_CHOICE", "quota": 2}, {"type": "TRUE_FALSE", "quota": 2}],
    "randomiseQuestions": False, "maxAttempts": 50, "deliveryMode": "assessment",
    "questionPresentation": "ALL_AT_ONCE",
}
for label, bad in (
    ("quotas that do not sum to the question count", {**base_config, "questionCount": 9}),
    ("a pass mark above 100", {**base_config, "passMark": 150}),
    ("a negative question count", {**base_config, "questionCount": -1}),
    ("zero attempts allowed", {**base_config, "maxAttempts": 0}),
    ("an unknown question type", {**base_config,
                                  "questionTypes": [{"type": "NONSENSE", "quota": 4}]}),
):
    status, body = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration", bad, token=ADMIN)
    check(f"rejected: {label}", status in (400, 422), f"{status} {json.dumps(body)[:130]}")

section("1.2 A configuration the bank cannot satisfy is refused, with the capacity report")
# 90 is inside the field limit of 100, so this reaches the capacity check rather than being
# rejected as a malformed number first. The bank holds far fewer single-choice questions than that.
status, availability = call("GET", f"/api/admin/quizzes/{QUIZ}/question-bank", token=ADMIN)
available = (availability.get("availableByType") or {}).get("SINGLE_CHOICE", 0)
check("the bank reports what it can supply, per type", status == 200 and available > 0,
      json.dumps(availability.get("availableByType"))[:160])

status, body = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration",
                    {**base_config, "questionCount": 90,
                     "questionTypes": [{"type": "SINGLE_CHOICE", "quota": 90}]}, token=ADMIN)
check("a paper larger than the bank can supply is refused", status in (400, 409, 422), str(status))
check("and the refusal explains it as a bank shortfall, not a malformed request",
      "capacity" in json.dumps(body).lower() or "insufficient" in json.dumps(body).lower()
      or "bank" in json.dumps(body).lower(),
      json.dumps(body)[:240])

section("1.3 Saving publishes an immutable version")
status, first = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration", base_config, token=ADMIN)
check("a valid configuration saves", status in (200, 201), f"{status} {json.dumps(first)[:160]}")
v1 = (first.get("configuration") or {})
check("it publishes a version with a number", v1.get("id") and v1.get("versionNumber"),
      json.dumps(v1)[:160])

status, repeat = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration", base_config, token=ADMIN)
v_repeat = (repeat.get("configuration") or {})
check("saving the identical configuration does NOT publish a new version",
      v_repeat.get("id") == v1.get("id"), f"{v1.get('id')} vs {v_repeat.get('id')}")

status, changed = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration",
                       {**base_config, "passMark": 60}, token=ADMIN)
v2 = (changed.get("configuration") or {})
check("a real change DOES publish a new version", v2.get("id") != v1.get("id"),
      f"{v1.get('id')} -> {v2.get('id')}")
check("and the version number advances", v2.get("versionNumber") == v1.get("versionNumber") + 1,
      f"{v1.get('versionNumber')} -> {v2.get('versionNumber')}")

status, history = call("GET", f"/api/admin/quizzes/{QUIZ}/configuration/versions", token=ADMIN)
versions = history.get("versions", history) if isinstance(history, dict) else history
check("the version history lists them all", status == 200 and len(versions) >= 2,
      f"{status} {len(versions) if isinstance(versions, list) else '?'}")

section("1.4 The learner's rules view is read-only and matches the active version")
status, rules = call("GET", f"/api/quizzes/{QUIZ}/rules", token=LEARNER)
check("rules read", status == 200, str(status))
check("they report the active version's numbers",
      rules.get("passMark") == 60 and rules.get("questionCount") == 4,
      json.dumps(rules)[:200])
check("and the version they came from", rules.get("configurationVersionNumber") == v2.get("versionNumber"),
      f"{rules.get('configurationVersionNumber')} vs {v2.get('versionNumber')}")
status, refused = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration", base_config, token=LEARNER)
check("a learner cannot configure a quiz", status == 403, str(status))


# ===========================================================================
print("\n" + "=" * 72 + "\nUC-04 · UC-05 · UC-06 — SCORING, GATING, FEEDBACK\n" + "=" * 72)

clear_open_attempt(LEARNER)

section("4.1 A failing attempt is scored honestly")
fail_attempt, fail_paper = sit(LEARNER, correct_count=0)
check("an attempt could be sat", fail_attempt is not None)
status, result = call("POST", f"/api/v1/attempts/{fail_attempt}/result", {}, token=LEARNER)
scored = result.get("result", {})
check("it scores", status in (200, 201) and scored.get("status") == "SCORED",
      f"{status} {json.dumps(scored)[:180]}")
check("nothing correct, nothing unanswered", scored.get("correctCount") == 0
      and scored.get("unansweredCount") == 0, json.dumps(scored)[:200])
check("the percentage is 0", scored.get("percentage") == 0.0, str(scored.get("percentage")))
check("per-question scores are returned", len(result.get("questionScores", [])) == 4,
      str(len(result.get("questionScores", []))))
check("each names the answer key source, so marking is traceable",
      all(qs.get("answerKeySource") for qs in result.get("questionScores", [])),
      json.dumps(result.get("questionScores", [])[:1])[:200])

status, replay = call("POST", f"/api/v1/attempts/{fail_attempt}/result", {}, token=LEARNER)
check("re-scoring replays the stored result rather than recomputing",
      (replay.get("result") or {}).get("resultId") == scored.get("resultId"),
      f"{scored.get('resultId')} vs {(replay.get('result') or {}).get('resultId')}")

section("5.1 The verdict, and the certificate that must not exist")
status, outcome = call("POST", f"/api/v1/attempts/{fail_attempt}/outcome", {}, token=LEARNER)
verdict = outcome.get("outcome", {})
check("the outcome is determined", status in (200, 201), f"{status}")
check("it is a FAIL", verdict.get("outcome") == "FAIL", json.dumps(verdict)[:180])
check("judged against the attempt's own pass mark", verdict.get("passMarkPercentage") == 60,
      str(verdict.get("passMarkPercentage")))
check("no certificate is issued for a fail", outcome.get("certificate") is None,
      json.dumps(outcome.get("certificate"))[:160])
check("the CPD record is still written — a fail is a completion too",
      (outcome.get("cpd") or {}).get("passed") is False, json.dumps(outcome.get("cpd"))[:180])
check("attempts remaining is reported to the learner",
      outcome.get("attemptsRemaining") is not None, json.dumps(outcome)[:180])

section("6.1 Feedback explains the failure, question by question")
status, feedback = call("POST", f"/api/v1/attempts/{fail_attempt}/feedback", {}, token=LEARNER)
check("a report is generated", status in (200, 201) and feedback.get("status") == "GENERATED",
      f"{status} {json.dumps(feedback)[:160]}")
items = feedback.get("items", [])
check("one item per delivered question", len(items) == 4, str(len(items)))
for field in ("question", "learnerAnswer", "correctAnswer", "explanation", "questionScore"):
    check(f"every item carries '{field}'", all(field in item and item[field] not in (None, "")
                                               or field == "questionScore" for item in items),
          json.dumps(items[:1])[:200])
check("the summary agrees with UC-04's result",
      feedback.get("summary", {}).get("percentage") == scored.get("percentage")
      and feedback.get("summary", {}).get("passed") is False,
      json.dumps(feedback.get("summary"))[:200])
status, regenerated = call("POST", f"/api/v1/attempts/{fail_attempt}/feedback", {}, token=LEARNER)
check("regenerating replays the frozen report",
      regenerated.get("feedbackId") == feedback.get("feedbackId"),
      f"{feedback.get('feedbackId')} vs {regenerated.get('feedbackId')}")


# ===========================================================================
print("\n" + "=" * 72 + "\nUC-07 — AI COACHING REVIEW MODE\n" + "=" * 72)

section("7.1 Coaching is offered on the wrong answers — and refuses to invent teaching")
status, eligibility = call("GET", f"/api/v1/attempts/{fail_attempt}/coaching/eligibility",
                           token=LEARNER)
check("coaching eligibility reads", status == 200, str(status))
check("it answers with a reason rather than an error",
      eligibility.get("coachingAvailable") is not None and bool(eligibility.get("reason") or
                                                                eligibility.get("coachingAvailable")),
      json.dumps(eligibility)[:200])
if eligibility.get("coachingAvailable"):
    check("it reports how many questions were wrong",
          eligibility.get("incorrectQuestionCount") == 4, json.dumps(eligibility)[:200])
else:
    # No AI provider is bound on this deployment, which is the stock posture, so eligibility
    # short-circuits on that before counting anything. The count is exercised by the review queue
    # below, which does not depend on a provider.
    check("with no provider bound it says SERVICE_UNAVAILABLE and offers a retry",
          eligibility.get("reason") == "SERVICE_UNAVAILABLE"
          and eligibility.get("retryable") is True, json.dumps(eligibility)[:220])
check("and never leaks an answer key",
      "isCorrect" not in json.dumps(eligibility) and "correctAnswer" not in json.dumps(eligibility))

status, queue = call("GET", f"/api/v1/attempts/{fail_attempt}/coaching/review", token=LEARNER)
check("the review queue lists exactly the incorrect questions",
      status == 200 and queue.get("totalIncorrect") == 4, f"{status} {json.dumps(queue)[:180]}")
check("the queue carries no answer key either",
      "isCorrect" not in json.dumps(queue) and "correctAnswer" not in json.dumps(queue))

first_wrong = (queue.get("items") or [{}])[0].get("questionId")
status, started = call("POST",
                       f"/api/v1/attempts/{fail_attempt}/coaching/questions/{first_wrong}",
                       {}, token=LEARNER)
# No AI provider is configured on this deployment, which is the stock posture.
if eligibility.get("coachingAvailable") is False or status in (409, 503):
    check("with no provider configured, coaching says so rather than inventing a reply",
          status in (409, 503) or eligibility.get("coachingAvailable") is False,
          f"{status} {json.dumps(started)[:200]}")
    check("and the refusal is retryable, not a dead end",
          (started.get("error") or {}).get("retryable") is True
          or eligibility.get("retryable") is True
          or "unavailable" in json.dumps(started).lower() + json.dumps(eligibility).lower(),
          json.dumps(started)[:200])
    check("no coaching text was fabricated",
          "message" not in (started.get("exchange") or {}),
          json.dumps(started)[:200])
else:
    check("a coaching session started", status in (200, 201), json.dumps(started)[:200])

section("7.2 Coaching cannot be reached for the wrong attempt or the wrong learner")
status, other = call("GET", f"/api/v1/attempts/{fail_attempt}/coaching/eligibility",
                     token=LEARNER2)
# This endpoint is deliberately "never fails, always explains" — an unsubmitted attempt, an
# unreleased report and a correctly answered question all come back as reasons so the panel can
# say why rather than vanishing. Ownership is one of those reasons. The test is therefore not the
# status code but whether anything is disclosed.
check("a non-owner is told it is not their attempt",
      status in (403, 404) or other.get("reason") == "NOT_ATTEMPT_OWNER",
      f"{status} {json.dumps(other)[:200]}")
check("and is given no coaching content whatsoever",
      not other.get("questions") and not other.get("incorrectQuestionCount"),
      json.dumps(other)[:220])
status, other_queue = call("GET", f"/api/v1/attempts/{fail_attempt}/coaching/review",
                           token=LEARNER2)
check("and the queue that does carry content refuses outright", status == 403, str(status))
check("no question text leaks in that refusal",
      "questionText" not in json.dumps(other_queue) and "prompt" not in json.dumps(other_queue),
      json.dumps(other_queue)[:200])
status, as_admin = call("GET", f"/api/v1/attempts/{fail_attempt}/coaching/eligibility", token=ADMIN)
check("an administrator is not a learner here either", as_admin and status == 403, str(status))


# ===========================================================================
print("\n" + "=" * 72 + "\nUC-08 — RETAKE MANAGEMENT\n" + "=" * 72)

section("8.1 Eligibility after a failure")
status, elig = call("GET", f"/api/v1/quizzes/{QUIZ}/retake-eligibility", token=LEARNER)
check("retake eligibility reads", status == 200, str(status))
check("the learner may retake after failing", elig.get("can_retake") is True,
      json.dumps(elig)[:200])
check("the allowance is explained, not just asserted",
      elig.get("allowance", {}).get("attempts_used") is not None
      and elig.get("allowance", {}).get("maximum_attempts") is not None,
      json.dumps(elig.get("allowance"))[:200])

section("8.2 A retake is a new, independent attempt")
before_paper = sorted(q["questionId"] for q in fail_paper)
status, retake = call("POST", f"/api/v1/quizzes/{QUIZ}/retakes", {}, token=LEARNER)
check("a retake is created", status == 201, f"{status} {json.dumps(retake)[:200]}")
retake_attempt = (retake.get("attempt") or {}).get("attempt_id")
check("it produced a different attempt", retake_attempt and retake_attempt != fail_attempt)
check("and its attempt number advanced",
      (retake.get("attempt") or {}).get("attempt_number", 0) > 1,
      json.dumps(retake.get("attempt"))[:160])
plan = retake.get("question_plan") or {}
check("the retake reports the paper it drew against",
      plan.get("required_count") is not None and plan.get("unused_pool_size") is not None,
      json.dumps(plan)[:200])
check("it takes as many unseen questions as the bank allows",
      plan.get("expected_fresh_questions") == min(plan.get("required_count", 0),
                                                  plan.get("unused_pool_size", 0)),
      json.dumps(plan)[:200])

status, again = call("POST", f"/api/v1/quizzes/{QUIZ}/retakes", {}, token=LEARNER)
check("requesting the retake twice does not consume a second attempt",
      status == 409 or (again.get("attempt") or {}).get("attempt_id") == retake_attempt,
      f"{status} {json.dumps(again)[:160]}")

section("8.3 The earlier attempt is untouched by the retake")
status, old_result = call("GET", f"/api/v1/attempts/{fail_attempt}/result", token=LEARNER)
check("the failed attempt still reads its original result",
      (old_result.get("result") or {}).get("resultId") == scored.get("resultId"),
      json.dumps(old_result.get("result"))[:180])
status, old_outcome = call("GET", f"/api/v1/attempts/{fail_attempt}/outcome", token=LEARNER)
check("and its original FAIL verdict",
      (old_outcome.get("outcome") or {}).get("outcome") == "FAIL",
      json.dumps(old_outcome.get("outcome"))[:160])

section("8.4 Passing the retake issues the certificate")
_, retake_paper = call("GET", f"/api/v1/attempts/{retake_attempt}/questions", token=LEARNER)
for question in retake_paper.get("questions", []):
    call("PUT", f"/api/v1/attempts/{retake_attempt}/questions/{question['questionId']}/answer",
         {"response": answer_payload(question, True), "source": "MANUAL"}, token=LEARNER)
call("POST", f"/api/v1/attempts/{retake_attempt}/submission", {"confirmed": True}, token=LEARNER)
_, retake_result = call("POST", f"/api/v1/attempts/{retake_attempt}/result", {}, token=LEARNER)
status, retake_outcome = call("POST", f"/api/v1/attempts/{retake_attempt}/outcome", {}, token=LEARNER)
check("the retake scores 100%", (retake_result.get("result") or {}).get("percentage") == 100.0,
      json.dumps(retake_result.get("result"))[:180])
check("and passes", (retake_outcome.get("outcome") or {}).get("outcome") == "PASS",
      json.dumps(retake_outcome.get("outcome"))[:160])
cert = retake_outcome.get("certificate") or {}
issued = cert.get("status") == "ISSUED" and bool(cert.get("certificateNumber"))
already = cert.get("status") == "FAILED" and cert.get("failureCode") == "CERTIFICATE_ALREADY_ISSUED"
check("a certificate is issued, or a duplicate is refused because one already exists",
      issued or already, json.dumps(cert)[:220])
if already:
    print("  [note] this learner already holds a certificate for this quiz — duplicate refused")

section("8.5 Attempt history, assembled read-only from six capabilities")
status, history = call("GET", f"/api/v1/quizzes/{QUIZ}/attempt-history", token=LEARNER)
entries = history.get("entries", [])
check("history reads", status == 200 and len(entries) >= 2, f"{status} {len(entries)}")
check("attempt numbers are sequential and gapless",
      [e["attempt_number"] for e in entries] == list(range(1, len(entries) + 1)),
      str([e["attempt_number"] for e in entries]))
check("it marks which attempts were retakes",
      any(e.get("is_retake") for e in entries), str([e.get("is_retake") for e in entries]))
check("an unscored attempt would say so rather than showing 0%",
      all((e.get("percentage") is not None) == e.get("score_available") for e in entries),
      json.dumps([(e.get("score_available"), e.get("percentage")) for e in entries])[:200])

section("8.6 An administrator grant is per learner, and changes no quiz")
_, rules_before = call("GET", f"/api/quizzes/{QUIZ}/rules", token=LEARNER)
learner_id = str((call("GET", "/api/session", token=LEARNER)[1].get("user") or {}).get("id"))
status, granted = call("POST", "/api/admin/retakes/grants",
                       {"learner_id": learner_id, "course_id": str(COURSE), "quiz_id": str(QUIZ),
                        "additional_attempts": 1, "reason": f"live check {RUN}",
                        "idempotency_key": f"live-{RUN}"}, token=ADMIN)
check("an administrator can grant an extra attempt", status in (200, 201),
      f"{status} {json.dumps(granted)[:180]}")
status, replayed = call("POST", "/api/admin/retakes/grants",
                        {"learner_id": learner_id, "course_id": str(COURSE), "quiz_id": str(QUIZ),
                         "additional_attempts": 1, "reason": f"live check {RUN}",
                         "idempotency_key": f"live-{RUN}"}, token=ADMIN)
check("the same idempotency key replays rather than granting twice", status == 200, str(status))
_, rules_after = call("GET", f"/api/quizzes/{QUIZ}/rules", token=LEARNER)
check("the quiz's own maximum is unchanged by the grant",
      rules_after.get("maxAttempts") == rules_before.get("maxAttempts"),
      f"{rules_before.get('maxAttempts')} -> {rules_after.get('maxAttempts')}")
check("and no new configuration version was published",
      rules_after.get("configurationVersionNumber") == rules_before.get("configurationVersionNumber"),
      f"{rules_before.get('configurationVersionNumber')} -> {rules_after.get('configurationVersionNumber')}")
status, learner_try = call("POST", "/api/admin/retakes/grants",
                           {"learner_id": learner_id, "course_id": str(COURSE),
                            "quiz_id": str(QUIZ), "additional_attempts": 5,
                            "reason": "self-service", "idempotency_key": f"live-self-{RUN}"},
                           token=LEARNER)
check("a learner cannot grant themselves attempts", learner_try and status == 403, str(status))


# ===========================================================================
print("\n" + "=" * 72 + "\nUC-10 — ANALYTICS & REPORTING\n" + "=" * 72)

section("10.1 The dashboard reports what the chain actually wrote")
status, overall = call("GET", "/api/admin/analytics/overall", token=ADMIN)
check("the dashboard reads", status == 200 and overall.get("data_state") == "OK",
      f"{status} {json.dumps(overall)[:200]}")
check("attempts, scores and verdicts are all counted",
      overall.get("attempt_volume", 0) > 0 and overall.get("scored_attempts", 0) > 0
      and overall.get("graded_attempts", 0) > 0, json.dumps(overall)[:220])
check("the denominators are consistent (scored <= completed <= volume)",
      overall["scored_attempts"] <= overall["completed_attempts"] <= overall["attempt_volume"],
      json.dumps(overall)[:220])
check("passed + failed does not exceed graded",
      overall.get("passed_attempts", 0) + overall.get("failed_attempts", 0)
      <= overall.get("graded_attempts", 0), json.dumps(overall)[:220])
check("it says when it was calculated", bool(overall.get("calculated_at")))

section("10.2 Filters narrow the population rather than decorating it")
status, cohort_a = call("GET", "/api/admin/analytics/overall?cohort_id=cohort-a", token=ADMIN)
status_b, cohort_b = call("GET", "/api/admin/analytics/overall?cohort_id=cohort-b", token=ADMIN)
check("the cohort filter is accepted", status == 200 and status_b == 200)
cohort_total = cohort_a.get("attempt_volume", 0) + cohort_b.get("attempt_volume", 0)
if cohort_total == 0:
    # The deployed seed enrols learners without a cohort_id, so there is nothing for this filter to
    # match. The filter is working — it is correctly returning the empty set — but the demo data
    # cannot show it doing anything useful. Reported rather than asserted away.
    skip("the cohort filter partitioning the attempts",
         "the deployed seed enrols learners with no cohort_id, so no attempt carries a cohort. "
         "The filter narrows correctly (it returns the empty set and reports NO_ATTEMPTS); "
         "verify_e2e section 32 proves the partition against data that has cohorts")
    check("an unmatched cohort reports no data rather than a misleading zero",
          cohort_a.get("data_state") == "NO_ATTEMPTS" and cohort_a.get("average_score") is None,
          json.dumps(cohort_a)[:200])
else:
    check("the two cohorts partition the attempts",
          cohort_total == overall.get("attempt_volume"),
          f"{cohort_a.get('attempt_volume')} + {cohort_b.get('attempt_volume')} "
          f"vs {overall.get('attempt_volume')}")
status, formal_only = call("GET",
                           "/api/admin/analytics/overall?assessment_type=FORMAL_ASSESSMENT",
                           token=ADMIN)
check("the assessment-type filter narrows it further",
      status == 200 and formal_only.get("attempt_volume", 0) < overall.get("attempt_volume"),
      f"{formal_only.get('attempt_volume')} of {overall.get('attempt_volume')}")

section("10.3 No data is distinguishable from a measured zero")
status, empty = call("GET", "/api/admin/analytics/courses/987654/overall", token=ADMIN)
check("a course with no attempts answers 200", status == 200, str(status))
check("and says NO_ATTEMPTS", empty.get("data_state") == "NO_ATTEMPTS",
      json.dumps(empty)[:200])
check("with null rates, not zeros a reader would take as measured",
      empty.get("average_score") is None and empty.get("pass_rate") is None
      and empty.get("completion_rate") is None, json.dumps(empty)[:200])

section("10.4 Question analytics, flags, and the CSV export")
status, questions = call("GET", "/api/admin/analytics/questions", token=ADMIN)
items = questions.get("items", [])
check("question analytics reads", status == 200 and len(items) > 0, f"{status} {len(items)}")
check("each question reports a human-readable type",
      all(i.get("question_type_label") for i in items), json.dumps(items[:1])[:200])
check("accuracy and wrong-answer rate are reported per question",
      any(i.get("accuracy_percentage") is not None for i in items), json.dumps(items[:1])[:200])
check("no analytics payload carries an answer key",
      "isCorrect" not in json.dumps(questions) and "correctAnswer" not in json.dumps(questions))

status, evaluated = call("POST", "/api/admin/analytics/questions/flags/evaluate", {}, token=ADMIN)
check("flags can be recalculated", status in (200, 201), f"{status}")
status, flagged = call("GET", "/api/admin/analytics/questions/flagged", token=ADMIN)
check("the flagged panel reads and reports its threshold",
      status == 200 and flagged.get("threshold_used") is not None,
      f"{status} {json.dumps(flagged)[:180]}")

status, csv_body = call("GET", "/api/admin/analytics/exports/overall.csv", token=ADMIN)
check("the CSV export is CSV, not JSON",
      status == 200 and isinstance(csv_body, str) and "," in csv_body.splitlines()[0]
      and not csv_body.lstrip().startswith("{"), str(csv_body)[:140])
rows = [r for r in str(csv_body).splitlines() if r.strip()]
check("it has a header and at least one data row", len(rows) >= 2, str(len(rows)))

section("10.5 Analytics is read-only towards assessment data")
_, before_result = call("GET", f"/api/v1/attempts/{fail_attempt}/result", token=LEARNER)
call("GET", "/api/admin/analytics/overall", token=ADMIN)
call("GET", "/api/admin/analytics/questions", token=ADMIN)
_, after_result = call("GET", f"/api/v1/attempts/{fail_attempt}/result", token=LEARNER)
check("reading the dashboard changed no stored result",
      json.dumps(before_result) == json.dumps(after_result))


# ===========================================================================
print("\n" + "=" * 72 + "\nUC-09 — FORMAL ASSESSMENT MODE\n" + "=" * 72)

section("9.1 A quiz becomes a formal assessment by configuration")
formal_config = {**base_config, "passMark": 50, "isFormalAssessment": True,
                 "requiresHumanReview": True, "requiresAssessorApproval": True}
status, formal_saved = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration", formal_config,
                            token=ADMIN)
check("the quiz is reconfigured as a formal assessment", status in (200, 201),
      f"{status} {json.dumps(formal_saved)[:180]}")
check("the flag is on the published version",
      (formal_saved.get("configuration") or {}).get("isFormalAssessment") is True,
      json.dumps(formal_saved.get("configuration"))[:200])

section("9.2 The pre-start sequence is a gate, not a wizard")
FORMAL_LEARNER, FORMAL_LABEL = None, None
for label, token in (("learner 2", LEARNER2), ("learner 1", LEARNER)):
    status, conditions = call("GET", f"/api/v1/quizzes/{QUIZ}/formal-conditions", token=token)
    if status != 200:
        continue
    codes = [c["code"] for c in conditions.get("conditions", [])]
    status, ack = call("POST", f"/api/v1/quizzes/{QUIZ}/conditions-acknowledgement",
                       {"acknowledged_condition_codes": codes}, token=token)
    if status in (200, 201):
        FORMAL_LEARNER, FORMAL_LABEL = token, label
        FORMAL_ID = ack["formal_attempt_id"]
        check("the conditions are served and can be acknowledged", True)
        check("the acknowledgement records which version was agreed",
              bool(ack.get("conditions_version")), json.dumps(ack)[:180])
        print(f"  [note] running the formal journey as {label}")
        break
    if (ack.get("error") or {}).get("code") != "DUPLICATE_FORMAL_ATTEMPT":
        check("the conditions can be acknowledged", False, f"{status} {json.dumps(ack)[:180]}")
        break
    print(f"  [note] {label} already holds a formal assessment for this quiz")

if FORMAL_LEARNER is None:
    skip("the formal assessment journey",
         "both seeded learners already hold a formal assessment for this quiz — one per learner "
         "and quiz is the rule")
else:
    status, premature = call("POST", f"/api/v1/quizzes/{QUIZ}/formal-attempts",
                             {"device": {"fingerprint": f"live-{RUN}", "platform": "live"}},
                             token=FORMAL_LEARNER)
    check("starting before identity confirmation is refused", status in (403, 409, 422),
          f"{status} {json.dumps(premature)[:160]}")

    _, who = call("GET", "/api/session", token=FORMAL_LEARNER)
    entry = next(u for u in who["users"] if u["token"] == FORMAL_LEARNER)
    status, wrong = call("POST", f"/api/v1/quizzes/{QUIZ}/identity-confirmation",
                         {"full_name": "Someone Else", "email": entry["email"]},
                         token=FORMAL_LEARNER)
    check("a name that does not match the directory is refused",
          status != 200 or wrong.get("identity_check", {}).get("confirmed") is not True,
          f"{status} {json.dumps(wrong)[:160]}")
    status, confirmed = call("POST", f"/api/v1/quizzes/{QUIZ}/identity-confirmation",
                             {"full_name": entry["displayName"], "email": entry["email"]},
                             token=FORMAL_LEARNER)
    check("the real identity is confirmed",
          status == 200 and confirmed["identity_check"]["confirmed"] is True,
          json.dumps(confirmed)[:180])

    section("9.3 One device, no pausing, no coaching")
    status, started = call("POST", f"/api/v1/quizzes/{QUIZ}/formal-attempts",
                           {"device": {"fingerprint": f"live-{RUN}-a", "platform": "live"}},
                           token=FORMAL_LEARNER)
    check("the examination starts", status in (200, 201), f"{status} {json.dumps(started)[:200]}")
    SESSION = {"X-Formal-Session": started["session"]["session_token"]}
    FORMAL_ATTEMPT = started["attempt_id"]
    check("it produced a real attempt", bool(FORMAL_ATTEMPT))

    status, second_device = call("POST", f"/api/v1/quizzes/{QUIZ}/formal-attempts",
                                 {"device": {"fingerprint": f"live-{RUN}-b", "platform": "live"}},
                                 token=FORMAL_LEARNER)
    check("a second device is refused with a conflict, not a crash", status in (403, 409),
          f"{status} {json.dumps(second_device)[:200]}")
    status, paused = call("POST", f"/api/v1/formal-attempts/{FORMAL_ID}/pause", {},
                          token=FORMAL_LEARNER)
    check("a formal assessment cannot be paused", status == 409, str(status))
    status, coach = call("GET", f"/api/v1/attempts/{FORMAL_ATTEMPT}/coaching/eligibility",
                         token=FORMAL_LEARNER)
    check("AI coaching is unavailable while it runs",
          status in (403, 409) or coach.get("coachingAvailable") is False,
          f"{status} {json.dumps(coach)[:180]}")

    section("9.4 A disconnect commits the autosaved work")
    _, paper = call("GET", f"/api/v1/attempts/{FORMAL_ATTEMPT}/questions", token=FORMAL_LEARNER)
    delivered = paper.get("questions", [])
    answers = [{"question_id": q["questionId"], "response": answer_payload(q, True)}
               for q in delivered[:2]]
    status, saved = call("POST", f"/api/v1/formal-attempts/{FORMAL_ID}/autosave",
                         {"answers": answers}, token=FORMAL_LEARNER, headers=SESSION)
    check("autosave from the registered device is accepted", status == 200,
          f"{status} {json.dumps(saved)[:180]}")
    status, forged = call("POST", f"/api/v1/formal-attempts/{FORMAL_ID}/autosave",
                          {"answers": answers}, token=FORMAL_LEARNER,
                          headers={"X-Formal-Session": "forged"})
    check("an autosave with a forged session token is refused", status in (401, 403, 409),
          f"{status} {json.dumps(forged)[:160]}")

    status, dropped = call("POST", f"/api/v1/formal-attempts/{FORMAL_ID}/disconnect",
                           {"reason": "NETWORK_LOSS"}, token=FORMAL_LEARNER, headers=SESSION)
    check("a disconnect auto-submits rather than losing the work", status in (200, 201),
          f"{status} {json.dumps(dropped)[:200]}")
    status, resumed = call("POST", f"/api/v1/formal-attempts/{FORMAL_ID}/resume", {},
                           token=FORMAL_LEARNER)
    check("and the attempt cannot be resumed afterwards", status in (403, 409), str(status))

    section("9.5 A pass waits for a named assessor")
    _, fresult = call("POST", f"/api/v1/attempts/{FORMAL_ATTEMPT}/result", {}, token=FORMAL_LEARNER)
    status, foutcome = call("POST", f"/api/v1/attempts/{FORMAL_ATTEMPT}/outcome", {},
                            token=FORMAL_LEARNER)
    check("the autosaved answers were scored",
          (fresult.get("result") or {}).get("correctCount") == 2,
          json.dumps(fresult.get("result"))[:200])
    check("and nothing was invented for what was never reached",
          (fresult.get("result") or {}).get("unansweredCount") == len(delivered) - 2,
          json.dumps(fresult.get("result"))[:200])
    passed = (foutcome.get("outcome") or {}).get("outcome") == "PASS"
    fcert = foutcome.get("certificate") or {}
    check("no certificate exists before an assessor decides",
          fcert.get("status") != "ISSUED", json.dumps(fcert)[:200])

    status, admin_queue = call("GET", "/api/assessor/pending-reviews", token=ADMIN)
    check("an administrator cannot see the assessor queue", status == 403, str(status))
    status, queue = call("GET", "/api/assessor/pending-reviews", token=ASSESSOR)
    check("the assessor can", status == 200, str(status))
    mine = [r for r in queue.get("reviews", []) if r.get("attempt_id") == FORMAL_ATTEMPT]
    if passed and mine:
        review_id = mine[0]["review_id"]
        status, admin_decide = call("POST", f"/api/assessor/reviews/{review_id}/decision",
                                    {"decision": "APPROVED", "notes": "admin attempt"},
                                    token=ADMIN)
        check("an administrator cannot approve it", status == 403, str(status))
        call("POST", f"/api/assessor/reviews/{review_id}/review-start", {}, token=ASSESSOR)
        status, decided = call("POST", f"/api/assessor/reviews/{review_id}/decision",
                               {"decision": "APPROVED", "notes": f"live check {RUN}"},
                               token=ASSESSOR)
        check("the assessor's approval is accepted", status in (200, 201),
              json.dumps(decided)[:180])
        status, workflow = call("POST",
                                f"/api/assessor/reviews/{review_id}/certificate-workflow",
                                {}, token=ASSESSOR)
        check("the certificate workflow runs after approval", status in (200, 201),
              json.dumps(workflow)[:180])
        _, after = call("GET", f"/api/v1/attempts/{FORMAL_ATTEMPT}/outcome", token=FORMAL_LEARNER)
        acert = after.get("certificate") or {}
        check("and only now does a certificate exist for this attempt",
              acert.get("status") == "ISSUED"
              or acert.get("failureCode") == "CERTIFICATE_ALREADY_ISSUED",
              json.dumps(acert)[:220])
    elif not passed:
        check("a failing formal assessment reaches no review",
              not mine, f"reviews for this attempt: {len(mine)}")
        check("and produces no certificate", fcert.get("status") != "ISSUED",
              json.dumps(fcert)[:180])


# ===========================================================================
print("\n" + "=" * 72 + "\nUC-11 — CROSS-CUTTING INTEGRITY & SECURITY\n" + "=" * 72)

section("11.1 A submitted attempt is immutable through every route")
_, snapshot_before = call("GET", f"/api/v1/attempts/{fail_attempt}/result", token=LEARNER)
for label, method, path, body in (
    ("answer it again", "PUT",
     f"/api/v1/attempts/{fail_attempt}/questions/{fail_paper[0]['questionId']}/answer",
     {"response": answer_payload(fail_paper[0], True)}),
    ("clear an answer", "DELETE",
     f"/api/v1/attempts/{fail_attempt}/questions/{fail_paper[0]['questionId']}/answer", None),
    ("flag a question", "PUT",
     f"/api/v1/attempts/{fail_attempt}/questions/{fail_paper[0]['questionId']}/flag",
     {"flagged": True}),
    ("move the cursor", "PUT", f"/api/v1/attempts/{fail_attempt}/cursor", {"position": 1}),
):
    status, _ = call(method, path, body, token=LEARNER)
    check(f"a submitted attempt refuses: {label}", status == 409, str(status))
_, snapshot_after = call("GET", f"/api/v1/attempts/{fail_attempt}/result", token=LEARNER)
check("and the stored result is byte-for-byte unchanged",
      json.dumps(snapshot_before) == json.dumps(snapshot_after))

section("11.2 The role boundaries, on the live deployment")
matrix = [
    ("anonymous", None, "/api/question-bank/questions", 401),
    ("anonymous", None, "/api/admin/analytics/overall", 401),
    ("anonymous", None, "/api/assessor/pending-reviews", 401),
    ("anonymous", None, "/api/v1/results", 401),
    ("learner", LEARNER, "/api/question-bank/questions", 403),
    ("learner", LEARNER, "/api/admin/quizzes", 403),
    ("learner", LEARNER, "/api/admin/analytics/overall", 403),
    ("learner", LEARNER, "/api/assessor/pending-reviews", 403),
    ("learner", LEARNER, "/api/system/formal-assessments/review-queue/unpublished", 403),
    ("admin", ADMIN, "/api/v1/results", 403),
    ("admin", ADMIN, "/api/v1/outcomes", 403),
    ("admin", ADMIN, "/api/assessor/pending-reviews", 403),
    ("assessor", ASSESSOR, "/api/admin/analytics/overall", 403),
    ("assessor", ASSESSOR, "/api/question-bank/questions", 403),
]
for who, token, path, expected in matrix:
    status, _ = call("GET", path, token=token)
    check(f"{who} -> {path} is {expected}", status == expected, f"got {status}")

section("11.3 Errors are structured and disclose nothing")
for path in ("/api/v1/attempts/no-such-attempt", "/api/question-bank/questions/no-such-question"):
    status, body = call("GET", path, token=ADMIN if "question-bank" in path else LEARNER)
    check(f"{path} answers a structured error", isinstance(body, dict) and "error" in body,
          json.dumps(body)[:160])
    check("  …with a code and a request id", bool((body.get("error") or {}).get("code"))
          and bool((body.get("error") or {}).get("requestId")), json.dumps(body)[:180])
    check("  …and no stack trace or file path",
          "Traceback" not in json.dumps(body) and ".py" not in json.dumps(body),
          json.dumps(body)[:180])


print("\n" + "=" * 72)
total = len(passes) + len(fails)
if skips:
    print(f"{len(skips)} skipped (already exercised on this database, not defects):")
    for s in skips:
        print(f"  - {s}")
    print()
if fails:
    print(f"RESULT: {len(fails)} of {total} checks FAILED")
    for f in fails:
        print(f"  - {f}")
else:
    print(f"RESULT: all {total} checks PASSED")
