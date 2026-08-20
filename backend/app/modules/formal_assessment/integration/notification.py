"""Learner notification (§12).

The integration point for telling a learner what happened to their formal assessment: it was
approved and their certificate is on its way, or it has been referred for further review.

**No external provider is built here.** No email client, no SMS gateway, no push service, no
template engine. The company connects its own notification infrastructure to
:class:`LearnerNotifier` at integration; UC-09 decides *when* a learner should be told and *what the
facts are*.

NOTIFICATION FAILURE MUST NOT CORRUPT ANYTHING
----------------------------------------------
This is the hard requirement, and it is met structurally: notification happens **after** every state
change is persisted, and its outcome is recorded as an audit event rather than as part of the formal
attempt's state. An assessor's approval that was persisted and a notification that was not is a
learner who has to check the page themselves — annoying, and strictly better than an approval rolled
back because a mail server was down. :meth:`LearnerNotifier.notify` may raise; the calling service
catches and audits, and never lets the failure reach the assessment.

WHAT A NOTIFICATION CARRIES
---------------------------
Ids, the event, the course and quiz, and the percentage. **Not** the learner's email address — the
notifier resolves that from the learner id through the platform's own channel preferences, which is
where that decision belongs, and it keeps UC-09 from holding a second copy of contact details.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


class LearnerNotificationEvent(StrEnum):
    """The formal-assessment moments a learner is told about."""

    #: The assessment was approved and the certificate workflow has been triggered.
    FORMAL_ASSESSMENT_APPROVED = "FORMAL_ASSESSMENT_APPROVED"
    #: The assessment was referred for further review. No certificate, and the learner should know
    #: why
    #: nothing is coming yet.
    FORMAL_ASSESSMENT_REQUIRES_FURTHER_REVIEW = "FORMAL_ASSESSMENT_REQUIRES_FURTHER_REVIEW"
    #: The assessment passed and is waiting for review. Optional in most deployments, and available
    #: so
    #: a learner is not left wondering why a pass produced nothing.
    FORMAL_ASSESSMENT_PENDING_REVIEW = "FORMAL_ASSESSMENT_PENDING_REVIEW"


@dataclass(frozen=True, slots=True)
class LearnerNotification:
    """One notification, as facts rather than as prose.

    No subject line and no body: the wording belongs to the company's templates, which are
    versioned, translated and owned by people who are not writing this module.
    """

    event: LearnerNotificationEvent
    learner_id: str
    course_id: str
    quiz_id: str
    formal_attempt_id: str
    attempt_id: str | None = None
    review_id: str | None = None
    percentage: float | None = None
    occurred_at: str | None = None
    #: Anything else a template may want, kept explicit so nothing personal arrives by accident.
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "formal_attempt_id": self.formal_attempt_id,
            "attempt_id": self.attempt_id,
            "review_id": self.review_id,
            "percentage": self.percentage,
            "occurred_at": self.occurred_at,
            "context": dict(self.context),
        }


@dataclass(frozen=True, slots=True)
class NotificationOutcome:
    """Whether the notification went out. Recorded in the audit trail, not on the assessment."""

    delivered: bool
    reference: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"delivered": self.delivered, "reference": self.reference, "error": self.error}


@runtime_checkable
class LearnerNotifier(Protocol):
    """The platform's notification channel, as UC-09 uses it."""

    async def notify(self, notification: LearnerNotification) -> NotificationOutcome:
        """Send one notification.

        May raise: the caller catches, audits and carries on. Returning
        ``NotificationOutcome(delivered=False, error=...)`` is the gentler way to report the same
        thing and is preferred where the adapter can tell the difference between "refused" and
        "broke".
        """
        ...


class LoggingLearnerNotifier:
    """Default binding: log the notification instead of sending it.

    An unwired deployment therefore has a record of every notification it *would* have sent, which
    is the honest fallback — and it never fails, so the "notification failure cannot corrupt state"
    property is not being tested by the default binding.
    """

    async def notify(self, notification: LearnerNotification) -> NotificationOutcome:
        with contextlib.suppress(Exception):
            logger.info(
                f"formal.notification.{notification.event.value}",
                extra={
                    "learner_id": notification.learner_id,
                    "formal_attempt_id": notification.formal_attempt_id,
                    "review_id": notification.review_id,
                },
            )
        return NotificationOutcome(delivered=True, reference="logged")
