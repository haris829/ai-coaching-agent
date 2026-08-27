/**
 * PORT: Lesson content.
 * TODAY  -> MockLessonContentProvider
 * LATER  -> Company lesson/content API
 *
 * The payload is deliberately loose: it is whatever the content source returns.
 * src/core/lesson-normalizer.ts is the ONLY component allowed to read this shape.
 */
export interface RawLessonPayload {
  [key: string]: unknown;
}

export interface LessonContentProvider {
  /** Fetch raw lesson content. Throw ProviderError for missing/unavailable/timeout. */
  getLesson(courseId: string, lessonId: string): Promise<RawLessonPayload>;
}
