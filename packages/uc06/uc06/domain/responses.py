"""Response types - DISCLAIMER ENFORCEMENT LAYER 1 (type level).

A case-linked response is a type that cannot be constructed without the
disclaimer:

* `disclaimer` is declared `field(init=False)`, so __init__ accepts no such
  parameter. There is no override argument to pass, no keyword to omit, and no
  builder that takes one. Business logic cannot express omission.
* It is stamped in __post_init__ directly from CANONICAL_DISCLAIMER. No caller,
  no config, no generator output participates.
* The dataclass is frozen, so there is no setter: reassignment raises
  FrozenInstanceError. Nothing downstream can blank or edit it.
* to_payload() writes the disclaimer field itself, after the subclass body, so a
  subclass cannot shadow or drop it.

Layers 2 (boundary check) and 3 (output scan) do not trust any of this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .disclaimer import CANONICAL_DISCLAIMER, DISCLAIMER_FIELD
from .enums import GuardClass, NaricLevel, NaricLevelSource, ResponseMode, SourceStatus


@dataclass(frozen=True)
class DisclaimedResponse:
    """Base of every payload UC-06 emits from the case-coaching surface.

    Educational responses, degraded fallbacks and safe errors all inherit it, so
    every path - including every error path - carries the disclaimer.
    """

    # No default, no init parameter: __post_init__ is the only writer.
    disclaimer: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, DISCLAIMER_FIELD, CANONICAL_DISCLAIMER)

    def _body(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    def to_payload(self) -> dict[str, Any]:
        payload = dict(self._body())
        # Written last and unconditionally: a subclass body cannot displace it.
        payload[DISCLAIMER_FIELD] = self.disclaimer
        return payload


@dataclass(frozen=True)
class CaseLinkedResponse(DisclaimedResponse):
    """An educational, case-linked explanation.

    `content` is generated text. It is a separate field from `disclaimer` and is
    never scanned for one, never trusted to contain one, and never used to supply
    one.
    """

    response_id: str
    session_id: str
    case_file_id: str
    explanation_profile: str
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    content: str
    case_facts_referenced: tuple[str, ...]
    guard_triggered: GuardClass | None
    case_file_status: SourceStatus
    learner_context_status: SourceStatus
    topic_tag: str
    mode: ResponseMode = ResponseMode.CASE_LINKED

    def _body(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id,
            "session_id": self.session_id,
            "mode": self.mode.value,
            "case_file_id": self.case_file_id,
            "explanation_profile": self.explanation_profile,
            "naric_level": self.naric_level.value,
            "naric_level_source": self.naric_level_source.value,
            "content": self.content,
            "case_facts_referenced": list(self.case_facts_referenced),
            "guard_triggered": self.guard_triggered.value if self.guard_triggered else None,
            "case_file_status": self.case_file_status.value,
            "learner_context_status": self.learner_context_status.value,
            "topic_tag": self.topic_tag,
            "notice": None,
        }


@dataclass(frozen=True)
class GeneralTopicResponse(DisclaimedResponse):
    """Degraded, NOT case-linked: the case file could not be read.

    Carries no case facts and no case content - only the topic area, plus an
    explicit notice that the case file could not be accessed.
    """

    response_id: str
    session_id: str
    case_file_id: str | None
    explanation_profile: str
    naric_level: NaricLevel
    naric_level_source: NaricLevelSource
    content: str
    notice: str
    case_file_status: SourceStatus
    learner_context_status: SourceStatus
    topic_tag: str
    guard_triggered: GuardClass | None = None
    mode: ResponseMode = ResponseMode.GENERAL_FALLBACK

    def _body(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id,
            "session_id": self.session_id,
            "mode": self.mode.value,
            "case_file_id": self.case_file_id,
            "explanation_profile": self.explanation_profile,
            "naric_level": self.naric_level.value,
            "naric_level_source": self.naric_level_source.value,
            "content": self.content,
            "case_facts_referenced": [],
            "guard_triggered": self.guard_triggered.value if self.guard_triggered else None,
            "case_file_status": self.case_file_status.value,
            "learner_context_status": self.learner_context_status.value,
            "topic_tag": self.topic_tag,
            "notice": self.notice,
        }


@dataclass(frozen=True)
class SafeErrorResponse(DisclaimedResponse):
    """The uniform error envelope for the case-coaching surface.

    Carries no case content, no provider name, no internal exception text and no
    stack trace - and still carries the disclaimer, so that no response from this
    surface is ever unlabelled.
    """

    code: str
    message: str
    request_id: str
    retryable: bool = False
    session_halted: bool = False

    def _body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "request_id": self.request_id,
                "retryable": self.retryable,
                "session_halted": self.session_halted,
            }
        }
