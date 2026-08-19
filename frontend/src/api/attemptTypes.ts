/**
 * UC-03 wire types — quiz attempt delivery.
 *
 * Kept in their own module rather than merged into `types.ts` so the boundary stays visible: these
 * mirror what UC-03's presenters emit, and nothing here is shared with UC-01's or UC-02's shapes.
 * Note in particular that UC-03 identifies quizzes, courses and learners by **opaque string**, while
 * UC-01 and UC-02 use numeric ids — the string is deliberate, because those ids belong to another
 * capability and UC-03 never parses them.
 */

/** The five structures a learner can be asked. Identical vocabulary to UC-02's. */
export type AttemptQuestionType =
  | 'SINGLE_CHOICE'
  | 'TRUE_FALSE'
  | 'MULTI_SELECT'
  | 'DRAG_TO_ORDER'
  | 'SCENARIO';

/** Whether the paper is delivered as a whole or one question at a time. */
export type QuestionPresentation = 'ALL_AT_ONCE' | 'ONE_AT_A_TIME';

export type AttemptStatus =
  | 'ACTIVE'
  | 'SUBMITTED'
  | 'SUBMISSION_PENDING'
  | 'EXPIRED'
  | 'ABANDONED';

export type SubmissionState = 'PENDING' | 'SUBMITTED' | 'FAILED';

export interface AttemptOption {
  optionId: string;
  text: string;
}

export interface AttemptOrderItem {
  itemId: string;
  text: string;
}

export interface AttemptSubQuestion {
  subQuestionId: string;
  type: AttemptQuestionType;
  prompt: string;
  options?: AttemptOption[];
  orderItems?: AttemptOrderItem[];
  minSelections?: number;
  maxSelections?: number;
}

/**
 * A delivered question, read from the attempt's frozen snapshot.
 *
 * Grading data is absent by construction — the presenter strips it — so there is nothing for this
 * UI to accidentally reveal.
 */
export interface AttemptQuestion {
  questionId: string;
  position: number;
  questionType: AttemptQuestionType;
  questionVersion: number;
  points: number;
  prompt: string;
  scenarioText?: string;
  topicId?: string;
  options?: AttemptOption[];
  orderItems?: AttemptOrderItem[];
  subQuestions?: AttemptSubQuestion[];
  minSelections?: number;
  maxSelections?: number;
}

/** The effective rules of the attempt, from the configuration version locked at creation. */
export interface AttemptConfiguration {
  configurationVersionId: string;
  version: number;
  questionCount: number;
  timeLimitSeconds: number | null;
  passMarkPercentage: number | null;
  maxAttempts: number | null;
  questionPresentation: QuestionPresentation;
  randomiseQuestionOrder: boolean;
  randomiseOptionOrder: boolean;
  allowIncompleteSubmission: boolean;
  questionTypeQuotas: { type: AttemptQuestionType; count: number }[];
  activatedAt: string | null;
}

/**
 * Server-authoritative timing. The *only* source a countdown may trust.
 *
 * `reportedClientSkewSeconds` is advisory: the server echoes the difference it observed so the UI
 * can warn, but it never influences the remaining time.
 */
export interface AttemptTiming {
  serverTime: string;
  serverTimeEpochMs: number;
  status: AttemptStatus;
  startedAt: string;
  expiresAt: string | null;
  timeLimitSeconds: number | null;
  timed: boolean;
  elapsedSeconds: number;
  remainingSeconds: number | null;
  expired: boolean;
  submittedAt: string | null;
  clockResyncThresholdSeconds: number;
  autosaveIntervalSeconds: number;
  reportedClientSkewSeconds?: number;
}

export interface Attempt {
  attemptId: string;
  learnerId: string;
  courseId: string;
  quizId: string;
  attemptNumber: number;
  status: AttemptStatus;
  questionPresentation: QuestionPresentation;
  totalQuestions: number;
  currentPosition: number;
  startedAt: string;
  expiresAt: string | null;
  submittedAt: string | null;
  finalisedAt: string | null;
  submissionReason: string | null;
  lastActivityAt: string;
  configurationVersionId: string;
  configuration: AttemptConfiguration;
  timing?: AttemptTiming;
}

export interface AttemptEligibility {
  quizId: string;
  courseId: string | null;
  learnerId: string;
  eligible: boolean;
  /** Every reason the attempt is refused, not just the first — the UI lists them all. */
  reasons: { code: string; message: string }[];
  enrolled: boolean;
  enrolmentStatus: string | null;
  attemptsUsed: number;
  maxAttempts: number | null;
  attemptsRemaining: number | null;
  openAttemptId: string | null;
  activeConfigurationVersionId: string | null;
  activeConfigurationVersion: number | null;
}

/** Per-question progress. `complete` differs from `answered` for a partly answered scenario. */
export interface QuestionOutlineEntry {
  questionId: string;
  position: number;
  questionType: AttemptQuestionType;
  answered: boolean;
  complete: boolean;
  flagged: boolean;
}

export interface AttemptState {
  attemptId: string;
  status: AttemptStatus;
  questionPresentation: QuestionPresentation;
  currentPosition: number;
  totalQuestions: number;
  answeredCount: number;
  completeCount: number;
  unansweredCount: number;
  flaggedCount: number;
  questions: QuestionOutlineEntry[];
  timing: AttemptTiming;
}

/** The per-type answer payloads UC-03's validator accepts. */
export type SingleChoiceResponse = { selectedOptionId: string };
export type TrueFalseResponse = { value: boolean };
export type MultiSelectResponse = { selectedOptionIds: string[] };
export type DragToOrderResponse = { orderedItemIds: string[] };
export type ScenarioResponse = {
  responses: { subQuestionId: string; answer: SubAnswerResponse }[];
};
export type SubAnswerResponse =
  | SingleChoiceResponse
  | TrueFalseResponse
  | MultiSelectResponse
  | DragToOrderResponse;
export type AnswerResponse =
  | SingleChoiceResponse
  | TrueFalseResponse
  | MultiSelectResponse
  | DragToOrderResponse
  | ScenarioResponse
  | null;

export interface SavedAnswer {
  questionId: string;
  position: number;
  questionType: AttemptQuestionType;
  answered: boolean;
  complete: boolean;
  response: AnswerResponse;
  revision: number;
  source: string | null;
  savedAt: string | null;
  /** Present on a save response: false when the payload was identical to what was stored. */
  changed?: boolean;
}

/** The reload path: every delivered question, answered or not. */
export interface AnswerSheet {
  attemptId: string;
  status: AttemptStatus;
  totalQuestions: number;
  answeredCount: number;
  completeCount: number;
  answers: SavedAnswer[];
  timing: AttemptTiming;
}

export interface BatchSaveResult {
  saved: SavedAnswer[];
  savedCount: number;
  changedCount: number;
  timing: AttemptTiming;
  persistedAt: string;
}

export interface SubmissionPreview {
  attemptId: string;
  attemptStatus: AttemptStatus;
  totalQuestions: number;
  answeredCount: number;
  completeCount: number;
  unansweredCount: number;
  unanswered: { position: number; questionId: string }[];
  flagged: { position: number; questionId: string }[];
  allowIncompleteSubmission: boolean;
  /** False when a blocker applies; the confirmation button is disabled on it. */
  canSubmit: boolean;
  blockers: { code: string; message: string }[];
  warnings: { code: string; message: string }[];
  timing: AttemptTiming;
  requiresConfirmation: boolean;
  /** Reused across retries so a double-click cannot produce two submissions. */
  suggestedIdempotencyKey: string;
}

export interface SubmissionRecord {
  submissionId: string;
  state: SubmissionState;
  reason: string | null;
  idempotencyKey: string | null;
  downstreamReference: string | null;
  attempts: number;
  createdAt: string;
  updatedAt: string | null;
}

export interface SubmissionStatus {
  attemptId: string;
  status: AttemptStatus;
  submittedAt: string | null;
  history: SubmissionRecord[];
}

/** Whether a save was a deliberate learner action or the periodic autosave. */
export type AnswerSource = 'MANUAL' | 'AUTOSAVE';

/**
 * What creating an attempt returns.
 *
 * Deliberately *not* the questions. The paper is fetched separately, because where it comes from
 * depends on the locked presentation — the whole set for all-at-once, the current one otherwise — and
 * `questionsUrl` names the right endpoint so a client cannot pick the wrong one.
 */
export interface AttemptCreated {
  attempt: Attempt;
  delivery: {
    questionPresentation: QuestionPresentation;
    totalQuestions: number;
    questionTypeCounts: Record<string, number>;
    questionsUrl: string;
  };
}

/** What a single-answer save returns: the stored answer plus fresh authoritative timing. */
export interface SaveAnswerResult {
  answer: SavedAnswer;
  timing: AttemptTiming;
  persistedAt: string;
}
