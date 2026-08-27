"""Case-linked coaching - the orchestration UC-06 owns.

Order of operations is deliberate and load-bearing:

  halt check -> injection scan -> learner context -> guard classification ->
  read-access verification -> case file load -> origin verification ->
  redirect or generation -> fact-reference verification -> output scan ->
  response construction -> interaction + audit records

Read access is verified BEFORE any case content is loaded, on every request, and
the decision is never cached. Nothing here can construct a response without the
disclaimer, because the response types do not permit it; the boundary check in
emitter.py verifies that independently.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from uuid import uuid4

from ..config import Settings
from ..domain.enums import (
    DEFAULT_NARIC_LEVEL,
    ExplanationProfile,
    GuardClass,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    ResponseMode,
    SecurityIncidentKind,
    SourceStatus,
    profile_for,
)
from ..domain.errors import (
    ProviderError,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ..domain.guard_vocabulary import (
    classify_question,
    detect_injection,
    detect_output_prediction,
)
from ..domain.legal_tests import LEGAL_TEST_LIBRARY_VERSION, get_test, resolve_topic
from ..domain.models import (
    AuditRecord,
    CaseFile,
    GenerationRequest,
    GenerationResult,
    GuardResult,
    InteractionRecord,
    LearnerContext,
    SecurityIncident,
    fact_digest,
)
from ..domain.legal_tests import build_redirect
from ..domain.responses import (
    CaseLinkedResponse,
    DisclaimedResponse,
    GeneralTopicResponse,
    SafeErrorResponse,
)
from ..logging_setup import get_logger
from ..ports.case_file import CaseFileProvider
from ..ports.generator import AnswerGenerator
from ..ports.guard import GuardClassifier
from ..ports.learner_context import LearnerContextProvider
from ..ports.sinks import SecurityIncidentSink
from ..ports.storage import InteractionLogRepository, SessionHaltRepository
from .fact_references import MARKER, verify_and_render
from .prompt_registry import PromptRegistry

_log = get_logger("case_coaching")

#: Notices for the degraded, NOT case-linked path. Carry no case content.
NOTICE_CASE_FILE_UNREADABLE = (
    "The case file could not be accessed for this answer, so this is general coaching on the topic area "
    "and refers to no facts from your matter."
)
NOTICE_CONTEXT_UNAVAILABLE = (
    "Your learner profile and session details could not be confirmed, so this answer is general coaching "
    "on the topic area at the default explanation level and refers to no facts from your matter."
)
NOTICE_ACCESS_UNVERIFIABLE = (
    "Your access to the case file could not be confirmed for this answer, so this is general coaching on "
    "the topic area and refers to no facts from your matter."
)

QUESTION_CLASS_EXPLANATION = "case_linked_explanation"
QUESTION_CLASS_OUTCOME_REDIRECT = "outcome_prediction_redirect"
QUESTION_CLASS_STRATEGY_REDIRECT = "litigation_strategy_redirect"
QUESTION_CLASS_GENERAL_FALLBACK = "general_topic_fallback"

#: Recognises disclaimer-shaped text a generator may have emitted, so it can be
#: removed from generated content. The canonical disclaimer is added at the
#: boundary from the constant; the model's version is never used and never left
#: in place to sit alongside it.
_MODEL_DISCLAIMER_LINE = re.compile(
    r"^\s*(disclaimer\s*[:\-].*|.*\bnot\s+(?:constitute\s+)?legal\s+advice\b.*)$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class ServiceOutcome:
    response: DisclaimedResponse
    status_code: int
    session_id: str
    case_file_id: str | None


@dataclass(frozen=True, slots=True)
class _Resolved:
    level: NaricLevel
    source: NaricLevelSource
    status: SourceStatus
    profile: ExplanationProfile
    context: LearnerContext | None


class CaseCoachingService:
    def __init__(
        self,
        *,
        settings: Settings,
        case_files: CaseFileProvider,
        learner_context: LearnerContextProvider,
        generator: AnswerGenerator,
        guard: GuardClassifier,
        interactions: InteractionLogRepository,
        halts: SessionHaltRepository,
        security_incidents: SecurityIncidentSink,
    ) -> None:
        self._settings = settings
        self._case_files = case_files
        self._learner_context = learner_context
        self._generator = generator
        self._guard = guard
        self._interactions = interactions
        self._halts = halts
        self._security = security_incidents
        self._audit: list[AuditRecord] = []

    # ------------------------------------------------------------------ API
    def ask(
        self,
        *,
        session_id: str,
        user_id: str,
        question: str,
        case_file_id: str,
        request_id: str,
    ) -> ServiceOutcome:
        _log.info(
            "case_coaching.requested",
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file_id,
        )

        # 1. A halted session refuses further case-linked responses.
        if self._halts.is_halted(session_id):
            _log.warning(
                "case_coaching.halt_blocked_request",
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                halted=True,
            )
            self._audit_event("case_linked_coaching_refused", user_id, session_id, case_file_id, "session_halted")
            return ServiceOutcome(
                response=SafeErrorResponse(
                    code="session_halted",
                    message=(
                        "Case-linked coaching is paused for this session pending administrator review. "
                        "General study material is unaffected."
                    ),
                    request_id=request_id,
                    retryable=False,
                    session_halted=True,
                ),
                status_code=409,
                session_id=session_id,
                case_file_id=case_file_id,
            )

        # 2. Injection scan. Matching text is DATA to be logged, never an
        #    instruction to obey: the question is still answered normally.
        self._scan_for_injection(question, session_id, user_id, case_file_id, request_id)

        # 3. Learner context. Failure never removes a safety control and never
        #    blocks an answer.
        resolved = self._resolve_context(session_id, user_id, request_id)

        # 4. Guard classification, before anything is generated.
        guard_result = self._classify(question, resolved)

        # A session whose case-linked mode cannot be confirmed does not get case
        # content: we degrade rather than read a confidential file on an
        # unverified session.
        if resolved.context is None:
            return self._general_fallback(
                session_id=session_id,
                user_id=user_id,
                question=question,
                case_file_id=case_file_id,
                request_id=request_id,
                resolved=resolved,
                guard_result=guard_result,
                notice=NOTICE_CONTEXT_UNAVAILABLE,
                case_status=SourceStatus.UNAVAILABLE,
                outcome="degraded_context_unavailable",
            )
        if not resolved.context.case_linked_mode:
            self._audit_event(
                "case_linked_coaching_refused", user_id, session_id, case_file_id, "session_not_case_linked"
            )
            return self._error(
                code="session_not_case_linked",
                message="This session is not linked to a case file, so case-linked coaching is unavailable.",
                request_id=request_id,
                session_id=session_id,
                case_file_id=case_file_id,
                status_code=409,
            )
        if resolved.context.case_file_id and resolved.context.case_file_id != case_file_id:
            self._audit_event(
                "case_linked_coaching_refused", user_id, session_id, case_file_id, "case_file_not_linked_to_session"
            )
            return self._error(
                code="case_file_not_linked_to_session",
                message="The requested case file is not the one linked to this session.",
                request_id=request_id,
                session_id=session_id,
                case_file_id=case_file_id,
                status_code=409,
            )

        # 5. Read access, server-side, before any case content is loaded, on
        #    every request, never cached.
        try:
            access = self._case_files.verify_read_access(user_id, case_file_id)
        except (ProviderUnavailable, ProviderTimeout) as exc:
            _log.warning(
                "case_coaching.case_file_unavailable",
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                port=exc.port,
                error_code=exc.code,
                reason_code="access_check_failed",
            )
            return self._general_fallback(
                session_id=session_id,
                user_id=user_id,
                question=question,
                case_file_id=case_file_id,
                request_id=request_id,
                resolved=resolved,
                guard_result=guard_result,
                notice=NOTICE_ACCESS_UNVERIFIABLE,
                case_status=SourceStatus.UNAVAILABLE,
                outcome="degraded_access_unverifiable",
            )

        if not access.granted:
            self._security.record(
                SecurityIncident(
                    incident_id=uuid4().hex,
                    occurred_at=datetime.now(timezone.utc),
                    kind=SecurityIncidentKind.UNAUTHORISED_CASE_ACCESS,
                    session_id=session_id,
                    user_id=user_id,
                    case_file_id=case_file_id,
                    detail_code=access.reason_code,
                )
            )
            _log.warning(
                "case_coaching.access_denied",
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                reason_code=access.reason_code,
            )
            self._audit_event("case_linked_coaching_refused", user_id, session_id, case_file_id, "access_denied")
            return self._error(
                code="case_access_denied",
                message="You do not have read access to this case file.",
                request_id=request_id,
                session_id=session_id,
                case_file_id=case_file_id,
                status_code=403,
            )

        _log.info(
            "case_coaching.access_verified",
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file_id,
        )

        # 6. Load the case file.
        try:
            case_file = self._case_files.get_case_file(case_file_id)
        except ProviderError as exc:
            _log.warning(
                "case_coaching.case_file_unavailable",
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                port=exc.port,
                error_code=exc.code,
                reason_code="case_read_failed",
            )
            return self._general_fallback(
                session_id=session_id,
                user_id=user_id,
                question=question,
                case_file_id=case_file_id,
                request_id=request_id,
                resolved=resolved,
                guard_result=guard_result,
                notice=NOTICE_CASE_FILE_UNREADABLE,
                case_status=(
                    SourceStatus.INVALID if isinstance(exc, ProviderInvalidResponse) else SourceStatus.UNAVAILABLE
                ),
                outcome="degraded_case_file_unreadable",
            )

        # 7. Origin verification, before any of it is used.
        if not case_file.from_case_prep_agent:
            _log.warning(
                "case_coaching.origin_rejected",
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                reason_code="origin_not_case_prep_agent",
            )
            self._audit_event("case_linked_coaching_refused", user_id, session_id, case_file_id, "origin_rejected")
            return self._error(
                code="case_origin_rejected",
                message=(
                    "This case file did not originate from the case preparation workflow, so it cannot be "
                    "used for case-linked coaching."
                ),
                request_id=request_id,
                session_id=session_id,
                case_file_id=case_file_id,
                status_code=409,
            )

        _log.info(
            "case_coaching.case_file_loaded",
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file_id,
            source_status=case_file.source_status.value,
            fact_count=len(case_file.facts),
        )

        # 8. Guard: redirect instead of generating.
        if guard_result.triggered:
            return self._redirect(
                session_id=session_id,
                user_id=user_id,
                question=question,
                case_file=case_file,
                request_id=request_id,
                resolved=resolved,
                guard_result=guard_result,
            )

        # 9. Generate.
        return self._generate(
            session_id=session_id,
            user_id=user_id,
            question=question,
            case_file=case_file,
            request_id=request_id,
            resolved=resolved,
            guard_result=guard_result,
        )

    # ------------------------------------------------------------- halt view
    def session_status(self, session_id: str, user_id: str) -> tuple[dict[str, object], int]:
        """Halt state for a caller to render. Returns no case content."""
        records = self._interactions.list_for_session(session_id)
        if records and all(r.user_id != user_id for r in records):
            return {"code": "session_not_visible"}, 403
        record = self._halts.get(session_id)
        return (
            {
                "session_id": session_id,
                "case_linked_coaching_halted": record.halted,
                "halt_reason_code": record.reason_code,
                "halted_at": record.halted_at.isoformat() if record.halted_at else None,
                "interactions_recorded": len(records),
            },
            200,
        )

    def audit_records(self) -> Sequence[AuditRecord]:
        return tuple(self._audit)

    # -------------------------------------------------------------- internals
    def _scan_for_injection(
        self, question: str, session_id: str, user_id: str, case_file_id: str | None, request_id: str
    ) -> None:
        matched = detect_injection(question)
        if not matched:
            return
        self._security.record(
            SecurityIncident(
                incident_id=uuid4().hex,
                occurred_at=datetime.now(timezone.utc),
                kind=SecurityIncidentKind.PROMPT_DISCLAIMER_SUPPRESSION,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file_id,
                matched_rule_ids=matched,
                detail_code="prompt_injection_attempt",
            )
        )
        # The question text itself is NOT logged: only the rule identifiers.
        _log.warning(
            "security.incident_recorded",
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file_id,
            kind=SecurityIncidentKind.PROMPT_DISCLAIMER_SUPPRESSION.value,
            matched_rule_ids=list(matched),
        )

    def _resolve_context(self, session_id: str, user_id: str, request_id: str) -> _Resolved:
        try:
            context = self._learner_context.get_context(session_id, user_id)
        except ProviderError as exc:
            _log.warning(
                "case_coaching.context_defaulted",
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                port=exc.port,
                error_code=exc.code,
                naric_level=DEFAULT_NARIC_LEVEL.value,
                naric_level_source=NaricLevelSource.DEFAULT.value,
                source_status=(
                    SourceStatus.INVALID.value
                    if isinstance(exc, ProviderInvalidResponse)
                    else SourceStatus.UNAVAILABLE.value
                ),
            )
            status = SourceStatus.INVALID if isinstance(exc, ProviderInvalidResponse) else SourceStatus.UNAVAILABLE
            return _Resolved(
                level=DEFAULT_NARIC_LEVEL,
                source=NaricLevelSource.DEFAULT,
                status=status,
                profile=profile_for(DEFAULT_NARIC_LEVEL),
                context=None,
            )
        return _Resolved(
            level=context.naric_level,
            source=context.naric_level_source,
            status=context.source_status,
            profile=profile_for(context.naric_level),
            context=context,
        )

    def _classify(self, question: str, resolved: _Resolved) -> GuardResult:
        try:
            return self._guard.classify(question)
        except ProviderError:
            # The guard is never skipped because a provider is down: fall back to
            # the in-domain rule set, which is always available in-process.
            guard_class, rule_id = classify_question(question)
            return GuardResult(
                guard_class=guard_class,
                matched_rule_id=rule_id,
                topic_tag=resolve_topic(question).topic_tag,
            )

    def _redirect(
        self,
        *,
        session_id: str,
        user_id: str,
        question: str,
        case_file: CaseFile,
        request_id: str,
        resolved: _Resolved,
        guard_result: GuardResult,
    ) -> ServiceOutcome:
        """Substantive educational redirect, composed in-domain.

        Not configurable, not delegated to the generator, and never a bare
        refusal: the learner gets the legal test the court would apply, its
        elements, the burden, and how the case-file material maps onto it.
        """
        charges = tuple(c.label for c in case_file.charges)
        test = get_test(guard_result.topic_tag)
        if test.topic_tag == "general":
            test = resolve_topic(question, case_file.practice_area, charges)

        cited = tuple(f.fact_id for f in case_file.facts[:3])
        fact_lines = tuple(
            f"- (case file fact {fact.fact_id}) {fact.text} Set this against element "
            f"{min(index, len(test.elements))} and ask what further material the court would want."
            for index, fact in enumerate(case_file.facts[:3], 1)
        )
        content = build_redirect(guard_result.guard_class, test, resolved.profile, fact_lines)

        _log.info(
            "case_coaching.guard_redirected",
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file.case_file_id,
            guard_triggered=guard_result.guard_class.value,
            guard_rule_id=guard_result.matched_rule_id,
            topic_tag=test.topic_tag,
            legal_test_version=LEGAL_TEST_LIBRARY_VERSION,
        )

        response = CaseLinkedResponse(
            response_id=uuid4().hex,
            session_id=session_id,
            case_file_id=case_file.case_file_id,
            explanation_profile=resolved.profile.value,
            naric_level=resolved.level,
            naric_level_source=resolved.source,
            content=content,
            case_facts_referenced=cited,
            guard_triggered=guard_result.guard_class,
            case_file_status=case_file.source_status,
            learner_context_status=resolved.status,
            topic_tag=test.topic_tag,
        )
        self._record(
            response=response,
            user_id=user_id,
            question_class=(
                QUESTION_CLASS_OUTCOME_REDIRECT
                if guard_result.guard_class is GuardClass.OUTCOME_PREDICTION
                else QUESTION_CLASS_STRATEGY_REDIRECT
            ),
        )
        self._audit_event(
            "case_linked_coaching", user_id, session_id, case_file.case_file_id, "guard_redirected",
            case_file.source_status,
        )
        return ServiceOutcome(response, 200, session_id, case_file.case_file_id)

    def _generate(
        self,
        *,
        session_id: str,
        user_id: str,
        question: str,
        case_file: CaseFile,
        request_id: str,
        resolved: _Resolved,
        guard_result: GuardResult,
    ) -> ServiceOutcome:
        prompt = PromptRegistry.for_case_linked(resolved.profile)
        gen_request = GenerationRequest(
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.version,
            system_instructions=prompt.system_instructions,
            question_text=question,
            profile=resolved.profile.value,
            practice_area=case_file.practice_area,
            case_file_id=case_file.case_file_id,
            available_fact_ids=tuple(f.fact_id for f in case_file.facts),
            fact_digest=fact_digest(case_file.facts),
            charges=tuple(c.label for c in case_file.charges),
            legislation=tuple(n.citation for n in case_file.legislation_notes),
            timeout_ms=self._settings.generation_timeout_ms,
        )

        started = time.monotonic()
        try:
            result = self._generator.generate(gen_request)
        except ProviderTimeout as exc:
            return self._generation_failed(
                exc, session_id, user_id, case_file.case_file_id, request_id,
                code="generation_timeout", retryable=True, status_code=504,
                message="The explanation could not be produced in time. Please try again.",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except ProviderUnavailable as exc:
            return self._generation_failed(
                exc, session_id, user_id, case_file.case_file_id, request_id,
                code="generation_unavailable", retryable=True, status_code=503,
                message="The explanation service is temporarily unavailable. Please try again.",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except ProviderInvalidResponse as exc:
            return self._generation_failed(
                exc, session_id, user_id, case_file.case_file_id, request_id,
                code="generation_invalid", retryable=False, status_code=502,
                message="The explanation could not be produced for this question.",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        duration_ms = int((time.monotonic() - started) * 1000)

        if not isinstance(result, GenerationResult) or not isinstance(result.content, str) or not result.content.strip():
            return self._generation_failed(
                ProviderInvalidResponse("answer_generator", "malformed_or_empty_generation"),
                session_id, user_id, case_file.case_file_id, request_id,
                code="generation_invalid", retryable=False, status_code=502,
                message="The explanation could not be produced for this question.",
                duration_ms=duration_ms,
            )

        # Generated disclaimer text is discarded, never used and never left in
        # place beside the canonical one.
        content = _strip_model_disclaimer(result.content)

        # Fabricated fact references are rejected, not cleaned up and passed on.
        try:
            verified = verify_and_render(content, tuple(result.fact_ids_referenced), case_file)
        except ProviderInvalidResponse as exc:
            _log.error(
                "case_coaching.fabricated_fact_reference",
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file.case_file_id,
                reason_code=exc.detail.split(":")[0],
                fact_ids=exc.detail.split(":", 1)[1].split(",") if ":" in exc.detail else [],
            )
            self._audit_event(
                "case_linked_coaching_refused", user_id, session_id, case_file.case_file_id,
                "fabricated_fact_reference", case_file.source_status,
            )
            return self._error(
                code="generation_invalid",
                message="The explanation could not be produced for this question.",
                request_id=request_id,
                session_id=session_id,
                case_file_id=case_file.case_file_id,
                status_code=502,
            )

        # Output-side guard: a generator that predicts an outcome has it caught
        # here, and the learner gets the redirect instead of the prediction.
        predicted = detect_output_prediction(verified.text)
        if predicted:
            _log.warning(
                "case_coaching.output_prediction_blocked",
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                case_file_id=case_file.case_file_id,
                matched_rule_ids=list(predicted),
            )
            return self._redirect(
                session_id=session_id,
                user_id=user_id,
                question=question,
                case_file=case_file,
                request_id=request_id,
                resolved=resolved,
                guard_result=GuardResult(
                    guard_class=GuardClass.OUTCOME_PREDICTION,
                    matched_rule_id=predicted[0],
                    topic_tag=guard_result.topic_tag,
                ),
            )

        _log.info(
            "case_coaching.answered",
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file.case_file_id,
            naric_level=resolved.level.value,
            naric_level_source=resolved.source.value,
            explanation_profile=resolved.profile.value,
            fact_ids=list(verified.fact_ids),
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.version,
            duration_ms=duration_ms,
            disclaimer_present=True,
        )

        response = CaseLinkedResponse(
            response_id=uuid4().hex,
            session_id=session_id,
            case_file_id=case_file.case_file_id,
            explanation_profile=resolved.profile.value,
            naric_level=resolved.level,
            naric_level_source=resolved.source,
            content=verified.text,
            case_facts_referenced=verified.fact_ids,
            guard_triggered=None,
            case_file_status=case_file.source_status,
            learner_context_status=resolved.status,
            topic_tag=guard_result.topic_tag,
        )
        self._record(response=response, user_id=user_id, question_class=QUESTION_CLASS_EXPLANATION)
        self._audit_event(
            "case_linked_coaching", user_id, session_id, case_file.case_file_id, "answered",
            case_file.source_status,
        )
        return ServiceOutcome(response, 200, session_id, case_file.case_file_id)

    def _general_fallback(
        self,
        *,
        session_id: str,
        user_id: str,
        question: str,
        case_file_id: str | None,
        request_id: str,
        resolved: _Resolved,
        guard_result: GuardResult,
        notice: str,
        case_status: SourceStatus,
        outcome: str,
    ) -> ServiceOutcome:
        """Degraded coaching on the general legal topic area.

        NOT case-linked: it carries no case facts, references no case content and
        is recorded with mode=general_fallback. The learner is never left with
        nothing, and the disclaimer is present exactly as on every other path.
        """
        test = resolve_topic(question, resolved.context.practice_area if resolved.context else None)

        if guard_result.triggered:
            content = build_redirect(guard_result.guard_class, test, resolved.profile, ())
        else:
            content = self._general_content(question, test, resolved)

        _log.info(
            "case_coaching.case_file_unavailable",
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file_id,
            mode=ResponseMode.GENERAL_FALLBACK.value,
            case_file_status=case_status.value,
            learner_context_status=resolved.status.value,
            outcome=outcome,
            topic_tag=test.topic_tag,
        )

        response = GeneralTopicResponse(
            response_id=uuid4().hex,
            session_id=session_id,
            case_file_id=case_file_id,
            explanation_profile=resolved.profile.value,
            naric_level=resolved.level,
            naric_level_source=resolved.source,
            content=content,
            notice=notice,
            case_file_status=case_status,
            learner_context_status=resolved.status,
            topic_tag=test.topic_tag,
            guard_triggered=guard_result.guard_class if guard_result.triggered else None,
        )
        self._record(
            response=response,
            user_id=user_id,
            question_class=QUESTION_CLASS_GENERAL_FALLBACK,
            mode=ResponseMode.GENERAL_FALLBACK,
            case_facts=(),
        )
        self._audit_event("case_linked_coaching_degraded", user_id, session_id, case_file_id, outcome, case_status)
        return ServiceOutcome(response, 200, session_id, case_file_id)

    def _general_content(self, question: str, test, resolved: _Resolved) -> str:
        """Generated general-topic content, or the in-domain explanation.

        Degrading twice is still an answer: the in-domain library always produces
        a substantive explanation, so the learner is never left with nothing.
        """
        prompt = PromptRegistry.for_general_topic(resolved.profile)
        try:
            result = self._generator.generate(
                GenerationRequest(
                    prompt_id=prompt.prompt_id,
                    prompt_version=prompt.version,
                    system_instructions=prompt.system_instructions,
                    question_text=question,
                    profile=resolved.profile.value,
                    practice_area=test.topic_tag,
                    case_file_id=None,
                    available_fact_ids=(),
                    fact_digest=(),
                    timeout_ms=self._settings.generation_timeout_ms,
                )
            )
        except ProviderError:
            return _general_explanation(test, resolved.profile)

        if not isinstance(result, GenerationResult) or not isinstance(result.content, str):
            return _general_explanation(test, resolved.profile)

        text = _strip_model_disclaimer(result.content)
        # No case file is loaded on this path, so no fact reference could ever
        # resolve: a marker at all means the generation is unusable here.
        if MARKER.search(text) or not text.strip() or detect_output_prediction(text):
            return _general_explanation(test, resolved.profile)
        return text

    def _generation_failed(
        self,
        exc: ProviderError,
        session_id: str,
        user_id: str,
        case_file_id: str | None,
        request_id: str,
        *,
        code: str,
        retryable: bool,
        status_code: int,
        message: str,
        duration_ms: int,
    ) -> ServiceOutcome:
        _log.warning(
            "case_coaching.generation_failed",
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            case_file_id=case_file_id,
            port=exc.port,
            error_code=exc.code,
            retryable=retryable,
            duration_ms=duration_ms,
        )
        self._audit_event("case_linked_coaching_failed", user_id, session_id, case_file_id, code)
        return self._error(
            code=code,
            message=message,
            request_id=request_id,
            session_id=session_id,
            case_file_id=case_file_id,
            status_code=status_code,
            retryable=retryable,
        )

    def _error(
        self,
        *,
        code: str,
        message: str,
        request_id: str,
        session_id: str,
        case_file_id: str | None,
        status_code: int,
        retryable: bool = False,
    ) -> ServiceOutcome:
        return ServiceOutcome(
            response=SafeErrorResponse(
                code=code,
                message=message,
                request_id=request_id,
                retryable=retryable,
                session_halted=False,
            ),
            status_code=status_code,
            session_id=session_id,
            case_file_id=case_file_id,
        )

    def _record(
        self,
        *,
        response: CaseLinkedResponse | GeneralTopicResponse,
        user_id: str,
        question_class: str,
        mode: ResponseMode = ResponseMode.CASE_LINKED,
        case_facts: tuple[str, ...] | None = None,
    ) -> None:
        facts = case_facts if case_facts is not None else tuple(getattr(response, "case_facts_referenced", ()))
        self._interactions.append(
            InteractionRecord(
                interaction_id=uuid4().hex,
                session_id=response.session_id,
                user_id=user_id,
                asked_at=datetime.now(timezone.utc),
                question_class=question_class,
                topic_tag=response.topic_tag,
                naric_level=response.naric_level,
                response_id=response.response_id,
                mode=mode,
                case_file_id=response.case_file_id,
                case_facts_referenced=facts,
                guard_triggered=response.guard_triggered,
                disclaimer_present=True,
                rating_state=RatingState.PENDING,
            )
        )

    def _audit_event(
        self,
        action: str,
        user_id: str,
        session_id: str,
        case_file_id: str | None,
        outcome: str,
        source_status: SourceStatus | None = None,
    ) -> None:
        record = AuditRecord(
            audit_id=uuid4().hex,
            occurred_at=datetime.now(timezone.utc),
            action=action,
            user_id=user_id,
            session_id=session_id,
            case_file_id=case_file_id,
            outcome=outcome,
            source_status=source_status,
        )
        self._audit.append(record)
        _log.info(
            "audit.case_linked_coaching",
            audit_id=record.audit_id,
            action=action,
            user_id=user_id,
            session_id=session_id,
            case_file_id=case_file_id,
            outcome=outcome,
            source_status=source_status.value if source_status else None,
        )


def _strip_model_disclaimer(content: str) -> str:
    cleaned = _MODEL_DISCLAIMER_LINE.sub("", content)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _general_explanation(test, profile: ExplanationProfile) -> str:
    """In-domain general explanation, used when the generator cannot serve the
    degraded path either. Always available, always substantive."""
    lines = [
        f"This question sits within {test.name}. Without access to a case file this is general coaching on "
        "the topic area, so it refers to no facts from any matter.",
        "The elements a court works through are:",
    ]
    lines.extend(str(i) + ". " + element + "." for i, element in enumerate(test.elements, 1))
    lines.append("How the court approaches it: " + test.court_approach)
    lines.append("Burden and standard: " + test.burden)
    if profile is ExplanationProfile.ADVANCED:
        lines.append("Authorities: " + "; ".join(test.authorities) + ".")
        lines.append("Doctrinal note: " + test.doctrinal_note)
    elif profile is ExplanationProfile.INTERMEDIATE:
        lines.append("Key authorities: " + "; ".join(test.authorities) + ".")
    lines.append(
        "Working through each element in turn against the material you hold is the reasoning a court would "
        "recognise."
    )
    return "\n\n".join(lines)
