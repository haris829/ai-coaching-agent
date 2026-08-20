"""Resolving the formal-assessment policy for a quiz or an attempt.

A small service with one job, and it exists as its own class because three different services need
the same answer and none of them should be the one that decides how to get it: "is this a formal
assessment, on which course, and may it be sat?"

THE REFUSALS IT OWNS
--------------------
* the quiz does not exist                       -> ``QuizNotFoundError``
* the quiz is not a formal assessment           -> ``QuizNotFormalAssessmentError``
* UC-01 could not be reached               -> the port's ``ProviderUnavailableError`` propagates

The last one is the important one. An unreadable configuration must never become "not formal",
because "not formal" is the answer that lets an attempt be sat without any of UC-09's conditions.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.formal_assessment.domain.errors import (
    QuizNotFormalAssessmentError,
    QuizNotFoundError,
)
from app.modules.formal_assessment.integration.uc01 import (
    FormalAssessmentPolicy,
    FormalAssessmentPolicyProvider,
)

logger = get_logger(__name__)


class FormalPolicyService:
    def __init__(self, *, policies: FormalAssessmentPolicyProvider) -> None:
        self._policies = policies

    async def get_policy(self, quiz_id: str) -> FormalAssessmentPolicy:
        """The policy for a quiz, refusing anything that is not a formal assessment."""
        policy = await self._policies.get_policy(quiz_id)
        if policy is None:
            raise QuizNotFoundError(quiz_id)
        if not policy.is_formal_assessment:
            raise QuizNotFormalAssessmentError(quiz_id)
        return policy

    async def find_policy(self, quiz_id: str) -> FormalAssessmentPolicy | None:
        """The policy for a quiz without refusing a non-formal one.

        For callers that need to *report* whether a quiz is formal rather than act on it — a client
        asking what conditions apply before it renders anything.
        """
        return await self._policies.get_policy(quiz_id)

    async def find_policy_for_attempt(self, attempt_id: str) -> FormalAssessmentPolicy | None:
        """The policy that applied to an existing attempt, from its locked configuration version."""
        return await self._policies.get_policy_for_attempt(attempt_id)

    async def require_available(self, quiz_id: str) -> FormalAssessmentPolicy:
        """The policy, refusing a quiz that cannot be sat at all.

        UC-03 checks availability too, when the attempt is created. This check is here so a
        withdrawn quiz produces one clean refusal before a formal record exists, instead of a half-
        built gate whose attempt creation then fails.
        """
        policy = await self.get_policy(quiz_id)
        if not policy.available:
            raise QuizNotFoundError(quiz_id)
        return policy
