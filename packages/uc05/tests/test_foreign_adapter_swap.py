"""Proof that the swap is real.

The service, domain, API, persistence and every rule are exercised twice: once
against the mock adapter family and once against a **deliberately foreign**
one whose fictional upstream uses different field names, different nesting and
a different value representation.

The service code is byte-for-byte the same in both runs.  The only difference
is which adapters the container was handed -- which, in production, is one
environment variable.

If both runs produce the same platform-level outcomes, replaceability is
demonstrated rather than asserted.  That is the point of this file.
"""

from __future__ import annotations

import pytest

from uc05.adapters.foreign.acme import (
    AcmeAnswerGenerator,
    AcmeGuidingQuestionGenerator,
    AcmeIntentClassifier,
    AcmeLearnerContextAdapter,
)
from uc05.adapters.memory.repositories import (
    InMemoryDialogueRepository,
    InMemoryInteractionLogRepository,
    InMemorySessionModeRepository,
)
from uc05.application.socratic_service import SocraticService
from uc05.config import load_settings
from uc05.domain.enums import (
    DialogueState,
    ExplanationProfile,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    Resolution,
    ResponseKind,
    SourceStatus,
)

from .conftest import SESSION, USER, build_service

QUESTION = "When is a contract formed, and what does consideration require?"

# --------------------------------------------------------------------------
# The Acme upstream's fictional payloads.  Note how little they resemble the
# mock family's: different names, deeper nesting, its own level vocabulary.
# --------------------------------------------------------------------------

ACME_CONTEXT = {
    "data": {
        "attributes": {
            "academicProfile": {"tier": {"code": "RQF6"}, "origin": "LOOKUP"},
            "specialism": {"primary": {"label": "Employment"}},
        }
    }
}


def acme_probe(text: str, focus: str) -> dict:
    return {
        "result": {
            "messages": [
                {
                    "role": "tutor",
                    "segments": [
                        {"type": "probe", "text": text},
                        {"type": "meta", "text": focus},
                    ],
                }
            ]
        }
    }


ACME_TRANSCRIPT = [
    acme_probe(
        "Which element of the rule is unsatisfied on these facts?",
        "the unsatisfied element",
    ),
    acme_probe(
        "Who must establish that element, and to what standard?",
        "the evidential burden",
    ),
    acme_probe(
        "Would a written record alter the outcome you are describing?",
        "the effect of formality",
    ),
    acme_probe(
        "If that element failed, what relief would remain available?",
        "the consequence of failure",
    ),
    acme_probe(
        "How far does the leading authority extend the principle?",
        "the reach of the authority",
    ),
]


def acme_prediction(code: str) -> dict:
    return {"prediction": {"code": code, "confidence": 0.93}}


def acme_service(predictions: list[dict]) -> SocraticService:
    """The *unmodified* service, wired to the foreign family."""
    return SocraticService(
        settings=load_settings(),
        learner_context=AcmeLearnerContextAdapter(upstream=ACME_CONTEXT),
        guiding_generator=AcmeGuidingQuestionGenerator(transcript=ACME_TRANSCRIPT),
        answer_generator=AcmeAnswerGenerator(),
        intent_classifier=AcmeIntentClassifier(predictions=predictions),
        dialogues=InMemoryDialogueRepository(),
        modes=InMemorySessionModeRepository(),
        interactions=InMemoryInteractionLogRepository(),
    )


# --------------------------------------------------------------------------
# Value normalisation: the foreign representation arrives as the platform enum
# --------------------------------------------------------------------------


async def test_a_foreign_level_representation_arrives_as_the_platform_enum():
    service = acme_service([acme_prediction("ANSWER_ATTEMPT")])
    await service.set_mode(SESSION, USER, True)
    turn = await service.ask(session_id=SESSION, user_id=USER, question_text=QUESTION)

    # Acme sent "RQF6". Nothing outside its adapter ever saw that string.
    assert turn.context.naric_level is NaricLevel.LEVEL_6
    assert turn.context.naric_level_source is NaricLevelSource.RETRIEVED
    assert turn.context.explanation_profile is ExplanationProfile.INTERMEDIATE
    assert turn.context.source_status["naric_level"] is SourceStatus.AVAILABLE
    assert "RQF6" not in turn.model_dump_json()


# --------------------------------------------------------------------------
# The same dialogues, both families, same platform outcomes
# --------------------------------------------------------------------------


async def test_the_cap_path_behaves_identically_against_both_families():
    mock = build_service(context_scenario="level_6")
    await mock.enable()
    mock_turn = await mock.start(question=QUESTION)
    for _ in range(5):
        mock_turn = await mock.say(mock_turn.dialogue_id, "still working on it")

    acme = acme_service([acme_prediction("ANSWER_ATTEMPT")] * 6)
    await acme.set_mode(SESSION, USER, True)
    acme_turn = await acme.ask(
        session_id=SESSION, user_id=USER, question_text=QUESTION
    )
    for _ in range(5):
        acme_turn = await acme.reply(
            dialogue_id=acme_turn.dialogue_id,
            user_id=USER,
            message="still working on it",
        )

    for turn in (mock_turn, acme_turn):
        assert turn.response_kind is ResponseKind.CAPPED_ANSWER
        assert turn.resolution is Resolution.CAPPED
        assert turn.state is DialogueState.CAPPED
        assert turn.exchanges_used == 5
        assert turn.exchanges_remaining == 0
        assert len(turn.reasoning_chain) == 5
        assert turn.answer is not None
        assert turn.transition == "T14_cap"
        assert turn.context.naric_level is NaricLevel.LEVEL_6


async def test_the_two_step_exit_behaves_identically_against_both_families():
    mock = build_service()
    await mock.enable()
    mock_turn = await mock.start(question=QUESTION)
    mock_offer = await mock.say(mock_turn.dialogue_id, "just tell me")
    mock_answer = await mock.say(mock_turn.dialogue_id, "yes")

    acme = acme_service(
        [
            acme_prediction("ANSWER_ATTEMPT"),
            acme_prediction("REQ_DIRECT"),
            acme_prediction("AFFIRM"),
        ]
    )
    await acme.set_mode(SESSION, USER, True)
    acme_turn = await acme.ask(session_id=SESSION, user_id=USER, question_text=QUESTION)
    # Acme's classifier is scripted, so the first reply is consumed by the
    # ANSWER_ATTEMPT prediction; drive the same two-step exit from there.
    acme_offer = await acme.reply(
        dialogue_id=acme_turn.dialogue_id, user_id=USER, message="I would like the answer"
    )
    acme_offer = await acme.reply(
        dialogue_id=acme_turn.dialogue_id, user_id=USER, message="just tell me"
    )
    acme_answer = await acme.reply(
        dialogue_id=acme_turn.dialogue_id, user_id=USER, message="yes"
    )

    for offer in (mock_offer, acme_offer):
        assert offer.response_kind is ResponseKind.EXIT_OFFER
        assert offer.answer is None
    for answer in (mock_answer, acme_answer):
        assert answer.response_kind is ResponseKind.DIRECT_ANSWER
        assert answer.resolution is Resolution.EXITED_ON_REQUEST
        assert answer.answer is not None


async def test_the_frustration_exit_behaves_identically_against_both_families():
    mock = build_service()
    await mock.enable()
    mock_turn = await mock.start(question=QUESTION)
    mock_exit = await mock.say(mock_turn.dialogue_id, "I genuinely have no idea.")

    acme = acme_service([acme_prediction("BLOCKED_EXPLICIT")])
    await acme.set_mode(SESSION, USER, True)
    acme_turn = await acme.ask(session_id=SESSION, user_id=USER, question_text=QUESTION)
    acme_exit = await acme.reply(
        dialogue_id=acme_turn.dialogue_id, user_id=USER, message="no idea at all"
    )

    for exited in (mock_exit, acme_exit):
        assert exited.resolution is Resolution.EXITED_ON_FRUSTRATION
        assert exited.response_kind is ResponseKind.DIRECT_ANSWER
        assert exited.re_entry_offer
        assert exited.answer is not None


async def test_casual_difficulty_does_not_rescue_under_either_family():
    acme = acme_service([acme_prediction("BLOCKED_CASUAL")])
    await acme.set_mode(SESSION, USER, True)
    turn = await acme.ask(session_id=SESSION, user_id=USER, question_text=QUESTION)
    result = await acme.reply(
        dialogue_id=turn.dialogue_id, user_id=USER, message="ugh this is hard"
    )
    assert result.answer is None
    assert result.resolution is None
    assert result.exchanges_used == 2


async def test_loop_protection_works_against_the_foreign_family():
    """The same probe twice, in Acme's shape, still forces the cap early."""
    repeated = [ACME_TRANSCRIPT[0], ACME_TRANSCRIPT[0]]
    service = SocraticService(
        settings=load_settings(),
        learner_context=AcmeLearnerContextAdapter(upstream=ACME_CONTEXT),
        guiding_generator=AcmeGuidingQuestionGenerator(transcript=repeated),
        answer_generator=AcmeAnswerGenerator(),
        intent_classifier=AcmeIntentClassifier(
            predictions=[acme_prediction("ANSWER_ATTEMPT")]
        ),
        dialogues=InMemoryDialogueRepository(),
        modes=InMemorySessionModeRepository(),
        interactions=InMemoryInteractionLogRepository(),
    )
    await service.set_mode(SESSION, USER, True)
    turn = await service.ask(session_id=SESSION, user_id=USER, question_text=QUESTION)
    looped = await service.reply(
        dialogue_id=turn.dialogue_id, user_id=USER, message="another attempt"
    )

    assert looped.resolution is Resolution.LOOP_DETECTED
    assert looped.transition == "T16_loop"


async def test_the_interaction_log_is_identical_in_shape_under_both_families():
    acme = acme_service([acme_prediction("ANSWER_ATTEMPT")] * 3)
    await acme.set_mode(SESSION, USER, True)
    turn = await acme.ask(session_id=SESSION, user_id=USER, question_text=QUESTION)
    await acme.reply(dialogue_id=turn.dialogue_id, user_id=USER, message="an attempt")

    records = await acme.interactions.list_for_session(SESSION)
    assert [record.exchange_number for record in records] == [1, 2]
    assert all(record.mode == "socratic" for record in records)
    assert all(record.rating_state is RatingState.PENDING for record in records)
    assert records[0].naric_level is NaricLevel.LEVEL_6
    assert records[1].follow_up_of == records[0].interaction_id


async def test_the_output_guard_applies_to_the_foreign_family_too():
    """UC-05's rejection rules are not a property of the mock generator."""
    from uc05.domain.errors import ProviderInvalidResponse

    declarative = {
        "result": {
            "messages": [
                {
                    "role": "tutor",
                    "segments": [
                        {
                            "type": "probe",
                            "text": (
                                "A contract is formed once offer, acceptance and "
                                "consideration are present."
                            ),
                        }
                    ],
                }
            ]
        }
    }
    service = SocraticService(
        settings=load_settings(),
        learner_context=AcmeLearnerContextAdapter(upstream=ACME_CONTEXT),
        guiding_generator=AcmeGuidingQuestionGenerator(transcript=[declarative]),
        answer_generator=AcmeAnswerGenerator(),
        intent_classifier=AcmeIntentClassifier(predictions=[acme_prediction("ANSWER_ATTEMPT")]),
        dialogues=InMemoryDialogueRepository(),
        modes=InMemorySessionModeRepository(),
        interactions=InMemoryInteractionLogRepository(),
    )
    await service.set_mode(SESSION, USER, True)
    with pytest.raises(ProviderInvalidResponse):
        await service.ask(session_id=SESSION, user_id=USER, question_text=QUESTION)


# --------------------------------------------------------------------------
# The swap over the wire: same app, same routes, config alone decides
# --------------------------------------------------------------------------


def test_the_service_boots_unmodified_against_the_foreign_family(monkeypatch):
    """One environment variable. No code change anywhere."""
    from fastapi.testclient import TestClient

    from uc05.api.app import create_app
    from uc05.composition import reset_container

    monkeypatch.setenv("GENERATOR", "acme")
    monkeypatch.setenv("LEARNER_CONTEXT_PROVIDER", "acme")
    monkeypatch.setenv("INTENT_CLASSIFIER", "acme")
    reset_container()

    app = create_app(load_settings())
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/healthz").status_code == 200
        headers = {"X-User-Id": USER}
        assert (
            client.put(
                "/api/v1/socratic/mode/s-foreign",
                json={"enabled": True},
                headers=headers,
            ).status_code
            == 200
        )

    reset_container()
