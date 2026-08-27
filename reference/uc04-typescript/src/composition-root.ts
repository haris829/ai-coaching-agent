import {
  InMemoryActivityRepository,
  InMemoryExplanationHistoryStore,
  InMemoryFalsePositiveLog,
} from './adapters/memory/in-memory-repositories';
import {
  MockContextProvider,
  MockCourseProvider,
  MockEnrollmentProvider,
  MockLessonContentProvider,
} from './adapters/mock/mock-providers';
import { SequentialIdGenerator, SystemClock } from './contracts/clock';
import type { Clock, IdGenerator } from './contracts/clock';
import type { ContextProvider } from './contracts/context-provider';
import type { CourseProvider } from './contracts/course-provider';
import type { EnrollmentProvider } from './contracts/enrollment-provider';
import type { ExplanationEngine } from './contracts/explanation-engine';
import type { LessonContentProvider } from './contracts/lesson-content-provider';
import type { QuizIntentClassifier } from './contracts/quiz-intent-classifier';
import type { SectionRetriever } from './contracts/section-retriever';
import type { ActivityRepository } from './contracts/activity-repository';
import type { ExplanationHistoryStore } from './contracts/explanation-history-store';
import type { FalsePositiveLog } from './contracts/false-positive-log';
import { CourseCoachingService } from './core/course-coaching-service';
import { TemplateExplanationEngine } from './core/explanation/template-explanation-engine';
import { HeuristicQuizIntentClassifier } from './core/quiz/heuristic-quiz-intent-classifier';
import { KeywordSectionRetriever } from './core/retrieval/keyword-section-retriever';

/**
 * THE INTEGRATION SEAM.
 *
 * This is the only file a company integration engineer needs to edit to swap the mocks for
 * real services. Every field below is a port; UC-04 core depends on the port, never on the
 * implementation. See INTEGRATION.md.
 */
export interface UC04Overrides {
  courseProvider?: CourseProvider;
  enrollmentProvider?: EnrollmentProvider;
  lessonContentProvider?: LessonContentProvider;
  contextProvider?: ContextProvider;
  retriever?: SectionRetriever;
  explanationEngine?: ExplanationEngine;
  quizClassifier?: QuizIntentClassifier;
  activityRepository?: ActivityRepository;
  explanationHistory?: ExplanationHistoryStore;
  falsePositiveLog?: FalsePositiveLog;
  clock?: Clock;
  ids?: IdGenerator;
}

export interface UC04Container {
  service: CourseCoachingService;
  courseProvider: CourseProvider;
  enrollmentProvider: EnrollmentProvider;
  lessonContentProvider: LessonContentProvider;
  contextProvider: ContextProvider;
  activityRepository: ActivityRepository;
  explanationHistory: ExplanationHistoryStore;
  falsePositiveLog: FalsePositiveLog;
}

export function buildUC04(overrides: UC04Overrides = {}): UC04Container {
  const courseProvider = overrides.courseProvider ?? new MockCourseProvider();
  const enrollmentProvider = overrides.enrollmentProvider ?? new MockEnrollmentProvider();
  const lessonContentProvider = overrides.lessonContentProvider ?? new MockLessonContentProvider();
  const contextProvider = overrides.contextProvider ?? new MockContextProvider();
  const activityRepository = overrides.activityRepository ?? new InMemoryActivityRepository();
  const explanationHistory = overrides.explanationHistory ?? new InMemoryExplanationHistoryStore();
  const falsePositiveLog = overrides.falsePositiveLog ?? new InMemoryFalsePositiveLog();

  const service = new CourseCoachingService({
    courseProvider,
    enrollmentProvider,
    lessonContentProvider,
    contextProvider,
    retriever: overrides.retriever ?? new KeywordSectionRetriever(),
    explanationEngine: overrides.explanationEngine ?? new TemplateExplanationEngine(),
    quizClassifier: overrides.quizClassifier ?? new HeuristicQuizIntentClassifier(),
    activityRepository,
    explanationHistory,
    falsePositiveLog,
    clock: overrides.clock ?? new SystemClock(),
    ids: overrides.ids ?? new SequentialIdGenerator(),
  });

  return {
    service,
    courseProvider,
    enrollmentProvider,
    lessonContentProvider,
    contextProvider,
    activityRepository,
    explanationHistory,
    falsePositiveLog,
  };
}
