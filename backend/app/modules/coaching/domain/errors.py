"""UC-07's own failures (§29).

These extend the shared taxonomy in ``app.core.errors``, exactly as UC-03's and UC-04's do: the
kernel owns the envelope, the HTTP status vocabulary and ``retryable``; a capability owns the
*codes* for its own domain failures. Nothing here defines a rival exception base, so every one of
them serialises through the one handler into the one envelope.

The 404s and 409s reuse the kernel's ``NotFoundError`` and ``ConflictError`` with a code of their
own. The rest adapt ``AppError`` directly, because the kernel has no 403-with-a-code, 502, 503 or
504 class and should not grow four for one capability's AI provider.

Read the list as the answer to "what can UC-07 legitimately refuse to do?":

* the request is not this learner's to make — ``LearnerNotAuthorized``;
* the world is not ready — ``AttemptNotSubmitted``, ``ScoreNotConfirmed``, ``FeedbackUnavailable``;
* there is nothing to coach — ``QuestionNotInAttempt``, ``QuestionNotIncorrect``;
* the session cannot do what was asked — ``CoachingSessionNotFound``,
  ``CoachingSessionStateConflict``, ``DirectExplanationNotAvailable``, ``ExchangeLimitReached``;
* the AI could not answer — ``CoachingServiceUnavailable``, ``CoachingTimeout``,
  ``InvalidCoachingResponse``, ``CoachingPolicyViolation``;
* something tried to put an answer key in front of the model — ``AnswerKeyContamination``.

Three properties hold across all of them.

**Nothing here can touch the learner's result.** Every upstream port is read-only. A coaching
failure of any kind leaves the score, the pass/fail outcome and the feedback report exactly as they
were (§27).

**Failures are typed by what the caller should do.** ``retryable`` is set from that, not from
sentiment: an unreachable model is retryable, a question that was answered correctly never will be.

**Contamination fails closed.** ``AnswerKeyContaminationError`` is not retryable and carries no
detail of what leaked — reporting the leaked value in the error would be the leak (§13, §25).
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError

# ---------------------------------------------------------------------------
# The three boundary classes the kernel does not provide
# ---------------------------------------------------------------------------


class UpstreamProviderUnavailableError(AppError):
    """503 — UC-03, UC-04 or UC-06 could not be read (§27).

    Always retryable: nothing has been decided, so repeating the request is safe. Raised by a
    provider adapter rather than returned as an empty result, because "we could not confirm this
    attempt was submitted" must never degrade into "coaching allowed".
    """

    status_code = 503
    code = "COACHING_UPSTREAM_UNAVAILABLE"
    retryable = True

    def __init__(
        self,
        upstream: str,
        *,
        attempt_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            "Coaching could not be prepared because a quiz record could not be read. Your quiz "
            "result and feedback are unaffected. Please try again shortly.",
            context={"attemptId": attempt_id} if attempt_id else {},
            log_context={"upstream": upstream, "cause": str(cause) if cause else None},
        )


# ---------------------------------------------------------------------------
# Authorisation and readiness (§7, §8, §9)
# ---------------------------------------------------------------------------


class AttemptNotFoundError(NotFoundError):
    def __init__(self, attempt_id: str) -> None:
        super().__init__(
            "Attempt", attempt_id, code="ATTEMPT_NOT_FOUND", context={"attemptId": attempt_id}
        )


class LearnerNotAuthorizedError(AppError):
    """403 — the attempt exists but belongs to a different learner (§9).

    The message deliberately says nothing about the attempt: whether it was submitted, how it
    scored, or which questions were wrong. A learner probing someone else's attempt id must learn
    only that it is not theirs.
    """

    status_code = 403
    code = "LEARNER_NOT_AUTHORIZED"

    def __init__(self, attempt_id: str, learner_id: str) -> None:
        super().__init__(
            "This attempt does not belong to the requesting learner.",
            context={"attemptId": attempt_id},
            log_context={"attempt_id": attempt_id, "learner_id": learner_id},
        )


class CoachingNotAvailableError(ConflictError):
    """409 — the request is well formed but coaching is not available for it (§9, §27).

    One class for the whole gate with a specific ``code`` per cause, because the client's response
    is shaped by ``retryable`` rather than by the individual code. The ``context`` carries the
    observed upstream state so an operator can see *which* precondition is outstanding without
    reading logs.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, context=context)
        self.retryable = retryable


class AttemptNotSubmittedError(CoachingNotAvailableError):
    """The active-quiz protection, enforced in the domain rather than by hiding a button (§8)."""

    def __init__(self, attempt_id: str, status: str) -> None:
        super().__init__(
            code="ATTEMPT_NOT_SUBMITTED",
            message=(
                "Coaching is only available after the quiz has been submitted. This attempt is "
                "not submitted, so no coaching can be started for it."
            ),
            retryable=True,
            context={"attemptId": attempt_id, "attemptStatus": status},
        )


class FormalAssessmentInProgressError(ForbiddenError):
    """Coaching is refused because the learner is sitting a formal assessment (UC-09 §7).

    403 rather than 409: this is not a conflict with the *attempt's* state — the attempt may be
    long submitted and perfectly coachable — it is a refusal of the caller, right now, because of
    something else they are doing. It is retryable in the sense that finishing the exam clears it,
    which the eligibility payload says explicitly.
    """

    code = "FORMAL_ASSESSMENT_IN_PROGRESS"

    def __init__(self, attempt_id: str, learner_id: str) -> None:
        super().__init__(
            "AI coaching is unavailable while you are sitting a formal assessment. It becomes "
            "available again once that assessment has been submitted.",
            code="FORMAL_ASSESSMENT_IN_PROGRESS",
            context={"attemptId": attempt_id, "learnerId": learner_id},
        )


class ScoreNotConfirmedError(CoachingNotAvailableError):
    def __init__(self, attempt_id: str, status: str | None) -> None:
        super().__init__(
            code="SCORE_NOT_CONFIRMED",
            message=(
                "Coaching requires a confirmed scoring result. Until scoring is confirmed there "
                "is no authoritative record of which questions were answered incorrectly."
            ),
            retryable=True,
            context={"attemptId": attempt_id, "scoreStatus": status},
        )


class FeedbackUnavailableError(CoachingNotAvailableError):
    """Feedback must be released before coaching begins (§7)."""

    def __init__(self, attempt_id: str, status: str | None) -> None:
        super().__init__(
            code="FEEDBACK_UNAVAILABLE",
            message=(
                "Coaching becomes available once the detailed feedback report has been released "
                "for this attempt. It has not been released yet."
            ),
            retryable=True,
            context={"attemptId": attempt_id, "feedbackStatus": status},
        )


class QuestionNotInAttemptError(NotFoundError):
    """404 — the question is not part of this learner's submitted attempt (§9, §20)."""

    def __init__(self, attempt_id: str, question_id: str) -> None:
        super().__init__(
            "Question in attempt", question_id, code="QUESTION_NOT_IN_ATTEMPT"
        )
        self.context = {"attemptId": attempt_id, "questionId": question_id}


class QuestionNotIncorrectError(CoachingNotAvailableError):
    """The question was not answered incorrectly, so there is nothing to coach (§9, §20).

    Not retryable: a confirmed outcome does not change. Coaching a correct answer would also be
    the easiest possible way to leak the key — a learner could open a session on every question
    and read the coach's reaction.
    """

    def __init__(self, attempt_id: str, question_id: str, outcome: str) -> None:
        super().__init__(
            code="QUESTION_NOT_INCORRECT",
            message=(
                "Coaching is offered only for questions that were answered incorrectly. This "
                "question was not."
            ),
            retryable=False,
            context={"attemptId": attempt_id, "questionId": question_id, "outcome": outcome},
        )


# ---------------------------------------------------------------------------
# Session lifecycle (§17, §28, §30)
# ---------------------------------------------------------------------------


class CoachingSessionNotFoundError(NotFoundError):
    def __init__(self, session_id: str) -> None:
        super().__init__("Coaching session", session_id, code="COACHING_SESSION_NOT_FOUND")


class DuplicateCoachingSessionError(ConflictError):
    """Raised by a repository when the natural key already holds a session.

    The natural key is ``(learner_id, attempt_id, question_id)``. Callers treat this as "a
    concurrent request opened it first" and read the winner rather than overwriting, which is what
    makes starting coaching idempotent under concurrency (§30).
    """

    def __init__(self, learner_id: str, attempt_id: str, question_id: str) -> None:
        super().__init__(
            "A coaching session already exists for this learner, attempt and question.",
            code="DUPLICATE_COACHING_SESSION",
            context={"attemptId": attempt_id, "questionId": question_id},
            # learner_id is carried for the log only.
        )
        self.log_context = {"learner_id": learner_id}


class CoachingSessionStateConflictError(ConflictError):
    """409 — the session is not in a state that allows what was asked (§29)."""

    def __init__(self, session_id: str, status: str, *, action: str) -> None:
        super().__init__(
            f"This coaching session cannot {action} while it is {status}.",
            code="COACHING_SESSION_STATE_CONFLICT",
            context={"sessionId": session_id, "status": status, "action": action},
        )


class DirectExplanationNotAvailableError(ConflictError):
    """409 — direct explanation was requested before the exchange threshold (§15, §16).

    The threshold is the whole point of the transition: switching to a direct explanation on the
    first turn would turn Socratic coaching into an answer-request button.
    """

    def __init__(self, session_id: str, exchange_count: int, threshold: int) -> None:
        super().__init__(
            (
                "A direct concept explanation becomes available after "
                f"{threshold} coaching exchanges. This session has completed {exchange_count}."
            ),
            code="DIRECT_EXPLANATION_NOT_AVAILABLE",
            context={
                "sessionId": session_id,
                "exchangeCount": exchange_count,
                "directExplanationThreshold": threshold,
            },
        )


class ExchangeLimitReachedError(ConflictError):
    """409 — the session has hit the hard exchange ceiling.

    A runaway guard, not a teaching rule: the learner is told to start a fresh session rather than
    having an unbounded conversation billed against one question.
    """

    def __init__(self, session_id: str, limit: int) -> None:
        super().__init__(
            (
                f"This coaching session has reached its limit of {limit} exchanges. Start a new "
                "session to continue reviewing this question."
            ),
            code="COACHING_EXCHANGE_LIMIT_REACHED",
            context={"sessionId": session_id, "limit": limit},
        )


class NoIncorrectQuestionsError(ConflictError):
    """409 — the learner has no incorrect questions to review (§19).

    A pleasant failure: it means they got everything right.
    """

    def __init__(self, attempt_id: str) -> None:
        super().__init__(
            "This attempt has no incorrectly answered questions to review.",
            code="NO_INCORRECT_QUESTIONS",
            context={"attemptId": attempt_id},
        )


# ---------------------------------------------------------------------------
# AI service (§23, §27, §28, §29)
# ---------------------------------------------------------------------------


class CoachingServiceUnavailableError(AppError):
    """503 — the AI coaching service could not be reached (§27).

    Retryable, and deliberately empty of provider detail: an AI provider's error body can echo the
    prompt it was sent, so forwarding it would open an error-path route around the sanitiser.
    """

    status_code = 503
    code = "COACHING_SERVICE_UNAVAILABLE"
    retryable = True

    def __init__(self, *, reason: str | None = None, session_id: str | None = None) -> None:
        super().__init__(
            "The AI coaching service is temporarily unavailable. Your quiz result and feedback "
            "are unaffected. Please try again shortly.",
            context={"sessionId": session_id} if session_id else {},
            log_context={"reason": reason} if reason else {},
        )


class CoachingTimeoutError(AppError):
    """504 — the model did not answer in time (§29).

    Kept distinct from ``CoachingServiceUnavailableError`` because the operational response differs:
    a timeout usually means the provider is alive and slow, and a caller may want to back off for
    longer before retrying.
    """

    status_code = 504
    code = "COACHING_TIMEOUT"
    retryable = True

    def __init__(
        self, *, session_id: str | None = None, timeout_seconds: float | None = None
    ) -> None:
        super().__init__(
            "The AI coach did not respond in time. Your quiz result and feedback are unaffected. "
            "Please try again.",
            context={"sessionId": session_id} if session_id else {},
            log_context={"timeout_seconds": timeout_seconds},
        )


class InvalidCoachingResponseError(AppError):
    """502 — the provider answered, but not with something usable (§29).

    Empty, non-textual or absurdly long output. Retryable, because the alternative — substituting
    a canned coaching message — is exactly the fake chatbot §6 forbids.
    """

    status_code = 502
    code = "INVALID_COACHING_RESPONSE"
    retryable = True

    def __init__(self, *, reason: str, session_id: str | None = None) -> None:
        super().__init__(
            "The AI coach returned a response that could not be used. Please try again.",
            context={"sessionId": session_id} if session_id else {},
            log_context={"reason": reason},
        )


class CoachingPolicyViolationError(AppError):
    """502 — the model would not follow the coaching policy (§14, §24).

    Raised when a Socratic reply announces an answer instead of asking a question, and still does
    so after the allowed regeneration. The reply is discarded rather than shown: a "coach" that
    hands over a guessed answer is worse than one that admits it is having a bad minute.
    """

    status_code = 502
    code = "COACHING_POLICY_VIOLATION"
    retryable = True

    def __init__(self, *, session_id: str | None = None, violations: tuple[str, ...] = ()) -> None:
        super().__init__(
            "The AI coach could not produce a reply that follows the coaching policy. Please try "
            "again.",
            context={"sessionId": session_id} if session_id else {},
            log_context={"violations": list(violations)},
        )


class AnswerKeyContaminationError(AppError):
    """500 — answer-key material was found in something about to reach the model (§13, §25, §26).

    **Fails closed and is not retryable.** Reaching this means the sanitiser's structural guarantee
    was violated by an upstream change, and the correct response is to refuse coaching for that
    question until someone looks at it — never to strip a bit more and carry on.

    The error carries *where* contamination was found and never *what* was found: putting the
    leaked value into an error message, a log line or an API response would be the leak (§22).
    """

    status_code = 500
    code = "ANSWER_KEY_CONTAMINATION"
    retryable = False

    def __init__(self, *, findings: tuple[str, ...] = (), question_id: str | None = None) -> None:
        super().__init__(
            "Coaching could not be prepared for this question because its coaching context failed "
            "a safety check.",
            context={"questionId": question_id} if question_id else {},
            log_context={"contamination_findings": list(findings)},
        )
        #: Field paths only — never values.
        self.findings = findings


#: Every error code UC-07 can return, as a set a test can assert the API against.
#:
#: The counterpart of ``app.core.errors.PLATFORM_ERROR_CODES``: a capability owns the codes for its
#: own domain failures, and the kernel owns the ones for failures beneath the domain. Nothing the
#: coaching API returns may fall outside the union of the two, and
#: ``tests/coaching/test_error_taxonomy.py`` proves it — without that, an unhandled failure quietly
#: introduces an undocumented code and no test notices.
COACHING_ERROR_CODES: frozenset[str] = frozenset(
    {
        UpstreamProviderUnavailableError.code,
        "ATTEMPT_NOT_FOUND",
        LearnerNotAuthorizedError.code,
        "ATTEMPT_NOT_SUBMITTED",
        "SCORE_NOT_CONFIRMED",
        "FEEDBACK_UNAVAILABLE",
        "QUESTION_NOT_IN_ATTEMPT",
        "QUESTION_NOT_INCORRECT",
        "COACHING_SESSION_NOT_FOUND",
        "DUPLICATE_COACHING_SESSION",
        "COACHING_SESSION_STATE_CONFLICT",
        "DIRECT_EXPLANATION_NOT_AVAILABLE",
        "COACHING_EXCHANGE_LIMIT_REACHED",
        "NO_INCORRECT_QUESTIONS",
        CoachingServiceUnavailableError.code,
        CoachingTimeoutError.code,
        InvalidCoachingResponseError.code,
        CoachingPolicyViolationError.code,
        AnswerKeyContaminationError.code,
    }
)
