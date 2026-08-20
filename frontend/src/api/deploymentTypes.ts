/**
 * Response shapes for UC-08 (retakes), UC-09 (formal assessment) and UC-10 (analytics).
 *
 * Kept separate from `types.ts` for one reason worth stating: these three capabilities serialise in
 * **snake_case**, while UC-01…UC-07 serialise in camelCase. That is not an inconsistency to be
 * tidied away here — the field names are the API's, and a client that renamed them would be
 * inventing a second vocabulary for the same data, so the next person reading a network trace
 * beside this code would have to translate. The seam is the file boundary, and it is documented.
 *
 * Only the fields this test UI actually renders are declared. Everything the API returns is still
 * present at runtime; a partial interface is a statement about what the demo shows, not a claim
 * about what the backend sends.
 */

// ---------------------------------------------------------------------------
// UC-08 — Retake Management
// ---------------------------------------------------------------------------

/** How many attempts a learner has, has used, and has been granted. */
export interface RetakeAllowance {
  maximum_attempts: number | null;
  attempts_used: number;
  granted_attempts: number;
  total_entitlement: number | null;
  available_attempts: number | null;
  has_available_attempts: boolean;
  unlimited: boolean;
  relies_on_grant: boolean;
}

export interface RetakeBlocker {
  code: string;
  message: string;
}

/**
 * `state` is the whole answer, and the three values are deliberately distinct:
 * `ELIGIBLE` (configured attempts remain), `ADDITIONAL_ATTEMPT_AVAILABLE` (attempts remain *only*
 * because an administrator granted one) and `EXHAUSTED`. A UI that collapsed the first two could
 * not tell a learner why they still have an attempt.
 */
export interface RetakeEligibility {
  learner_id: string;
  quiz_id: string;
  state: 'ELIGIBLE' | 'ADDITIONAL_ATTEMPT_AVAILABLE' | 'EXHAUSTED' | string;
  can_retake: boolean;
  allowance: RetakeAllowance;
  blockers: RetakeBlocker[];
  next_attempt_number: number | null;
  configuration_version_number: number | null;
  configuration_version_source: string | null;
  /** Administrator-contact wording, present only when the allowance is spent. */
  guidance: string | null;
}

/** What the retake was told to avoid, and what the bank could actually support. */
export interface RetakeQuestionPlan {
  required_count: number;
  eligible_pool_size: number;
  unused_pool_size: number;
  reuse_expected: boolean;
  reuse_reason: string | null;
  expected_fresh_questions: number;
  feasible: boolean;
}

export interface RetakeCreated {
  retake_id: string;
  attempt: { attempt_id: string; attempt_number: number };
  question_plan: RetakeQuestionPlan | null;
  replayed?: boolean;
}

/**
 * One attempt as history shows it.
 *
 * The `*_available` flags matter: history is assembled read-only from six capabilities, and a fact
 * an upstream module has not produced is reported as unavailable rather than filled in. A UI that
 * rendered `percentage` without checking `score_available` would print "0%" for an attempt that has
 * not been scored yet.
 */
export interface AttemptHistoryEntry {
  attempt_id: string;
  attempt_number: number;
  status: string;
  configuration_version_number: number | null;
  started_at: string | null;
  submitted_at: string | null;
  total_questions: number | null;
  score_available: boolean;
  total_marks: number | null;
  maximum_marks: number | null;
  percentage: number | null;
  pass_fail_available: boolean;
  pass_fail_status: string | null;
  pass_mark_percentage: number | null;
  feedback_available: boolean;
  coaching_available: boolean;
  is_retake: boolean;
  retake_of_attempt_id: string | null;
}

export interface AttemptHistory {
  learner_id: string;
  quiz_id: string;
  attempt_count: number;
  entries: AttemptHistoryEntry[];
}

export interface AdditionalAttemptGrant {
  grant_id: string;
  learner_id: string;
  quiz_id: string;
  additional_attempts: number;
  reason: string;
  granted_by: string;
  granted_at: string;
  revoked_at?: string | null;
}

// ---------------------------------------------------------------------------
// UC-09 — Formal Assessment Mode
// ---------------------------------------------------------------------------

export interface FormalCondition {
  code: string;
  title: string;
  detail?: string;
}

export interface FormalConditions {
  quiz_id: string;
  is_formal_assessment: boolean;
  conditions_version: string;
  conditions: FormalCondition[];
}

export interface FormalAcknowledgement {
  formal_attempt_id: string;
  acknowledged_at: string;
  conditions_version: string;
}

export interface FormalIdentityCheck {
  identity_check: { confirmed: boolean; reason?: string | null };
}

/**
 * The session token is handed out **once**, at start. It is the device's proof, and every
 * subsequent write carries it in `X-Formal-Session`: the bearer token says who the learner is, the
 * session token says which device is entitled to write for them. Losing it means the assessment
 * cannot be continued from this device, which is the rule, not a bug.
 */
export interface FormalStarted {
  formal_attempt_id: string;
  attempt_id: string;
  session: { session_id: string; session_token: string; registered_at: string };
  replayed?: boolean;
}

export interface FormalAttemptState {
  formal_attempt_id: string;
  attempt_id: string | null;
  state: string;
  submitted: boolean;
  auto_submitted: boolean;
}

export interface FormalReview {
  review_id: string;
  formal_attempt_id: string;
  learner_id: string;
  quiz_id: string;
  attempt_id: string | null;
  state: string;
  percentage: number | null;
  submitted_at: string | null;
  auto_submitted: boolean;
  anomaly_count: number;
  assigned_to: string | null;
  decision: string | null;
  created_at: string;
}

export interface PendingReviews {
  reviews: FormalReview[];
  total_pending: number;
}

// ---------------------------------------------------------------------------
// UC-10 — Analytics & Reporting
// ---------------------------------------------------------------------------

/**
 * `data_state` is the field that keeps this dashboard honest.
 *
 * `NO_ATTEMPTS` with `average_score: null` is a different statement from `OK` with
 * `average_score: 0`, and conflating them would tell an administrator that every learner failed
 * when in fact nobody has sat the quiz. Every rate here is nullable for exactly that reason.
 */
export interface OverallAnalytics {
  scope: string;
  course_id: string | null;
  data_state: 'OK' | 'NO_ATTEMPTS' | string;
  attempt_volume: number;
  completed_attempts: number;
  scored_attempts: number;
  graded_attempts: number;
  passed_attempts: number;
  unique_learners: number;
  average_score: number | null;
  pass_rate: number | null;
  completion_rate: number | null;
  calculated_at: string;
}

export interface QuestionAnalytics {
  question_id: string;
  question_type: string;
  /** The label a human reads — "Single choice", not "SINGLE_CHOICE". */
  question_type_label: string;
  attempt_count: number;
  answered_count: number;
  unanswered_count: number;
  graded_count: number;
  correct_count: number;
  incorrect_count: number;
  accuracy_percentage: number | null;
  wrong_answer_rate: number | null;
  most_frequent_wrong_answer: string | null;
  average_time_seconds: number | null;
  data_state: string;
  is_flagged: boolean;
  meets_flag_criteria: boolean;
  flag_threshold: number | null;
}

export interface QuestionAnalyticsPage {
  items: QuestionAnalytics[];
  data_state: string;
  calculated_at: string;
}

export interface FlaggedQuestions {
  items: QuestionAnalytics[];
  total: number;
  threshold_used: number;
  min_responses_required: number;
  includes_unpersisted_candidates: boolean;
  calculated_at: string;
}

export interface AnalyticsFilters {
  course_id?: string;
  cohort_id?: string;
  assessment_type?: 'STANDARD_QUIZ' | 'FORMAL_ASSESSMENT';
  from_date?: string;
  to_date?: string;
}

export type ReviewActionType = 'NO_CHANGE' | 'QUESTION_UPDATED' | 'QUESTION_RETIRED';
