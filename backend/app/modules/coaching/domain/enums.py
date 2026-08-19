"""The UC-07 state model (§15, §17, §19, §27).

Five small vocabularies, each answering one question a learner-facing application will ask.

**May this learner be coached on this question?** ``EligibilityCode`` — one code per reason, so a
refusal can be rendered without the frontend guessing (§9, §27).

**What is the coach doing?** ``CoachingMode`` — Socratic by default; direct explanation only after
the learner is offered the choice and takes it (§15, §16).

**Where is this session in its life?** ``CoachingSessionStatus`` — the four states §17 names and
not one more.

**Who said this?** ``MessageRole``.

**How far through the wrong answers are we?** ``ReviewItemStatus`` (§19).

Vocabularies only. The primitives that *parse* them — ``parse_enum``, ``enum_values`` — belong to
the shared kernel (``app.core.coercion``), so every capability agrees on what "  active " means.
"""

from __future__ import annotations

from enum import StrEnum


class CoachingMode(StrEnum):
    """How the coach is currently teaching (§14, §15, §16)."""

    #: The default and the only mode a session may start in. Guiding questions, progressive hints,
    #: no answer handed over (§14).
    SOCRATIC = "SOCRATIC"
    #: A direct explanation of the underlying concept, offered only after the exchange threshold
    #: and only when the learner asks for it (§15, §16). Still bound by answer-key isolation: the
    #: coach explains the concept it has been reasoning about, because it has never had anything
    #: else to explain.
    DIRECT_EXPLANATION = "DIRECT_EXPLANATION"


class CoachingSessionStatus(StrEnum):
    """The coaching session lifecycle (§17).

    ``UNAVAILABLE`` is not a synonym for "the AI is down". It is the state of a session record
    that exists but has no coaching in it yet, because the model could not be reached when it was
    opened. The record exists so a retry resumes *this* session instead of opening a second one
    (§28, §30) — the state is what makes idempotent recovery possible rather than a spare label.
    """

    ACTIVE = "ACTIVE"
    #: The learner finished with this question — explicitly, or by advancing through the review
    #: queue (§19).
    COMPLETED = "COMPLETED"
    #: Repeated AI failure has parked the session. Retriable; nothing about the quiz is affected.
    FAILED = "FAILED"
    #: Opened, but the coach has not spoken yet. Retry moves it to ACTIVE.
    UNAVAILABLE = "UNAVAILABLE"


#: Statuses in which a session will accept a learner message.
LIVE_SESSION_STATUSES: frozenset[CoachingSessionStatus] = frozenset({CoachingSessionStatus.ACTIVE})

#: Statuses a retry may act on (§28).
RETRIABLE_SESSION_STATUSES: frozenset[CoachingSessionStatus] = frozenset(
    {CoachingSessionStatus.FAILED, CoachingSessionStatus.UNAVAILABLE, CoachingSessionStatus.ACTIVE}
)


class MessageRole(StrEnum):
    """Who produced one turn of the conversation.

    There is no SYSTEM role. The coaching policy is not a conversation turn — it is assembled by
    ``app.modules.coaching.prompts`` at request time and is never stored in a learner's transcript,
    so it cannot be edited, replayed or injected through the message history (§25).
    """

    LEARNER = "LEARNER"
    COACH = "COACH"


class EligibilityCode(StrEnum):
    """Why coaching is or is not available for a question (§9, §27, §29).

    The order of the members is the order the authorisation checks run in, and that order is
    deliberate: ownership is decided before existence details are revealed, so probing another
    learner's attempt cannot be used to discover which questions they got wrong.
    """

    ELIGIBLE = "ELIGIBLE"
    #: UC-03 has no such attempt.
    ATTEMPT_NOT_FOUND = "ATTEMPT_NOT_FOUND"
    #: The attempt exists but belongs to someone else (§9).
    NOT_ATTEMPT_OWNER = "NOT_ATTEMPT_OWNER"
    #: The quiz is not over. This is the active-quiz protection (§7, §8).
    ATTEMPT_NOT_SUBMITTED = "ATTEMPT_NOT_SUBMITTED"
    #: UC-04 has no confirmed scoring result, so nothing is known to be incorrect yet.
    SCORE_NOT_CONFIRMED = "SCORE_NOT_CONFIRMED"
    #: UC-06 has not released the feedback report (§7).
    FEEDBACK_UNAVAILABLE = "FEEDBACK_UNAVAILABLE"
    #: The question is not part of this attempt (§9, §20).
    QUESTION_NOT_IN_ATTEMPT = "QUESTION_NOT_IN_ATTEMPT"
    #: The question was not answered incorrectly, so there is nothing to coach (§9, §20).
    QUESTION_NOT_INCORRECT = "QUESTION_NOT_INCORRECT"
    #: The coaching service itself cannot be reached (§27).
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


#: Refusals caused by the state of the world rather than by the request. A frontend can retry these
#: later; the others will never become eligible for this learner and question.
TRANSIENT_ELIGIBILITY_CODES: frozenset[EligibilityCode] = frozenset(
    {
        EligibilityCode.SCORE_NOT_CONFIRMED,
        EligibilityCode.FEEDBACK_UNAVAILABLE,
        EligibilityCode.SERVICE_UNAVAILABLE,
    }
)


class ReviewItemStatus(StrEnum):
    """Where one incorrect question sits in the review-all-wrong-answers queue (§19)."""

    #: Not started. The next PENDING item is what "move to next question" returns.
    PENDING = "PENDING"
    #: A coaching session is open on it.
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class SessionOutcome(StrEnum):
    """What one call to "start coaching" actually did (§30).

    ``RESUMED`` is what makes starting idempotent: a learner who reloads, double-taps, or comes
    back tomorrow re-enters the same conversation instead of opening a second one.
    """

    STARTED = "STARTED"
    RESUMED = "RESUMED"
    #: The session record exists but the coach could not speak. Nothing was invented (§6, §27).
    UNAVAILABLE = "UNAVAILABLE"


class ExchangeOutcome(StrEnum):
    """What one call to "send a coaching message" actually did (§27, §28)."""

    #: The coach replied. The exchange counted.
    COMPLETED = "COMPLETED"
    #: The AI service could not be reached or timed out. The exchange did **not** count, no
    #: message was stored, and no text was invented (§6, §27).
    UNAVAILABLE = "UNAVAILABLE"
