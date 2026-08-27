"""Internal domain models.

These are the shapes UC-04's business logic reasons over. Nothing here is provider-shaped: an
adapter is the only place an upstream payload is known, and it maps that payload onto these
models at the boundary.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    DEFAULT_NARIC_LEVEL,
    ExplanationProfile,
    FramingStrategy,
    Grounding,
    NARIC_LEVEL_PROFILE,
    NaricLevel,
    NaricLevelSource,
    QuestionClass,
    RatingState,
    ResponseAction,
    ResponseStatus,
    SectionRefStatus,
    SourceStatus,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- lesson content


class QuizItem(_Frozen):
    """A quiz item carried by the lesson.

    ``correct_option_id`` is loaded so known-item matching can recognise the question. It is
    never rendered, never logged and never leaves the service.
    """

    quiz_item_id: str
    question_text: str
    option_ids: tuple[str, ...] = ()
    correct_option_id: str | None = None
    concept_tag: str | None = None


class LessonSection(_Frozen):
    section_id: str
    title: str
    #: Full section prose. Used for matching only - never emitted verbatim (see the
    #: extraction budget in core.extraction).
    body: str = ""
    #: Curated bullets. These are the only prose the generator may quote, and only within budget.
    key_points: tuple[str, ...] = ()
    concept_tags: tuple[str, ...] = ()
    order: int = 0


class LessonConcept(_Frozen):
    concept_tag: str
    name: str
    #: One-sentence definition supplied by the lesson.
    summary: str = ""
    section_id: str = ""
    keywords: tuple[str, ...] = ()


class LessonContent(_Frozen):
    course_id: str
    lesson_id: str
    title: str
    sections: tuple[LessonSection, ...] = ()
    concepts: tuple[LessonConcept, ...] = ()
    quiz_items: tuple[QuizItem, ...] = ()
    revision: str | None = None

    @property
    def has_quiz_items(self) -> bool:
        return len(self.quiz_items) > 0


class CourseLessonRef(_Frozen):
    lesson_id: str
    title: str
    order: int = 0


class CourseStructure(_Frozen):
    """The lessons that really exist in this course. The whitelist for cross-referencing."""

    course_id: str
    title: str
    lessons: tuple[CourseLessonRef, ...] = ()

    def contains(self, lesson_id: str) -> bool:
        return any(lesson.lesson_id == lesson_id for lesson in self.lessons)

    def find(self, lesson_id: str) -> CourseLessonRef | None:
        for lesson in self.lessons:
            if lesson.lesson_id == lesson_id:
                return lesson
        return None


class EnrolmentRecord(_Frozen):
    user_id: str
    course_id: str
    enrolled: bool
    #: Provider-side detail for logging only, e.g. "lapsed". Never returned to a client.
    reason: str | None = None


# ------------------------------------------------------------------------- learner context


class LearnerContext(_Frozen):
    """Assembled elsewhere on the platform; UC-04 only receives it.

    ``naric_level`` is always populated - when nothing usable arrived, it carries
    ``DEFAULT_NARIC_LEVEL`` and ``naric_level_source`` is ``default``.
    """

    user_id: str
    naric_level: NaricLevel = DEFAULT_NARIC_LEVEL
    naric_level_source: NaricLevelSource = NaricLevelSource.DEFAULT
    practice_area: str | None = None
    source_status: SourceStatus = SourceStatus.AVAILABLE

    @property
    def explanation_profile(self) -> ExplanationProfile:
        return NARIC_LEVEL_PROFILE[self.naric_level]


def default_learner_context(user_id: str, status: SourceStatus) -> LearnerContext:
    """The documented fallback: Level 5, marked ``default``, question still answered."""
    return LearnerContext(
        user_id=user_id,
        naric_level=DEFAULT_NARIC_LEVEL,
        naric_level_source=NaricLevelSource.DEFAULT,
        practice_area=None,
        source_status=status,
    )


# ------------------------------------------------------------------------------ generation


class CrossLessonRef(_Frozen):
    """A pointer to another lesson. Unverified until checked against the course structure."""

    lesson_id: str
    title: str
    reason: str = ""


class GenerationRequest(_Frozen):
    question: str
    profile: ExplanationProfile
    framing: FramingStrategy
    grounding: Grounding
    lesson_title: str | None = None
    course_title: str | None = None
    section: LessonSection | None = None
    concept: LessonConcept | None = None
    #: Spans the service permits the generator to quote, already inside the extraction budget.
    quotable_spans: tuple[str, ...] = ()
    #: True when the budget is spent - the generator must work without new source material.
    budget_exhausted: bool = False
    #: True when the question was flagged as answer-seeking. The generator must not echo the
    #: learner's wording back: repeating "the correct option" or an injected instruction is
    #: both a poor answer and a way for crafted text to reappear in output.
    suppress_question_echo: bool = False
    candidate_cross_lesson_refs: tuple[CrossLessonRef, ...] = ()
    prompt_id: str = ""
    prompt_version: str = ""


class GenerationResult(_Frozen):
    """Structure, not prose. Validated on return; a malformed shape is ProviderInvalidResponse."""

    explanation: str
    section_id: str | None = None
    concept_tag: str | None = None
    cross_lesson_refs: tuple[CrossLessonRef, ...] = ()
    framing_used: FramingStrategy


# ----------------------------------------------------------------------- quiz protection


class QuizIntentResult(_Frozen):
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    #: Named signals for audit and tuning. Never contains lesson content.
    signals: tuple[str, ...] = ()
    classifier: str = ""


class KnownItemMatch(_Frozen):
    quiz_item_id: str
    score: float
    concept_tag: str | None = None


class QuizAssessment(_Frozen):
    """The combined verdict of the two independent signals."""

    intent_detected: bool
    detection_confirmed: bool | None
    known_item: KnownItemMatch | None
    intent: QuizIntentResult
    suspected_false_positive: bool


# ------------------------------------------------------------------------------- tagging


class ConceptTag(_Frozen):
    """Closed-vocabulary tags. ``unclassified`` when nothing matched."""

    concept_tag: str
    topic_tag: str
    matched: bool


# ------------------------------------------------------------- interaction log (published)


class InteractionRecord(BaseModel):
    """The published interaction log record.

    Field names and order follow the platform contract exactly. ``question_text`` is part of
    the contract shape but UC-04 does not persist the learner's words: see
    ``core.privacy.redact_question`` - the field carries a redaction marker, never the question.
    """

    model_config = ConfigDict(extra="forbid")

    interaction_id: str
    session_id: str
    user_id: str
    asked_at: datetime
    question_text: str | None
    topic_tag: str
    question_class: QuestionClass
    naric_level: NaricLevel
    response_id: str
    course_id: str
    lesson_id: str
    lesson_section_id: str | None
    concept_tag: str
    grounding: Grounding
    quiz_intent_detected: bool
    quiz_detection_confirmed: bool | None
    framing_used: FramingStrategy | None
    explain_differently_count: int
    follow_up_of: str | None
    rating_state: RatingState = RatingState.PENDING


class FalsePositiveRecord(BaseModel):
    """Suspected quiz-detection false positives, kept for tuning.

    Not part of the platform contract - documented in SHARED_CONTRACT.md so it is known.
    Carries no lesson content and no question text.
    """

    model_config = ConfigDict(extra="forbid")

    record_id: str
    interaction_id: str
    session_id: str
    user_id: str
    recorded_at: datetime
    classifier_label: str
    classifier_confidence: float
    classifier_signals: tuple[str, ...]
    known_item_matched: bool
    concept_tag: str
    #: Always true: a false positive still receives a full explanation.
    explanation_delivered: bool = True


class FramingAttempt(BaseModel):
    """One recorded framing use. Session- and concept-scoped.

    Not part of the platform contract - documented in SHARED_CONTRACT.md.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    concept_tag: str
    framing: FramingStrategy
    #: Stable content fingerprint used to reject paraphrase-only repeats.
    fingerprint: str
    fingerprint_tokens: tuple[str, ...]
    recorded_at: datetime


# ------------------------------------------------------------------------------ response


class SectionReference(_Frozen):
    status: SectionRefStatus
    lesson_section_id: str | None = None


class CoachingResponse(_Frozen):
    """What the API returns. Never carries raw lesson content."""

    status: ResponseStatus
    interaction_id: str
    session_id: str
    course_id: str
    lesson_id: str
    grounding: Grounding
    explanation: str
    section_reference: SectionReference
    concept_tag: str
    topic_tag: str
    framing_used: FramingStrategy | None
    explain_differently_count: int
    cross_lesson_references: tuple[CrossLessonRef, ...] = ()
    actions: tuple[ResponseAction, ...] = ()
    #: Learner-visible notice, e.g. that the lesson could not be accessed.
    notice: str | None = None
    naric_level: NaricLevel = DEFAULT_NARIC_LEVEL
    naric_level_source: NaricLevelSource = NaricLevelSource.DEFAULT
    explanation_profile: ExplanationProfile = ExplanationProfile.INTERMEDIATE
    quiz_intent_detected: bool = False
    #: Per-dependency status. Keys are dependency names, values the source-status vocabulary.
    source_status: dict[str, SourceStatus] = Field(default_factory=dict)
    rating_state: RatingState = RatingState.PENDING
