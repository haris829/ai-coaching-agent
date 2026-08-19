"""The coaching conversation (§14–§18, §27, §28, §30).

Everything a learner does inside one question's coaching session lives here: opening it, talking to
the coach, switching mode after the five-exchange transition, retrying after an outage, and
finishing.

FOUR RULES SHAPE EVERY METHOD BELOW
-----------------------------------
**1. The gate runs on every call** (§8). Not once at ``start`` — every time. See
``services.authorization``.

**2. The context is rebuilt, sanitised, on every call** (§13, §26). Nothing sanitised is cached and
no coaching context is stored, so there is no stale copy of a question that could outlive a change
upstream, and no representation of question material sitting in UC-07's storage.

**3. Nothing is ever invented** (§6, §27). When the model cannot be reached, times out, or answers
with something unusable, the method returns a controlled ``UNAVAILABLE`` outcome. It does not
return a canned coaching message, a cached reply or an apology dressed as teaching, and it does not
raise past the caller as a crash.

**4. A failed exchange is not an exchange** (§15, §28). ``exchange_count`` moves only when a
learner message has been answered by a coach reply. An outage cannot push a learner closer to the
direct-explanation threshold, and a retry cannot push them past it twice.

WHAT COUNTS AS AN EXCHANGE
--------------------------
One learner message answered by one coach reply. Two coach turns are deliberately *not* exchanges:

* the **opening question**, because the learner has not reasoned yet — counting it would spend one
  of the five for free;
* the **direct explanation** produced when the learner switches mode, because they asked to be
  told rather than to be asked.

IDEMPOTENCY (§30)
-----------------
``start_coaching`` is safe to call any number of times. The natural key
``(learner_id, attempt_id, question_id)`` is unique in the repository, a duplicate insert is caught
and resolved by reading the winner, and a session that already has a coach turn is *resumed* rather
than re-opened — so a double-tapped button cannot produce two conversations or two opening
questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.errors import AppError, BadRequestError
from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.coaching.domain import redaction
from app.modules.coaching.domain.enums import (
    CoachingMode,
    CoachingSessionStatus,
    ExchangeOutcome,
    MessageRole,
    SessionOutcome,
)
from app.modules.coaching.domain.errors import (
    CoachingPolicyViolationError,
    CoachingServiceUnavailableError,
    CoachingSessionNotFoundError,
    CoachingSessionStateConflictError,
    CoachingTimeoutError,
    DirectExplanationNotAvailableError,
    DuplicateCoachingSessionError,
    ExchangeLimitReachedError,
    InvalidCoachingResponseError,
)
from app.modules.coaching.domain.response_policy import evaluate_response
from app.modules.coaching.domain.sanitizer import SanitizedCoachingContext
from app.modules.coaching.domain.session import CoachingSession, new_session
from app.modules.coaching.domain.transcript import (
    ChatMessage,
    CoachingTranscript,
    build_messages,
    to_history,
)
from app.modules.coaching.ids import IdGenerator
from app.modules.coaching.integration.activity import (
    CoachingActivityEvent,
    CoachingActivityLog,
    CoachingActivityType,
)
from app.modules.coaching.integration.knowledge_gaps import (
    KnowledgeGapEvent,
    KnowledgeGapTracker,
)
from app.modules.coaching.integration.llm import CoachingLLM, CoachingRequest
from app.modules.coaching.prompts import (
    PROMPT_VERSION,
    build_system_prompt,
    policy_reminder,
    render_context,
)
from app.modules.coaching.repositories.protocols import (
    CoachingSessionRepository,
    CoachingTranscriptRepository,
)
from app.modules.coaching.services.authorization import CoachingAuthorizer, GateResult
from app.modules.coaching.services.context_builder import CoachingContextBuilder

logger = get_logger(__name__)

#: What separates the coaching policy, the rendered question context and any policy correction
#: inside one system prompt. A blank line, so each section stays legible to the model as a
#: distinct block rather than running into the one before it.
PROMPT_SEPARATOR = "\n\n"

#: Failures that mean "the coach could not answer this time". Each becomes a controlled
#: ``UNAVAILABLE`` outcome and a recorded failure on the session, never an exception past the
#: caller and never a substituted reply (§27).
#:
#: ``UpstreamProviderUnavailableError`` is deliberately absent. An unreadable UC-03/UC-04/UC-06
#: record is not the coach having a bad minute, and it is raised by the gate — which runs before any
#: coach turn — so catching it here would only mislabel a quiz-data outage as an AI outage on the
#: learner's session.
_AI_FAILURES: tuple[type[AppError], ...] = (
    CoachingServiceUnavailableError,
    CoachingTimeoutError,
    InvalidCoachingResponseError,
    CoachingPolicyViolationError,
)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoachingSessionState:
    """A session plus its conversation — what a client renders."""

    session: CoachingSession
    transcript: CoachingTranscript

    def as_dict(self, *, include_transcript: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session": self.session.as_dict(),
            "message_count": len(self.transcript.messages),
        }
        if include_transcript:
            payload["messages"] = [message.as_dict() for message in self.transcript.messages]
        return payload


@dataclass(frozen=True, slots=True)
class CoachingStart:
    """What one call to "start coaching" did (§30)."""

    outcome: SessionOutcome
    state: CoachingSessionState
    #: What the sanitiser removed on the way in. Names and counts only (§13, §22).
    sanitization: dict[str, Any] | None = None
    #: Set when the coach could not speak: the error code, never a provider message (§29).
    unavailable_reason: str | None = None

    @property
    def coaching_available(self) -> bool:
        return self.outcome is not SessionOutcome.UNAVAILABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "coaching_available": self.coaching_available,
            "reason": self.unavailable_reason,
            "sanitization": self.sanitization,
            **self.state.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class CoachingExchange:
    """What one coach turn did (§27, §28)."""

    outcome: ExchangeOutcome
    state: CoachingSessionState
    reply: ChatMessage | None = None
    unavailable_reason: str | None = None
    retryable: bool = False

    @property
    def coaching_available(self) -> bool:
        return self.outcome is ExchangeOutcome.COMPLETED

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "coaching_available": self.coaching_available,
            "reason": self.unavailable_reason,
            "retryable": self.retryable,
            "reply": self.reply.as_dict() if self.reply else None,
            **self.state.as_dict(),
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CoachingService:
    """Socratic coaching for one incorrectly answered question at a time."""

    def __init__(
        self,
        *,
        authorizer: CoachingAuthorizer,
        context_builder: CoachingContextBuilder,
        sessions: CoachingSessionRepository,
        transcripts: CoachingTranscriptRepository,
        llm: CoachingLLM,
        activity: CoachingActivityLog,
        knowledge_gaps: KnowledgeGapTracker,
        clock: Clock,
        new_id: IdGenerator,
        settings: Settings,
    ) -> None:
        self._authorizer = authorizer
        self._context = context_builder
        self._sessions = sessions
        self._transcripts = transcripts
        self._llm = llm
        self._activity = activity
        self._gaps = knowledge_gaps
        self._clock = clock
        self._new_id = new_id
        self._settings = settings

    # -- Starting -----------------------------------------------------------

    async def start_coaching(
        self, *, learner_id: str, attempt_id: str, question_id: str
    ) -> CoachingStart:
        """Open — or resume — coaching for one incorrectly answered question (§9, §30).

        The order of operations is the interesting part. The gate runs first, then the context is
        built and sanitised, and only then is a session record created. Building the context before
        writing anything means a question whose material fails the safety check never produces a
        session at all (§25) — there is nothing to clean up and nothing for a client to retry into.
        """
        gate = await self._authorizer.authorize(
            learner_id=learner_id, attempt_id=attempt_id, question_id=question_id
        )
        sanitized = await self._build_context(gate, question_id)
        now = to_iso(self._clock.now())

        session, created = await self._open_session(
            gate=gate,
            learner_id=learner_id,
            attempt_id=attempt_id,
            question_id=question_id,
            sanitized=sanitized,
            now=now,
        )

        transcript = await self._transcripts.get(session.session_id)
        if transcript.messages:
            # Already has a conversation: resume it. No second opening question, no second
            # session, no extra model call (§30).
            if session.status is not CoachingSessionStatus.ACTIVE:
                session = await self._sessions.update(session.activated(now))
            return CoachingStart(
                outcome=SessionOutcome.RESUMED,
                state=CoachingSessionState(session=session, transcript=transcript),
                sanitization=sanitized.report.as_dict(),
            )

        exchange = await self._coach_turn(session=session, sanitized=sanitized, counts=False)
        outcome = SessionOutcome.STARTED if created else SessionOutcome.RESUMED
        if exchange.outcome is ExchangeOutcome.UNAVAILABLE:
            outcome = SessionOutcome.UNAVAILABLE

        return CoachingStart(
            outcome=outcome,
            state=exchange.state,
            sanitization=sanitized.report.as_dict(),
            unavailable_reason=exchange.unavailable_reason,
        )

    async def _open_session(
        self,
        *,
        gate: GateResult,
        learner_id: str,
        attempt_id: str,
        question_id: str,
        sanitized: SanitizedCoachingContext,
        now: str,
    ) -> tuple[CoachingSession, bool]:
        """Find the session for this natural key, or create it. Returns ``(session, created)``."""
        existing = await self._sessions.find_open(learner_id, attempt_id, question_id)
        if existing is not None:
            return existing, False

        result = gate.eligibility.result
        candidate = new_session(
            session_id=self._new_id(),
            learner_id=learner_id,
            attempt_id=attempt_id,
            course_id=sanitized.context.course_id,
            question_id=question_id,
            now=now,
            topic=sanitized.context.topic,
            question_position=result.position if result else None,
            direct_explanation_threshold=self._settings.direct_explanation_threshold,
        )
        try:
            session = await self._sessions.insert(candidate)
        except DuplicateCoachingSessionError:
            # A concurrent request won. Read the winner rather than overwriting it — this is what
            # makes two simultaneous "start coaching" calls converge on one conversation (§30).
            winner = await self._sessions.find_open(learner_id, attempt_id, question_id)
            if winner is None:  # pragma: no cover - only reachable if the store lost the winner
                raise
            return winner, False

        # Recorded once, at creation. A resumed session records nothing further, so a learner who
        # spends twenty turns on one question contributes one knowledge gap, not twenty (§21).
        await self._record_gap(session, sanitized)
        await self._record_activity(CoachingActivityType.SESSION_STARTED, session)
        return session, True

    # -- Talking ------------------------------------------------------------

    async def send_message(
        self, *, learner_id: str, session_id: str, text: str
    ) -> CoachingExchange:
        """Send one learner message and get the coach's reply (§14).

        The learner's message is stored *before* the model is called. If the call then fails, the
        learner has not lost what they typed and ``retry`` re-sends exactly that message — no
        duplicate session, no duplicate exchange, and the count untouched (§28).
        """
        message_text = (text or "").strip()
        if not message_text:
            raise BadRequestError("A coaching message cannot be empty.")
        if len(message_text) > self._settings.coaching_max_message_chars:
            raise BadRequestError(
                "That message is too long for a coaching exchange. Please shorten it."
            )

        session = await self._require_session(learner_id, session_id)
        if session.status is not CoachingSessionStatus.ACTIVE:
            raise CoachingSessionStateConflictError(
                session_id, session.status.value, action="accept a message"
            )
        if session.exchange_count >= self._settings.coaching_max_exchanges:
            raise ExchangeLimitReachedError(session_id, self._settings.coaching_max_exchanges)

        gate = await self._authorizer.authorize(
            learner_id=learner_id,
            attempt_id=session.attempt_id,
            question_id=session.question_id,
        )
        sanitized = await self._build_context(gate, session.question_id)

        now = to_iso(self._clock.now())
        transcript = await self._transcripts.get(session_id)
        await self._transcripts.append(
            session_id,
            build_messages(
                transcript, role=MessageRole.LEARNER, content=message_text, created_at=now
            ),
        )
        return await self._coach_turn(session=session, sanitized=sanitized, counts=True)

    async def select_mode(
        self, *, learner_id: str, session_id: str, mode: CoachingMode
    ) -> CoachingExchange:
        """Choose between continuing Socratic coaching and a direct explanation (§15, §16).

        Switching *to* a direct explanation produces the explanation immediately — the learner has
        asked to be told, and making them type "go on" first would be theatre. That turn is not
        counted as an exchange: an exchange is the learner reasoning and the coach responding, and
        this is neither.

        Switching *back* to Socratic is always allowed and produces no turn; the next message is
        simply coached rather than explained.
        """
        session = await self._require_session(learner_id, session_id)
        if session.status is CoachingSessionStatus.COMPLETED:
            raise CoachingSessionStateConflictError(
                session_id, session.status.value, action="change mode"
            )
        if mode is CoachingMode.DIRECT_EXPLANATION and not session.direct_explanation_available:
            raise DirectExplanationNotAvailableError(
                session_id, session.exchange_count, session.direct_explanation_threshold
            )

        now = to_iso(self._clock.now())
        if mode is not session.mode:
            session = await self._sessions.update(session.with_mode(mode, now))
            await self._record_activity(CoachingActivityType.MODE_CHANGED, session)

        if mode is CoachingMode.SOCRATIC:
            transcript = await self._transcripts.get(session_id)
            return CoachingExchange(
                outcome=ExchangeOutcome.COMPLETED,
                state=CoachingSessionState(session=session, transcript=transcript),
            )

        gate = await self._authorizer.authorize(
            learner_id=learner_id,
            attempt_id=session.attempt_id,
            question_id=session.question_id,
        )
        sanitized = await self._build_context(gate, session.question_id)
        return await self._coach_turn(session=session, sanitized=sanitized, counts=False)

    async def retry(self, *, learner_id: str, session_id: str) -> CoachingExchange:
        """Retry a coach turn that could not be produced (§28).

        Three cases, and none of them creates a session or duplicates an exchange:

        * **nothing said yet** — the opening question failed; produce it now;
        * **the learner spoke last** — their message is still there; answer it;
        * **the coach spoke last** — there is nothing outstanding. The stored reply is returned and
          a session parked as FAILED is recovered to ACTIVE. Retrying a healthy session is a no-op
          rather than an extra model call.
        """
        session = await self._require_session(learner_id, session_id)
        if session.status is CoachingSessionStatus.COMPLETED:
            raise CoachingSessionStateConflictError(
                session_id, session.status.value, action="be retried"
            )

        gate = await self._authorizer.authorize(
            learner_id=learner_id,
            attempt_id=session.attempt_id,
            question_id=session.question_id,
        )
        sanitized = await self._build_context(gate, session.question_id)
        await self._record_activity(CoachingActivityType.SESSION_RETRIED, session)

        now = to_iso(self._clock.now())
        transcript = await self._transcripts.get(session_id)
        if not transcript.messages:
            return await self._coach_turn(session=session, sanitized=sanitized, counts=False)

        if transcript.messages[-1].role is MessageRole.LEARNER:
            return await self._coach_turn(session=session, sanitized=sanitized, counts=True)

        if session.status is not CoachingSessionStatus.ACTIVE:
            session = await self._sessions.update(session.activated(now))
        return CoachingExchange(
            outcome=ExchangeOutcome.COMPLETED,
            state=CoachingSessionState(session=session, transcript=transcript),
            reply=transcript.messages[-1],
        )

    # -- Reading and finishing ---------------------------------------------

    async def get_session(self, *, learner_id: str, session_id: str) -> CoachingSessionState:
        """The session and its conversation.

        Deliberately not gated on AI availability: a learner can always read what was already said
        to them, even during an outage (§27).
        """
        session = await self._require_session(learner_id, session_id)
        transcript = await self._transcripts.get(session_id)
        return CoachingSessionState(session=session, transcript=transcript)

    async def complete_session(
        self, *, learner_id: str, session_id: str
    ) -> CoachingSessionState:
        """Finish with this question. Idempotent — completing twice is not an error (§19)."""
        session = await self._require_session(learner_id, session_id)
        transcript = await self._transcripts.get(session_id)
        if session.status is CoachingSessionStatus.COMPLETED:
            return CoachingSessionState(session=session, transcript=transcript)

        session = await self._sessions.update(session.completed(to_iso(self._clock.now())))
        await self._record_activity(CoachingActivityType.SESSION_COMPLETED, session)
        return CoachingSessionState(session=session, transcript=transcript)

    # -- Internals ----------------------------------------------------------

    async def _require_session(self, learner_id: str, session_id: str) -> CoachingSession:
        """Ownership-scoped read. A session belonging to someone else is *not found* (§9).

        Not "forbidden": a learner probing session ids must not be able to tell the difference
        between one that does not exist and one that is not theirs.
        """
        session = await self._sessions.get_for_learner(learner_id, session_id)
        if session is None:
            raise CoachingSessionNotFoundError(session_id)
        return session

    async def _build_context(
        self, gate: GateResult, question_id: str
    ) -> SanitizedCoachingContext:
        result = gate.eligibility.result
        if gate.attempt is None or result is None:  # pragma: no cover - the gate guarantees both
            raise CoachingServiceUnavailableError(reason="INCOMPLETE_GATE_RESULT")
        return await self._context.build(
            attempt=gate.attempt, result=result, feedback=gate.feedback
        )

    async def _coach_turn(
        self, *, session: CoachingSession, sanitized: SanitizedCoachingContext, counts: bool
    ) -> CoachingExchange:
        """Produce one coach turn, store it, and move the session on.

        ``counts`` says whether this turn completes an exchange. On failure nothing is stored, the
        count does not move, and the caller gets a controlled unavailable state (§27, §28).
        """
        now = to_iso(self._clock.now())
        transcript = await self._transcripts.get(session.session_id)

        try:
            text = await self._ask_coach(
                session=session, sanitized=sanitized, transcript=transcript
            )
        except _AI_FAILURES as exc:
            return await self._record_turn_failure(session, transcript, exc, now)

        message = build_messages(
            transcript,
            role=MessageRole.COACH,
            content=text,
            created_at=now,
            mode=session.mode.value,
        )
        transcript = await self._transcripts.append(session.session_id, message)

        was_offered = session.direct_explanation_offered
        moved = session.with_exchange(now) if counts else session.activated(now)
        session = await self._sessions.update(moved)

        if counts:
            await self._record_activity(CoachingActivityType.EXCHANGE_COMPLETED, session)
            if session.direct_explanation_offered and not was_offered:
                # The five-exchange transition: the choice is now available to the learner (§15).
                await self._record_activity(
                    CoachingActivityType.DIRECT_EXPLANATION_OFFERED, session
                )

        return CoachingExchange(
            outcome=ExchangeOutcome.COMPLETED,
            state=CoachingSessionState(session=session, transcript=transcript),
            reply=message,
        )

    async def _record_turn_failure(
        self,
        session: CoachingSession,
        transcript: CoachingTranscript,
        exc: AppError,
        now: str,
    ) -> CoachingExchange:
        failed = session.with_failure(
            exc.code, now, limit=self._settings.coaching_max_consecutive_failures
        )
        session = await self._sessions.update(failed)
        await self._record_activity(
            CoachingActivityType.SESSION_FAILED, session, failure_code=exc.code
        )
        logger.warning(
            "coaching.turn_failed",
            extra={**redaction.session_context(session), "code": exc.code},
        )
        return CoachingExchange(
            outcome=ExchangeOutcome.UNAVAILABLE,
            state=CoachingSessionState(session=session, transcript=transcript),
            unavailable_reason=exc.code,
            retryable=exc.retryable,
        )

    async def _ask_coach(
        self,
        *,
        session: CoachingSession,
        sanitized: SanitizedCoachingContext,
        transcript: CoachingTranscript,
    ) -> str:
        """Call the model and check what came back (§14, §24, §29).

        A reply that breaks the coaching policy is discarded and regenerated with the specific
        failure named, up to ``coaching_policy_retries`` times. When those are exhausted the
        exchange fails: there is no canned reply to fall back to, by design (§6).
        """
        payload = sanitized.context.as_dict()
        base_prompt = build_system_prompt(
            mode=session.mode.value,
            exchange_count=session.exchange_count,
            direct_explanation_offered=session.direct_explanation_offered,
            question_type=payload.get("question_type"),
        )
        rendered = render_context(payload)
        conversation = to_history(
            transcript.window(self._settings.coaching_history_window)
        )

        reminder = ""
        verdict = None
        for _ in range(self._settings.coaching_policy_retries + 1):
            request = CoachingRequest(
                system_prompt=PROMPT_SEPARATOR.join(
                    part for part in (base_prompt, rendered, reminder) if part
                ),
                context=payload,
                conversation=conversation,
                mode=session.mode.value,
                turn=session.exchange_count + 1,
                timeout_seconds=self._settings.coaching_llm_timeout_seconds,
                max_output_chars=self._settings.coaching_llm_max_output_chars,
                session_id=session.session_id,
            )
            completion = await self._invoke(request)
            verdict = evaluate_response(
                getattr(completion, "text", None),
                mode=session.mode,
                max_chars=self._settings.coaching_llm_max_output_chars,
            )
            if verdict.usable:
                logger.info(
                    "coaching.turn",
                    extra={
                        **redaction.session_context(session),
                        **redaction.sanitization_context(sanitized.report),
                        "prompt_version": PROMPT_VERSION,
                        "model": completion.model,
                    },
                )
                return completion.text.strip()
            reminder = policy_reminder(verdict.violations) if verdict.violations else ""

        if verdict is not None and verdict.invalid_reason is not None:
            raise InvalidCoachingResponseError(
                reason=verdict.invalid_reason, session_id=session.session_id
            )
        raise CoachingPolicyViolationError(
            session_id=session.session_id,
            violations=verdict.violations if verdict else (),
        )

    async def _invoke(self, request: CoachingRequest):  # noqa: ANN201 - the port's type is the contract
        """Call the provider, normalising anything it throws into UC-07's taxonomy (§29).

        An adapter is asked to raise a typed error; one that raises a vendor exception, a
        ``TimeoutError`` or anything else still produces a controlled unavailable state rather than
        a 500 with a stack trace in the logs and a broken page for the learner.
        """
        try:
            return await self._llm.generate_response(request)
        except AppError:
            raise
        except TimeoutError as exc:
            raise CoachingTimeoutError(
                session_id=request.session_id,
                timeout_seconds=request.timeout_seconds,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - deliberately broad; see the docstring.
            raise CoachingServiceUnavailableError(
                reason=type(exc).__name__, session_id=request.session_id
            ) from exc

    # -- Outbound records, isolated ----------------------------------------

    async def _record_gap(
        self, session: CoachingSession, sanitized: SanitizedCoachingContext
    ) -> None:
        """Record the topic as a potential knowledge gap (§21). Never affects the session."""
        event = KnowledgeGapEvent(
            learner_id=session.learner_id,
            attempt_id=session.attempt_id,
            course_id=session.course_id,
            question_id=session.question_id,
            session_id=session.session_id,
            topic=sanitized.context.topic,
            occurred_at=session.started_at,
        )
        try:
            await self._gaps.record_gap(event)
        except Exception:  # noqa: BLE001 - analytics must never break coaching (§21).
            logger.warning(
                "coaching.knowledge_gap_failed", extra=redaction.session_context(session)
            )

    async def _record_activity(
        self,
        event_type: CoachingActivityType,
        session: CoachingSession,
        *,
        failure_code: str | None = None,
    ) -> None:
        """Record one lifecycle event (§22). Never affects the session."""
        event = CoachingActivityEvent(
            event_type=event_type,
            session_id=session.session_id,
            attempt_id=session.attempt_id,
            learner_id=session.learner_id,
            question_id=session.question_id,
            course_id=session.course_id,
            topic=session.topic,
            mode=session.mode.value,
            exchange_count=session.exchange_count,
            status=session.status.value,
            failure_code=failure_code,
            occurred_at=to_iso(self._clock.now()),
        )
        try:
            await self._activity.record(event)
        except Exception:  # noqa: BLE001 - activity logging must never break coaching (§22).
            logger.warning("coaching.activity_failed", extra=redaction.session_context(session))
