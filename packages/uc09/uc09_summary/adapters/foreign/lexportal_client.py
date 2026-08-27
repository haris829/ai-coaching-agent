"""A fictional upstream, deliberately unlike the mocks in every respect.

"LexPortal" exists to prove replaceability. Nothing about its shape resembles
the mock adapters or the domain models:

* different field names - ``ref``, ``prompt``, ``shortForm``, ``headline``
* different nesting - everything under ``payload`` / ``result`` envelopes
* different value representations - epoch milliseconds instead of ISO
  datetimes, ``RQF-7`` instead of ``level_7``, ``FINISHED`` instead of
  ``completed``, a 0..1 ratio instead of an integer percentage, ``STATUTE`` and
  ``JUDGMENT`` instead of the platform resource kinds
* different identifier namespace - ``LP-SESS-0001``, not ``sess-...``
* its own error type, carrying its own hostnames and status codes

It is in-process and deterministic: no URL is called, because inventing an
external API is exactly what this component must not do. It stands in for the
payload an unseen real system would send, so that the swap can be exercised
rather than asserted.
"""

from __future__ import annotations

from typing import Any


class LexPortalError(Exception):
    """Upstream error type. Its text must never escape the adapter boundary."""

    def __init__(self, message: str, *, status_code: int = 500) -> None:
        self.status_code = status_code
        super().__init__(message)


class LexPortalTimeout(LexPortalError):
    """Upstream deadline exceeded."""


# -- identifiers used by the foreign fixtures ------------------------------

SESSION_OK = "LP-SESS-0001"
SESSION_SINGLE_TOPIC = "LP-SESS-0002"
SESSION_NO_AUTHORITIES = "LP-SESS-0003"
SESSION_BAD_TIER = "LP-SESS-0004"
SESSION_LIVE = "LP-SESS-0005"
SESSION_DOWN = "LP-SESS-0666"
SESSION_SLOW = "LP-SESS-0777"
SESSION_ABSENT = "LP-SESS-9999"

LEARNER_OK = "LP-USER-77"
LEARNER_NO_RECOMMENDATIONS = "LP-USER-78"
LEARNER_DOWN = "LP-USER-666"
LEARNER_SLOW = "LP-USER-777"

_H = 3_600_000
_M = 60_000
#: 2026-03-04T09:00:00Z in epoch milliseconds.
_BASE_MS = 1_772_614_800_000


def _ms(minutes: int) -> int:
    return _BASE_MS + minutes * _M


_SESSIONS: dict[str, dict[str, Any]] = {
    SESSION_OK: {
        "payload": {
            "ref": SESSION_OK,
            "learner": {"ref": LEARNER_OK, "fullName": "Amara Osei"},
            "window": {"openedAtEpochMs": _ms(0), "closedAtEpochMs": _ms(47)},
            "lifecycle": "FINISHED",
            "academicTier": {"code": "RQF-7", "origin": "LOOKUP"},
            "programme": {"name": "Employment Law Practice", "progressRatio": 0.62},
        }
    },
    SESSION_SINGLE_TOPIC: {
        "payload": {
            "ref": SESSION_SINGLE_TOPIC,
            "learner": {"ref": LEARNER_OK, "fullName": "Amara Osei"},
            "window": {"openedAtEpochMs": _ms(0), "closedAtEpochMs": _ms(31)},
            "lifecycle": "FINISHED",
            "academicTier": {"code": "RQF-6", "origin": "LOOKUP"},
            "programme": {"name": "Employment Law Practice", "progressRatio": 0.4},
        }
    },
    SESSION_NO_AUTHORITIES: {
        "payload": {
            "ref": SESSION_NO_AUTHORITIES,
            "learner": {"ref": LEARNER_NO_RECOMMENDATIONS, "fullName": "Rhys Lloyd"},
            "window": {"openedAtEpochMs": _ms(0), "closedAtEpochMs": _ms(22)},
            "lifecycle": "FINISHED",
            "academicTier": {"code": "RQF-3", "origin": "DEFAULTED"},
            "programme": {"name": "Employment Law Practice", "progressRatio": 0.11},
        }
    },
    SESSION_BAD_TIER: {
        "payload": {
            "ref": SESSION_BAD_TIER,
            "learner": {"ref": LEARNER_OK, "fullName": "Amara Osei"},
            "window": {"openedAtEpochMs": _ms(0), "closedAtEpochMs": _ms(15)},
            "lifecycle": "FINISHED",
            # Maps to no platform level. Must become the default, marked invalid.
            "academicTier": {"code": "GRADE-XI", "origin": "LOOKUP"},
            "programme": {"name": "Employment Law Practice", "progressRatio": 0.5},
        }
    },
    SESSION_LIVE: {
        "payload": {
            "ref": SESSION_LIVE,
            "learner": {"ref": LEARNER_OK, "fullName": "Amara Osei"},
            "window": {"openedAtEpochMs": _ms(0), "closedAtEpochMs": None},
            "lifecycle": "LIVE",
            "academicTier": {"code": "RQF-7+", "origin": "LOOKUP"},
            "programme": {"name": "Employment Law Practice", "progressRatio": 0.62},
        }
    },
}

_INTERACTIONS: dict[str, list[dict[str, Any]]] = {
    SESSION_OK: [
        {
            "uid": "LP-INT-1",
            "atEpochMs": _ms(2),
            "prompt": "When does a dismissal become automatically unfair?",
            "labels": {
                "subjects": ["UNFAIR_DISMISSAL"],
                "ideas": ["AUTOMATICALLY_UNFAIR_REASONS", "QUALIFYING_PERIOD"],
            },
        },
        {
            "uid": "LP-INT-2",
            "atEpochMs": _ms(9),
            "prompt": "How is the basic award calculated?",
            "labels": {
                "subjects": ["UNFAIR_DISMISSAL", "REMEDIES"],
                "ideas": ["BASIC_AWARD_CALCULATION"],
            },
        },
        {
            "uid": "LP-INT-3",
            "atEpochMs": _ms(18),
            "prompt": "What is the band of reasonable responses?",
            "labels": {
                "subjects": ["UNFAIR_DISMISSAL"],
                "ideas": ["BAND_OF_REASONABLE_RESPONSES"],
            },
        },
        {
            "uid": "LP-INT-4",
            "atEpochMs": _ms(41),
            "prompt": "What counts as a protected disclosure?",
            "labels": {
                "subjects": ["WHISTLEBLOWING"],
                "ideas": ["PROTECTED_DISCLOSURE"],
            },
        },
    ],
    SESSION_SINGLE_TOPIC: [
        {
            "uid": "LP-INT-11",
            "atEpochMs": _ms(3),
            "prompt": "What makes a restrictive covenant enforceable?",
            "labels": {
                "subjects": ["RESTRICTIVE_COVENANTS"],
                "ideas": ["LEGITIMATE_BUSINESS_INTEREST"],
            },
        },
        {
            "uid": "LP-INT-12",
            "atEpochMs": _ms(11),
            "prompt": "How is reasonableness of scope assessed?",
            "labels": {
                "subjects": ["RESTRICTIVE_COVENANTS"],
                "ideas": ["REASONABLENESS_OF_SCOPE"],
            },
        },
        {
            "uid": "LP-INT-13",
            "atEpochMs": _ms(19),
            "prompt": "Can an unreasonable clause be severed?",
            "labels": {
                "subjects": ["RESTRICTIVE_COVENANTS"],
                "ideas": ["SEVERANCE_OF_CLAUSES"],
            },
        },
    ],
    SESSION_NO_AUTHORITIES: [
        {
            "uid": "LP-INT-21",
            "atEpochMs": _ms(2),
            "prompt": "How should I structure a grievance meeting?",
            "labels": {
                "subjects": ["GRIEVANCE_PROCEDURE"],
                "ideas": ["MEETING_STRUCTURE", "RECORD_KEEPING"],
            },
        },
        {
            "uid": "LP-INT-22",
            "atEpochMs": _ms(12),
            "prompt": "Who should chair the meeting?",
            "labels": {
                "subjects": ["GRIEVANCE_PROCEDURE"],
                "ideas": ["IMPARTIAL_CHAIR"],
            },
        },
    ],
    SESSION_BAD_TIER: [
        {
            "uid": "LP-INT-31",
            "atEpochMs": _ms(4),
            "prompt": "What is the qualifying period?",
            "labels": {
                "subjects": ["UNFAIR_DISMISSAL"],
                "ideas": ["QUALIFYING_PERIOD"],
            },
        },
    ],
}
_INTERACTIONS[SESSION_LIVE] = _INTERACTIONS[SESSION_OK]

_AUTHORITIES: dict[str, list[dict[str, Any]]] = {
    SESSION_OK: [
        {
            "key": "UK.ERA1996.S98",
            "class": "STATUTE",
            "shortForm": "Employment Rights Act 1996, s 98",
            "longForm": "Employment Rights Act 1996, section 98",
            "seenIn": ["LP-INT-1", "LP-INT-3"],
            "firstSeenEpochMs": _ms(2),
        },
        {
            "key": "UK.ICELAND.1983",
            "class": "JUDGMENT",
            "shortForm": "Iceland Frozen Foods Ltd v Jones [1983] ICR 17",
            "longForm": "Iceland Frozen Foods Ltd v Jones",
            "seenIn": ["LP-INT-3"],
            "firstSeenEpochMs": _ms(18),
        },
    ],
    SESSION_SINGLE_TOPIC: [
        {
            "key": "UK.TILLMAN.2019",
            "class": "JUDGMENT",
            "shortForm": "Tillman v Egon Zehnder Ltd [2019] UKSC 32",
            "longForm": "Tillman v Egon Zehnder Ltd",
            "seenIn": ["LP-INT-13"],
            "firstSeenEpochMs": _ms(19),
        },
    ],
    # Nothing cited. An empty list, which is not the same as being down.
    SESSION_NO_AUTHORITIES: [],
    SESSION_BAD_TIER: [],
}
_AUTHORITIES[SESSION_LIVE] = _AUTHORITIES[SESSION_OK]

_RECOMMENDATIONS: dict[str, dict[str, Any]] = {
    LEARNER_OK: {
        "result": {
            "recommendations": [
                {
                    "code": "LP-REC-TUPE",
                    "headline": "TUPE: transfer of undertakings",
                    "because": "Not yet covered at the expected depth.",
                },
                {
                    "code": "LP-REC-DISCRIM",
                    "headline": "Discrimination remedies",
                    "because": "Partially covered previously.",
                },
            ]
        }
    },
    LEARNER_NO_RECOMMENDATIONS: {"result": {"recommendations": []}},
}


class LexPortalClient:
    """In-process stand-in for the fictional upstream."""

    def fetch_session(self, ref: str) -> dict[str, Any]:
        self._guard(ref, SESSION_DOWN, SESSION_SLOW)
        if ref not in _SESSIONS:
            raise LexPortalError(
                f"lexportal-eu-west-2: 404 no session with ref {ref}", status_code=404
            )
        return _SESSIONS[ref]

    def fetch_transcript(self, ref: str) -> dict[str, Any]:
        self._guard(ref, SESSION_DOWN, SESSION_SLOW)
        return {"payload": {"items": _INTERACTIONS.get(ref, [])}}

    def fetch_authorities(self, ref: str) -> dict[str, Any]:
        self._guard(ref, SESSION_DOWN, SESSION_SLOW)
        return {"payload": {"authorities": _AUTHORITIES.get(ref, [])}}

    def fetch_recommendations(self, learner_ref: str) -> dict[str, Any] | None:
        self._guard(learner_ref, LEARNER_DOWN, LEARNER_SLOW)
        return _RECOMMENDATIONS.get(learner_ref)

    @staticmethod
    def _guard(ref: str, down: str, slow: str) -> None:
        if ref == down:
            raise LexPortalError(
                "lexportal-eu-west-2: 503 upstream cluster unavailable", status_code=503
            )
        if ref == slow:
            raise LexPortalTimeout(
                "lexportal-eu-west-2: read timeout after 5000ms", status_code=504
            )
