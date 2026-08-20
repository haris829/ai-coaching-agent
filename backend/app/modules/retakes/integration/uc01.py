"""UC-01 (Quiz Configuration & Rules) — the contract UC-08 consumes.

UC-08 reads configuration and never writes it. This is the single most important boundary in the
module, because it is where §11's rule is either kept or broken: **granting a learner an extra
attempt must not change the quiz configuration.** There is deliberately no method on this port
that could write a ``maximum_attempts``, publish a version, or activate one. A grant is a record
in UC-08's own store; the course-wide maximum is read-only here and stays read-only.

Two reads, for two different questions:

* ``get_locked_configuration(version_id)`` — "what did the learner's last attempt run under?"
  That version, not today's, supplies the maximum used in the allowance, for exactly the reason
  UC-05 gives: a limit lowered after the fact must not retroactively strip an attempt.
* ``get_active_configuration(quiz_id)`` — "what would a new attempt lock?" A retake is a new
  attempt, so under the default policy this is the version it locks, exactly as UC-03 does.

The shapes are an anti-corruption layer, not a copy of UC-01's storage model. They are a narrowed
form of the ``QuizConfigurationVersion`` UC-03 already snapshots onto an attempt, carrying only
what a retake decision needs: the identity of the version, the attempt limit, the question count,
the type rules and the randomisation flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.question_types import QuestionType as SharedQuestionType

#: The question vocabulary, re-exported from the shared kernel rather than restated.
#:
#: UC-08 shipped with a copy of these five names, because standalone it had no shared kernel to
#: import. The merged application has exactly one — ``app.core.question_types`` — and
#: ``tests/test_architecture.py`` enforces that there is only one, so the copy is gone. UC-08 still
#: does not *define* question types and still must not refuse an unknown one: every field on the
#: types below is a plain ``str``, and this alias exists for readers, not for validation.
QuestionType = SharedQuestionType


@dataclass(frozen=True, slots=True)
class QuestionTypeQuota:
    """A per-type quota. When quotas are used they sum to ``question_count`` (UC-01's rule)."""

    type: str
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "count": self.count}


@dataclass(frozen=True, slots=True)
class QuizConfigurationVersion:
    """One immutable UC-01 configuration version, narrowed to what a retake needs."""

    configuration_version_id: str
    quiz_id: str
    course_id: str
    #: Monotonically increasing version number within the quiz.
    version: int

    #: How many questions a delivered attempt contains.
    question_count: int
    #: ``None`` means unlimited attempts. UC-08 never writes this field.
    maximum_attempts: int | None = None

    #: Optional per-type quotas. When empty, any allowed type may be used.
    question_type_quotas: tuple[QuestionTypeQuota, ...] = field(default_factory=tuple)
    #: Optional whitelist of permitted types, used when quotas are not supplied.
    allowed_question_types: tuple[str, ...] = field(default_factory=tuple)
    #: Optional topic filter passed through to the UC-02 question bank.
    topic_ids: tuple[str, ...] = field(default_factory=tuple)

    randomise_question_order: bool = False
    randomise_option_order: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "configuration_version_id": self.configuration_version_id,
            "quiz_id": self.quiz_id,
            "course_id": self.course_id,
            "version": self.version,
            "question_count": self.question_count,
            "maximum_attempts": self.maximum_attempts,
            "question_type_quotas": [quota.as_dict() for quota in self.question_type_quotas],
            "allowed_question_types": list(self.allowed_question_types),
            "topic_ids": list(self.topic_ids),
            "randomise_question_order": self.randomise_question_order,
            "randomise_option_order": self.randomise_option_order,
        }


@dataclass(frozen=True, slots=True)
class QuizAvailability:
    """Whether the quiz may be attempted at all, independent of any one version."""

    quiz_id: str
    course_id: str
    #: False when the quiz is archived, unpublished, or outside its window.
    available: bool
    reason: str | None = None


@runtime_checkable
class ConfigurationProvider(Protocol):
    """Read-only port onto UC-01.

    A transient failure must raise ``app.core.errors.ProviderUnavailableError`` so a retake is
    refused with a controlled, retryable state rather than created against a half-read
    configuration.
    """

    async def get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None:
        """Availability, or ``None`` when the quiz does not exist."""
        ...

    async def get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None:
        """The version active *now*, or ``None`` when the quiz has none.

        Read once per retake, at creation, and then locked onto the attempt by UC-03 — never
        re-read for an attempt already in flight.
        """
        ...

    async def get_locked_configuration(
        self, configuration_version_id: str
    ) -> QuizConfigurationVersion | None:
        """A specific historical version by id.

        This is how a historical attempt keeps its own rules: the maximum attempts, question
        count and type rules that applied when it ran are read from the version it locked, not
        from whatever is active today.
        """
        ...
