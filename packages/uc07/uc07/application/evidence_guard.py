"""Evidence integrity guard.

A gap whose evidence cannot be resolved back to an interaction actually used in
the analysis is a defect. The guard rejects such a gap instead of emitting it, so
a fabricated or unknown evidence id can never reach a report.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from uc07.domain.enums import EvidenceBasis, GapType
from uc07.domain.models import Gap


@dataclass(frozen=True, slots=True)
class EvidenceGuardResult:
    gaps: tuple[Gap, ...]
    rejected_gap_count: int
    rejection_reasons: tuple[str, ...]


def _violations(gap: Gap, resolvable_ids: frozenset[str]) -> list[str]:
    reasons: list[str] = []
    if gap.gap_type is GapType.STRUGGLE:
        if gap.evidence.basis is not EvidenceBasis.INTERACTION_IDS:
            reasons.append("struggle_gap_without_interaction_evidence")
        if not gap.evidence.interaction_ids:
            reasons.append("struggle_gap_with_empty_evidence")
        unknown = set(gap.evidence.interaction_ids) - resolvable_ids
        if unknown:
            reasons.append("evidence_id_does_not_resolve")
    else:
        if gap.evidence.interaction_ids:
            reasons.append("unexplored_gap_with_interaction_evidence")
        if gap.evidence.basis is not EvidenceBasis.ZERO_INTERACTIONS_FOR_SPECIALITY_AREA:
            reasons.append("unexplored_gap_with_wrong_evidence_basis")

    for signal in gap.evidence.per_signal:
        if set(signal.interaction_ids) - set(gap.evidence.interaction_ids):
            reasons.append("signal_evidence_outside_gap_evidence")
        if set(signal.interaction_ids) - resolvable_ids:
            reasons.append("signal_evidence_id_does_not_resolve")
    return reasons


def enforce_evidence_integrity(
    gaps: Sequence[Gap], resolvable_interaction_ids: Iterable[str]
) -> EvidenceGuardResult:
    """Drop any gap whose evidence does not resolve to a used interaction."""
    resolvable = frozenset(resolvable_interaction_ids)
    kept: list[Gap] = []
    reasons: list[str] = []

    for gap in gaps:
        gap_reasons = _violations(gap, resolvable)
        if gap_reasons:
            reasons.extend(sorted(set(gap_reasons)))
            continue
        kept.append(gap)

    return EvidenceGuardResult(
        gaps=tuple(kept),
        rejected_gap_count=len(gaps) - len(kept),
        rejection_reasons=tuple(reasons),
    )
