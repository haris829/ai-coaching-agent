import type { ExplanationLevel } from './enums';

/**
 * INTERNAL LESSON CONTRACT.
 *
 * UC-04 business logic depends ONLY on these types - never on raw Courses Agent JSON.
 * The normalizer (src/core/lesson-normalizer.ts) is the single place that turns a
 * provider payload into a LessonContext.
 */

/** A concept taught by a lesson section. Only ever built from supplied lesson data. */
export interface LessonConcept {
  concept_id: string;
  name: string;
  /** One or two sentence definition taken from the lesson. */
  summary: string;
  /** The section this concept lives in. */
  section_id: string;
  /** Retrieval keywords supplied by the content source (optional). */
  keywords: string[];
  /** Worked examples supplied by the lesson. Empty when the lesson provides none. */
  examples: string[];
  /** Analogies supplied by the lesson. Empty when the lesson provides none. */
  analogies: string[];
  /** Concept ids in the same lesson this concept is explicitly contrasted with. */
  contrasts_with: string[];
}

/** A section of the linked lesson. */
export interface LessonSection {
  section_id: string;
  title: string;
  /** Prose body of the section, verbatim from the content source. */
  content: string;
  /** Ordered key points supplied by the section. Empty when none supplied. */
  key_points: string[];
  /** Concept ids taught in this section. */
  concept_ids: string[];
  order: number;
}

/** A pointer to another REAL lesson in the same course. Never synthesised. */
export interface RelatedLessonRef {
  lesson_id: string;
  title: string;
  /** Why the content source links them (e.g. "prerequisite"). Free text from the source. */
  relationship: string;
  /** Retrieval keywords for the related lesson, if supplied. */
  keywords: string[];
}

/** The normalized lesson the coaching service reasons over. */
export interface LessonContext {
  course_id: string;
  course_name: string | null;
  lesson_id: string;
  lesson_title: string;
  /** Full lesson body. UC-04 avoids reproducing this wholesale in answers. */
  lesson_content: string;
  sections: LessonSection[];
  concepts: LessonConcept[];
  related_lessons: RelatedLessonRef[];
  /** Provenance marker so downstream code can tell where the content came from. */
  source: {
    provider: string;
    /** Content revision/etag if the provider supplies one. */
    revision: string | null;
    normalized_at: string;
  };
}

/** Learner-side context. Every field is optional - UC-04 must work without it. */
export interface LearnerContext {
  user_id: string;
  explanation_level: ExplanationLevel | null;
  /** Free-form preferences the context service may supply. Not required by UC-04. */
  preferred_language: string | null;
  available: boolean;
}

/**
 * The authoritative server-side binding for a coaching session.
 * Course/lesson identity comes from HERE, never from the client request body.
 */
export interface SessionBinding {
  session_id: string;
  user_id: string;
  course_id: string;
  /** Null for a session that is not linked to a specific lesson. */
  lesson_id: string | null;
}
