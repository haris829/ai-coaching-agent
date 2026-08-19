/**
 * API contract types for both capabilities.
 *
 * These mirror the backend's response shapes — the pydantic models under
 * `backend/app/modules/question_bank/schemas/` and the serialisers under
 * `backend/app/modules/quiz_configuration/api/serializers.py`. They are hand-maintained rather
 * than generated so the frontend has no build-time dependency on the Python project; the
 * backend's OpenAPI document at `/api/openapi.json` is the source of truth if they disagree.
 */

export const QUESTION_TYPES = [
  'SINGLE_CHOICE',
  'TRUE_FALSE',
  'MULTI_SELECT',
  'SCENARIO',
  'DRAG_TO_ORDER',
] as const;
export type QuestionType = (typeof QUESTION_TYPES)[number];

export const QUESTION_STATUSES = ['DRAFT', 'ACTIVE', 'RETIRED'] as const;
export type QuestionStatus = (typeof QUESTION_STATUSES)[number];

export const SCORING_STRATEGIES = [
  'ALL_OR_NOTHING',
  'PARTIAL_CREDIT',
  'PARTIAL_CREDIT_WITH_PENALTY',
] as const;
export type ScoringStrategy = (typeof SCORING_STRATEGIES)[number];

export const DIFFICULTIES = ['EASY', 'MEDIUM', 'HARD'] as const;
export type Difficulty = (typeof DIFFICULTIES)[number];

/** Which scoring strategies the backend accepts per type — mirrors the domain enums. */
export const ALLOWED_SCORING_STRATEGIES: Record<QuestionType, readonly ScoringStrategy[]> = {
  SINGLE_CHOICE: ['ALL_OR_NOTHING'],
  TRUE_FALSE: ['ALL_OR_NOTHING'],
  SCENARIO: ['ALL_OR_NOTHING'],
  MULTI_SELECT: ['ALL_OR_NOTHING', 'PARTIAL_CREDIT', 'PARTIAL_CREDIT_WITH_PENALTY'],
  DRAG_TO_ORDER: ['ALL_OR_NOTHING', 'PARTIAL_CREDIT'],
};

export const QUESTION_TYPE_LABELS: Record<QuestionType, string> = {
  SINGLE_CHOICE: 'Single choice',
  TRUE_FALSE: 'True / False',
  MULTI_SELECT: 'Multi-select',
  SCENARIO: 'Scenario',
  DRAG_TO_ORDER: 'Drag-to-order',
};

export const SCORING_STRATEGY_LABELS: Record<ScoringStrategy, string> = {
  ALL_OR_NOTHING: 'All or nothing',
  PARTIAL_CREDIT: 'Partial credit',
  PARTIAL_CREDIT_WITH_PENALTY: 'Partial credit, with penalty',
};

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export interface FieldIssue {
  field: string;
  code: string;
  message: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: FieldIssue[] };
}

// ---------------------------------------------------------------------------
// Topics
// ---------------------------------------------------------------------------

export interface TopicRef {
  id: string;
  slug: string;
  name: string;
}

export interface Topic extends TopicRef {
  description: string | null;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
  questionCount: number | null;
}

// ---------------------------------------------------------------------------
// Questions
// ---------------------------------------------------------------------------

export interface QuestionOption {
  id: string;
  label: string;
  text: string;
  /** Default presentation order — not the answer. */
  position: number;
  isCorrect: boolean;
  isPrimary: boolean;
  /** Correct answer order (DRAG_TO_ORDER only). */
  correctPosition: number | null;
  feedback: string | null;
}

export interface Scoring {
  points: number;
  scoringStrategy: ScoringStrategy;
  penaltyPerIncorrect: number;
}

export interface UsageSummary {
  total: number;
  completed: number;
  inProgress: number;
  hasHistory: boolean;
  canHardDelete: boolean;
}

export interface Question {
  id: string;
  reference: string;
  seq: number;
  externalRef: string | null;
  type: QuestionType;
  status: QuestionStatus;
  questionText: string;
  scenarioText: string | null;
  explanation: string | null;
  difficulty: Difficulty | null;
  scoring: Scoring;
  version: number;
  contentHash: string;
  options: QuestionOption[];
  topics: TopicRef[];
  correctLabels: string[];
  correctOrder: string[];
  primaryLabel: string | null;
  retiredAt: string | null;
  retiredReason: string | null;
  retiredBy: string | null;
  createdAt: string;
  updatedAt: string;
  createdBy: string | null;
  updatedBy: string | null;
  importId: string | null;
  importRowNumber: number | null;
  isDeliverable: boolean;
  usage: UsageSummary | null;
}

export interface QuestionListItem {
  id: string;
  reference: string;
  type: QuestionType;
  status: QuestionStatus;
  questionText: string;
  topics: TopicRef[];
  points: number;
  scoringStrategy: ScoringStrategy;
  difficulty: Difficulty | null;
  version: number;
  optionCount: number;
  usageCount: number;
  isDeliverable: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface PageMeta {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  hasNext: boolean;
  hasPrevious: boolean;
}

export interface Paged<T> {
  items: T[];
  meta: PageMeta;
}

export interface QuestionSnapshot {
  id: string;
  questionId: string;
  version: number;
  reference: string;
  type: QuestionType;
  status: string;
  questionText: string;
  scenarioText: string | null;
  explanation: string | null;
  points: number;
  scoringStrategy: string;
  penaltyPerIncorrect: number;
  contentHash: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Request payloads
// ---------------------------------------------------------------------------

export interface OptionPayload {
  label: string;
  text: string;
  position?: number;
  isCorrect?: boolean;
  isPrimary?: boolean;
  correctPosition?: number | null;
  feedback?: string | null;
}

export interface QuestionPayload {
  type: QuestionType;
  questionText: string;
  scenarioText?: string | null;
  explanation?: string | null;
  difficulty?: string | null;
  status?: string | null;
  externalRef?: string | null;
  options: OptionPayload[];
  topics: string[];
  scoring: {
    points: number;
    scoringStrategy: ScoringStrategy;
    penaltyPerIncorrect?: number;
  };
}

// ---------------------------------------------------------------------------
// CSV import
// ---------------------------------------------------------------------------

export interface ImportRowError {
  rowNumber: number;
  field: string | null;
  code: string;
  message: string;
}

export interface ImportedRow {
  rowNumber: number;
  questionId: string;
  reference: string;
  questionText: string;
}

export interface RejectedRow {
  rowNumber: number;
  errors: ImportRowError[];
  rawRow: Record<string, string> | null;
}

export interface ImportResult {
  id: string;
  filename: string;
  status: 'PROCESSING' | 'COMPLETED' | 'FAILED';
  totalRows: number;
  importedRows: number;
  rejectedRows: number;
  errorMessage: string | null;
  startedAt: string;
  completedAt: string | null;
  imported: ImportedRow[];
  rejected: RejectedRow[];
}

export interface ImportListItem {
  id: string;
  filename: string;
  status: 'PROCESSING' | 'COMPLETED' | 'FAILED';
  totalRows: number;
  importedRows: number;
  rejectedRows: number;
  errorMessage: string | null;
  startedAt: string;
  completedAt: string | null;
}

export interface TemplateGuideField {
  column: string;
  required: string;
  description: string;
}

export interface TemplateGuide {
  headers: string[];
  requiredHeaders: string[];
  listDelimiter: string;
  optionSyntax: string;
  maxBytes: number;
  maxRows: number;
  fields: TemplateGuideField[];
  templateUrl: string;
}

// ---------------------------------------------------------------------------
// Delivery + historical reporting
// ---------------------------------------------------------------------------

export interface Usage {
  id: string;
  attemptRef: string;
  learnerRef: string | null;
  questionId: string;
  questionReference: string;
  snapshotId: string;
  snapshotVersion: number;
  attemptStatus: 'IN_PROGRESS' | 'COMPLETED' | 'ABANDONED';
  learnerResponse: Record<string, unknown> | null;
  presentationOrder: string[] | null;
  isCorrect: boolean | null;
  awardedPoints: number | null;
  maxPoints: number | null;
  deliveredAt: string;
  respondedAt: string | null;
  completedAt: string | null;
}

export interface AttemptReportItem {
  questionId: string;
  questionReference: string;
  snapshotVersion: number;
  currentQuestionStatus: QuestionStatus;
  type: QuestionType;
  questionText: string;
  scenarioText: string | null;
  explanation: string | null;
  options: Array<{
    label: string;
    text: string;
    position: number;
    isCorrect: boolean;
    correctPosition: number | null;
  }>;
  correctLabels: string[];
  correctOrder: string[];
  topics: string[];
  learnerResponse: { selectedLabels?: string[]; orderedLabels?: string[] } | null;
  presentationOrder: string[] | null;
  isCorrect: boolean | null;
  awardedPoints: number | null;
  maxPoints: number | null;
  deliveredAt: string;
  completedAt: string | null;
}

export interface AttemptReport {
  attemptRef: string;
  learnerRef: string | null;
  attemptStatus: string;
  questionCount: number;
  totalAwardedPoints: number;
  totalMaxPoints: number;
  items: AttemptReportItem[];
}


// ---------------------------------------------------------------------------
// UC-01 — Quiz Configuration & Rules
// ---------------------------------------------------------------------------

export const DELIVERY_MODES = ['practice', 'assessment', 'exam'] as const;
export type DeliveryMode = (typeof DELIVERY_MODES)[number];

export interface QuizSummary {
  id: number;
  courseId: number;
  courseTitle: string;
  slug: string;
  title: string;
}

/** One selected question type. `quota` is null when questions are drawn freely across types. */
export interface QuestionTypeSelection {
  type: QuestionType;
  quota: number | null;
}

/** A topic scope entry, frozen onto a configuration version at save time. */
export interface TopicScope {
  id: string;
  slug: string;
  name: string;
}

/** An immutable configuration version. Never edited — a change creates the next one. */
export interface ConfigurationVersion {
  id: number;
  quizId: number;
  versionNumber: number;
  questionCount: number;
  timeLimitMinutes: number | null;
  passMark: number;
  randomiseQuestions: boolean;
  maxAttempts: number;
  deliveryMode: DeliveryMode;
  questionTypes: QuestionTypeSelection[];
  topics: TopicScope[];
  isActive: boolean;
  settingsFingerprint: string;
  createdByUserId: number | null;
  createdBy: string | null;
  createdAt: string;
  attemptCount: number;
}

export interface CapacityEntry {
  type: QuestionType;
  requested: number | null;
  available: number;
  shortfall: number;
}

/** Can the active question bank satisfy this configuration right now? */
export interface CapacityReport {
  satisfiable: boolean;
  requestedTotal: number;
  availableTotal: number;
  totalShortfall: number;
  breakdown: CapacityEntry[];
  messages: string[];
}

export interface QuizConfigurationResponse {
  quiz: QuizSummary;
  configuration: ConfigurationVersion | null;
  capacity: CapacityReport | null;
}

export interface SaveConfigurationResponse {
  configuration: ConfigurationVersion;
  capacity: CapacityReport;
  created: boolean;
  unchanged?: boolean;
}

export interface VersionHistory {
  quiz: QuizSummary;
  versions: ConfigurationVersion[];
}

/** What the admin form submits. Mirrors the backend validator's expected payload. */
export interface QuizConfigurationInput {
  questionCount: number | string;
  timeLimitMinutes: number | string | null;
  passMark: number | string;
  maxAttempts: number | string;
  deliveryMode: DeliveryMode | '';
  randomiseQuestions: boolean;
  questionTypes: QuestionTypeSelection[];
  topicIds?: string[];
}

export interface QuestionBankAvailability {
  quiz: QuizSummary;
  topicIds: string[];
  availableByType: Record<QuestionType, number>;
}

export interface ConfigurationMeta {
  questionTypes: { value: QuestionType; label: string }[];
  deliveryModes: { value: DeliveryMode; label: string }[];
  limits: Record<string, { min: number; max: number }>;
  maxConfigurationTopics: number;
  deliverableQuestionStatuses: QuestionStatus[];
}

export type AttemptStatus = 'in_progress' | 'submitted' | 'abandoned';

export interface Attempt {
  id: number;
  quizId: number;
  userId: number;
  configurationVersionId: number;
  configurationVersionNumber: number;
  attemptNumber: number;
  status: AttemptStatus;
  usageRef: string;
  startedAt: string;
  expiresAt: string | null;
  submittedAt: string | null;
  scorePercent: number | null;
  passed: boolean | null;
}

/** The rules a learner is shown, or that an attempt runs under. Always from ONE version. */
export interface RulesSummary {
  configurationVersionId: number;
  configurationVersionNumber: number;
  questionCount: number;
  timeLimitMinutes: number | null;
  passMark: number;
  randomiseQuestions: boolean;
  deliveryMode: DeliveryMode;
  maxAttempts: number;
  questionTypes: QuestionTypeSelection[];
  topics: TopicScope[];
}

export type BlockedReason =
  | 'attempt_in_progress'
  | 'attempt_limit_reached'
  | 'question_bank_insufficient';

export interface QuizRules extends RulesSummary {
  quiz: QuizSummary;
  attemptsUsed: number;
  remainingAttempts: number;
  canStart: boolean;
  blockedReason: BlockedReason | null;
  attemptInProgress: Attempt | null;
}

/** A question as the learner sees it — read from the pinned snapshot, answer key removed. */
export interface DeliveredQuestion {
  position: number;
  questionId: string;
  reference: string;
  type: QuestionType;
  questionText: string;
  scenarioText: string | null;
  snapshotVersion: number;
  options: { label: string; text: string; position: number }[];
}

export interface AttemptDetail {
  attempt: Attempt;
  quiz: QuizSummary;
  /** Always the LOCKED version's rules, never the quiz's current active configuration. */
  rules: RulesSummary;
  questions: DeliveredQuestion[];
}
