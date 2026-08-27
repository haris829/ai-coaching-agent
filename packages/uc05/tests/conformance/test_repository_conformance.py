"""Repository conformance.

Parameterised on the repository factory, so a company-backed implementation
runs the same suite: add the factory to the list at the top and nothing else
changes.

    python -m pytest tests/conformance/test_repository_conformance.py -q
"""

from __future__ import annotations

import pytest

from uc05.adapters.memory.repositories import (
    InMemoryDialogueRepository,
    InMemoryInteractionLogRepository,
    InMemorySessionModeRepository,
)
from uc05.domain.enums import (
    DialogueState,
    ExplanationProfile,
    ModeSource,
    NaricLevel,
    NaricLevelSource,
    RatingState,
    ResponseKind,
)
from uc05.domain.models import (
    Dialogue,
    ExchangeRecord,
    InteractionLogRecord,
    utcnow,
)


def _json_mode_repository():
    """A JsonFileSessionModeRepository over a fresh temporary file.

    This is a *harness*, not a test: the conformance suite below is unchanged.
    """
    import os
    import tempfile
    import uuid

    from uc05.adapters.local.json_session_mode import JsonFileSessionModeRepository

    path = os.path.join(tempfile.gettempdir(), f"uc05-modes-{uuid.uuid4()}.json")
    os.environ["SESSION_MODE_FILE"] = path
    return JsonFileSessionModeRepository()


DIALOGUE_REPOSITORIES = [InMemoryDialogueRepository]
MODE_REPOSITORIES = [InMemorySessionModeRepository, _json_mode_repository]
LOG_REPOSITORIES = [InMemoryInteractionLogRepository]


def make_dialogue(dialogue_id="d1", session_id="s1", user_id="u1") -> Dialogue:
    now = utcnow()
    return Dialogue(
        dialogue_id=dialogue_id,
        session_id=session_id,
        user_id=user_id,
        question_text="When is a contract formed?",
        topic_tag="contract",
        naric_level=NaricLevel.LEVEL_5,
        naric_level_source=NaricLevelSource.RETRIEVED,
        explanation_profile=ExplanationProfile.INTERMEDIATE,
        practice_area=None,
        source_status={},
        state=DialogueState.AWAITING_LEARNER_RESPONSE,
        exchange_cap=5,
        exchanges=[
            ExchangeRecord(
                exchange_number=1,
                guiding_question="Which element is missing?",
                probing_focus="the missing element",
                question_fingerprint="element|miss",
                asked_at=now,
            )
        ],
        prompt_version="socratic-v1.2.0",
        created_at=now,
        updated_at=now,
    )


def make_record(interaction_id="i1", session_id="s1") -> InteractionLogRecord:
    return InteractionLogRecord(
        interaction_id=interaction_id,
        session_id=session_id,
        user_id="u1",
        asked_at=utcnow(),
        question_text="When is a contract formed?",
        topic_tag="contract",
        naric_level=NaricLevel.LEVEL_5,
        response_id="r1",
        dialogue_id="d1",
        exchange_number=1,
        response_kind=ResponseKind.GUIDING_QUESTION,
    )


# --------------------------------------------------------------------------
# DialogueRepository
# --------------------------------------------------------------------------

dialogues = pytest.mark.parametrize("factory", DIALOGUE_REPOSITORIES)


@dialogues
async def test_save_then_get_round_trips(factory):
    repository = factory()
    dialogue = make_dialogue()
    await repository.save(dialogue)

    loaded = await repository.get("d1")
    assert loaded is not None
    assert loaded.model_dump() == dialogue.model_dump()


@dialogues
async def test_a_missing_dialogue_returns_none_rather_than_raising(factory):
    assert await factory().get("nope") is None


@dialogues
async def test_saving_twice_updates_rather_than_duplicating(factory):
    repository = factory()
    dialogue = make_dialogue()
    await repository.save(dialogue)
    dialogue.state = DialogueState.CAPPED
    await repository.save(dialogue)

    assert (await repository.get("d1")).state is DialogueState.CAPPED
    assert len(await repository.for_session("s1")) == 1


@dialogues
async def test_stored_state_is_isolated_from_the_callers_object(factory):
    """A caller must not be able to mutate persisted state outside the machine."""
    repository = factory()
    dialogue = make_dialogue()
    await repository.save(dialogue)

    dialogue.state = DialogueState.ABANDONED
    assert (await repository.get("d1")).state is DialogueState.AWAITING_LEARNER_RESPONSE

    loaded = await repository.get("d1")
    loaded.exchanges.clear()
    assert len((await repository.get("d1")).exchanges) == 1


@dialogues
async def test_for_session_filters_by_session(factory):
    repository = factory()
    await repository.save(make_dialogue("d1", session_id="s1"))
    await repository.save(make_dialogue("d2", session_id="s2"))

    assert [d.dialogue_id for d in await repository.for_session("s1")] == ["d1"]
    assert await repository.for_session("unknown") == []


# --------------------------------------------------------------------------
# SessionModeRepository
# --------------------------------------------------------------------------

modes = pytest.mark.parametrize("factory", MODE_REPOSITORIES)


@modes
async def test_an_unseen_session_returns_none_and_invents_no_default(factory):
    """The application owns the default, so two implementations cannot disagree."""
    assert await factory().get_mode("never-seen") is None


@modes
async def test_set_then_get_round_trips(factory):
    repository = factory()
    stored = await repository.set_mode("s1", True, "u1")

    assert stored.enabled is True
    assert stored.source is ModeSource.PERSISTED
    assert stored.owner_user_id == "u1"
    assert stored.updated_at is not None
    assert (await repository.get_mode("s1")).enabled is True


@modes
async def test_the_first_writer_remains_the_owner(factory):
    repository = factory()
    await repository.set_mode("s1", True, "u1")
    updated = await repository.set_mode("s1", False, "u1")
    assert updated.owner_user_id == "u1"


@modes
async def test_modes_are_per_session(factory):
    repository = factory()
    await repository.set_mode("s1", True, "u1")
    assert await repository.get_mode("s2") is None


# --------------------------------------------------------------------------
# InteractionLogRepository
# --------------------------------------------------------------------------

logs = pytest.mark.parametrize("factory", LOG_REPOSITORIES)


@logs
async def test_append_then_get_round_trips(factory):
    repository = factory()
    record = make_record()
    await repository.append(record)
    assert (await repository.get("i1")).model_dump() == record.model_dump()


@logs
async def test_the_log_is_append_only_and_ordered(factory):
    repository = factory()
    for index in range(3):
        await repository.append(make_record(f"i{index}"))

    stored = await repository.list_for_session("s1")
    assert [record.interaction_id for record in stored] == ["i0", "i1", "i2"]


@logs
async def test_rating_state_defaults_to_pending(factory):
    repository = factory()
    await repository.append(make_record())
    assert (await repository.get("i1")).rating_state is RatingState.PENDING


@logs
async def test_list_filters_by_session(factory):
    repository = factory()
    await repository.append(make_record("i1", session_id="s1"))
    await repository.append(make_record("i2", session_id="s2"))
    assert [r.interaction_id for r in await repository.list_for_session("s2")] == ["i2"]
