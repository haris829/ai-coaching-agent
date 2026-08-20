"""Identity confirmation (§2).

Two facts have to hold before a formal attempt may start:

1. the name the learner typed matches the name on their profile **exactly**; 2. the learner's email
address is already confirmed on their account.

THE MATCHING RULE, AND WHY IT IS NOT MORE CLEVER THAN THIS
----------------------------------------------------------
The comparison is exact and case-sensitive, after the whitespace normalisation the rest of this
codebase already applies to every string that arrives in a request body: UC-03's and UC-08's request
models are declared with ``str_strip_whitespace=True``, so leading and trailing whitespace is not
part of any value anywhere in the system. :func:`normalise_name` extends that to internal runs of
whitespace collapsing to a single space, because ``"John  Smith"`` and ``"John Smith"`` differ only
by an artefact of typing, not by a character the learner meant.

Nothing else is normalised. Case is significant: ``"john smith"`` does **not** match ``"John
Smith"``. There is no accent folding, no punctuation stripping, no initial matching and no fuzzy
distance. Two reasons:

* the specification says exact match, and says not to invent normalisation behaviour;
* every relaxation of an identity check is a policy decision with a compliance owner, and this
  is not that owner. :data:`NAME_MATCH_RULE` documents the rule that is implemented so the company
  can change it deliberately, in one function, with a test that says what changed.

There is no configuration switch for the matching mode. A deployment that could quietly turn
identity matching down to case-insensitive is a deployment where nobody can say afterwards what was
actually checked.

WHAT IS NEVER RETURNED
----------------------
:class:`IdentityCheck` says which field failed. It does not carry the profile's name or email
address back to the caller, and neither does the error built from it: a confirmation endpoint that
echoed the expected value would be an endpoint for reading a learner's registered email address.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.formal_assessment.domain.enums import IdentityMismatchField

#: Documented, testable statement of the rule in force. Quoted in the API description so a client
#: can tell a learner what "exactly" means before they type.
NAME_MATCH_RULE = (
    "Exact, case-sensitive match against the profile name, after leading, trailing and repeated "
    "internal whitespace is normalised."
)

_WHITESPACE = re.compile(r"\s+")

#: Bounded so an oversized field cannot become an oversized comparison or an oversized audit entry.
MAX_NAME_LENGTH = 200
MAX_EMAIL_LENGTH = 320


def normalise_name(value: str | None) -> str:
    """Collapse whitespace. Nothing else — see the module docstring."""
    if not isinstance(value, str):
        return ""
    return _WHITESPACE.sub(" ", value).strip()


def normalise_email(value: str | None) -> str:
    """Trim and case-fold an email address.

    Case-folding here and not for the name because the domain part of an email address is defined to
    be case-insensitive and every mail system in practice treats the local part that way too. This
    is the same normalisation the platform's own account system applies when it stores the address;
    a case-sensitive email comparison would reject a learner for typing their own address the way
    their mail client shows it.
    """
    if not isinstance(value, str):
        return ""
    return value.strip().casefold()


@dataclass(frozen=True, slots=True)
class LearnerIdentityProfile:
    """The authoritative identity facts, as read from the platform's profile source.

    ``email_confirmed`` is the account's own flag. UC-09 does not send confirmation emails and does
    not decide what confirmation means — it reads the fact and refuses to start without it.
    """

    learner_id: str
    full_name: str
    email: str
    email_confirmed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "full_name": self.full_name,
            "email": self.email,
            "email_confirmed": self.email_confirmed,
        }


@dataclass(frozen=True, slots=True)
class IdentitySubmission:
    """What the learner typed on the confirmation step.

    ``email`` is optional. The specification requires that the learner's email confirmation has
    occurred — a property of the account — and a deployment may additionally ask the learner to type
    the address. When it is supplied it must match; when it is not, the account flag is still
    required.
    """

    full_name: str
    email: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityCheck:
    """The verdict. ``confirmed`` is the only thing a caller acts on."""

    confirmed: bool
    mismatched_fields: tuple[IdentityMismatchField, ...] = ()
    email_confirmed: bool = False
    #: Client-safe detail: counts and booleans, never the compared values.
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def mismatch_codes(self) -> tuple[str, ...]:
        return tuple(field_.value for field_ in self.mismatched_fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "email_confirmed": self.email_confirmed,
            "mismatched_fields": list(self.mismatch_codes),
            "name_match_rule": NAME_MATCH_RULE,
            **({"details": dict(self.details)} if self.details else {}),
        }


def check_identity(
    *, submission: IdentitySubmission, profile: LearnerIdentityProfile
) -> IdentityCheck:
    """Compare what the learner typed against their profile (§2).

    A pure function over two records, so the rule is testable without a repository, an HTTP request
    or a learner. The order of the checks does not matter here — unlike an authorisation gate, this
    is not probing anything the caller does not already know about themselves — so every mismatched
    field is reported at once and the learner fixes both in one go.
    """
    mismatches: list[IdentityMismatchField] = []

    entered_name = normalise_name(submission.full_name)
    profile_name = normalise_name(profile.full_name)
    # An empty profile name can never be matched: without a name on file there is nothing to confirm
    # against, and treating "" == "" as a match would let a profile with no name through the gate.
    if not profile_name or not entered_name or entered_name != profile_name:
        mismatches.append(IdentityMismatchField.FULL_NAME)

    if submission.email is not None:
        entered_email = normalise_email(submission.email)
        profile_email = normalise_email(profile.email)
        if not profile_email or not entered_email or entered_email != profile_email:
            mismatches.append(IdentityMismatchField.EMAIL)

    return IdentityCheck(
        confirmed=not mismatches and profile.email_confirmed,
        mismatched_fields=tuple(mismatches),
        email_confirmed=bool(profile.email_confirmed),
        details={
            "name_supplied": bool(entered_name),
            "email_supplied": submission.email is not None,
        },
    )
