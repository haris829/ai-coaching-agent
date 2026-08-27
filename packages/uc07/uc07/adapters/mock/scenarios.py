"""Named, fully deterministic mock scenarios.

Every dataset here is a pure function of constants: no randomness, no clock, no
sleeping, no network, no API key. The same scenario always produces the same
payload, which is what makes the determinism tests meaningful.

Scenario names are the values accepted by ``MOCK_SCENARIO``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from uc07.adapters.mock.courses import MockCoursesPayload
from uc07.adapters.mock.feedback import MockFeedbackPayload
from uc07.adapters.mock.interaction_log import MockInteractionPayload
from uc07.adapters.mock.profile import MockProfilePayload
from uc07.domain.enums import SourceStatus

LEARNER = "learner-001"
OTHER_LEARNER = "learner-002"

BASE_TIME = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)

TOPIC_CYCLE = (
    "contract_formation",
    "negligence",
    "land_registration",
    "evidence_admissibility",
)


def _at(minutes: int) -> str:
    return (BASE_TIME + timedelta(minutes=minutes)).isoformat()


def interaction(
    *,
    interaction_id: str,
    session_id: str,
    topic_tag: str,
    minute: int,
    user_id: str = LEARNER,
    question_class: str = "concept",
    naric_level: str = "LEVEL_6",
    follow_up_of: str | None = None,
    explain_differently_count: int = 0,
    rating_state: str = "pending",
) -> dict[str, Any]:
    """One mock wire-shape interaction record."""
    return {
        "interaction_id": interaction_id,
        "session_id": session_id,
        "user_id": user_id,
        "asked_at": _at(minute),
        "topic_tag": topic_tag,
        "question_class": question_class,
        "naric_level": naric_level,
        "response_id": f"response-{interaction_id}",
        "follow_up_of": follow_up_of,
        "explain_differently_count": explain_differently_count,
        "rating_state": rating_state,
    }


def feedback(
    *,
    rating_id: str,
    interaction_id: str,
    rating: str,
    minute: int,
    user_id: str = LEARNER,
    comment: str | None = None,
) -> dict[str, Any]:
    return {
        "rating_id": rating_id,
        "interaction_id": interaction_id,
        "user_id": user_id,
        "rated_at": _at(minute),
        "rating": rating,
        "comment": comment,
    }


# ---------------------------------------------------------------------------
# Interaction datasets
# ---------------------------------------------------------------------------


def sequence_records(count: int, *, user_id: str = LEARNER) -> tuple[dict[str, Any], ...]:
    """``count`` deterministic interactions cycling over four topic tags.

    Signal shape (stable for any ``count``):
      * every 4th record (index % 4 == 0) carries explain_differently_count=2;
      * every (index % 4 == 1) record is a follow-up of the previous record;
      * (index % 4 == 2) records get a thumbs-down in :func:`sequence_feedback`.
    """
    records: list[dict[str, Any]] = []
    for index in range(count):
        topic = TOPIC_CYCLE[index % len(TOPIC_CYCLE)]
        previous = f"seq-{index - 1:03d}" if index > 0 else None
        records.append(
            interaction(
                interaction_id=f"seq-{index:03d}",
                session_id=f"session-{index // 5:02d}",
                topic_tag=topic,
                minute=index * 7,
                user_id=user_id,
                question_class="concept" if index % 2 == 0 else "application",
                explain_differently_count=2 if index % 4 == 0 else 0,
                follow_up_of=previous if index % 4 == 1 else None,
                rating_state="rated" if index % 4 == 2 else "pending",
            )
        )
    return tuple(records)


def sequence_feedback(count: int, *, user_id: str = LEARNER) -> tuple[dict[str, Any], ...]:
    """Thumbs-down ratings clustered on the ``land_registration`` records."""
    records: list[dict[str, Any]] = []
    for index in range(count):
        if index % 4 != 2:
            continue
        records.append(
            feedback(
                rating_id=f"rating-{index:03d}",
                interaction_id=f"seq-{index:03d}",
                rating="down",
                minute=index * 7 + 1,
                user_id=user_id,
            )
        )
    return tuple(records)


#: The showcase history: 14 interactions, 3 sessions, 5 topic tags, mixed signals.
STRUGGLE_MIXED_RECORDS: tuple[dict[str, Any], ...] = (
    # contract_formation: heavy explain-differently + thumbs-down
    interaction(
        interaction_id="interaction-101",
        session_id="session-1",
        topic_tag="contract_formation",
        minute=0,
        explain_differently_count=2,
        rating_state="rated",
    ),
    interaction(
        interaction_id="interaction-102",
        session_id="session-1",
        topic_tag="contract_formation",
        minute=12,
        follow_up_of="interaction-101",
        question_class="clarification",
    ),
    interaction(
        interaction_id="interaction-103",
        session_id="session-2",
        topic_tag="contract_formation",
        minute=1440,
        explain_differently_count=1,
        rating_state="rated",
    ),
    # negligence: follow-up heavy, no ratings
    interaction(
        interaction_id="interaction-201",
        session_id="session-1",
        topic_tag="negligence",
        minute=25,
    ),
    interaction(
        interaction_id="interaction-202",
        session_id="session-1",
        topic_tag="negligence",
        minute=31,
        follow_up_of="interaction-201",
        question_class="clarification",
    ),
    interaction(
        interaction_id="interaction-203",
        session_id="session-2",
        topic_tag="negligence",
        minute=1465,
        follow_up_of="interaction-201",
        question_class="clarification",
    ),
    # land_registration: a single thumbs-down (low-rating threshold is 1)
    interaction(
        interaction_id="interaction-301",
        session_id="session-2",
        topic_tag="land_registration",
        minute=1480,
        rating_state="rated",
    ),
    interaction(
        interaction_id="interaction-302",
        session_id="session-3",
        topic_tag="land_registration",
        minute=2900,
    ),
    # professional_conduct: below every threshold -> must NOT surface
    interaction(
        interaction_id="interaction-401",
        session_id="session-3",
        topic_tag="professional_conduct",
        minute=2915,
        explain_differently_count=1,
    ),
    # evidence_admissibility: quiet topic, no signals
    interaction(
        interaction_id="interaction-501",
        session_id="session-3",
        topic_tag="evidence_admissibility",
        minute=2930,
    ),
    interaction(
        interaction_id="interaction-502",
        session_id="session-3",
        topic_tag="evidence_admissibility",
        minute=2937,
        rating_state="rated",
    ),
    interaction(
        interaction_id="interaction-503",
        session_id="session-3",
        topic_tag="evidence_admissibility",
        minute=2944,
    ),
    interaction(
        interaction_id="interaction-504",
        session_id="session-3",
        topic_tag="evidence_admissibility",
        minute=2951,
    ),
    interaction(
        interaction_id="interaction-505",
        session_id="session-3",
        topic_tag="evidence_admissibility",
        minute=2958,
    ),
)

STRUGGLE_MIXED_FEEDBACK: tuple[dict[str, Any], ...] = (
    feedback(rating_id="rating-1", interaction_id="interaction-101", rating="down", minute=5),
    feedback(rating_id="rating-2", interaction_id="interaction-103", rating="down", minute=1450),
    feedback(rating_id="rating-3", interaction_id="interaction-301", rating="down", minute=1490),
    feedback(rating_id="rating-4", interaction_id="interaction-201", rating="up", minute=30),
    feedback(rating_id="rating-5", interaction_id="interaction-502", rating="up", minute=2940),
)

#: One topic only -> insufficient topic diversity.
NARROW_TOPIC_RECORDS: tuple[dict[str, Any], ...] = tuple(
    interaction(
        interaction_id=f"narrow-{index:03d}",
        session_id=f"session-{index // 4:02d}",
        topic_tag="contract_formation",
        minute=index * 9,
        explain_differently_count=2 if index % 3 == 0 else 0,
        follow_up_of=f"narrow-{index - 1:03d}" if index % 3 == 1 else None,
    )
    for index in range(12)
)

#: Six distinct topics across four sessions.
DIVERSE_TOPIC_RECORDS: tuple[dict[str, Any], ...] = tuple(
    interaction(
        interaction_id=f"diverse-{index:03d}",
        session_id=f"session-{index // 3:02d}",
        topic_tag=(
            "contract_formation",
            "negligence",
            "land_registration",
            "trusts_formation",
            "criminal_mens_rea",
            "civil_procedure",
        )[index % 6],
        minute=index * 11,
        explain_differently_count=2 if index % 6 in (0, 1) else 0,
        follow_up_of=f"diverse-{index - 6:03d}" if index >= 6 and index % 6 == 2 else None,
    )
    for index in range(12)
)

#: Explain-differently only: no follow-ups, no ratings.
HEAVY_EXPLAIN_RECORDS: tuple[dict[str, Any], ...] = tuple(
    interaction(
        interaction_id=f"explain-{index:03d}",
        session_id="session-explain",
        topic_tag="misrepresentation" if index < 4 else "civil_procedure",
        minute=index * 6,
        explain_differently_count=3 if index < 4 else 0,
    )
    for index in range(10)
)

#: Follow-ups only: no explain-differently, no ratings.
HEAVY_FOLLOW_UP_RECORDS: tuple[dict[str, Any], ...] = tuple(
    interaction(
        interaction_id=f"followup-{index:03d}",
        session_id="session-followup",
        topic_tag="trusts_formation" if index < 5 else "professional_conduct",
        minute=index * 6,
        follow_up_of=f"followup-{index - 1:03d}" if 0 < index < 5 else None,
        question_class="clarification" if 0 < index < 5 else "concept",
    )
    for index in range(10)
)

#: 12 raw records, two of which repeat an id -> exactly 10 qualifying.
DUPLICATE_ID_RECORDS: tuple[dict[str, Any], ...] = sequence_records(10) + (
    sequence_records(10)[3],
    sequence_records(10)[7],
)

#: 12 raw records, two of which belong to another learner -> exactly 10 qualifying.
MIXED_OWNER_RECORDS: tuple[dict[str, Any], ...] = sequence_records(10) + (
    interaction(
        interaction_id="foreign-001",
        session_id="session-foreign",
        topic_tag="negligence",
        minute=500,
        user_id=OTHER_LEARNER,
        explain_differently_count=5,
    ),
    interaction(
        interaction_id="foreign-002",
        session_id="session-foreign",
        topic_tag="negligence",
        minute=507,
        user_id=OTHER_LEARNER,
        explain_differently_count=5,
    ),
)

#: A payload that cannot satisfy the platform contract (blank id, bad NARIC).
INVALID_RECORDS: tuple[dict[str, Any], ...] = (
    interaction(
        interaction_id="interaction-901",
        session_id="session-9",
        topic_tag="negligence",
        minute=0,
    ),
    {
        "interaction_id": "",
        "session_id": "session-9",
        "user_id": LEARNER,
        "asked_at": _at(5),
        "topic_tag": "negligence",
        "question_class": "concept",
        "naric_level": "LEVEL_99",
        "response_id": "response-902",
    },
)


# ---------------------------------------------------------------------------
# Courses datasets
# ---------------------------------------------------------------------------

CATALOGUE: tuple[dict[str, Any], ...] = (
    {
        "course_id": "course-contract-essentials",
        "title": "Contract Law Essentials",
        "topic_tags": ("contract_formation", "contract_terms"),
        "lessons": (
            {
                "lesson_id": "lesson-cf-01",
                "title": "Offer and acceptance",
                "topic_tags": ("contract_formation",),
            },
            {
                "lesson_id": "lesson-cf-02",
                "title": "Consideration and intention",
                "topic_tags": ("contract_formation", "contract_terms"),
            },
            {
                "lesson_id": "lesson-ct-01",
                "title": "Implied terms",
                "topic_tags": ("contract_terms",),
            },
        ),
    },
    {
        "course_id": "course-tort-foundations",
        "title": "Tort Foundations",
        "topic_tags": ("negligence", "vicarious_liability"),
        "lessons": (
            {
                "lesson_id": "lesson-ng-01",
                "title": "Duty of care",
                "topic_tags": ("negligence",),
            },
            {
                "lesson_id": "lesson-ng-02",
                "title": "Causation and remoteness",
                "topic_tags": ("negligence",),
            },
        ),
    },
    {
        "course_id": "course-property-practice",
        "title": "Property Practice",
        "topic_tags": ("land_registration",),
        "lessons": (
            {
                "lesson_id": "lesson-lr-01",
                "title": "Registered title and priority",
                "topic_tags": ("land_registration",),
            },
        ),
    },
    {
        "course_id": "course-commercial-drafting",
        "title": "Commercial Drafting",
        "topic_tags": ("commercial_drafting",),
        "lessons": (
            {
                "lesson_id": "lesson-cd-01",
                "title": "Structuring an agreement",
                "topic_tags": ("commercial_drafting",),
            },
        ),
    },
    {
        "course_id": "course-data-protection",
        "title": "Data Protection in Practice",
        "topic_tags": ("data_protection",),
        "lessons": (
            {
                "lesson_id": "lesson-dp-01",
                "title": "Lawful bases",
                "topic_tags": ("data_protection",),
            },
        ),
    },
    {
        "course_id": "course-evidence-advanced",
        "title": "Advanced Evidence",
        "topic_tags": ("evidence_admissibility", "criminal_mens_rea"),
        "lessons": (
            {
                "lesson_id": "lesson-ev-01",
                "title": "Hearsay",
                "topic_tags": ("evidence_admissibility",),
            },
        ),
    },
    {
        "course_id": "course-trusts-core",
        "title": "Trusts Core",
        "topic_tags": ("trusts_formation",),
        "lessons": (
            {
                "lesson_id": "lesson-tr-01",
                "title": "The three certainties",
                "topic_tags": ("trusts_formation",),
            },
        ),
    },
    {
        "course_id": "course-civil-procedure",
        "title": "Civil Procedure",
        "topic_tags": ("civil_procedure",),
        "lessons": (
            {
                "lesson_id": "lesson-cp-01",
                "title": "Case management",
                "topic_tags": ("civil_procedure",),
            },
        ),
    },
    {
        "course_id": "course-misrepresentation",
        "title": "Misrepresentation",
        "topic_tags": ("misrepresentation",),
        "lessons": (
            {
                "lesson_id": "lesson-mr-01",
                "title": "Categories and remedies",
                "topic_tags": ("misrepresentation",),
            },
        ),
    },
    {
        "course_id": "course-conduct",
        "title": "Professional Conduct",
        "topic_tags": ("professional_conduct",),
        "lessons": (
            {
                "lesson_id": "lesson-pc-01",
                "title": "Conflicts of interest",
                "topic_tags": ("professional_conduct",),
            },
        ),
    },
)


def _course_candidate(topic_tag: str, course_id: str, title: str) -> dict[str, Any]:
    return {
        "topic_tag": topic_tag,
        "recommendation_type": "course",
        "course_id": course_id,
        "lesson_id": None,
        "title": title,
    }


CANDIDATES: tuple[dict[str, Any], ...] = (
    _course_candidate("contract_formation", "course-contract-essentials", "Contract Law Essentials"),
    _course_candidate("negligence", "course-tort-foundations", "Tort Foundations"),
    _course_candidate("land_registration", "course-property-practice", "Property Practice"),
    _course_candidate("commercial_drafting", "course-commercial-drafting", "Commercial Drafting"),
    _course_candidate("data_protection", "course-data-protection", "Data Protection in Practice"),
    _course_candidate("evidence_admissibility", "course-evidence-advanced", "Advanced Evidence"),
    _course_candidate("trusts_formation", "course-trusts-core", "Trusts Core"),
    _course_candidate("civil_procedure", "course-civil-procedure", "Civil Procedure"),
    _course_candidate("misrepresentation", "course-misrepresentation", "Misrepresentation"),
    _course_candidate("professional_conduct", "course-conduct", "Professional Conduct"),
    # Deliberately unresolvable: unknown course id -> must be removed.
    _course_candidate("negligence", "course-does-not-exist", "Ghost Course"),
    # Deliberately unresolvable: unknown lesson id -> must be removed.
    {
        "topic_tag": "land_registration",
        "recommendation_type": "lesson",
        "course_id": "course-property-practice",
        "lesson_id": "lesson-lr-99",
        "title": "Lesson that does not exist",
    },
)

ENROLMENTS: tuple[dict[str, Any], ...] = (
    {
        "user_id": LEARNER,
        "course_id": "course-contract-essentials",
        "enrolled_at": _at(-1440),
        "completion_percentage": 35,
    },
)


# ---------------------------------------------------------------------------
# Scenario composition
# ---------------------------------------------------------------------------

PROFILE_WITH_UNEXPLORED: dict[str, Any] = {
    "user_id": LEARNER,
    "speciality_areas": (
        "contract_formation",
        "negligence",
        "commercial_drafting",
        "data_protection",
    ),
    "speciality_status": SourceStatus.AVAILABLE.value,
    "naric_level": "LEVEL_6",
    "naric_level_source": "retrieved",
}

PROFILE_FULLY_COVERED: dict[str, Any] = {
    "user_id": LEARNER,
    "speciality_areas": ("contract_formation", "negligence"),
    "speciality_status": SourceStatus.AVAILABLE.value,
    "naric_level": "LEVEL_6",
    "naric_level_source": "retrieved",
}

PROFILE_PARTIAL: dict[str, Any] = {
    "user_id": LEARNER,
    "speciality_areas": ("contract_formation", "data_protection"),
    "speciality_status": SourceStatus.PARTIAL.value,
    "naric_level": "LEVEL_6",
    "naric_level_source": "retrieved",
}

PROFILE_NO_SPECIALITY: dict[str, Any] = {
    "user_id": LEARNER,
    "speciality_areas": (),
    "speciality_status": SourceStatus.EMPTY.value,
    "naric_level": "LEVEL_5",
    "naric_level_source": "default",
}


@dataclass(frozen=True, slots=True)
class MockScenario:
    """A complete, deterministic four-source fixture."""

    name: str
    user_id: str
    interactions: dict[str, MockInteractionPayload]
    feedback: MockFeedbackPayload
    profiles: dict[str, MockProfilePayload]
    courses: MockCoursesPayload

    def with_interactions(self, payload: MockInteractionPayload) -> "MockScenario":
        return replace(self, interactions={self.user_id: payload})


def _interactions(
    records: tuple[dict[str, Any], ...],
    *,
    status: str = SourceStatus.AVAILABLE.value,
    failure: str | None = None,
    user_id: str = LEARNER,
) -> dict[str, MockInteractionPayload]:
    return {
        user_id: MockInteractionPayload(
            records=records, status=status, failure=failure
        )
    }


def _profiles(
    profile: dict[str, Any] | None = None,
    *,
    failure: str | None = None,
    user_id: str = LEARNER,
) -> dict[str, MockProfilePayload]:
    return {user_id: MockProfilePayload(profile=profile, failure=failure)}


def _courses(
    *,
    status: str = SourceStatus.AVAILABLE.value,
    failure: str | None = None,
    catalogue: tuple[dict[str, Any], ...] = CATALOGUE,
    candidates: tuple[dict[str, Any], ...] = CANDIDATES,
    enrolments: tuple[dict[str, Any], ...] = ENROLMENTS,
) -> MockCoursesPayload:
    return MockCoursesPayload(
        catalogue=catalogue,
        candidates=candidates,
        enrolments=enrolments,
        status=status,
        failure=failure,
    )


def scenario(
    name: str,
    *,
    records: tuple[dict[str, Any], ...] | None = None,
    interaction_status: str = SourceStatus.AVAILABLE.value,
    interaction_failure: str | None = None,
    feedback_records: tuple[dict[str, Any], ...] | None = None,
    feedback_status: str = SourceStatus.AVAILABLE.value,
    feedback_failure: str | None = None,
    profile: dict[str, Any] | None = PROFILE_WITH_UNEXPLORED,
    profile_failure: str | None = None,
    courses_status: str = SourceStatus.AVAILABLE.value,
    courses_failure: str | None = None,
    courses_candidates: tuple[dict[str, Any], ...] = CANDIDATES,
    courses_enrolments: tuple[dict[str, Any], ...] = ENROLMENTS,
    user_id: str = LEARNER,
) -> MockScenario:
    """Compose a scenario from the building blocks above."""
    resolved_records = STRUGGLE_MIXED_RECORDS if records is None else records
    resolved_feedback = (
        STRUGGLE_MIXED_FEEDBACK if feedback_records is None else feedback_records
    )
    return MockScenario(
        name=name,
        user_id=user_id,
        interactions=_interactions(
            resolved_records,
            status=interaction_status,
            failure=interaction_failure,
            user_id=user_id,
        ),
        feedback=MockFeedbackPayload(
            records=resolved_feedback,
            status=feedback_status,
            failure=feedback_failure,
        ),
        profiles=_profiles(profile, failure=profile_failure, user_id=user_id),
        courses=_courses(
            status=courses_status,
            failure=courses_failure,
            candidates=courses_candidates,
            enrolments=courses_enrolments,
        ),
    )


def _count_scenario(count: int) -> MockScenario:
    return scenario(
        f"count_{count}",
        records=sequence_records(count),
        feedback_records=sequence_feedback(count),
        interaction_status=(
            SourceStatus.EMPTY.value if count == 0 else SourceStatus.AVAILABLE.value
        ),
        feedback_status=(
            SourceStatus.EMPTY.value if count == 0 else SourceStatus.AVAILABLE.value
        ),
    )


SCENARIOS: dict[str, MockScenario] = {
    # -- threshold matrix --------------------------------------------------
    "count_0": _count_scenario(0),
    "count_5": _count_scenario(5),
    "count_9": _count_scenario(9),
    "count_10": _count_scenario(10),
    "count_11": _count_scenario(11),
    "count_50": _count_scenario(50),
    # -- aggregation / topic shape ----------------------------------------
    "struggle_mixed": scenario("struggle_mixed"),
    "diverse_topics": scenario(
        "diverse_topics",
        records=DIVERSE_TOPIC_RECORDS,
        feedback_records=(),
        feedback_status=SourceStatus.EMPTY.value,
    ),
    "narrow_topics": scenario(
        "narrow_topics",
        records=NARROW_TOPIC_RECORDS,
        feedback_records=(),
        feedback_status=SourceStatus.EMPTY.value,
        profile=PROFILE_FULLY_COVERED,
    ),
    "heavy_explain_differently": scenario(
        "heavy_explain_differently",
        records=HEAVY_EXPLAIN_RECORDS,
        feedback_records=(),
        feedback_status=SourceStatus.EMPTY.value,
        profile=PROFILE_NO_SPECIALITY,
    ),
    "heavy_follow_ups": scenario(
        "heavy_follow_ups",
        records=HEAVY_FOLLOW_UP_RECORDS,
        feedback_records=(),
        feedback_status=SourceStatus.EMPTY.value,
        profile=PROFILE_NO_SPECIALITY,
    ),
    "duplicate_interaction_ids": scenario(
        "duplicate_interaction_ids",
        records=DUPLICATE_ID_RECORDS,
        feedback_records=sequence_feedback(10),
    ),
    "mixed_owner_records": scenario(
        "mixed_owner_records",
        records=MIXED_OWNER_RECORDS,
        feedback_records=sequence_feedback(10),
    ),
    # -- interaction source degradation -----------------------------------
    "interactions_unavailable": scenario(
        "interactions_unavailable",
        interaction_failure="unavailable",
        interaction_status=SourceStatus.UNAVAILABLE.value,
    ),
    "interactions_timeout": scenario(
        "interactions_timeout",
        interaction_failure="timeout",
        interaction_status=SourceStatus.UNAVAILABLE.value,
    ),
    "interactions_invalid": scenario(
        "interactions_invalid",
        records=INVALID_RECORDS,
    ),
    "interactions_partial": scenario(
        "interactions_partial",
        interaction_status=SourceStatus.PARTIAL.value,
    ),
    # -- feedback source variants -----------------------------------------
    "feedback_empty": scenario(
        "feedback_empty",
        feedback_records=(),
        feedback_status=SourceStatus.EMPTY.value,
    ),
    "feedback_unavailable": scenario(
        "feedback_unavailable",
        feedback_failure="unavailable",
        feedback_status=SourceStatus.UNAVAILABLE.value,
    ),
    "feedback_partial": scenario(
        "feedback_partial",
        feedback_records=STRUGGLE_MIXED_FEEDBACK[:1],
        feedback_status=SourceStatus.PARTIAL.value,
    ),
    "feedback_invalid": scenario(
        "feedback_invalid",
        feedback_failure="invalid",
        feedback_status=SourceStatus.INVALID.value,
    ),
    # -- profile source variants ------------------------------------------
    "profile_fully_covered": scenario(
        "profile_fully_covered", profile=PROFILE_FULLY_COVERED
    ),
    "profile_no_speciality": scenario(
        "profile_no_speciality", profile=PROFILE_NO_SPECIALITY
    ),
    "profile_partial": scenario("profile_partial", profile=PROFILE_PARTIAL),
    "profile_unavailable": scenario(
        "profile_unavailable", profile=None, profile_failure="unavailable"
    ),
    "profile_invalid": scenario(
        "profile_invalid", profile=None, profile_failure="invalid"
    ),
    # -- courses source variants ------------------------------------------
    "courses_unavailable": scenario(
        "courses_unavailable",
        courses_failure="unavailable",
        courses_status=SourceStatus.UNAVAILABLE.value,
    ),
    "courses_partial": scenario(
        "courses_partial", courses_status=SourceStatus.PARTIAL.value
    ),
    "courses_invalid": scenario(
        "courses_invalid",
        courses_failure="invalid",
        courses_status=SourceStatus.INVALID.value,
    ),
    "courses_not_enrolled": scenario("courses_not_enrolled", courses_enrolments=()),
    "courses_only_invalid_candidates": scenario(
        "courses_only_invalid_candidates",
        courses_candidates=(
            _course_candidate("contract_formation", "course-missing", "Ghost"),
            {
                "topic_tag": "negligence",
                "recommendation_type": "lesson",
                "course_id": "course-tort-foundations",
                "lesson_id": "lesson-ng-99",
                "title": "Ghost lesson",
            },
        ),
    ),
}

DEFAULT_SCENARIO = "struggle_mixed"


def get_scenario(name: str) -> MockScenario:
    """Look up a scenario by name, failing loudly on an unknown name."""
    try:
        return SCENARIOS[name]
    except KeyError as exc:
        known = ", ".join(sorted(SCENARIOS))
        raise KeyError(
            f"unknown mock scenario '{name}'; known scenarios: {known}"
        ) from exc
