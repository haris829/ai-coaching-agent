"""The content review flagging rule.

Nothing in this module holds a threshold, a minimum sample size or a window length as a
constant.  Every number arrives in :class:`FlaggingPolicy`, which the application layer
builds from :class:`~uc10.ports.threshold_config_provider.ThresholdConfigProvider` at
evaluation time.  An automated test asserts that no numeric threshold literal exists
anywhere in the domain or application packages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from uc10.domain.enums import RatingValue
from uc10.domain.models import RatingRecord
from uc10.domain.window import Window


@dataclass(frozen=True, slots=True)
class FlaggingPolicy:
    """The rule in force for one evaluation, captured so a flag can record what produced it."""

    down_rate_threshold: float
    minimum_sample_size: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.down_rate_threshold <= 1.0:
            raise ValueError("down-rate threshold must be a fraction between 0 and 1")
        if self.minimum_sample_size < 1:
            raise ValueError("minimum sample size must be at least 1")


class DecisionReason(StrEnum):
    FLAGGED = "flagged"
    BELOW_MINIMUM_SAMPLE = "below_minimum_sample"
    BELOW_THRESHOLD = "below_threshold"
    NO_RATINGS = "no_ratings"


@dataclass(frozen=True, slots=True)
class FlagCandidate:
    """A flag the rule says should exist. Carries counts and identifiers only."""

    topic_tag: str
    window: Window
    total_ratings: int
    down_ratings: int
    down_rate: float
    threshold_applied: float
    minimum_sample_size_applied: int
    flagging_interaction_ids: tuple[str, ...]
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class FlagDecision:
    topic_tag: str
    reason: DecisionReason
    total_ratings: int
    down_ratings: int
    down_rate: float
    candidate: FlagCandidate | None

    @property
    def flagged(self) -> bool:
        return self.reason is DecisionReason.FLAGGED


def _meets_threshold(down: int, total: int, threshold: float) -> bool:
    """``down / total >= threshold`` without float division error.

    Compared as ``down >= threshold * total`` in Decimal so that a dataset sitting
    *exactly* on the configured threshold flags, deterministically, on every platform.
    """
    return Decimal(down) >= Decimal(str(threshold)) * Decimal(total)


def evaluate_topic(
    *,
    topic_tag: str,
    ratings: list[RatingRecord],
    window: Window,
    policy: FlaggingPolicy,
    evaluated_at: datetime,
) -> FlagDecision:
    """Apply the rolling-window rule to one topic, across all users.

    ``ratings`` must already be the *current* (non-superseded) ratings for this topic
    inside ``window``: a learner who flips thumbs down to thumbs up contributes their
    latest verdict once, not both.
    """
    in_window = [r for r in ratings if r.topic_tag == topic_tag and window.contains(r.rated_at)]
    total = len(in_window)
    downs = [r for r in in_window if r.rating is RatingValue.DOWN]
    down_count = len(downs)
    rate = round(down_count / total, 6) if total else 0.0

    if total == 0:
        reason = DecisionReason.NO_RATINGS
    elif total < policy.minimum_sample_size:
        # Without this guard a single thumbs down sits at 100% and flags a sample of one.
        reason = DecisionReason.BELOW_MINIMUM_SAMPLE
    elif not _meets_threshold(down_count, total, policy.down_rate_threshold):
        reason = DecisionReason.BELOW_THRESHOLD
    else:
        reason = DecisionReason.FLAGGED

    candidate = None
    if reason is DecisionReason.FLAGGED:
        # Order preserved by rating time so the identifier list is stable across retries.
        by_time = sorted(downs, key=lambda r: r.rated_at)
        ids = tuple(dict.fromkeys(r.interaction_id for r in by_time))
        candidate = FlagCandidate(
            topic_tag=topic_tag,
            window=window,
            total_ratings=total,
            down_ratings=down_count,
            down_rate=rate,
            threshold_applied=policy.down_rate_threshold,
            minimum_sample_size_applied=policy.minimum_sample_size,
            flagging_interaction_ids=ids,
            evaluated_at=evaluated_at,
        )

    return FlagDecision(
        topic_tag=topic_tag,
        reason=reason,
        total_ratings=total,
        down_ratings=down_count,
        down_rate=rate,
        candidate=candidate,
    )


def topics_in(ratings: list[RatingRecord]) -> list[str]:
    """Every topic present in a rating set, in first-seen order."""
    return list(dict.fromkeys(r.topic_tag for r in ratings))
