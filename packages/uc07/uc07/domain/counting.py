"""THE single definition of what counts as a qualifying coaching interaction.

Nothing else in UC-07 is allowed to count interactions. The threshold check, the
report's ``source_interaction_count``, the aggregation input and every signal all
consume the output of :func:`qualifying_interactions`.

The rule (docs/assumptions.md A-01..A-04):

1. A record must be a valid :class:`~uc07.domain.models.InteractionRecord`.
   Records that cannot satisfy the platform contract never reach this function:
   adapters raise ``ProviderInvalidResponse`` instead of bending the model.
2. A record only counts for the learner it belongs to. Records whose ``user_id``
   differs from the requested learner are discarded (privacy + correctness).
3. Duplicate ``interaction_id`` values count once. The first occurrence in
   provider order wins; later duplicates are discarded.
4. Follow-ups count. Clarifying interactions count when represented as an
   ``InteractionRecord``. "Explain differently" is a *counter on* an interaction,
   not an interaction, so it never adds to the count.
5. Counting spans the learner's complete history, all sessions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from uc07.domain.models import InteractionRecord


@dataclass(frozen=True, slots=True)
class QualifyingInteractions:
    """Result of applying the counting rule."""

    records: tuple[InteractionRecord, ...]
    duplicates_discarded: int
    other_user_records_discarded: int

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(record.interaction_id for record in self.records)

    @property
    def session_ids(self) -> frozenset[str]:
        return frozenset(record.session_id for record in self.records)


def _sort_key(record: InteractionRecord) -> tuple[str, str]:
    # Deterministic, provider-order independent ordering.
    return (record.asked_at.isoformat(), record.interaction_id)


def qualifying_interactions(
    records: Iterable[InteractionRecord], *, user_id: str
) -> QualifyingInteractions:
    """Apply the qualifying-interaction rule to a provider's records."""
    seen: set[str] = set()
    kept: list[InteractionRecord] = []
    duplicates = 0
    other_user = 0

    for record in records:
        if record.user_id != user_id:
            other_user += 1
            continue
        if record.interaction_id in seen:
            duplicates += 1
            continue
        seen.add(record.interaction_id)
        kept.append(record)

    return QualifyingInteractions(
        records=tuple(sorted(kept, key=_sort_key)),
        duplicates_discarded=duplicates,
        other_user_records_discarded=other_user,
    )
