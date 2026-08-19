"""Conversation state (§18).

The coach needs enough of the conversation to ask a sensible next question. That is *state*, not a
log: it is kept because the next request needs it, it is reached through a repository protocol so
the company's persistence layer can hold it, and it is never emitted to a log sink (§22).

Three deliberate properties:

**No system role.** The coaching policy is assembled at request time by
``app.modules.coaching.prompts`` and is never stored as a message. A stored system turn would be
editable state that reaches the model as instructions — the exact shape of a prompt-injection
vulnerability, and one that would let a stored message outlive the policy that authored it (§25).

**Bounded replay.** ``window`` returns the trailing slice actually sent to the model. A coaching
conversation grows without limit; the model does not need all of it to ask the next good question,
and an unbounded history is a slow, expensive way to make replies worse.

**Content is never counted as coaching progress.** ``exchange_count`` lives on the session, not
here, so a corrupted or partially written transcript cannot move the five-exchange threshold (§15).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.modules.coaching.domain.enums import MessageRole


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One turn of the coaching conversation."""

    role: MessageRole
    content: str
    #: 0-based position in the conversation. Assigned by the service, so ordering never depends on
    #: timestamps that a distributed store might not preserve.
    index: int
    created_at: str
    #: Which mode the coach was in when it produced this turn. Absent for learner messages.
    mode: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "content": self.content,
            "index": self.index,
            "created_at": self.created_at,
            "mode": self.mode,
        }


@dataclass(frozen=True, slots=True)
class CoachingTranscript:
    """The stored conversation for one coaching session."""

    session_id: str
    messages: tuple[ChatMessage, ...] = field(default_factory=tuple)

    @property
    def next_index(self) -> int:
        return len(self.messages)

    @property
    def learner_message_count(self) -> int:
        return sum(1 for item in self.messages if item.role is MessageRole.LEARNER)

    @property
    def coach_message_count(self) -> int:
        return sum(1 for item in self.messages if item.role is MessageRole.COACH)

    @property
    def last_learner_message(self) -> ChatMessage | None:
        """The most recent learner turn — what a retry re-sends (§28)."""
        return next(
            (item for item in reversed(self.messages) if item.role is MessageRole.LEARNER), None
        )

    def window(self, size: int) -> tuple[ChatMessage, ...]:
        """The trailing ``size`` messages, in order, as sent to the model."""
        if size <= 0:
            return ()
        return self.messages[-size:]

    def appended(self, *messages: ChatMessage) -> CoachingTranscript:
        return CoachingTranscript(
            session_id=self.session_id, messages=self.messages + tuple(messages)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "messages": [item.as_dict() for item in self.messages],
        }


def build_messages(
    transcript: CoachingTranscript,
    *,
    role: MessageRole,
    content: str,
    created_at: str,
    mode: str | None = None,
) -> ChatMessage:
    """Create the next message for a transcript, with the index it must occupy."""
    return ChatMessage(
        role=role,
        content=content,
        index=transcript.next_index,
        created_at=created_at,
        mode=mode,
    )


def to_history(messages: Sequence[ChatMessage]) -> tuple[dict[str, str], ...]:
    """The provider-neutral form handed to a ``CoachingLLM`` implementation.

    Deliberately plain — role and content — so an adapter for any vendor is a mapping exercise and
    the domain never learns a provider's message schema (§23).
    """
    return tuple({"role": item.role.value, "content": item.content} for item in messages)
