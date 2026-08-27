"""Fixture data for the mock adapters, one session id per scenario.

The scenario is chosen by the session id, so a test names the situation it
wants rather than assembling one. Every scenario in the specification matrix
has an id here.

This is mock data for a component whose upstreams do not exist yet. It is not
seeded from, and makes no claim about, any real learner or session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from uc09_summary.domain.enums import (
    NaricLevel,
    NaricLevelSource,
    ResourceKind,
    SessionStatus,
    SourceStatus,
    SuggestionSource,
)
from uc09_summary.domain.models import (
    InteractionRecord,
    Resource,
    SessionRecord,
    Suggestion,
)

UTC = UTC

#: Owner of every scenario session except NOT_OWNED.
OWNER_USER_ID = "user-owner-001"
OTHER_USER_ID = "user-other-002"

BASE = datetime(2026, 3, 4, 9, 0, 0, tzinfo=UTC)

# -- session ids ------------------------------------------------------------
SESSION_COMPLETE = "sess-complete-multi-topic"
SESSION_IN_PROGRESS = "sess-in-progress"
SESSION_NOT_OWNED = "sess-not-owned"
SESSION_UNAVAILABLE = "sess-session-provider-down"
SESSION_SINGLE_TOPIC = "sess-single-topic"
SESSION_ONE_INTERACTION = "sess-one-interaction"
SESSION_NO_INTERACTIONS = "sess-no-interactions"
SESSION_INTERACTIONS_UNAVAILABLE = "sess-interactions-down"
SESSION_NO_CITATIONS = "sess-no-citations"
SESSION_CITATIONS_UNAVAILABLE = "sess-citations-down"
SESSION_NO_GAP_SUGGESTIONS = "sess-no-gap-suggestions"
SESSION_GAP_UNAVAILABLE = "sess-gap-down"
SESSION_INVALID_NARIC = "sess-invalid-naric"
SESSION_MISSING = "sess-does-not-exist"
SESSION_TIMEOUT = "sess-session-provider-timeout"
#: Nothing logged and no gap report: the only case where Next Steps has no
#: admissible source at all, and must therefore be omitted with a note.
SESSION_NOTHING_TO_REPORT = "sess-nothing-to-report"

#: Users whose gap report behaves specially, keyed by user id.
USER_NO_GAP_SUGGESTIONS = "user-no-gap-003"
USER_GAP_UNAVAILABLE = "user-gap-down-004"
USER_GAP_TIMEOUT = "user-gap-timeout-005"


def _session(
    session_id: str,
    *,
    user_id: str = OWNER_USER_ID,
    display_name: str = "Amara Osei",
    status: SessionStatus = SessionStatus.COMPLETED,
    started: datetime = BASE,
    duration_minutes: int | None = 47,
    naric: NaricLevel = NaricLevel.LEVEL_7,
    naric_source: NaricLevelSource = NaricLevelSource.RETRIEVED,
    naric_status: SourceStatus = SourceStatus.AVAILABLE,
    completion: int = 62,
    course: str | None = "Employment Law Practice",
) -> SessionRecord:
    ended = started + timedelta(minutes=duration_minutes) if duration_minutes else None
    return SessionRecord(
        session_id=session_id,
        user_id=user_id,
        user_display_name=display_name,
        started_at=started,
        ended_at=ended,
        status=status,
        naric_level=naric,
        naric_level_source=naric_source,
        naric_level_status=naric_status,
        course_completion_percent=completion,
        course_title=course,
    )


#: session_id -> SessionRecord for every retrievable scenario.
SESSIONS: dict[str, SessionRecord] = {
    SESSION_COMPLETE: _session(SESSION_COMPLETE),
    SESSION_IN_PROGRESS: _session(
        SESSION_IN_PROGRESS, status=SessionStatus.IN_PROGRESS, duration_minutes=None
    ),
    SESSION_NOT_OWNED: _session(
        SESSION_NOT_OWNED, user_id=OTHER_USER_ID, display_name="Rhys Lloyd"
    ),
    SESSION_SINGLE_TOPIC: _session(SESSION_SINGLE_TOPIC, duration_minutes=31),
    SESSION_ONE_INTERACTION: _session(SESSION_ONE_INTERACTION, duration_minutes=6),
    SESSION_NO_INTERACTIONS: _session(SESSION_NO_INTERACTIONS, duration_minutes=2),
    SESSION_INTERACTIONS_UNAVAILABLE: _session(SESSION_INTERACTIONS_UNAVAILABLE),
    SESSION_NO_CITATIONS: _session(SESSION_NO_CITATIONS, duration_minutes=22),
    SESSION_CITATIONS_UNAVAILABLE: _session(SESSION_CITATIONS_UNAVAILABLE),
    SESSION_NO_GAP_SUGGESTIONS: _session(
        SESSION_NO_GAP_SUGGESTIONS, user_id=USER_NO_GAP_SUGGESTIONS
    ),
    SESSION_GAP_UNAVAILABLE: _session(
        SESSION_GAP_UNAVAILABLE, user_id=USER_GAP_UNAVAILABLE
    ),
    # Upstream sent something that maps to no enum member. The adapter applies
    # the LEVEL_5 default, marks the source default and the status invalid.
    SESSION_NOTHING_TO_REPORT: _session(
        SESSION_NOTHING_TO_REPORT,
        user_id=USER_NO_GAP_SUGGESTIONS,
        duration_minutes=1,
    ),
    SESSION_INVALID_NARIC: _session(
        SESSION_INVALID_NARIC,
        naric=NaricLevel.LEVEL_5,
        naric_source=NaricLevelSource.DEFAULT,
        naric_status=SourceStatus.INVALID,
    ),
}


def _interaction(
    session_id: str,
    index: int,
    minutes: int,
    question: str,
    topics: tuple[str, ...],
    concepts: tuple[str, ...],
) -> InteractionRecord:
    return InteractionRecord(
        interaction_id=f"{session_id}-i{index:02d}",
        session_id=session_id,
        occurred_at=BASE + timedelta(minutes=minutes),
        question_text=question,
        topic_tags=topics,
        concept_tags=concepts,
    )


_MULTI = (
    _interaction(
        SESSION_COMPLETE, 1, 2,
        "When does a dismissal become automatically unfair?",
        ("unfair-dismissal",),
        ("automatically-unfair-reasons", "qualifying-period"),
    ),
    _interaction(
        SESSION_COMPLETE, 2, 9,
        "How is the basic award calculated?",
        ("unfair-dismissal", "remedies"),
        ("basic-award-calculation",),
    ),
    _interaction(
        SESSION_COMPLETE, 3, 18,
        "What is the band of reasonable responses?",
        ("unfair-dismissal",),
        ("band-of-reasonable-responses",),
    ),
    _interaction(
        SESSION_COMPLETE, 4, 29,
        "Can a compensatory award be reduced for contributory fault?",
        ("remedies",),
        ("contributory-fault",),
    ),
    _interaction(
        SESSION_COMPLETE, 5, 41,
        "What counts as a protected disclosure?",
        ("whistleblowing",),
        ("protected-disclosure",),
    ),
)

_SINGLE = (
    _interaction(
        SESSION_SINGLE_TOPIC, 1, 3,
        "What makes a restrictive covenant enforceable?",
        ("restrictive-covenants",),
        ("legitimate-business-interest",),
    ),
    _interaction(
        SESSION_SINGLE_TOPIC, 2, 11,
        "How is reasonableness of scope assessed?",
        ("restrictive-covenants",),
        ("reasonableness-of-scope",),
    ),
    _interaction(
        SESSION_SINGLE_TOPIC, 3, 19,
        "Can an unreasonable clause be severed?",
        ("restrictive-covenants",),
        ("severance-of-clauses",),
    ),
    _interaction(
        SESSION_SINGLE_TOPIC, 4, 26,
        "What duration is usually upheld?",
        ("restrictive-covenants",),
        ("duration-limits",),
    ),
)

_ONE = (
    _interaction(
        SESSION_ONE_INTERACTION, 1, 1,
        "What is the qualifying period for unfair dismissal?",
        ("unfair-dismissal",),
        ("qualifying-period",),
    ),
)

_NO_CITATION_SESSION = (
    _interaction(
        SESSION_NO_CITATIONS, 1, 2,
        "How should I structure a grievance meeting?",
        ("grievance-procedure",),
        ("meeting-structure", "record-keeping"),
    ),
    _interaction(
        SESSION_NO_CITATIONS, 2, 12,
        "Who should chair the meeting?",
        ("grievance-procedure",),
        ("impartial-chair",),
    ),
)

_GAP_VARIANT_INTERACTIONS = {
    SESSION_NO_GAP_SUGGESTIONS: _MULTI,
    SESSION_GAP_UNAVAILABLE: _MULTI,
    SESSION_CITATIONS_UNAVAILABLE: _MULTI,
    SESSION_INVALID_NARIC: _MULTI,
    SESSION_IN_PROGRESS: _MULTI[:3],
    SESSION_NOT_OWNED: _MULTI,
}


def _rebind(
    records: tuple[InteractionRecord, ...], session_id: str
) -> tuple[InteractionRecord, ...]:
    """Re-key a fixture set onto another session id."""
    return tuple(
        record.model_copy(
            update={
                "session_id": session_id,
                "interaction_id": record.interaction_id.replace(
                    record.session_id, session_id
                ),
            }
        )
        for record in records
    )


#: session_id -> interactions.
INTERACTIONS: dict[str, tuple[InteractionRecord, ...]] = {
    SESSION_COMPLETE: _MULTI,
    SESSION_SINGLE_TOPIC: _SINGLE,
    SESSION_ONE_INTERACTION: _ONE,
    SESSION_NO_INTERACTIONS: (),
    SESSION_NOTHING_TO_REPORT: (),
    SESSION_NO_CITATIONS: _NO_CITATION_SESSION,
    **{
        session_id: _rebind(records, session_id)
        for session_id, records in _GAP_VARIANT_INTERACTIONS.items()
    },
}


def _resource(
    resource_id: str,
    kind: ResourceKind,
    citation: str,
    title: str,
    cited_in: tuple[str, ...],
    minutes: int,
) -> Resource:
    return Resource(
        resource_id=resource_id,
        kind=kind,
        citation=citation,
        title=title,
        cited_in_interaction_ids=cited_in,
        first_cited_at=BASE + timedelta(minutes=minutes),
    )


_MULTI_CITATIONS = (
    _resource(
        "era-1996-s98",
        ResourceKind.LEGISLATION,
        "Employment Rights Act 1996, s 98",
        "Employment Rights Act 1996, section 98",
        (f"{SESSION_COMPLETE}-i01", f"{SESSION_COMPLETE}-i03"),
        2,
    ),
    _resource(
        "era-1996-s119",
        ResourceKind.LEGISLATION,
        "Employment Rights Act 1996, s 119",
        "Employment Rights Act 1996, section 119",
        (f"{SESSION_COMPLETE}-i02",),
        9,
    ),
    _resource(
        "iceland-frozen-foods",
        ResourceKind.CASE_LAW,
        "Iceland Frozen Foods Ltd v Jones [1983] ICR 17",
        "Iceland Frozen Foods Ltd v Jones",
        (f"{SESSION_COMPLETE}-i03",),
        18,
    ),
)

#: session_id -> authorities actually cited during that session.
CITATIONS: dict[str, tuple[Resource, ...]] = {
    SESSION_COMPLETE: _MULTI_CITATIONS,
    SESSION_SINGLE_TOPIC: (
        _resource(
            "herbert-morris-saxelby",
            ResourceKind.CASE_LAW,
            "Herbert Morris Ltd v Saxelby [1916] 1 AC 688",
            "Herbert Morris Ltd v Saxelby",
            (f"{SESSION_SINGLE_TOPIC}-i01", f"{SESSION_SINGLE_TOPIC}-i02"),
            3,
        ),
        _resource(
            "tillman-egon-zehnder",
            ResourceKind.CASE_LAW,
            "Tillman v Egon Zehnder Ltd [2019] UKSC 32",
            "Tillman v Egon Zehnder Ltd",
            (f"{SESSION_SINGLE_TOPIC}-i03",),
            19,
        ),
    ),
    SESSION_ONE_INTERACTION: (
        _resource(
            "era-1996-s108",
            ResourceKind.LEGISLATION,
            "Employment Rights Act 1996, s 108",
            "Employment Rights Act 1996, section 108",
            (f"{SESSION_ONE_INTERACTION}-i01",),
            1,
        ),
    ),
    # Nothing was cited. Empty, not unavailable.
    SESSION_NO_CITATIONS: (),
    SESSION_NO_INTERACTIONS: (),
    SESSION_NOTHING_TO_REPORT: (),
}

for _sid in (
    SESSION_NO_GAP_SUGGESTIONS,
    SESSION_GAP_UNAVAILABLE,
    SESSION_INVALID_NARIC,
    SESSION_NOT_OWNED,
):
    CITATIONS[_sid] = tuple(
        r.model_copy(
            update={
                "resource_id": r.resource_id,
                "cited_in_interaction_ids": tuple(
                    cid.replace(SESSION_COMPLETE, _sid)
                    for cid in r.cited_in_interaction_ids
                ),
            }
        )
        for r in _MULTI_CITATIONS
    )

CITATIONS[SESSION_IN_PROGRESS] = tuple(
    r.model_copy(
        update={
            "cited_in_interaction_ids": tuple(
                cid.replace(SESSION_COMPLETE, SESSION_IN_PROGRESS)
                for cid in r.cited_in_interaction_ids
            )
        }
    )
    for r in _MULTI_CITATIONS
    if r.resource_id != "iceland-frozen-foods"
)


#: user_id -> gap-report suggestions. Absence from this map is not the same as
#: an empty tuple: see the mock provider.
GAP_SUGGESTIONS: dict[str, tuple[Suggestion, ...]] = {
    OWNER_USER_ID: (
        Suggestion(
            suggestion_id="gap-tupe-basics",
            label="TUPE: transfer of undertakings",
            rationale="Not yet covered at the depth your course expects.",
            source=SuggestionSource.GAP_REPORT,
        ),
        Suggestion(
            suggestion_id="gap-discrimination-remedies",
            label="Discrimination remedies and injury to feelings",
            rationale="Partially covered in earlier sessions.",
            source=SuggestionSource.GAP_REPORT,
        ),
    ),
    OTHER_USER_ID: (
        Suggestion(
            suggestion_id="gap-contract-formation",
            label="Contract formation",
            rationale="Foundational gap identified.",
            source=SuggestionSource.GAP_REPORT,
        ),
    ),
    # Gap analysis ran and suggested nothing. Empty, not unavailable.
    USER_NO_GAP_SUGGESTIONS: (),
}


def owner_of(session_id: str) -> str:
    """Return the learner who owns a scenario session.

    Several scenarios deliberately belong to a learner other than the default
    owner, so that ownership enforcement is exercised rather than assumed.
    """
    record = SESSIONS.get(session_id)
    return record.user_id if record is not None else OWNER_USER_ID
