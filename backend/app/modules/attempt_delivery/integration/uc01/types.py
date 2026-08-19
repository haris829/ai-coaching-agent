"""UC-01 (Quiz Configuration & Rules) — contract types consumed by UC-03.

These describe *only* what UC-03 needs in order to deliver an attempt. They are
deliberately narrow: UC-03 does not own quiz configuration, does not persist the
authoritative copy, and must not re-implement its authoring rules.

When UC-01 is merged, the concrete adapter is replaced but this shape stays as the
anti-corruption layer. If UC-01's internal model differs, the mapping belongs in
the adapter — never in UC-03's services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.attempt_delivery.domain.enums import QuestionPresentation, QuestionType


@dataclass(frozen=True, slots=True)
class QuestionTypeQuota:
    """A per-type quota. When quotas are used they must sum to ``question_count``."""

    type: QuestionType
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": str(self.type), "count": self.count}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuestionTypeQuota:
        return cls(type=QuestionType(raw["type"]), count=int(raw["count"]))


@dataclass(frozen=True, slots=True)
class QuizConfigurationVersion:
    """The resolved, immutable configuration for one quiz configuration version.

    UC-03 snapshots this onto the attempt at creation. Every later decision for
    that attempt — timing, question count, pass mark, delivery mode — is made from
    the snapshot, never from a fresh read of UC-01.
    """

    #: Identity of this specific version. Stored on the attempt.
    configuration_version_id: str
    quiz_id: str
    course_id: str
    #: Monotonically increasing version number within the quiz.
    version: int

    #: Total number of questions to deliver.
    question_count: int

    #: Pass mark as a percentage (0-100). Carried through for downstream grading.
    pass_mark_percentage: float

    #: ISO-8601 UTC instant this version became active in UC-01.
    activated_at: str

    #: Time limit in seconds. ``None`` means the attempt is untimed.
    time_limit_seconds: int | None = None
    #: Maximum attempts per learner. ``None`` means unlimited.
    max_attempts: int | None = None

    #: Optional per-type quotas. When empty, any eligible type may be used.
    question_type_quotas: tuple[QuestionTypeQuota, ...] = ()
    #: Optional whitelist of permitted types, used when quotas are not supplied.
    allowed_question_types: tuple[QuestionType, ...] = ()
    #: Optional topic filter passed through to the UC-02 question bank.
    topic_ids: tuple[str, ...] = ()

    randomise_question_order: bool = False
    randomise_option_order: bool = False
    question_presentation: QuestionPresentation = QuestionPresentation.ALL_AT_ONCE

    #: Whether the learner may submit with questions left unanswered.
    allow_incomplete_submission: bool = True

    #: Anything UC-01 sends that UC-03 does not interpret. Retained in the snapshot
    #: so an attempt keeps a faithful record of the version it ran under.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for storage in the attempt's configuration snapshot."""
        return {
            "configurationVersionId": self.configuration_version_id,
            "quizId": self.quiz_id,
            "courseId": self.course_id,
            "version": self.version,
            "questionCount": self.question_count,
            "passMarkPercentage": self.pass_mark_percentage,
            "activatedAt": self.activated_at,
            "timeLimitSeconds": self.time_limit_seconds,
            "maxAttempts": self.max_attempts,
            "questionTypeQuotas": [quota.to_dict() for quota in self.question_type_quotas],
            "allowedQuestionTypes": [str(item) for item in self.allowed_question_types],
            "topicIds": list(self.topic_ids),
            "randomiseQuestionOrder": self.randomise_question_order,
            "randomiseOptionOrder": self.randomise_option_order,
            "questionPresentation": str(self.question_presentation),
            "allowIncompleteSubmission": self.allow_incomplete_submission,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuizConfigurationVersion:
        """Rehydrate from a stored snapshot."""
        return cls(
            configuration_version_id=raw["configurationVersionId"],
            quiz_id=raw["quizId"],
            course_id=raw["courseId"],
            version=int(raw["version"]),
            question_count=int(raw["questionCount"]),
            pass_mark_percentage=float(raw["passMarkPercentage"]),
            activated_at=raw["activatedAt"],
            time_limit_seconds=raw.get("timeLimitSeconds"),
            max_attempts=raw.get("maxAttempts"),
            question_type_quotas=tuple(
                QuestionTypeQuota.from_dict(item) for item in raw.get("questionTypeQuotas") or []
            ),
            allowed_question_types=tuple(
                QuestionType(item) for item in raw.get("allowedQuestionTypes") or []
            ),
            topic_ids=tuple(raw.get("topicIds") or []),
            randomise_question_order=bool(raw.get("randomiseQuestionOrder", False)),
            randomise_option_order=bool(raw.get("randomiseOptionOrder", False)),
            question_presentation=QuestionPresentation(
                raw.get("questionPresentation", QuestionPresentation.ALL_AT_ONCE)
            ),
            allow_incomplete_submission=bool(raw.get("allowIncompleteSubmission", True)),
            extra=dict(raw.get("extra") or {}),
        )


@dataclass(frozen=True, slots=True)
class QuizAvailability:
    """Whether the quiz may be attempted at all, independent of any one version."""

    quiz_id: str
    course_id: str
    #: False when the quiz is archived, unpublished, or outside its window.
    available: bool
    #: Machine-readable reason when ``available`` is False.
    reason: str | None = None
