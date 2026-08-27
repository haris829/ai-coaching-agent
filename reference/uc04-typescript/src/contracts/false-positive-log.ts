import type { ProtectionDecision, QuizIntentLabel } from '../domain/enums';

/**
 * PORT: Suspected quiz-classifier false positives, kept for future model tuning.
 * TODAY  -> InMemoryFalsePositiveLog
 * LATER  -> Company database
 *
 * PRIVACY: the record deliberately stores the user's question (needed for tuning) but
 * NEVER lesson content, the produced explanation, or any answer key material.
 */
export interface FalsePositiveRecord {
  record_id: string;
  session_id: string;
  user_id: string;
  question: string;
  classifier_result: QuizIntentLabel;
  classifier_confidence: number;
  classifier_signals: string[];
  final_decision: ProtectionDecision;
  timestamp: string;
}

export interface FalsePositiveLog {
  record(entry: FalsePositiveRecord): Promise<void>;
  list(sessionId?: string): Promise<FalsePositiveRecord[]>;
}
