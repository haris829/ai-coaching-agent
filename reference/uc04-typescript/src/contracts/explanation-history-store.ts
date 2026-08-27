import type { FramingType } from '../domain/enums';

/**
 * PORT: Session-scoped explanation history (the "Explain Differently" memory).
 * TODAY  -> InMemoryExplanationHistoryStore
 * LATER  -> Company session store / cache implementing the SAME interface
 *
 * History is scoped to (session_id, concept_key). A new session starts with a clean slate.
 */
export interface ExplanationAttempt {
  session_id: string;
  /** Concept id when lesson-grounded, else a stable topic key for GENERAL answers. */
  concept_id: string;
  framing_type: FramingType;
  /** Stable content fingerprint (see core/explanation/fingerprint.ts). */
  explanation_fingerprint: string;
  /** Normalized token set used for near-duplicate detection. */
  fingerprint_tokens: string[];
  timestamp: string;
}

export interface ExplanationHistoryStore {
  listAttempts(sessionId: string, conceptId: string): Promise<ExplanationAttempt[]>;
  record(attempt: ExplanationAttempt): Promise<void>;
  /** Test/ops helper; not used by request handling. */
  clearSession(sessionId: string): Promise<void>;
}
