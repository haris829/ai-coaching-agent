"""Deterministic mock adapters for the UC-03 contracts.

Every mock supports the scenarios the service must survive: normal context,
missing NARIC, missing practice area, authority found, authority not found,
provider failure, slow response, timeout, and logging failure. They are
configured by constructor arguments so a test can compose any combination
without monkeypatching.

Integrity note: `MockLegalAuthorityProvider` stamps `verified_by` with an
explicit MOCK marker. Nothing downstream may mistake a development fixture for
a real company verification.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..domain.enums import (
    AuthorityStatus,
    FieldAvailability,
    FramingStrategy,
    NaricLevel,
    NaricLevelSource,
)
from ..domain.models import (
    AuthorityLookupResult,
    LearnerContext,
    Principal,
    QuestionLogRecord,
    VerifiedAuthority,
)
from ..domain.topics import TopicTag

Delay = float | Callable[[], float]

MOCK_VERIFIER = "MOCK_AUTHORITY_SOURCE (development fixture - not a real verification)"


async def _sleep(delay: Delay) -> None:
    seconds = delay() if callable(delay) else delay
    if seconds > 0:
        await asyncio.sleep(seconds)


class ProviderFailure(RuntimeError):
    """Raised by a mock configured to simulate a dependency failure."""


# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class FixedClock:
    moment: datetime = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.moment


# --------------------------------------------------------------------------
# Context provider
# --------------------------------------------------------------------------


def full_context(user_id: str, session_id: str) -> LearnerContext:
    return LearnerContext(
        user_id=user_id,
        session_id=session_id,
        naric_level=NaricLevel.LEVEL_7,
        naric_level_source=NaricLevelSource.RETRIEVED,
        practice_area="employment",
        practice_area_availability=FieldAvailability.PROVIDED,
    )


def context_without_naric(user_id: str, session_id: str) -> LearnerContext:
    return LearnerContext(
        user_id=user_id,
        session_id=session_id,
        naric_level_source=NaricLevelSource.DEFAULT,
        practice_area="family",
        practice_area_availability=FieldAvailability.PROVIDED,
    )


def context_without_practice_area(user_id: str, session_id: str) -> LearnerContext:
    return LearnerContext(
        user_id=user_id,
        session_id=session_id,
        naric_level=NaricLevel.LEVEL_4,
        naric_level_source=NaricLevelSource.RETRIEVED,
        practice_area=None,
        practice_area_availability=FieldAvailability.MISSING,
    )


@dataclass
class MockContextProvider:
    """Mock `ContextProvider`.

    `builder` maps (user_id, session_id) -> LearnerContext. Set `fail=True` to
    simulate the company context service being unavailable; the service must
    then fall back to safe defaults and mark both fields PROVIDER_UNAVAILABLE.
    """

    builder: Callable[[str, str], LearnerContext] = full_context
    fail: bool = False
    delay: Delay = 0.0
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def get_context(self, *, user_id: str, session_id: str) -> LearnerContext:
        self.calls.append((user_id, session_id))
        await _sleep(self.delay)
        if self.fail:
            raise ProviderFailure("mock context provider unavailable")
        return self.builder(user_id, session_id)


# --------------------------------------------------------------------------
# Legal authority provider
# --------------------------------------------------------------------------


def _authority(citation: str, title: str, source: str, url: str, ref: str) -> VerifiedAuthority:
    return VerifiedAuthority(
        citation=citation,
        title=title,
        source=source,
        url=url,
        verified_by=MOCK_VERIFIER,
        verification_id=ref,
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# A deliberately small catalogue of real, independently checkable UK
# authorities. Anything not in the catalogue returns NO_VERIFIED_AUTHORITY -
# the mock never invents a citation to fill a gap.
DEFAULT_AUTHORITY_CATALOGUE: dict[TopicTag, VerifiedAuthority] = {
    TopicTag.NEGLIGENCE: _authority(
        "Donoghue v Stevenson [1932] UKHL 100",
        "Donoghue v Stevenson",
        "BAILII",
        "https://www.bailii.org/uk/cases/UKHL/1932/100.html",
        "MOCK-REF-0001",
    ),
    TopicTag.CONTRACT_FORMATION: _authority(
        "Carlill v Carbolic Smoke Ball Co [1892] EWCA Civ 1",
        "Carlill v Carbolic Smoke Ball Company",
        "BAILII",
        "https://www.bailii.org/ew/cases/EWCA/Civ/1892/1.html",
        "MOCK-REF-0002",
    ),
}


@dataclass
class MockLegalAuthorityProvider:
    """Mock `LegalAuthorityProvider`.

    Returns VERIFIED only for topics present in the catalogue. `fail=True`
    simulates the approved legal source being unreachable; `force_no_authority`
    forces the NO_VERIFIED_AUTHORITY branch regardless of catalogue contents.
    """

    catalogue: dict[TopicTag, VerifiedAuthority] = field(
        default_factory=lambda: dict(DEFAULT_AUTHORITY_CATALOGUE)
    )
    fail: bool = False
    force_no_authority: bool = False
    delay: Delay = 0.0
    calls: list[str] = field(default_factory=list)

    async def lookup(
        self, *, question: str, topic_tag: str, practice_area: str | None
    ) -> AuthorityLookupResult:
        self.calls.append(topic_tag)
        await _sleep(self.delay)
        if self.fail:
            raise ProviderFailure("mock legal authority source unavailable")
        if self.force_no_authority:
            return AuthorityLookupResult(status=AuthorityStatus.NO_VERIFIED_AUTHORITY)
        try:
            tag = TopicTag(topic_tag)
        except ValueError:
            return AuthorityLookupResult(status=AuthorityStatus.NO_VERIFIED_AUTHORITY)
        found = self.catalogue.get(tag)
        if found is None:
            return AuthorityLookupResult(status=AuthorityStatus.NO_VERIFIED_AUTHORITY)
        return AuthorityLookupResult(status=AuthorityStatus.VERIFIED, authority=found)


# --------------------------------------------------------------------------
# Question logger
# --------------------------------------------------------------------------


@dataclass
class InMemoryQuestionLogger:
    """Mock `QuestionLogger` standing in for the company database/event log."""

    fail: bool = False
    delay: Delay = 0.0
    records: list[QuestionLogRecord] = field(default_factory=list)

    async def log(self, record: QuestionLogRecord) -> None:
        await _sleep(self.delay)
        if self.fail:
            raise ProviderFailure("mock question log unavailable")
        self.records.append(record)

    async def get_interaction(self, *, question_id: str) -> QuestionLogRecord | None:
        """`InteractionReader`: read a logged interaction back by id."""
        for record in reversed(self.records):
            if record.question_id == question_id:
                return record
        return None

    # Convenience accessors for tests
    @property
    def last(self) -> QuestionLogRecord:
        return self.records[-1]

    def __len__(self) -> int:
        return len(self.records)


# --------------------------------------------------------------------------
# Session authorisation
# --------------------------------------------------------------------------


@dataclass
class StaticSessionAuthorizer:
    """Mock `SessionAuthorizer` standing in for company authentication.

    `credentials` maps an opaque bearer token to a user id; `sessions` maps a
    session id to the user that owns it. Ownership is checked server-side, so a
    caller cannot read another user's session by passing its id.
    """

    credentials: dict[str, str] = field(
        default_factory=lambda: {"dev-token-alice": "user-alice", "dev-token-bob": "user-bob"}
    )
    sessions: dict[str, str] = field(
        default_factory=lambda: {
            "session-alice-1": "user-alice",
            "session-alice-2": "user-alice",
            "session-bob-1": "user-bob",
        }
    )

    async def authenticate(self, *, credential: str) -> Principal | None:
        user_id = self.credentials.get(credential)
        return Principal(user_id=user_id) if user_id else None

    async def owns_session(self, *, user_id: str, session_id: str) -> bool:
        return self.sessions.get(session_id) == user_id


# --------------------------------------------------------------------------
# Failure-injection helpers for generator / classifier / tagger
# --------------------------------------------------------------------------


@dataclass
class SlowAnswerGenerator:
    """Wraps a generator and adds latency - drives thinking-state and timeout tests."""

    inner: object
    delay: Delay = 0.0

    async def generate(self, request):  # noqa: ANN001, ANN201 - structural typing
        await _sleep(self.delay)
        return await self.inner.generate(request)


@dataclass
class FailingAnswerGenerator:
    async def generate(self, request):  # noqa: ANN001, ANN201
        raise ProviderFailure("mock generator failure")


@dataclass
class RogueCitationGenerator:
    """A generator that emits fabricated-looking citations in its prose.

    Used to prove the citation guard removes unverified citations even when the
    generator misbehaves.
    """

    async def generate(self, request):  # noqa: ANN001, ANN201
        from ..domain.models import GeneratedProse

        return GeneratedProse(
            plain_english=(
                "Under Smith v Jones [2021] UKSC 99 the position is settled; see also "
                "https://example.com/fake-case for a summary."
            ),
            formal_definition=(
                "See s. 12(3) of the Imaginary Legal Practice Act 2019 for the statutory test."
            ),
            practice_example="In practice, R v Nobody [2020] EWCA Crim 1 is applied routinely.",
        )


@dataclass
class StaticTopicTagger:
    """Tagger that returns whatever it is told - including invalid proposals."""

    tag: str | None = None

    async def propose_tag(self, *, question: str) -> str | None:
        return self.tag


# --------------------------------------------------------------------------
# Framing registry
# --------------------------------------------------------------------------


@dataclass
class InMemoryFramingRegistry:
    """Mock `FramingRegistry`.

    Deliberately a separate object from the generator: the never-repeat rule is
    service state, not generator memory, so it survives generator replacement.
    """

    used: dict[tuple[str, str], set[FramingStrategy]] = field(default_factory=dict)
    explanations: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    fail: bool = False
    delay: Delay = 0.0

    async def used_framings(
        self, *, session_id: str, concept_key: str
    ) -> frozenset[FramingStrategy]:
        await _sleep(self.delay)
        if self.fail:
            raise ProviderFailure("mock framing registry unavailable")
        return frozenset(self.used.get((session_id, concept_key), set()))

    async def previous_explanations(
        self, *, session_id: str, concept_key: str
    ) -> tuple[str, ...]:
        await _sleep(self.delay)
        if self.fail:
            raise ProviderFailure("mock framing registry unavailable")
        return tuple(self.explanations.get((session_id, concept_key), []))

    async def record_framing(
        self,
        *,
        session_id: str,
        concept_key: str,
        framing: FramingStrategy,
        explanation: str,
    ) -> None:
        await _sleep(self.delay)
        if self.fail:
            raise ProviderFailure("mock framing registry unavailable")
        self.used.setdefault((session_id, concept_key), set()).add(framing)
        self.explanations.setdefault((session_id, concept_key), []).append(explanation)


@dataclass
class ParaphraseGenerator:
    """Generator that ignores the requested framing and always says the same
    thing in slightly different words. Used to prove the service rejects a
    paraphrase rather than presenting it as a new explanation."""

    async def generate(self, request):  # noqa: ANN001, ANN201
        from ..domain.models import GeneratedProse

        return GeneratedProse(
            plain_english=(
                "A duty of care arises where harm to a neighbour is reasonably "
                "foreseeable and the relationship is sufficiently proximate."
            ),
            formal_definition="The requirement must be established on the facts.",
            practice_example="An adviser reviews the file and takes a statement.",
        )
