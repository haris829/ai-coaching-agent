"""Reusable, adapter-agnostic conformance suites - one per port.

Each suite is a pytest test class parameterised on an `adapter` fixture the
integrator supplies. Nothing here imports a mock: the assertions are about the
contract in `uc03.contracts`, so the same suite validates the shipped mocks and
a company adapter identically.

Common assertions across ports:
  * the adapter returns UC-03 domain types, not upstream payloads
  * values are normalised to the platform contract regardless of what the
    upstream sent (closed enums, lowercase tags)
  * documented failure modes raise, and the service's degradation path - not
    the adapter - decides what a failure means
  * no upstream field name, response shape or error string escapes the boundary
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from ..contracts import (
    AnswerGenerator,
    ContextProvider,
    FramingRegistry,
    InteractionReader,
    LegalAuthorityProvider,
    QuestionClassifier,
    QuestionLogger,
    SessionAuthorizer,
    TopicTagger,
)
from ..domain.enums import (
    AuthorityStatus,
    Classification,
    ClassificationKind,
    ExplanationDepth,
    FieldAvailability,
    FramingStrategy,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    ResponseStatus,
)
from ..domain.models import (
    AuthorityLookupResult,
    ClassificationResult,
    GeneratedProse,
    GenerationRequest,
    LearnerContext,
    Principal,
    QuestionLogRecord,
    VerifiedAuthority,
)
from ..domain.topics import TOPIC_VOCABULARY, TopicTag

# A question every legal-learning adapter should cope with.
SAMPLE_QUESTION = "What is negligence in tort law?"

#: Substrings that indicate an upstream implementation detail has leaked into a
#: value UC-03 will store or show.
LEAK_MARKERS: tuple[str, ...] = (
    "Traceback",
    "requests.exceptions",
    "httpx.",
    "psycopg",
    "sqlalchemy",
    "<html",
    "HTTP 5",
    "500 Internal",
)


def _assert_no_leak(value: object, where: str) -> None:
    text = str(value)
    for marker in LEAK_MARKERS:
        assert marker not in text, f"upstream detail {marker!r} leaked via {where}: {text[:200]}"


class _Base:
    """Shared plumbing. Subclasses provide the `adapter` fixture."""

    @pytest.fixture
    def adapter(self):  # pragma: no cover - must be overridden
        raise NotImplementedError(
            "Provide an `adapter` fixture returning the adapter under test."
        )


# --------------------------------------------------------------------------
# ContextProvider
# --------------------------------------------------------------------------


class ContextProviderConformance(_Base):
    """Conformance for `ContextProvider`.

    Optional fixture `known_user` -> (user_id, session_id) that the adapter can
    resolve. Defaults to a synthetic pair, which is fine for adapters that
    return a default context for unknown users.
    """

    @pytest.fixture
    def known_user(self) -> tuple[str, str]:
        return ("conformance-user", "conformance-session")

    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, ContextProvider)

    def test_get_context_is_awaitable(self, adapter):
        assert inspect.iscoroutinefunction(adapter.get_context)

    async def test_returns_a_learner_context(self, adapter, known_user):
        user_id, session_id = known_user
        context = await adapter.get_context(user_id=user_id, session_id=session_id)
        assert isinstance(context, LearnerContext), (
            "must return uc03 LearnerContext, not an upstream payload"
        )

    async def test_echoes_the_identity_it_was_given(self, adapter, known_user):
        user_id, session_id = known_user
        context = await adapter.get_context(user_id=user_id, session_id=session_id)
        assert context.user_id == user_id
        assert context.session_id == session_id

    async def test_level_is_a_closed_enum_member(self, adapter, known_user):
        user_id, session_id = known_user
        context = await adapter.get_context(user_id=user_id, session_id=session_id)
        assert isinstance(context.naric_level, NaricLevel), (
            "qualification level must be normalised to the closed NaricLevel enum"
        )
        assert context.naric_level.value in {level.value for level in NaricLevel}

    async def test_level_source_is_recorded_separately(self, adapter, known_user):
        """A defaulted level must be distinguishable from a retrieved one."""
        user_id, session_id = known_user
        context = await adapter.get_context(user_id=user_id, session_id=session_id)
        assert isinstance(context.naric_level_source, NaricLevelSource)

    async def test_practice_area_availability_is_consistent(self, adapter, known_user):
        user_id, session_id = known_user
        context = await adapter.get_context(user_id=user_id, session_id=session_id)
        assert isinstance(context.practice_area_availability, FieldAvailability)
        if context.practice_area_availability is FieldAvailability.PROVIDED:
            assert context.practice_area, "PROVIDED requires an actual practice area"
        else:
            assert not context.has_practice_area

    async def test_no_upstream_detail_leaks(self, adapter, known_user):
        user_id, session_id = known_user
        context = await adapter.get_context(user_id=user_id, session_id=session_id)
        _assert_no_leak(context.practice_area, "practice_area")

    async def test_repeated_calls_agree(self, adapter, known_user):
        user_id, session_id = known_user
        first = await adapter.get_context(user_id=user_id, session_id=session_id)
        second = await adapter.get_context(user_id=user_id, session_id=session_id)
        assert first.naric_level is second.naric_level
        assert first.naric_level_source is second.naric_level_source


# --------------------------------------------------------------------------
# LegalAuthorityProvider
# --------------------------------------------------------------------------


class LegalAuthorityProviderConformance(_Base):
    """Conformance for `LegalAuthorityProvider` - the integrity boundary."""

    @pytest.fixture
    def lookup_topic(self) -> str:
        return TopicTag.NEGLIGENCE.value

    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, LegalAuthorityProvider)

    async def test_returns_a_lookup_result(self, adapter, lookup_topic):
        result = await adapter.lookup(
            question=SAMPLE_QUESTION, topic_tag=lookup_topic, practice_area=None
        )
        assert isinstance(result, AuthorityLookupResult)
        assert isinstance(result.status, AuthorityStatus)

    async def test_verified_results_carry_provenance(self, adapter, lookup_topic):
        """A VERIFIED claim must be auditable back to the source that vouched."""
        result = await adapter.lookup(
            question=SAMPLE_QUESTION, topic_tag=lookup_topic, practice_area=None
        )
        if result.status is AuthorityStatus.VERIFIED:
            assert isinstance(result.authority, VerifiedAuthority)
            assert result.authority.citation.strip()
            assert result.authority.verified_by.strip()
            assert result.authority.verification_id.strip()
            assert result.authority.source.strip()

    async def test_unverified_results_carry_no_authority(self, adapter, lookup_topic):
        result = await adapter.lookup(
            question=SAMPLE_QUESTION, topic_tag=lookup_topic, practice_area=None
        )
        if result.status is AuthorityStatus.NO_VERIFIED_AUTHORITY:
            assert result.authority is None, (
                "NO_VERIFIED_AUTHORITY must not smuggle a citation through"
            )

    async def test_unknown_topic_does_not_invent_an_authority(self, adapter):
        """A gap must produce NO_VERIFIED_AUTHORITY, never a plausible guess."""
        result = await adapter.lookup(
            question="What is the rule in an entirely fictional area of law?",
            topic_tag="definitely_not_a_real_topic",
            practice_area=None,
        )
        assert isinstance(result, AuthorityLookupResult)
        assert result.status is AuthorityStatus.NO_VERIFIED_AUTHORITY
        assert result.authority is None

    async def test_no_upstream_detail_leaks(self, adapter, lookup_topic):
        result = await adapter.lookup(
            question=SAMPLE_QUESTION, topic_tag=lookup_topic, practice_area=None
        )
        if result.authority is not None:
            _assert_no_leak(result.authority.citation, "citation")
            _assert_no_leak(result.authority.title, "title")


# --------------------------------------------------------------------------
# QuestionClassifier
# --------------------------------------------------------------------------


class QuestionClassifierConformance(_Base):
    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, QuestionClassifier)

    async def test_returns_a_classification_result(self, adapter):
        result = await adapter.classify(question=SAMPLE_QUESTION)
        assert isinstance(result, ClassificationResult)
        assert isinstance(result.kind, ClassificationKind)

    async def test_confidence_is_in_range(self, adapter):
        result = await adapter.classify(question=SAMPLE_QUESTION)
        assert 0.0 <= result.confidence <= 1.0

    async def test_ambiguous_carries_exactly_one_question(self, adapter):
        for question in (SAMPLE_QUESTION, "consideration", "Tell me about it"):
            result = await adapter.classify(question=question)
            if result.kind is ClassificationKind.AMBIGUOUS:
                assert result.clarification_question, (
                    "AMBIGUOUS must supply a clarification question"
                )
                assert result.clarification_question.count("?") == 1, (
                    "exactly one clarification question, not several"
                )

    async def test_decided_classes_carry_no_clarification(self, adapter):
        result = await adapter.classify(question=SAMPLE_QUESTION)
        if result.kind is not ClassificationKind.AMBIGUOUS:
            assert result.clarification_question is None

    async def test_never_returns_a_class_outside_the_enum(self, adapter):
        for question in (SAMPLE_QUESTION, "", "   ", "?" * 50, "How do I cook pasta?"):
            result = await adapter.classify(question=question)
            assert result.kind in set(ClassificationKind)


# --------------------------------------------------------------------------
# AnswerGenerator
# --------------------------------------------------------------------------


class AnswerGeneratorConformance(_Base):
    """Conformance for `AnswerGenerator`.

    The critical assertion is negative: the generator must not be able to
    produce an Authority Reference. That part comes only from a verified
    authority source.
    """

    @pytest.fixture(params=list(ExplanationDepth))
    def depth(self, request) -> ExplanationDepth:
        return request.param

    @pytest.fixture(params=list(FramingStrategy))
    def framing(self, request) -> FramingStrategy:
        return request.param

    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, AnswerGenerator)

    async def test_returns_the_three_prose_parts(self, adapter, depth, framing):
        prose = await adapter.generate(
            GenerationRequest(
                question=SAMPLE_QUESTION,
                classification=Classification.LEGAL_CONCEPT,
                depth=depth,
                practice_area="employment",
                practice_area_available=True,
                framing=framing,
            )
        )
        assert isinstance(prose, GeneratedProse)
        assert prose.plain_english.strip()
        assert prose.formal_definition.strip()
        assert prose.practice_example.strip()

    async def test_cannot_express_an_authority(self, adapter):
        assert "authority" not in GeneratedProse.model_fields, (
            "the generator contract must have no authority field"
        )

    async def test_does_not_invent_a_speciality_when_none_is_given(self, adapter):
        prose = await adapter.generate(
            GenerationRequest(
                question=SAMPLE_QUESTION,
                classification=Classification.LEGAL_CONCEPT,
                depth=ExplanationDepth.FOUNDATION,
                practice_area=None,
                practice_area_available=False,
                framing=FramingStrategy.ANALOGY,
            )
        )
        assert "employment" not in prose.practice_example.lower()

    async def test_distinct_framings_produce_distinct_prose(self, adapter):
        """A framing must change the explanation, not just be echoed back."""
        outputs = []
        for framing in FramingStrategy:
            prose = await adapter.generate(
                GenerationRequest(
                    question=SAMPLE_QUESTION,
                    classification=Classification.LEGAL_CONCEPT,
                    depth=ExplanationDepth.INTERMEDIATE,
                    practice_area=None,
                    practice_area_available=False,
                    framing=framing,
                )
            )
            outputs.append(prose.plain_english)
        assert len(set(outputs)) == len(outputs), (
            "each framing must produce a different explanation"
        )

    async def test_no_upstream_detail_leaks(self, adapter):
        prose = await adapter.generate(
            GenerationRequest(
                question=SAMPLE_QUESTION,
                classification=Classification.LEGAL_CONCEPT,
                depth=ExplanationDepth.FOUNDATION,
                practice_area=None,
                practice_area_available=False,
                framing=FramingStrategy.ANALOGY,
            )
        )
        _assert_no_leak(prose.plain_english, "plain_english")


# --------------------------------------------------------------------------
# TopicTagger
# --------------------------------------------------------------------------


class TopicTaggerConformance(_Base):
    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, TopicTagger)

    async def test_returns_a_string_or_none(self, adapter):
        proposal = await adapter.propose_tag(question=SAMPLE_QUESTION)
        assert proposal is None or isinstance(proposal, str)

    async def test_proposals_are_in_the_controlled_vocabulary(self, adapter):
        """A tagger may return None, but a proposal must be a real tag.

        UC-03 validates anyway, so a non-conforming tagger is not dangerous -
        it just silently loses analytics coverage. This test surfaces that.
        """
        proposal = await adapter.propose_tag(question=SAMPLE_QUESTION)
        if proposal is not None:
            assert proposal.strip().lower() in TOPIC_VOCABULARY, (
                f"{proposal!r} is outside the controlled vocabulary and would be "
                "coerced to 'unclassified'"
            )

    async def test_tolerates_degenerate_input(self, adapter):
        for question in ("", "   ", "?" * 200):
            proposal = await adapter.propose_tag(question=question)
            assert proposal is None or isinstance(proposal, str)


# --------------------------------------------------------------------------
# QuestionLogger
# --------------------------------------------------------------------------


def _sample_record(question_id: str = "conformance-q1") -> QuestionLogRecord:
    from datetime import datetime, timezone

    return QuestionLogRecord(
        question_id=question_id,
        session_id="conformance-session",
        user_id="conformance-user",
        question=SAMPLE_QUESTION,
        classification=ClassificationKind.LEGAL_CONCEPT,
        status=ResponseStatus.ANSWERED,
        answer=None,
        topic_tag=TopicTag.NEGLIGENCE,
        topic_tag_accepted=True,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        rating_state=RatingState.PENDING,
        naric_level=NaricLevel.LEVEL_6,
        naric_level_source=NaricLevelSource.RETRIEVED,
        concept_key="negligence|tort",
        framing=FramingStrategy.ANALOGY,
    )


class QuestionLoggerConformance(_Base):
    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, QuestionLogger)

    async def test_accepts_a_complete_record(self, adapter):
        await adapter.log(_sample_record())

    async def test_accepts_a_record_with_no_answer(self, adapter):
        """Timeouts, errors and clarifications all log with `answer=None`."""
        record = _sample_record("conformance-q2").model_copy(
            update={"status": ResponseStatus.TIMEOUT, "answer": None}
        )
        await adapter.log(record)

    async def test_accepts_every_status(self, adapter):
        for index, status in enumerate(ResponseStatus):
            record = _sample_record(f"conformance-status-{index}").model_copy(
                update={"status": status, "answer": None}
            )
            await adapter.log(record)

    async def test_writes_are_concurrent_safe(self, adapter):
        await asyncio.gather(
            *(adapter.log(_sample_record(f"conformance-concurrent-{i}")) for i in range(10))
        )

    async def test_log_returns_none(self, adapter):
        """`log` reports failure by raising, never by returning a status.

        A logger that returns a sentinel instead of raising would let UC-03
        record a write as successful when it was not.
        """
        assert await adapter.log(_sample_record("conformance-returns-none")) is None


class InteractionReaderConformance(_Base):
    """Conformance for `InteractionReader` (the follow-up read path).

    Assumes the adapter also implements `QuestionLogger`, which is the usual
    case: one store, two ports.
    """

    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, InteractionReader)

    async def test_round_trips_a_written_record(self, adapter):
        record = _sample_record("conformance-roundtrip")
        await adapter.log(record)
        found = await adapter.get_interaction(question_id="conformance-roundtrip")
        assert isinstance(found, QuestionLogRecord)
        assert found.question_id == "conformance-roundtrip"
        assert found.question == SAMPLE_QUESTION
        assert found.concept_key == "negligence|tort"

    async def test_unknown_id_returns_none_not_an_error(self, adapter):
        assert await adapter.get_interaction(question_id="no-such-id") is None


# --------------------------------------------------------------------------
# SessionAuthorizer
# --------------------------------------------------------------------------


class SessionAuthorizerConformance(_Base):
    """Conformance for `SessionAuthorizer`.

    Requires two fixtures describing a real credential/session pair:
      `valid_credential` -> str
      `owned_session`    -> session id the credential's user owns
      `foreign_session`  -> session id the credential's user does NOT own
    """

    @pytest.fixture
    def valid_credential(self) -> str:
        return "dev-token-alice"

    @pytest.fixture
    def owned_session(self) -> str:
        return "session-alice-1"

    @pytest.fixture
    def foreign_session(self) -> str:
        return "session-bob-1"

    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, SessionAuthorizer)

    async def test_valid_credential_resolves_a_principal(self, adapter, valid_credential):
        principal = await adapter.authenticate(credential=valid_credential)
        assert isinstance(principal, Principal)
        assert principal.user_id

    async def test_unknown_credential_returns_none(self, adapter):
        assert await adapter.authenticate(credential="definitely-not-a-token") is None

    async def test_empty_credential_returns_none(self, adapter):
        assert await adapter.authenticate(credential="") is None

    async def test_owner_owns_their_session(self, adapter, valid_credential, owned_session):
        principal = await adapter.authenticate(credential=valid_credential)
        assert await adapter.owns_session(
            user_id=principal.user_id, session_id=owned_session
        ) is True

    async def test_owner_does_not_own_a_foreign_session(
        self, adapter, valid_credential, foreign_session
    ):
        principal = await adapter.authenticate(credential=valid_credential)
        assert await adapter.owns_session(
            user_id=principal.user_id, session_id=foreign_session
        ) is False

    async def test_unknown_session_is_not_owned(self, adapter, valid_credential):
        principal = await adapter.authenticate(credential=valid_credential)
        assert await adapter.owns_session(
            user_id=principal.user_id, session_id="no-such-session"
        ) is False

    async def test_ownership_returns_a_real_bool(self, adapter, valid_credential, owned_session):
        principal = await adapter.authenticate(credential=valid_credential)
        result = await adapter.owns_session(
            user_id=principal.user_id, session_id=owned_session
        )
        assert isinstance(result, bool), "must be a bool, not a truthy payload"


# --------------------------------------------------------------------------
# FramingRegistry
# --------------------------------------------------------------------------


class FramingRegistryConformance(_Base):
    SESSION = "conformance-session"
    CONCEPT = "negligence|tort"

    def test_satisfies_the_protocol(self, adapter):
        assert isinstance(adapter, FramingRegistry)

    async def test_starts_empty_for_an_unseen_concept(self, adapter):
        used = await adapter.used_framings(
            session_id=self.SESSION, concept_key="never-seen-before"
        )
        assert used == frozenset()

    async def test_records_and_returns_a_framing(self, adapter):
        await adapter.record_framing(
            session_id=self.SESSION,
            concept_key=self.CONCEPT,
            framing=FramingStrategy.ANALOGY,
            explanation="An analogy about a referee.",
        )
        used = await adapter.used_framings(
            session_id=self.SESSION, concept_key=self.CONCEPT
        )
        assert FramingStrategy.ANALOGY in used

    async def test_returns_an_immutable_set_of_enum_members(self, adapter):
        await adapter.record_framing(
            session_id=self.SESSION,
            concept_key=self.CONCEPT,
            framing=FramingStrategy.WORKED_EXAMPLE,
            explanation="A worked example.",
        )
        used = await adapter.used_framings(
            session_id=self.SESSION, concept_key=self.CONCEPT
        )
        assert isinstance(used, frozenset)
        assert all(isinstance(f, FramingStrategy) for f in used)

    async def test_scoped_by_session(self, adapter):
        await adapter.record_framing(
            session_id="session-a",
            concept_key=self.CONCEPT,
            framing=FramingStrategy.ANALOGY,
            explanation="text",
        )
        other = await adapter.used_framings(
            session_id="session-b", concept_key=self.CONCEPT
        )
        assert FramingStrategy.ANALOGY not in other, "framing state must not leak across sessions"

    async def test_scoped_by_concept(self, adapter):
        await adapter.record_framing(
            session_id=self.SESSION,
            concept_key="concept-a",
            framing=FramingStrategy.ANALOGY,
            explanation="text",
        )
        other = await adapter.used_framings(
            session_id=self.SESSION, concept_key="concept-b"
        )
        assert FramingStrategy.ANALOGY not in other, "framing state must not leak across concepts"

    async def test_previous_explanations_round_trip(self, adapter):
        await adapter.record_framing(
            session_id=self.SESSION,
            concept_key="explanations",
            framing=FramingStrategy.ANALOGY,
            explanation="The referee analogy.",
        )
        previous = await adapter.previous_explanations(
            session_id=self.SESSION, concept_key="explanations"
        )
        assert isinstance(previous, tuple)
        assert "The referee analogy." in previous

    async def test_recording_is_idempotent_per_framing(self, adapter):
        for _ in range(3):
            await adapter.record_framing(
                session_id=self.SESSION,
                concept_key="idempotent",
                framing=FramingStrategy.ANALOGY,
                explanation="same text",
            )
        used = await adapter.used_framings(
            session_id=self.SESSION, concept_key="idempotent"
        )
        assert used == frozenset({FramingStrategy.ANALOGY})
