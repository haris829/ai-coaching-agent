"""Learner-facing formal assessment endpoints (§17).

::

    GET  …/quizzes/{q}/formal-conditions                      the conditions and the policy
    POST …/quizzes/{q}/conditions-acknowledgement acknowledge them (§1)
    POST …/quizzes/{q}/identity-confirmation      confirm identity (§2)
    POST …/quizzes/{q}/formal-attempts            start, locking one device (§3)
    GET  …/quizzes/{q}/formal-attempts/open       the open formal attempt, if any
    GET  …/formal-attempts/{f}                    status (§17)
    POST …/formal-attempts/{f}/session/heartbeat  the session is alive (§3)
    POST …/formal-attempts/{f}/autosave           autosave through UC-03 (§6)
    POST …/formal-attempts/{f}/submission         submit (§20)
    POST …/formal-attempts/{f}/disconnect                      the learner's client reports a
    disconnect
    POST …/formal-attempts/{f}/pause              always refused (§4)
    POST …/formal-attempts/{f}/resume             always refused (§4)
    GET  …/ai-coaching-eligibility                may Larry run? (§7)
    GET  …/formal-attempts/{f}/certificate-eligibility  the gate, read-only (§11)

Every route enforces, in this order: authentication (the bearer token, through the merged
application's one identity seam), ownership (the services re-read every record scoped to the
resolved learner), the formal state, the business rules, and — for anything
that touches a live attempt — the device session. The handlers are deliberately thin: resolve
identity, call one service, map the result. Every decision is made in the services, so a host
application that calls them directly gets the same rules.

``POST …/formal-attempts`` returns **201** for an attempt that was started and **200** for a retry
that found the one it had already started, which is how a client tells "started" from "your retry
found it" without either being
an error. ``POST …/submission`` returns **200** either way — matching UC-03, where only attempt
creation is a 201 — and carries ``replayed`` to distinguish a duplicate submit from the one that
committed the attempt.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response, status

from app.modules.formal_assessment.api.dependencies import (
    ContainerDep,
    FormalLearner,
    SessionTokenDep,
    describe_device,
)
from app.modules.formal_assessment.domain.identity import IdentitySubmission
from app.modules.formal_assessment.integration.uc03 import AnswerSubmission
from app.modules.formal_assessment.schemas.requests import (
    AcknowledgeConditionsRequest,
    ConfirmIdentityRequest,
    DisconnectNotificationRequest,
    FormalAutosaveRequest,
    StartFormalAttemptRequest,
)
from app.modules.formal_assessment.schemas.responses import (
    AcknowledgementResponse,
    AiCoachingEligibilityResponse,
    CertificateEligibilityResponse,
    FormalAttemptStartResponse,
    FormalAttemptStatusResponse,
    FormalAutosaveResponse,
    FormalConditionsResponse,
    FormalSubmissionResponse,
    IdentityConfirmationResponse,
    SessionHeartbeatResponse,
)

router = APIRouter(tags=["Formal assessment — learner"])

QuizPath = Annotated[str, Path(description="The quiz being sat formally.")]
FormalAttemptPath = Annotated[str, Path(description="The formal attempt.")]


@router.get(
    "/quizzes/{quiz_id}/formal-conditions",
    response_model=FormalConditionsResponse,
    summary="The formal assessment conditions a learner must acknowledge",
    description=(
        "The canonical conditions text, its version, and the policy that applies to this quiz. "
        "Defined in the backend so the wording a learner agreed to and the wording the system "
        "recorded are the same string. A client renders this; it does not maintain its own copy."
    ),
)
async def get_conditions(
    quiz_id: QuizPath,
    learner_id: FormalLearner,
    container: ContainerDep,
) -> FormalConditionsResponse:
    payload = await container.services.conditions.describe(quiz_id)
    return FormalConditionsResponse.model_validate(payload)


@router.post(
    "/quizzes/{quiz_id}/conditions-acknowledgement",
    response_model=AcknowledgementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Acknowledge the formal assessment conditions",
    description=(
        "Records the learner's acknowledgement and creates the formal attempt record in "
        "`CONDITIONS_ACKNOWLEDGED`. Every required condition code must be present — the backend "
        "derives `conditions_acknowledged` from the codes rather than trusting a boolean.\n\n"
        "Idempotent: acknowledging again updates the open record rather than creating a second "
        "one, and returns **200**."
    ),
)
async def acknowledge_conditions(
    quiz_id: QuizPath,
    learner_id: FormalLearner,
    container: ContainerDep,
    request: Request,
    response: Response,
    payload: AcknowledgeConditionsRequest,
) -> AcknowledgementResponse:
    outcome = await container.services.conditions.acknowledge(
        learner_id=learner_id,
        quiz_id=quiz_id,
        acknowledged_codes=payload.acknowledged_condition_codes,
        user_agent=request.headers.get("user-agent"),
    )
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return AcknowledgementResponse.model_validate(outcome.as_dict())


@router.post(
    "/quizzes/{quiz_id}/identity-confirmation",
    response_model=IdentityConfirmationResponse,
    summary="Confirm identity before a formal assessment",
    description=(
        "The entered name must match the learner's profile name exactly, case-sensitively, after "
        "whitespace normalisation. The learner's account email address must already be confirmed; "
        "supplying `email` additionally requires it to match.\n\n"
        "A rejected confirmation does not change the formal attempt's state — it is counted, "
        "audited, and surfaced to the assessor as an anomaly if the learner eventually passes."
    ),
)
async def confirm_identity(
    quiz_id: QuizPath,
    learner_id: FormalLearner,
    container: ContainerDep,
    payload: ConfirmIdentityRequest,
) -> IdentityConfirmationResponse:
    outcome = await container.services.identity.confirm(
        learner_id=learner_id,
        quiz_id=quiz_id,
        submission=IdentitySubmission(full_name=payload.full_name, email=payload.email),
    )
    return IdentityConfirmationResponse.model_validate(outcome.as_dict())


@router.post(
    "/quizzes/{quiz_id}/formal-attempts",
    response_model=FormalAttemptStartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start the formal assessment",
    description=(
        "Starts the attempt through UC-03 and locks it to this device session. The response "
        "carries the `session_token` **once**; present it in the `X-Formal-Session` header on "
        "every later operation against this attempt.\n\n"
        "Requires the conditions to have been acknowledged for the current version and the "
        "identity to have been confirmed. A second device is refused with `409 "
        "SECOND_DEVICE_REJECTED`. A retry that supplies the same `client_request_id` replays the "
        "session it already created and returns **200**."
    ),
)
async def start_formal_attempt(
    quiz_id: QuizPath,
    learner_id: FormalLearner,
    container: ContainerDep,
    request: Request,
    response: Response,
    payload: StartFormalAttemptRequest | None = None,
) -> FormalAttemptStartResponse:
    body = payload or StartFormalAttemptRequest()
    device = describe_device(
        request,
        fingerprint=body.device.fingerprint if body.device else None,
        platform=body.device.platform if body.device else None,
    )
    outcome = await container.services.attempts.start(
        learner_id=learner_id,
        quiz_id=quiz_id,
        device=device,
        client_request_id=body.client_request_id,
        retake_of_attempt_id=body.retake_of_attempt_id,
    )
    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
    return FormalAttemptStartResponse.model_validate(outcome.as_dict())


@router.get(
    "/quizzes/{quiz_id}/formal-attempts/open",
    response_model=FormalAttemptStatusResponse | None,
    summary="The learner's open formal attempt for this quiz, if any",
    description=(
        "How a client resumes the *pre-start* steps after a page reload: it tells the learner "
        "whether they have already acknowledged the conditions or confirmed their identity. It is "
        "not a way back into an attempt that has ended — a formal attempt cannot be resumed."
    ),
)
async def get_open_formal_attempt(
    quiz_id: QuizPath,
    learner_id: FormalLearner,
    container: ContainerDep,
) -> FormalAttemptStatusResponse | None:
    record = await container.services.attempts.find_open(learner_id, quiz_id)
    if record is None:
        return None
    status_payload = await container.services.attempts.status(learner_id, record.formal_attempt_id)
    return FormalAttemptStatusResponse.model_validate(status_payload.as_dict())


@router.get(
    "/formal-attempts/{formal_attempt_id}",
    response_model=FormalAttemptStatusResponse,
    summary="The status of a formal attempt",
    description=(
        "The authoritative status, including the latest autosaved state while the attempt is in "
        "progress and the result once it is resolved. `pause_allowed` and `resume_allowed` are "
        "always false: the backend states what is permitted rather than leaving a client to infer "
        "it.\n\n"
        "Reading the status of a submitted attempt also attempts result resolution, because "
        "scoring is asynchronous and this is the honest moment to look."
    ),
)
async def get_formal_attempt_status(
    formal_attempt_id: FormalAttemptPath,
    learner_id: FormalLearner,
    container: ContainerDep,
) -> FormalAttemptStatusResponse:
    payload = await container.services.attempts.status(learner_id, formal_attempt_id)
    return FormalAttemptStatusResponse.model_validate(payload.as_dict())


@router.post(
    "/formal-attempts/{formal_attempt_id}/session/heartbeat",
    response_model=SessionHeartbeatResponse,
    summary="Confirm the device session is still alive",
    description=(
        "Requires the `X-Formal-Session` token. Confirms this device still holds the authoritative "
        "session and records that it was seen, so the platform's session monitor does not declare "
        "a disconnect while the learner is still there."
    ),
)
async def session_heartbeat(
    formal_attempt_id: FormalAttemptPath,
    learner_id: FormalLearner,
    container: ContainerDep,
    session_token: SessionTokenDep,
) -> SessionHeartbeatResponse:
    session = await container.services.attempts.heartbeat(
        learner_id=learner_id,
        formal_attempt_id=formal_attempt_id,
        session_token=session_token,
    )
    return SessionHeartbeatResponse(
        session_id=session.session_id,
        formal_attempt_id=session.formal_attempt_id,
        state=session.state.value,
        last_seen_at=session.last_seen_at,
        heartbeat_timeout_seconds=container.services.sessions.heartbeat_timeout_seconds,
    )


@router.post(
    "/formal-attempts/{formal_attempt_id}/autosave",
    response_model=FormalAutosaveResponse,
    summary="Autosave answers during a formal attempt",
    description=(
        "Requires the `X-Formal-Session` token and an attempt that is in progress. The answers are "
        "handed to UC-03's existing autosave unread and all-or-nothing — UC-09 adds the formal "
        "checks, not a second autosave. This is the state a disconnect auto-submits."
    ),
)
async def autosave(
    formal_attempt_id: FormalAttemptPath,
    learner_id: FormalLearner,
    container: ContainerDep,
    session_token: SessionTokenDep,
    payload: FormalAutosaveRequest,
) -> FormalAutosaveResponse:
    answers = tuple(
        AnswerSubmission(question_id=item.question_id, response=item.response)
        for item in payload.answers
    )
    result = await container.services.attempts.autosave(
        learner_id=learner_id,
        formal_attempt_id=formal_attempt_id,
        session_token=session_token,
        answers=answers,
    )
    return FormalAutosaveResponse.model_validate(result.as_dict())


@router.post(
    "/formal-attempts/{formal_attempt_id}/submission",
    response_model=FormalSubmissionResponse,
    summary="Submit the formal assessment",
    description=(
        "Requires the `X-Formal-Session` token. Commits the attempt through UC-03 and closes the "
        "session.\n\n"
        "A duplicate submit is a replay, not an error: it returns **200** with `replayed: true` "
        "and the existing submission. A passing result becomes `PENDING_REVIEW` — it does **not** "
        "produce a certificate."
    ),
)
async def submit_formal_attempt(
    formal_attempt_id: FormalAttemptPath,
    learner_id: FormalLearner,
    container: ContainerDep,
    session_token: SessionTokenDep,
    response: Response,
) -> FormalSubmissionResponse:
    outcome = await container.services.attempts.submit(
        learner_id=learner_id,
        formal_attempt_id=formal_attempt_id,
        session_token=session_token,
    )
    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
    return FormalSubmissionResponse.model_validate(outcome.as_dict())


@router.post(
    "/formal-attempts/{formal_attempt_id}/disconnect",
    response_model=FormalSubmissionResponse,
    summary="Report that this device's formal session disconnected",
    description=(
        "The learner's own client reporting a disconnect — a page-unload beacon, for instance. "
        "Takes UC-03's latest valid autosaved state, submits it with reason "
        "`DISCONNECT_AUTO_SUBMIT`, ends the session and prevents any resume.\n\n"
        "Idempotent: repeated disconnect events produce one submission. The platform's session "
        "monitor uses the system endpoint instead."
    ),
)
async def report_disconnect(
    formal_attempt_id: FormalAttemptPath,
    learner_id: FormalLearner,
    container: ContainerDep,
    payload: DisconnectNotificationRequest | None = None,
) -> FormalSubmissionResponse:
    body = payload or DisconnectNotificationRequest()
    outcome = await container.services.attempts.handle_disconnect_for_learner(
        learner_id=learner_id,
        formal_attempt_id=formal_attempt_id,
        reported_by="LEARNER_CLIENT",
        last_seen_at=body.last_seen_at,
        reason=body.reason,
    )
    return FormalSubmissionResponse.model_validate(outcome.as_dict())


@router.post(
    "/formal-attempts/{formal_attempt_id}/pause",
    summary="Pause a formal assessment — always refused",
    status_code=status.HTTP_409_CONFLICT,
    description=(
        "Exists so the refusal is explicit and auditable. A formal assessment cannot be paused: "
        "there is no paused state in the lifecycle. Always returns `409 PAUSE_NOT_ALLOWED`, "
        "records the attempt as having had a pause requested, and emits `PAUSE_REJECTED`."
    ),
    responses={409: {"description": "Always. `PAUSE_NOT_ALLOWED`."}},
)
async def pause_formal_attempt(
    formal_attempt_id: FormalAttemptPath,
    learner_id: FormalLearner,
    container: ContainerDep,
) -> None:
    await container.services.attempts.reject_pause(learner_id, formal_attempt_id)


@router.post(
    "/formal-attempts/{formal_attempt_id}/resume",
    summary="Resume a formal assessment — always refused",
    status_code=status.HTTP_409_CONFLICT,
    description=(
        "Exists so the refusal is explicit and auditable. A formal assessment cannot be resumed — "
        "while it is running the learner simply continues in the session they hold, and once it "
        "has ended it is over. Always returns `409 RESUME_NOT_ALLOWED` and emits "
        "`RESUME_REJECTED`."
    ),
    responses={409: {"description": "Always. `RESUME_NOT_ALLOWED`."}},
)
async def resume_formal_attempt(
    formal_attempt_id: FormalAttemptPath,
    learner_id: FormalLearner,
    container: ContainerDep,
) -> None:
    await container.services.attempts.reject_resume(learner_id, formal_attempt_id)


@router.get(
    "/ai-coaching-eligibility",
    response_model=AiCoachingEligibilityResponse,
    summary="May AI coaching (Larry) run for this learner right now?",
    description=(
        "The server-side answer to the AI coaching restriction. Blocked while **any** formal "
        "assessment of this learner's is in progress, whichever attempt is asked about — a learner "
        "sitting a formal assessment on one quiz may not coach on another.\n\n"
        "This endpoint reports the decision; the coaching module enforces it by calling the same "
        "check. A direct call to a coaching API is refused with `403 AI_COACHING_FORBIDDEN` "
        "regardless of what any client renders."
    ),
)
async def ai_coaching_eligibility(
    learner_id: FormalLearner,
    container: ContainerDep,
    attempt_id: Annotated[
        str | None, Query(description="The attempt coaching was requested about, if any.")
    ] = None,
) -> AiCoachingEligibilityResponse:
    permission = await container.services.coaching.is_ai_coaching_allowed(
        learner_id=learner_id, attempt_id=attempt_id
    )
    return AiCoachingEligibilityResponse.model_validate(permission.as_dict())


@router.get(
    "/formal-attempts/{formal_attempt_id}/certificate-eligibility",
    response_model=CertificateEligibilityResponse,
    summary="Whether a certificate may be issued for this formal assessment",
    description=(
        "The learner's read-only view of the certificate gate: passing is not enough, and this "
        "says what it is waiting for. A blocked read here is not audited as an attempted bypass — "
        "a learner refreshing a page is not trying to get around anything."
    ),
)
async def certificate_eligibility(
    formal_attempt_id: FormalAttemptPath,
    learner_id: FormalLearner,
    container: ContainerDep,
) -> CertificateEligibilityResponse:
    eligibility = await container.services.certificates.check_eligibility_for_learner(
        learner_id=learner_id, formal_attempt_id=formal_attempt_id
    )
    return CertificateEligibilityResponse.model_validate(eligibility.as_dict())
