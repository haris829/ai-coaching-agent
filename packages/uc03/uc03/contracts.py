"""The UC-03 internal contract surface.

This is the *only* file a company integrator needs to implement against. Each
Protocol below is a seam: today it is satisfied by a mock or rule-based adapter
in `uc03.adapters`; later it is satisfied by a company adapter. The service in
`uc03.service` depends on these Protocols and on nothing else, so swapping an
adapter requires no change to UC-03 business logic.

They are `typing.Protocol`s (structural), so a company adapter does not need to
import or subclass anything from UC-03 — it just needs matching methods.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .domain.enums import FramingStrategy
from .domain.models import (
    AuthorityLookupResult,
    ClassificationResult,
    GeneratedProse,
    GenerationRequest,
    LearnerContext,
    Principal,
    QuestionLogRecord,
)


@runtime_checkable
class ContextProvider(Protocol):
    """Supplies normalised learner context (NARIC level, Legal Footprints
    practice area) for a session.

    Replaced later by: the company NARIC/context service + Legal Footprints.

    Contract: may raise on failure — the service degrades to safe defaults and
    records the field as PROVIDER_UNAVAILABLE. Must never invent a NARIC level
    or practice area; omit the field instead.
    """

    async def get_context(self, *, user_id: str, session_id: str) -> LearnerContext: ...


@runtime_checkable
class LegalAuthorityProvider(Protocol):
    """Returns a *verified* legal authority, or NO_VERIFIED_AUTHORITY.

    Replaced later by: the company's approved legal authority source.

    Contract: an implementation must only return AuthorityStatus.VERIFIED when
    the citation has been affirmatively verified against a real source, and
    must populate `verified_by` / `verification_id` so the claim is auditable.
    Never derive a VERIFIED result from model output.
    """

    async def lookup(
        self, *, question: str, topic_tag: str, practice_area: str | None
    ) -> AuthorityLookupResult: ...


@runtime_checkable
class QuestionClassifier(Protocol):
    """Classifies a question before any answer is generated.

    Contract: return AMBIGUOUS with exactly one `clarification_question` rather
    than guessing between classes; return OUT_OF_SCOPE for questions outside
    legal learning.
    """

    async def classify(self, *, question: str) -> ClassificationResult: ...


@runtime_checkable
class AnswerGenerator(Protocol):
    """Produces the three prose parts of the answer.

    Contract: must not emit citations, legislation references or URLs — the
    Authority Reference part is assembled by the service from the
    LegalAuthorityProvider. Output passes through the citation guard regardless.
    """

    async def generate(self, request: GenerationRequest) -> GeneratedProse: ...


@runtime_checkable
class TopicTagger(Protocol):
    """Proposes a topic tag. The proposal is always validated against the
    controlled vocabulary before it is stored — see `domain.topics`."""

    async def propose_tag(self, *, question: str) -> str | None: ...


@runtime_checkable
class QuestionLogger(Protocol):
    """Persists one record per incoming question.

    Replaced later by: the company database / event log.

    Contract: may raise. The service treats a logging failure as degraded
    service, not as a request failure, and falls back to stderr logging so the
    record is never silently lost.
    """

    async def log(self, record: QuestionLogRecord) -> None: ...


@runtime_checkable
class SessionAuthorizer(Protocol):
    """Authenticates the caller and authorises session ownership.

    Replaced later by: the company authentication/session system.
    """

    async def authenticate(self, *, credential: str) -> Principal | None: ...

    async def owns_session(self, *, user_id: str, session_id: str) -> bool: ...


@runtime_checkable
class FramingRegistry(Protocol):
    """Remembers which explanation framings have been used, per session and
    per concept, so a follow-up never repeats one.

    Replaced later by: company-side storage (its own table, or the same store
    behind the QuestionLogger). It must NOT live in generator memory - the rule
    has to survive a process restart and hold across generator instances.
    """

    async def used_framings(
        self, *, session_id: str, concept_key: str
    ) -> frozenset[FramingStrategy]: ...

    async def previous_explanations(
        self, *, session_id: str, concept_key: str
    ) -> tuple[str, ...]:
        """The plain-English texts already shown for this concept.

        Used to reject a paraphrase: selecting an unused framing does not
        by itself prove the new explanation is actually different.
        """
        ...

    async def record_framing(
        self,
        *,
        session_id: str,
        concept_key: str,
        framing: FramingStrategy,
        explanation: str,
    ) -> None: ...


@runtime_checkable
class InteractionReader(Protocol):
    """Reads back a previously logged interaction by question id.

    Needed so a follow-up can be anchored to the original question. Replaced
    later by a read path over the company database / event log.
    """

    async def get_interaction(self, *, question_id: str) -> QuestionLogRecord | None: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


__all__ = [
    "ContextProvider",
    "LegalAuthorityProvider",
    "QuestionClassifier",
    "AnswerGenerator",
    "TopicTagger",
    "QuestionLogger",
    "SessionAuthorizer",
    "FramingRegistry",
    "InteractionReader",
    "Clock",
]
