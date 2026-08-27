"""Heuristic QuizIntentClassifier - signal 2 of the two quiz-protection signals.

Deliberately not a flat keyword list. It scores independent signal families and plays them
against each other:

* **hard answer-seeking** - asking for the answer, the correct option, a hint that reveals it,
  or an elimination that leaves one option standing;
* **soft answer-seeking** - confirmation seeking, explanation suppression, assessment context;
* **learning intent** - explain / why / how / principle / difference-between / teach.

Learning intent discounts the answer-seeking score, but that discount is *capped* when a hard
signal fired, so "explain which option is correct" is still caught while "explain the principle
this question tests" is not. Phrases where explanation is being refused ("don't explain, just
tell me") are stripped before learning detection, so a refusal cannot earn learning credit.

Every regex here is verified individually in tests/test_regex_verification.py rather than
assumed to behave as it did in the reference implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...core.text import normalize_text
from ...core.thresholds import (
    ASSESSMENT_CONTEXT_WEIGHT,
    HARD_SIGNAL_DISCOUNT_CAP,
    LEARNING_DISCOUNT,
    QUIZ_INTENT_AMBIGUOUS_THRESHOLD,
    QUIZ_INTENT_BLOCK_THRESHOLD,
)
from ...domain.enums import QuizIntentLabel
from ...domain.models import LessonContent, QuizIntentResult

CLASSIFIER_NAME = "heuristic_multi_signal_v1"


@dataclass(frozen=True)
class SignalRule:
    name: str
    weight: float
    hard: bool
    patterns: tuple[re.Pattern[str], ...]


def _p(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


ANSWER_SIGNALS: tuple[SignalRule, ...] = (
    SignalRule(
        "direct_answer_solicitation", 0.60, True,
        _p(
            r"\b(what|which)\s+(is|are|was)\s+(the\s+)?(correct\s+|right\s+)?answers?\b",
            r"\bthe\s+answers?\s+(to|for|of)\b",
            r"\b(tell|give|show|send)\s+(me\s+)?(the\s+)?(correct\s+|right\s+)?answers?\b",
            r"\banswers?\s+key\b",
            r"\bwhat\s+should\s+i\s+(put|answer|select|choose|tick)\b",
            r"\bsolve\s+(this\s+)?(question|quiz|test|exam)\b",
        ),
    ),
    SignalRule(
        "option_selection", 0.55, True,
        _p(
            r"\bwhich\s+(option|choice|answer|one|of\s+(these|the\s+following))\s+(is|are)\s+(the\s+)?(correct|right|true|best)\b",
            r"\b(the\s+)?(correct|right)\s+(option|choice|answer)\b",
            r"\bis\s+(it|the\s+answer)\s+[a-e]\b",
            r"\b(option|answer|choice)\s+[a-e]\s+(is|the)\s+(correct|right)\b",
        ),
    ),
    SignalRule(
        "option_confirmation", 0.55, True,
        _p(
            r"\b(confirm|verify|check)\s+(whether|if|that)?\s*(my\s+answer|option\s+)?[a-e]?\b.*\b(correct|right|wrong)\b",
            r"\bis\s+[a-e]\s+(correct|right|wrong|the\s+answer)\b",
            r"\bi\s+(picked|chose|selected|went\s+with)\s+[a-e]\b",
            r"\bis\s+my\s+answer\s+(correct|right|wrong)\b",
        ),
    ),
    SignalRule(
        "hint_to_answer", 0.55, True,
        _p(
            r"\bhint\b.*\b(which|correct|right|answer|option)\b",
            r"\b(point|nudge|steer)\s+me\s+(to|toward|towards)\s+(the\s+)?(correct|right|answer|option)\b",
            r"\bnarrow\s+it\s+down\s+to\b",
            r"\bwithout\s+telling\s+me\b.*\b(which|answer)\b",
        ),
    ),
    SignalRule(
        "elimination_request", 0.55, True,
        _p(
            r"\b(rule|cross)\s+out\b",
            r"\beliminate\s+(the\s+)?(wrong|incorrect|bad)\b",
            r"\bwhich\s+(ones?|options?)\s+(can|should)\s+i\s+(rule\s+out|eliminate|discard)\b",
            r"\bwhich\s+(ones?|options?)\s+(are|is)\s+(wrong|incorrect)\b",
        ),
    ),
    SignalRule(
        "answer_confirmation_seeking", 0.35, False,
        _p(
            r"\bam\s+i\s+(right|correct|wrong)\b",
            r"\bjust\s+(confirm|tell|say|answer)\b",
            r"\byes\s+or\s+no\b",
            r"\bright\s+or\s+wrong\b",
            r"\bdid\s+i\s+get\s+(it|this|that)\s+(right|correct|wrong)\b",
        ),
    ),
    SignalRule(
        "explanation_suppression", 0.35, False,
        _p(
            r"\b(don't|do\s+not|dont)\s+explain\b",
            r"\bwithout\s+explain(ing)?\b",
            r"\bno\s+explanation\b",
            r"\bskip\s+the\s+explanation\b",
            r"\bjust\s+the\s+answer\b",
        ),
    ),
    SignalRule(
        "weak_option_reference", 0.30, False,
        _p(
            r"\bwhich\s+(ones?|options?|choices?|of\s+(these|the\s+following))\b",
            r"\bshould\s+i\s+(pick|choose|select)\b",
        ),
    ),
)

ASSESSMENT_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = _p(
    r"\bquiz\b", r"\bexam\b", r"\bassessment\b", r"\bmcq\b", r"\bmultiple\s+choice\b",
    r"\bquestion\s+\d+\b", r"\bq\d+\b", r"\bthis\s+question\b", r"\boption\s+[a-e]\b",
    r"\bmy\s+answer\b", r"\bgraded?\b", r"\bmarks?\b",
)

LEARNING_PATTERNS: tuple[re.Pattern[str], ...] = _p(
    r"\bexplain\b", r"\bexplanation\s+of\b", r"\bwhy\s+(is|are|does|do|would|should)\b",
    r"\bhow\s+(does|do|is|are|would)\b", r"\bhelp\s+me\s+understand\b",
    r"\bi\s+(don't|do\s+not)\s+understand\b", r"\bwhat\s+does\s+.+\s+mean\b",
    r"\bdifference\s+between\b", r"\bwalk\s+me\s+through\b", r"\bteach\s+me\b",
    r"\bprinciple\b", r"\bconcept\b", r"\bin\s+your\s+own\s+words\b",
    r"\bis\s+my\s+(understanding|reasoning|thinking)\b", r"\bhow\s+it\s+works\b",
)

#: Phrases where explanation is refused, not requested. Stripped before learning detection.
NEGATED_EXPLANATION = re.compile(
    r"\b(don't|do\s+not|dont|no\s+need\s+to|without|skip\s+the)\s+explain(ing|ation)?\b"
)

#: Attempts to override the system's own rules. Detected so injection is visible in the
#: signals, and so it cannot pass as an innocuous learning request.
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = _p(
    r"\bignore\s+(all\s+|any\s+|your\s+|the\s+)?(previous\s+|prior\s+|above\s+)?(instructions?|rules?|guardrails?)\b",
    r"\bdisregard\s+(your|the|all)\b",
    r"\byou\s+are\s+now\b",
    r"\bsystem\s+prompt\b",
    r"\bdeveloper\s+mode\b",
    r"\breveal\s+your\s+(instructions?|prompt|rules?)\b",
    r"\bquiz\s+protection\s+(is\s+)?(off|disabled)\b",
    r"\b(disable|turn\s+off|bypass|override)\s+(the\s+)?(quiz\s+)?(protection|filter|safeguards?)\b",
)


class HeuristicQuizIntentClassifier:
    name = "mock"

    def classify(self, question: str, lesson: LessonContent | None) -> QuizIntentResult:
        text = normalize_text(question)
        signals: list[str] = []
        score = 0.0
        hard_fired = False

        for rule in ANSWER_SIGNALS:
            if any(p.search(text) for p in rule.patterns):
                signals.append(rule.name)
                score += rule.weight
                hard_fired = hard_fired or rule.hard

        # Server-derived only: whether the lesson itself carries assessment items. Nothing the
        # caller sends can weaken this, and nothing can switch it off.
        lesson_has_quiz = bool(lesson and lesson.has_quiz_items)
        context_in_text = any(p.search(text) for p in ASSESSMENT_CONTEXT_PATTERNS)
        if context_in_text:
            signals.append("assessment_context")
        if lesson_has_quiz:
            signals.append("lesson_has_quiz_items")
        if context_in_text or lesson_has_quiz:
            score += ASSESSMENT_CONTEXT_WEIGHT

        if any(p.search(text) for p in INJECTION_PATTERNS):
            # An attempt to disable protection is itself strong evidence of answer seeking.
            signals.append("instruction_override_attempt")
            score += 0.60
            hard_fired = True

        learning_text = NEGATED_EXPLANATION.sub(" ", text)
        learning_hit = any(p.search(learning_text) for p in LEARNING_PATTERNS)
        if learning_hit:
            signals.append("learning_intent")

        cap = HARD_SIGNAL_DISCOUNT_CAP if hard_fired else LEARNING_DISCOUNT
        discount = min(LEARNING_DISCOUNT, cap) if learning_hit else 0.0
        final = max(0.0, min(1.0, min(1.0, score) - discount))

        if final >= QUIZ_INTENT_BLOCK_THRESHOLD:
            label = QuizIntentLabel.QUIZ_ANSWER_REQUEST
            confidence = round(min(1.0, final), 2)
        elif final >= QUIZ_INTENT_AMBIGUOUS_THRESHOLD:
            label = QuizIntentLabel.AMBIGUOUS
            confidence = round(0.5 + (QUIZ_INTENT_BLOCK_THRESHOLD - final) / 4, 2)
        else:
            label = QuizIntentLabel.CONCEPT_LEARNING_REQUEST
            confidence = round(1.0 - final, 2)

        return QuizIntentResult(
            label=label.value,
            confidence=confidence,
            signals=tuple(signals),
            classifier=CLASSIFIER_NAME,
        )
