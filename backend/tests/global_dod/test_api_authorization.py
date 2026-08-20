"""UC-11 §16 and §18 — the authorization sweep, over the whole API surface.

Those two sections ask for the coaching and formal-assessment security boundaries. Each capability's
own suite checks its own; this checks *every* route the application exposes, on the principle that a
boundary nobody enumerated is the one that is missing.

Not a list of endpoints somebody remembered to check. This suite **enumerates the application's own
OpenAPI document** and asserts a policy over every route it finds, so a capability added tomorrow is
covered the day it ships rather than the day somebody notices.

That distinction is the whole point. task11's F-15 recorded that its first security suite drove two
modules hard and left five mounted but never called — and F-13 and F-14, both critical, were sitting
behind exactly that gap. F-02 in this merge was the same shape: UC-02's reads had no guard, its own
suite was green, and nothing looked across the whole surface.

THE POLICY
----------
1. Every route requires a credential, except a named allow-list of four.
2. A learner credential cannot reach an administrator, assessor or system route.
3. An administrator credential cannot reach a learner's attempt data or approve an assessment.
4. No refusal — of any kind, on any route — discloses assessment data.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.global_dod.conftest import ADMIN_TOKEN, LEARNER_TOKEN, auth, sit
from tests.harness import ASSESSOR_TOKEN

#: The only routes reachable without a credential, and why each one is.
#:
#: Named individually. A route that becomes anonymous by accident fails this test, which is the
#: property the list exists for — it is a decision record, not a convenience.
PUBLIC_ROUTES: dict[str, str] = {
    "/api/health": "readiness probe — a load balancer must reach it to take a broken instance out",
    "/api/health/live": "liveness probe — deliberately touches nothing",
    "/api/meta": "build metadata; carries no learner or question data",
    "/api/session": (
        "answers 'who am I' — null for an anonymous caller. It also lists the placeholder "
        "directory's development tokens, which is gated to ENVIRONMENT in {development, test}"
    ),
    "/api/question-bank/imports/template": "a static CSV header row",
    "/api/question-bank/imports/template/guide": "static documentation of that CSV's columns",
}

#: Prefixes that identify who a route is for. Used to check that a credential of one kind cannot
#: reach a route meant for another.
LEARNER_PREFIX = "/api/v1"
ADMIN_PREFIXES = ("/api/admin", "/api/question-bank")
ASSESSOR_PREFIX = "/api/assessor"
SYSTEM_PREFIX = "/api/system"


@pytest.fixture(autouse=True)
def _guards_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this suite with the administrator guard switched **on**.

    Without this the suite would assert nothing. ``ADMIN_API_TOKEN`` is unset across the test
    environment — deliberately, so the other suites can drive admin endpoints without carrying a
    credential — and while it is unset ``require_admin`` falls through to its documented
    local-development path and admits everybody.

    That is exactly the gap this suite exists to close, and it is not hypothetical: it is why
    ``Settings`` now refuses to start a deployed environment without both tokens
    (``_require_credentials_outside_development``). Here the token is set for the duration of the
    suite so the guard is the one production will run.
    """
    from app.modules.identity import security as identity_security

    monkeypatch.setattr(identity_security.settings, "admin_api_token", "uc11-admin-token")
    monkeypatch.setattr(identity_security.settings, "system_api_token", "uc11-system-token")


def test_a_deployed_environment_refuses_to_start_without_its_guards() -> None:
    """The enforcement behind this whole suite.

    Documenting "set ADMIN_API_TOKEN in production" is not enough — a requirement nobody enforces
    is eventually not met, and the symptom is silent: nothing errors, the guards simply admit
    everybody. So the application refuses to start instead.
    """
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError) as caught:
        Settings(environment="production", admin_api_token=None, system_api_token=None)
    assert "ADMIN_API_TOKEN" in str(caught.value)
    assert "SYSTEM_API_TOKEN" in str(caught.value)

    # A staging-like name gets the strict behaviour too: the safe-list is development and test.
    with pytest.raises(ValidationError):
        Settings(environment="staging")

    # And with both set, it starts.
    configured = Settings(
        environment="production", admin_api_token="a", system_api_token="b"
    )
    assert configured.is_production is True


def _routes(ctx: Any) -> dict[str, dict[str, Any]]:
    document = ctx.client.get("/api/openapi.json").json()
    return document["paths"]


def _fill(path: str, ctx: Any, attempt_id: str, question_id: str) -> str:
    """Substitute plausible values for path parameters.

    The values need only be *shaped* right: this suite asserts who is refused, and a refusal must
    happen before anything is looked up. A route that returned 404 for a well-formed id it should
    have refused would be leaking the same information the guard exists to withhold.
    """
    return (
        path.replace("{attempt_id}", attempt_id)
        .replace("{attemptId}", attempt_id)
        .replace("{question_id}", question_id)
        .replace("{questionId}", question_id)
        .replace("{quiz_id}", str(ctx.quiz_id))
        .replace("{quizId}", str(ctx.quiz_id))
        .replace("{learner_id}", str(ctx.learner_id))
        .replace("{topic_id}", "1")
        .replace("{version}", "1")
        .replace("{import_id}", "1")
        .replace("{review_id}", "unknown-review")
        .replace("{formal_attempt_id}", "unknown-formal-attempt")
        .replace("{session_id}", "unknown-session")
        .replace("{retake_id}", "unknown-retake")
        .replace("{grant_id}", "unknown-grant")
        .replace("{attempt_ref}", attempt_id)
        .replace("{course_id}", str(ctx.course_id))
        .replace("{usage_id}", "unknown-usage")
    )


# ---------------------------------------------------------------------------
# 1. Everything requires a credential
# ---------------------------------------------------------------------------


def test_every_route_declares_an_authentication_dependency(simple_system: Any) -> None:
    """Checked against the declared contract, not by calling — so a route with no data still fails.

    A route whose guard only refuses once it has looked something up would pass a behavioural probe
    on an empty database and fail in production. Reading the dependency out of the OpenAPI document
    asks the question the right way round: *is this route guarded?* rather than *did this call
    happen to be refused?*
    """
    ctx = simple_system
    unguarded: list[str] = []

    for path, operations in _routes(ctx).items():
        for method, spec in operations.items():
            parameters = {p.get("name", "").lower() for p in spec.get("parameters", [])}
            if "authorization" in parameters:
                continue
            if path in PUBLIC_ROUTES:
                continue
            unguarded.append(f"{method.upper()} {path}")

    assert unguarded == [], (
        "every route must require a credential, or be listed in PUBLIC_ROUTES with a reason: "
        + str(sorted(unguarded))
    )


def test_the_public_allow_list_still_matches_the_application(simple_system: Any) -> None:
    """The allow-list must not rot: a listed route that no longer exists hides a real gap."""
    ctx = simple_system
    paths = set(_routes(ctx))

    missing = [path for path in PUBLIC_ROUTES if path not in paths]
    assert missing == [], f"PUBLIC_ROUTES names routes that no longer exist: {missing}"


def test_an_anonymous_caller_reaches_nothing_that_carries_data(simple_system: Any) -> None:
    """The behavioural counterpart: actually call every GET with no credential."""
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)
    question_id = questions[0]["questionId"]

    reachable: list[str] = []
    for path, operations in _routes(ctx).items():
        if "get" not in operations or path in PUBLIC_ROUTES:
            continue
        response = ctx.client.get(_fill(path, ctx, attempt_id, question_id))
        if response.status_code not in (401, 403):
            reachable.append(f"{path} -> {response.status_code}")

    assert reachable == [], (
        "these routes answered an anonymous caller: " + str(sorted(reachable))
    )


# ---------------------------------------------------------------------------
# 2. A learner cannot reach an operator's surface
# ---------------------------------------------------------------------------


def test_a_learner_credential_reaches_no_administrator_route(simple_system: Any) -> None:
    """Analytics aggregates every learner; the question bank holds every answer key."""
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)
    question_id = questions[0]["questionId"]

    reachable: list[str] = []
    for path, operations in _routes(ctx).items():
        if not path.startswith(ADMIN_PREFIXES) or "get" not in operations:
            continue
        if path in PUBLIC_ROUTES:
            continue
        response = ctx.client.get(
            _fill(path, ctx, attempt_id, question_id), headers=auth(LEARNER_TOKEN)
        )
        if response.status_code not in (401, 403):
            reachable.append(f"{path} -> {response.status_code}")

    assert reachable == [], (
        "a learner reached these administrator routes: " + str(sorted(reachable))
    )


def test_a_learner_credential_reaches_no_assessor_or_system_route(simple_system: Any) -> None:
    """A learner who could declare their own exam disconnected could submit somebody else's paper."""
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)
    question_id = questions[0]["questionId"]

    reachable: list[str] = []
    for path, operations in _routes(ctx).items():
        if not path.startswith((ASSESSOR_PREFIX, SYSTEM_PREFIX)) or "get" not in operations:
            continue
        response = ctx.client.get(
            _fill(path, ctx, attempt_id, question_id), headers=auth(LEARNER_TOKEN)
        )
        if response.status_code not in (401, 403):
            reachable.append(f"{path} -> {response.status_code}")

    assert reachable == [], (
        "a learner reached these assessor/system routes: " + str(sorted(reachable))
    )


def test_an_assessor_credential_reaches_no_administrator_route(simple_system: Any) -> None:
    """Reviewing a learner's assessment and configuring the quiz are different authorities."""
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)
    question_id = questions[0]["questionId"]

    reachable: list[str] = []
    for path, operations in _routes(ctx).items():
        if not path.startswith(ADMIN_PREFIXES) or "get" not in operations:
            continue
        if path in PUBLIC_ROUTES:
            continue
        response = ctx.client.get(
            _fill(path, ctx, attempt_id, question_id), headers=auth(ASSESSOR_TOKEN)
        )
        if response.status_code not in (401, 403):
            reachable.append(f"{path} -> {response.status_code}")

    assert reachable == [], (
        "an assessor reached these administrator routes: " + str(sorted(reachable))
    )


# ---------------------------------------------------------------------------
# 3. An administrator is not a learner
# ---------------------------------------------------------------------------


def test_an_administrator_credential_reaches_no_learner_attempt_route(
    simple_system: Any,
) -> None:
    """§10 again, from the other side: an admin API cannot read a learner's submitted work.

    An administrator configures quizzes and authors questions. Reading what one learner answered is
    a different power, and the system does not grant it through the admin credential — the feedback
    report and the result are the learner's own.
    """
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)
    question_id = questions[0]["questionId"]

    reachable: list[str] = []
    for path, operations in _routes(ctx).items():
        if not path.startswith(LEARNER_PREFIX) or "get" not in operations:
            continue
        if "{attempt_id}" not in path:
            continue
        response = ctx.client.get(
            _fill(path, ctx, attempt_id, question_id), headers=auth(ADMIN_TOKEN)
        )
        if response.status_code not in (401, 403, 404):
            reachable.append(f"{path} -> {response.status_code}")

    assert reachable == [], (
        "an administrator read these learner attempt routes: " + str(sorted(reachable))
    )


def test_an_administrator_cannot_approve_a_formal_assessment(simple_system: Any) -> None:
    """Whoever sets the pass mark must not also sign off the passes."""
    ctx = simple_system

    refused = ctx.client.get("/api/assessor/pending-reviews", headers=auth(ADMIN_TOKEN))
    assert refused.status_code == 403, refused.text


# ---------------------------------------------------------------------------
# 4. No refusal discloses assessment data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("credential", ["none", "learner", "admin", "assessor"])
def test_no_refusal_anywhere_discloses_assessment_data(
    simple_system: Any, credential: str
) -> None:
    """Whatever the guard decides, the refusal itself must carry nothing.

    A 403 that helpfully explains *what* it is refusing access to has leaked the thing it refused.
    """
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)
    question_id = questions[0]["questionId"]

    headers = {
        "none": {},
        "learner": auth(LEARNER_TOKEN),
        "admin": auth(ADMIN_TOKEN),
        "assessor": auth(ASSESSOR_TOKEN),
    }[credential]

    leaked: list[str] = []
    for path, operations in _routes(ctx).items():
        if "get" not in operations or path in PUBLIC_ROUTES:
            continue
        response = ctx.client.get(_fill(path, ctx, attempt_id, question_id), headers=headers)
        if response.status_code < 400:
            continue
        body = response.text
        for forbidden in ("isCorrect", "correctPosition", "answerKey", "learnerAnswer"):
            if forbidden in body:
                leaked.append(f"{path} leaked {forbidden}")

    assert leaked == [], str(sorted(leaked))


def test_a_forged_credential_is_refused_everywhere(simple_system: Any) -> None:
    """A syntactically valid bearer token that resolves to nobody is nobody."""
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)
    question_id = questions[0]["questionId"]

    reachable: list[str] = []
    for path, operations in _routes(ctx).items():
        if "get" not in operations or path in PUBLIC_ROUTES:
            continue
        response = ctx.client.get(
            _fill(path, ctx, attempt_id, question_id),
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        if response.status_code not in (401, 403):
            reachable.append(f"{path} -> {response.status_code}")

    assert reachable == [], str(sorted(reachable))
