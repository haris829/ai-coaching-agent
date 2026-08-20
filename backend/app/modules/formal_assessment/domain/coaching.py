"""The AI coaching restriction (§7).

    ordinary quiz                      ->  Larry allowed
    formal assessment in progress      ->  Larry forbidden, for this learner, everywhere

WHY THE RESTRICTION IS LEARNER-SCOPED AND NOT ATTEMPT-SCOPED
------------------------------------------------------------
This is the part that a per-attempt check would get wrong, so it is worth being explicit about.

UC-07's existing gate already refuses coaching on an attempt that is not submitted, which means it
already refuses coaching *about* a live formal attempt. What it cannot refuse is the interesting
case: a learner who is sitting a formal assessment on quiz B opens coaching on their submitted,
scored, feedback-released attempt at quiz A. Every one of UC-07's conditions is satisfied. The
learner is also sitting an exam with an AI coach open in another tab.

So UC-09's rule keys on the *learner*: while any formal attempt of theirs is in progress, coaching
is refused regardless of which attempt was asked about. That is what §7's
``is_ai_coaching_allowed(user_id, attempt_id)`` signature implies — the learner is the first
argument for a reason — and it is why this module exposes a check UC-07 calls rather than a flag
UC-07 reads off an attempt.

WHEN THE RESTRICTION LIFTS
--------------------------
The moment the formal attempt is submitted, by the learner or by auto-submission. Coaching on a
formal attempt afterwards is UC-07's business and subject to its own gate; UC-09 stops having an
opinion. A learner who has acknowledged the conditions but not started is *not* yet in an
assessment, so coaching still works — they have not begun, and blocking them would be a restriction
nobody asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.formal_assessment.domain.attempt import FormalAttempt
from app.modules.formal_assessment.domain.enums import CoachingBlockReason


@dataclass(frozen=True, slots=True)
class CoachingPermission:
    """Whether AI coaching may run for this learner right now.

    ``allowed`` is what a caller acts on. The rest is what a caller renders or logs.
    """

    allowed: bool
    reason: CoachingBlockReason | None = None
    message: str | None = None
    #: The formal attempt responsible for the block, so an audit line can name it.
    formal_attempt_id: str | None = None
    quiz_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ai_coaching_allowed": self.allowed,
            "reason": self.reason.value if self.reason else None,
            "message": self.message,
            "formal_attempt_id": self.formal_attempt_id,
            "quiz_id": self.quiz_id,
            **({"details": dict(self.details)} if self.details else {}),
        }


_ALLOWED = CoachingPermission(allowed=True)

_BLOCK_MESSAGE = (
    "AI coaching is not available while a formal assessment is in progress. It will be available "
    "again once the assessment has been submitted."
)


def evaluate_coaching_permission(
    *,
    active_formal_attempts: tuple[FormalAttempt, ...],
    requested_attempt_id: str | None = None,
) -> CoachingPermission:
    """Decide whether coaching may run (§7).

    ``active_formal_attempts`` is every formal attempt of this learner's that is in progress — the
    service fetches them; this function decides. Normally there is at most one, because UC-03
    permits one open attempt per quiz and UC-09 permits one open formal attempt per learner and
    quiz; the signature takes a collection so a learner enrolled on two courses cannot slip through
    a check that only looked at one.

    ``requested_attempt_id`` refines only the *reason*, never the verdict: an in-progress formal
    assessment blocks coaching whether or not it is the attempt the caller asked about.
    """
    in_progress = tuple(item for item in active_formal_attempts if item.in_progress)
    if not in_progress:
        return _ALLOWED

    about_requested = next(
        (
            item
            for item in in_progress
            if requested_attempt_id and item.attempt_id == requested_attempt_id
        ),
        None,
    )
    blocking = about_requested or in_progress[0]
    reason = (
        CoachingBlockReason.FORMAL_ATTEMPT_IN_PROGRESS
        if about_requested is not None
        else CoachingBlockReason.FORMAL_ASSESSMENT_IN_PROGRESS
    )
    return CoachingPermission(
        allowed=False,
        reason=reason,
        message=_BLOCK_MESSAGE,
        formal_attempt_id=blocking.formal_attempt_id,
        quiz_id=blocking.quiz_id,
        details={
            "requested_attempt_id": requested_attempt_id,
            "formal_attempt_state": blocking.state.value,
            "in_progress_count": len(in_progress),
        },
    )
