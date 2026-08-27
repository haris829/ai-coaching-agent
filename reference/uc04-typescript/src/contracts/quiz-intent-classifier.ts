import type { QuizIntentLabel } from '../domain/enums';

/**
 * PORT: Quiz answer-seeking detection.
 * TODAY  -> HeuristicQuizIntentClassifier (multi-signal, weighted - not a keyword list)
 * LATER  -> ML/LLM classifier implementing the SAME interface
 *
 * The classifier is ADVISORY. UC-04 decides the final action, and quiz protection can
 * never be disabled by anything in the client request.
 */
export interface QuizClassificationInput {
  question: string;
  /**
   * Server-derived hint that the session sits next to an assessment. May only ever make
   * protection STRONGER; it is derived server-side and never read from the client body.
   */
  assessmentContext: boolean;
}

export interface QuizClassification {
  label: QuizIntentLabel;
  /** 0..1. */
  confidence: number;
  /** Named signals that fired, for audit/tuning. Must not contain lesson content. */
  signals: string[];
  classifier: string;
}

export interface QuizIntentClassifier {
  classify(input: QuizClassificationInput): Promise<QuizClassification>;
}
