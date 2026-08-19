/**
 * Contract types for UC-07 — AI Coaching Review Mode ("Review with Larry").
 *
 * Hand-written from `docs/API.md`, exactly as the UC-01/02/03 and results-chain types are, so the test
 * UI compiles against the shape the backend documents. Only the fields this UI reads are declared.
 *
 * TWO THINGS TO NOTICE IN THE SHAPES BELOW
 * ----------------------------------------
 * **`coachingAvailable` is on every level** — the attempt, each question, each review item, each
 * session operation. The backend states whether the action may be offered and this UI decides how to
 * render it; nothing in the browser works out whether coaching is allowed.
 *
 * **There is no field for a correct answer.** Not on a review item, not on a coaching turn. The
 * answer key never reaches the AI coach, and it never reaches this client either — the learner reads
 * the correct answer on their feedback report (UC-06), which is a different screen with a different
 * purpose.
 */

/** Why coaching is or is not available. One code per reason, so a refusal can be rendered. */
export type CoachingReason =
  | 'ELIGIBLE'
  | 'ATTEMPT_NOT_FOUND'
  | 'NOT_ATTEMPT_OWNER'
  | 'ATTEMPT_NOT_SUBMITTED'
  | 'SCORE_NOT_CONFIRMED'
  | 'FEEDBACK_UNAVAILABLE'
  | 'QUESTION_NOT_IN_ATTEMPT'
  | 'QUESTION_NOT_INCORRECT'
  | 'SERVICE_UNAVAILABLE';

export type CoachingMode = 'SOCRATIC' | 'DIRECT_EXPLANATION';
export type CoachingSessionStatus = 'ACTIVE' | 'COMPLETED' | 'FAILED' | 'UNAVAILABLE';
export type ReviewItemStatus = 'PENDING' | 'IN_PROGRESS' | 'COMPLETED';

/** Whether coaching may be offered for one question of the attempt. */
export interface QuestionEligibility {
  readonly questionId: string;
  readonly position: number;
  readonly outcome: string;
  readonly coachingAvailable: boolean;
  readonly reason: CoachingReason;
}

/** The attempt-level verdict, plus the per-question breakdown when there is one. */
export interface CoachingEligibility {
  readonly attemptId: string;
  readonly coachingAvailable: boolean;
  readonly reason: CoachingReason;
  readonly message: string | null;
  /** True when asking again later could produce a different answer. */
  readonly retryable: boolean;
  readonly details?: Readonly<Record<string, unknown>> | null;
  readonly questions: ReadonlyArray<QuestionEligibility>;
  readonly incorrectQuestionCount: number;
}

/** One incorrectly answered question in the review queue. */
export interface ReviewItem {
  readonly questionId: string;
  readonly position: number;
  readonly status: ReviewItemStatus;
  readonly topic: string | null;
  readonly sessionId: string | null;
  readonly exchangeCount: number;
  readonly coachingAvailable: boolean;
}

/** Every incorrect question on the attempt, in delivery order. Progress is derived, not stored. */
export interface ReviewQueue {
  readonly attemptId: string;
  readonly totalIncorrect: number;
  readonly completedCount: number;
  readonly remainingCount: number;
  readonly finished: boolean;
  readonly items: ReadonlyArray<ReviewItem>;
  readonly nextQuestionId: string | null;
}

export interface ReviewAdvance {
  readonly completedQuestionId: string | null;
  readonly nextQuestion: ReviewItem | null;
  readonly review: ReviewQueue;
}

/** The coaching session's state. */
export interface CoachingSession {
  readonly sessionId: string;
  readonly learnerId: string;
  readonly attemptId: string;
  readonly courseId: string;
  readonly questionId: string;
  readonly questionPosition: number | null;
  readonly topic: string | null;
  readonly mode: CoachingMode;
  readonly status: CoachingSessionStatus;
  readonly exchangeCount: number;
  /** The five-exchange transition, as a flag this UI acts on rather than counts towards itself. */
  readonly directExplanationAvailable: boolean;
  readonly directExplanationOffered: boolean;
  readonly directExplanationThreshold: number;
  readonly exchangesUntilChoice: number;
  readonly startedAt: string;
  readonly updatedAt: string;
  readonly completedAt: string | null;
  readonly lastFailureCode: string | null;
  readonly revision: number;
}

/** One turn of the conversation. */
export interface CoachingMessage {
  readonly role: 'LEARNER' | 'COACH';
  readonly content: string;
  readonly index: number;
  readonly createdAt: string;
  readonly mode: CoachingMode | null;
}

/**
 * What the answer-key sanitiser removed on the way in: names and counts, never values.
 *
 * Rendered in the panel because it is the visible evidence of the security boundary — a reviewer can
 * see on screen that the answer key was excluded, rather than having to take it on trust.
 */
export interface Sanitization {
  readonly removedFields: ReadonlyArray<string>;
  readonly scrubbedFields: ReadonlyArray<string>;
  readonly forbiddenValueCount: number;
  readonly contaminationFindings: ReadonlyArray<string>;
  readonly answerKeyExcluded: boolean;
}

export interface SessionState {
  readonly session: CoachingSession;
  readonly messageCount: number;
  readonly messages: ReadonlyArray<CoachingMessage>;
}

/** The result of starting or resuming coaching. `RESUMED` is what makes the button idempotent. */
export interface StartCoaching extends SessionState {
  readonly outcome: 'STARTED' | 'RESUMED' | 'UNAVAILABLE';
  readonly coachingAvailable: boolean;
  /** An error code when the coach could not speak. Never a provider message. */
  readonly reason: string | null;
  readonly sanitization: Sanitization | null;
}

/** The result of one coach turn. */
export interface CoachingExchange extends SessionState {
  readonly outcome: 'COMPLETED' | 'UNAVAILABLE';
  readonly coachingAvailable: boolean;
  readonly reason: string | null;
  readonly retryable: boolean;
  readonly reply: CoachingMessage | null;
}
