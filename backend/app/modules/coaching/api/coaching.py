"""Coaching endpoints (§31).

Nine operations, matching §31's list and nothing beyond it. Mounted under UC-03's ``/api/v1``
prefix, continuing the same learner conversation about one attempt that ``/result``, ``/outcome``
and ``/feedback`` are part of::

    GET  /v1/attempts/{id}/coaching/eligibility                  can I be coached, and on what?
    GET  /v1/attempts/{id}/coaching/review                       every incorrect question
    POST /v1/attempts/{id}/coaching/review/next                  move to the next one
    POST /v1/attempts/{id}/coaching/questions/{qid}              start (idempotent)
    GET  /v1/coaching/sessions/{sid}                             session state + conversation
    POST /v1/coaching/sessions/{sid}/messages                    send a learner message
    POST /v1/coaching/sessions/{sid}/mode                        Socratic / direct explanation
    POST /v1/coaching/sessions/{sid}/retry                       retry after a failure
    POST /v1/coaching/sessions/{sid}/complete                    finish with this question

**The learner is not in the path.** UC-07 shipped with ``/learners/{id}/…`` because it had no
identity layer to consult; here the learner comes from the bearer token through the one
authentication seam, exactly as UC-04's, UC-05's and UC-06's learner-scoped endpoints do. The
ownership rule is unchanged and still enforced in the domain: every service call re-checks that the
attempt and the session belong to the resolved learner (§9), because "a token resolved" and "this
attempt is theirs" are different claims.

**An AI outage is a 503 with a full body.** When the coach cannot speak, these endpoints return the
session state, the stored conversation and ``coachingAvailable: false`` with a reason code, under a
503. The learner keeps their session, the client knows it may retry, and nothing was invented to
fill the gap (§27, §28). That is why the coaching endpoints set the status code themselves instead
of raising.

**No endpoint returns a correct answer, a mark or a pass/fail verdict.** UC-07 explains nothing
about scoring and changes nothing about it (§4, §36) — the learner reads those on their feedback
report.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from app.modules.coaching.api.dependencies import CoachingCtx, CoachingLearner
from app.modules.coaching.domain.enums import ExchangeOutcome, SessionOutcome
from app.modules.coaching.schemas.requests import (
    NextQuestionRequest,
    SelectModeRequest,
    SendMessageRequest,
)
from app.modules.coaching.schemas.responses import (
    EligibilityModel,
    ExchangeModel,
    ReviewAdvanceModel,
    ReviewQueueModel,
    SessionStateModel,
    StartCoachingModel,
)
from app.modules.coaching.services.coaching_service import CoachingExchange, CoachingStart

router = APIRouter(tags=["Quiz Result — AI Coaching Review Mode (UC-07)"])


def _mark_unavailable(response: Response) -> None:
    """503 for a coaching turn that could not be produced — with the body intact (§27)."""
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Eligibility and the review queue
# ---------------------------------------------------------------------------


@router.get(
    "/attempts/{attempt_id}/coaching/eligibility",
    response_model=EligibilityModel,
    summary="Whether AI coaching is available for this attempt",
    description=(
        "Reports `coachingAvailable` for the attempt and for every question on it, so a client can "
        "decide whether to offer the coaching action.\n\n"
        "**Never fails for an ineligible attempt.** An attempt still in progress, an unreleased "
        "feedback report and a question that was answered correctly are all reported as reasons, "
        "not as errors — this is the call a result screen makes before it renders anything, and it "
        "should get an answer either way.\n\n"
        "Coaching is available only for a **submitted** attempt with a **confirmed score** and a "
        "**released feedback report**, and only for questions the authoritative scoring result "
        "marks incorrect. `reason` names whichever of those is outstanding."
    ),
)
async def check_eligibility(
    attempt_id: str,
    learner_id: CoachingLearner,
    ctx: CoachingCtx,
    question_id: str | None = Query(
        default=None,
        alias="questionId",
        description="Narrow the check to one question. Omit for the whole attempt.",
    ),
) -> EligibilityModel:
    eligibility = await ctx.services.review.check_eligibility(
        learner_id=learner_id, attempt_id=attempt_id, question_id=question_id
    )
    return EligibilityModel.model_validate(eligibility.as_dict())


@router.get(
    "/attempts/{attempt_id}/coaching/review",
    response_model=ReviewQueueModel,
    summary="Every incorrectly answered question, for review",
    description=(
        "The review-all-wrong-answers queue, in delivery order (§19). Only questions the "
        "authoritative scoring result marks incorrect appear; correct and unanswered questions "
        "never enter it (§20).\n\n"
        "Progress is **derived** from the coaching sessions that exist rather than stored, so this "
        "read is consistent however a client abandoned or resumed a previous review.\n\n"
        "Does not require the AI service to be up: a learner can always see which questions they "
        "got wrong, even during an outage."
    ),
)
async def get_review(
    attempt_id: str, learner_id: CoachingLearner, ctx: CoachingCtx
) -> ReviewQueueModel:
    queue = await ctx.services.review.get_review(learner_id=learner_id, attempt_id=attempt_id)
    return ReviewQueueModel.model_validate(queue.as_dict())


@router.post(
    "/attempts/{attempt_id}/coaching/review/next",
    response_model=ReviewAdvanceModel,
    summary="Move to the next incorrectly answered question",
    description=(
        "Finishes with the question currently being coached and returns the next one (§19).\n\n"
        "**Idempotent.** Once every question has been reviewed it keeps returning the finished "
        "queue with no next question, rather than wrapping around. Send "
        "`{\"completeCurrent\": false}` to look ahead without leaving the current question."
    ),
)
async def next_question(
    attempt_id: str,
    learner_id: CoachingLearner,
    ctx: CoachingCtx,
    body: NextQuestionRequest | None = None,
) -> ReviewAdvanceModel:
    request = body or NextQuestionRequest()
    advance = await ctx.services.review.next_question(
        learner_id=learner_id,
        attempt_id=attempt_id,
        complete_current=request.complete_current,
    )
    return ReviewAdvanceModel.model_validate(advance.as_dict())


# ---------------------------------------------------------------------------
# The coaching conversation
# ---------------------------------------------------------------------------


@router.post(
    "/attempts/{attempt_id}/coaching/questions/{question_id}",
    response_model=StartCoachingModel,
    status_code=status.HTTP_200_OK,
    summary="Start coaching for an incorrectly answered question",
    description=(
        "Opens a Socratic coaching session — *Review with Larry* — and returns the coach's opening "
        "question.\n\n"
        "**Idempotent.** Repeating the call resumes the same session (`outcome: RESUMED`) and "
        "never opens a second conversation or a second opening question; a unique constraint on "
        "`(learner, attempt, question)` decides it even under a race (§30).\n\n"
        "Refused with 409 while the attempt is unsubmitted or the feedback report is unreleased "
        "(§7, §8), with 403 when the attempt belongs to another learner, and with 409 when the "
        "question was not answered incorrectly (§9). Returns 503 with the session intact when the "
        "AI service could not be reached (§27).\n\n"
        "`sanitization` reports what the answer-key boundary removed on the way in: field names "
        "and counts, never values (§13)."
    ),
)
async def start_coaching(
    attempt_id: str,
    question_id: str,
    learner_id: CoachingLearner,
    ctx: CoachingCtx,
    response: Response,
) -> StartCoachingModel:
    started: CoachingStart = await ctx.services.coaching.start_coaching(
        learner_id=learner_id, attempt_id=attempt_id, question_id=question_id
    )
    if started.outcome is SessionOutcome.UNAVAILABLE:
        _mark_unavailable(response)
    return StartCoachingModel.model_validate(started.as_dict())


@router.get(
    "/coaching/sessions/{session_id}",
    response_model=SessionStateModel,
    summary="A coaching session and its conversation",
    description=(
        "The session's mode, status, exchange count and `directExplanationAvailable` flag, plus "
        "the stored conversation (§17, §18).\n\n"
        "Readable during an AI outage: a learner can always see what has already been said to "
        "them. 404 when the session does not exist **or is not this learner's** — a guessed "
        "session id must not be distinguishable from a missing one (§9)."
    ),
)
async def get_session(
    session_id: str, learner_id: CoachingLearner, ctx: CoachingCtx
) -> SessionStateModel:
    state = await ctx.services.coaching.get_session(
        learner_id=learner_id, session_id=session_id
    )
    return SessionStateModel.model_validate(state.as_dict())


@router.post(
    "/coaching/sessions/{session_id}/messages",
    response_model=ExchangeModel,
    summary="Send a learner message and get the coach's reply",
    description=(
        "One exchange: the learner's message answered by one coach turn.\n\n"
        "The exchange count moves **only when both halves complete**, so an outage cannot push a "
        "learner towards the five-exchange transition (§15, §28). On failure the learner's message "
        "is kept and 503 is returned with the session intact; `POST …/retry` re-sends it."
    ),
)
async def send_message(
    session_id: str,
    body: SendMessageRequest,
    learner_id: CoachingLearner,
    ctx: CoachingCtx,
    response: Response,
) -> ExchangeModel:
    exchange: CoachingExchange = await ctx.services.coaching.send_message(
        learner_id=learner_id, session_id=session_id, text=body.message
    )
    if exchange.outcome is ExchangeOutcome.UNAVAILABLE:
        _mark_unavailable(response)
    return ExchangeModel.model_validate(exchange.as_dict())


@router.post(
    "/coaching/sessions/{session_id}/mode",
    response_model=ExchangeModel,
    summary="Choose Socratic coaching or a direct concept explanation",
    description=(
        "After the configured number of exchanges the learner may choose to have the concept "
        "explained directly instead of continuing to be questioned (§15, §16). Choosing "
        "`DIRECT_EXPLANATION` produces the explanation immediately; that turn is **not** counted "
        "as an exchange.\n\n"
        "Refused with 409 before the threshold — the transition is what stops direct explanation "
        "from becoming an answer button. Switching back to `SOCRATIC` is always allowed and "
        "produces no turn.\n\n"
        "The explanation teaches the concept: the coach has never been given the answer key, so it "
        "has nothing else to explain (§12, §16)."
    ),
)
async def select_mode(
    session_id: str,
    body: SelectModeRequest,
    learner_id: CoachingLearner,
    ctx: CoachingCtx,
    response: Response,
) -> ExchangeModel:
    exchange = await ctx.services.coaching.select_mode(
        learner_id=learner_id, session_id=session_id, mode=body.mode
    )
    if exchange.outcome is ExchangeOutcome.UNAVAILABLE:
        _mark_unavailable(response)
    return ExchangeModel.model_validate(exchange.as_dict())


@router.post(
    "/coaching/sessions/{session_id}/retry",
    response_model=ExchangeModel,
    summary="Retry a coach turn that could not be produced",
    description=(
        "Recovers a session after an AI failure without creating a duplicate session or a "
        "duplicate exchange (§28). If the learner spoke last, their message is answered; if the "
        "coach spoke last, the stored reply is returned and a failed session is reactivated — so "
        "retrying a healthy session is a no-op rather than an extra model call."
    ),
)
async def retry_coaching(
    session_id: str, learner_id: CoachingLearner, ctx: CoachingCtx, response: Response
) -> ExchangeModel:
    exchange = await ctx.services.coaching.retry(learner_id=learner_id, session_id=session_id)
    if exchange.outcome is ExchangeOutcome.UNAVAILABLE:
        _mark_unavailable(response)
    return ExchangeModel.model_validate(exchange.as_dict())


@router.post(
    "/coaching/sessions/{session_id}/complete",
    response_model=SessionStateModel,
    summary="Finish coaching for this question",
    description=(
        "Marks the session COMPLETED, which advances the review queue past this question (§19). "
        "Idempotent — completing twice is not an error."
    ),
)
async def complete_session(
    session_id: str, learner_id: CoachingLearner, ctx: CoachingCtx
) -> SessionStateModel:
    state = await ctx.services.coaching.complete_session(
        learner_id=learner_id, session_id=session_id
    )
    return SessionStateModel.model_validate(state.as_dict())
