"""SQLAlchemy ORM models for UC-07 AI Coaching Review Mode.

Tables are prefixed ``qk_`` — *quiz coaching*, spelled with a ``k`` because ``qc_`` already belongs
to UC-01's quiz configuration. They sit alongside UC-02's ``qb_``, UC-03's ``qd_``, UC-04's ``qr_``,
UC-05's ``qg_`` and UC-06's ``qf_`` tables without collision.

WHY THIS FILE EXISTS AT ALL
---------------------------
UC-07 was built as a standalone module with **no schema**: sessions and transcripts were reached
through the protocols in ``repositories/protocols.py`` and satisfied by dictionaries, because the
company database did not exist yet and inventing one would have been guesswork. In the merged system
the database does exist, so the protocols get the adapter they were written for. The protocols did
not change; the docstring in ``repositories/protocols.py`` listed four guarantees the eventual
implementation had to provide, and each is now a constraint below:

============================= ============================================================
Guarantee                     How it is enforced here
============================= ============================================================
Idempotent start              ``UNIQUE (learner_id, attempt_id, question_id)``
Ordered conversation          ``UNIQUE (session_id, message_index)``
Nothing rewrites a message    ``trg_qk_message_no_update`` — a trigger, not a convention
One knowledge gap per session ``UNIQUE (session_id)``
============================= ============================================================

The first one is the load-bearing one. "Starting coaching twice resumes the same conversation" is a
claim about concurrency, and a read-then-write check in the service cannot make it true: two
simultaneous requests both read "no session" and both insert. The unique constraint decides it, the
loser is caught as ``DuplicateCoachingSessionError``, and the service reads the winner.

APPEND-ONLY, BY TRIGGER
-----------------------
``qk_coaching_messages`` and ``qk_coaching_activity`` reject every ``UPDATE``. A coaching transcript
is a learner reasoning aloud about something they got wrong, and an activity stream is an audit
record; neither is a thing this system should be able to rewrite. UC-04's confirmed scores and
UC-06's generated reports are protected the same way, for the same reason — and, as there,
``DELETE`` is deliberately left alone: nothing in the application deletes either, and a blanket
delete trigger would also block a test database being truncated between tests.

Cross-boundary references (``learner_id``, ``attempt_id``, ``course_id``, ``question_id``) are
deliberately *soft*: indexed columns without foreign keys, as in every other capability here.
Those rows belong to UC-03, UC-02 and the platform, and a coaching session must survive a question
being retired or a configuration version being superseded. Everything inside UC-07's own aggregate
(``qk_coaching_messages`` → ``qk_coaching_sessions``) is bound by a real foreign key with a cascade.

WHAT IS NOT STORED
------------------
No coaching *context*. The sanitised material handed to the model is rebuilt on every turn and never
persisted, so there is no representation of a question — safe or otherwise — sitting in UC-07's
storage waiting to be found (§13, §22). And no answer key: there is no column here capable of
holding one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DDL,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, id_column
from app.modules.coaching.domain.enums import (
    CoachingMode,
    CoachingSessionStatus,
    MessageRole,
)
from app.modules.coaching.integration.activity import CoachingActivityType

TABLE_PREFIX = "qk_"

_STATUS_VALUES = ", ".join(f"'{item.value}'" for item in CoachingSessionStatus)
_MODE_VALUES = ", ".join(f"'{item.value}'" for item in CoachingMode)
_ROLE_VALUES = ", ".join(f"'{item.value}'" for item in MessageRole)
_EVENT_VALUES = ", ".join(f"'{item.value}'" for item in CoachingActivityType)


class CoachingSessionRow(Base):
    """One learner's coaching conversation about one incorrectly answered question.

    The generated ``id`` is not what makes a session unique — see the unique constraint on the
    natural key, and the note in ``app.modules.coaching.ids``.
    """

    __tablename__ = f"{TABLE_PREFIX}coaching_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # Soft references to rows owned by UC-03, UC-02 and the platform.
    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: 1-based position in the delivered paper, so a review queue can order sessions without
    #: re-reading UC-03. Nullable: a question UC-04 scored without a delivery position still gets
    #: coached, it just sorts last.
    question_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A label, never content. ``NULL`` when the question carries no topic in UC-03 or UC-06 —
    #: recorded as-is rather than filled with an invented placeholder.
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)

    exchange_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The threshold in force for *this* session, copied at creation. Changing the configuration
    #: cannot move the goalposts under a conversation already running.
    direct_explanation_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    direct_explanation_offered: Mapped[bool] = mapped_column(
        Boolean(create_constraint=False), nullable=False, default=False
    )

    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: A code from UC-07's taxonomy. Never a provider message, which can echo back the prompt.
    last_failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Advances on every stored transition, for optimistic concurrency.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    started_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    messages: Mapped[list[CoachingMessageRow]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="CoachingMessageRow.message_index",
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status"),
        CheckConstraint(f"mode IN ({_MODE_VALUES})", name="mode"),
        CheckConstraint("exchange_count >= 0", name="exchange_count_non_negative"),
        CheckConstraint("consecutive_failures >= 0", name="consecutive_failures_non_negative"),
        CheckConstraint(
            "direct_explanation_threshold >= 1", name="direct_explanation_threshold_positive"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("question_position IS NULL OR question_position >= 1", name="position"),
        CheckConstraint("direct_explanation_offered IN (0, 1)", name="direct_explanation_offered"),
        # A completed session always says when, and an uncompleted one never does. Makes "ACTIVE
        # but claiming to have finished" unrepresentable rather than merely unlikely.
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)", name="completed_state"
        ),
        # THE constraint. One coaching session per learner per incorrect question, which is what
        # makes starting coaching idempotent under concurrency rather than only under a check.
        UniqueConstraint("learner_id", "attempt_id", "question_id", name="natural_key"),
        Index(f"ix_{TABLE_PREFIX}coaching_sessions_learner_attempt", "learner_id", "attempt_id"),
        Index(f"ix_{TABLE_PREFIX}coaching_sessions_status", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CoachingSession {self.id} q={self.question_id} {self.status}>"


class CoachingMessageRow(Base):
    """One turn of a coaching conversation. Written once, never edited.

    ``message_index`` is assigned by the service rather than derived from ``created_at``, so replay
    order never depends on clock skew in a distributed store.

    There is no SYSTEM role. The coaching policy is assembled at request time by
    ``app.modules.coaching.prompts`` and is never stored as a message — a stored system turn would
    be editable state that reaches the model as instructions, which is the shape of a
    prompt-injection vulnerability, and would let a stored message outlive the policy (§25).
    """

    __tablename__ = f"{TABLE_PREFIX}coaching_messages"

    id: Mapped[str] = id_column()
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{TABLE_PREFIX}coaching_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: 0-based position in the conversation.
    message_index: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which mode the coach was in when it produced this turn. NULL for learner messages.
    mode: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    session: Mapped[CoachingSessionRow] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(f"role IN ({_ROLE_VALUES})", name="role"),
        CheckConstraint(f"mode IS NULL OR mode IN ({_MODE_VALUES})", name="mode"),
        CheckConstraint("message_index >= 0", name="message_index_non_negative"),
        UniqueConstraint("session_id", "message_index", name="session_id_message_index"),
        Index(f"ix_{TABLE_PREFIX}coaching_messages_session_id", "session_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CoachingMessage {self.session_id}#{self.message_index} {self.role}>"


class KnowledgeGapRow(Base):
    """One topic a learner may need to revisit (§21).

    "May" is doing real work in that sentence. One wrong answer is weak evidence, and it is the
    business's analytics — not UC-07 — that decides when a pattern of them means something. What is
    recorded is exactly §21's list: learner, attempt, course, question, topic, coaching session,
    timestamp.

    One row per coaching session, enforced by a unique constraint rather than by remembering not to
    write twice. A learner who spends twenty turns on one question has one knowledge gap in one
    topic; counting their persistence as twenty would make the dataset actively misleading.
    """

    __tablename__ = f"{TABLE_PREFIX}knowledge_gaps"

    id: Mapped[str] = id_column()

    #: Soft reference to ``qk_coaching_sessions.id``. Unique — see the class docstring and the
    #: constraint below. Not a foreign key: the gap is an analytics fact about the learner and
    #: outlives any decision to prune coaching conversations under a retention policy.
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)

    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: NULL when the question carries no topic in either UC-03 or UC-06. An untagged question is a
    #: content problem worth seeing in the data; inventing "General" would hide it.
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: What caused the record. Explicit so a future source is distinguishable in the data.
    source: Mapped[str] = mapped_column(String(48), nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", name="session_id"),
        Index(f"ix_{TABLE_PREFIX}knowledge_gaps_learner_course", "learner_id", "course_id"),
        Index(f"ix_{TABLE_PREFIX}knowledge_gaps_topic", "topic"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeGap {self.learner_id} {self.topic!r}>"


class CoachingActivityRow(Base):
    """One coaching lifecycle event (§22). Identifiers, counts and codes only.

    §22 lists what to track — session, attempt, learner, question, topic, mode, exchange count,
    status, timestamp — and, more importantly, what not to: no answer keys, no correct answers, no
    sensitive learner information, and **no conversation**. There is no column here capable of
    holding any of those, which is a stronger guarantee than a rule saying not to write them.

    The conversation is *state*, kept in ``qk_coaching_messages`` because the next request needs it.
    It is not activity, and it does not belong in a stream that will be fanned out to dashboards.
    """

    __tablename__ = f"{TABLE_PREFIX}coaching_activity"

    id: Mapped[str] = id_column()

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    learner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(24), nullable=True)
    exchange_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Error code for SESSION_FAILED, from UC-07's taxonomy — never a provider message.
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(f"event_type IN ({_EVENT_VALUES})", name="event_type"),
        CheckConstraint(f"mode IS NULL OR mode IN ({_MODE_VALUES})", name="mode"),
        CheckConstraint(f"status IS NULL OR status IN ({_STATUS_VALUES})", name="status"),
        CheckConstraint("exchange_count >= 0", name="exchange_count_non_negative"),
        Index(f"ix_{TABLE_PREFIX}coaching_activity_session_id", "session_id"),
        Index(
            f"ix_{TABLE_PREFIX}coaching_activity_learner_attempt", "learner_id", "attempt_id"
        ),
        Index(f"ix_{TABLE_PREFIX}coaching_activity_event_type", "event_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CoachingActivity {self.event_type} {self.session_id}>"


# ---------------------------------------------------------------------------
# Integrity triggers
#
# Two append-only tables, expressed as a database property rather than as an application habit.
# Emitted per dialect so the guarantee survives the move to the company database, exactly as UC-01's
# immutable configuration versions, UC-04's confirmed scores and UC-06's generated reports are.
# ---------------------------------------------------------------------------

IMMUTABLE_MESSAGE_MESSAGE = (
    "IMMUTABLE_COACHING_MESSAGE: coaching messages are written once and cannot be modified"
)
IMMUTABLE_ACTIVITY_MESSAGE = (
    "IMMUTABLE_COACHING_ACTIVITY: coaching activity records are append-only"
)

_MESSAGES = f"{TABLE_PREFIX}coaching_messages"
_ACTIVITY = f"{TABLE_PREFIX}coaching_activity"

MESSAGE_TRIGGER = "trg_qk_message_no_update"
ACTIVITY_TRIGGER = "trg_qk_activity_no_update"

#: Table -> trigger, consumed by both the ``create_all`` hooks below and the Alembic migration, so a
#: migrated database and a ``create_all`` database cannot end up with different guarantees.
IMMUTABLE_TABLES: tuple[tuple[str, str], ...] = (
    (_MESSAGES, MESSAGE_TRIGGER),
    (_ACTIVITY, ACTIVITY_TRIGGER),
)

POSTGRES_MESSAGE_FN = f"""
CREATE OR REPLACE FUNCTION fn_reject_coaching_message_update()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION '{IMMUTABLE_MESSAGE_MESSAGE}';
END; $$ LANGUAGE plpgsql;
"""

POSTGRES_ACTIVITY_FN = f"""
CREATE OR REPLACE FUNCTION fn_reject_coaching_activity_update()
RETURNS trigger AS $$ BEGIN
  RAISE EXCEPTION '{IMMUTABLE_ACTIVITY_MESSAGE}';
END; $$ LANGUAGE plpgsql;
"""


def _sqlite_statements() -> dict[str, list[str]]:
    return {
        _MESSAGES: [
            f"""
CREATE TRIGGER {MESSAGE_TRIGGER}
BEFORE UPDATE ON {_MESSAGES}
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_MESSAGE_MESSAGE}');
END;
"""
        ],
        _ACTIVITY: [
            f"""
CREATE TRIGGER {ACTIVITY_TRIGGER}
BEFORE UPDATE ON {_ACTIVITY}
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_ACTIVITY_MESSAGE}');
END;
"""
        ],
    }


def _postgres_statements() -> dict[str, list[str]]:
    return {
        _MESSAGES: [
            POSTGRES_MESSAGE_FN,
            f"""
CREATE TRIGGER {MESSAGE_TRIGGER}
BEFORE UPDATE ON {_MESSAGES}
FOR EACH ROW EXECUTE FUNCTION fn_reject_coaching_message_update();
""",
        ],
        _ACTIVITY: [
            POSTGRES_ACTIVITY_FN,
            f"""
CREATE TRIGGER {ACTIVITY_TRIGGER}
BEFORE UPDATE ON {_ACTIVITY}
FOR EACH ROW EXECUTE FUNCTION fn_reject_coaching_activity_update();
""",
        ],
    }


def sqlite_trigger_statements() -> list[str]:
    """SQLite DDL for UC-07's append-only triggers. Used by the Alembic migration."""
    return [statement for group in _sqlite_statements().values() for statement in group]


def postgres_trigger_statements() -> list[str]:
    """PostgreSQL DDL for the same guarantees, functions first."""
    return [statement for group in _postgres_statements().values() for statement in group]


def trigger_names() -> list[str]:
    return [trigger for _table, trigger in IMMUTABLE_TABLES]


def trigger_table(trigger_name: str) -> str:
    for table, trigger in IMMUTABLE_TABLES:
        if trigger == trigger_name:
            return table
    raise ValueError(f"unknown trigger {trigger_name}")


def _attach_triggers() -> None:
    """Emit each trigger right after the table it protects is created."""
    sqlite_by_table = _sqlite_statements()
    postgres_by_table = _postgres_statements()
    for table_name, statements in sqlite_by_table.items():
        table = Base.metadata.tables[table_name]
        for statement in statements:
            event.listen(table, "after_create", DDL(statement).execute_if(dialect="sqlite"))
        for statement in postgres_by_table[table_name]:
            event.listen(table, "after_create", DDL(statement).execute_if(dialect="postgresql"))


_attach_triggers()


def is_immutability_violation(error: BaseException) -> bool:
    """True when a database trigger rejected an edit to a stored message or activity record."""
    text = str(error)
    return IMMUTABLE_MESSAGE_MESSAGE in text or IMMUTABLE_ACTIVITY_MESSAGE in text
