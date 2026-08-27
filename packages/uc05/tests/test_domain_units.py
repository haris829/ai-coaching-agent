"""Domain units: profiles, topic tags, normalisation, model invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from uc05.domain.enums import (
    ExplanationProfile,
    IntentKind,
    Mode,
    NaricLevel,
    NaricLevelSource,
    ResponseKind,
    SourceStatus,
)
from uc05.domain.models import (
    ExchangeRecord,
    FourPartAnswer,
    InteractionLogRecord,
    LearnerContext,
    LearnerMessage,
    ModeState,
    utcnow,
)
from uc05.domain.normalisation import clauses, content_tokens, flatten, stem
from uc05.domain.profiles import (
    EXPLANATION_PROFILE_BY_LEVEL,
    coerce_naric_level,
    explanation_profile_for,
)
from uc05.domain.topics import GENERAL_TAG, derive_topic_tag

# --------------------------------------------------------------------------
# Explanation profiles
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level,profile",
    [
        (NaricLevel.LEVEL_3, ExplanationProfile.BASIC),
        (NaricLevel.LEVEL_4, ExplanationProfile.BASIC),
        (NaricLevel.LEVEL_5, ExplanationProfile.INTERMEDIATE),
        (NaricLevel.LEVEL_6, ExplanationProfile.INTERMEDIATE),
        (NaricLevel.LEVEL_7, ExplanationProfile.ADVANCED),
        (NaricLevel.LEVEL_7_PLUS, ExplanationProfile.ADVANCED),
    ],
)
def test_the_profile_mapping_is_exactly_as_specified(level, profile):
    assert explanation_profile_for(level) is profile


def test_every_level_has_a_profile():
    assert set(EXPLANATION_PROFILE_BY_LEVEL) == set(NaricLevel)


def test_level_6_is_deliberately_not_advanced():
    """An undergraduate law degree is not Masters level."""
    assert explanation_profile_for(NaricLevel.LEVEL_6) is not ExplanationProfile.ADVANCED


# --------------------------------------------------------------------------
# Level coercion
# --------------------------------------------------------------------------


def test_a_valid_level_string_is_retrieved():
    level, source, status = coerce_naric_level("LEVEL_7")
    assert (level, source, status) == (
        NaricLevel.LEVEL_7,
        NaricLevelSource.RETRIEVED,
        SourceStatus.AVAILABLE,
    )


@pytest.mark.parametrize("raw", ["LEVEL_8", 6, "undergraduate", "RQF Level 6", ""])
def test_an_unmappable_value_is_invalid_and_defaults(raw):
    level, source, status = coerce_naric_level(raw)
    assert level is NaricLevel.LEVEL_5
    assert source is NaricLevelSource.DEFAULT
    assert status is SourceStatus.INVALID


def test_a_missing_value_is_empty_not_invalid():
    _, _, status = coerce_naric_level(None)
    assert status is SourceStatus.EMPTY


def test_coercion_never_raises_and_never_invents_a_level():
    for raw in (object(), [], {}, 3.5):
        level, _, status = coerce_naric_level(raw)
        assert level in NaricLevel
        assert status is SourceStatus.INVALID


# --------------------------------------------------------------------------
# Topic tags
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,tag",
    [
        ("When is a contract formed?", "contract"),
        ("What is the duty of care in negligence?", "tort"),
        ("Is this an unfair dismissal?", "employment"),
        ("Does the easement bind a successor?", "land"),
        ("What is the mens rea for theft?", "crime"),
        ("How is a beneficiary protected against a trustee?", "equity"),
        ("What happens on a judicial review of that decision?", "public"),
        ("Is that hearsay admissible?", "evidence"),
        ("How does the sun rise?", GENERAL_TAG),
    ],
)
def test_topic_tags_are_derived_deterministically(question, tag):
    assert derive_topic_tag(question) == tag
    assert derive_topic_tag(question) == derive_topic_tag(question)


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


def test_flatten_removes_case_punctuation_and_accents():
    assert flatten("  Don't  -- CAFÉ, really?! ") == "dont cafe really"


def test_flatten_handles_empty_input():
    assert flatten("") == ""
    assert flatten(None) == ""


@pytest.mark.parametrize(
    "word,expected",
    [("requires", "requir"), ("required", "requir"), ("require", "requir")],
)
def test_stemming_collapses_a_word_family(word, expected):
    assert stem(word) == expected


def test_question_frame_words_are_not_content():
    assert content_tokens("What do you think might happen?") == frozenset()


def test_clause_splitting_uses_the_raw_punctuation():
    assert clauses("I give up, I don't know.") == ["i give up", "i dont know"]
    assert clauses("") == []


# --------------------------------------------------------------------------
# Model invariants
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "plain_english_explanation",
        "formal_legal_definition",
        "practical_example",
        "authority_reference",
    ],
)
def test_a_four_part_answer_refuses_a_blank_part(field):
    parts = {
        "plain_english_explanation": "a",
        "formal_legal_definition": "b",
        "practical_example": "c",
        "authority_reference": "d",
    }
    parts[field] = "   "
    with pytest.raises(ValidationError):
        FourPartAnswer(**parts)


def test_a_four_part_answer_refuses_a_missing_part():
    with pytest.raises(ValidationError):
        FourPartAnswer(
            plain_english_explanation="a",
            formal_legal_definition="b",
            practical_example="c",
        )


def test_domain_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        LearnerContext(
            naric_level=NaricLevel.LEVEL_5,
            naric_level_source=NaricLevelSource.DEFAULT,
            upstream_extra="smuggled",
        )


def test_the_defaulted_context_is_level_5_with_recorded_status():
    context = LearnerContext.defaulted(SourceStatus.UNAVAILABLE)
    assert context.naric_level is NaricLevel.LEVEL_5
    assert context.naric_level_source is NaricLevelSource.DEFAULT
    assert context.practice_area is None
    assert context.source_status["naric_level"] is SourceStatus.UNAVAILABLE
    assert context.explanation_profile is ExplanationProfile.INTERMEDIATE


def test_the_default_mode_state_is_off_and_unowned():
    state = ModeState.default_for("s1")
    assert state.enabled is False
    assert state.owner_user_id is None
    assert state.updated_at is None


def test_an_exchange_reports_the_answering_message():
    now = utcnow()
    exchange = ExchangeRecord(
        exchange_number=1,
        guiding_question="Which element?",
        probing_focus="the element",
        question_fingerprint="element",
        asked_at=now,
        learner_messages=[
            LearnerMessage(
                text="just tell me",
                intent=IntentKind.DIRECT_ANSWER_REQUEST,
                received_at=now,
            ),
            LearnerMessage(
                text="actually, I think it is consideration",
                intent=IntentKind.SUBSTANTIVE_RESPONSE,
                received_at=now,
            ),
        ],
    )
    assert exchange.learner_response == "actually, I think it is consideration"
    assert exchange.responded_at == now


def test_an_unanswered_exchange_reports_no_response():
    exchange = ExchangeRecord(
        exchange_number=1,
        guiding_question="Which element?",
        probing_focus="the element",
        question_fingerprint="element",
        asked_at=utcnow(),
    )
    assert exchange.learner_response is None
    assert exchange.responded_at is None


# --------------------------------------------------------------------------
# The interaction log record's `mode` field. Integration brief §4.2 instructs
# that it stop being the fixed literal "socratic" and become a closed enum.
# A closed enum earns its keep by rejecting what is not in it, so that is what
# these pin - a Literal would have passed the first two and failed the third.
# --------------------------------------------------------------------------


def _log_record(**overrides):
    fields = dict(
        interaction_id="int-1",
        session_id="sess-1",
        user_id="user-1",
        asked_at=utcnow(),
        question_text="What is consideration?",
        topic_tag="contract",
        naric_level=NaricLevel.LEVEL_5,
        response_id="resp-1",
        dialogue_id="dlg-1",
        exchange_number=1,
        response_kind=ResponseKind.GUIDING_QUESTION,
    )
    fields.update(overrides)
    return InteractionLogRecord(**fields)


def test_mode_defaults_to_socratic_and_keeps_its_wire_value():
    """UC-05 writes records only for Socratic turns, and the wire value is
    unchanged by the move from a literal to an enum."""
    record = _log_record()
    assert record.mode is Mode.SOCRATIC
    assert record.model_dump(mode="json")["mode"] == "socratic"


def test_mode_rejects_a_value_outside_the_closed_set():
    with pytest.raises(ValidationError):
        _log_record(mode="tutorial")


def test_mode_admits_the_three_session_types_the_platform_defines():
    """UC-05 never writes these, but the field is the platform's, not UC-05's:
    the enum is the shape a shared interaction store needs (§4.3)."""
    for value in ("free_form", "course_linked", "case_linked"):
        assert _log_record(mode=value).mode.value == value
