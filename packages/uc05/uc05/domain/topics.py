"""Topic tagging.

The platform's interaction log record carries a ``topic_tag``.  No taxonomy was
supplied, so this is ASSUMED (A-TOPIC-TAG): a small, deterministic keyword map
producing a coarse tag, defaulting to ``general``.  A caller may supply a tag
explicitly and it is taken as given -- when the company supplies its taxonomy,
the map here is replaced and callers that already send a tag are unaffected.

Deliberately *not* a model call: a tag on every interaction record is not worth
a generation, and a deterministic tag keeps the log stable across prompt
revisions.
"""

from __future__ import annotations

from .normalisation import flatten

GENERAL_TAG = "general"

#: Ordered: the first matching entry wins, so more specific areas come first.
TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("contract", ("contract", "consideration", "offer", "acceptance", "promisee",
                  "breach of contract", "misrepresentation")),
    ("tort", ("tort", "negligence", "duty of care", "nuisance", "defamation")),
    ("employment", ("employment", "unfair dismissal", "redundancy", "tribunal",
                    "employee", "employer")),
    ("land", ("land", "easement", "freehold", "leasehold", "conveyance",
              "covenant")),
    ("crime", ("criminal", "mens rea", "actus reus", "theft", "assault")),
    ("equity", ("trust", "trustee", "fiduciary", "beneficiary", "equitable")),
    ("family", ("divorce", "custody", "matrimonial", "child arrangements")),
    ("public", ("judicial review", "human rights", "ultra vires", "statutory duty")),
    ("evidence", ("evidence", "hearsay", "burden of proof", "admissible")),
    ("procedure", ("limitation", "pleading", "disclosure", "costs order")),
)


def derive_topic_tag(question_text: str) -> str:
    haystack = flatten(question_text)
    for tag, keywords in TOPIC_KEYWORDS:
        for keyword in keywords:
            if keyword in haystack:
                return tag
    return GENERAL_TAG
