"""Answering a question from what the courses actually hold.

Pure: keyword extraction, ranking, the prompt, and the parse. No database, no HTTP.

WHY THIS RETRIEVES BEFORE IT ASKS
----------------------------------
A model asked "what is collective efficacy?" will answer well from its own knowledge, and the
answer will have nothing to do with this platform's courses. The point of the database is that
its 700-odd questions carry explanations written for these courses - so the material is found
first, put in front of the model, and shown to the reader alongside the answer.

WHAT IS NOT CLAIMED
-------------------
Where the material does not cover the question, the answer is general subject knowledge and says
so. It is never dressed up as coming from a course. The reader sees the same list of material the
model saw, so "grounded in the course" is something they can check rather than something they
have to take on trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .replies import collapse, json_object

#: Words too common to tell one question from another. Short rather than exhaustive: a longer
#: list starts discarding terms that matter ("act", "law", "duty" are all real legal keywords).
STOPWORDS = frozenset(
    """
    a an and are as at be been but by can could did do does for from had has have how i if in
    into is it its me my no not of on or our shall should so some such than that the their them
    then there these they this to under was we were what when where which who why will with
    would you your about

    explain describe define definition tell give list outline summarise summarize compare
    contrast difference differences between mean means meaning example examples please
    """.split()
)

#: Below this length a token is noise - "of", "an", and the stray letters in "s.2(1)".
MIN_KEYWORD_CHARS = 3

#: How many pieces of material to put in front of the model.
#:
#: Enough to cover a question from more than one angle, few enough that the prompt stays about
#: the question rather than becoming a reading list. Beyond roughly this many, the lowest-ranked
#: items are matching on one incidental word and dilute the ones that matter.
MAX_SNIPPETS = 8

#: How much of one explanation to show. Long enough for the substance, short enough that eight
#: of them do not crowd out the question.
SNIPPET_CHARS = 600

_TOKEN = re.compile(r"[a-z0-9]+")


def keywords(text: str) -> tuple[str, ...]:
    """The words worth searching for, in the order they appeared, without repeats."""
    seen: dict[str, None] = {}
    for token in _TOKEN.findall((text or "").lower()):
        if len(token) >= MIN_KEYWORD_CHARS and token not in STOPWORDS:
            seen.setdefault(token, None)
    return tuple(seen)


@dataclass(frozen=True, slots=True)
class Snippet:
    """One piece of material from the database, with where it came from."""

    #: Where it was read from, for the reader: "course questions" or "question bank".
    source: str
    course: str | None
    text: str
    explanation: str | None = None

    @property
    def body(self) -> str:
        return f"{self.text}\n{self.explanation}" if self.explanation else self.text


def score(snippet: Snippet, terms: tuple[str, ...]) -> int:
    """How many of the question's keywords this snippet contains.

    Distinct keywords, not occurrences: a snippet that says "negligence" nine times is about one
    thing, and one that mentions "negligence", "duty" and "foreseeability" once each is closer to
    a question asking about all three.
    """
    haystack = snippet.body.lower()
    return sum(1 for term in terms if term in haystack)


def required_matches(terms: tuple[str, ...]) -> int:
    """How many keywords a snippet must contain to count as relevant.

    One match is enough for a short question - "mens rea" is two words and both matter. But a
    question with three or more content words has enough to discriminate with, and accepting a
    single match there fills the prompt with material that shares one incidental word. That is
    worse than finding nothing: the model is handed eight irrelevant items and may cite them,
    and the page then tells the reader an answer is grounded in a course when it is not.
    """
    return 2 if len(terms) >= 3 else 1


def rank(
    snippets: list[Snippet], terms: tuple[str, ...], *, limit: int = MAX_SNIPPETS
) -> list[Snippet]:
    """The best material for these keywords, best first. Nothing that is only barely relevant."""
    if not terms:
        return []
    floor = required_matches(terms)
    scored = [(score(snippet, terms), index, snippet) for index, snippet in enumerate(snippets)]
    hits = [item for item in scored if item[0] >= floor]
    # Sorted by score, then by the order they arrived - which the database read leaves as newest
    # first, so a tie goes to the more recent material.
    hits.sort(key=lambda item: (-item[0], item[1]))
    return [snippet for _, _, snippet in hits[:limit]]


@dataclass(frozen=True, slots=True)
class Answer:
    """What came back, and what it rests on."""

    text: str
    #: Indexes into the material list that the model said it used. Empty means it answered from
    #: general knowledge, which the page states rather than hides.
    used: tuple[int, ...] = ()
    #: True when the reply could not be read as JSON and the whole of it was taken as the answer.
    #: The answer is still usable - there is no key to get wrong in prose - but the citations are
    #: unknown, and saying "unknown" is not the same as saying "none".
    citations_unavailable: bool = False
    material: tuple[Snippet, ...] = field(default_factory=tuple)

    @property
    def grounded(self) -> bool:
        return bool(self.used)


_RULES = """
Rules:
- Answer the question directly, in plain English. Three to six sentences.
- Prefer the material above. Where it covers the point, use its wording and its reasoning.
- Where the material does not cover the question, say so in your first sentence and then answer
  from your own knowledge of the subject.
- Never cite a case, statute, section number or authority that is not in the material unless you
  are certain of it.
- No headings, no bullet points, no preamble such as "Great question".

Return ONLY a JSON object, no prose before or after it:

{"answer": "...", "used": [1, 3]}

"used" lists the numbers of the material items you actually relied on. Use an empty list if you
answered from your own knowledge.
""".strip()


def build_answer_prompt(
    question: str,
    material: list[Snippet],
    *,
    course: str | None = None,
) -> str:
    """The instruction sent to the model.

    The material is numbered because the reply cites those numbers, and the page shows the same
    numbers next to the same items - so a reader can put the answer against what it came from.
    """
    lines = [f"A learner asks: {collapse(question)}"]
    if course:
        lines.append(f"\nThey are studying: {course}")

    if material:
        lines.append("\nMaterial from the course:")
        for index, snippet in enumerate(material, start=1):
            body = collapse(snippet.body, SNIPPET_CHARS)
            where = f" ({snippet.course})" if snippet.course else ""
            lines.append(f"[{index}]{where} {body}")
    else:
        # Said plainly rather than left as an empty section, so the model does not invent a
        # course source to fill the gap.
        lines.append(
            "\nThere is no material in the course library on this. Answer from your own "
            "knowledge of the subject and say, in your first sentence, that the course material "
            "does not cover it."
        )

    return "\n".join(lines) + "\n\n" + _RULES


def parse_answer(text: str, material: list[Snippet]) -> Answer | None:
    """The answer in the model's reply, or ``None`` if there was nothing to read.

    Unlike a generated question, prose has no answer key to get wrong, so a reply that is not
    JSON is used as the answer rather than thrown away - with ``citations_unavailable`` set,
    because "we do not know what this rests on" and "it rests on nothing" are different things
    and the page says which.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    payload = json_object(text)
    if payload is None:
        return Answer(
            text=collapse(text),
            citations_unavailable=True,
            material=tuple(material),
        )

    answer = collapse(payload.get("answer"))
    if not answer:
        return None

    used: list[int] = []
    raw = payload.get("used")
    if isinstance(raw, list):
        for value in raw:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            # Out-of-range citations are dropped, not renumbered. A model pointing at item 9 of
            # eight has not used a ninth item; keeping it would put a reference on the page with
            # nothing behind it.
            if 1 <= number <= len(material) and number not in used:
                used.append(number)

    return Answer(text=answer, used=tuple(used), material=tuple(material))
