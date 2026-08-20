"""The formal attempt state machine (§4, §15, §20).

One table, one function, and every state change in UC-09 goes through them::

    NOT_STARTED
        -> CONDITIONS_ACKNOWLEDGED          acknowledge the formal conditions
        -> IDENTITY_CONFIRMED               name matched, email confirmed
        -> ACTIVE                           UC-03 delivered the attempt, one device holds the lock
        -> SUBMITTED                        the learner submitted
        -> RESULT_CALCULATED                UC-04's score is confirmed
        -> PASSED | FAILED                  UC-05's decision, recorded here
        -> PENDING_REVIEW                   a pass waits for a human. NOT a certificate
        -> APPROVED | REQUIRES_FURTHER_REVIEW
        -> CERTIFICATE_ALLOWED              the certificate workflow was triggered

    ACTIVE -> AUTO_SUBMIT_IN_PROGRESS -> SUBMITTED        the disconnect path

WHY A TABLE AND NOT ``if`` STATEMENTS SCATTERED THROUGH THE SERVICES
-------------------------------------------------------------------
Because the interesting requirements are all *negative*. "A formal attempt cannot be paused", "a
disconnected attempt cannot be resumed", "a certificate cannot be issued without approval", "a
duplicate submission must not create a second submission" — every one of them is an absence, and an
absence is only enforceable if there is a single place where presence is decided. Anything missing
from :data:`ALLOWED_TRANSITIONS` is refused by construction, including the transitions nobody
thought to write a test for.

WHAT PAUSE AND RESUME LOOK LIKE HERE
------------------------------------
They look like nothing at all, and that is deliberate. There is no PAUSED state to move to and no
transition out of one, so pausing is not a rejected transition — it is an operation the state model
cannot express. UC-03 has no PAUSED status either (its lifecycle is ACTIVE / SUBMISSION_PENDING /
SUBMITTED), so this is the same model, not a competing one. ``pause_rejection`` and
``resume_rejection`` in ``domain.transitions_guards`` turn the request into the right refusal, and
the state never moves.

TERMINAL MEANS TERMINAL
-----------------------
``FAILED``, ``REQUIRES_FURTHER_REVIEW`` and ``CERTIFICATE_ALLOWED`` have no outgoing transitions.
In particular there is no ``REQUIRES_FURTHER_REVIEW -> APPROVED`` edge: once an assessor escalates,
nothing in UC-09 can turn that into a certificate. Whatever a company's escalation process is, it is
not "call the same endpoint again", and the safe failure is the one that keeps the certificate
blocked.
"""

from __future__ import annotations

from types import MappingProxyType

from app.modules.formal_assessment.domain.enums import FormalAttemptState

State = FormalAttemptState

#: Every legal transition. Read as "from this state, only these are possible".
_TRANSITIONS: dict[State, frozenset[State]] = {
    State.NOT_STARTED: frozenset({State.CONDITIONS_ACKNOWLEDGED}),
    # Re-acknowledging is legal: the conditions text may have been re-versioned between the
    # learner reading it and starting, and a fresh acknowledgement of the current version is
    # exactly what should happen then.
    State.CONDITIONS_ACKNOWLEDGED: frozenset(
        {State.CONDITIONS_ACKNOWLEDGED, State.IDENTITY_CONFIRMED}
    ),
    # Identity may be re-confirmed (the learner corrected a typo and submitted again), and the
    # attempt starts from here. There is no path from IDENTITY_CONFIRMED straight to SUBMITTED: an
    # attempt that was never active cannot be submitted.
    State.IDENTITY_CONFIRMED: frozenset(
        {State.IDENTITY_CONFIRMED, State.CONDITIONS_ACKNOWLEDGED, State.ACTIVE}
    ),
    State.ACTIVE: frozenset({State.AUTO_SUBMIT_IN_PROGRESS, State.SUBMITTED}),
    # The claim made by the first disconnect event. It can only complete; there is no way back to
    # ACTIVE, which is what "no resume after disconnect" means in the state model itself.
    State.AUTO_SUBMIT_IN_PROGRESS: frozenset({State.SUBMITTED}),
    State.SUBMITTED: frozenset({State.RESULT_CALCULATED}),
    State.RESULT_CALCULATED: frozenset({State.PASSED, State.FAILED}),
    State.PASSED: frozenset({State.PENDING_REVIEW}),
    State.FAILED: frozenset(),
    State.PENDING_REVIEW: frozenset({State.APPROVED, State.REQUIRES_FURTHER_REVIEW}),
    State.APPROVED: frozenset({State.CERTIFICATE_ALLOWED}),
    State.REQUIRES_FURTHER_REVIEW: frozenset(),
    State.CERTIFICATE_ALLOWED: frozenset(),
}

#: Read-only view, so no caller can widen the machine at runtime.
ALLOWED_TRANSITIONS = MappingProxyType(
    {state: frozenset(targets) for state, targets in _TRANSITIONS.items()}
)

#: States with no way out.
TERMINAL_STATES: frozenset[State] = frozenset(
    state for state, targets in _TRANSITIONS.items() if not targets
)


def can_transition(current: State, target: State) -> bool:
    """Whether ``current -> target`` is a legal move."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def is_terminal(state: State) -> bool:
    return state in TERMINAL_STATES


def is_idempotent_repeat(current: State, target: State) -> bool:
    """Whether asking to move to ``target`` from ``current`` is a repeat of work already done.

    Distinguishes "this request is a duplicate, return what exists" from "this request is illegal".
    A second submit of an already-submitted attempt is the former; a submit of an approved attempt
    is neither — it is a caller that has lost track of reality, and it gets an invalid-transition
    refusal.
    """
    return current is target


def reachable_from(state: State) -> frozenset[State]:
    """Every state reachable from ``state``, transitively. Used by tests and diagnostics."""
    seen: set[State] = set()
    frontier = [state]
    while frontier:
        current = frontier.pop()
        for target in ALLOWED_TRANSITIONS.get(current, frozenset()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return frozenset(seen)
