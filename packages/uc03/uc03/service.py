"""UC-03 Legal Concept Q&A - core service.

Depends only on the Protocols in `uc03.contracts`, so replacing a mock with a
company adapter requires no change here.

Order of operations is fixed by the company requirements:

    authorise -> validate -> classify -> (clarify | redirect | answer) -> log

Classification always completes before any answer is generated. Ambiguous
questions return exactly one clarification question and no answer. Out-of-scope
questions return a redirect. Every path - including timeout and error - writes
exactly one log record.

Follow-ups (`follow_up`) are real operations, not labels: each one selects an
explanation framing not yet used for that concept in that session, rejects a
paraphrase of an earlier framing, and says so honestly when framings run out.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from . import citation_guard, distinctness
from .config import Settings, settings as default_settings
from .contracts import (
    AnswerGenerator,
    Clock,
    ContextProvider,
    FramingRegistry,
    InteractionReader,
    LegalAuthorityProvider,
    QuestionClassifier,
    QuestionLogger,
    SessionAuthorizer,
    TopicTagger,
)
from .domain.enums import (
    DEFAULT_NARIC_LEVEL,
    AuthorityStatus,
    Classification,
    ClassificationKind,
    ExplanationDepth,
    FieldAvailability,
    FollowUpAction,
    FramingStrategy,
    LogStatus,
    NaricLevelSource,
    RatingState,
    ResponseStatus,
)
from .domain.models import (
    AnswerParts,
    AuthorityPart,
    ClassificationResult,
    GenerationRequest,
    LearnerContext,
    Principal,
    QuestionLogRecord,
    QuestionResponse,
    ResponseMeta,
    default_follow_up_actions,
)
from .domain.topics import TopicTag, validate_topic_tag
from .errors import (
    AuthenticationError,
    AuthorizationError,
    InputValidationError,
    InteractionNotFoundError,
)
from .explanation import (
    INITIAL_FRAMING,
    concept_key,
    deepen,
    depth_for,
    normalise_level,
    select_framing,
)
from .text import extract_subject

_fallback_log = logging.getLogger("uc03.fallback_question_log")

ThinkingCallback = Callable[[], Awaitable[None]]

# Degradation reason vocabulary - surfaced on `meta.degraded` and logged.
DEGRADED_CONTEXT = "context_provider_unavailable"
DEGRADED_AUTHORITY = "authority_provider_unavailable"
DEGRADED_LOG = "question_log_unavailable"
DEGRADED_TOPIC_TAG = "topic_tag_rejected"
DEGRADED_CITATIONS = "citations_redacted"
DEGRADED_PERSONALISATION = "practice_area_unavailable"
DEGRADED_NARIC = "naric_level_unavailable"
DEGRADED_NARIC_INVALID = "naric_level_unrecognised"
DEGRADED_FRAMING_REGISTRY = "framing_registry_unavailable"


@dataclass
class _Progress:
    """Mutable state shared with the timeout path.

    If the pipeline is cancelled at the 10s deadline, whatever was already
    established (classification, topic tag) still reaches the log record.
    """

    classification: ClassificationKind | None = None
    topic_tag: TopicTag = TopicTag.UNCLASSIFIED
    topic_tag_accepted: bool = False
    degraded: list[str] = field(default_factory=list)
    citation_violations: int = 0
    context: LearnerContext | None = None
    concept: str | None = None
    framing: FramingStrategy | None = None
    framings_used: tuple[FramingStrategy, ...] = ()
    framings_remaining: int | None = None
    depth: ExplanationDepth | None = None

    def degrade(self, reason: str) -> None:
        if reason not in self.degraded:
            self.degraded.append(reason)


@dataclass
class _Outcome:
    status: ResponseStatus
    classification: Classification | None = None
    parts: AnswerParts | None = None
    clarification_question: str | None = None
    message: str | None = None
    retry_available: bool = False


class QAService:
    """The UC-03 use case."""

    def __init__(
        self,
        *,
        classifier: QuestionClassifier,
        generator: AnswerGenerator,
        context_provider: ContextProvider,
        authority_provider: LegalAuthorityProvider,
        tagger: TopicTagger,
        logger: QuestionLogger,
        authorizer: SessionAuthorizer,
        clock: Clock,
        framing_registry: FramingRegistry | None = None,
        interaction_reader: InteractionReader | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._classifier = classifier
        self._generator = generator
        self._context = context_provider
        self._authority = authority_provider
        self._tagger = tagger
        self._logger = logger
        self._authorizer = authorizer
        self._clock = clock
        self._framings = framing_registry
        self._interactions = interaction_reader
        self._settings = settings or default_settings

    @property
    def settings(self) -> Settings:
        """Effective server-side configuration (read-only to callers)."""
        return self._settings

    @property
    def supports_follow_up(self) -> bool:
        return self._framings is not None and self._interactions is not None

    # -- public API --------------------------------------------------------

    async def authenticate(self, credential: str) -> Principal:
        principal = await self._authorizer.authenticate(credential=credential)
        if principal is None:
            raise AuthenticationError("Unrecognised credential.")
        return principal

    async def answer(
        self,
        *,
        question: str,
        session_id: str,
        principal: Principal,
        on_thinking: ThinkingCallback | None = None,
    ) -> QuestionResponse:
        """Answer one question.

        `principal` is resolved server-side from the credential. There is no
        parameter through which a client can supply NARIC level, practice area,
        prompts, authority data or another user's identity.
        """
        return await self._run(
            question=question,
            session_id=session_id,
            principal=principal,
            on_thinking=on_thinking,
            action=None,
            origin=None,
        )

    async def follow_up(
        self,
        *,
        question_id: str,
        action: FollowUpAction,
        session_id: str,
        principal: Principal,
        on_thinking: ThinkingCallback | None = None,
    ) -> QuestionResponse:
        """Perform a follow-up action on a previously answered question.

        Re-explains the same concept using a framing that has not been used for
        it in this session, deepening the level for GO_DEEPER.
        """
        if not self.supports_follow_up:
            raise InputValidationError(
                "Follow-up actions are not configured on this service.",
                reason="follow_up_unsupported",
            )

        await self._require_session(principal=principal, session_id=session_id)

        origin = await self._interactions.get_interaction(question_id=question_id)
        if origin is None:
            raise InteractionNotFoundError(f"No interaction {question_id}.")
        if origin.session_id != session_id or origin.user_id != principal.user_id:
            # Do not confirm the existence of another user's interaction.
            raise InteractionNotFoundError(f"No interaction {question_id}.")
        if origin.status is not ResponseStatus.ANSWERED:
            raise InputValidationError(
                "Only an answered question can be followed up.",
                reason="follow_up_target_not_answered",
            )

        return await self._run(
            question=origin.question,
            session_id=session_id,
            principal=principal,
            on_thinking=on_thinking,
            action=action,
            origin=origin,
        )

    # -- shared driver -----------------------------------------------------

    async def _run(
        self,
        *,
        question: str,
        session_id: str,
        principal: Principal,
        on_thinking: ThinkingCallback | None,
        action: FollowUpAction | None,
        origin: QuestionLogRecord | None,
    ) -> QuestionResponse:
        question_id = str(uuid.uuid4())
        progress = _Progress()
        started = time.perf_counter()

        if action is None:
            # Security: session ownership, then input validation.
            if not await self._authorizer.owns_session(
                user_id=principal.user_id, session_id=session_id
            ):
                await self._write_log(
                    question_id=question_id,
                    session_id=session_id,
                    user_id=principal.user_id,
                    question=question,
                    progress=progress,
                    status=ResponseStatus.ERROR,
                    parts=None,
                    elapsed_ms=self._elapsed_ms(started),
                    error="unauthorized_session",
                    action=None,
                    origin=None,
                )
                raise AuthorizationError(
                    "Session does not belong to the authenticated user."
                )
            try:
                self._validate_question(question)
            except InputValidationError as exc:
                await self._write_log(
                    question_id=question_id,
                    session_id=session_id,
                    user_id=principal.user_id,
                    question=question[: self._settings.max_question_chars],
                    progress=progress,
                    status=ResponseStatus.ERROR,
                    parts=None,
                    elapsed_ms=self._elapsed_ms(started),
                    error=exc.reason,
                    action=None,
                    origin=None,
                )
                raise

        thinking_emitted = False
        task = asyncio.create_task(
            self._pipeline(
                question=question,
                user_id=principal.user_id,
                session_id=session_id,
                progress=progress,
                action=action,
                origin=origin,
            )
        )
        thinking_s = self._settings.thinking_after_ms / 1000
        timeout_s = self._settings.timeout_ms / 1000

        try:
            try:
                # `shield` so reaching the 1.5s thinking mark does not cancel work.
                outcome = await asyncio.wait_for(asyncio.shield(task), timeout=thinking_s)
            except asyncio.TimeoutError:
                thinking_emitted = True
                if on_thinking is not None:
                    await on_thinking()
                outcome = await asyncio.wait_for(task, timeout=max(timeout_s - thinking_s, 0))
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            outcome = _Outcome(
                status=ResponseStatus.TIMEOUT,
                message=self._settings.timeout_message,
                retry_available=True,
            )
        except Exception as exc:  # noqa: BLE001 - any dependency failure degrades safely
            task.cancel()
            _fallback_log.warning("uc03 pipeline failed: %s", exc)
            outcome = _Outcome(
                status=ResponseStatus.ERROR,
                message=self._settings.generation_error_message,
                retry_available=True,
            )

        elapsed_ms = self._elapsed_ms(started)

        log_status = await self._write_log(
            question_id=question_id,
            session_id=session_id,
            user_id=principal.user_id,
            question=question,
            progress=progress,
            status=outcome.status,
            parts=outcome.parts,
            elapsed_ms=elapsed_ms,
            error=None if outcome.status is not ResponseStatus.ERROR else "pipeline_error",
            action=action,
            origin=origin,
        )
        if log_status is LogStatus.FAILED:
            progress.degrade(DEGRADED_LOG)

        context = progress.context
        return QuestionResponse(
            question_id=question_id,
            session_id=session_id,
            classification=outcome.classification,
            status=outcome.status,
            parts=outcome.parts,
            clarification_question=outcome.clarification_question,
            message=outcome.message,
            follow_up_actions=(
                default_follow_up_actions()
                if outcome.status is ResponseStatus.ANSWERED
                else ()
            ),
            rating_state=RatingState.PENDING,
            retry_available=outcome.retry_available,
            follow_up_of=origin.question_id if origin is not None else None,
            meta=ResponseMeta(
                elapsed_ms=elapsed_ms,
                thinking_after_ms=self._settings.thinking_after_ms,
                timeout_ms=self._settings.timeout_ms,
                thinking_state_emitted=thinking_emitted,
                explanation_depth=progress.depth,
                naric_level=context.naric_level if context is not None else None,
                naric_level_source=(
                    context.naric_level_source if context is not None else None
                ),
                practice_area_availability=(
                    context.practice_area_availability
                    if context is not None
                    else FieldAvailability.MISSING
                ),
                personalisation_applied=bool(
                    context is not None and context.has_practice_area
                ),
                topic_tag=progress.topic_tag,
                topic_tag_accepted=progress.topic_tag_accepted,
                framing=progress.framing,
                framings_used=progress.framings_used,
                framings_remaining=progress.framings_remaining,
                citation_guard_violations=progress.citation_violations,
                log_status=log_status,
                degraded=tuple(progress.degraded),
            ),
        )

    # -- pipeline ----------------------------------------------------------

    async def _pipeline(
        self,
        *,
        question: str,
        user_id: str,
        session_id: str,
        progress: _Progress,
        action: FollowUpAction | None,
        origin: QuestionLogRecord | None,
    ) -> _Outcome:
        # 1. Topic tag first so every log record carries one, even for
        #    out-of-scope and ambiguous questions.
        proposed = await self._safe_tag(question)
        progress.topic_tag, progress.topic_tag_accepted = validate_topic_tag(proposed)
        if proposed is not None and not progress.topic_tag_accepted:
            progress.degrade(DEGRADED_TOPIC_TAG)

        # 2. Classification - always before generation.
        result: ClassificationResult = await self._classifier.classify(question=question)
        progress.classification = result.kind

        if result.kind is ClassificationKind.OUT_OF_SCOPE:
            return _Outcome(
                status=ResponseStatus.OUT_OF_SCOPE,
                message=self._settings.out_of_scope_message,
            )

        if result.kind is ClassificationKind.AMBIGUOUS:
            return _Outcome(
                status=ResponseStatus.CLARIFICATION_NEEDED,
                clarification_question=(
                    result.clarification_question
                    or "Could you tell me a little more about what you would like to know?"
                ),
            )

        classification = result.kind.as_classification()
        assert classification is not None  # the remaining kinds are the three classes

        # 3. Context - safe defaults, never invented values.
        context = await self._resolve_context(
            user_id=user_id, session_id=session_id, progress=progress
        )
        progress.context = context
        if not context.has_naric:
            progress.degrade(DEGRADED_NARIC)
        if not context.has_practice_area:
            progress.degrade(DEGRADED_PERSONALISATION)

        # 4. Concept identity and framing selection.
        progress.concept = (
            origin.concept_key
            if origin is not None and origin.concept_key
            else concept_key(progress.topic_tag.value, extract_subject(question))
        )
        framing, previous, exhausted = await self._select_framing(
            session_id=session_id,
            concept=progress.concept,
            action=action,
            progress=progress,
        )
        if exhausted:
            return _Outcome(
                status=ResponseStatus.FRAMINGS_EXHAUSTED,
                classification=classification,
                message=self._settings.framings_exhausted_message,
            )
        progress.framing = framing

        # 5. Depth, deepened for GO_DEEPER.
        depth = depth_for(context.naric_level)
        if action is FollowUpAction.GO_DEEPER:
            depth = deepen(depth)
        progress.depth = depth

        # 6. Authority - verified reference or an explicit no-authority result.
        authority_part = await self._resolve_authority(
            question=question, context=context, progress=progress
        )

        # 7. Generation of the three prose parts only.
        prose = await self._generator.generate(
            GenerationRequest(
                question=question,
                classification=classification,
                depth=depth,
                practice_area=context.practice_area if context.has_practice_area else None,
                practice_area_available=context.has_practice_area,
                framing=framing,
            )
        )

        # 8. A new framing must produce a genuinely new explanation.
        if previous and distinctness.is_paraphrase(
            prose.plain_english,
            previous,
            threshold=self._settings.paraphrase_threshold,
        ):
            _fallback_log.warning(
                "rejected paraphrase for concept %s (overlap %.2f)",
                progress.concept,
                distinctness.max_overlap(prose.plain_english, previous),
            )
            return _Outcome(
                status=ResponseStatus.ERROR,
                classification=classification,
                message=self._settings.paraphrase_rejected_message,
                retry_available=True,
            )

        # 9. Citation guard over the prose.
        allowed = (
            (authority_part.authority.citation,)
            if authority_part.is_verified and authority_part.authority is not None
            else ()
        )
        parts, violations = self._guard_prose(prose, allowed)
        progress.citation_violations = violations
        if violations:
            progress.degrade(DEGRADED_CITATIONS)

        await self._record_framing(
            session_id=session_id,
            concept=progress.concept,
            framing=framing,
            explanation=parts[0],
            progress=progress,
        )

        return _Outcome(
            status=ResponseStatus.ANSWERED,
            classification=classification,
            parts=AnswerParts(
                plain_english=parts[0],
                formal_definition=parts[1],
                practice_example=parts[2],
                authority=authority_part,
            ),
        )

    # -- steps -------------------------------------------------------------

    async def _require_session(self, *, principal: Principal, session_id: str) -> None:
        if not await self._authorizer.owns_session(
            user_id=principal.user_id, session_id=session_id
        ):
            raise AuthorizationError("Session does not belong to the authenticated user.")

    async def _select_framing(
        self,
        *,
        session_id: str,
        concept: str,
        action: FollowUpAction | None,
        progress: _Progress,
    ) -> tuple[FramingStrategy, tuple[str, ...], bool]:
        """Choose a framing not yet used for this concept in this session."""
        if self._framings is None:
            return INITIAL_FRAMING, (), False

        try:
            used = await self._framings.used_framings(
                session_id=session_id, concept_key=concept
            )
            previous = await self._framings.previous_explanations(
                session_id=session_id, concept_key=concept
            )
        except Exception:  # noqa: BLE001
            progress.degrade(DEGRADED_FRAMING_REGISTRY)
            if action is not None:
                # Without the registry we cannot guarantee a framing is unused,
                # and "never repeat a framing" is not a best-effort rule.
                raise
            return INITIAL_FRAMING, (), False

        progress.framings_used = tuple(sorted(used, key=lambda f: f.value))
        chosen = select_framing(action=action.value if action else None, used=used)
        if chosen is None:
            progress.framings_remaining = 0
            return INITIAL_FRAMING, previous, True

        progress.framings_remaining = len(FramingStrategy) - len(used) - 1
        return chosen, previous, False

    async def _record_framing(
        self,
        *,
        session_id: str,
        concept: str,
        framing: FramingStrategy,
        explanation: str,
        progress: _Progress,
    ) -> None:
        if self._framings is None:
            return
        try:
            await self._framings.record_framing(
                session_id=session_id,
                concept_key=concept,
                framing=framing,
                explanation=explanation,
            )
        except Exception:  # noqa: BLE001
            progress.degrade(DEGRADED_FRAMING_REGISTRY)

    async def _safe_tag(self, question: str) -> str | None:
        try:
            return await self._tagger.propose_tag(question=question)
        except Exception:  # noqa: BLE001 - a tagger failure must not fail the answer
            return None

    async def _resolve_context(
        self, *, user_id: str, session_id: str, progress: _Progress
    ) -> LearnerContext:
        try:
            context = await self._context.get_context(
                user_id=user_id, session_id=session_id
            )
        except Exception:  # noqa: BLE001
            progress.degrade(DEGRADED_CONTEXT)
            # Safe defaults. `naric_level_source` records that the level is a
            # default, so nothing downstream presents it as the learner's real
            # qualification.
            return LearnerContext(
                user_id=user_id,
                session_id=session_id,
                naric_level=DEFAULT_NARIC_LEVEL,
                naric_level_source=NaricLevelSource.DEFAULT,
                practice_area=None,
                practice_area_availability=FieldAvailability.PROVIDER_UNAVAILABLE,
            )
        return self._normalise_context(context, progress)

    @staticmethod
    def _normalise_context(context: LearnerContext, progress: _Progress) -> LearnerContext:
        """Coerce an adapter's context into the platform contract.

        A level outside the closed set is an adapter bug; it degrades to the
        default rather than crashing the request or being stored as-is.
        """
        level = normalise_level(context.naric_level)
        if level != context.naric_level:
            progress.degrade(DEGRADED_NARIC_INVALID)
            return context.model_copy(
                update={
                    "naric_level": level,
                    "naric_level_source": NaricLevelSource.DEFAULT,
                }
            )
        return context

    async def _resolve_authority(
        self, *, question: str, context: LearnerContext, progress: _Progress
    ) -> AuthorityPart:
        try:
            result = await self._authority.lookup(
                question=question,
                topic_tag=progress.topic_tag.value,
                practice_area=context.practice_area if context.has_practice_area else None,
            )
        except Exception:  # noqa: BLE001
            progress.degrade(DEGRADED_AUTHORITY)
            return self._no_authority_part()

        if result.status is AuthorityStatus.VERIFIED and result.authority is not None:
            return AuthorityPart(
                status=AuthorityStatus.VERIFIED,
                authority=result.authority,
                verification_routes=self._settings.verification_routes,
            )
        return self._no_authority_part()

    def _no_authority_part(self) -> AuthorityPart:
        return AuthorityPart(
            status=AuthorityStatus.NO_VERIFIED_AUTHORITY,
            authority=None,
            message=self._settings.no_authority_message,
            verification_routes=self._settings.verification_routes,
        )

    def _guard_prose(self, prose, allowed: tuple[str, ...]) -> tuple[tuple[str, str, str], int]:
        if not self._settings.citation_guard_enabled:
            return (prose.plain_english, prose.formal_definition, prose.practice_example), 0
        results = [
            citation_guard.scrub(text, allowed_citations=allowed)
            for text in (prose.plain_english, prose.formal_definition, prose.practice_example)
        ]
        return (
            (results[0].text, results[1].text, results[2].text),
            sum(r.violations for r in results),
        )

    # -- validation & logging ---------------------------------------------

    def _validate_question(self, question: str) -> None:
        stripped = question.strip()
        if len(stripped) < self._settings.min_question_chars:
            raise InputValidationError("Question is too short.", reason="input_too_short")
        if len(question) > self._settings.max_question_chars:
            raise InputValidationError("Question is too long.", reason="input_too_long")

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    async def _write_log(
        self,
        *,
        question_id: str,
        session_id: str,
        user_id: str,
        question: str,
        progress: _Progress,
        status: ResponseStatus,
        parts: AnswerParts | None,
        elapsed_ms: int,
        error: str | None,
        action: FollowUpAction | None,
        origin: QuestionLogRecord | None,
    ) -> LogStatus:
        context = progress.context
        record = QuestionLogRecord(
            question_id=question_id,
            session_id=session_id,
            user_id=user_id,
            question=question,
            classification=progress.classification,
            status=status,
            answer=parts,
            topic_tag=progress.topic_tag,
            topic_tag_accepted=progress.topic_tag_accepted,
            timestamp=self._clock.now(),
            rating_state=RatingState.PENDING,
            naric_level=context.naric_level if context is not None else None,
            naric_level_source=(
                context.naric_level_source if context is not None else None
            ),
            concept_key=progress.concept,
            framing=progress.framing,
            follow_up_of=origin.question_id if origin is not None else None,
            follow_up_action=action,
            elapsed_ms=elapsed_ms,
            citation_guard_violations=progress.citation_violations,
            degraded=tuple(progress.degraded),
            error=error,
        )
        try:
            await self._logger.log(record)
            return LogStatus.RECORDED
        except Exception as exc:  # noqa: BLE001
            # A logging failure degrades service; it never fails the request and
            # never silently loses the record.
            _fallback_log.error(
                "question log write failed (%s); record=%s",
                exc,
                record.model_dump_json(),
            )
            return LogStatus.FAILED


__all__ = ["QAService"]
