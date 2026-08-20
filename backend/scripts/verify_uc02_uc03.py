"""Drive UC-02 (Question Bank) and UC-03 (Quiz Attempt Delivery) as a real user.

    python -m scripts.verify_uc02_uc03 --base-url https://your-app.up.railway.app --yes

Everything goes over HTTP to a running deployment. Nothing reaches behind the API, and nothing is
read from a database — the answer key is read through the question bank *as the administrator*,
which is a legitimate read for that role and the same route the authoring screens use.

WHY THESE TWO TOGETHER
----------------------
UC-02 is what an author does and UC-03 is what a learner then receives, and the properties worth
checking live at the join: a retired question must leave the delivery pool without disturbing an
attempt that already froze it, and the answer key an author can see must be absent from everything
a learner is served.

WHAT IT WRITES
--------------
Questions, topics, a CSV import, and one or two attempts on the seeded unconfigured quiz — real rows
on whatever it targets. That is the point; a check that wrote nothing would prove nothing. It also
means this belongs on a review deployment rather than one holding data anybody depends on, which is
why `--yes` is required.

It is safe to re-run: an attempt left open by a previous run is submitted first, and every question
it authors is suffixed with a per-run id so the bank's duplicate rule is not tripped.
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


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        passes.append(label)
        print(f"  [PASS] {label}")
    else:
        fails.append(f"{label}{f' - {detail}' if detail else ''}")
        print(f"  [FAIL] {label}{f' - {detail}' if detail else ''}")


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def call(method, path, body=None, token=None, headers=None, raw=None, content_type=None):
    data = None
    hdrs = dict(headers or {})
    if raw is not None:
        data = raw
        hdrs["Content-Type"] = content_type or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body).encode()
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


# ---------------------------------------------------------------------------
# Identities, straight from the deployment
# ---------------------------------------------------------------------------
_, session = call("GET", "/api/session")
directory = {u["role"]: u for u in session["users"]}
learners = [u for u in session["users"] if u["role"] == "learner"]
ADMIN = directory["admin"]["token"]
LEARNER = learners[0]["token"]
LEARNER2 = learners[1]["token"]

print(f"target : {BASE}")
print(f"run    : {RUN}")


def question(kind: str, suffix: str) -> dict:
    """A valid payload per type, unique to this run so the bank's duplicate rule is not tripped."""
    base = {"difficulty": "MEDIUM", "topics": [f"Live Check {RUN}"]}
    if kind == "SINGLE_CHOICE":
        return {**base, "type": kind,
                "questionText": f"[{RUN}-{suffix}] Which port does HTTPS use by default?",
                "explanation": "443 is the registered port for HTTP over TLS.",
                "options": [{"label": "A", "text": "21", "isCorrect": False},
                            {"label": "B", "text": "80", "isCorrect": False},
                            {"label": "C", "text": "443", "isCorrect": True},
                            {"label": "D", "text": "8080", "isCorrect": False}],
                "scoring": {"points": 1, "scoringStrategy": "ALL_OR_NOTHING"}}
    if kind == "TRUE_FALSE":
        return {**base, "type": kind,
                "questionText": f"[{RUN}-{suffix}] TLS 1.3 removed support for RSA key exchange.",
                "explanation": "TLS 1.3 dropped static RSA to guarantee forward secrecy.",
                "options": [{"label": "TRUE", "text": "True", "isCorrect": True},
                            {"label": "FALSE", "text": "False", "isCorrect": False}],
                "scoring": {"points": 1}}
    if kind == "MULTI_SELECT":
        return {**base, "type": kind,
                "questionText": f"[{RUN}-{suffix}] Which of these are symmetric ciphers?",
                "explanation": "AES and ChaCha20 are symmetric; RSA and ECDSA are not.",
                "options": [{"label": "A", "text": "AES", "isCorrect": True},
                            {"label": "B", "text": "ChaCha20", "isCorrect": True},
                            {"label": "C", "text": "RSA", "isCorrect": False},
                            {"label": "D", "text": "ECDSA", "isCorrect": False}],
                "scoring": {"points": 2, "scoringStrategy": "PARTIAL_CREDIT_WITH_PENALTY",
                            "penaltyPerIncorrect": 0.5}}
    if kind == "DRAG_TO_ORDER":
        return {**base, "type": kind,
                "questionText": f"[{RUN}-{suffix}] Order the TLS handshake steps.",
                "explanation": "Hello, certificate, key exchange, finished.",
                "options": [{"label": "A", "text": "ClientHello", "position": 1, "correctPosition": 1},
                            {"label": "B", "text": "ServerHello", "position": 2, "correctPosition": 2},
                            {"label": "C", "text": "Certificate", "position": 3, "correctPosition": 3},
                            {"label": "D", "text": "Finished", "position": 4, "correctPosition": 4}],
                "scoring": {"points": 4, "scoringStrategy": "PARTIAL_CREDIT"}}
    if kind == "SCENARIO":
        return {**base, "type": kind,
                "questionText": f"[{RUN}-{suffix}] What should the engineer check first?",
                "scenarioText": ("A user reports that an internal site fails to load over HTTPS but "
                                 "works over HTTP. Other HTTPS sites load normally."),
                "explanation": "A certificate mismatch is the first thing to rule out.",
                "options": [{"label": "A", "text": "The certificate's validity and name", "isCorrect": True},
                            {"label": "B", "text": "The user's keyboard layout", "isCorrect": False},
                            {"label": "C", "text": "The office printer queue", "isCorrect": False}],
                "scoring": {"points": 2}}
    raise AssertionError(kind)


# ===========================================================================
print("\n" + "=" * 72)
print("UC-02 — QUESTION BANK MANAGEMENT, as an author")
print("=" * 72)

section("2.1 Authoring one question of every supported type")
created: dict[str, dict] = {}
for kind in ("SINGLE_CHOICE", "TRUE_FALSE", "MULTI_SELECT", "DRAG_TO_ORDER", "SCENARIO"):
    status, body = call("POST", "/api/question-bank/questions", question(kind, "a"), token=ADMIN)
    ok = status == 201
    if ok:
        created[kind] = body.get("question", body)
    check(f"create {kind}", ok, f"{status} {json.dumps(body)[:160]}")

check("all five types were accepted", len(created) == 5, str(sorted(created)))
if len(created) == 5:
    refs = [q.get("reference") for q in created.values()]
    check("each is given a stable human reference", all(refs), str(refs))
    check("references are unique", len(set(refs)) == 5, str(refs))

section("2.2 The backend validates, whatever a client sends")
bad_cases = [
    ("no correct option", {**question("SINGLE_CHOICE", "bad1"),
                           "options": [{"label": "A", "text": "one", "isCorrect": False},
                                       {"label": "B", "text": "two", "isCorrect": False}]}),
    ("duplicate option labels", {**question("SINGLE_CHOICE", "bad2"),
                                 "options": [{"label": "A", "text": "one", "isCorrect": True},
                                             {"label": "A", "text": "two", "isCorrect": False}]}),
    ("two correct on single choice", {**question("SINGLE_CHOICE", "bad3"),
                                      "options": [{"label": "A", "text": "one", "isCorrect": True},
                                                  {"label": "B", "text": "two", "isCorrect": True}]}),
    ("empty question text", {**question("SINGLE_CHOICE", "bad4"), "questionText": ""}),
    ("unknown type", {**question("SINGLE_CHOICE", "bad5"), "type": "NOT_A_TYPE"}),
]
for label, payload in bad_cases:
    status, body = call("POST", "/api/question-bank/questions", payload, token=ADMIN)
    check(f"rejected: {label}", status in (400, 422),
          f"{status} {json.dumps(body)[:140]}")
    if status in (400, 422):
        has_detail = bool((body.get("error") or {}).get("details") or (body.get("error") or {}).get("message"))
        check(f"  …and says why: {label}", has_detail, json.dumps(body)[:140])

status, body = call("POST", "/api/question-bank/questions",
                    question("SINGLE_CHOICE", "a"), token=ADMIN)
check("an exact duplicate of an existing question is refused", status in (400, 409, 422),
      f"{status} {json.dumps(body)[:140]}")

section("2.3 Reading the bank back")
status, listing = call("GET", "/api/question-bank/questions?pageSize=100", token=ADMIN)
check("the question list reads", status == 200, str(status))
items = listing.get("items", listing.get("questions", [])) if isinstance(listing, dict) else []
mine = [q for q in items if RUN in (q.get("questionText") or "")]
check("this run's questions appear in the list", len(mine) >= 5, f"found {len(mine)}")

first = created.get("SINGLE_CHOICE", {})
qid = first.get("id") or first.get("questionId")
status, single = call("GET", f"/api/question-bank/questions/{qid}", token=ADMIN)
check("one question reads by id", status == 200, str(status))
detail = single.get("question", single) if isinstance(single, dict) else {}
check("the author sees the answer key", any(o.get("isCorrect") for o in detail.get("options", [])),
      json.dumps(detail)[:160])

status, filtered = call("GET", "/api/question-bank/questions?type=MULTI_SELECT&pageSize=100",
                        token=ADMIN)
fitems = filtered.get("items", filtered.get("questions", [])) if isinstance(filtered, dict) else []
check("filtering by type returns only that type", status == 200 and fitems
      and all(q.get("type") == "MULTI_SELECT" for q in fitems),
      f"{status} {len(fitems)} items")

section("2.4 Editing: what versions a question, and what does not")
# The rule is explicit in the service: explanation, difficulty, external ref, topics and status are
# metadata; changing one does not alter what the question *asks*, so it does not version. Changing
# the text, the options or the scoring does. Both halves are checked, because "everything makes a
# version" and "nothing does" are both wrong and both look fine from one example.
status, meta_edit = call("PATCH", f"/api/question-bank/questions/{qid}",
                         {"explanation": f"Rewritten during live check {RUN}."}, token=ADMIN)
check("a metadata-only edit is accepted", status == 200, f"{status} {json.dumps(meta_edit)[:140]}")
check("and does NOT create a new version",
      (meta_edit.get("question", meta_edit) or {}).get("version") == 1,
      f"version={(meta_edit.get('question', meta_edit) or {}).get('version')}")

original_text = detail.get("questionText")
status, content_edit = call("PATCH", f"/api/question-bank/questions/{qid}",
                            {"questionText": f"[{RUN}-a] Which port does HTTPS use, by default?"},
                            token=ADMIN)
check("a content edit is accepted", status == 200, f"{status} {json.dumps(content_edit)[:140]}")
new_version_no = (content_edit.get("question", content_edit) or {}).get("version")
check("and DOES create a new version", new_version_no == 2, f"version={new_version_no}")

status, versions = call("GET", f"/api/question-bank/questions/{qid}/versions", token=ADMIN)
# Returned as a bare JSON array, not wrapped.
vlist = versions if isinstance(versions, list) else versions.get("versions", [])
check("both versions are in the history", status == 200 and len(vlist) == 2,
      f"{status} {len(vlist)} versions")
check("numbered 1 and 2", sorted(v.get("version") for v in vlist) == [1, 2],
      str([v.get("version") for v in vlist]))

status, v1 = call("GET", f"/api/question-bank/questions/{qid}/versions/1", token=ADMIN)
snapshot = v1 if isinstance(v1, dict) else {}
check("version 1 still holds the ORIGINAL text, not the edit",
      snapshot.get("questionText") == original_text,
      f"{str(snapshot.get('questionText'))[:60]!r} vs {str(original_text)[:60]!r}")
status, missing = call("GET", f"/api/question-bank/questions/{qid}/versions/99", token=ADMIN)
check("an unknown version is a clean 404", status == 404, str(status))

section("2.5 Topics")
status, topic = call("POST", "/api/question-bank/topics",
                     {"name": f"Live Topic {RUN}", "description": "created by the live check"},
                     token=ADMIN)
check("a topic can be created", status in (200, 201), f"{status} {json.dumps(topic)[:140]}")
topic_id = (topic.get("topic") or topic).get("id") if isinstance(topic, dict) else None
status, assigned = call("POST", f"/api/question-bank/questions/{qid}/topics",
                        {"topicIds": [topic_id]}, token=ADMIN)
check("a topic can be attached to a question", status in (200, 201),
      f"{status} {json.dumps(assigned)[:140]}")
status, topics = call("GET", "/api/question-bank/topics", token=ADMIN)
tlist = topics if isinstance(topics, list) else topics.get("topics", [])
check("the topic list includes it", status == 200
      and any(t.get("name") == f"Live Topic {RUN}" for t in tlist), f"{status} {len(tlist)}")

section("2.6 CSV import — the bulk path")
status, template = call("GET", "/api/question-bank/imports/template", token=ADMIN)
check("the CSV template downloads", status == 200 and "type" in str(template)[:200], str(status))

csv_body = (
    "type,question_text,scenario_text,options,correct_answers,correct_order,explanation,topics,"
    "points,scoring_strategy,penalty_per_incorrect,difficulty,external_ref\n"
    f'TRUE_FALSE,"[{RUN}] HTTP 204 means No Content",,,TRUE,,"204 has no body.",HTTP,1,,,EASY,LIVE-{RUN}-1\n'
    f'SINGLE_CHOICE,"[{RUN}] Which status means Not Found?",,"A:200|B:301|C:404|D:500",C,,'
    f'"404 is Not Found.",HTTP,1,,,EASY,LIVE-{RUN}-2\n'
    f'badtype,"[{RUN}] This row has an invalid type",,"A:1|B:2",A,,"x",HTTP,1,,,,LIVE-{RUN}-3\n'
)
boundary = f"----live{RUN}"
parts = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="live.csv"\r\n'
    f"Content-Type: text/csv\r\n\r\n{csv_body}\r\n--{boundary}--\r\n"
).encode()
status, imported = call("POST", "/api/question-bank/imports", raw=parts, token=ADMIN,
                        content_type=f"multipart/form-data; boundary={boundary}")
check("a CSV import is accepted", status in (200, 201), f"{status} {json.dumps(imported)[:200]}")
if status in (200, 201):
    summary = imported.get("import", imported)
    ok_rows = summary.get("importedRows", summary.get("imported_rows"))
    bad_rows = summary.get("rejectedRows", summary.get("rejected_rows"))
    check("the two valid rows were imported", ok_rows == 2, f"imported={ok_rows}")
    check("the invalid row was rejected, not silently dropped", bad_rows == 1,
          f"rejected={bad_rows}")
    check("the import reports per-row errors", bool(summary.get("errors") or summary.get("id")),
          json.dumps(summary)[:160])

section("2.7 Retirement, reactivation, and the delete rule")
retire_target = (created.get("SCENARIO") or {}).get("id") or (created.get("SCENARIO") or {}).get("questionId")
status, retired = call("POST", f"/api/question-bank/questions/{retire_target}/retire",
                       {"reason": f"live check {RUN}"}, token=ADMIN)
check("a question can be retired", status == 200, f"{status} {json.dumps(retired)[:140]}")
status, after = call("GET", f"/api/question-bank/questions/{retire_target}", token=ADMIN)
state = (after.get("question", after) or {}).get("status")
check("its status reads RETIRED", state == "RETIRED", str(state))
status, pool = call("GET", "/api/question-bank/delivery/pool?type=SCENARIO&limit=200", token=ADMIN)
pool_ids = [q.get("questionId") or q.get("id") for q in
            (pool.get("questions", pool.get("items", [])) if isinstance(pool, dict) else [])]
check("a retired question leaves the delivery pool", retire_target not in pool_ids,
      f"pool size {len(pool_ids)}")
status, reactivated = call("POST", f"/api/question-bank/questions/{retire_target}/reactivate",
                           {}, token=ADMIN)
check("it can be reactivated", status == 200, f"{status} {json.dumps(reactivated)[:140]}")

section("2.8 The answer key is an author's to see, and nobody else's")
status, learner_read = call("GET", f"/api/question-bank/questions/{qid}", token=LEARNER)
check("a learner cannot read the question bank at all", status == 403, str(status))
status, anon_read = call("GET", f"/api/question-bank/questions/{qid}")
check("nor can an anonymous caller", status == 401, str(status))


# ===========================================================================
print("\n" + "=" * 72)
print("UC-03 — QUIZ ATTEMPT DELIVERY, as a learner")
print("=" * 72)

# The unconfigured seeded quiz, configured now — the real administrator journey, and it leaves the
# learner a full allowance to sit.
_, quizzes = call("GET", "/api/quizzes", token=LEARNER)
QUIZ = next(q["id"] for q in quizzes["quizzes"] if q["slug"] == "end-of-course-assessment")

section("3.0 An administrator configures the quiz first")
configuration = {
    "questionCount": 5, "timeLimitMinutes": 30, "passMark": 60,
    "questionTypes": [{"type": "SINGLE_CHOICE", "quota": 3}, {"type": "TRUE_FALSE", "quota": 2}],
    "randomiseQuestions": False,
    # Generous on purpose. Every run of this script sits an attempt, and the allowance counts
    # against the *active* version, so a realistic 3 leaves the script unable to run a fourth time —
    # which is the rule working, not a defect, but it makes the script useless as a repeatable
    # check. UC-08's own tests and verify_e2e section 30 exercise the allowance properly.
    "maxAttempts": 50,
    "deliveryMode": "assessment",
    # The main flow reads the whole paper, so it is configured ALL_AT_ONCE. Section 3.13 publishes
    # ONE_AT_A_TIME and checks that mode's rule separately — a presentation is locked onto an
    # attempt at creation, so the two cannot be exercised by one attempt.
    "questionPresentation": "ALL_AT_ONCE",
}
status, saved = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration", configuration, token=ADMIN)
check("the quiz is configured", status in (200, 201), f"{status} {json.dumps(saved)[:200]}")
version_id = ((saved.get("configuration") or {}).get("id")) if isinstance(saved, dict) else None
version_no = ((saved.get("configuration") or {}).get("versionNumber")) if isinstance(saved, dict) else None
check("an immutable version was published", version_id is not None, str(version_id))

# A previous run may have left an attempt open — `ux_attempt_single_open` permits exactly one,
# which is the rule under test in 3.2 below. Clear it first so this run starts from a known place.
status, existing = call("GET", f"/api/v1/attempts/active?quizId={QUIZ}", token=LEARNER)
open_attempt = (existing.get("attempt") or {}).get("attemptId") if isinstance(existing, dict) else None
if open_attempt:
    call("POST", f"/api/v1/attempts/{open_attempt}/submission", {"confirmed": True}, token=LEARNER)
    print(f"  [note] submitted an attempt left open by a previous run ({open_attempt[:8]})")

section("3.1 What the learner is told before starting")
status, rules = call("GET", f"/api/quizzes/{QUIZ}/rules", token=LEARNER)
check("the rules read", status == 200, str(status))
check("the rules match the configuration just published",
      rules.get("questionCount") == 5 and rules.get("passMark") == 60
      and rules.get("timeLimitMinutes") == 30,
      json.dumps(rules)[:200])
status, eligibility_body = call(
    "GET", f"/api/v1/quizzes/{QUIZ}/attempt-eligibility", token=LEARNER
)
eligibility = eligibility_body.get("eligibility", eligibility_body)
check("eligibility says the learner may start",
      status == 200 and eligibility.get("eligible") is True,
      f"{status} {json.dumps(eligibility_body)[:200]}")
check("and it explains the allowance rather than just saying yes",
      eligibility.get("attemptsUsed") is not None and eligibility.get("maxAttempts") is not None,
      json.dumps(eligibility)[:200])
check("the learner's enrolment is checked, not assumed",
      eligibility.get("enrolled") is True and eligibility.get("enrolmentStatus") == "ACTIVE",
      json.dumps(eligibility)[:200])

section("3.2 Starting an attempt")
status, started = call("POST", "/api/v1/attempts", {"quizId": str(QUIZ)}, token=LEARNER)
check("an attempt is created", status == 201, f"{status} {json.dumps(started)[:200]}")
attempt = started["attempt"]
ATTEMPT = attempt["attemptId"]
check("it is locked to the configuration version published above",
      str(attempt.get("configurationVersionId")) == str(version_id),
      f"{attempt.get('configurationVersionId')} vs {version_id}")
check("the paper is the configured size", attempt.get("totalQuestions") == 5,
      str(attempt.get("totalQuestions")))
check("it starts ACTIVE", attempt.get("status") == "ACTIVE", str(attempt.get("status")))

status, again = call("POST", "/api/v1/attempts", {"quizId": str(QUIZ)}, token=LEARNER)
check("a second concurrent attempt is refused", again and status == 409, f"{status}")

section("3.3 The delivered paper carries no answer key")
status, paper = call("GET", f"/api/v1/attempts/{ATTEMPT}/questions", token=LEARNER)
check("the questions are delivered", status == 200 and len(paper.get("questions", [])) == 5,
      f"{status} {len(paper.get('questions', []))}")
blob = json.dumps(paper)
for field in ("isCorrect", "correctPosition", "isPrimary", "correctAnswer", "explanation"):
    check(f"the paper does not leak '{field}'", field not in blob)
questions = paper["questions"]
check("each delivered question has options but no marking data",
      all(q.get("options") or q.get("subQuestions") for q in questions))

section("3.4 Answering, autosaving, and the idempotency that makes autosave safe")
q1, q2, q3, q4, q5 = questions


def answer_for(q, correct=True):
    if q["questionType"] == "TRUE_FALSE":
        return {"value": bool(correct)}
    options = q["options"]
    return {"selectedOptionId": options[0]["optionId"] if correct else options[-1]["optionId"]}


status, first_save = call("PUT",
                          f"/api/v1/attempts/{ATTEMPT}/questions/{q1['questionId']}/answer",
                          {"response": answer_for(q1), "source": "MANUAL"}, token=LEARNER)
check("one answer saves", status == 200, f"{status} {json.dumps(first_save)[:160]}")
rev = first_save["answer"]["revision"]
check("the save reports a revision and that it changed",
      first_save["answer"]["changed"] is True and rev >= 1, json.dumps(first_save)[:160])

status, resave = call("PUT", f"/api/v1/attempts/{ATTEMPT}/questions/{q1['questionId']}/answer",
                      {"response": answer_for(q1), "source": "AUTOSAVE"}, token=LEARNER)
check("re-saving the same answer is idempotent (changed=false, revision unchanged)",
      status == 200 and resave["answer"]["changed"] is False
      and resave["answer"]["revision"] == rev, json.dumps(resave)[:160])

status, batch = call("POST", f"/api/v1/attempts/{ATTEMPT}/answers",
                     {"answers": [{"questionId": q2["questionId"], "response": answer_for(q2)},
                                  {"questionId": q3["questionId"], "response": answer_for(q3)}],
                      "source": "AUTOSAVE"}, token=LEARNER)
check("a batch autosave saves several answers at once",
      status == 200 and batch.get("savedCount") == 2, f"{status} {json.dumps(batch)[:160]}")
check("the autosave returns an authoritative clock with it",
      bool(batch.get("persistedAt")) and batch.get("timing", {}).get("remainingSeconds", 0) > 0,
      json.dumps(batch.get("timing"))[:140])

status, bad_batch = call("POST", f"/api/v1/attempts/{ATTEMPT}/answers",
                         {"answers": [{"questionId": q4["questionId"], "response": answer_for(q4)},
                                      {"questionId": q5["questionId"],
                                       "response": {"value": "not-a-boolean"}}]}, token=LEARNER)
check("one malformed entry rejects the whole batch", bad_batch and status == 422, str(status))
status, sheet = call("GET", f"/api/v1/attempts/{ATTEMPT}/answers", token=LEARNER)
check("and nothing from the rejected batch was written",
      sheet.get("answeredCount") == 3, f"answered={sheet.get('answeredCount')}")

section("3.5 Flagging, navigating, and resuming")
status, flagged = call("PUT", f"/api/v1/attempts/{ATTEMPT}/questions/{q4['questionId']}/flag",
                       {"flagged": True}, token=LEARNER)
check("a question can be flagged for review", status == 200, f"{status}")
status, flags = call("GET", f"/api/v1/attempts/{ATTEMPT}/flags", token=LEARNER)
flag_ids = [f.get("questionId") for f in (flags.get("flags") or flags.get("items") or [])]
check("the flag is listed", q4["questionId"] in flag_ids, json.dumps(flags)[:160])
status, _ = call("DELETE", f"/api/v1/attempts/{ATTEMPT}/questions/{q4['questionId']}/flag",
                 token=LEARNER)
check("and can be cleared", status in (200, 204), str(status))

status, cursor = call("PUT", f"/api/v1/attempts/{ATTEMPT}/cursor", {"position": 4}, token=LEARNER)
check("the resume position can be moved", status == 200, f"{status}")
status, state_body = call("GET", f"/api/v1/attempts/{ATTEMPT}/state", token=LEARNER)
state = state_body.get("state", state_body)
check("and the attempt remembers it", state.get("currentPosition") == 4,
      json.dumps(state_body)[:160])
status, at3 = call("GET", f"/api/v1/attempts/{ATTEMPT}/questions/at/3", token=LEARNER)
check("a learner can revisit any question by position",
      status == 200 and at3.get("question", {}).get("position") == 3, str(status))

section("3.6 A reload restores exactly what was saved")
status, reloaded = call("GET", f"/api/v1/attempts/{ATTEMPT}/answers", token=LEARNER)
check("the answer sheet reads back", status == 200, str(status))
check("every delivered question is listed, answered or not",
      len(reloaded.get("answers", [])) == 5, str(len(reloaded.get("answers", []))))
answered = [a for a in reloaded["answers"] if a["answered"]]
check("three answered, two still unanswered — explicit, not inferred",
      len(answered) == 3, f"{len(answered)} answered")
status, revisions = call("GET", f"/api/v1/attempts/{ATTEMPT}/answers/revisions", token=LEARNER)
check("an audit trail of accepted saves exists", status == 200
      and revisions.get("count", 0) >= 3, f"{status} {revisions.get('count')}")

section("3.7 Time is the server's")
status, timing_body = call("GET", f"/api/v1/attempts/{ATTEMPT}/timing", token=LEARNER)
timing = timing_body.get("timing", timing_body)
check("timing reads", status == 200, str(status))
check("it reports remaining seconds within the configured 30 minutes",
      0 < timing.get("remainingSeconds", 0) <= 30 * 60,
      json.dumps(timing)[:200])
check("it names the server's own clock, so a client can resync",
      bool(timing.get("serverTime")) and bool(timing.get("serverTimeEpochMs")),
      json.dumps(timing)[:200])
check("and the expiry it will enforce",
      bool(timing.get("expiresAt")) and timing.get("status") == "ACTIVE",
      json.dumps(timing)[:200])

section("3.8 A configuration change cannot reach a running attempt")
# Only the pass mark changes: the quotas still have to sum to `questionCount`, and an inconsistent
# payload is correctly refused with 422 by UC-01's own validation, which is a different thing from
# the property under test here.
status, republished = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration",
                           {**configuration, "passMark": 95}, token=ADMIN)
check("the administrator publishes a new version mid-attempt", status in (200, 201),
      f"{status} {json.dumps(republished)[:200]}")
new_version = ((republished.get("configuration") or {}).get("id")) if isinstance(republished, dict) else None
check("it really is a different version",
      new_version is not None and str(new_version) != str(version_id),
      f"{version_id} -> {new_version}")
status, still = call("GET", f"/api/v1/attempts/{ATTEMPT}", token=LEARNER)
locked = (still.get("attempt") or {}).get("configurationVersionId")
check("the running attempt is still locked to the original version",
      str(locked) == str(version_id), f"{locked} vs {version_id}")
status, paper_again = call("GET", f"/api/v1/attempts/{ATTEMPT}/questions", token=LEARNER)
check("and its paper is unchanged — still five questions",
      len(paper_again.get("questions", [])) == 5,
      str(len(paper_again.get("questions", []))))

section("3.9 Another learner cannot touch this attempt")
for label, path in (("read the attempt", f"/api/v1/attempts/{ATTEMPT}"),
                    ("read its questions", f"/api/v1/attempts/{ATTEMPT}/questions"),
                    ("read its answers", f"/api/v1/attempts/{ATTEMPT}/answers")):
    status, _ = call("GET", path, token=LEARNER2)
    check(f"a second learner cannot {label}", status in (403, 404), str(status))
status, _ = call("PUT", f"/api/v1/attempts/{ATTEMPT}/questions/{q1['questionId']}/answer",
                 {"response": answer_for(q1, correct=False)}, token=LEARNER2)
check("nor answer on their behalf", status in (403, 404), str(status))

section("3.10 Submission")
status, preview_body = call("GET", f"/api/v1/attempts/{ATTEMPT}/submission/preview", token=LEARNER)
preview = preview_body.get("preview", preview_body)
check("a preview reports what is about to be submitted", status == 200, str(status))
check("the preview counts the two unanswered questions",
      preview.get("unansweredCount") == 2, json.dumps(preview)[:220])
check("and names which ones, so the learner can go back to them",
      len(preview.get("unanswered", [])) == 2, json.dumps(preview)[:220])
check("it reports three answered of five", preview.get("answeredCount") == 3
      and preview.get("totalQuestions") == 5, json.dumps(preview)[:220])

status, submitted = call("POST", f"/api/v1/attempts/{ATTEMPT}/submission",
                         {"confirmed": True}, token=LEARNER)
check("the attempt submits", status == 200, f"{status} {json.dumps(submitted)[:180]}")
check("it reports SUBMITTED", (submitted.get("attempt") or {}).get("status") == "SUBMITTED",
      json.dumps(submitted)[:180])

status, resubmit = call("POST", f"/api/v1/attempts/{ATTEMPT}/submission",
                        {"confirmed": True}, token=LEARNER)
check("submitting again is idempotent, not a second submission", status in (200, 409),
      f"{status} {json.dumps(resubmit)[:160]}")

section("3.11 A submitted attempt is closed to further writes")
status, late = call("PUT", f"/api/v1/attempts/{ATTEMPT}/questions/{q5['questionId']}/answer",
                    {"response": answer_for(q5)}, token=LEARNER)
check("an answer after submission is refused", status == 409, str(status))
status, late_batch = call("POST", f"/api/v1/attempts/{ATTEMPT}/answers",
                          {"answers": [{"questionId": q5["questionId"], "response": answer_for(q5)}],
                           "source": "AUTOSAVE"}, token=LEARNER)
check("a late autosave is refused too", status == 409, str(status))
status, late_flag = call("PUT", f"/api/v1/attempts/{ATTEMPT}/questions/{q4['questionId']}/flag",
                         {"flagged": True}, token=LEARNER)
check("so is a late flag", status == 409, str(status))

section("3.12 The submitted attempt is scored, and the paper it froze is intact")
status, result = call("POST", f"/api/v1/attempts/{ATTEMPT}/result", {}, token=LEARNER)
check("the attempt scores", status in (200, 201), f"{status} {json.dumps(result)[:160]}")
scored = result.get("result", {})
check("three answered, two unanswered — as sat", scored.get("unansweredCount") == 2,
      json.dumps(scored)[:200])
check("it is judged against the version it locked, not the new one",
      str(scored.get("configurationVersionId")) == str(version_id),
      f"{scored.get('configurationVersionId')} vs {version_id}")
check("the pass mark applied is the locked 60, not the republished 95",
      scored.get("passMarkPercentage") == 60, str(scored.get("passMarkPercentage")))

section("3.13 The other delivery mode: one question at a time")
# Presentation is locked onto an attempt when it is created, so this needs its own attempt under a
# configuration that asks for it. The rule is not cosmetic: a client that could pull the whole paper
# would defeat the point of delivering it one question at a time.
status, one_at_a_time = call("PUT", f"/api/admin/quizzes/{QUIZ}/configuration",
                             {**configuration, "questionPresentation": "ONE_AT_A_TIME"},
                             token=ADMIN)
check("the quiz can be reconfigured to one-at-a-time", status in (200, 201), str(status))

status, seq = call("POST", "/api/v1/attempts", {"quizId": str(QUIZ)}, token=LEARNER)
if status != 201:
    check("a one-at-a-time attempt starts", False, f"{status} {json.dumps(seq)[:160]}")
else:
    SEQ = seq["attempt"]["attemptId"]
    check("a one-at-a-time attempt starts", True)
    check("the attempt records its presentation",
          seq["attempt"].get("questionPresentation") == "ONE_AT_A_TIME",
          str(seq["attempt"].get("questionPresentation")))

    status, refused = call("GET", f"/api/v1/attempts/{SEQ}/questions", token=LEARNER)
    check("asking for the whole paper is refused", status == 409, str(status))
    check("and the refusal names the rule, not a generic error",
          (refused.get("error") or {}).get("code") == "QUESTION_PRESENTATION_VIOLATION",
          json.dumps(refused)[:200])

    status, current = call("GET", f"/api/v1/attempts/{SEQ}/questions/current", token=LEARNER)
    check("the current question is served", status == 200 and current.get("question"), str(status))
    check("it is position 1 for a fresh attempt",
          (current.get("question") or {}).get("position") == 1,
          str((current.get("question") or {}).get("position")))
    check("and it carries no answer key either",
          "isCorrect" not in json.dumps(current) and "correctPosition" not in json.dumps(current))

    status, second = call("GET", f"/api/v1/attempts/{SEQ}/questions/at/2", token=LEARNER)
    check("a specific position is servable", status == 200
          and (second.get("question") or {}).get("position") == 2, str(status))
    status, beyond = call("GET", f"/api/v1/attempts/{SEQ}/questions/at/99", token=LEARNER)
    check("a position past the end is a clean refusal", status in (400, 404, 422), str(status))
    check("with a structured error, not a stack trace",
          isinstance(beyond, dict) and bool((beyond.get("error") or {}).get("code"))
          and "Traceback" not in json.dumps(beyond),
          json.dumps(beyond)[:160])

    call("POST", f"/api/v1/attempts/{SEQ}/submission", {"confirmed": True}, token=LEARNER)
    print("  [note] submitted the one-at-a-time attempt to leave the slot clear")

print("\n" + "=" * 72)
total = len(passes) + len(fails)
if fails:
    print(f"RESULT: {len(fails)} of {total} checks FAILED")
    for f in fails:
        print(f"  - {f}")
else:
    print(f"RESULT: all {total} checks PASSED")
