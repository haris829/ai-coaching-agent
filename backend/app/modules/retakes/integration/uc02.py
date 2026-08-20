"""UC-02 (Question Bank Management) — the contract UC-08 consumes.

UC-08 reads the question bank for exactly one purpose: to work out **how many alternatives
exist** before it asks UC-03 to deliver a retake. It does not author, edit, retire, validate or
select question content, and it never sees an answer key — the descriptor below carries an id, a
type and a topic, and nothing a learner could be shown or graded against.

Two rules live at this boundary.

**Retired questions are not alternatives.** ``exclude_retired`` defaults to True and UC-08 never
sets it False. §8 is explicit that reuse is preferable to reaching for a retired question, and
the cheapest way to guarantee that is never to ask for one: a retired question is absent from
every pool this module counts, so it cannot be counted as an alternative and cannot influence the
decision to exclude.

**The bank is asked for a pool, not for a paper.** Count, quotas, randomisation and ordering are
UC-03's selection rules and stay there (§6). UC-08 sizes the pool and decides what to exclude.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class QuestionDescriptor:
    """A question, reduced to what a *counting* decision needs.

    Deliberately not UC-02's question: there is no prompt, no option, no correct answer and no
    explanation on this type. UC-08 decides how many questions of each type are available and
    which ids the learner has already seen; it has no business holding content, and a type that
    cannot hold content cannot leak it.
    """

    question_id: str
    question_type: str
    topic_id: str | None = None
    #: Present so a permissive adapter that ignores ``exclude_retired`` is still filtered by the
    #: pool logic rather than trusted. Defaults to False so an adapter that omits it is safe.
    retired: bool = False


@dataclass(frozen=True, slots=True)
class QuestionPoolQuery:
    """The filter UC-08 applies when sizing the pool available to a retake."""

    quiz_id: str
    course_id: str
    #: Restricts the pool to the types the configuration version permits or requires.
    types: tuple[str, ...] = field(default_factory=tuple)
    topic_ids: tuple[str, ...] = field(default_factory=tuple)
    #: Never set False by UC-08. See the module docstring.
    exclude_retired: bool = True


@runtime_checkable
class QuestionBankProvider(Protocol):
    """Read-only port onto UC-02.

    A transient failure must raise ``app.core.errors.ProviderUnavailableError`` (or the more
    specific ``QuestionBankUnavailableError``) so the retake is refused rather than planned
    against an empty pool that would look like "no alternatives exist".
    """

    async def find_eligible_questions(
        self, query: QuestionPoolQuery
    ) -> tuple[QuestionDescriptor, ...]:
        """Every non-retired question matching the filter.

        Implementations must exclude retired questions unless ``query.exclude_retired`` is
        explicitly False, and must not shuffle, limit or otherwise pre-select: sizing the pool is
        the caller's job and a truncated pool would silently understate the alternatives.
        """
        ...
