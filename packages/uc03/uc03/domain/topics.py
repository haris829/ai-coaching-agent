"""Controlled topic vocabulary for downstream learning-gap tracking.

Topic tags feed company analytics, so they are a *closed* set. A tagger (rule
based today, possibly LLM backed later) proposes a tag; `validate_topic_tag`
is the only way a tag enters a log record. Anything outside the vocabulary is
coerced to `unclassified` and the rejection is recorded, so an LLM cannot invent
new analytics dimensions by emitting free text.

Tag values are lowercase, per the platform contract's `unclassified`.
"""

from enum import Enum


class TopicTag(str, Enum):
    CONTRACT_FORMATION = "contract_formation"
    CONTRACT_REMEDIES = "contract_remedies"
    NEGLIGENCE = "negligence"
    CRIMINAL_LIABILITY = "criminal_liability"
    CRIMINAL_PROCEDURE = "criminal_procedure"
    CIVIL_PROCEDURE = "civil_procedure"
    LAND_AND_PROPERTY = "land_and_property"
    EMPLOYMENT = "employment"
    COMPANY_AND_INSOLVENCY = "company_and_insolvency"
    FAMILY = "family"
    IMMIGRATION = "immigration"
    WILLS_AND_PROBATE = "wills_and_probate"
    EVIDENCE = "evidence"
    HUMAN_RIGHTS = "human_rights"
    LEGAL_SYSTEM = "legal_system"
    PROFESSIONAL_CONDUCT = "professional_conduct"
    UNCLASSIFIED = "unclassified"


TOPIC_VOCABULARY: frozenset[str] = frozenset(t.value for t in TopicTag)


def validate_topic_tag(raw: str | None) -> tuple[TopicTag, bool]:
    """Coerce a proposed tag into the vocabulary.

    Accepts any casing or hyphenation on the way in and normalises to the
    lowercase contract value. Returns ``(tag, accepted)``; ``accepted`` is False
    when the proposal was absent or outside the vocabulary, which callers record
    on the log entry.
    """
    if raw is None:
        return TopicTag.UNCLASSIFIED, False
    candidate = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if candidate in TOPIC_VOCABULARY:
        return TopicTag(candidate), True
    return TopicTag.UNCLASSIFIED, False
