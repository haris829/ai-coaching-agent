import type { ExplanationAttempt } from '../../contracts/explanation-history-store';
import { DEFAULT_FRAMING_ORDER, FramingType } from '../../domain/enums';

/**
 * Chooses which framing to try next for a (session, concept) pair.
 *
 * Policy:
 *  1. Unused framings first, in the configured preference order.
 *  2. When every framing has been used, fall back to LEAST RECENTLY USED - never simply
 *     repeat the most recent one.
 *
 * Returns the full ordered candidate list so the caller can walk it when a generated
 * explanation turns out to be a near-duplicate of an earlier one.
 */
export interface FramingPlan {
  /** Ordered candidates, best first. Always non-empty. */
  candidates: FramingType[];
  /** Framings already used for this (session, concept). */
  used: FramingType[];
  /** True when every configured framing had already been used. */
  exhausted: boolean;
}

export class FramingSelector {
  constructor(private readonly order: readonly FramingType[] = DEFAULT_FRAMING_ORDER) {
    if (order.length === 0) throw new Error('FramingSelector requires at least one framing');
  }

  plan(attempts: readonly ExplanationAttempt[]): FramingPlan {
    const lastUsedAt = new Map<FramingType, string>();
    const usedInOrder: FramingType[] = [];
    for (const attempt of attempts) {
      if (!lastUsedAt.has(attempt.framing_type)) usedInOrder.push(attempt.framing_type);
      // attempts are appended chronologically, so the last write wins.
      lastUsedAt.set(attempt.framing_type, attempt.timestamp);
    }

    const unused = this.order.filter((f) => !lastUsedAt.has(f));
    const used = this.order
      .filter((f) => lastUsedAt.has(f))
      .sort((a, b) => {
        const ta = lastUsedAt.get(a) ?? '';
        const tb = lastUsedAt.get(b) ?? '';
        if (ta === tb) return this.order.indexOf(a) - this.order.indexOf(b);
        return ta < tb ? -1 : 1; // least recently used first
      });

    return {
      candidates: [...unused, ...used],
      used: usedInOrder,
      exhausted: unused.length === 0,
    };
  }

  /** First-turn framing when there is no history at all. */
  initial(): FramingType {
    return this.order[0] as FramingType;
  }
}
