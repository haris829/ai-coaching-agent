"""The deterministic question bank behind the fake guiding generator.

Two properties are load-bearing for the tests:

*   Consecutive questions are **semantically distant** -- their normalised
    token sets overlap well below ``LOOP_SIMILARITY_THRESHOLD`` -- so a normal
    five-exchange dialogue never trips loop detection by accident.
*   Each question has a matching ``reworded`` variant that is *close* to it,
    above the threshold but not identical, so a reworded repeat is a real test
    of the similarity comparison rather than of string equality.

``tests/test_loop_protection.py`` asserts both properties numerically, so the
bank cannot silently drift into either failure mode.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BankEntry:
    question: str
    reworded: str
    probing_focus: str


BANK: tuple[BankEntry, ...] = (
    BankEntry(
        question="What has to be present before an agreement becomes legally binding?",
        reworded=(
            "Before an agreement becomes legally binding, which requirements "
            "must be present?"
        ),
        probing_focus="the constituent elements the rule requires",
    ),
    BankEntry(
        question="Which party carries the burden of proving that element?",
        reworded=(
            "Which party actually carries the burden of proving that element "
            "in practice?"
        ),
        probing_focus="where the evidential burden sits",
    ),
    BankEntry(
        question=(
            "How would the position change if the promise had been recorded in "
            "writing?"
        ),
        reworded=(
            "If the promise had instead been recorded in writing, how would the "
            "position change?"
        ),
        probing_focus="the effect of formality on the outcome",
    ),
    BankEntry(
        question="What remedy would follow if that element turned out to be absent?",
        reworded=(
            "Supposing that element turned out to be absent, what remedy would "
            "follow?"
        ),
        probing_focus="the consequence of the element failing",
    ),
    BankEntry(
        question=(
            "Where does the leading authority draw the line in a comparable "
            "dispute?"
        ),
        reworded=(
            "In a comparable dispute, where exactly does the leading authority "
            "draw the line?"
        ),
        probing_focus="how the authority bounds the rule",
    ),
    BankEntry(
        question="Which facts are doing the decisive work in your reasoning?",
        reworded="In your reasoning, which facts are doing the decisive work?",
        probing_focus="which facts the learner is actually relying on",
    ),
    BankEntry(
        question="What would an opponent argue against the conclusion you reached?",
        reworded=(
            "Against the conclusion you reached, what would an opponent argue?"
        ),
        probing_focus="the strongest counter-argument",
    ),
    BankEntry(
        question="How does the statutory wording constrain that interpretation?",
        reworded=(
            "How does the statutory wording constrain that interpretation in "
            "practice?"
        ),
        probing_focus="the limits the statute places on the reading",
    ),
)


def entry_for(index: int) -> BankEntry:
    """Cycle the bank so a cap of any size still yields a question."""
    return BANK[index % len(BANK)]
