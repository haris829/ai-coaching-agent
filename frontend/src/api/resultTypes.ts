/**
 * Contract types for the results chain — UC-04 scoring, UC-05 pass/fail and certificate, UC-06
 * feedback.
 *
 * Hand-written from `docs/API.md` rather than generated, exactly as the UC-01/02/03 types are, so the
 * test UI compiles against the shape the backend documents. Only the fields this UI reads are declared:
 * a partial type that fails to compile when a field it needs disappears is more useful here than an
 * exhaustive mirror nobody maintains.
 */

/** UC-04 — one attempt's score. `PENDING_SCORE` is shown to a learner as `statusLabel`. */
export interface AttemptResult {
  readonly resultId: string;
  readonly attemptId: string;
  readonly status: 'SCORED' | 'PENDING_SCORE';
  readonly statusLabel: string;
  readonly totalMarks: number;
  readonly maximumMarks: number;
  readonly percentage: number;
  readonly passMarkPercentage: number;
  readonly totalQuestions: number;
  readonly correctCount: number;
  readonly incorrectCount: number;
  readonly unansweredCount: number;
  readonly timeTakenSeconds: number | null;
  readonly scoredAt: string | null;
  readonly configurationVersion: number;
  readonly anomalies: ReadonlyArray<{ readonly code: string; readonly questionId?: string }>;
  readonly failureCode: string | null;
  readonly failureMessage: string | null;
}

/** UC-04 — the marks for one question, and what the learner and the key said. */
export interface QuestionScore {
  readonly questionId: string;
  readonly questionType: string;
  readonly position: number;
  readonly questionText: string;
  readonly awardedMarks: number;
  readonly maximumMarks: number;
  readonly rawMarks: number;
  readonly deduction: number;
  readonly outcome: string;
  readonly answered: boolean;
  readonly answerKeySource: string | null;
  readonly anomaly: string | null;
}

export interface ResultResponse {
  readonly result: AttemptResult;
  readonly questionScores: readonly QuestionScore[];
}

/** UC-05 — the pass/fail determination. */
export interface AttemptOutcome {
  readonly outcomeId: string;
  readonly attemptId: string;
  readonly outcome: 'PASS' | 'FAIL';
  readonly outcomeLabel: string;
  readonly passed: boolean;
  readonly percentage: number;
  readonly passMarkPercentage: number;
  readonly totalMarks: number;
  readonly maximumMarks: number;
  /** The version the attempt was locked to — the one the verdict was judged against. */
  readonly configurationVersionId: string;
  readonly certificateRequired: boolean;
  readonly determinedAt: string | null;
}

/** UC-05 — the certificate, present only for a pass. */
export interface Certificate {
  readonly certificateId: string;
  readonly status: 'PENDING' | 'ISSUED' | 'FAILED';
  readonly certificateNumber: string | null;
  readonly documentReference: string | null;
  readonly courseName: string;
  readonly quizTitle: string | null;
  readonly generationAttemptCount: number;
  readonly issuedAt: string | null;
  readonly failureCode: string | null;
  readonly failureMessage: string | null;
}

/** UC-05 — the CPD record: attempt date, score, pass/fail and course name. */
export interface CpdRecord {
  readonly cpdRecordId: string;
  readonly status: 'PENDING' | 'SYNCHRONISED' | 'FAILED';
  readonly attemptDate: string | null;
  readonly scorePercentage: number;
  readonly passed: boolean;
  readonly courseName: string;
  readonly externalReference: string | null;
  readonly syncAttemptCount: number;
  readonly failureCode: string | null;
  readonly failureMessage: string | null;
}

export interface OutcomeResponse {
  readonly outcome: AttemptOutcome;
  readonly certificate: Certificate | null;
  readonly cpd: CpdRecord | null;
  readonly attemptsUsed: number;
  readonly attemptsRemaining: number | null;
  readonly maxAttempts: number | null;
  readonly mayReattempt: boolean;
}

/** UC-06 — one question's feedback. Every field is required by the specification. */
export interface FeedbackItem {
  readonly position: number;
  readonly questionId: string;
  readonly questionType: string;
  readonly questionReference: string | null;
  readonly question: string;
  readonly scenarioText: string | null;
  readonly learnerAnswer: FeedbackAnswer;
  readonly correctAnswer: FeedbackAnswer;
  readonly explanation: string;
  readonly lessonReference: string;
  readonly questionScore: number;
  readonly maximumMarks: number;
  readonly deduction: number;
  readonly outcome: string;
  readonly answered: boolean;
  readonly optionBreakdown: readonly FeedbackOption[];
}

/**
 * A rendered answer: option texts plus a one-line summary.
 *
 * `labels` is deliberately mutable rather than `readonly`, so a renderer can join or sort it without
 * copying — nothing in this UI mutates a response body.
 */
export interface FeedbackAnswer {
  summary?: string;
  labels?: string[];
  optionIds?: string[];
  orderedItemIds?: string[];
}

/** UC-06 — per-option detail, required for multi-select. */
export interface FeedbackOption {
  readonly optionId: string;
  readonly text: string;
  readonly selected: boolean;
  readonly correct: boolean;
  readonly markContribution: number;
  readonly feedback?: string;
}

export interface FeedbackSummary {
  readonly totalScore: number;
  readonly maximumMarks: number;
  readonly percentage: number;
  readonly passMarkPercentage: number;
  readonly passed: boolean | null;
  readonly timeTakenSeconds: number | null;
  readonly totalQuestions: number;
  readonly correctCount: number;
  readonly incorrectCount: number;
  readonly unansweredCount: number;
}

export interface FeedbackResponse {
  readonly feedbackId: string;
  readonly attemptId: string;
  readonly status: 'PENDING' | 'GENERATED' | 'FAILED';
  readonly statusLabel: string;
  readonly summary: FeedbackSummary;
  readonly items: readonly FeedbackItem[];
  readonly generatedAt: string | null;
  readonly failureCode: string | null;
  readonly failureMessage: string | null;
}

/** The per-option breakdown, under the name UC-04 gives it in its own payloads. */
export type OptionMark = FeedbackOption;
