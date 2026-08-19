"""The answer-key sanitisation boundary (§12, §13, §25, §26).

    Raw Question Data  →  Sanitizer  →  Safe Coaching Context  →  LLM

This module is the reason UC-07 can make a security claim rather than a promise. The coaching
prompt tells the model not to reveal answers; that is a *second* layer and worth having, but it is
not what makes the system safe. What makes it safe is that by the time any model is called, the
answer key is not in the room (§26).

THREE STAGES, IN THIS ORDER
---------------------------
**1. Construct by allow-list.** ``_build`` copies a fixed set of named fields out of the raw
material into ``SafeCoachingContext``. It is not a filter that removes bad fields — it is a
whitelist that never reads them. An upstream module that starts returning a new answer-bearing
field cannot leak it through this module, because nothing here would copy it.

**2. Scrub narrative text.** Free text can carry an answer without any field being named after it:
a misconception note that says "the correct answer is B", or a prompt that quotes the key. Exact
answer-bearing values from UC-04/UC-06 are removed from narrative fields, as are
"the correct answer is …" spans. Everything removed is counted in the report.

**3. Verify, and fail closed.** The finished payload is walked and checked for two things: any key
whose name suggests an answer key, and any surviving answer-bearing value. A finding raises
``AnswerKeyContaminationError`` and coaching is refused for that question. This stage should never
fire — stages 1 and 2 make it unreachable — which is precisely why it is worth having: if it ever
does fire, an upstream change has broken an assumption and the correct response is to stop, not to
strip a little harder and carry on (§25).

WHY THE FULL OPTION SET IS EXEMPT FROM THE VALUE SCAN
-----------------------------------------------------
The correct option's *text* is, necessarily, one of the option texts the learner was shown. Failing
the scan on it would mean never showing the coach the question at all. What leaks is not the
presence of the right answer among the choices — the learner saw all of them — but anything that
*distinguishes* it: a correctness flag, a re-ordering, a per-option mark, a subset. None of those
has a field in ``SafeCoachingContext``, delivered ``position`` is copied verbatim, and the option
list is copied whole. So the option texts are exempt from the phrase scan and nothing else is
(``PHRASE_EXEMPT_PATHS``).

WHAT IS NEVER COPIED
--------------------
``QuestionResult.answer_key``, ``QuestionFeedback.explanation``,
``QuestionFeedback.correct_answer_text``, ``QuestionFeedback.correct_option_ids``, and every
``metadata`` blob from any upstream record. The first four are the answer key in four different
costumes. The blobs are dropped because their contents are unknown by definition, and "unknown" is
not a category of data that may be forwarded to a model that must not learn the answer (§13).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.modules.coaching.domain.answers import describe_learner_answer
from app.modules.coaching.domain.context import (
    ContextOption,
    ContextOrderItem,
    LessonPointer,
    SafeCoachingContext,
)
from app.modules.coaching.domain.errors import AnswerKeyContaminationError
from app.modules.coaching.domain.topics import resolve_topics
from app.modules.coaching.integration.uc03 import (
    AttemptContext,
    DeliveredQuestion,
    LearnerAnswer,
)
from app.modules.coaching.integration.uc04 import QuestionResult
from app.modules.coaching.integration.uc06 import QuestionFeedback

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: A key containing any of these fragments must not appear anywhere in a coaching payload.
#: Matched as a substring on the lower-cased key, so ``correct_option_id``, ``isCorrect`` and
#: ``answerKeyHash`` are all covered without enumerating them.
#:
#: Note that ``SafeCoachingContext`` deliberately names its outcome field ``outcome`` rather than
#: anything containing "correct", so this list needs no exceptions.
FORBIDDEN_KEY_FRAGMENTS: tuple[str, ...] = (
    "correct",
    "answer_key",
    "answerkey",
    "answer_id",
    "solution",
    "expected_answer",
    "marking_key",
    "scoring_key",
    "grading_key",
    "key_hash",
    "is_right",
)

#: Paths whose *values* are exempt from the answer-bearing phrase scan. See the module docstring.
#: ``[*]`` matches any index.
PHRASE_EXEMPT_PATHS: tuple[str, ...] = (
    "options[*].text",
    "order_items[*].text",
    "learner_response.selected_option_labels[*]",
    "learner_response.ordered_item_labels[*]",
)

#: Narrative fields that stage 2 scrubs. Structured fields are not scrubbed because they are not
#: copied from anything answer-bearing in the first place.
NARRATIVE_FIELDS: tuple[str, ...] = (
    "question_prompt",
    "scenario_text",
    "misconception_note",
    "lesson_title",
    "learner_free_text",
)

#: Shorter answer-bearing values are not scanned for. An option id ("A"), a boolean ("true") or a
#: two-letter code appears in ordinary prose constantly, and treating those as contamination would
#: make the verifier fire on innocent text while adding nothing: a bare "A" in a coaching context
#: that already lists options A–D identifies nothing. Longer values — a correct answer sentence, a
#: serialised key, an explanation — are what actually leak.
MIN_PHRASE_CHARS = 8

#: Spans that assert an answer in prose. These are removed from narrative text regardless of what
#: the answer key contains, because "the correct answer is B" is a leak in any deployment.
#: The value part is required: a question that merely *asks* "which is the correct answer?" reveals
#: nothing and must survive intact.
_ANSWER_ASSERTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(?:the\s+)?(?:correct|right)\s+(?:answer|option|choice|response|selection)s?"
        r"\s*(?:is|are|was|were|:|=)\s*[^.!?\n]*"
    ),
    re.compile(r"(?i)\banswer\s*key\s*(?:is|:|=)\s*[^.!?\n]*"),
    re.compile(r"(?i)\bcorrect[_\s]?option[_\s]?ids?\s*(?:is|are|:|=)\s*[^.!?\n]*"),
)

REMOVED = "[removed]"


# ---------------------------------------------------------------------------
# Inputs and report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawCoachingMaterial:
    """Everything upstream knows about one incorrectly answered question.

    **Untrusted by definition** (§13). This bundle deliberately includes the answer key and UC-06's
    explanation, because that is what the real records carry and a sanitiser that is only ever fed
    clean input has not been tested. It exists purely to be consumed by ``CoachingContextSanitizer``
    and is never passed to a service, a prompt or a provider.
    """

    attempt: AttemptContext
    question: DeliveredQuestion
    result: QuestionResult
    answer: LearnerAnswer | None = None
    feedback: QuestionFeedback | None = None


@dataclass(frozen=True, slots=True)
class SanitizationReport:
    """What the sanitiser removed, for logging and for tests (§13, §22).

    Carries *names and counts only*. Putting a removed value in here would recreate the leak one
    layer down, in exactly the place people forget to look.
    """

    #: Answer-bearing inputs that were present in the raw material and were not copied.
    removed_fields: tuple[str, ...] = field(default_factory=tuple)
    #: Narrative fields from which text was scrubbed in stage 2.
    scrubbed_fields: tuple[str, ...] = field(default_factory=tuple)
    #: How many distinct answer-bearing values were being guarded against.
    forbidden_value_count: int = 0
    #: Field paths where stage 3 found contamination. Empty on every healthy run.
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "removed_fields": list(self.removed_fields),
            "scrubbed_fields": list(self.scrubbed_fields),
            "forbidden_value_count": self.forbidden_value_count,
            "contamination_findings": list(self.findings),
            "answer_key_excluded": True,
        }


@dataclass(frozen=True, slots=True)
class SanitizedCoachingContext:
    """A safe context and the record of what it cost to make it safe."""

    context: SafeCoachingContext
    report: SanitizationReport


# ---------------------------------------------------------------------------
# Stage 2/3 primitives — public so they can be tested on their own
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _leaf_strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        if node.strip():
            yield node
    elif isinstance(node, Mapping):
        for value in node.values():
            yield from _leaf_strings(value)
    elif isinstance(node, Sequence) and not isinstance(node, str | bytes):
        for value in node:
            yield from _leaf_strings(value)


#: Answer-key fields that describe *how* a question was scored rather than *what* the answer was.
#: Their values ("SINGLE_CHOICE", "PARTIAL_CREDIT_WITH_PENALTY") are legitimate context vocabulary
#: and appear in the safe payload by design, so treating them as answer-bearing would make the
#: verifier fire on ``question_type`` every single time.
_STRUCTURAL_ANSWER_KEY_FIELDS: frozenset[str] = frozenset(
    {"type", "question_type", "strategy", "scoring_rule", "version", "question_id"}
)


def forbidden_values(material: RawCoachingMaterial) -> tuple[str, ...]:
    """Every answer-bearing value the sanitiser must guarantee is absent from the context.

    Assembled *from* the answer key rather than passing it on: this is the only code in the module
    that reads ``QuestionResult.answer_key``, and it reads it in order to forbid it.

    Two filters keep the list honest:

    * values shorter than ``MIN_PHRASE_CHARS`` are dropped — see that constant;
    * values that are simply the text of a **delivered option or order item** are dropped, because
      those are presented to the coach in full anyway. Guarding against them would achieve nothing
      (the coach can already read all four options) while doing real damage: the learner's own
      answer summary would be scrubbed of the very choice they made. What still protects that case
      is ``_ANSWER_ASSERTION_PATTERNS`` — "the correct answer is X" is removed whatever X is.
    """
    candidates: list[str] = []

    result_key = material.result.answer_key
    if result_key:
        # Both the serialised key and each of its leaves: a leak can be either the whole blob
        # pasted into a note, or one sentence lifted out of it.
        candidates.append(json.dumps(dict(result_key), sort_keys=True, default=str))
        candidates.extend(_answer_key_leaves(dict(result_key)))

    feedback = material.feedback
    if feedback is not None:
        candidates.extend(
            value
            for value in (feedback.correct_answer_text, feedback.explanation)
            if isinstance(value, str)
        )
        candidates.extend(feedback.correct_option_ids)
        candidates.extend(_answer_bearing_metadata_values(feedback.metadata))

    candidates.extend(_answer_bearing_metadata_values(material.question.metadata))

    presented = {
        _normalise(text)
        for text in (
            *(option.text for option in material.question.options),
            *(item.text for item in material.question.order_items),
        )
        if text
    }

    seen: dict[str, None] = {}
    for candidate in candidates:
        normalised = _normalise(candidate)
        if len(normalised) >= MIN_PHRASE_CHARS and normalised not in presented:
            seen.setdefault(candidate.strip(), None)
    return tuple(seen)


def _answer_key_leaves(answer_key: Mapping[str, Any]) -> list[str]:
    """String leaves of an answer key, skipping the fields that only describe its shape."""
    return [
        text
        for key, value in answer_key.items()
        if str(key).lower() not in _STRUCTURAL_ANSWER_KEY_FIELDS
        for text in _leaf_strings(value)
    ]


def _answer_bearing_metadata_values(metadata: Mapping[str, Any] | None) -> list[str]:
    """Values from an untrusted blob whose *key* suggests they are answer-bearing.

    The blob is dropped wholesale during construction, so this does not protect the blob — it
    protects against the same value having been copied into a narrative field somewhere upstream.
    """
    if not metadata:
        return []
    found: list[str] = []
    for key, value in metadata.items():
        if any(fragment in str(key).lower() for fragment in FORBIDDEN_KEY_FRAGMENTS):
            found.extend(_leaf_strings(value))
            if isinstance(value, Mapping | list | tuple):
                found.append(json.dumps(value, sort_keys=True, default=str))
    return found


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Match a phrase tolerantly: any run of whitespace in it matches any run in the text."""
    return re.compile(r"\s+".join(re.escape(part) for part in phrase.split()), re.IGNORECASE)


def scrub_text(text: str | None, values: Sequence[str]) -> tuple[str | None, bool]:
    """Stage 2 for one narrative string. Returns the cleaned text and whether anything changed."""
    if not text:
        return text, False

    cleaned = text
    for phrase in values:
        if len(_normalise(phrase)) < MIN_PHRASE_CHARS:
            continue
        cleaned = _phrase_pattern(phrase).sub(REMOVED, cleaned)
    for pattern in _ANSWER_ASSERTION_PATTERNS:
        cleaned = pattern.sub(REMOVED, cleaned)

    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return (cleaned or None), cleaned != text


def _walk(node: Any, path: str = "") -> Iterator[tuple[str, str | None, Any]]:
    """Yield ``(path, key, value)`` for every node in a payload, arrays included."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield child, str(key), value
            yield from _walk(value, child)
    elif isinstance(node, Sequence) and not isinstance(node, str | bytes):
        for index, value in enumerate(node):
            child = f"{path}[{index}]"
            yield child, None, value
            yield from _walk(value, child)


def _path_matches(path: str, pattern: str) -> bool:
    return re.fullmatch(re.escape(pattern).replace(r"\[\*\]", r"\[\d+\]"), path) is not None


def _phrase_exempt(path: str) -> bool:
    return any(_path_matches(path, pattern) for pattern in PHRASE_EXEMPT_PATHS)


def verify(payload: Mapping[str, Any], values: Sequence[str]) -> tuple[str, ...]:
    """Stage 3. Return the field paths where contamination was found — never the values (§22).

    Two independent checks, because the two leaks look different: an answer key that arrives as a
    *field* is caught by its name, and one that arrives as *prose* is caught by its content.
    """
    findings: list[str] = []
    haystacks: list[tuple[str, str]] = []

    for path, key, value in _walk(payload):
        if key is not None and any(
            fragment in key.lower() for fragment in FORBIDDEN_KEY_FRAGMENTS
        ):
            findings.append(f"key:{path}")
        if isinstance(value, str) and value.strip() and not _phrase_exempt(path):
            haystacks.append((path, _normalise(value)))

    for phrase in values:
        normalised = _normalise(phrase)
        if len(normalised) < MIN_PHRASE_CHARS:
            continue
        findings.extend(
            f"value:{path}" for path, haystack in haystacks if normalised in haystack
        )

    # Stable and de-duplicated, so a log line or an assertion is reproducible.
    return tuple(sorted(set(findings)))


# ---------------------------------------------------------------------------
# The sanitiser
# ---------------------------------------------------------------------------


class CoachingContextSanitizer:
    """Turns untrusted upstream material into a ``SafeCoachingContext``, or refuses (§13).

    Stateless and free of I/O, so the security tests exercise the real thing rather than a stand-in.
    """

    def sanitize(self, material: RawCoachingMaterial) -> SanitizedCoachingContext:
        """Run all three stages.

        Raises ``AnswerKeyContaminationError`` if stage 3 finds anything — coaching for that
        question is then refused rather than delivered with a smaller leak (§25).
        """
        values = forbidden_values(material)
        context, scrubbed = self._build(material, values)
        report = SanitizationReport(
            removed_fields=_removed_fields(material),
            scrubbed_fields=scrubbed,
            forbidden_value_count=len(values),
            findings=verify(context.as_dict(), values),
        )
        if not report.clean:
            raise AnswerKeyContaminationError(
                findings=report.findings, question_id=material.question.question_id
            )
        return SanitizedCoachingContext(context=context, report=report)

    # -- stage 1 + 2 --------------------------------------------------------

    def _build(
        self, material: RawCoachingMaterial, values: Sequence[str]
    ) -> tuple[SafeCoachingContext, tuple[str, ...]]:
        question = material.question
        feedback = material.feedback
        scrubbed: list[str] = []

        def clean(name: str, text: str | None) -> str | None:
            result, changed = scrub_text(text, values)
            if changed:
                scrubbed.append(name)
            return result

        response = describe_learner_answer(question, material.answer)
        free_text = clean("learner_free_text", response.free_text)
        summary = clean("learner_answer_summary", response.summary)
        if free_text != response.free_text or summary != response.summary:
            response = replace(response, free_text=free_text, summary=summary)

        lesson = None
        if feedback is not None and feedback.lesson_reference is not None:
            reference = feedback.lesson_reference
            lesson = LessonPointer(
                lesson_id=reference.lesson_id,
                title=clean("lesson_title", reference.title),
                url=reference.url,
                topic=reference.topic,
                module_title=reference.module_title,
            )

        context = SafeCoachingContext(
            attempt_id=material.attempt.attempt_id,
            course_id=material.attempt.course_id,
            course_name=material.attempt.course_name,
            question_id=question.question_id,
            question_type=question.question_type,
            question_position=question.position,
            question_prompt=clean("question_prompt", question.prompt),
            scenario_text=clean("scenario_text", question.scenario_text),
            topics=resolve_topics(question, feedback),
            options=tuple(
                # Whole list, delivered order, three fields. Not a subset, not re-sorted.
                ContextOption(
                    option_id=option.option_id, text=option.text, position=option.position
                )
                for option in question.options
            ),
            order_items=tuple(
                ContextOrderItem(item_id=item.item_id, text=item.text, position=item.position)
                for item in question.order_items
            ),
            learner_response=response,
            misconception_note=clean(
                "misconception_note", feedback.misconception_note if feedback else None
            ),
            lesson=lesson,
            outcome=material.result.outcome.value,
        )
        return context, tuple(dict.fromkeys(scrubbed))


def _removed_fields(material: RawCoachingMaterial) -> tuple[str, ...]:
    """Which answer-bearing inputs were present and left behind. Names only (§22)."""
    removed: list[str] = []
    if material.result.answer_key:
        removed.append("uc04.question_result.answer_key")
    if material.question.metadata:
        removed.append("uc03.delivered_question.metadata")
    feedback = material.feedback
    if feedback is not None:
        if feedback.explanation:
            removed.append("uc06.question_feedback.explanation")
        if feedback.correct_answer_text:
            removed.append("uc06.question_feedback.correct_answer_text")
        if feedback.correct_option_ids:
            removed.append("uc06.question_feedback.correct_option_ids")
        if feedback.metadata:
            removed.append("uc06.question_feedback.metadata")
    return tuple(removed)
