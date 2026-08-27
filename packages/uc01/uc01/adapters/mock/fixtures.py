"""Fixture data for the development mocks.

The payload shapes below imitate what an external service might plausibly return
(camelCase keys, nested objects, string levels, ISO timestamps). They are intentionally
*not* UC-01 domain shapes: the adapters normalise them, which is exactly the work a real
adapter will have to do.

Three development users, chosen so that the per-user states the brief requires can be
exercised without any global flag:

======== ====================== ================ =====================
user     NARIC                  courses          case files
======== ====================== ================ =====================
u_alice  complete (level 8)     2 courses        1 case file
u_bob    calibrating            1 course         none  -> case-linked disabled
u_carol  incomplete             none  -> disabled 1 case file
======== ====================== ================ =====================
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# --------------------------------------------------------------------------- #
# Development user directory (stands in for the company auth system)
# --------------------------------------------------------------------------- #

DEV_USERS: Mapping[str, Mapping[str, str]] = {
    "u_alice": {"token": "dev-alice", "label": "Alice (full access)"},
    "u_bob": {"token": "dev-bob", "label": "Bob (no case files)"},
    "u_carol": {"token": "dev-carol", "label": "Carol (no courses, partial profile)"},
}

DEV_TOKEN_TO_USER: Mapping[str, str] = {
    values["token"]: user_id for user_id, values in DEV_USERS.items()
}


# --------------------------------------------------------------------------- #
# "Upstream" NARIC payloads
# --------------------------------------------------------------------------- #

NARIC_PAYLOADS: Mapping[str, Mapping[str, Any]] = {
    "u_alice": {
        "assessmentStatus": "COMPLETED",
        "learner": {"externalId": "u_alice"},
        "result": {"explanationLevel": "8", "assessedAt": "2026-05-02T09:15:00Z"},
    },
    "u_bob": {
        "assessmentStatus": "IN_CALIBRATION",
        "learner": {"externalId": "u_bob"},
        "result": {"explanationLevel": None, "assessedAt": None},
    },
    "u_carol": {
        "assessmentStatus": "PARTIAL",
        "learner": {"externalId": "u_carol"},
        "result": {"explanationLevel": None, "assessedAt": None},
        "missingSections": ["reading-comprehension", "prior-study"],
    },
}

NARIC_SUCCESS_PAYLOAD: Mapping[str, Any] = {
    "assessmentStatus": "COMPLETED",
    "learner": {"externalId": "fixture"},
    "result": {"explanationLevel": "7", "assessedAt": "2026-06-01T12:00:00Z"},
}

NARIC_INCOMPLETE_PAYLOAD: Mapping[str, Any] = {
    "assessmentStatus": "PARTIAL",
    "learner": {"externalId": "fixture"},
    "result": {"explanationLevel": None},
    "missingSections": ["prior-study"],
}

NARIC_CALIBRATING_PAYLOAD: Mapping[str, Any] = {
    "assessmentStatus": "IN_CALIBRATION",
    "learner": {"externalId": "fixture"},
    "result": {"explanationLevel": None},
}

NARIC_INVALID_PAYLOAD: Mapping[str, Any] = {
    # Reachable service, unusable answer: status is unknown and the level is not a
    # number. UC-01 must refuse to trust any part of this.
    "assessmentStatus": "¿QUE?",
    "result": {"explanationLevel": "very high"},
}


# --------------------------------------------------------------------------- #
# "Upstream" Courses Agent payloads
# --------------------------------------------------------------------------- #

COURSE_CATALOGUE: Sequence[Mapping[str, Any]] = (
    {
        "courseId": "crs_contract_law",
        "courseTitle": "Contract Law Foundations",
        "enrolledLearners": ["u_alice"],
        "modules": [
            {"lessonId": "lsn_offer", "lessonTitle": "Offer and Acceptance", "seq": 1},
            {"lessonId": "lsn_consideration", "lessonTitle": "Consideration", "seq": 2},
            {"lessonId": "lsn_terms", "lessonTitle": "Implied Terms", "seq": 3},
        ],
    },
    {
        "courseId": "crs_evidence",
        "courseTitle": "Evidence and Proof",
        "enrolledLearners": ["u_alice"],
        "modules": [
            {"lessonId": "lsn_burden", "lessonTitle": "Burden of Proof", "seq": 1},
            {"lessonId": "lsn_hearsay", "lessonTitle": "Hearsay", "seq": 2},
        ],
    },
    {
        "courseId": "crs_tort",
        "courseTitle": "Tort Law Essentials",
        "enrolledLearners": ["u_bob"],
        "modules": [
            {"lessonId": "lsn_duty", "lessonTitle": "Duty of Care", "seq": 1},
        ],
    },
    {
        "courseId": "crs_no_lessons",
        # A course with no lessons yet: course-linked selection must still demand a
        # lesson, so this course can be listed but not opened.
        "courseTitle": "Advanced Advocacy (coming soon)",
        "enrolledLearners": ["u_alice"],
        "modules": [],
    },
)

COURSES_INVALID_PAYLOAD: Any = {"unexpected": "shape", "courses": "not-a-list"}


# --------------------------------------------------------------------------- #
# "Upstream" Case Prep / Case File payloads
# --------------------------------------------------------------------------- #

CASE_CATALOGUE: Sequence[Mapping[str, Any]] = (
    {
        "caseFileId": "case_alpha",
        "caseName": "Alpha Holdings v. Brookfield",
        "matterRef": "AH-2026-0142",
        "authorisedLearners": ["u_alice"],
    },
    {
        "caseFileId": "case_beta",
        "caseName": "Re: Beta Estate",
        "matterRef": "BE-2026-0077",
        "authorisedLearners": ["u_carol"],
    },
)

CASES_INVALID_PAYLOAD: Any = {"records": [{"noIdHere": True}]}


# --------------------------------------------------------------------------- #
# "Upstream" Profile payloads
# --------------------------------------------------------------------------- #

PROFILE_PAYLOADS: Mapping[str, Mapping[str, Any]] = {
    "u_alice": {
        "id": "u_alice",
        "personal": {"firstName": "Alice", "lastName": "Osei"},
        "prefs": {"language": "en-GB"},
        "progress": {"currentCourseId": "crs_contract_law", "currentLessonId": "lsn_consideration"},
    },
    "u_bob": {
        "id": "u_bob",
        "personal": {"firstName": "Bob", "lastName": "Ryan"},
        "prefs": {"language": "en-GB"},
        "progress": {"currentCourseId": "crs_tort", "currentLessonId": "lsn_duty"},
    },
    "u_carol": {
        # Incomplete profile: no name at all. Must not be invented downstream.
        "id": "u_carol",
        "personal": {},
        "prefs": {},
        "progress": {},
    },
}


__all__ = [
    "CASES_INVALID_PAYLOAD",
    "CASE_CATALOGUE",
    "COURSES_INVALID_PAYLOAD",
    "COURSE_CATALOGUE",
    "DEV_TOKEN_TO_USER",
    "DEV_USERS",
    "NARIC_CALIBRATING_PAYLOAD",
    "NARIC_INCOMPLETE_PAYLOAD",
    "NARIC_INVALID_PAYLOAD",
    "NARIC_PAYLOADS",
    "NARIC_SUCCESS_PAYLOAD",
    "PROFILE_PAYLOADS",
]
