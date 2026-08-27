"""Deterministic struggle-signal detection.

Three independent signals, each independently testable, each comparing an
observed value against a configured threshold:

* ``explain_differently`` - total "explain differently" requests on a topic.
* ``follow_up`` - number of follow-up interactions on a topic.
* ``low_rating`` - number of thumbs-down feedback records on a topic.

A topic below **all** configured thresholds is not a struggle. Signals combine:
a topic that crosses two thresholds carries two signals with separate evidence.

No LLM, no heuristics beyond the configured thresholds, no randomness.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from uc07.application.aggregation import HistoryAggregate, TopicAggregate
from uc07.application.config import AnalysisThresholds
from uc07.domain.enums import Rating, SIGNAL_ORDER, SignalKind
from uc07.domain.models import FeedbackRecord, SignalEvidence


@dataclass(frozen=True, slots=True)
class StruggleFinding:
    """A topic that crossed at least one configured struggle threshold."""

    topic_tag: str
    signals: tuple[SignalEvidence, ...]

    @property
    def signal_kinds(self) -> tuple[SignalKind, ...]:
        return tuple(item.signal for item in self.signals)

    @property
    def evidence_interaction_ids(self) -> tuple[str, ...]:
        ids: set[str] = set()
        for signal in self.signals:
            ids.update(signal.interaction_ids)
        return tuple(sorted(ids))


@dataclass(frozen=True, slots=True)
class LowRatingIndex:
    """Thumbs-down feedback mapped onto topics, or an explicit 'not evaluated'."""

    evaluated: bool
    counts: dict[str, int]
    interaction_ids: dict[str, tuple[str, ...]]

    @classmethod
    def not_evaluated(cls) -> "LowRatingIndex":
        return cls(evaluated=False, counts={}, interaction_ids={})


def build_low_rating_index(
    history: HistoryAggregate, feedback: list[FeedbackRecord] | None
) -> LowRatingIndex:
    """Map thumbs-down feedback onto topics.

    ``feedback is None`` means the rating source could not be read, which is a
    different state from "the learner has no ratings" (an empty list).

    Only feedback that belongs to the learner *and* points at an interaction used
    in this analysis is counted - a rating for an unknown interaction can never
    manufacture evidence.
    """
    if feedback is None:
        return LowRatingIndex.not_evaluated()

    topic_by_interaction = {
        record.interaction_id: record.topic_tag for record in history.interactions
    }
    counts: dict[str, int] = defaultdict(int)
    ids: dict[str, set[str]] = defaultdict(set)

    for record in feedback:
        if record.rating is not Rating.DOWN:
            continue
        if record.user_id != history.user_id:
            continue
        topic_tag = topic_by_interaction.get(record.interaction_id)
        if topic_tag is None:
            continue
        counts[topic_tag] += 1
        ids[topic_tag].add(record.interaction_id)

    return LowRatingIndex(
        evaluated=True,
        counts=dict(counts),
        interaction_ids={tag: tuple(sorted(v)) for tag, v in ids.items()},
    )


def _explain_differently_signal(
    topic: TopicAggregate, thresholds: AnalysisThresholds
) -> SignalEvidence | None:
    observed = topic.explain_differently_total
    threshold = thresholds.explain_differently_struggle_threshold
    if observed < threshold:
        return None
    return SignalEvidence(
        signal=SignalKind.EXPLAIN_DIFFERENTLY,
        observed_value=observed,
        threshold=threshold,
        interaction_ids=topic.explain_differently_interaction_ids,
    )


def _follow_up_signal(
    topic: TopicAggregate, thresholds: AnalysisThresholds
) -> SignalEvidence | None:
    observed = topic.follow_up_count
    threshold = thresholds.follow_up_struggle_threshold
    if observed < threshold:
        return None
    return SignalEvidence(
        signal=SignalKind.FOLLOW_UP,
        observed_value=observed,
        threshold=threshold,
        interaction_ids=topic.follow_up_interaction_ids,
    )


def _low_rating_signal(
    topic: TopicAggregate, thresholds: AnalysisThresholds, low_ratings: LowRatingIndex
) -> SignalEvidence | None:
    if not low_ratings.evaluated:
        return None
    observed = low_ratings.counts.get(topic.topic_tag, 0)
    threshold = thresholds.low_rating_struggle_threshold
    if observed < threshold:
        return None
    return SignalEvidence(
        signal=SignalKind.LOW_RATING,
        observed_value=observed,
        threshold=threshold,
        interaction_ids=low_ratings.interaction_ids.get(topic.topic_tag, ()),
    )


def detect_struggles(
    history: HistoryAggregate,
    thresholds: AnalysisThresholds,
    low_ratings: LowRatingIndex,
) -> tuple[StruggleFinding, ...]:
    """Return one finding per topic that crossed at least one threshold.

    Findings are ordered by ``topic_tag`` and each finding's signals follow the
    canonical signal order, so the output is byte-stable for stable input.
    """
    findings: list[StruggleFinding] = []
    for topic in history.topics:
        candidates = [
            _explain_differently_signal(topic, thresholds),
            _follow_up_signal(topic, thresholds),
            _low_rating_signal(topic, thresholds, low_ratings),
        ]
        signals = tuple(
            sorted(
                (item for item in candidates if item is not None),
                key=lambda evidence: SIGNAL_ORDER.index(evidence.signal),
            )
        )
        if not signals:
            continue
        findings.append(StruggleFinding(topic_tag=topic.topic_tag, signals=signals))
    return tuple(sorted(findings, key=lambda finding: finding.topic_tag))
