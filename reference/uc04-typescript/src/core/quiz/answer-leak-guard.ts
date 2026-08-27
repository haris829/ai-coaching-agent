/**
 * Last line of defence for quiz protection.
 *
 * Even though the explanation engine is lesson-grounded (and lesson prose does not contain an
 * answer key), any text returned on a QUIZ_PROTECTED turn is passed through this guard. It
 * strips sentences that would reveal or confirm an answer through wording, elimination or
 * "almost correct" feedback.
 *
 * The guard is defensive, not clever: it drops whole sentences rather than trying to rewrite
 * them, and reports what it removed so the behaviour is testable.
 */

const LEAK_PATTERNS: RegExp[] = [
  /\b(the\s+)?(correct|right)\s+(answer|option|choice)\b/i,
  /\banswer\s+is\b/i,
  /\bis\s+(the\s+)?correct\b/i,
  /\boption\s+[a-e]\b/i,
  /\b(choose|pick|select)\s+[a-e]\b/i,
  /\b[a-e]\s+is\s+(correct|right|wrong|incorrect)\b/i,
  /\b(rule|cross)\s+out\b/i,
  /\beliminate\s+(the\s+)?(wrong|incorrect)\b/i,
  /\byou'?re?\s+(almost|nearly)\s+(right|correct)\b/i,
  /\bthat'?s\s+(right|correct|wrong|incorrect)\b/i,
  /\banswer\s+key\b/i,
];

export interface LeakGuardResult {
  text: string;
  /** True when anything was removed. */
  redacted: boolean;
  removedCount: number;
}

export function stripAnswerLeaks(text: string): LeakGuardResult {
  if (!text) return { text, redacted: false, removedCount: 0 };

  const lines = text.split('\n');
  let removedCount = 0;

  const cleanedLines = lines.map((line) => {
    // Split into sentence-ish units so one bad clause does not take a whole paragraph with it.
    const units = line.split(/(?<=[.!?])\s+/);
    const kept = units.filter((unit) => {
      const leaks = LEAK_PATTERNS.some((p) => p.test(unit));
      if (leaks) removedCount += 1;
      return !leaks;
    });
    return kept.join(' ');
  });

  const cleaned = cleanedLines
    .filter((line, index) => line.trim().length > 0 || lines[index]?.trim().length === 0)
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  return { text: cleaned, redacted: removedCount > 0, removedCount };
}

/** Assertion helper used in tests and by the service before returning a protected response. */
export function containsAnswerLeak(text: string): boolean {
  return LEAK_PATTERNS.some((p) => p.test(text));
}
