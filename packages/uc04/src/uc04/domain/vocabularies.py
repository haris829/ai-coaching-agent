"""Closed topic and concept vocabularies.

Free-form tags make later aggregation meaningless: "hearsay", "Hearsay" and "the hearsay rule"
become three unrelated topics. Tags therefore come from these tables only. Anything unmatched
becomes ``UNCLASSIFIED`` and contributes to the logged unclassified rate.

These tables are an ASSUMPTION (see docs/assumptions.md, A-06/A-07). The company has not
supplied its taxonomy; when it does, this file is replaced and nothing else changes.
"""

from __future__ import annotations

from typing import NamedTuple

from .enums import UNCLASSIFIED

#: Closed topic vocabulary.
TOPIC_VOCABULARY: tuple[str, ...] = (
    "evidence",
    "civil_procedure",
    "professional_conduct",
    "contract_law",
    "data_protection",
)


class ConceptEntry(NamedTuple):
    concept_tag: str
    topic_tag: str
    #: Surface forms used for deterministic tagging. Matched on normalised tokens.
    surface_forms: tuple[str, ...]


#: Closed concept vocabulary. Every concept belongs to exactly one topic.
CONCEPT_VOCABULARY: tuple[ConceptEntry, ...] = (
    ConceptEntry("hearsay", "evidence", ("hearsay", "hearsay rule", "rule against hearsay", "out of court statement")),
    ConceptEntry("hearsay_exception", "evidence", ("hearsay exception", "exception to hearsay", "business records", "res gestae")),
    ConceptEntry("witness_competence", "evidence", ("competence", "competent witness", "witness competence")),
    ConceptEntry("witness_compellability", "evidence", ("compellability", "compellable", "witness compellability")),
    ConceptEntry("expert_evidence", "evidence", ("expert evidence", "expert witness", "expert report", "single joint expert")),
    ConceptEntry("burden_of_proof", "evidence", ("burden of proof", "legal burden", "evidential burden", "who must prove")),
    ConceptEntry("standard_of_proof", "evidence", ("standard of proof", "balance of probabilities", "beyond reasonable doubt")),
    ConceptEntry("legal_advice_privilege", "evidence", ("legal advice privilege", "privileged advice", "solicitor client privilege")),
    ConceptEntry("litigation_privilege", "evidence", ("litigation privilege", "dominant purpose")),
    ConceptEntry("standard_disclosure", "civil_procedure", ("standard disclosure", "disclosure obligation", "disclosure list")),
    ConceptEntry("without_prejudice", "civil_procedure", ("without prejudice", "settlement correspondence")),
    ConceptEntry("limitation_period", "civil_procedure", ("limitation period", "limitation", "time limit for claim")),
    ConceptEntry("duty_of_candour", "professional_conduct", ("duty of candour", "candour", "duty to the court")),
    ConceptEntry("conflict_of_interest", "professional_conduct", ("conflict of interest", "own interest conflict")),
)

_BY_TAG: dict[str, ConceptEntry] = {entry.concept_tag: entry for entry in CONCEPT_VOCABULARY}


def concept_entry(concept_tag: str) -> ConceptEntry | None:
    return _BY_TAG.get(concept_tag)


def topic_for_concept(concept_tag: str) -> str:
    """Topic for a concept tag, or ``unclassified`` when the tag is not in the vocabulary."""
    entry = _BY_TAG.get(concept_tag)
    return entry.topic_tag if entry else UNCLASSIFIED


def is_known_concept(concept_tag: str) -> bool:
    return concept_tag in _BY_TAG
