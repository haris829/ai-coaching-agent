"""Assembling attempt history (§9, §10).

A read model and nothing else. Each fact is read from the module that owns it and none of them is
copied into UC-08's store, so there is no second version of a score that could disagree with
UC-04's and no historical attempt that creating a retake could disturb.

The retake relationship (§10) is the one column UC-08 contributes. It comes from the retake
requests this module already stores — ``previous_attempt_id`` → ``attempt_id`` — rather than from a
new lineage structure, because UC-03 already owns attempts and their numbering and a second store
of the same fact is a second place for it to be wrong. It is rendered in both directions:
``retake_of_attempt_id`` on the retake, and ``retaken_by_attempt_id`` on the attempt it followed,
so a client can walk the chain without a second query.

Every downstream read is *individually* tolerant. One unreadable score degrades one entry, not the
listing: a learner asking "how did I do last time?" should not be met with an error page because a
feedback service is restarting.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.retakes.domain.history import AttemptHistory, AttemptHistoryEntry
from app.modules.retakes.domain.requests import RetakeRequest
from app.modules.retakes.integration.downstream import (
    CoachingProvider,
    FeedbackProvider,
    PassFailResultProvider,
    ScoringResultProvider,
)
from app.modules.retakes.integration.uc01 import ConfigurationProvider
from app.modules.retakes.integration.uc03 import AttemptContext, AttemptProvider
from app.modules.retakes.repositories.protocols import RetakeRequestRepository

logger = get_logger(__name__)


class AttemptHistoryService:
    def __init__(
        self,
        *,
        attempts: AttemptProvider,
        configurations: ConfigurationProvider,
        retakes: RetakeRequestRepository,
        scores: ScoringResultProvider,
        results: PassFailResultProvider,
        feedback: FeedbackProvider,
        coaching: CoachingProvider,
    ) -> None:
        self._attempts = attempts
        self._configurations = configurations
        self._retakes = retakes
        self._scores = scores
        self._results = results
        self._feedback = feedback
        self._coaching = coaching

    async def for_learner_quiz(self, learner_id: str, quiz_id: str) -> AttemptHistory:
        """Every attempt this learner has made at this quiz, oldest first."""
        attempts = sorted(
            await self._attempts.list_attempts(learner_id, quiz_id),
            key=lambda attempt: attempt.attempt_number,
        )
        requests = await self._retakes.list_for_learner_quiz(learner_id, quiz_id)
        links = _retake_links(requests)

        availability = await self._configurations.get_quiz_availability(quiz_id)
        course_id = availability.course_id if availability else None
        if course_id is None and attempts:
            course_id = attempts[0].course_id

        entries = [await self._entry(attempt, links) for attempt in attempts]
        return AttemptHistory(
            learner_id=learner_id,
            quiz_id=quiz_id,
            course_id=course_id,
            entries=tuple(entries),
        )

    # ----------------------------------------------------------- internals

    async def _entry(
        self, attempt: AttemptContext, links: _RetakeLinks
    ) -> AttemptHistoryEntry:
        score = await self._read(self._scores.get_score, attempt.attempt_id, "score")
        result = await self._read(self._results.get_result, attempt.attempt_id, "pass_fail")
        feedback = await self._read(
            self._feedback.get_feedback_availability, attempt.attempt_id, "feedback"
        )
        coaching = await self._read(
            self._coaching.get_coaching_availability, attempt.attempt_id, "coaching"
        )
        link = links.by_attempt.get(attempt.attempt_id)

        return AttemptHistoryEntry(
            attempt_id=attempt.attempt_id,
            attempt_number=attempt.attempt_number,
            status=str(attempt.status),
            configuration_version_id=attempt.configuration_version_id,
            configuration_version_number=attempt.configuration_version_number,
            started_at=attempt.started_at,
            submitted_at=attempt.submitted_at,
            total_questions=attempt.total_questions,
            # UC-04's numbers, carried through. Nothing is recomputed here.
            score_available=bool(score and score.confirmed),
            total_marks=score.total_marks if score else None,
            maximum_marks=score.maximum_marks if score else None,
            percentage=score.percentage if score else None,
            # UC-05's decision, copied verbatim.
            pass_fail_available=result is not None,
            pass_fail_status=str(result.status) if result else None,
            pass_mark_percentage=result.pass_mark_percentage if result else None,
            feedback_available=bool(feedback and feedback.available),
            coaching_available=bool(coaching and coaching.available),
            # UC-08's own contribution.
            is_retake=link is not None,
            retake_of_attempt_id=link.previous_attempt_id if link else None,
            retake_id=link.retake_id if link else None,
            retaken_by_attempt_id=links.followed_by.get(attempt.attempt_id),
        )

    async def _read(self, reader, attempt_id: str, label: str):  # type: ignore[no-untyped-def]
        """Call one downstream provider, degrading that field alone on failure.

        Broad by intent: a provider that raises anything at all costs one field on one entry, and
        the log line names which. Letting it propagate would mean an unrelated module's outage
        removed a learner's whole attempt history.
        """
        try:
            return await reader(attempt_id)
        except Exception:
            logger.warning(
                "retake.history_field_unavailable",
                extra={"attempt_id": attempt_id, "field": label},
            )
            return None


class _RetakeLinks:
    """The retake relationships for one learner and quiz, indexed both ways."""

    __slots__ = ("by_attempt", "followed_by")

    def __init__(self) -> None:
        #: attempt_id of a retake -> the request that created it
        self.by_attempt: dict[str, RetakeRequest] = {}
        #: attempt_id of a previous attempt -> attempt_id of the retake that followed it
        self.followed_by: dict[str, str] = {}


def _retake_links(requests: tuple[RetakeRequest, ...]) -> _RetakeLinks:
    """Index the completed retakes. Reserved and failed requests produced no attempt."""
    links = _RetakeLinks()
    for request in requests:
        if not request.completed or not request.attempt_id:
            continue
        links.by_attempt[request.attempt_id] = request
        links.followed_by[request.previous_attempt_id] = request.attempt_id
    return links
