import type { CourseProvider, CourseSummary } from '../../contracts/course-provider';
import type { EnrollmentProvider, EnrollmentStatus } from '../../contracts/enrollment-provider';
import type { LessonContentProvider, RawLessonPayload } from '../../contracts/lesson-content-provider';
import type { ContextProvider } from '../../contracts/context-provider';
import { ProviderError } from '../../contracts/errors';
import type { LearnerContext, SessionBinding } from '../../domain/lesson-context';
import {
  LESSON_TIMEOUT,
  LESSON_UNAVAILABLE,
  MOCK_COURSES,
  MOCK_ENROLLMENTS,
  MOCK_LEARNER_LEVELS,
  MOCK_LESSON_PAYLOADS,
  MOCK_SESSIONS,
  USER_NO_CONTEXT,
} from './fixtures';

/**
 * Deterministic mock adapters.
 *
 * Every mock exposes a `failureMode` switch so tests can drive the resilience scenarios
 * (unavailable / timeout / forbidden) without touching UC-04 core. The real company adapters
 * implement the same interfaces and simply will not have these switches.
 */
export type FailureMode = 'NONE' | 'UNAVAILABLE' | 'TIMEOUT' | 'THROW_PLAIN';

function applyFailureMode(mode: FailureMode, provider: string): void {
  switch (mode) {
    case 'UNAVAILABLE':
      throw new ProviderError('UNAVAILABLE', provider, `${provider} is unavailable`);
    case 'TIMEOUT':
      throw new ProviderError('TIMEOUT', provider, `${provider} timed out`);
    case 'THROW_PLAIN':
      // A provider that blows up in an unexpected way - UC-04 must still not crash.
      throw new Error(`unexpected failure inside ${provider}`);
    default:
      return;
  }
}

export class MockCourseProvider implements CourseProvider {
  static readonly NAME = 'MockCourseProvider';
  failureMode: FailureMode = 'NONE';

  constructor(private readonly courses: readonly { course_id: string; course_name: string; lesson_ids: string[] }[] = MOCK_COURSES) {}

  async getCourse(courseId: string): Promise<CourseSummary> {
    applyFailureMode(this.failureMode, MockCourseProvider.NAME);
    const course = this.courses.find((c) => c.course_id === courseId);
    if (!course) {
      throw new ProviderError('NOT_FOUND', MockCourseProvider.NAME, `Course ${courseId} not found`);
    }
    return {
      course_id: course.course_id,
      course_name: course.course_name,
      lesson_ids: [...course.lesson_ids],
    };
  }
}

export class MockEnrollmentProvider implements EnrollmentProvider {
  static readonly NAME = 'MockEnrollmentProvider';
  failureMode: FailureMode = 'NONE';

  constructor(private readonly enrollments: readonly { user_id: string; course_ids: string[] }[] = MOCK_ENROLLMENTS) {}

  async isEnrolled(userId: string, courseId: string): Promise<EnrollmentStatus> {
    applyFailureMode(this.failureMode, MockEnrollmentProvider.NAME);
    const record = this.enrollments.find((e) => e.user_id === userId);
    const enrolled = Boolean(record?.course_ids.includes(courseId));
    return enrolled ? { enrolled: true } : { enrolled: false, reason: 'no_active_enrollment' };
  }
}

export class MockLessonContentProvider implements LessonContentProvider {
  static readonly NAME = 'MockLessonContentProvider';
  failureMode: FailureMode = 'NONE';
  /** Counts calls so tests can prove the lesson was never fetched before enrollment passed. */
  readonly calls: { courseId: string; lessonId: string }[] = [];

  constructor(private readonly payloads: Record<string, RawLessonPayload> = MOCK_LESSON_PAYLOADS) {}

  async getLesson(courseId: string, lessonId: string): Promise<RawLessonPayload> {
    this.calls.push({ courseId, lessonId });
    applyFailureMode(this.failureMode, MockLessonContentProvider.NAME);

    // Fixture lesson ids that model provider-side failures.
    if (lessonId === LESSON_UNAVAILABLE) {
      throw new ProviderError('UNAVAILABLE', MockLessonContentProvider.NAME, 'Content service unavailable');
    }
    if (lessonId === LESSON_TIMEOUT) {
      throw new ProviderError('TIMEOUT', MockLessonContentProvider.NAME, 'Content service timed out');
    }

    const payload = this.payloads[lessonId];
    if (!payload) {
      throw new ProviderError('NOT_FOUND', MockLessonContentProvider.NAME, `Lesson ${lessonId} not found`);
    }
    // Return a deep copy so callers cannot mutate the fixture between tests.
    return JSON.parse(JSON.stringify(payload)) as RawLessonPayload;
  }
}

export class MockContextProvider implements ContextProvider {
  static readonly NAME = 'MockContextProvider';
  failureMode: FailureMode = 'NONE';
  /** Independent switch: session binding can be healthy while learner context is down. */
  learnerFailureMode: FailureMode = 'NONE';

  constructor(
    private readonly sessions: readonly SessionBinding[] = MOCK_SESSIONS,
    private readonly levels: Record<string, string> = MOCK_LEARNER_LEVELS,
  ) {}

  async getSessionBinding(sessionId: string): Promise<SessionBinding> {
    applyFailureMode(this.failureMode, MockContextProvider.NAME);
    const session = this.sessions.find((s) => s.session_id === sessionId);
    if (!session) {
      throw new ProviderError('NOT_FOUND', MockContextProvider.NAME, `Session ${sessionId} not found`);
    }
    return { ...session };
  }

  async getLearnerContext(userId: string): Promise<LearnerContext> {
    applyFailureMode(this.learnerFailureMode, MockContextProvider.NAME);
    // Models a real service that simply has nothing on file for this user.
    if (userId === USER_NO_CONTEXT) {
      return { user_id: userId, explanation_level: null, preferred_language: null, available: false };
    }
    const level = this.levels[userId];
    return {
      user_id: userId,
      explanation_level: (level as LearnerContext['explanation_level']) ?? null,
      preferred_language: 'en-GB',
      available: true,
    };
  }
}
