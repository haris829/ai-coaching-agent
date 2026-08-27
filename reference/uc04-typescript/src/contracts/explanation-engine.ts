import type { FramingType, ExplanationLevel, SourceScope } from '../domain/enums';
import type { LessonContext, LessonConcept, LessonSection, RelatedLessonRef } from '../domain/lesson-context';

/**
 * PORT: Explanation generation.
 * TODAY  -> TemplateExplanationEngine (deterministic, lesson-grounded, testable)
 * LATER  -> LLM-backed engine implementing the SAME interface
 *
 * Contract rules an implementation MUST honour:
 *  - When sourceScope is LESSON/COURSE, the explanation is grounded in the supplied
 *    section/concept text and must not attribute invented facts to the lesson.
 *  - When sourceScope is GENERAL, the explanation must NOT claim to come from the lesson.
 *  - `framing` is chosen by UC-04, not by the engine. The engine must honour it.
 */
export interface ExplanationRequest {
  question: string;
  framing: FramingType;
  sourceScope: SourceScope;
  explanationLevel: ExplanationLevel | null;
  lesson: LessonContext | null;
  section: LessonSection | null;
  concept: LessonConcept | null;
  relatedLesson: RelatedLessonRef | null;
  /** Course topic hint used for GENERAL answers when the lesson is unavailable. */
  courseName: string | null;
  /**
   * Incremented by UC-04 when a produced explanation was rejected as a near-duplicate.
   * Implementations MUST vary their output when this changes.
   */
  variantSeed: number;
  /** True when UC-04 needs an answer that explains the tested concept without revealing answers. */
  quizSafeMode: boolean;
}

export interface GeneratedExplanation {
  text: string;
  framing: FramingType;
  /** Section ids actually used as grounding. Empty for GENERAL answers. */
  groundedSectionIds: string[];
}

export interface ExplanationEngine {
  explain(request: ExplanationRequest): Promise<GeneratedExplanation>;
}
