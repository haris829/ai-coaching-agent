"""Explicit UC-01 enumerations.

Every mode / status / source value used anywhere in UC-01 comes from this module.
Arbitrary strings are never scattered through the codebase.
"""

from __future__ import annotations

from enum import Enum


class SessionMode(str, Enum):
    """The three coaching session modes supported by UC-01."""

    FREE_FORM = "free-form"
    COURSE_LINKED = "course-linked"
    CASE_LINKED = "case-linked"

    @classmethod
    def parse(cls, raw: object) -> SessionMode:
        """Strict parse used by the backend.

        Raises ``ValueError`` for anything that is not one of the three modes, so an
        unknown client-supplied string can never flow further into the system.
        """
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            for member in cls:
                if member.value == raw:
                    return member
        raise ValueError(f"unsupported session mode: {raw!r}")


class SessionStatus(str, Enum):
    """Lifecycle state of a session-open attempt."""

    INITIALIZING = "initializing"
    ACTIVE = "active"
    DEGRADED = "degraded"
    FAILED = "failed"


class NaricLevelSource(str, Enum):
    """Where the explanation level actually came from.

    ``NARIC`` is only ever used when a NARIC assessment genuinely supplied the level.
    Everything else is explicitly marked as not-from-NARIC.
    """

    NARIC = "naric"
    DEFAULT = "default"
    DEFAULT_USER_ACKNOWLEDGED = "default_user_acknowledged"


class DependencyName(str, Enum):
    """External dependencies UC-01 talks to, all behind adapters."""

    NARIC = "naric"
    COURSES = "courses"
    CASES = "cases"
    PROFILE = "profile"


class DependencyState(str, Enum):
    """Normalised availability of one external dependency."""

    AVAILABLE = "available"
    EMPTY = "empty"
    """Reachable, but the user has nothing accessible in it."""

    INCOMPLETE = "incomplete"
    """Reachable, but the returned data is not usable as-is (e.g. NARIC calibrating)."""

    UNAVAILABLE = "unavailable"
    """Not reachable, errored, or returned an unusable/invalid payload."""


class NaricAssessmentState(str, Enum):
    """Normalised NARIC assessment states, independent of any upstream payload."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    CALIBRATING = "calibrating"


class LinkedResourceType(str, Enum):
    COURSE = "course"
    CASE_FILE = "case_file"


class DependencyFailurePolicy(str, Enum):
    """What the caller wants to happen if the mode's dependency fails at open time.

    The choice is a client *preference*; the server still decides what is permitted.
    """

    FAIL = "fail"
    """Reject the open attempt (still records the session as failed)."""

    FALLBACK_FREE_FORM = "fallback_free_form"
    """Open a free-form session instead, recorded as degraded with a downgrade note."""


class SessionEventType(str, Enum):
    """UC-01 initiation events. UC-07 / UC-10 event types are deliberately absent."""

    SESSION_INITIALIZING = "session.initializing"
    DEPENDENCY_DEGRADED = "session.dependency_degraded"
    MODE_DOWNGRADED = "session.mode_downgraded"
    SESSION_OPENED = "session.opened"
    SESSION_FAILED = "session.failed"
