"""Every tuned constant, in one place, with its provenance.

These were RE-TUNED for this runtime against the fixtures, not copied from the TypeScript
reference. Tokenizer and stemmer output is not byte-identical across runtimes, and every one of
these sits downstream of it. tests/test_thresholds.py pins each value against the boundary case
that fixes it, so a future change to tokenisation fails loudly instead of drifting.

The TypeScript value is recorded beside each so the two can be compared.
"""

from __future__ import annotations

# --------------------------------------------------------------------- section matching
#: Minimum weighted score for a section to be the answer's home.
#: TypeScript: 0.35. Python: 0.35 (unmoved - verified against the boundary fixtures).
SECTION_MATCH_THRESHOLD = 0.35

#: Weight for a query token that appears nowhere in the lesson.
#: TypeScript: 1.0. Python: 1.0 (unmoved).
OOV_TOKEN_WEIGHT = 1.0

#: Multiplier when a hit lands on a title, concept name or keyword rather than body prose.
#: TypeScript: 1.35. Python: 1.35 (unmoved).
SALIENT_FIELD_BOOST = 1.35

# ----------------------------------------------------------------------- quiz protection
#: Minimum section score for naming the concept a quiz question tests. Lower than the normal
#: threshold because a protected turn only needs the topic, and nothing it returns reveals an
#: answer. TypeScript: 0.25. Python: 0.25 (unmoved).
QUIZ_TOPIC_MIN_SCORE = 0.25

#: Intent score at or above which the question is treated as answer-seeking.
#: TypeScript: 0.55. Python: 0.55 (unmoved).
QUIZ_INTENT_BLOCK_THRESHOLD = 0.55

#: Intent score at or above which the question is ambiguous rather than plainly a concept
#: question. TypeScript: 0.28. Python: 0.30 (MOVED - see docs/assumptions.md A-12).
QUIZ_INTENT_AMBIGUOUS_THRESHOLD = 0.30

#: Signal weights. Hard signals are unambiguous answer-seeking moves.
ASSESSMENT_CONTEXT_WEIGHT = 0.15
LEARNING_DISCOUNT = 0.30
HARD_SIGNAL_DISCOUNT_CAP = 0.10

# ------------------------------------------------------------------ paraphrase detection
#: Jaccard similarity at or above which a new explanation counts as a repeat of an earlier one.
#: TypeScript: 0.82. Python: 0.65 (MOVED - see docs/assumptions.md A-01).
#:
#: Fitted to measured data rather than carried over. On this runtime's tokeniser:
#:     max similarity between the six real framings ... 0.512  (must NOT be rejected)
#:     a substantive reword of one explanation ....... 0.733  (MUST be rejected)
#:     a genuinely different framing .................. 0.040
#: 0.65 sits between the two live boundaries with headroom on each side (0.138 below,
#: 0.083 above). tests/test_thresholds.py pins both edges.
PARAPHRASE_SIMILARITY_THRESHOLD = 0.65

# --------------------------------------------------------------------- extraction budget
#: Distinct verbatim source spans UC-04 will quote for one concept in one session.
MAX_QUOTED_SPANS_PER_CONCEPT = 3

#: Distinct verbatim spans any single response may carry.
MAX_QUOTED_SPANS_PER_RESPONSE = 2

#: Hard word cap on any single quoted span.
MAX_QUOTED_SPAN_WORDS = 25
