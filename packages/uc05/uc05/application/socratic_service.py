"""The Socratic dialogue service.

This is the only place that orchestrates.  It holds no policy of its own: the
rules live in ``domain.state_machine`` (transitions), ``domain.intent_rules``
(what a message means), ``domain.vocabulary`` (what may be said) and
``application.guards`` (what a generator may return).  What lives here is the
*order* those are consulted in, and that order carries two guarantees worth
stating explicitly:

**Generate before you mutate.**  Every provider call for a turn happens before
any change to the dialogue is persisted.  A generator timeout therefore cannot
consume an exchange, half-open a dialogue, or leave state that a retry would
trip over.  The dialogue on disk is either the turn before or the turn after,
never in between.

**Classify, then transition, then respond.**  A learner message becomes an
intent, an intent becomes an event *given the current state*, and only the
transition table decides what happens.  There is no branch anywhere in this
file that produces a direct answer without a transition row authorising it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable, TypeVar

from ..config import Settings
from ..domain import state_machine as sm
from ..domain import vocabulary as vocab
from ..domain.enums import (
    TERMINAL_STATES,
    DialogueEvent,
    RatingState,
    Resolution,
    ResponseKind,
    SourceStatus,
)
from ..domain.errors import (
    AccessDenied,
    DialogueNotFound,
    InvalidTransition,
    ProviderError,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ..domain.models import (
    Dialogue,
    ExchangeRecord,
    FourPartAnswer,
    InteractionLogRecord,
    LearnerContext,
    LearnerMessage,
    ModeState,
    utcnow,
)
from ..domain.normalisation import fingerprint, is_repeat
from ..domain.profiles import explanation_profile_for
from ..domain.topics import derive_topic_tag
from ..ports import (
    AnswerGenerator,
    DialogueRepository,
    GuidingQuestionGenerator,
    IntentClassifier,
    InteractionLogRepository,
    LearnerContextProvider,
    SessionModeRepository,
)
from .guards import AnswerGuard, GuidingQuestionGuard
from .logging_config import log_event
from .prompts import ACTIVE_PROMPT_VERSION
from .reasoning_chain import build_reasoning_chain
from .results import ContextSummary, ModeStateResult, SocraticTurn

T = TypeVar("T")


class SocraticService:
    def __init__(
        self,
        *,
        settings: Settings,
        learner_context: LearnerContextProvider,
        guiding_generator: GuidingQuestionGenerator,
        answer_generator: AnswerGenerator,
        intent_classifier: IntentClassifier,
        dialogues: DialogueRepository,
        modes: SessionModeRepository,
        interactions: InteractionLogRepository,
        guiding_guard: GuidingQuestionGuard | None = None,
        answer_guard: AnswerGuard | None = None,
    ) -> None:
        self.settings = settings
        self.learner_context = learner_context
        self.guiding_generator = guiding_generator
        self.answer_generator = answer_generator
        self.intent_classifier = intent_classifier
        self.dialogues = dialogues
        self.modes = modes
        self.interactions = interactions
        self.guiding_guard = guiding_guard or GuidingQuestionGuard()
        self.answer_guard = answer_guard or AnswerGuard()

    # ------------------------------------------------------------------
    # Provider plumbing
    # ------------------------------------------------------------------

    async def _call(
        self, port: str, factory: Callable[[], Awaitable[T]]
    ) -> T:
        """Call a provider under the configured budget, timing the result.

        ``asyncio.wait_for`` is the enforcement point for
        ``GENERATION_TIMEOUT_MS``: an adapter that hangs is cancelled here
        rather than holding the request open.  ``GENERATION_TARGET_P95_MS`` is
        not enforced -- it is a target, so exceeding it is logged, not failed.
        """
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                factory(), timeout=self.settings.generation_timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            log_event(
                "provider.timeout",
                level=logging.WARNING,
                port=port,
                duration_ms=elapsed,
                retryable=True,
            )
            raise ProviderTimeout(port, "generation budget exceeded") from exc
        except ProviderError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            log_event(
                "provider.error",
                level=logging.WARNING,
                port=port,
                error_type=type(exc).__name__,
                retryable=exc.retryable,
                duration_ms=elapsed,
            )
            raise

        elapsed = int((time.perf_counter() - started) * 1000)
        log_event(
            "provider.ok",
            port=port,
            duration_ms=elapsed,
            over_p95_target=elapsed > self.settings.generation_target_p95_ms,
        )
        return result

    async def _resolve_context(self, session_id: str, user_id: str) -> LearnerContext:
        """Fetch learner context, or proceed on the documented default.

        A context failure never leaves the learner without a response.  The
        three provider error categories map onto distinct source statuses so
        that "the source was down" and "the source answered with nonsense" stay
        distinguishable in the record.
        """
        try:
            return await self._call(
                "learner_context_provider",
                lambda: self.learner_context.get_context(session_id, user_id),
            )
        except ProviderInvalidResponse:
            status = SourceStatus.INVALID
        except (ProviderTimeout, ProviderUnavailable):
            status = SourceStatus.UNAVAILABLE

        context = LearnerContext.defaulted(status)
        log_event(
            "learner_context.defaulted",
            level=logging.WARNING,
            session_id=session_id,
            user_id=user_id,
            naric_level=context.naric_level.value,
            naric_level_source=context.naric_level_source.value,
            source_status={k: v.value for k, v in context.source_status.items()},
        )
        return context

    # ------------------------------------------------------------------
    # Mode
    # ------------------------------------------------------------------

    async def get_mode(self, session_id: str, user_id: str) -> ModeStateResult:
        state = await self._mode_state(session_id, user_id)
        return ModeStateResult(
            session_id=session_id,
            enabled=state.enabled,
            source=state.source,
            updated_at=state.updated_at.isoformat() if state.updated_at else None,
        )

    async def _mode_state(self, session_id: str, user_id: str) -> ModeState:
        stored = await self.modes.get_mode(session_id)
        if stored is None:
            return ModeState.default_for(session_id)
        if stored.owner_user_id and stored.owner_user_id != user_id:
            raise AccessDenied("session mode belongs to another user")
        return stored

    async def set_mode(
        self, session_id: str, user_id: str, enabled: bool
    ) -> ModeStateResult:
        """Toggle Socratic mode for a session.

        Turning it off closes any in-flight dialogue on that session through
        the state machine (``MODE_TOGGLED_OFF``), so the dialogue is *recorded*
        as abandoned rather than silently dropped.  The next question then
        takes the four-part answer path.
        """
        await self._mode_state(session_id, user_id)  # ownership check
        state = await self.modes.set_mode(session_id, enabled, user_id)

        closed: list[str] = []
        if not enabled:
            closed = await self._close_in_flight(session_id, user_id)

        log_event(
            "mode.set",
            session_id=session_id,
            user_id=user_id,
            mode_enabled=enabled,
            mode_source=state.source.value,
            count=len(closed),
        )
        return ModeStateResult(
            session_id=session_id,
            enabled=state.enabled,
            source=state.source,
            updated_at=state.updated_at.isoformat() if state.updated_at else None,
            closed_dialogue_ids=closed,
        )

    async def _close_in_flight(self, session_id: str, user_id: str) -> list[str]:
        closed: list[str] = []
        for dialogue in await self.dialogues.for_session(session_id):
            if not dialogue.is_open or dialogue.user_id != user_id:
                continue
            transition = sm.lookup(dialogue.state, DialogueEvent.MODE_TOGGLED_OFF)
            previous_state = dialogue.state
            dialogue.state = transition.target
            dialogue.resolution = transition.resolution
            dialogue.closed_at = utcnow()
            dialogue.updated_at = dialogue.closed_at
            await self.dialogues.save(dialogue)
            closed.append(dialogue.dialogue_id)
            log_event(
                "dialogue.transition",
                dialogue_id=dialogue.dialogue_id,
                session_id=session_id,
                user_id=user_id,
                previous_state=previous_state.value,
                state=dialogue.state.value,
                dialogue_event=DialogueEvent.MODE_TOGGLED_OFF.value,
                transition=transition.name,
                resolution=transition.resolution.value if transition.resolution else None,
                exchanges_used=dialogue.exchanges_used,
            )
        return closed

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_dialogue(self, dialogue_id: str, user_id: str) -> Dialogue:
        dialogue = await self.dialogues.get(dialogue_id)
        if dialogue is None:
            raise DialogueNotFound(dialogue_id)
        # Ownership is checked on every read, without exception.  A dialogue
        # records where a practising professional was wrong; no endpoint
        # returns another user's.
        if dialogue.user_id != user_id:
            raise AccessDenied("dialogue belongs to another user")
        return dialogue

    # ------------------------------------------------------------------
    # Asking a new question
    # ------------------------------------------------------------------

    async def ask(
        self,
        *,
        session_id: str,
        user_id: str,
        question_text: str,
        topic_tag: str | None = None,
    ) -> SocraticTurn:
        mode = await self._mode_state(session_id, user_id)
        context = await self._resolve_context(session_id, user_id)
        tag = topic_tag or derive_topic_tag(question_text)

        if not mode.enabled:
            return await self._standard_answer_turn(
                session_id=session_id,
                user_id=user_id,
                question_text=question_text,
                context=context,
                mode=mode,
            )

        now = utcnow()
        dialogue = Dialogue(
            dialogue_id=str(uuid.uuid4()),
            session_id=session_id,
            user_id=user_id,
            question_text=question_text,
            topic_tag=tag,
            naric_level=context.naric_level,
            naric_level_source=context.naric_level_source,
            explanation_profile=explanation_profile_for(context.naric_level),
            practice_area=context.practice_area,
            source_status=dict(context.source_status),
            state=sm.INITIAL_STATE,
            resolution=None,
            exchange_cap=self.settings.socratic_exchange_cap,
            exchanges=[],
            prompt_version=ACTIVE_PROMPT_VERSION,
            created_at=now,
            updated_at=now,
        )

        transition = sm.lookup(None, DialogueEvent.DIALOGUE_STARTED)
        # Generate before persisting anything: a failure here leaves no
        # half-created dialogue behind.
        generated = await self._generate_guiding(dialogue, context)

        self._open_exchange(dialogue, generated.question, generated.probing_focus)
        dialogue.state = transition.target
        dialogue.updated_at = utcnow()

        turn = await self._finalise(
            dialogue=dialogue,
            transition=transition,
            context=context,
            mode=mode,
            acknowledgement=None,
            guiding_question=generated.question,
        )
        return turn

    async def _standard_answer_turn(
        self,
        *,
        session_id: str,
        user_id: str,
        question_text: str,
        context: LearnerContext,
        mode: ModeState,
    ) -> SocraticTurn:
        """Socratic mode is off: the next response is the four-part answer.

        No dialogue is created and no interaction record is written.  UC-05
        owns Socratic dialogue only; the interaction log record it publishes is
        fixed at ``mode: "socratic"``, and writing one for a response produced
        outside Socratic mode would misdescribe it.  Logging the standard
        answer path belongs to the component that owns it (A-MODE-OFF-LOGGING).
        """
        answer = await self._generate_answer(question_text, context)
        log_event(
            "answer.standard",
            session_id=session_id,
            user_id=user_id,
            mode_enabled=False,
            response_kind=ResponseKind.DIRECT_ANSWER.value,
            explanation_profile=context.explanation_profile.value,
        )
        return SocraticTurn(
            session_id=session_id,
            dialogue_id=None,
            mode_enabled=False,
            mode_source=mode.source,
            response_kind=ResponseKind.DIRECT_ANSWER,
            state=None,
            resolution=None,
            exchanges_used=0,
            exchanges_remaining=0,
            exchange_cap=self.settings.socratic_exchange_cap,
            answer=answer,
            context=self._context_summary(context),
        )

    # ------------------------------------------------------------------
    # Replying within a dialogue
    # ------------------------------------------------------------------

    async def reply(
        self, *, dialogue_id: str, user_id: str, message: str
    ) -> SocraticTurn:
        dialogue = await self.get_dialogue(dialogue_id, user_id)

        if dialogue.state in TERMINAL_STATES:
            raise InvalidTransition(dialogue.state.value, "learner_reply")

        mode = await self._mode_state(dialogue.session_id, user_id)
        context = self._context_from_dialogue(dialogue)

        # 1. What did the learner mean?
        intent = await self._call(
            "intent_classifier",
            lambda: self.intent_classifier.classify(message, dialogue),
        )

        # 2. What event is that, in this state?
        event = sm.event_for_intent(dialogue.state, intent.kind)

        # 3. Does the cap bind before anything else is generated?
        if (
            event is DialogueEvent.SUBSTANTIVE_RESPONSE
            and dialogue.exchanges_used >= dialogue.exchange_cap
        ):
            event = DialogueEvent.CAP_REACHED

        # 4. If a new question is due, generate it and check it for a loop.
        generated_question: str | None = None
        generated_focus: str | None = None
        loop_similarity: float | None = None
        loop_index: int | None = None

        if event in sm.GENERATING_EVENTS:
            generated = await self._generate_guiding(dialogue, context)
            repeat, loop_index, loop_similarity = is_repeat(
                generated.question,
                dialogue.previous_questions(),
                threshold=self.settings.loop_similarity_threshold,
            )
            if repeat:
                event = DialogueEvent.LOOP_DETECTED
            else:
                generated_question = generated.question
                generated_focus = generated.probing_focus

        transition = sm.lookup(dialogue.state, event)

        # 5. Only now is anything mutated.  Everything above could fail
        #    without touching persisted state.
        previous_state = dialogue.state
        dialogue.exchanges[-1].learner_messages.append(
            LearnerMessage(text=message, intent=intent.kind, received_at=utcnow())
        )

        acknowledgement: str | None = None
        guiding_question: str | None = None
        exit_offer: str | None = None
        re_entry_offer: str | None = None

        if transition.opens_exchange:
            assert generated_question is not None and generated_focus is not None
            acknowledgement = self._acknowledgement_for(dialogue.exchanges_used + 1)
            self._open_exchange(dialogue, generated_question, generated_focus)
            guiding_question = generated_question
        elif transition.reposes_current_question:
            current = dialogue.current_question
            guiding_question = current.guiding_question if current else None
            acknowledgement = (
                vocab.RESUME_ACKNOWLEDGEMENT
                if event is DialogueEvent.EXIT_DECLINED
                else vocab.REDIRECT_ACKNOWLEDGEMENT
            )
        elif transition.resolution is Resolution.LEARNER_REASONED:
            acknowledgement = vocab.CLOSING_ACKNOWLEDGEMENT
            guiding_question = vocab.CONSOLIDATING_QUESTION
        elif transition.response_kind is ResponseKind.EXIT_OFFER:
            exit_offer = vocab.EXIT_OFFER

        if event is DialogueEvent.LOOP_DETECTED:
            dialogue.loop_matched_exchange = (
                loop_index + 1 if loop_index is not None else None
            )

        dialogue.state = transition.target
        dialogue.resolution = transition.resolution
        dialogue.updated_at = utcnow()
        if dialogue.state in TERMINAL_STATES:
            dialogue.closed_at = dialogue.updated_at

        if transition.resolution is Resolution.EXITED_ON_FRUSTRATION:
            re_entry_offer = vocab.RE_ENTRY_OFFER

        log_event(
            "dialogue.transition",
            dialogue_id=dialogue.dialogue_id,
            session_id=dialogue.session_id,
            user_id=user_id,
            previous_state=previous_state.value,
            state=dialogue.state.value,
            dialogue_event=event.value,
            transition=transition.name,
            intent=intent.kind.value,
            intent_rule=intent.rule,
            matched_phrase=intent.matched_phrase,
            resolution=transition.resolution.value if transition.resolution else None,
            exchanges_used=dialogue.exchanges_used,
            exchanges_remaining=dialogue.exchanges_remaining,
            loop_similarity=round(loop_similarity, 3) if loop_similarity else None,
            loop_matched_exchange=dialogue.loop_matched_exchange,
        )

        return await self._finalise(
            dialogue=dialogue,
            transition=transition,
            context=context,
            mode=mode,
            acknowledgement=acknowledgement,
            guiding_question=guiding_question,
            exit_offer=exit_offer,
            re_entry_offer=re_entry_offer,
        )

    # ------------------------------------------------------------------
    # Shared turn construction
    # ------------------------------------------------------------------

    async def _finalise(
        self,
        *,
        dialogue: Dialogue,
        transition: sm.Transition,
        context: LearnerContext,
        mode: ModeState,
        acknowledgement: str | None,
        guiding_question: str | None,
        exit_offer: str | None = None,
        re_entry_offer: str | None = None,
    ) -> SocraticTurn:
        answer: FourPartAnswer | None = None
        reasoning = None

        if transition.response_kind in sm.ANSWER_BEARING_KINDS:
            # Structural guarantee: an answer is only ever produced under a
            # transition whose resolution is one of the four permitted ones.
            assert transition.resolution in sm.DIRECT_ANSWER_RESOLUTIONS
            answer = await self._generate_answer(dialogue.question_text, context)
            if transition.response_kind is ResponseKind.CAPPED_ANSWER:
                reasoning = build_reasoning_chain(dialogue)

        await self.dialogues.save(dialogue)

        record = InteractionLogRecord(
            interaction_id=str(uuid.uuid4()),
            session_id=dialogue.session_id,
            user_id=dialogue.user_id,
            asked_at=utcnow(),
            question_text=dialogue.question_text,
            topic_tag=dialogue.topic_tag,
            naric_level=dialogue.naric_level,
            response_id=str(uuid.uuid4()),
            dialogue_id=dialogue.dialogue_id,
            exchange_number=max(1, dialogue.exchanges_used),
            response_kind=transition.response_kind or ResponseKind.GUIDING_QUESTION,
            resolution=transition.resolution,
            follow_up_of=dialogue.last_interaction_id,
            rating_state=RatingState.PENDING,
        )
        await self.interactions.append(record)

        dialogue.last_interaction_id = record.interaction_id
        await self.dialogues.save(dialogue)

        log_event(
            "interaction.recorded",
            interaction_id=record.interaction_id,
            dialogue_id=record.dialogue_id,
            session_id=record.session_id,
            user_id=record.user_id,
            exchange_number=record.exchange_number,
            response_kind=record.response_kind.value,
            resolution=record.resolution.value if record.resolution else None,
            topic_tag=record.topic_tag,
            naric_level=record.naric_level.value,
        )

        return SocraticTurn(
            session_id=dialogue.session_id,
            dialogue_id=dialogue.dialogue_id,
            mode_enabled=mode.enabled,
            mode_source=mode.source,
            response_kind=transition.response_kind or ResponseKind.GUIDING_QUESTION,
            state=dialogue.state,
            resolution=dialogue.resolution,
            exchanges_used=dialogue.exchanges_used,
            exchanges_remaining=dialogue.exchanges_remaining,
            exchange_cap=dialogue.exchange_cap,
            acknowledgement=acknowledgement,
            guiding_question=guiding_question,
            exit_offer=exit_offer,
            re_entry_offer=re_entry_offer,
            answer=answer,
            reasoning_chain=reasoning,
            interaction_id=record.interaction_id,
            transition=transition.name,
            context=self._context_summary(context),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _generate_guiding(self, dialogue: Dialogue, context: LearnerContext):
        result = await self._call(
            "guiding_question_generator",
            lambda: self.guiding_generator.generate(
                dialogue, dialogue.question_text, context
            ),
        )
        self.guiding_guard.validate(result, dialogue)
        return result

    async def _generate_answer(
        self, question_text: str, context: LearnerContext
    ) -> FourPartAnswer:
        answer = await self._call(
            "answer_generator",
            lambda: self.answer_generator.generate(question_text, context),
        )
        self.answer_guard.validate(answer.model_dump())
        return answer

    @staticmethod
    def _open_exchange(dialogue: Dialogue, question: str, probing_focus: str) -> None:
        dialogue.exchanges.append(
            ExchangeRecord(
                exchange_number=len(dialogue.exchanges) + 1,
                guiding_question=question,
                probing_focus=probing_focus,
                question_fingerprint=fingerprint(question),
                asked_at=utcnow(),
            )
        )

    @staticmethod
    def _acknowledgement_for(exchange_number: int) -> str:
        """Deterministic selection from the constrained set.

        Deterministic on purpose: the neutrality test can then assert over
        every acknowledgement the service can ever emit, rather than sampling.
        """
        return vocab.ACKNOWLEDGEMENTS[
            (exchange_number - 2) % len(vocab.ACKNOWLEDGEMENTS)
        ]

    @staticmethod
    def _context_summary(context: LearnerContext) -> ContextSummary:
        return ContextSummary(
            naric_level=context.naric_level,
            naric_level_source=context.naric_level_source,
            explanation_profile=context.explanation_profile,
            practice_area=context.practice_area,
            source_status=dict(context.source_status),
        )

    @staticmethod
    def _context_from_dialogue(dialogue: Dialogue) -> LearnerContext:
        """Reuse the context recorded when the dialogue opened.

        A dialogue is one question; re-fetching context mid-dialogue could
        change the explanation profile between exchange three and exchange
        four, which would be incoherent for the learner and would make the
        recorded ``naric_level`` on the interaction records inconsistent
        within a single dialogue (A-CONTEXT-ONCE).
        """
        return LearnerContext(
            naric_level=dialogue.naric_level,
            naric_level_source=dialogue.naric_level_source,
            practice_area=dialogue.practice_area,
            source_status=dict(dialogue.source_status),
        )


def mint_dev_session_id(settings: Settings) -> str:
    """Development helper only.

    UC-05 receives an opaque ``session_id`` and never creates one on a
    production path.  This exists so the service can be exercised standalone,
    and it is gated by ``ALLOW_DEV_SESSION_IDS``, which defaults to false.
    """
    from ..domain.errors import DevEndpointDisabled

    if not settings.allow_dev_session_ids:
        raise DevEndpointDisabled("dev session minting is disabled")
    return f"dev-session-{uuid.uuid4()}"
