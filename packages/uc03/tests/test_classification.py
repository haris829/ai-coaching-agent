"""Requirement 1 - question classification, ambiguity, and out-of-scope."""

from __future__ import annotations

import pytest

from uc03.adapters.rule_based import RuleBasedClassifier
from uc03.domain.enums import Classification, ClassificationKind, ResponseStatus

from .conftest import ALICE_SESSION


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is negligence in tort law?", ClassificationKind.LEGAL_CONCEPT),
        ("Explain the doctrine of precedent and why it matters", ClassificationKind.LEGAL_CONCEPT),
        ("What is the difference between a freehold and a leasehold?", ClassificationKind.LEGAL_CONCEPT),
        ("How do I file a claim in the small claims court?", ClassificationKind.PROCESS),
        ("What are the steps to apply for probate?", ClassificationKind.PROCESS),
        ("What does mens rea mean?", ClassificationKind.DEFINITIONAL),
        ("What is the definition of consideration?", ClassificationKind.DEFINITIONAL),
        ("Define hearsay", ClassificationKind.DEFINITIONAL),
    ],
)
async def test_classifies_the_three_company_classes(question, expected):
    result = await RuleBasedClassifier().classify(question=question)
    assert result.kind is expected
    assert result.clarification_question is None


@pytest.mark.parametrize(
    "question",
    [
        "Tell me about consideration",
        "Define the procedure for judicial review",
        "negligence",
    ],
)
async def test_ambiguous_questions_return_exactly_one_clarification(question):
    result = await RuleBasedClassifier().classify(question=question)
    assert result.kind is ClassificationKind.AMBIGUOUS
    assert result.clarification_question
    # Exactly one question - not a list of them.
    assert result.clarification_question.count("?") == 1
    assert result.clarification_question.strip().endswith("?")


@pytest.mark.parametrize(
    "question",
    [
        "What is the weather tomorrow?",
        "How do I cook pasta?",
        "Write me a python function to sort a list",
        "Who won the football last night?",
    ],
)
async def test_non_legal_questions_are_out_of_scope(question):
    result = await RuleBasedClassifier().classify(question=question)
    assert result.kind is ClassificationKind.OUT_OF_SCOPE


async def test_ambiguous_question_does_not_generate_an_answer(service, alice):
    response = await service.answer(
        question="Tell me about consideration",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert response.status is ResponseStatus.CLARIFICATION_NEEDED
    assert response.parts is None, "no answer may be generated before clarification"
    assert response.clarification_question
    assert response.follow_up_actions == ()
    assert response.classification is None


async def test_classification_happens_before_generation(alice):
    """The generator must never be invoked for a question that needs clarifying."""
    from .conftest import build_service

    calls: list[str] = []

    class SpyGenerator:
        async def generate(self, request):  # noqa: ANN001, ANN202
            calls.append(request.question)
            raise AssertionError("generator ran before classification resolved")

    svc = build_service(generator=SpyGenerator())
    response = await svc.answer(
        question="Tell me about consideration", session_id=ALICE_SESSION, principal=alice
    )
    assert response.status is ResponseStatus.CLARIFICATION_NEEDED
    assert calls == []


async def test_out_of_scope_returns_polite_redirect(service, alice):
    response = await service.answer(
        question="What is the weather tomorrow?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert response.status is ResponseStatus.OUT_OF_SCOPE
    assert response.parts is None
    assert response.message
    assert "legal" in response.message.lower()
    assert response.follow_up_actions == ()


async def test_answered_response_carries_one_of_the_three_classes(service, alice):
    response = await service.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert response.status is ResponseStatus.ANSWERED
    assert isinstance(response.classification, Classification)
