"""Turning a course into multiple-choice questions: the prompt, and the parse.

Two pure functions and a couple of value types. No HTTP, no database, no model client — so the
part most likely to be wrong is also the part that is cheapest to test.

WHY THE MODEL IS ASKED FOR JSON
------------------------------
The company's own example asked in prose and got prose back — "Q1. … A. … B. … Answer: B". Parsing
that is guesswork: option labels drift between ``A.`` and ``a)``, an explanation runs across two
lines, a question contains the word "Answer". Asking for JSON moves the ambiguity into the model's
job, where it is good, and out of a regular expression, where it is not.

WHAT IS REFUSED, AND WHY THAT MATTERS MORE THAN WHAT IS ACCEPTED
---------------------------------------------------------------
A generated question is not trustworthy because a model produced it. :func:`parse_questions` throws
away anything it cannot vouch for — a missing key, four options that are not four, a correct answer
that is not one of the options, a duplicate question — and reports how many it dropped. The caller
then decides whether what survived is enough.

The alternative would be to repair a malformed question and keep it. That is exactly how a plausible
wrong answer reaches a learner who is being certified against it, so nothing here repairs anything.

**Every question is generated as a DRAFT.** UC-02 already has the lifecycle for this: a question is
DRAFT until an administrator activates it, and only ACTIVE questions are ever delivered. Generation
producing drafts is not caution for its own sake — it is what keeps a human between a model's output
and a professional's certificate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: The labels a four-option question uses, in order. UC-02 stores a label per option.
#:
#: Four because that is what UC-02 requires of a ``SINGLE_CHOICE`` question. The constant is
#: declared here rather than imported, because a domain package may not reach into another
#: capability — and ``tests/quiz_generation/test_generation_domain.py`` asserts this equals UC-02's
#: own ``SINGLE_CHOICE_OPTION_COUNT``, so the two cannot drift apart in silence.
OPTION_LABELS: tuple[str, ...] = ("A", "B", "C", "D")

#: Derived, so adding a label cannot leave the count behind.
SINGLE_CHOICE_OPTION_COUNT = len(OPTION_LABELS)

#: Hard ceiling per request. Not a product rule — a guard, so a mistyped "200" cannot spend a
#: fortune in one call and produce a quiz nobody will read.
MAX_QUESTIONS_PER_REQUEST = 50


@dataclass(frozen=True, slots=True)
class CourseBrief:
    """What the generator is told about the course it is writing questions for.

    Deliberately small. A course's name, level and description are enough to write questions about
    its subject, and everything else a course row carries — fees, application deadlines, graduate
    salaries — is marketing metadata that would only dilute the prompt.
    """

    course_id: str
    name: str
    description: str | None = None
    #: RQF level 2–8 where the platform records one. It is the single strongest signal of how hard
    #: the questions should be, so it is passed through rather than inferred from the title.
    rqf_level: int | None = None
    subject_area: str | None = None
    #: Module titles, when the course has them. A syllabus in miniature: it tells the model what the
    #: course actually covers, which a title alone does not.
    modules: tuple[str, ...] = field(default_factory=tuple)

    @property
    def topic(self) -> str:
        return self.subject_area or self.name


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


@dataclass(frozen=True, slots=True)
class ParseReport:
    """What survived, and what did not.

    ``rejected`` counts questions the model returned that could not be vouched for; ``reasons``
    names why, so a low yield is diagnosable rather than mysterious.
    """

    accepted: tuple[GeneratedQuestion, ...] = field(default_factory=tuple)
    rejected: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return len(self.accepted)


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------

_RULES = """
Write multiple-choice questions that test understanding, not recall of the course description.

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


def build_prompt(brief: CourseBrief, count: int) -> str:
    """The instruction sent to the model.

    Everything the model is told about the course comes from ``brief``. There is deliberately no
    instruction to "use your knowledge of the subject": the model will anyway, and pretending
    otherwise would obscure where the questions actually come from. What the brief does is aim that
    knowledge at the right subject and the right level.
    """
    lines = [f"Course: {brief.name}"]
    if brief.rqf_level is not None:
        # RQF 2 is GCSE-equivalent and 8 is doctoral, so this changes the questions substantially.
        lines.append(f"Level: RQF {brief.rqf_level}")
    if brief.subject_area:
        lines.append(f"Subject area: {brief.subject_area}")
    if brief.description:
        lines.append(f"Description: {' '.join(brief.description.split())[:1500]}")
    if brief.modules:
        lines.append("Modules covered:")
        lines.extend(f"  - {title}" for title in brief.modules[:30])

    return (
        f"Write {count} multiple-choice questions for the following course.\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _RULES
    )


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _payload(text: str) -> dict[str, Any] | None:
    """The JSON object in the model's reply, tolerating a fenced code block around it."""
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate[candidate.index("{") :] if "{" in candidate else candidate
    match = _JSON_BLOCK.search(candidate)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalise(text: object, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def parse_questions(text: str, *, wanted: int) -> ParseReport:
    """Everything in the model's reply that can be vouched for, and a count of what cannot.

    ``wanted`` caps the result: a model that returns thirty questions when asked for twenty has not
    followed instructions, and silently keeping the extras would make the quiz length unpredictable.
    """
    payload = _payload(text)
    if payload is None:
        return ParseReport(reasons=("the reply was not JSON",))

    raw = payload.get("questions")
    if not isinstance(raw, list) or not raw:
        return ParseReport(reasons=("the reply carried no questions array",))

    accepted: list[GeneratedQuestion] = []
    reasons: list[str] = []
    rejected = 0
    seen: set[str] = set()

    for item in raw:
        if len(accepted) >= wanted:
            break
        question, reason = _one(item, seen)
        if question is None:
            rejected += 1
            if reason and reason not in reasons:
                reasons.append(reason)
            continue
        seen.add(question.question_text.casefold())
        accepted.append(question)

    return ParseReport(accepted=tuple(accepted), rejected=rejected, reasons=tuple(reasons))


def _one(item: object, seen: set[str]) -> tuple[GeneratedQuestion | None, str | None]:
    """One question, or ``None`` and the reason it was refused."""
    if not isinstance(item, dict):
        return None, "an entry was not an object"

    question_text = _normalise(item.get("question"), 2000)
    if not question_text:
        return None, "a question had no text"
    if question_text.casefold() in seen:
        return None, "a question repeated an earlier one"

    options = item.get("options")
    if not isinstance(options, dict):
        return None, "a question had no options object"

    texts: dict[str, str] = {}
    for label in OPTION_LABELS:
        value = _normalise(options.get(label), 1000)
        if not value:
            return None, f"a question was missing option {label}"
        texts[label] = value

    if len(set(texts.values())) != SINGLE_CHOICE_OPTION_COUNT:
        # Two identical options make the question unanswerable, and a model that repeats itself
        # here has usually padded rather than thought.
        return None, "a question had duplicate options"

    answer = _normalise(item.get("answer"), 8).upper()[:1]
    if answer not in OPTION_LABELS:
        return None, "a question's answer was not one of A-D"

    return (
        GeneratedQuestion(
            question_text=question_text,
            options=tuple(
                GeneratedOption(label=label, text=texts[label], is_correct=label == answer)
                for label in OPTION_LABELS
            ),
            explanation=_normalise(item.get("explanation"), 2000) or None,
        ),
        None,
    )
