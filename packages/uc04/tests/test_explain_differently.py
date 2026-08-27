"""Explain differently: never the same approach twice, never a silent cycle."""

from __future__ import annotations

from conftest import IN_LESSON_QUESTION, SECOND_CONCEPT_QUESTION, build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.domain.enums import FRAMING_ORDER, FramingStrategy, ResponseAction, ResponseStatus
from uc04.domain.models import GenerationResult


def test_each_request_selects_a_different_strategy(harness) -> None:
    first = harness.ask(IN_LESSON_QUESTION)
    second = harness.explain_differently(first)
    third = harness.explain_differently(second)

    framings = [first.framing_used, second.framing_used, third.framing_used]
    assert len(set(framings)) == 3
    assert all(f is not None for f in framings)


def test_no_strategy_repeats_for_a_concept_within_a_session(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    seen = [response.framing_used]
    for _ in range(len(FRAMING_ORDER) - 1):
        response = harness.explain_differently(response)
        seen.append(response.framing_used)
    assert len(set(seen)) == len(FRAMING_ORDER)
    assert set(seen) == set(FRAMING_ORDER)


def test_the_texts_themselves_are_all_different(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    texts = [response.explanation]
    for _ in range(len(FRAMING_ORDER) - 1):
        response = harness.explain_differently(response)
        texts.append(response.explanation)
    assert len(set(texts)) == len(texts)


def test_exhaustion_is_stated_honestly_and_never_cycles(harness) -> None:
    """The sixth strategy is the last. There is no seventh, and no quiet return to the first."""
    response = harness.ask(IN_LESSON_QUESTION)
    for _ in range(len(FRAMING_ORDER) - 1):
        response = harness.explain_differently(response)
    assert response.status is ResponseStatus.ANSWERED

    exhausted = harness.explain_differently(response)
    assert exhausted.status is ResponseStatus.FRAMINGS_EXHAUSTED
    assert exhausted.framing_used is None
    assert "every angle" in exhausted.explanation.lower()
    assert ResponseAction.EXPLAIN_DIFFERENTLY not in exhausted.actions
    assert ResponseAction.GO_DEEPER in exhausted.actions


def test_exhaustion_is_stable_when_asked_again(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    for _ in range(len(FRAMING_ORDER) + 3):
        response = harness.explain_differently(response)
    assert response.status is ResponseStatus.FRAMINGS_EXHAUSTED
    assert response.framing_used is None


def test_a_paraphrase_only_generator_response_is_rejected() -> None:
    """A paraphrase is a repeat. Rewording the same content does not count as a new framing."""

    class ParaphrasingGenerator:
        """Returns the same substance every time, lightly reworded."""

        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request):  # noqa: ANN001, ANN201
            self.calls += 1
            orderings = [
                "Hearsay is an out-of-court statement offered for the truth of its contents.",
                "An out-of-court statement offered for the truth of its contents is hearsay.",
                "For the truth of its contents, an out-of-court statement offered is hearsay.",
                "Offered for the truth of its contents: an out-of-court statement, hearsay.",
                "Truth of its contents - hearsay is that out-of-court statement, offered.",
                "Its contents offered for truth, an out-of-court statement is hearsay.",
            ]
            return GenerationResult(
                explanation=orderings[(self.calls - 1) % len(orderings)],
                section_id=request.section.section_id if request.section else None,
                concept_tag=request.concept.concept_tag if request.concept else None,
                cross_lesson_refs=(),
                framing_used=request.framing,
            )

    harness = build_harness(generator=ParaphrasingGenerator())
    first = harness.ask(IN_LESSON_QUESTION)
    second = harness.explain_differently(first)

    # Every remaining framing produced a restatement, so exhaustion is reported rather than
    # a reworded repeat being passed off as a new explanation.
    assert second.status is ResponseStatus.FRAMINGS_EXHAUSTED
    assert second.explanation != first.explanation
    assert "restatement" in second.explanation.lower()


def test_history_is_scoped_to_the_session(harness) -> None:
    first = harness.ask(IN_LESSON_QUESTION, session_id=fx.SESSION_MAIN)
    harness.explain_differently(first)

    # A different session for the same learner and concept starts clean.
    fresh = harness.ask(IN_LESSON_QUESTION, session_id=fx.SESSION_SECOND)
    assert fresh.framing_used is FRAMING_ORDER[0]
    assert fresh.explain_differently_count == 0


def test_history_is_scoped_to_the_concept(harness) -> None:
    first = harness.ask(IN_LESSON_QUESTION)
    harness.explain_differently(first)

    other = harness.ask(SECOND_CONCEPT_QUESTION)
    assert other.concept_tag != first.concept_tag
    assert other.framing_used is FRAMING_ORDER[0], "a different concept starts from the top"


def test_explain_differently_count_increments_and_is_recorded(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    assert response.explain_differently_count == 0

    counts = []
    for _ in range(3):
        response = harness.explain_differently(response)
        counts.append(response.explain_differently_count)
    assert counts == [1, 2, 3]

    record = harness.interactions.get(response.interaction_id)
    assert record.explain_differently_count == 3


def test_the_difficulty_signal_is_recorded_against_the_concept(harness) -> None:
    """The count is a signal, recorded per concept - not a conclusion about the learner."""
    response = harness.ask(IN_LESSON_QUESTION)
    response = harness.explain_differently(response)
    record = harness.interactions.get(response.interaction_id)
    assert record.concept_tag == "hearsay"
    assert record.lesson_id == fx.LESSON_HEARSAY
    assert record.session_id == fx.SESSION_MAIN
    assert record.explain_differently_count == 1


def test_a_follow_up_is_linked_to_the_interaction_it_followed(harness) -> None:
    first = harness.ask(IN_LESSON_QUESTION)
    second = harness.explain_differently(first)
    record = harness.interactions.get(second.interaction_id)
    assert record.follow_up_of == first.interaction_id


def test_the_registry_not_the_generator_tracks_what_was_used(harness) -> None:
    """The registry is the source of truth, deterministic and inspectable."""
    first = harness.ask(IN_LESSON_QUESTION)
    harness.explain_differently(first)
    attempts = harness.framings.used_framings(fx.SESSION_MAIN, "hearsay")
    assert [a.framing for a in attempts] == [FRAMING_ORDER[0], FRAMING_ORDER[1]]
    assert all(a.fingerprint for a in attempts)


def test_go_deeper_does_not_spend_a_framing(harness) -> None:
    """Depth and approach are separate axes."""
    first = harness.ask(IN_LESSON_QUESTION)
    deeper = harness.go_deeper(first)
    assert deeper.explanation_profile.value == "advanced"
    attempts_after = harness.framings.used_framings(fx.SESSION_MAIN, "hearsay")
    # One attempt for the original answer, one for the deeper rendering, and the framing set
    # is not exhausted by asking for depth.
    assert len(attempts_after) == 2
    assert deeper.status is ResponseStatus.ANSWERED


def test_framing_strategies_are_the_documented_closed_set() -> None:
    assert set(FRAMING_ORDER) == set(FramingStrategy)
    assert len(FRAMING_ORDER) == 6


def test_the_paraphrase_detector_has_a_stated_lexical_limit() -> None:
    """Where the lexical net ends, and what actually guarantees the approach changed.

    Fingerprinting is a *secondary* net. It reliably catches reordering and light rewording,
    which is what a generator ignoring its instructions produces. It does NOT catch a heavy
    reword that swaps in new vocabulary - lexical similarity cannot, and pretending otherwise
    would be worse than saying so.

    The primary guarantee is structural: the framing registry hands out each strategy once, so
    the approach changes whether or not the wording does. Recorded as A-01.
    """
    from uc04.core.fingerprint import fingerprint, is_repeat
    from uc04.core.thresholds import PARAPHRASE_SIMILARITY_THRESHOLD
    from uc04.domain.models import FramingAttempt
    from datetime import UTC, datetime

    original = "Hearsay is an out-of-court statement offered for the truth of its contents."
    fp = fingerprint(original)
    history = [
        FramingAttempt(
            session_id="s",
            concept_tag="hearsay",
            framing=FRAMING_ORDER[0],
            fingerprint=fp.value,
            fingerprint_tokens=fp.tokens,
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    ]

    # Caught: a reordering.
    reordered = fingerprint("An out-of-court statement offered for the truth of its contents is hearsay.")
    assert is_repeat(reordered, history).is_repeat
    assert is_repeat(reordered, history).reason == "exact"

    # Caught: light rewording that keeps most of the vocabulary.
    light = fingerprint("Hearsay is a statement made out-of-court and offered for the truth of contents.")
    assert is_repeat(light, history).is_repeat

    # NOT caught lexically: heavy rewording with fresh vocabulary.
    heavy = fingerprint("The contents' veracity being the purpose, a remark made elsewhere qualifies.")
    verdict = is_repeat(heavy, history)
    assert not verdict.is_repeat
    assert verdict.similarity < PARAPHRASE_SIMILARITY_THRESHOLD
