"""A course in, multiple-choice questions out: the prompt, and the parse.

Pure functions and value types only. No database, no HTTP, no model client - which is why the
part most likely to be wrong is also the part cheapest to test.

WHY THE MODEL IS ASKED FOR JSON
-------------------------------
The obvious approach asks in prose and gets prose back: ``Q1. ... A. ... B. ... Answer: B``.
Parsing that is guesswork. Labels drift between ``A.`` and ``a)``, an explanation runs across two
lines, and a question that contains the word "Answer" derails the regular expression looking for
the key. Asking for JSON moves the ambiguity into the model's job, where it is good, and out of a
regular expression, where it is not.

WHAT IS REFUSED MATTERS MORE THAN WHAT IS ACCEPTED
--------------------------------------------------
A question is not trustworthy because a model produced it. :func:`parse_questions` throws away
anything it cannot vouch for and counts every refusal by reason, so a low yield is diagnosable
rather than mysterious.

Nothing here repairs anything. Picking an answer for a question that named none, or inventing a
fourth option for a question that had three, is exactly how a plausible wrong answer reaches
somebody's certificate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .replies import collapse, json_object

#: The labels a four-option question uses, in order.
OPTION_LABELS: tuple[str, ...] = ("A", "B", "C", "D")

#: Derived, so adding a label cannot leave the count behind.
OPTION_COUNT = len(OPTION_LABELS)

#: Hard ceiling per request. Not a product rule - a guard, so a mistyped "500" cannot spend a
#: fortune in one command and produce a paper nobody will read.
MAX_QUESTIONS_PER_REQUEST = 50

#: How many previously-asked questions to show the model when asking for new ones.
#:
#: Telling the model what it already wrote is far more effective than filtering afterwards: a
#: filter can only reject a repeat, this stops it being written. But the list cannot grow without
#: limit, or the hundredth generation spends its whole prompt reciting the first ninety-nine.
#: Forty covers the recent history a model is most likely to repeat; newest first, so what falls
#: off the end is the oldest and least likely to be reproduced.
MAX_AVOID_STEMS = 40

#: How much of each previously-asked question to show. The opening clause identifies the point
#: being tested; the rest is scenario detail that would crowd out the instruction.
AVOID_STEM_CHARS = 160

#: Questions per model call. Fifty in one call is ~16,000 sequential output tokens and takes
#: minutes; ten at a time, five calls at once, is the same fifty in well under a minute.
BATCH_SIZE = 10

#: Distinct angles to ask from, one per concurrent batch.
#:
#: Identical prompts running at the same time produce near-identical questions - five batches all
#: opening with the most obvious point in the syllabus, four fifths of them discarded as repeats.
#: The angle is what makes splitting worth doing.
#:
#: They are not arbitrary. They are the five things a professional assessment has to test: can you
#: apply it, do you know the numbers, do you know when it does not apply, do you know the
#: procedure, and do you know what happens when it goes wrong.
ANGLES: tuple[str, ...] = (
    "applying the rules to a short factual scenario",
    "the precise thresholds, time limits, and definitions",
    "exceptions, and the situations where the general rule does not apply",
    "procedure - who must do what, and in what order",
    "consequences - remedies, sanctions, and what follows from getting it wrong",
)


@dataclass(frozen=True, slots=True)
class CourseBrief:
    """What the generator is told about the course it is writing for.

    Deliberately small. A name, a level and a description are enough to aim the questions;
    everything else a course row could carry - fees, deadlines, graduate salaries - is marketing
    metadata that would dilute the prompt.

    On the catalogue as it stands, ``description``, ``rqf_level`` and ``subject_area`` are NULL on
    every row, so in practice this is a title and nothing else. :attr:`grounding` exists so that
    fact travels with the brief and can be reported honestly rather than glossed over.
    """

    #: The catalogue code (``LL-34590``), or ``None`` when no course matched and the caller's own
    #: words are all there is.
    code: str | None
    name: str
    description: str | None = None
    #: RQF 2 is GCSE-equivalent and 8 is doctoral, so this changes the questions more than any
    #: prompt wording does. Passed through rather than inferred from the title.
    rqf_level: int | None = None
    subject_area: str | None = None
    #: Module titles where the course has them: a syllabus in miniature.
    modules: tuple[str, ...] = field(default_factory=tuple)

    @property
    def grounding(self) -> str:
        """What the questions were actually written from - for reporting, not for the prompt."""
        if self.description or self.modules:
            return "the course description"
        if self.code:
            return "the course name only (the catalogue row has no description)"
        return "the name supplied by the caller (no course matched)"


@dataclass(frozen=True, slots=True)
class GeneratedOption:
    label: str
    text: str
    is_correct: bool


@dataclass(frozen=True, slots=True)
class GeneratedQuestion:
    """One question the parser was willing to vouch for."""

    question_text: str
    options: tuple[GeneratedOption, ...]
    explanation: str | None = None

    @property
    def answer_label(self) -> str:
        return next(option.label for option in self.options if option.is_correct)

    @property
    def key(self) -> str:
        """The identity used to detect a repeat: the stem, case- and space-insensitive."""
        return stem_key(self.question_text)


def stem_key(text: str) -> str:
    """The repeat-detection identity of a question stem.

    One function, used both when reading history out of the database and when checking a fresh
    reply, so the two cannot drift into disagreeing about what counts as the same question.
    """
    return collapse(text).casefold()


@dataclass(frozen=True, slots=True)
class ParseReport:
    """What survived, and what did not.

    ``refusals`` is counted per reason rather than as a bare total. "12 refused" tells an operator
    something went wrong; "12 refused, 9 of them for duplicate options" tells them what, and a
    prompt problem looks nothing like a throttling problem in that list.
    """

    accepted: tuple[GeneratedQuestion, ...] = field(default_factory=tuple)
    refusals: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return len(self.accepted)

    @property
    def refused(self) -> int:
        return sum(count for _, count in self.refusals)


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------

RULES = """
Write multiple-choice questions that test understanding, not recall of a course description.

Rules, all of which are checked:
- exactly 4 options per question, labelled A, B, C, D
- exactly one option is correct
- the three wrong options must be plausible to someone who half-knows the material; an obviously
  silly option teaches nothing and makes the question free
- no "all of the above", no "none of the above", no negated stems ("which is NOT...")
- no two questions may test the same point
- each question must stand alone; never refer to "the course" or "the text above"
- one sentence of explanation per question, saying why the correct option is correct

Return ONLY a JSON object, no prose before or after it:

{"questions": [
  {"question": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
   "answer": "B", "explanation": "..."}
]}
""".strip()


def build_prompt(
    brief: CourseBrief,
    count: int,
    *,
    angle: str | None = None,
    avoid: tuple[str, ...] = (),
) -> str:
    """The instruction sent to the model.

    Everything the model is told about the course comes from ``brief``. There is deliberately no
    "use your knowledge of the subject" instruction: it will anyway, and pretending otherwise
    would obscure where the questions actually come from. What the brief does is aim that
    knowledge at the right subject and the right level.

    ``angle`` narrows what this batch should test - see :data:`ANGLES`. Optional, because one
    small request needs no angle; it exists so concurrent batches do not all write the same paper.

    ``avoid`` lists stems already asked for this course, newest first. Capped at
    :data:`MAX_AVOID_STEMS` entries and :data:`AVOID_STEM_CHARS` characters each.
    """
    lines = [f"Course: {brief.name}"]
    if brief.rqf_level is not None:
        lines.append(f"Level: RQF {brief.rqf_level}")
    if brief.subject_area:
        lines.append(f"Subject area: {brief.subject_area}")
    if brief.description:
        lines.append(f"Description: {collapse(brief.description, 1500)}")
    if brief.modules:
        lines.append("Modules covered:")
        lines.extend(f"  - {title}" for title in brief.modules[:30])

    focus = f"\n\nFor these questions, focus on {angle}." if angle else ""

    already = ""
    if avoid:
        seen = "\n".join(
            f"  - {collapse(stem, AVOID_STEM_CHARS)}"
            for stem in avoid[:MAX_AVOID_STEMS]
        )
        already = (
            "\n\nThese questions have already been asked for this course. Write questions that "
            "test DIFFERENT points. Do not rephrase any of these, and do not test the same rule "
            "from a different angle:\n" + seen
        )

    return (
        f"Write {count} multiple-choice questions for the following course.\n\n"
        + "\n".join(lines)
        + focus
        + already
        + "\n\n"
        + RULES
    )


def batch_sizes(count: int, *, size: int = BATCH_SIZE) -> tuple[int, ...]:
    """How to split a request of ``count`` questions into concurrent calls."""
    if count <= 0:
        return ()
    whole, remainder = divmod(count, size)
    return tuple([size] * whole + ([remainder] if remainder else []))


def with_headroom(count: int, *, has_history: bool, extra: float = 0.4) -> int:
    """How many to ask for, to end up with ``count``.

    Repeats are dropped and nothing backfills them, so asking for ten on a course with history can
    return seven. Forty per cent extra covers the usual attrition and the surplus is discarded.
    Without history there is nothing to repeat, so nothing extra is asked for - a model's
    duplicates within one reply are rare and cheap to absorb.
    """
    if count <= 0:
        return 0
    if not has_history:
        return count
    return min(MAX_QUESTIONS_PER_REQUEST, count + max(1, round(count * extra)))


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------



def parse_questions(
    text: str,
    *,
    wanted: int,
    already_asked: frozenset[str] = frozenset(),
) -> ParseReport:
    """Everything in the model's reply that can be vouched for, and why the rest could not.

    ``wanted`` caps the result: a model that returns thirty when asked for twenty has not followed
    instructions, and silently keeping the extras makes the paper length unpredictable.

    ``already_asked`` holds :func:`stem_key` values for this course's history. A model told not to
    repeat itself sometimes does, so the instruction in the prompt is enforced here as well.
    """
    payload = json_object(text)
    if payload is None:
        return ParseReport(refusals=(("the reply was not JSON", 1),))

    raw = payload.get("questions")
    if not isinstance(raw, list) or not raw:
        return ParseReport(refusals=(("the reply carried no questions array", 1),))

    accepted: list[GeneratedQuestion] = []
    refusals: Counter[str] = Counter()
    seen: set[str] = set(already_asked)

    for item in raw:
        if len(accepted) >= wanted:
            break
        question, reason = _one(item, seen)
        if question is None:
            refusals[reason or "a question was refused"] += 1
            continue
        seen.add(question.key)
        accepted.append(question)

    return ParseReport(accepted=tuple(accepted), refusals=tuple(refusals.items()))


def _one(item: object, seen: set[str]) -> tuple[GeneratedQuestion | None, str | None]:
    """One question, or ``None`` and the reason it was refused."""
    if not isinstance(item, dict):
        return None, "an entry was not an object"

    question_text = collapse(item.get("question"), 2000)
    if not question_text:
        return None, "a question had no text"
    if stem_key(question_text) in seen:
        return None, "a question repeated one already asked"

    options = item.get("options")
    if not isinstance(options, dict):
        return None, "a question had no options object"

    if any(str(label).strip().upper() not in OPTION_LABELS for label in options):
        # A fifth option is as wrong as a third: it is an option the answer key was never checked
        # against, and a learner could be shown it.
        return None, "a question had options outside A-D"

    texts: dict[str, str] = {}
    for label in OPTION_LABELS:
        value = collapse(options.get(label), 1000)
        if not value:
            return None, f"a question was missing option {label}"
        texts[label] = value

    if len(set(texts.values())) != OPTION_COUNT:
        # Two identical options make the question unanswerable, and a model that repeats itself
        # here has usually padded rather than thought.
        return None, "a question had duplicate options"

    # Stripped of the punctuation a label picks up ("B.", "(B)"), and then required to *be* a
    # label rather than to start with one. Taking the first character would read "B and C" as B,
    # which is not a lenient parse of a well-formed answer - it is a two-answer question silently
    # turned into a one-answer question with a key nobody chose.
    answer = collapse(item.get("answer"), 16).upper().strip(" .()[]:-")
    if answer not in OPTION_LABELS:
        return None, "a question's answer was not one of A-D"

    return (
        GeneratedQuestion(
            question_text=question_text,
            options=tuple(
                GeneratedOption(label=label, text=texts[label], is_correct=label == answer)
                for label in OPTION_LABELS
            ),
            explanation=collapse(item.get("explanation"), 2000) or None,
        ),
        None,
    )
