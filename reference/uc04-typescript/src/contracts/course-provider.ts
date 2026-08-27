/**
 * PORT: Course catalogue.
 * TODAY  -> MockCourseProvider
 * LATER  -> CompanyCoursesAdapter (Courses Agent / course service)
 */

/** Minimal course descriptor UC-04 needs. Provider-shaped JSON is normalized by the adapter. */
export interface CourseSummary {
  course_id: string;
  course_name: string;
  /** Lessons that exist in this course. Used to validate related-lesson references. */
  lesson_ids: string[];
}

export interface CourseProvider {
  /** Resolve a course. Throw ProviderError('NOT_FOUND') when the course does not exist. */
  getCourse(courseId: string): Promise<CourseSummary>;
}
