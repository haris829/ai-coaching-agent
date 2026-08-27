import type { LessonContext, LessonConcept, LessonSection, RelatedLessonRef } from '../domain/lesson-context';

/**
 * PORT: Relevance detection (question -> lesson section/concept).
 * TODAY  -> KeywordSectionRetriever (deterministic lexical scoring, no vector DB)
 * LATER  -> embedding/vector retriever implementing the SAME interface
 *
 * Implementations MUST only return sections/concepts/related lessons that are present in
 * the supplied LessonContext. Inventing ids is a contract violation.
 */
/**
 * Strongest field of the section the question actually landed on:
 *   NAME    - a section title or concept name (the query is about this topic)
 *   KEYWORD - a retrieval keyword only (suggestive, but can be a lexical coincidence)
 *   BODY    - prose only (weakest; an everyday word reused by the section)
 *   NONE    - nothing matched
 */
export type MatchAnchor = 'NAME' | 'KEYWORD' | 'BODY' | 'NONE';

export interface LessonMatch {
  section: LessonSection;
  concept: LessonConcept | null;
  /** 0..1 confidence. */
  score: number;
  anchor: MatchAnchor;
  /** How many distinct query tokens the section contains. */
  matched_tokens: number;
}

export interface RelatedLessonMatch {
  related: RelatedLessonRef;
  score: number;
}

export interface RetrievalResult {
  /** Best in-lesson match, or null when nothing in the lesson is relevant enough. */
  bestMatch: LessonMatch | null;
  /** Ranked in-lesson matches (may be empty). */
  matches: LessonMatch[];
  /** Best related-lesson match from the SAME course, or null. */
  relatedMatch: RelatedLessonMatch | null;
}

export interface SectionRetriever {
  retrieve(question: string, lesson: LessonContext): RetrievalResult;
  /** Locate a concept by id when the caller already knows it (Explain Differently). */
  findConcept(conceptId: string, lesson: LessonContext): LessonMatch | null;
}
