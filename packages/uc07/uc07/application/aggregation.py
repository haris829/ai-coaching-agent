"""Full-history aggregation.

Interactions are aggregated across **all** sessions for the resolved learner and
grouped strictly by the ``topic_tag`` already present on the record. UC-07 never
retags, classifies, infers or invents a topic; tags are consumed exactly as
supplied by the upstream source.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from uc07.domain.counting import QualifyingInteractions
from uc07.domain.models import InteractionRecord


@dataclass(frozen=True, slots=True)
class TopicAggregate:
    """Per-topic view of the learner's history. All tuples are sorted."""

    topic_tag: str
    interaction_ids: tuple[str, ...]
    session_ids: tuple[str, ...]
    explain_differently_total: int
    explain_differently_interaction_ids: tuple[str, ...]
    follow_up_interaction_ids: tuple[str, ...]

    @property
    def interaction_count(self) -> int:
        return len(self.interaction_ids)

    @property
    def follow_up_count(self) -> int:
        return len(self.follow_up_interaction_ids)


@dataclass(frozen=True, slots=True)
class HistoryAggregate:
    """Aggregated, deterministic view of a learner's complete history."""

    user_id: str
    interactions: tuple[InteractionRecord, ...]
    topics: tuple[TopicAggregate, ...]
    duplicates_discarded: int
    other_user_records_discarded: int

    @property
    def interaction_count(self) -> int:
        return len(self.interactions)

    @property
    def topic_tags(self) -> tuple[str, ...]:
        return tuple(topic.topic_tag for topic in self.topics)

    @property
    def interaction_ids(self) -> frozenset[str]:
        return frozenset(record.interaction_id for record in self.interactions)

    @property
    def session_count(self) -> int:
        return len({record.session_id for record in self.interactions})

    def topic_of(self, interaction_id: str) -> str | None:
        for record in self.interactions:
            if record.interaction_id == interaction_id:
                return record.topic_tag
        return None


def aggregate_history(qualifying: QualifyingInteractions, *, user_id: str) -> HistoryAggregate:
    """Group qualifying interactions by their supplied ``topic_tag``."""
    by_topic: dict[str, list[InteractionRecord]] = defaultdict(list)
    for record in qualifying.records:
        by_topic[record.topic_tag].append(record)

    topics: list[TopicAggregate] = []
    for topic_tag in sorted(by_topic):
        records = by_topic[topic_tag]
        explain_ids = sorted(
            record.interaction_id
            for record in records
            if record.explain_differently_count > 0
        )
        follow_up_ids = sorted(
            record.interaction_id for record in records if record.is_follow_up
        )
        topics.append(
            TopicAggregate(
                topic_tag=topic_tag,
                interaction_ids=tuple(sorted(r.interaction_id for r in records)),
                session_ids=tuple(sorted({r.session_id for r in records})),
                explain_differently_total=sum(
                    record.explain_differently_count for record in records
                ),
                explain_differently_interaction_ids=tuple(explain_ids),
                follow_up_interaction_ids=tuple(follow_up_ids),
            )
        )

    return HistoryAggregate(
        user_id=user_id,
        interactions=qualifying.records,
        topics=tuple(topics),
        duplicates_discarded=qualifying.duplicates_discarded,
        other_user_records_discarded=qualifying.other_user_records_discarded,
    )
