"""The learner profile source — where identity comes from (§2).

UC-09 confirms an identity; it does not own one. The name and the email-confirmation flag are the
platform's account facts, read through this port and never written through it. There is no UC-09
endpoint that changes a learner's name, and no code path that could: the protocol has no write
method.

WHY THIS IS ITS OWN PORT RATHER THAN PART OF A UC PORT
-----------------------------------------------------
Because it is not a use case. The profile belongs to the platform's account system, which sits
beside UC-01…UC-08 rather than inside any of them, and at integration it binds to whatever the
company's user directory actually is. Keeping it separate means the identity check does not pretend
to be an integration with a quiz module.

FAILURE MUST NOT DEGRADE
------------------------
An implementation that cannot reach the directory must raise ``LearnerProfileUnavailableError``, not
return ``None``. "We could not read the learner's name" and "the learner has no name on file" are
different facts, and the one thing neither may become is "the name matched".
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.formal_assessment.domain.identity import LearnerIdentityProfile


@runtime_checkable
class LearnerProfileProvider(Protocol):
    """Read-only access to the identity facts UC-09 compares against."""

    async def get_profile(self, learner_id: str) -> LearnerIdentityProfile | None:
        """The learner's profile, or ``None`` when no such learner exists.

        Raise ``LearnerProfileUnavailableError`` when the directory could not be reached.
        """
        ...
