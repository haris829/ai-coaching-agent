/**
 * UC-04 Course Content Coaching - shared vocabulary.
 *
 * These enums are part of the STABLE INTERNAL CONTRACT. Company adapters map their
 * own vocabulary onto these values; UC-04 core logic never sees vendor-specific strings.
 */

/** Where the substance of an answer came from. Never inferred from the LLM - always decided by UC-04. */
export const SourceScope = {
  /** Answered from the linked lesson's own content. */
  LESSON: 'LESSON',
  /** Answered using another lesson in the same course (a real, supplied related lesson). */
  COURSE: 'COURSE',
  /** Not covered by the linked lesson/course; answered from general knowledge. */
  GENERAL: 'GENERAL',
  /** No substantive answer produced (blocked, unavailable, or clarification required). */
  NONE: 'NONE',
} as const;
export type SourceScope = (typeof SourceScope)[keyof typeof SourceScope];

/** Terminal status of a coaching turn. */
export const CoachingStatus = {
  ANSWERED: 'ANSWERED',
  QUIZ_PROTECTED: 'QUIZ_PROTECTED',
  NEEDS_CLARIFICATION: 'NEEDS_CLARIFICATION',
  LESSON_UNAVAILABLE: 'LESSON_UNAVAILABLE',
  ENROLLMENT_REQUIRED: 'ENROLLMENT_REQUIRED',
  ENROLLMENT_UNVERIFIED: 'ENROLLMENT_UNVERIFIED',
  COURSE_NOT_FOUND: 'COURSE_NOT_FOUND',
  SESSION_NOT_FOUND: 'SESSION_NOT_FOUND',
  SESSION_FORBIDDEN: 'SESSION_FORBIDDEN',
  CONTEXT_UNAVAILABLE: 'CONTEXT_UNAVAILABLE',
} as const;
export type CoachingStatus = (typeof CoachingStatus)[keyof typeof CoachingStatus];

/** Explanation framings tracked per (session, concept). Order here is the default preference order. */
export const FramingType = {
  DIRECT: 'DIRECT',
  ANALOGY: 'ANALOGY',
  PRACTICAL_EXAMPLE: 'PRACTICAL_EXAMPLE',
  STEP_BY_STEP: 'STEP_BY_STEP',
  CONTRAST: 'CONTRAST',
  SCENARIO: 'SCENARIO',
} as const;
export type FramingType = (typeof FramingType)[keyof typeof FramingType];

export const DEFAULT_FRAMING_ORDER: readonly FramingType[] = [
  FramingType.DIRECT,
  FramingType.ANALOGY,
  FramingType.PRACTICAL_EXAMPLE,
  FramingType.STEP_BY_STEP,
  FramingType.CONTRAST,
  FramingType.SCENARIO,
];

/** Structured actions handed to the orchestration/frontend layer. UC-04 never executes them. */
export const CoachingAction = {
  EXPLAIN_DIFFERENTLY: 'EXPLAIN_DIFFERENTLY',
  START_FREE_FORM_SESSION: 'START_FREE_FORM_SESSION',
} as const;
export type CoachingAction = (typeof CoachingAction)[keyof typeof CoachingAction];

/** Turn intent supplied by the caller (or inferred from the text). */
export const TurnIntent = {
  ASK: 'ASK',
  EXPLAIN_DIFFERENTLY: 'EXPLAIN_DIFFERENTLY',
} as const;
export type TurnIntent = (typeof TurnIntent)[keyof typeof TurnIntent];

/** Quiz intent classification labels. */
export const QuizIntentLabel = {
  QUIZ_ANSWER_REQUEST: 'QUIZ_ANSWER_REQUEST',
  CONCEPT_LEARNING_REQUEST: 'CONCEPT_LEARNING_REQUEST',
  UNCERTAIN: 'UNCERTAIN',
} as const;
export type QuizIntentLabel = (typeof QuizIntentLabel)[keyof typeof QuizIntentLabel];

/** What UC-04 actually did after considering the classifier output. */
export const ProtectionDecision = {
  BLOCKED: 'BLOCKED',
  CLARIFY: 'CLARIFY',
  ANSWERED: 'ANSWERED',
} as const;
export type ProtectionDecision = (typeof ProtectionDecision)[keyof typeof ProtectionDecision];

/** Activity / progress event types written to the ActivityRepository. */
export const ActivityType = {
  LESSON_LOADED: 'LESSON_LOADED',
  CONCEPT_EXPLAINED: 'CONCEPT_EXPLAINED',
  EXPLAIN_DIFFERENTLY: 'EXPLAIN_DIFFERENTLY',
  QUIZ_PROTECTED: 'QUIZ_PROTECTED',
  OFF_LESSON_QUESTION: 'OFF_LESSON_QUESTION',
  LESSON_UNAVAILABLE: 'LESSON_UNAVAILABLE',
  ENROLLMENT_DENIED: 'ENROLLMENT_DENIED',
  CLARIFICATION_REQUESTED: 'CLARIFICATION_REQUESTED',
} as const;
export type ActivityType = (typeof ActivityType)[keyof typeof ActivityType];

/** Difficulty signal kinds. A signal is an observation, never a diagnosis. */
export const DifficultySignalType = {
  EXPLAIN_DIFFERENTLY: 'EXPLAIN_DIFFERENTLY',
} as const;
export type DifficultySignalType = (typeof DifficultySignalType)[keyof typeof DifficultySignalType];

/** Learner explanation level, used only when the context provider supplies it. */
export const ExplanationLevel = {
  BEGINNER: 'BEGINNER',
  INTERMEDIATE: 'INTERMEDIATE',
  ADVANCED: 'ADVANCED',
} as const;
export type ExplanationLevel = (typeof ExplanationLevel)[keyof typeof ExplanationLevel];
