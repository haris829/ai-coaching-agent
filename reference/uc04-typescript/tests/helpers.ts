import {
  InMemoryActivityRepository,
  InMemoryExplanationHistoryStore,
  InMemoryFalsePositiveLog,
} from '../src/adapters/memory/in-memory-repositories';
import {
  MockContextProvider,
  MockCourseProvider,
  MockEnrollmentProvider,
  MockLessonContentProvider,
} from '../src/adapters/mock/mock-providers';
import { FixedClock, SequentialIdGenerator } from '../src/contracts/clock';
import { CourseCoachingService } from '../src/core/course-coaching-service';
import { TemplateExplanationEngine } from '../src/core/explanation/template-explanation-engine';
import { HeuristicQuizIntentClassifier } from '../src/core/quiz/heuristic-quiz-intent-classifier';
import { KeywordSectionRetriever } from '../src/core/retrieval/keyword-section-retriever';
import type { CoachingTurnRequest, CoachingTurnResponse } from '../src/domain/coaching';
import { TurnIntent } from '../src/domain/enums';
import { SESSION_MAIN, USER_ENROLLED } from '../src/adapters/mock/fixtures';

/**
 * Test harness: the same wiring as the composition root, but with the concrete mock classes
 * exposed so a test can drive failure modes and inspect what was recorded.
 */
export interface Harness {
  service: CourseCoachingService;
  courseProvider: MockCourseProvider;
  enrollmentProvider: MockEnrollmentProvider;
  lessonProvider: MockLessonContentProvider;
  contextProvider: MockContextProvider;
  activity: InMemoryActivityRepository;
  history: InMemoryExplanationHistoryStore;
  falsePositives: InMemoryFalsePositiveLog;
  ask(question: string, overrides?: Partial<CoachingTurnRequest>): Promise<CoachingTurnResponse>;
  explainDifferently(overrides?: Partial<CoachingTurnRequest>): Promise<CoachingTurnResponse>;
}

export function createHarness(): Harness {
  const courseProvider = new MockCourseProvider();
  const enrollmentProvider = new MockEnrollmentProvider();
  const lessonProvider = new MockLessonContentProvider();
  const contextProvider = new MockContextProvider();
  const activity = new InMemoryActivityRepository();
  const history = new InMemoryExplanationHistoryStore();
  const falsePositives = new InMemoryFalsePositiveLog();

  const service = new CourseCoachingService({
    courseProvider,
    enrollmentProvider,
    lessonContentProvider: lessonProvider,
    contextProvider,
    retriever: new KeywordSectionRetriever(),
    explanationEngine: new TemplateExplanationEngine(),
    quizClassifier: new HeuristicQuizIntentClassifier(),
    activityRepository: activity,
    explanationHistory: history,
    falsePositiveLog: falsePositives,
    clock: new FixedClock(),
    ids: new SequentialIdGenerator(),
  });

  const ask = (question: string, overrides: Partial<CoachingTurnRequest> = {}) =>
    service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_MAIN,
      question,
      intent: TurnIntent.ASK,
      ...overrides,
    });

  const explainDifferently = (overrides: Partial<CoachingTurnRequest> = {}) =>
    service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_MAIN,
      question: '',
      intent: TurnIntent.EXPLAIN_DIFFERENTLY,
      ...overrides,
    });

  return {
    service,
    courseProvider,
    enrollmentProvider,
    lessonProvider,
    contextProvider,
    activity,
    history,
    falsePositives,
    ask,
    explainDifferently,
  };
}

/** Questions used across suites, kept in one place so intent is obvious. */
export const QUESTIONS = {
  lessonConcept: 'What does consent actually mean in this lesson?',
  lessonConceptOther: 'How does the balancing test work for legitimate interests?',
  crossLesson: 'What is a subject access request?',
  offLesson: 'How do I bake sourdough bread at home?',
  directQuiz: [
    'What is the answer to question 4?',
    'Which option is correct?',
    'Tell me the answer.',
    'What should I put for question 7?',
  ],
  indirectQuiz: [
    'Just confirm whether B is correct.',
    "Don't explain it, just tell me if my answer is right.",
    'Give me a hint that tells me the correct option.',
    'Which ones can I rule out?',
  ],
  genuineLearning: [
    'Can you explain the principle tested by this question?',
    'Why does consent have to be freely given?',
    'Help me understand the difference between consent and legitimate interests.',
    'What does the balancing test mean in practice?',
  ],
} as const;
