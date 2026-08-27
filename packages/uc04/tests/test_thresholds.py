"""Threshold tuning evidence.

Every tuned constant is pinned here against the boundary case that fixes it. This is what makes
"re-tuned, not copied" checkable: if tokenisation changes and a boundary moves, these fail
loudly instead of the behaviour drifting quietly.

Each test names the value on both sides of the boundary, so a future change can see what the
constant is actually buying.
"""

from __future__ import annotations

from uc04.adapters.mock import fixtures as fx
from uc04.adapters.mock.quiz_intent import HeuristicQuizIntentClassifier
from uc04.core.fingerprint import fingerprint, is_repeat
from uc04.core.quiz_protection import KnownItemMatcher
from uc04.core.section_matcher import SectionMatcher
from uc04.core.text import content_tokens, jaccard
from uc04.core.thresholds import (
    MAX_QUOTED_SPAN_WORDS,
    MAX_QUOTED_SPANS_PER_CONCEPT,
    MAX_QUOTED_SPANS_PER_RESPONSE,
    PARAPHRASE_SIMILARITY_THRESHOLD,
    QUIZ_INTENT_AMBIGUOUS_THRESHOLD,
    QUIZ_INTENT_BLOCK_THRESHOLD,
    QUIZ_TOPIC_MIN_SCORE,
    SECTION_MATCH_THRESHOLD,
)
from uc04.domain.enums import FramingStrategy, QuizIntentLabel
from uc04.domain.models import FramingAttempt

LESSON = fx.LESSONS[fx.LESSON_HEARSAY]


# ------------------------------------------------------------------- section matching


def test_section_match_threshold_separates_on_topic_from_off_topic() -> None:
    matcher = SectionMatcher()

    on_topic = matcher.match("What does hearsay actually mean?", LESSON)
    assert on_topic.best is not None
    assert on_topic.best.score >= SECTION_MATCH_THRESHOLD

    off_topic = matcher.match("How do I renew my practising certificate?", LESSON)
    assert off_topic.best is None
    top = off_topic.ranked[0].score if off_topic.ranked else 0.0
    assert top < SECTION_MATCH_THRESHOLD, (
        f"off-topic question scored {top}, threshold is {SECTION_MATCH_THRESHOLD}"
    )


def test_the_matcher_is_deterministic_across_runs() -> None:
    matcher = SectionMatcher()
    a = matcher.match("What is a hearsay exception?", LESSON)
    b = matcher.match("What is a hearsay exception?", LESSON)
    assert a.best is not None and b.best is not None
    assert a.best.section.section_id == b.best.section.section_id
    assert a.best.score == b.best.score


def test_quiz_topic_min_score_is_below_the_normal_threshold() -> None:
    """A quiz question is padded with scaffolding, which drags its topical score down."""
    assert QUIZ_TOPIC_MIN_SCORE < SECTION_MATCH_THRESHOLD


# ------------------------------------------------------------------ paraphrase threshold


def _history(text: str) -> list[FramingAttempt]:
    from datetime import UTC, datetime

    fp = fingerprint(text)
    return [
        FramingAttempt(
            session_id="s",
            concept_tag="hearsay",
            framing=FramingStrategy.FIRST_PRINCIPLES,
            fingerprint=fp.value,
            fingerprint_tokens=fp.tokens,
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    ]


def test_paraphrase_threshold_catches_a_reword_and_spares_a_new_framing() -> None:
    original = (
        "Hearsay is an out-of-court statement offered for the truth of its contents, and the "
        "rule exists because the maker cannot be cross-examined."
    )
    history = _history(original)

    reworded = (
        "Hearsay means a statement made out of court and offered for the truth of the contents; "
        "the maker cannot be cross-examined on it."
    )
    reword_similarity = jaccard(fingerprint(reworded).tokens, history[0].fingerprint_tokens)
    assert is_repeat(fingerprint(reworded), history).is_repeat, (
        f"reword similarity {reword_similarity} did not reach {PARAPHRASE_SIMILARITY_THRESHOLD}"
    )

    genuinely_different = (
        "Picture a filter on a water supply: it does not judge the water, it stops what cannot "
        "be checked from reaching the tap. That is the job this doctrine does for testimony."
    )
    different_similarity = jaccard(
        fingerprint(genuinely_different).tokens, history[0].fingerprint_tokens
    )
    assert not is_repeat(fingerprint(genuinely_different), history).is_repeat
    assert different_similarity < PARAPHRASE_SIMILARITY_THRESHOLD

    # The threshold sits between the two, with headroom on each side. Both edges are pinned so
    # a tokeniser change that moves either one fails here rather than drifting silently.
    assert different_similarity < PARAPHRASE_SIMILARITY_THRESHOLD <= reword_similarity
    assert round(reword_similarity, 3) == 0.733
    assert round(different_similarity, 3) == 0.040


def test_the_real_framings_are_not_mistaken_for_paraphrases_of_each_other() -> None:
    """The threshold must not be so tight that legitimate framings collide."""
    from uc04.adapters.generators.fake import FakeAnswerGenerator
    from uc04.domain.enums import ExplanationProfile, Grounding
    from uc04.domain.models import GenerationRequest

    generator = FakeAnswerGenerator()
    section = LESSON.sections[0]
    concept = LESSON.concepts[0]
    seen: list[FramingAttempt] = []
    highest = 0.0
    for framing in FramingStrategy:
        result = generator.generate(
            GenerationRequest(
                question="what is hearsay",
                profile=ExplanationProfile.INTERMEDIATE,
                framing=framing,
                grounding=Grounding.LESSON,
                section=section,
                concept=concept,
                quotable_spans=(concept.summary,),
            )
        )
        fp = fingerprint(result.explanation)
        verdict = is_repeat(fp, seen)
        assert not verdict.is_repeat, (
            f"{framing.value} was judged a repeat (similarity {verdict.similarity})"
        )
        highest = max(highest, verdict.similarity)
        seen.extend(_history(result.explanation))

    # The measured ceiling for legitimate framings, and the margin the threshold keeps above it.
    assert highest <= 0.52
    assert PARAPHRASE_SIMILARITY_THRESHOLD - highest >= 0.12


# --------------------------------------------------------------- quiz intent bands


def test_the_block_threshold_separates_answer_seeking_from_learning() -> None:
    classifier = HeuristicQuizIntentClassifier()
    blocked = classifier.classify("Which option is correct?", None)
    learning = classifier.classify("Why does hearsay turn on the purpose of the statement?", None)
    assert blocked.label == QuizIntentLabel.QUIZ_ANSWER_REQUEST.value
    assert learning.label == QuizIntentLabel.CONCEPT_LEARNING_REQUEST.value
    assert QUIZ_INTENT_AMBIGUOUS_THRESHOLD < QUIZ_INTENT_BLOCK_THRESHOLD


def test_the_ambiguous_band_catches_a_genuinely_ambiguous_question() -> None:
    """Moved from the reference value: at 0.28 this landed as plain learning."""
    classifier = HeuristicQuizIntentClassifier()
    result = classifier.classify("Which one should I pick?", None)
    assert result.label == QuizIntentLabel.AMBIGUOUS.value


def test_the_ambiguous_band_does_not_swallow_clean_concept_questions() -> None:
    classifier = HeuristicQuizIntentClassifier()
    for question in (
        "What does hearsay mean?",
        "Explain the balancing exercise for litigation privilege.",
        "Help me understand standard disclosure.",
    ):
        assert (
            classifier.classify(question, None).label
            == QuizIntentLabel.CONCEPT_LEARNING_REQUEST.value
        ), question


# ------------------------------------------------------------------- known-item match


def test_quiz_match_threshold_recognises_the_stem_and_rejects_a_near_topic() -> None:
    matcher = KnownItemMatcher(threshold=0.85)
    stem = LESSON.quiz_items[0].question_text

    assert matcher.match(stem, LESSON) is not None

    # Same topic, not the item: must not match, or every hearsay question becomes a quiz.
    similarity = jaccard(
        content_tokens("What is an exception to the rule against hearsay?"),
        content_tokens(stem),
    )
    assert matcher.match("What is an exception to the rule against hearsay?", LESSON) is None
    assert similarity < 0.85


def test_a_lower_threshold_would_over_match_which_is_why_it_is_high() -> None:
    loose = KnownItemMatcher(threshold=0.30)
    assert loose.match("What is an exception to the rule against hearsay?", LESSON) is not None


# --------------------------------------------------------------- extraction budget


def test_the_extraction_budget_constants_are_internally_consistent() -> None:
    assert MAX_QUOTED_SPANS_PER_RESPONSE <= MAX_QUOTED_SPANS_PER_CONCEPT
    assert MAX_QUOTED_SPAN_WORDS > 0
    # The budget must actually bind on the fixture, or the extraction tests prove nothing.
    section = LESSON.sections[0]
    assert len(section.key_points) > MAX_QUOTED_SPANS_PER_CONCEPT
