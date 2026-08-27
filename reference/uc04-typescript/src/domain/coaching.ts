import type {
  CoachingAction,
  CoachingStatus,
  FramingType,
  QuizIntentLabel,
  SourceScope,
  TurnIntent,
} from './enums';

/**
 * UC-04 service input.
 *
 * SECURITY: the caller supplies only `principal_user_id` (from the authenticated request),
 * `session_id`, the question and an intent. Course/lesson identity is resolved server-side
 * from the session binding. `expected_course_id` / `expected_lesson_id` are OPTIONAL
 * assertions - if present they must MATCH the binding, they can never redirect it.
 */
export interface CoachingTurnRequest {
  principal_user_id: string;
  session_id: string;
  question: string;
  intent: TurnIntent;
  /** Concept the caller wants re-explained. Validated against the lesson; never trusted blindly. */
  concept_id?: string;
  expected_course_id?: string;
  expected_lesson_id?: string;
}

/** A related lesson surfaced to the caller. Always a real lesson from the course data. */
export interface RelatedLessonView {
  lesson_id: string;
  title: string;
  relationship: string;
}

/** UC-04 service output. Structured, never one giant text blob. */
export interface CoachingTurnResponse {
  status: CoachingStatus;
  session_id: string;
  course_id: string | null;
  lesson_id: string | null;
  source_scope: SourceScope;
  section_id: string | null;
  concept_id: string | null;
  /** Substantive answer. Null when blocked/unavailable/clarifying. */
  answer: string | null;
  /** Concept-level help returned instead of an answer when quiz protection fires. */
  concept_explanation: string | null;
  framing: FramingType | null;
  actions: CoachingAction[];
  quiz_protected: boolean;
  answer_revealed: boolean;
  free_form_available: boolean;
  /** User-facing notice, e.g. "not covered by the linked lesson". */
  notice: string | null;
  related_lesson_id: string | null;
  related_lessons: RelatedLessonView[];
  /** Non-sensitive diagnostics for the orchestration layer. */
  diagnostics: CoachingDiagnostics;
}

export interface CoachingDiagnostics {
  lesson_loaded: boolean;
  enrollment_verified: boolean;
  retrieval_score: number | null;
  quiz_label: QuizIntentLabel | null;
  quiz_confidence: number | null;
  explanation_attempt_index: number | null;
  framings_used: FramingType[];
  degraded: string[];
}
