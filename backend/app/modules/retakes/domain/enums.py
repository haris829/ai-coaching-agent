"""Enumerations owned by UC-08.

``StrEnum`` members compare equal to their string value, so they serialise straight into JSON and
into the ``String`` columns the company adapter will map them onto. String columns with ``CHECK``
constraints are preferred over native database enum types for the same reason UC-03 gives: they
are portable and can be extended without a type migration.

Attempt statuses, question types and pass/fail statuses are *not* redefined here. They belong to
UC-03, UC-02 and UC-05 respectively and are consumed through the ports in ``integration``.
"""

from __future__ import annotations

from enum import StrEnum


class RetakeState(StrEnum):
    """The state UC-08 exposes for "may this learner retake this quiz?".

    The business layer answers this; the user-facing layer renders it. A frontend never computes
    eligibility from an attempt count it holds, because it cannot see grants, in-flight
    reservations or the configuration version locked to the learner's history.
    """

    #: A retake can be created out of the learner's configured allowance.
    ELIGIBLE = "ELIGIBLE"
    #: A retake can be created, but only because an administrator granted an extra attempt.
    #: Distinguished from ELIGIBLE so an interface can say *why* the attempt exists.
    ADDITIONAL_ATTEMPT_AVAILABLE = "ADDITIONAL_ATTEMPT_AVAILABLE"
    #: The allowance is spent. Nothing is wrong; there are simply no attempts left.
    EXHAUSTED = "EXHAUSTED"
    #: The request cannot be fulfilled at the moment for a reason other than the allowance —
    #: no completed attempt to retake, an attempt still open, a withdrawn quiz, an upstream
    #: module that could not be read.
    UNAVAILABLE = "UNAVAILABLE"


class RetakeBlockerCode(StrEnum):
    """Why a retake is not currently available. Several may apply at once."""

    NO_ATTEMPTS_REMAINING = "NO_ATTEMPTS_REMAINING"
    NO_COMPLETED_ATTEMPT = "NO_COMPLETED_ATTEMPT"
    PREVIOUS_ATTEMPT_NOT_COMPLETE = "PREVIOUS_ATTEMPT_NOT_COMPLETE"
    ATTEMPT_IN_PROGRESS = "ATTEMPT_IN_PROGRESS"
    RETAKE_IN_PROGRESS = "RETAKE_IN_PROGRESS"
    QUIZ_NOT_AVAILABLE = "QUIZ_NOT_AVAILABLE"
    CONFIGURATION_UNAVAILABLE = "CONFIGURATION_UNAVAILABLE"
    QUESTION_BANK_UNAVAILABLE = "QUESTION_BANK_UNAVAILABLE"
    INSUFFICIENT_QUESTIONS = "INSUFFICIENT_QUESTIONS"
    LEARNER_UNKNOWN = "LEARNER_UNKNOWN"
    COURSE_UNKNOWN = "COURSE_UNKNOWN"


class RetakeRequestStatus(StrEnum):
    """Lifecycle of the record UC-08 writes *before* asking UC-03 to create an attempt.

    The record is the attempt-slot reservation. It exists so that the allowance check and the
    attempt creation cannot be separated by a concurrent request.
    """

    #: The slot is held. Counts against the allowance even though UC-03 has no attempt yet.
    RESERVED = "RESERVED"
    #: UC-03 created the attempt. From here the attempt itself is what counts.
    COMPLETED = "COMPLETED"
    #: Creation failed. The slot is released, and the same request may be retried safely.
    FAILED = "FAILED"


class GrantStatus(StrEnum):
    """Lifecycle of an administrator's additional-attempt grant."""

    ACTIVE = "ACTIVE"
    #: Withdrawn by an administrator. Retained, never deleted, so the audit trail survives.
    REVOKED = "REVOKED"


class ExclusionScope(StrEnum):
    """How much of the learner's history a retake's question selection excluded.

    Recorded on the retake so "why did I see that question again?" has an answer that does not
    require re-deriving the selection.
    """

    #: Every question the learner has ever been delivered for this quiz was excluded.
    ALL_PREVIOUS_ATTEMPTS = "ALL_PREVIOUS_ATTEMPTS"
    #: Only the immediately preceding attempt's questions were excluded — excluding the whole
    #: history would not have left enough eligible questions.
    PREVIOUS_ATTEMPT_ONLY = "PREVIOUS_ATTEMPT_ONLY"
    #: Nothing could be excluded; the bank is too small to avoid reuse.
    NONE = "NONE"


class QuestionReuseReason(StrEnum):
    """Why a retake was allowed to reuse questions the learner has already seen."""

    #: Excluding previously-seen questions would leave fewer than the configured count.
    INSUFFICIENT_UNUSED_QUESTIONS = "INSUFFICIENT_UNUSED_QUESTIONS"
    #: Enough questions overall, but not enough of a type the configuration requires.
    INSUFFICIENT_UNUSED_QUESTIONS_OF_TYPE = "INSUFFICIENT_UNUSED_QUESTIONS_OF_TYPE"


class ConfigurationVersionSource(StrEnum):
    """Which UC-01 configuration version the retake locked, and how it was chosen.

    UC-08 never *changes* a version: it resolves one and records the resolution, so a retake can
    never switch version by accident (see ``services.retake_service``).
    """

    #: The active version is the same one the previous attempt ran under.
    CARRIED_FORWARD = "CARRIED_FORWARD"
    #: UC-01 has published a newer active version since the previous attempt; the retake is a new
    #: attempt and locks it, exactly as UC-03 does for any attempt.
    ADVANCED_TO_ACTIVE = "ADVANCED_TO_ACTIVE"
    #: The deployment is configured to pin retakes to the previous attempt's version.
    PINNED_TO_PREVIOUS = "PINNED_TO_PREVIOUS"


class RetakeAnomalyCode(StrEnum):
    """Conditions worth recording on a retake that are not failures.

    A retake that has already been created must never be destroyed to report a problem with it —
    that would violate the immutability the whole module rests on. These are recorded instead.
    """

    #: Questions the learner had already seen were delivered again, unavoidably.
    QUESTION_REUSE_UNAVOIDABLE = "QUESTION_REUSE_UNAVOIDABLE"
    #: Alternatives existed, yet the delivered set is not meaningfully different.
    QUESTION_SET_NOT_MEANINGFULLY_DIFFERENT = "QUESTION_SET_NOT_MEANINGFULLY_DIFFERENT"
    #: UC-03 delivered a different configuration version than UC-08 resolved.
    CONFIGURATION_VERSION_MISMATCH = "CONFIGURATION_VERSION_MISMATCH"
    #: UC-03 delivered a different attempt number than the reservation held.
    ATTEMPT_NUMBER_MISMATCH = "ATTEMPT_NUMBER_MISMATCH"
    #: The locked maximum attempts is not a usable positive integer.
    INVALID_ATTEMPT_ALLOWANCE = "INVALID_ATTEMPT_ALLOWANCE"


class AnomalySeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
