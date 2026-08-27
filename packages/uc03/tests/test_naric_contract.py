"""Phase 0.1 - qualification level representation against the platform contract.

The contract is a closed enum LEVEL_3..LEVEL_7_PLUS plus a separate
`naric_level_source` of `retrieved` or `default`. These tests pin that, and pin
the regression that an unrecognised level must degrade rather than crash.
"""

from __future__ import annotations

import pytest

from uc03.adapters.mocks import (
    MockContextProvider,
    context_without_naric,
    full_context,
)
from uc03.domain.enums import (
    DEFAULT_NARIC_LEVEL,
    ExplanationDepth,
    NaricLevel,
    NaricLevelSource,
    ResponseStatus,
)
from uc03.domain.models import LearnerContext
from uc03.explanation import depth_for, normalise_level
from uc03.service import DEGRADED_NARIC_INVALID

from .conftest import ALICE_SESSION, build_service

QUESTION = "What is negligence in tort law?"


# --- the closed set -------------------------------------------------------


def test_level_vocabulary_matches_the_platform_contract():
    assert [level.value for level in NaricLevel] == [
        "LEVEL_3",
        "LEVEL_4",
        "LEVEL_5",
        "LEVEL_6",
        "LEVEL_7",
        "LEVEL_7_PLUS",
    ]


def test_unknown_is_not_representable_as_a_level():
    """"We don't know" is carried by the source field, never in-band."""
    assert not hasattr(NaricLevel, "UNKNOWN")
    with pytest.raises(ValueError):
        NaricLevel("UNKNOWN")


def test_source_vocabulary_matches_the_platform_contract():
    assert [s.value for s in NaricLevelSource] == ["retrieved", "default"]


def test_level_7_and_above_are_representable():
    assert NaricLevel.LEVEL_7.value == "LEVEL_7"
    assert NaricLevel.LEVEL_7_PLUS.value == "LEVEL_7_PLUS"
    assert depth_for(NaricLevel.LEVEL_7) is ExplanationDepth.ADVANCED
    assert depth_for(NaricLevel.LEVEL_7_PLUS) is ExplanationDepth.ADVANCED


def test_every_level_maps_to_a_depth():
    for level in NaricLevel:
        assert isinstance(depth_for(level), ExplanationDepth)


# --- end-to-end trace -----------------------------------------------------


@pytest.mark.parametrize("level", list(NaricLevel))
async def test_level_traces_from_provider_to_response_and_log(level, alice):
    """context provider -> domain -> depth -> log record -> API response."""
    from uc03.adapters.mocks import InMemoryQuestionLogger

    def builder(user_id, session_id):
        return full_context(user_id, session_id).model_copy(
            update={
                "naric_level": level,
                "naric_level_source": NaricLevelSource.RETRIEVED,
            }
        )

    logger = InMemoryQuestionLogger()
    svc = build_service(
        context_provider=MockContextProvider(builder=builder), logger=logger
    )
    response = await svc.answer(
        question=QUESTION, session_id=ALICE_SESSION, principal=alice
    )

    assert response.status is ResponseStatus.ANSWERED
    # API response carries the level and its provenance.
    assert response.meta.naric_level is level
    assert response.meta.naric_level_source is NaricLevelSource.RETRIEVED
    assert response.meta.explanation_depth is depth_for(level)
    # Log record carries the same, so a reader can tell what it was pitched at.
    assert logger.last.naric_level is level
    assert logger.last.naric_level_source is NaricLevelSource.RETRIEVED
    # And it survives JSON serialisation with the contract values.
    payload = response.model_dump(mode="json")
    assert payload["meta"]["naric_level"] == level.value
    assert payload["meta"]["naric_level_source"] == "retrieved"


async def test_defaulted_level_is_marked_as_defaulted(alice):
    from uc03.adapters.mocks import InMemoryQuestionLogger

    logger = InMemoryQuestionLogger()
    svc = build_service(
        context_provider=MockContextProvider(builder=context_without_naric),
        logger=logger,
    )
    response = await svc.answer(
        question=QUESTION, session_id=ALICE_SESSION, principal=alice
    )
    assert response.meta.naric_level is DEFAULT_NARIC_LEVEL
    assert response.meta.naric_level_source is NaricLevelSource.DEFAULT
    assert logger.last.naric_level_source is NaricLevelSource.DEFAULT
    # A defaulted level is never presented as the learner's real qualification.
    assert response.meta.explanation_depth is ExplanationDepth.FOUNDATION


def test_default_level_is_the_most_accessible():
    assert depth_for(DEFAULT_NARIC_LEVEL) is ExplanationDepth.FOUNDATION


# --- unrecognised values --------------------------------------------------


def test_context_model_rejects_an_unrecognised_level_at_construction():
    with pytest.raises(Exception):
        LearnerContext(user_id="u", session_id="s", naric_level="LEVEL_99")


@pytest.mark.parametrize("bad", ["LEVEL_99", "UNKNOWN", "", None, 7, "level_7"])
def test_normalise_level_never_raises(bad):
    assert normalise_level(bad) in set(NaricLevel)


@pytest.mark.parametrize("bad", ["LEVEL_99", "UNKNOWN", None, 7])
def test_depth_for_is_total(bad):
    assert isinstance(depth_for(bad), ExplanationDepth)


async def test_unrecognised_level_degrades_instead_of_crashing(alice):
    """Regression: an adapter yielding a level outside the closed set used to
    raise KeyError out of the response assembly and fail the whole request."""
    from uc03.adapters.mocks import InMemoryQuestionLogger

    def builder(user_id, session_id):
        # model_copy bypasses validation, exactly as a buggy adapter would.
        return full_context(user_id, session_id).model_copy(
            update={"naric_level": "LEVEL_99"}
        )

    logger = InMemoryQuestionLogger()
    svc = build_service(
        context_provider=MockContextProvider(builder=builder), logger=logger
    )
    response = await svc.answer(
        question=QUESTION, session_id=ALICE_SESSION, principal=alice
    )

    assert response.status is ResponseStatus.ANSWERED
    assert response.meta.naric_level is DEFAULT_NARIC_LEVEL
    assert response.meta.naric_level_source is NaricLevelSource.DEFAULT
    assert DEGRADED_NARIC_INVALID in response.meta.degraded
    assert logger.last.naric_level is DEFAULT_NARIC_LEVEL
