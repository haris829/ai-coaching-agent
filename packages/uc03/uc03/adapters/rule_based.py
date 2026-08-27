"""Deterministic, dependency-free implementations of the UC-03 contracts.

These are the default adapters. They are deterministic on purpose: every test
in the suite (including the P95 benchmark) runs offline and repeatably against
them, and they give the company a working service before any LLM or legal data
source is wired in.

`TemplateAnswerGenerator` is a development stand-in for a real generator. It
produces structurally correct, depth-differentiated, personalised prose derived
from the question, and by construction never emits a citation. Production
replaces it with `uc03.adapters.llm.AnthropicAnswerGenerator` (or a company
generator) behind the same `AnswerGenerator` contract.
"""

from __future__ import annotations

import re

from ..domain.enums import ClassificationKind, ExplanationDepth, FramingStrategy
from ..domain.models import ClassificationResult, GeneratedProse, GenerationRequest
from ..domain.topics import TopicTag
from ..explanation import profile_for_depth
from ..text import extract_subject, normalise_question

# --------------------------------------------------------------------------
# Lexicons
# --------------------------------------------------------------------------

LEGAL_TERMS: frozenset[str] = frozenset(
    """
    law legal statute statutory legislation act regulation precedent
    court tribunal judge judgment judicial claimant defendant appellant
    respondent litigation lawsuit sue liability liable negligence duty breach
    damages remedy injunction tort contract contractual consideration offer
    acceptance promissory estoppel misrepresentation frustration rescission
    warranty indemnity clause covenant lease freehold leasehold conveyancing
    easement mortgage trust trustee beneficiary fiduciary probate will intestacy
    executor company director shareholder insolvency liquidation administration
    employment dismissal redundancy discrimination grievance
    criminal offence prosecution mens actus rea reus sentencing bail caution
    evidence hearsay disclosure privilege admissibility burden proof
    jurisdiction appeal pleadings claim particulars costs
    rights convention proportionality vires
    family divorce custody residence contact adoption ancillary
    immigration asylum visa deportation
    solicitor barrister counsel client conduct regulatory retainer
    consent capacity vitiating enforceable void voidable rescind
    """.split()
)

_NON_LEGAL_MARKERS: frozenset[str] = frozenset(
    """
    weather recipe cook cooking dinner football cricket score fixture
    python javascript code debug programming compile server
    stock crypto bitcoin invest portfolio
    holiday flight hotel restaurant
    symptom medication diagnosis prescription
    joke poem song film movie celebrity
    homework algebra calculus physics chemistry
    """.split()
)

_DEFINITIONAL_MARKERS: tuple[str, ...] = (
    "define",
    "definition of",
    "what does",
    "meaning of",
    "what is meant by",
    "mean in law",
    "term for",
)

_PROCESS_MARKERS: tuple[str, ...] = (
    "how do i",
    "how does one",
    "how do you",
    "how to",
    "what are the steps",
    "steps to",
    "procedure",
    "process for",
    "what happens if i",
    "what happens when i",
    "how long does it take",
    "apply for",
    "file a",
    "bring a claim",
    "make a claim",
    "who do i",
    "where do i",
)

_CONCEPT_MARKERS: tuple[str, ...] = (
    "why",
    "explain",
    "difference between",
    "principle",
    "doctrine",
    "test for",
    "when is",
    "when does",
    "how does the",
    "what is the rule",
    "purpose of",
    "rationale",
)

# Generic interrogatives. Weaker than the specific marker sets above so that
# "what is the definition of X" resolves to DEFINITIONAL rather than tying with
# LEGAL_CONCEPT, while a bare "what is X" still resolves to LEGAL_CONCEPT.
_WEAK_CONCEPT_MARKERS: tuple[str, ...] = (
    "what is",
    "what are",
    "what's",
)

_STRONG_WEIGHT = 2
_WEAK_WEIGHT = 1

_TOPIC_KEYWORDS: tuple[tuple[TopicTag, tuple[str, ...]], ...] = (
    (
        TopicTag.CONTRACT_FORMATION,
        ("consideration", "offer", "acceptance", "formation", "estoppel", "intention to create"),
    ),
    (
        TopicTag.CONTRACT_REMEDIES,
        ("damages", "specific performance", "rescission", "repudiation", "frustration", "breach of contract"),
    ),
    (
        TopicTag.NEGLIGENCE,
        ("negligence", "duty of care", "tort", "foreseeab", "causation", "nuisance"),
    ),
    (
        TopicTag.CRIMINAL_LIABILITY,
        ("mens rea", "actus reus", "criminal liability", "offence", "theft", "assault"),
    ),
    (
        TopicTag.CRIMINAL_PROCEDURE,
        ("bail", "charge", "caution", "sentencing", "plea", "custody time"),
    ),
    (
        TopicTag.CIVIL_PROCEDURE,
        ("particulars of claim", "pleading", "disclosure", "civil procedure", "small claims", "costs order"),
    ),
    (
        TopicTag.LAND_AND_PROPERTY,
        ("freehold", "leasehold", "easement", "conveyanc", "mortgage", "land registr", "covenant"),
    ),
    (
        TopicTag.EMPLOYMENT,
        ("dismissal", "redundancy", "employment", "employee", "grievance", "unfair dismissal"),
    ),
    (
        TopicTag.COMPANY_AND_INSOLVENCY,
        ("director", "shareholder", "insolven", "liquidation", "company law", "winding up"),
    ),
    (
        TopicTag.FAMILY,
        ("divorce", "child arrangements", "custody", "adoption", "matrimonial", "ancillary relief"),
    ),
    (
        TopicTag.IMMIGRATION,
        ("asylum", "visa", "deportation", "leave to remain", "immigration"),
    ),
    (
        TopicTag.WILLS_AND_PROBATE,
        ("probate", "intestacy", "executor", "testator", "bequest"),
    ),
    (
        TopicTag.EVIDENCE,
        ("hearsay", "admissib", "burden of proof", "privilege", "witness statement"),
    ),
    (
        TopicTag.HUMAN_RIGHTS,
        ("human rights", "convention right", "proportionalit", "judicial review", "ultra vires"),
    ),
    (
        TopicTag.PROFESSIONAL_CONDUCT,
        ("professional conduct", "retainer", "conflict of interest", "client care"),
    ),
    (
        TopicTag.LEGAL_SYSTEM,
        ("precedent", "stare decisis", "common law", "jurisdiction", "hierarchy of courts", "statutory interpretation"),
    ),
)

def _has_legal_signal(text: str) -> bool:
    words = set(re.findall(r"[a-z\-]+", text))
    if words & LEGAL_TERMS:
        return True
    # Multi-word legal phrases that a token-set intersection would miss.
    return any(kw in text for _, group in _TOPIC_KEYWORDS for kw in group)


def _count(text: str, markers: tuple[str, ...], weight: int = _STRONG_WEIGHT) -> int:
    return sum(weight for m in markers if m in text)


class RuleBasedClassifier:
    """Deterministic `QuestionClassifier`.

    Classification always happens before generation. Returns AMBIGUOUS with
    exactly one clarification question rather than guessing between two equally
    plausible classes.
    """

    async def classify(self, *, question: str) -> ClassificationResult:
        text = normalise_question(question)
        legal = _has_legal_signal(text)
        non_legal_hits = sum(1 for m in _NON_LEGAL_MARKERS if m in text)

        if not legal:
            return ClassificationResult(
                kind=ClassificationKind.OUT_OF_SCOPE,
                confidence=0.9 if non_legal_hits else 0.7,
                rationale="No legal-learning signal detected in the question.",
            )
        if non_legal_hits:
            return ClassificationResult(
                kind=ClassificationKind.OUT_OF_SCOPE,
                confidence=0.6,
                rationale="Question mixes legal terms with a non-legal-learning request.",
            )

        scores = {
            ClassificationKind.DEFINITIONAL: _count(text, _DEFINITIONAL_MARKERS),
            ClassificationKind.PROCESS: _count(text, _PROCESS_MARKERS),
            ClassificationKind.LEGAL_CONCEPT: (
                _count(text, _CONCEPT_MARKERS)
                + _count(text, _WEAK_CONCEPT_MARKERS, _WEAK_WEIGHT)
            ),
        }
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top_kind, top_score = ranked[0]
        runner_kind, runner_score = ranked[1]

        if top_score == 0:
            return ClassificationResult(
                kind=ClassificationKind.AMBIGUOUS,
                confidence=0.4,
                clarification_question=(
                    "Would you like a short definition of that term, an explanation "
                    "of the underlying concept, or the practical steps involved?"
                ),
                rationale="Legal subject recognised but no intent marker present.",
            )
        if top_score == runner_score:
            return ClassificationResult(
                kind=ClassificationKind.AMBIGUOUS,
                confidence=0.45,
                clarification_question=_tie_break_question(top_kind, runner_kind),
                rationale=f"Competing intent signals: {top_kind.value} vs {runner_kind.value}.",
            )

        total = sum(scores.values()) or 1
        return ClassificationResult(
            kind=top_kind,
            confidence=min(0.5 + top_score / (2 * total), 1.0),
            rationale=f"Dominant intent marker set: {top_kind.value}.",
        )


def _tie_break_question(a: ClassificationKind, b: ClassificationKind) -> str:
    pair = frozenset({a, b})
    if pair == frozenset({ClassificationKind.DEFINITIONAL, ClassificationKind.LEGAL_CONCEPT}):
        return (
            "Would you like a short definition of that term, or a fuller "
            "explanation of how the concept works?"
        )
    if pair == frozenset({ClassificationKind.PROCESS, ClassificationKind.LEGAL_CONCEPT}):
        return (
            "Are you asking about the legal principle itself, or the practical "
            "steps involved?"
        )
    if pair == frozenset({ClassificationKind.PROCESS, ClassificationKind.DEFINITIONAL}):
        return (
            "Would you like the term defined, or a walkthrough of the procedure "
            "it belongs to?"
        )
    return (
        "Could you tell me whether you want a definition, an explanation of the "
        "concept, or the steps in the process?"
    )


class RuleBasedTopicTagger:
    """Deterministic `TopicTagger`.

    Proposals are still validated against the controlled vocabulary by the
    service - this class carries no special trust.
    """

    async def propose_tag(self, *, question: str) -> str | None:
        text = normalise_question(question)
        for tag, keywords in _TOPIC_KEYWORDS:
            if any(kw in text for kw in keywords):
                return tag.value
        if _has_legal_signal(text):
            return TopicTag.LEGAL_SYSTEM.value
        return None


class TemplateAnswerGenerator:
    """Deterministic `AnswerGenerator` used for development and testing.

    Produces the three prose parts only - it is structurally incapable of
    writing the Authority Reference part. Templates deliberately contain no
    case names, statute names, section numbers or URLs.

    Each `FramingStrategy` has its own lexically distinct body, so a follow-up
    that selects a new framing produces a materially different explanation
    rather than a reworded one. Depth controls register and length on top.
    """

    async def generate(self, request: GenerationRequest) -> GeneratedProse:
        subject = extract_subject(request.question)
        profile = profile_for_depth(request.depth)
        return GeneratedProse(
            plain_english=self._plain_english(subject, request.depth, request.framing),
            formal_definition=self._formal_definition(subject, profile.register),
            practice_example=self._practice_example(
                subject,
                request.practice_area,
                request.practice_area_available,
                request.framing,
            ),
        )

    # -- plain English, one distinct body per framing ---------------------

    @staticmethod
    def _framing_body(subject: str, framing: FramingStrategy) -> str:
        if framing is FramingStrategy.ANALOGY:
            return (
                f"Picture a football referee deciding whether a tackle was fair. "
                f"They are not asking what the player meant, only whether the "
                f"challenge crossed an agreed line. {subject.capitalize()} works the "
                f"same way: an outside observer measures the behaviour against a "
                f"shared standard."
            )
        if framing is FramingStrategy.WORKED_EXAMPLE:
            return (
                f"Take a shopkeeper and a delivery driver who fall out over a "
                f"damaged crate. Walking their dispute through step by step shows "
                f"where {subject} bites: you list what each side did, ask which "
                f"single ingredient is contested, and see whether the evidence "
                f"reaches it."
            )
        if framing is FramingStrategy.CONTRAST_NEAR_MISS:
            return (
                f"The quickest route into {subject} is spotting what it is not. "
                f"A neighbouring idea looks almost identical but turns on intention "
                f"instead of conduct. Line the two up side by side and the boundary "
                f"between them becomes obvious."
            )
        if framing is FramingStrategy.FIRST_PRINCIPLES:
            return (
                f"Start from why courts need {subject} at all. Somebody has to bear "
                f"a loss, and an arbitrary allocation would be unjust. So the law "
                f"builds a test from the ground up, requiring each ingredient to be "
                f"established before responsibility shifts."
            )
        if framing is FramingStrategy.PROCEDURAL_WALKTHROUGH:
            return (
                f"Follow the file through the office. Instructions arrive, a "
                f"statement is taken, {subject} is checked against those facts, and "
                f"only then does anything go to court. Each stage narrows what "
                f"remains genuinely in dispute."
            )
        return (
            f"Most people get {subject} wrong in the same way: they assume a bad "
            f"outcome is enough. It is not. Sympathy for the injured party does no "
            f"work here, and forgetting that produces confident but wrong answers."
        )

    @classmethod
    def _plain_english(
        cls, subject: str, depth: ExplanationDepth, framing: FramingStrategy
    ) -> str:
        body = cls._framing_body(subject, framing)
        if depth is ExplanationDepth.FOUNDATION:
            return f"{body} Nothing here needs prior legal study."
        if depth is ExplanationDepth.INTERMEDIATE:
            return (
                f"{body} Note that the assessment is objective (judged by an "
                f"outside standard, not private intention), and the burden sits "
                f"with whoever asserts it."
            )
        return (
            f"{body} Doctrinally it is a threshold requirement, fact-sensitive in "
            f"application and dispositive where unmet."
        )

    @staticmethod
    def _formal_definition(subject: str, register: str) -> str:
        return (
            f"Formally, {subject} is the requirement, recognised at common law, that "
            f"the party relying upon it establish each constituent element to the civil "
            f"standard. The formulation given here is a teaching summary expressed in a "
            f"{register} register and is not a substitute for the authoritative wording "
            f"of the source material."
        )

    # -- practice example, also framing-varied ----------------------------

    _EXAMPLE_SCENARIO: dict[FramingStrategy, str] = {
        FramingStrategy.ANALOGY: "a tenant querying a deduction from a deposit",
        FramingStrategy.WORKED_EXAMPLE: "a supplier chasing an unpaid invoice",
        FramingStrategy.CONTRAST_NEAR_MISS: "two clients whose facts differ by one detail",
        FramingStrategy.FIRST_PRINCIPLES: "a first interview with a walk-in client",
        FramingStrategy.PROCEDURAL_WALKTHROUGH: "a file being prepared for a hearing",
        FramingStrategy.MISCONCEPTION_CORRECTION: "a client convinced they must win",
    }

    @classmethod
    def _practice_example(
        cls,
        subject: str,
        practice_area: str | None,
        available: bool,
        framing: FramingStrategy,
    ) -> str:
        scenario = cls._EXAMPLE_SCENARIO[framing]
        if available and practice_area:
            return (
                f"In {practice_area} practice, consider {scenario}. You would test "
                f"{subject} against what actually happened, isolate the ingredient "
                f"genuinely in dispute, and advise on the evidence needed to reach it."
            )
        return (
            f"As a general example - not tailored to any speciality, because your "
            f"practice area was not available - consider {scenario}. The adviser tests "
            f"{subject} against the facts and identifies the evidence still needed."
        )
