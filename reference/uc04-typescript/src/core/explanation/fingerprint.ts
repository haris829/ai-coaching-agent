import { contentTokens, jaccard, stableHash } from '../text';

/**
 * Stable explanation fingerprinting.
 *
 * Two mechanisms, both used:
 *  1. `fingerprint`  - an exact, stable hash of the normalized content-token stream. Identical
 *     (or trivially reformatted) explanations collide here.
 *  2. `tokens`       - the normalized token set, used for NEAR-duplicate detection via Jaccard
 *     similarity, so a reworded but substantively identical explanation is still rejected.
 *
 * The fingerprint is content-only: it deliberately ignores framing labels and headings so that
 * "same explanation with a different heading" cannot slip through as new.
 */
export interface ExplanationFingerprint {
  fingerprint: string;
  tokens: string[];
}

/** Similarity at or above this is treated as "effectively identical". */
export const NEAR_DUPLICATE_THRESHOLD = 0.82;

export function fingerprintExplanation(text: string): ExplanationFingerprint {
  const tokens = contentTokens(text);
  // Sorted + de-duplicated so word order changes alone do not create a "new" explanation.
  const canonical = Array.from(new Set(tokens)).sort();
  return {
    fingerprint: stableHash(canonical.join(' ')),
    tokens: canonical,
  };
}

export interface DuplicateVerdict {
  isDuplicate: boolean;
  /** 'EXACT' when fingerprints collide, 'NEAR' when similarity crosses the threshold. */
  reason: 'EXACT' | 'NEAR' | null;
  similarity: number;
}

export function isEffectivelyIdentical(
  candidate: ExplanationFingerprint,
  previous: readonly { explanation_fingerprint: string; fingerprint_tokens: string[] }[],
  threshold: number = NEAR_DUPLICATE_THRESHOLD,
): DuplicateVerdict {
  let maxSimilarity = 0;
  for (const attempt of previous) {
    if (attempt.explanation_fingerprint === candidate.fingerprint) {
      return { isDuplicate: true, reason: 'EXACT', similarity: 1 };
    }
    const similarity = jaccard(candidate.tokens, attempt.fingerprint_tokens);
    if (similarity > maxSimilarity) maxSimilarity = similarity;
  }
  if (maxSimilarity >= threshold) {
    return { isDuplicate: true, reason: 'NEAR', similarity: round(maxSimilarity) };
  }
  return { isDuplicate: false, reason: null, similarity: round(maxSimilarity) };
}

function round(value: number): number {
  return Math.round(value * 10000) / 10000;
}
