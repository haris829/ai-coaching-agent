"""Shared vocabulary for UC-04.

Casing rule applied throughout:

* Values **specified by the platform contract** keep exactly the form the contract gives them.
  That means the NARIC levels are emitted as ``LEVEL_3`` .. ``LEVEL_7_PLUS`` (the contract names
  those tokens literally), while ``grounding``, ``naric_level_source``, ``rating_state`` and the
  source-status vocabulary are emitted lowercase, as the contract writes them.
* Values **invented here** are emitted lowercase.

Python member names are upper-case by convention; the emitted ``value`` is what matters.
"""

from __future__ import annotations

from enum import Enum


class NaricLevel(str, Enum):
    """Closed platform enum. Never an integer scale."""

    LEVEL_3 = "LEVEL_3"
    LEVEL_4 = "LEVEL_4"
    LEVEL_5 = "LEVEL_5"
    LEVEL_6 = "LEVEL_6"
    LEVEL_7 = "LEVEL_7"
    LEVEL_7_PLUS = "LEVEL_7_PLUS"


#: Applied whenever a level is missing, unusable, or the context provider is down.
DEFAULT_NARIC_LEVEL = NaricLevel.LEVEL_5


class NaricLevelSource(str, Enum):
    """Provenance of the level actually used. A fallback is never reported as retrieved."""

    RETRIEVED = "retrieved"
    DEFAULT = "default"


class ExplanationProfile(str, Enum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


#: Platform-specified mapping. LEVEL_6 is an undergraduate law degree and maps to
#: intermediate, NOT advanced.
NARIC_LEVEL_PROFILE: dict[NaricLevel, ExplanationProfile] = {
    NaricLevel.LEVEL_3: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_4: ExplanationProfile.BASIC,
    NaricLevel.LEVEL_5: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_6: ExplanationProfile.INTERMEDIATE,
    NaricLevel.LEVEL_7: ExplanationProfile.ADVANCED,
    NaricLevel.LEVEL_7_PLUS: ExplanationProfile.ADVANCED,
}


class Grounding(str, Enum):
    """Where the substance of an answer came from. Two values, per the platform contract."""

    LESSON = "lesson"
    GENERAL_KNOWLEDGE = "general_knowledge"


class SourceStatus(str, Enum):
    """Per-dependency status. ``empty`` and ``unavailable`` are different states."""

    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class RatingState(str, Enum):
    PENDING = "pending"
    RATED = "rated"


class FramingStrategy(str, Enum):
    """The closed set of ways an explanation can be approached.

    Selection is deterministic and unused-only: once all six are spent for a concept in a
    session, the set is exhausted and UC-04 says so. It never cycles back.
    """

    ANALOGY = "analogy"
    WORKED_EXAMPLE = "worked_example"
    CONTRAST_NEAR_MISS = "contrast_near_miss"
    FIRST_PRINCIPLES = "first_principles"
    PROCEDURAL_WALKTHROUGH = "procedural_walkthrough"
    MISCONCEPTION_CORRECTION = "misconception_correction"


#: Deterministic preference order for framing selection.
FRAMING_ORDER: tuple[FramingStrategy, ...] = (
    FramingStrategy.FIRST_PRINCIPLES,
    FramingStrategy.ANALOGY,
    FramingStrategy.WORKED_EXAMPLE,
    FramingStrategy.CONTRAST_NEAR_MISS,
    FramingStrategy.PROCEDURAL_WALKTHROUGH,
    FramingStrategy.MISCONCEPTION_CORRECTION,
)


class QuestionClass(str, Enum):
    """How UC-04 read the question. Recorded on the interaction record."""

    CONCEPT_EXPLANATION = "concept_explanation"
    QUIZ_ANSWER_SEEKING = "quiz_answer_seeking"
    OUT_OF_LESSON = "out_of_lesson"
    AMBIGUOUS = "ambiguous"


class SectionRefStatus(str, Enum):
    """Whether a lesson section could be resolved. Never guessed."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class ResponseStatus(str, Enum):
    """Turn outcome. UC-04's own vocabulary, distinct from SourceStatus."""

    ANSWERED = "answered"
    FRAMINGS_EXHAUSTED = "framings_exhausted"


class FollowUpAction(str, Enum):
    EXPLAIN_DIFFERENTLY = "explain_differently"
    GO_DEEPER = "go_deeper"


class ResponseAction(str, Enum):
    """Structured affordances handed to the caller. UC-04 never executes them."""

    EXPLAIN_DIFFERENTLY = "explain_differently"
    GO_DEEPER = "go_deeper"
    START_FREE_FORM_SESSION = "start_free_form_session"


class QuizIntentLabel(str, Enum):
    QUIZ_ANSWER_REQUEST = "quiz_answer_request"
    CONCEPT_LEARNING_REQUEST = "concept_learning_request"
    AMBIGUOUS = "ambiguous"


#: Tag applied when a question matches no entry in a closed vocabulary.
UNCLASSIFIED = "unclassified"
