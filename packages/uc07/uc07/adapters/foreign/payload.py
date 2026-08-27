"""A deliberately FOREIGN upstream payload ("Nexus LMS", fictional).

This payload exists to prove replaceability. It carries the same *meaning* as the
``struggle_mixed`` mock scenario while differing on every surface detail:

===============================  ==========================================
Platform contract                Nexus wire shape
===============================  ==========================================
``interaction_id``               ``entryRef``
``session_id``                   ``conversation.ref`` (nested)
``user_id``                      ``learner.externalId`` (nested, aliased)
``asked_at`` (ISO-8601)          ``occurredAtEpochMs`` (integer millis)
``topic_tag``                    ``taxonomy.primary`` (nested)
``question_class``               ``promptKind`` (upper case)
``naric_level`` (``LEVEL_6``)    ``eqfBand`` (``EQF-6``)
``response_id``                  ``reply.ref`` (nested)
``follow_up_of``                 ``parentEntryRef``
``explain_differently_count``    ``reexplainTally``
``rating_state``                 ``verdictLifecycle`` (``COMPLETE``/``AWAITING``)
``rating`` (``up``/``down``)     ``sentiment`` (``POSITIVE``/``NEGATIVE``)
source status                    ``completeness`` (``FULL``/``PARTIAL``/``ABSENT``)
``course_id`` / ``lesson_id``    ``programmeRef`` / ``moduleRef``
speciality areas                 ``profileDoc.focusAreas``
enrolments                       ``registrations``
===============================  ==========================================

No knowledge of these names is allowed to escape the foreign adapter module.
"""

from __future__ import annotations

from typing import Any

#: 2026-01-05T09:00:00Z expressed the way Nexus expresses time.
BASE_EPOCH_MS = 1_767_603_600_000
EXTERNAL_LEARNER_ID = "learner-001"
NEXUS_LEARNER_REF = "NX-LRN-77"


def _ms(minutes: int) -> int:
    return BASE_EPOCH_MS + minutes * 60_000


def _entry(
    entry_ref: str,
    conversation_ref: str,
    topic: str,
    minutes: int,
    *,
    prompt_kind: str = "CONCEPT",
    parent: str | None = None,
    reexplain: int = 0,
    verdict_lifecycle: str = "AWAITING",
    eqf_band: str = "EQF-6",
) -> dict[str, Any]:
    return {
        "entryRef": entry_ref,
        "conversation": {"ref": conversation_ref, "channel": "COACH"},
        "occurredAtEpochMs": _ms(minutes),
        "taxonomy": {"primary": topic, "secondary": []},
        "promptKind": prompt_kind,
        "eqfBand": eqf_band,
        "reply": {"ref": f"response-{entry_ref}", "deliveredVia": "COACH"},
        "parentEntryRef": parent,
        "reexplainTally": reexplain,
        "verdictLifecycle": verdict_lifecycle,
    }


COACHING_ENTRIES: tuple[dict[str, Any], ...] = (
    _entry("interaction-101", "session-1", "contract_formation", 0, reexplain=2, verdict_lifecycle="COMPLETE"),
    _entry("interaction-102", "session-1", "contract_formation", 12, prompt_kind="CLARIFICATION", parent="interaction-101"),
    _entry("interaction-103", "session-2", "contract_formation", 1440, reexplain=1, verdict_lifecycle="COMPLETE"),
    _entry("interaction-201", "session-1", "negligence", 25),
    _entry("interaction-202", "session-1", "negligence", 31, prompt_kind="CLARIFICATION", parent="interaction-201"),
    _entry("interaction-203", "session-2", "negligence", 1465, prompt_kind="CLARIFICATION", parent="interaction-201"),
    _entry("interaction-301", "session-2", "land_registration", 1480, verdict_lifecycle="COMPLETE"),
    _entry("interaction-302", "session-3", "land_registration", 2900),
    _entry("interaction-401", "session-3", "professional_conduct", 2915, reexplain=1),
    _entry("interaction-501", "session-3", "evidence_admissibility", 2930),
    _entry("interaction-502", "session-3", "evidence_admissibility", 2937, verdict_lifecycle="COMPLETE"),
    _entry("interaction-503", "session-3", "evidence_admissibility", 2944),
    _entry("interaction-504", "session-3", "evidence_admissibility", 2951),
    _entry("interaction-505", "session-3", "evidence_admissibility", 2958),
)

VERDICT_ENTRIES: tuple[dict[str, Any], ...] = (
    {
        "verdictRef": "rating-1",
        "entryRef": "interaction-101",
        "sentiment": "NEGATIVE",
        "atEpochMs": _ms(5),
        "remark": None,
    },
    {
        "verdictRef": "rating-2",
        "entryRef": "interaction-103",
        "sentiment": "NEGATIVE",
        "atEpochMs": _ms(1450),
        "remark": None,
    },
    {
        "verdictRef": "rating-3",
        "entryRef": "interaction-301",
        "sentiment": "NEGATIVE",
        "atEpochMs": _ms(1490),
        "remark": None,
    },
    {
        "verdictRef": "rating-4",
        "entryRef": "interaction-201",
        "sentiment": "POSITIVE",
        "atEpochMs": _ms(30),
        "remark": None,
    },
    {
        "verdictRef": "rating-5",
        "entryRef": "interaction-502",
        "sentiment": "POSITIVE",
        "atEpochMs": _ms(2940),
        "remark": None,
    },
)

PROFILE_DOC: dict[str, Any] = {
    "focusAreas": [
        {"tag": "contract_formation", "weight": 1},
        {"tag": "negligence", "weight": 1},
        {"tag": "commercial_drafting", "weight": 2},
        {"tag": "data_protection", "weight": 2},
    ],
    "focusCompleteness": "FULL",
    "eqfBand": "EQF-6",
    "eqfOrigin": "LOOKUP",
}

#: (programmeRef, label, subjectTags, ((moduleRef, label, subjectTags), ...))
_PROGRAMMES: tuple[tuple[str, str, tuple[str, ...], tuple[tuple[str, str, tuple[str, ...]], ...]], ...] = (
    (
        "course-contract-essentials",
        "Contract Law Essentials",
        ("contract_formation", "contract_terms"),
        (
            ("lesson-cf-01", "Offer and acceptance", ("contract_formation",)),
            ("lesson-cf-02", "Consideration and intention", ("contract_formation", "contract_terms")),
            ("lesson-ct-01", "Implied terms", ("contract_terms",)),
        ),
    ),
    (
        "course-tort-foundations",
        "Tort Foundations",
        ("negligence", "vicarious_liability"),
        (
            ("lesson-ng-01", "Duty of care", ("negligence",)),
            ("lesson-ng-02", "Causation and remoteness", ("negligence",)),
        ),
    ),
    (
        "course-property-practice",
        "Property Practice",
        ("land_registration",),
        (("lesson-lr-01", "Registered title and priority", ("land_registration",)),),
    ),
    (
        "course-commercial-drafting",
        "Commercial Drafting",
        ("commercial_drafting",),
        (("lesson-cd-01", "Structuring an agreement", ("commercial_drafting",)),),
    ),
    (
        "course-data-protection",
        "Data Protection in Practice",
        ("data_protection",),
        (("lesson-dp-01", "Lawful bases", ("data_protection",)),),
    ),
    (
        "course-evidence-advanced",
        "Advanced Evidence",
        ("evidence_admissibility", "criminal_mens_rea"),
        (("lesson-ev-01", "Hearsay", ("evidence_admissibility",)),),
    ),
    (
        "course-trusts-core",
        "Trusts Core",
        ("trusts_formation",),
        (("lesson-tr-01", "The three certainties", ("trusts_formation",)),),
    ),
    (
        "course-civil-procedure",
        "Civil Procedure",
        ("civil_procedure",),
        (("lesson-cp-01", "Case management", ("civil_procedure",)),),
    ),
    (
        "course-misrepresentation",
        "Misrepresentation",
        ("misrepresentation",),
        (("lesson-mr-01", "Categories and remedies", ("misrepresentation",)),),
    ),
    (
        "course-conduct",
        "Professional Conduct",
        ("professional_conduct",),
        (("lesson-pc-01", "Conflicts of interest", ("professional_conduct",)),),
    ),
)

CURRICULUM: dict[str, Any] = {
    "completeness": "FULL",
    "programmes": [
        {
            "programmeRef": programme_ref,
            "label": label,
            "subjectTags": list(subject_tags),
            "modules": [
                {
                    "moduleRef": module_ref,
                    "label": module_label,
                    "subjectTags": list(module_tags),
                }
                for module_ref, module_label, module_tags in modules
            ],
        }
        for programme_ref, label, subject_tags, modules in _PROGRAMMES
    ],
}

SUGGESTION_FEED: list[dict[str, Any]] = [
    {
        "subjectTag": subject,
        "grain": "PROGRAMME",
        "programmeRef": programme_ref,
        "moduleRef": None,
        "label": label,
    }
    for subject, programme_ref, label in (
        ("contract_formation", "course-contract-essentials", "Contract Law Essentials"),
        ("negligence", "course-tort-foundations", "Tort Foundations"),
        ("land_registration", "course-property-practice", "Property Practice"),
        ("commercial_drafting", "course-commercial-drafting", "Commercial Drafting"),
        ("data_protection", "course-data-protection", "Data Protection in Practice"),
        ("evidence_admissibility", "course-evidence-advanced", "Advanced Evidence"),
        ("trusts_formation", "course-trusts-core", "Trusts Core"),
        ("civil_procedure", "course-civil-procedure", "Civil Procedure"),
        ("misrepresentation", "course-misrepresentation", "Misrepresentation"),
        ("professional_conduct", "course-conduct", "Professional Conduct"),
    )
] + [
    # Same deliberate unresolvables as the mock, in Nexus vocabulary.
    {
        "subjectTag": "negligence",
        "grain": "PROGRAMME",
        "programmeRef": "course-does-not-exist",
        "moduleRef": None,
        "label": "Ghost Course",
    },
    {
        "subjectTag": "land_registration",
        "grain": "MODULE",
        "programmeRef": "course-property-practice",
        "moduleRef": "lesson-lr-99",
        "label": "Lesson that does not exist",
    },
]

REGISTRATIONS: list[dict[str, Any]] = [
    {
        "learnerExternalId": EXTERNAL_LEARNER_ID,
        "programmeRef": "course-contract-essentials",
        "joinedAtEpochMs": _ms(-1440),
        "progressPercent": 35,
    }
]

NEXUS_PAYLOAD: dict[str, Any] = {
    "schemaVersion": "nexus-2",
    "learnerDossiers": [
        {
            "learner": {"ref": NEXUS_LEARNER_REF, "externalId": EXTERNAL_LEARNER_ID},
            "coachingLedger": {
                "completeness": "FULL",
                "tally": len(COACHING_ENTRIES),
                "entries": list(COACHING_ENTRIES),
            },
            "verdictLedger": {
                "completeness": "FULL",
                "entries": list(VERDICT_ENTRIES),
            },
            "profileDoc": PROFILE_DOC,
        }
    ],
    "curriculum": CURRICULUM,
    "suggestionFeed": SUGGESTION_FEED,
    "registrations": REGISTRATIONS,
}
