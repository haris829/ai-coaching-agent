"""FastAPI application factory.

Ten capabilities, one API, one database, one error envelope:

* **UC-01 Quiz Configuration & Rules** — ``/api/admin/quizzes/…``
* **UC-02 Question Bank Management** — ``/api/question-bank/…``
* **UC-03 Quiz Attempt Delivery** — ``/api/v1/attempts/…``
* **UC-04 Answer Validation & Scoring** — ``/api/v1/attempts/{id}/result``
* **UC-05 Pass/Fail & Certificate Gating** — ``/api/v1/attempts/{id}/outcome``
* **UC-06 Detailed Feedback Report** — ``/api/v1/attempts/{id}/feedback``
* **UC-07 AI Coaching Review Mode** — ``/api/v1/attempts/{id}/coaching/…``
* **UC-08 Retake Management** — ``/api/v1/quizzes/{id}/retakes`` · ``/api/admin/retakes/…``
* **UC-09 Formal Assessment Mode** — ``/api/v1/formal-attempts/…`` · ``/api/assessor/…``
* **UC-10 Analytics & Reporting** — ``/api/admin/analytics/…``

Each is a separate module, and every dependency between them crosses a port with exactly one
adapter behind it:

    UC-01 ──QuestionBankPort──▶ UC-02
    UC-03 ──QuizConfigurationPort──▶ UC-01
    UC-03 ──QuestionBankPort──▶ UC-02
    UC-03 ──EnrolmentPort──▶ platform placeholder
    UC-03 ──SubmissionDispatchPort──▶ the results chain (UC-04 → UC-05 → UC-06)
    UC-04 ──AttemptSourcePort──▶ UC-03 · ──AnswerKeyPort──▶ UC-02
    UC-05 ──ScoreResultPort──▶ UC-04 · ──AttemptPolicyPort──▶ UC-03
    UC-05 ──CertificateServicePort / CpdSyncPort──▶ external systems (local adapters today)
    UC-06 ──ScoreDetailPort──▶ UC-04 · ──OutcomePort──▶ UC-05 · ──QuestionContentPort──▶ UC-02
    UC-07 ──AttemptProvider──▶ UC-03 · ──ScoringResultProvider──▶ UC-04
          ──FeedbackProvider──▶ UC-06 · ──CoachingLLM──▶ the AI provider (unbound by default)
    UC-08 ──ConfigurationProvider──▶ UC-01 · ──QuestionBankProvider──▶ UC-02
          ──AttemptProvider──▶ UC-03 (its one write) · ──history──▶ UC-04/05/06/07
    UC-09 ──FormalAssessmentPolicyProvider──▶ UC-01 · ──AttemptProvider──▶ UC-03
          ──Scoring/PassFail──▶ UC-04/UC-05 · ──CertificateWorkflow──▶ UC-05
    UC-07 ◀──FormalAssessmentPolicyPort── UC-09   (coaching is refused mid-exam)
    UC-05 ◀──CertificateGatePort────────── UC-09   (no certificate before approval)
    UC-10 ──AnalyticsRepository──▶ UC-02/03/04/05 (read-only; no mutating method exists)
          ──ReviewRepository──▶ its own qy_ tables (flags + append-only audit)

No module imports another's models directly, there is exactly one question bank, and exactly one
owner of attempts. ``tests/test_architecture.py`` enforces that rather than trusting it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import __version__
from app.composition import ResultsAppContext, ResultsPipeline
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import engine
from app.modules.analytics.api.router import analytics_router
from app.modules.analytics.container import AnalyticsAppContext
from app.modules.attempt_delivery.api.deps import attach_request_id
from app.modules.attempt_delivery.api.router import attempt_delivery_router
from app.modules.attempt_delivery.container import AppContext
from app.modules.attempt_delivery.integration.submission_dispatch.port import (
    NoopSubmissionDispatch,
)
from app.modules.certification.api.router import router as certification_router
from app.modules.coaching.api.router import coaching_router
from app.modules.coaching.container import CoachingAppContext
from app.modules.feedback.api.router import router as feedback_router
from app.modules.formal_assessment.api.router import (
    formal_assessment_router,
    formal_assessor_router,
    formal_system_router,
)
from app.modules.formal_assessment.container import FormalAssessmentAppContext
from app.modules.formal_assessment.integration.adapters import (
    FormalCertificateWorkflowAdapter,
)
from app.modules.formal_assessment.integration.uc03_adapter import (
    attempt_provider_factory as formal_attempt_provider_factory,
)
from app.modules.identity.api import router as identity_router
from app.modules.question_bank.api.router import question_bank_router
from app.modules.quiz_configuration.api.router import quiz_configuration_router
from app.modules.retakes.api.router import retake_admin_router, retakes_router
from app.modules.retakes.container import RetakeAppContext
from app.modules.retakes.integration.uc03_adapter import attempt_provider_factory
from app.modules.scoring.api.router import router as scoring_router
from app.web import mount_frontend, resolve_dist

logger = get_logger(__name__)

DESCRIPTION = """
**Courses Quiz Agent** — the configuration and question-bank foundation.

### UC-01 — Quiz Configuration & Rules
An administrator configures a quiz; every meaningful change creates a new **immutable
configuration version**; learners see a rules summary built from the active version; and pressing
**Start quiz** creates an attempt permanently locked to the version that was active at that
moment.

* `/admin/quizzes/{id}/configuration` — read, and save-as-new-version
* `/admin/quizzes/{id}/configuration/versions` — immutable history
* `/admin/quizzes/{id}/question-bank` — eligible question counts per type
* `/quizzes/{id}/rules` — learner rules summary (read-only)
* `/quizzes/{id}/attempts` — start quiz

### UC-02 — Question Bank Management
Create, edit, tag, retire and bulk-import questions across five question types, with
backend-authoritative validation and guaranteed historical preservation.

* `/question-bank/questions` — CRUD, retirement, snapshot history
* `/question-bank/topics` — topic tagging
* `/question-bank/imports` — CSV bulk import with row-level reporting
* `/question-bank/delivery` + `/question-bank/reporting` — the delivery seam

### UC-03 — Quiz Attempt Delivery
Create, run, autosave, time, flag and submit a learner's attempt. The active configuration version
is read **once**, at creation, and frozen onto the attempt; the questions are selected once and
snapshotted. Timing is server-authoritative and submission is idempotent.

* `/v1/quizzes/{quizId}/attempt-eligibility` — may this learner start?
* `/v1/attempts` — start an attempt · list · resume the active one
* `/v1/attempts/{id}/questions` — the frozen paper (no answer key)
* `/v1/attempts/{id}/answers` — autosave, batch-atomic and idempotent
* `/v1/attempts/{id}/timing` — remaining time from the server clock only
* `/v1/attempts/{id}/submission` — preview, confirm, retry

### How they fit together
A configuration is only saveable when the **active** question bank can satisfy it. Retired
questions do not count towards capacity and are never drawn for an attempt — enforced by the
bank's own deliverable query, so the capacity rule and the delivery rule cannot drift apart. An
attempt then runs entirely on its own frozen copies, so an administrator editing the configuration
or retiring a question cannot disturb a learner mid-attempt.
"""


def create_app(
    *,
    attempt_context: AppContext | None = None,
    results_context: ResultsAppContext | None = None,
    coaching_context: CoachingAppContext | None = None,
    retake_context: RetakeAppContext | None = None,
    formal_context: FormalAssessmentAppContext | None = None,
    analytics_context: AnalyticsAppContext | None = None,
) -> FastAPI:
    """Build the app.

    ``attempt_context`` is injectable so a test can supply a controlled clock, an isolated
    database, or a failing submission dispatcher without patching internals. ``results_context``
    does the same for the results chain (UC-04 → UC-05 → UC-06) — a failing certificate service, for
    instance — and ``coaching_context`` for UC-07, whose AI provider is the boundary a test most
    needs to control. ``retake_context`` does the same for UC-08, and
    ``formal_context`` for UC-09 — whose assessor directory and review queue are the boundaries a
    test most needs to control. ``analytics_context`` does the same for UC-10, whose slow and
    failing providers are what its own suite exists to exercise.
    """
    configure_logging()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Content-Disposition"],
        )

    # Correlation id: returned as `X-Request-Id` and echoed in every error body, so an operator
    # can tie a learner's report to the exact log entry without the response carrying a traceback.
    app.middleware("http")(attach_request_id)

    register_exception_handlers(app)

    # UC-03 owns attempts and needs process-wide dependencies (clock, ports). Built once here.
    context = attempt_context or AppContext()
    app.state.context = context

    # UC-04/05/06 share UC-03's session factory and clock, so the whole chain is on one database and
    # one clock. See app/composition.py.
    results = results_context or ResultsAppContext.from_attempt_context(context)
    app.state.results = results

    # UC-07 is given the same session factory and the same clock, for the same reason: it coaches
    # the attempt UC-03 recorded and reads the score and feedback UC-04 and UC-06 wrote, so it has
    # to be looking at the same database. It is wired *here* rather than reading them off UC-03's
    # context itself, because a capability that imported another's composition root would be a
    # cross-capability dependency outside its own integration/ package.
    #
    # Its AI provider is process-wide and built once inside the context. With none configured,
    # coaching honestly reports itself unavailable rather than inventing teaching text.
    app.state.coaching = coaching_context or CoachingAppContext(
        session_factory=context.session_factory, clock=context.clock
    )

    # UC-08 is given the same session factory and clock for the same reason, and additionally
    # UC-03's own context: the one write a retake makes goes *through* UC-03's attempt service
    # rather than around it, so it needs the fully wired service — the enrolment check, the
    # configuration lock, the frozen snapshot — and not merely a database handle. Wiring it here
    # rather than letting UC-08 reach for UC-03's composition root itself keeps the dependency
    # where ``tests/test_architecture.py`` can see it.
    app.state.retakes = retake_context or RetakeAppContext(
        session_factory=context.session_factory,
        attempts=attempt_provider_factory(context),
        clock=context.clock,
    )

    # UC-09 supervises a sitting UC-03 delivers and UC-05 certificates, so it is given both: a
    # per-session UC-03 attempt provider, and a certificate workflow that calls UC-05's own
    # service once an assessor approves. Both are passed in rather than resolved inside UC-09's
    # composition root, which is not allowed to know which capability satisfies one of its ports.
    # UC-10 needs only the session factory and the clock: it reads other capabilities' rows
    # through a projection that has no mutating method, and owns two tables of its own. Nothing
    # it does can change an attempt, so there is no capability it has to be handed.
    app.state.analytics = analytics_context or AnalyticsAppContext(
        session_factory=context.session_factory, clock=context.clock
    )

    app.state.formal_assessment = formal_context or FormalAssessmentAppContext(
        session_factory=context.session_factory,
        upstream=formal_attempt_provider_factory(context),
        certificates=lambda session: FormalCertificateWorkflowAdapter(
            session, results.build(session).certification
        ),
        clock=context.clock,
    )

    # Wire the result chain into UC-03's downstream hand-off, which is the seam its
    # SubmissionDispatchPort was written for. Only when the dispatcher is still the documented no-op
    # default: a caller that supplied its own dispatcher meant it, and silently wrapping it would
    # change the behaviour a test is asserting.
    if isinstance(context.ports.dispatcher, NoopSubmissionDispatch):
        context.ports = replace(context.ports, dispatcher=ResultsPipeline(results))

    @app.get(
        f"{settings.api_prefix}/health/live",
        tags=["Health"],
        summary="Liveness — process is up, no dependencies checked",
    )
    def liveness() -> dict[str, str]:
        """Deliberately touches nothing.

        A liveness probe that queries the database restarts healthy processes during a database
        blip, which makes an outage worse. Readiness is the probe that is allowed to fail on a
        dependency — that is ``/health`` below.
        """
        return {"status": "ok", "version": __version__}

    @app.get(f"{settings.api_prefix}/health", tags=["Health"], summary="Readiness + database check")
    def health(response: Response) -> dict[str, Any]:
        database_ok = True
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover - exercised only on a broken database
            database_ok = False
            logger.error("health.database_unreachable", exc_info=exc)
            # 503, not a 200 saying "degraded": a load balancer acts on the status code, and a
            # readiness probe that always answers 200 cannot take a broken instance out of rotation.
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if database_ok else "degraded",
            "version": __version__,
            "modules": [
                "UC-01 Quiz Configuration & Rules",
                "UC-02 Question Bank Management",
                "UC-03 Quiz Attempt Delivery",
                "UC-04 Answer Validation & Scoring",
                "UC-05 Pass/Fail & Certificate Gating",
                "UC-06 Detailed Feedback Report",
                "UC-07 AI Coaching Review Mode",
                "UC-08 Retake Management",
                "UC-09 Formal Assessment Mode",
                "UC-10 Analytics & Reporting",
            ],
            "database": "ok" if database_ok else "unreachable",
            "environment": settings.environment,
            # Whether an AI coach is bound. Reported so an operator can see at a glance why
            # coaching is refusing every request: an unbound provider means UC-07 says
            # "temporarily unavailable" rather than serving invented teaching.
            "coachingProvider": {
                "configured": bool(
                    getattr(app.state.coaching.llm, "configured", False)
                ),
                "name": settings.coaching_llm_provider or None,
            },
        }

    app.include_router(identity_router, prefix=settings.api_prefix)
    app.include_router(quiz_configuration_router, prefix=settings.api_prefix)
    app.include_router(question_bank_router, prefix=settings.api_prefix)
    # UC-03 keeps its own versioned prefix: it is the surface a learner client talks to, and
    # versioning the learner API independently of the admin API is worth preserving.
    app.include_router(attempt_delivery_router, prefix=f"{settings.api_prefix}/v1")
    # UC-04/05/06 continue the same learner conversation about one attempt, so they share UC-03's
    # versioned prefix: /v1/attempts/{id}/result, /outcome, /feedback.
    app.include_router(scoring_router, prefix=f"{settings.api_prefix}/v1")
    app.include_router(certification_router, prefix=f"{settings.api_prefix}/v1")
    app.include_router(feedback_router, prefix=f"{settings.api_prefix}/v1")
    # UC-07 continues the same conversation: /v1/attempts/{id}/coaching/… and /v1/coaching/…
    app.include_router(coaching_router, prefix=f"{settings.api_prefix}/v1")
    # UC-08's learner half joins the same versioned conversation — a retake is a new attempt —
    # while its administrator half sits with UC-01's and UC-02's admin surface.
    app.include_router(retakes_router, prefix=f"{settings.api_prefix}/v1")
    app.include_router(retake_admin_router, prefix=f"{settings.api_prefix}/admin/retakes")
    # UC-09's learner half joins the same versioned conversation; its assessor and system halves
    # get their own roots, because they carry different credentials.
    # UC-10 is an administrator capability end to end — every endpoint reads or reviews aggregate
    # data and none of it is learner-facing — so it joins the admin surface rather than the
    # versioned learner one. See ``analytics/api/router.py``.
    app.include_router(analytics_router, prefix=f"{settings.api_prefix}/admin")
    app.include_router(formal_assessment_router, prefix=f"{settings.api_prefix}/v1")
    app.include_router(formal_assessor_router, prefix=f"{settings.api_prefix}/assessor")
    app.include_router(
        formal_system_router, prefix=f"{settings.api_prefix}/system/formal-assessments"
    )

    # Mounted **after** every router. The SPA fallback is a catch-all on "/{path}", and FastAPI
    # matches in registration order, so registering it earlier would shadow every API route
    # declared after it — the kind of mistake that shows up as one endpoint mysteriously returning
    # HTML.
    dist = resolve_dist()
    if dist is not None:
        mount_frontend(app, dist)

    logger.info(
        "app.started",
        extra={
            "environment": settings.environment,
            "api_prefix": settings.api_prefix,
            "database": "sqlite" if settings.is_sqlite else "server",
            "frontend": "served" if dist is not None else "api-only",
            "demoIdentities": settings.demo_identities,
        },
    )
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    # Development entry point only; a deployment runs `python -m scripts.start`, which migrates
    # first and binds the platform's port. `--reload` here is the whole reason this branch exists.
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
