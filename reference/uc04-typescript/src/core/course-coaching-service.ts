import type { ActivityEvent, ActivityRepository } from '../contracts/activity-repository';
import type { Clock, IdGenerator } from '../contracts/clock';
import type { ContextProvider } from '../contracts/context-provider';
import type { CourseProvider, CourseSummary } from '../contracts/course-provider';
import type { EnrollmentProvider } from '../contracts/enrollment-provider';
import { ProviderError, failureKindOf } from '../contracts/errors';
import type { ExplanationEngine } from '../contracts/explanation-engine';
import type { ExplanationHistoryStore } from '../contracts/explanation-history-store';
import type { FalsePositiveLog } from '../contracts/false-positive-log';
import type { LessonContentProvider } from '../contracts/lesson-content-provider';
import type { QuizClassification, QuizIntentClassifier } from '../contracts/quiz-intent-classifier';
import type { LessonMatch, SectionRetriever } from '../contracts/section-retriever';
import type {
  CoachingDiagnostics,
  CoachingTurnRequest,
  CoachingTurnResponse,
  RelatedLessonView,
} from '../domain/coaching';
import {
  ActivityType,
  CoachingAction,
  CoachingStatus,
  DifficultySignalType,
  ExplanationLevel,
  FramingType,
  ProtectionDecision,
  QuizIntentLabel,
  SourceScope,
  TurnIntent,
} from '../domain/enums';
import type {
  LearnerContext,
  LessonContext,
  RelatedLessonRef,
  SessionBinding,
} from '../domain/lesson-context';
import { FramingSelector } from './explanation/framing-selector';
import { fingerprintExplanation, isEffectivelyIdentical } from './explanation/fingerprint';
import { normalizeLesson } from './lesson-normalizer';
import { stripAnswerLeaks } from './quiz/answer-leak-guard';
import { stableHash, uniqueTokens } from './text';

export interface CourseCoachingDependencies {
  courseProvider: CourseProvider;
  enrollmentProvider: EnrollmentProvider;
  lessonContentProvider: LessonContentProvider;
  contextProvider: ContextProvider;
  retriever: SectionRetriever;
  explanationEngine: ExplanationEngine;
  quizClassifier: QuizIntentClassifier;
  activityRepository: ActivityRepository;
  explanationHistory: ExplanationHistoryStore;
  falsePositiveLog: FalsePositiveLog;
  clock: Clock;
  ids: IdGenerator;
  framingSelector?: FramingSelector;
}

/**
 * Minimum retrieval score for naming the concept a quiz question is testing. Lower than the
 * normal LESSON threshold because a protected turn only needs the topic, and nothing it
 * returns can reveal an answer (see AnswerLeakGuard).
 */
const QUIZ_TOPIC_MIN_SCORE = 0.25;

/** Ordinal words used to guarantee a textually distinct final fallback explanation. */
const ORDINALS = [
  'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth',
  'eleventh', 'twelfth',
];

/**
 * UC-04 CORE.
 *
 * Everything here is expressed against the internal contracts only - no company API, no
 * vendor JSON, no HTTP. Replacing a mock adapter with a company adapter does not touch this
 * file. The service is also the sole owner of the invariants:
 *
 *   - lesson content is never requested before enrollment verification SUCCEEDS;
 *   - quiz protection runs on every turn and cannot be switched off by the caller;
 *   - source scope (LESSON / COURSE / GENERAL) is decided here, never by the engine;
 *   - no provider failure escapes: every path returns an explicit status.
 */
export class CourseCoachingService {
  private readonly framings: FramingSelector;

  constructor(private readonly deps: CourseCoachingDependencies) {
    this.framings = deps.framingSelector ?? new FramingSelector();
  }

  async handleTurn(request: CoachingTurnRequest): Promise<CoachingTurnResponse> {
    const degraded: string[] = [];

    // ---- 1. Session binding: the authoritative course/lesson identity -------------------
    let binding: SessionBinding;
    try {
      binding = await this.deps.contextProvider.getSessionBinding(request.session_id);
    } catch (error) {
      const kind = failureKindOf(error);
      if (kind === 'NOT_FOUND') {
        return this.terminal(request, CoachingStatus.SESSION_NOT_FOUND, null, null, {
          notice: 'That coaching session does not exist.',
          degraded,
        });
      }
      degraded.push(`context_provider_${kind.toLowerCase()}`);
      return this.terminal(request, CoachingStatus.CONTEXT_UNAVAILABLE, null, null, {
        notice:
          'Your session context is temporarily unavailable, so I cannot verify which lesson this session is linked to. No lesson content can be loaded until it is back.',
        degraded,
        freeForm: true,
      });
    }

    // ---- 2. Ownership: the client may assert, never redirect ----------------------------
    if (binding.user_id !== request.principal_user_id) {
      return this.terminal(request, CoachingStatus.SESSION_FORBIDDEN, null, null, {
        notice: 'You do not have access to this coaching session.',
        degraded,
      });
    }
    if (request.expected_course_id && request.expected_course_id !== binding.course_id) {
      return this.terminal(request, CoachingStatus.SESSION_FORBIDDEN, null, null, {
        notice: 'The requested course does not match this session.',
        degraded,
      });
    }
    if (request.expected_lesson_id && request.expected_lesson_id !== binding.lesson_id) {
      return this.terminal(request, CoachingStatus.SESSION_FORBIDDEN, null, null, {
        notice: 'The requested lesson does not match this session.',
        degraded,
      });
    }

    // ---- 3. Enrollment guard - FAIL CLOSED, before any content call ---------------------
    let enrollmentVerified = false;
    try {
      const status = await this.deps.enrollmentProvider.isEnrolled(binding.user_id, binding.course_id);
      enrollmentVerified = status.enrolled === true;
    } catch (error) {
      degraded.push(`enrollment_provider_${failureKindOf(error).toLowerCase()}`);
      await this.safeLog({
        activity_type: ActivityType.ENROLLMENT_DENIED,
        binding,
        sourceScope: SourceScope.NONE,
        metadata: { reason: 'enrollment_provider_failure' },
      });
      return this.terminal(request, CoachingStatus.ENROLLMENT_UNVERIFIED, binding.course_id, null, {
        notice:
          'I cannot verify your enrollment right now, so I am not able to open the lesson content. I can still help with the general topic.',
        degraded,
        freeForm: true,
      });
    }

    if (!enrollmentVerified) {
      await this.safeLog({
        activity_type: ActivityType.ENROLLMENT_DENIED,
        binding,
        sourceScope: SourceScope.NONE,
        metadata: { reason: 'not_enrolled' },
      });
      return this.terminal(request, CoachingStatus.ENROLLMENT_REQUIRED, binding.course_id, null, {
        notice: 'You are not enrolled on this course, so I cannot open its lesson content.',
        degraded,
      });
    }

    // ---- 4. Learner context (optional - absence must not break anything) ----------------
    let learner: LearnerContext | null = null;
    try {
      learner = await this.deps.contextProvider.getLearnerContext(binding.user_id);
      if (!learner.available) degraded.push('learner_context_unavailable');
    } catch (error) {
      degraded.push(`learner_context_${failureKindOf(error).toLowerCase()}`);
    }
    const explanationLevel: ExplanationLevel | null = learner?.explanation_level ?? null;

    // ---- 5. Course lookup ---------------------------------------------------------------
    let course: CourseSummary | null = null;
    try {
      course = await this.deps.courseProvider.getCourse(binding.course_id);
    } catch (error) {
      const kind = failureKindOf(error);
      if (kind === 'NOT_FOUND') {
        return this.terminal(request, CoachingStatus.COURSE_NOT_FOUND, binding.course_id, null, {
          notice: 'That course could not be found.',
          degraded,
        });
      }
      degraded.push(`course_provider_${kind.toLowerCase()}`);
    }

    // ---- 6. Lesson loading + normalization ----------------------------------------------
    let lesson: LessonContext | null = null;
    if (binding.lesson_id) {
      const loaded = await this.loadLesson(binding.course_id, binding.lesson_id, course, degraded);
      lesson = loaded;
    } else {
      degraded.push('session_not_linked_to_lesson');
    }

    if (lesson) {
      await this.safeLog({
        activity_type: ActivityType.LESSON_LOADED,
        binding,
        sourceScope: SourceScope.LESSON,
        metadata: {
          sections: lesson.sections.length,
          concepts: lesson.concepts.length,
          related_lessons: lesson.related_lessons.length,
        },
      });
    } else if (binding.lesson_id) {
      await this.safeLog({
        activity_type: ActivityType.LESSON_UNAVAILABLE,
        binding,
        sourceScope: SourceScope.GENERAL,
        metadata: { reason: degraded.find((d) => d.startsWith('lesson_')) ?? 'unknown' },
      });
    }

    // ---- 7. Quiz protection - always, lesson available or not --------------------------
    const classification = await this.classify(request.question, lesson, degraded);

    // Retrieval is needed both for answering and for judging false-positive risk.
    const retrieval = lesson ? this.safeRetrieve(request.question, lesson, degraded) : null;
    const retrievalScore = retrieval?.bestMatch?.score ?? null;

    if (classification.label === QuizIntentLabel.QUIZ_ANSWER_REQUEST) {
      return this.quizProtectedTurn(request, binding, lesson, classification, explanationLevel, degraded, retrievalScore);
    }
    if (classification.label === QuizIntentLabel.UNCERTAIN) {
      return this.clarificationTurn(request, binding, lesson, classification, degraded, retrievalScore);
    }

    // ---- 8. Answering -------------------------------------------------------------------
    return this.answerTurn(
      request,
      binding,
      lesson,
      course,
      classification,
      explanationLevel,
      retrieval,
      degraded,
    );
  }

  /** Explained-concept view for future UCs (e.g. gap tracking). Ownership enforced. */
  async listExplainedConcepts(principalUserId: string, sessionId: string) {
    const binding = await this.deps.contextProvider.getSessionBinding(sessionId);
    if (binding.user_id !== principalUserId) {
      throw new ProviderError('FORBIDDEN', 'CourseCoachingService', 'Session does not belong to this user');
    }
    return this.deps.activityRepository.listExplainedConcepts({
      session_id: sessionId,
      user_id: principalUserId,
    });
  }

  // ==================================================================== lesson loading ===

  private async loadLesson(
    courseId: string,
    lessonId: string,
    course: CourseSummary | null,
    degraded: string[],
  ): Promise<LessonContext | null> {
    try {
      const raw = await this.deps.lessonContentProvider.getLesson(courseId, lessonId);
      const { lesson, warnings } = normalizeLesson(raw, {
        courseId,
        lessonId,
        courseName: course?.course_name ?? null,
        courseLessonIds: course?.lesson_ids ?? null,
        providerName: 'lesson_content_provider',
        nowIso: this.deps.clock.nowIso(),
      });
      for (const warning of warnings) degraded.push(`lesson_warning:${warning}`);
      return lesson;
    } catch (error) {
      degraded.push(`lesson_${failureKindOf(error).toLowerCase()}`);
      return null;
    }
  }

  private safeRetrieve(question: string, lesson: LessonContext, degraded: string[]) {
    try {
      return this.deps.retriever.retrieve(question, lesson);
    } catch (error) {
      degraded.push('retriever_failed');
      return null;
    }
  }

  private async classify(
    question: string,
    lesson: LessonContext | null,
    degraded: string[],
  ): Promise<QuizClassification> {
    // Server-derived only. Nothing about the client request can weaken this.
    const assessmentContext =
      lesson?.sections.some((s) => /quiz|assessment|knowledge check|test your/i.test(s.title)) ?? false;
    try {
      return await this.deps.quizClassifier.classify({ question, assessmentContext });
    } catch (error) {
      degraded.push('quiz_classifier_failed');
      // Fail SAFE: an unavailable classifier means we do not answer freely.
      return {
        label: QuizIntentLabel.UNCERTAIN,
        confidence: 0,
        signals: ['CLASSIFIER_ERROR'],
        classifier: 'unavailable',
      };
    }
  }

  // ================================================================== quiz protection ====

  private async quizProtectedTurn(
    request: CoachingTurnRequest,
    binding: SessionBinding,
    lesson: LessonContext | null,
    classification: QuizClassification,
    explanationLevel: ExplanationLevel | null,
    degraded: string[],
    retrievalScore: number | null,
  ): Promise<CoachingTurnResponse> {
    let conceptExplanation =
      'I will not give you the answer to an assessment question - working it out is the part that teaches you something. What I can do is explain the concept the question is getting at, so you can reason it through yourself.';
    let match: LessonMatch | null = null;
    let framing: FramingType | null = null;

    if (lesson) {
      // A quiz question is padded with scaffolding ("which option is...", "for question 4"),
      // which drags the normal retrieval score down. So on a protected turn we also accept a
      // match anchored on a section title or concept NAME - enough to name the topic being
      // tested - but never one anchored on a stray keyword or body word, which would mean
      // lecturing about an unrelated section.
      const retrieval = this.safeRetrieve(request.question, lesson, degraded);
      match =
        retrieval?.bestMatch ??
        retrieval?.matches.find((m) => m.anchor === 'NAME' && m.score >= QUIZ_TOPIC_MIN_SCORE) ??
        null;
      if (match) {
        const conceptKey = this.conceptKey(match, request.question);
        const generated = await this.generateNonDuplicate({
          sessionId: binding.session_id,
          conceptKey,
          request,
          lesson,
          match,
          relatedLesson: null,
          sourceScope: SourceScope.LESSON,
          explanationLevel,
          courseName: lesson.course_name,
          quizSafeMode: true,
          degraded,
        });
        framing = generated.framing;
        conceptExplanation = `${conceptExplanation}\n\n${generated.text}`;
      }
    }

    // Defence in depth: strip anything that could confirm or reveal an option.
    const guarded = stripAnswerLeaks(conceptExplanation);
    if (guarded.redacted) degraded.push('answer_leak_guard_redacted');

    await this.safeLog({
      activity_type: ActivityType.QUIZ_PROTECTED,
      binding,
      sourceScope: match ? SourceScope.LESSON : SourceScope.NONE,
      conceptId: match?.concept?.concept_id ?? null,
      topic: match ? match.section.title : null,
      metadata: {
        classifier: classification.classifier,
        confidence: classification.confidence,
        signals: classification.signals.join(','),
        answer_revealed: false,
      },
    });

    await this.maybeLogFalsePositive(request, binding, classification, ProtectionDecision.BLOCKED, lesson);

    return {
      status: CoachingStatus.QUIZ_PROTECTED,
      session_id: binding.session_id,
      course_id: binding.course_id,
      lesson_id: lesson?.lesson_id ?? binding.lesson_id,
      source_scope: match ? SourceScope.LESSON : SourceScope.NONE,
      section_id: match?.section.section_id ?? null,
      concept_id: match?.concept?.concept_id ?? null,
      answer: null,
      concept_explanation: guarded.text,
      framing,
      actions: [],
      quiz_protected: true,
      answer_revealed: false,
      free_form_available: false,
      notice: 'I can explain the concept being tested. I will not confirm or reveal answers.',
      related_lesson_id: null,
      related_lessons: [],
      diagnostics: this.diagnostics({
        lessonLoaded: Boolean(lesson),
        enrollmentVerified: true,
        retrievalScore,
        classification,
        degraded,
      }),
    };
  }

  private async clarificationTurn(
    request: CoachingTurnRequest,
    binding: SessionBinding,
    lesson: LessonContext | null,
    classification: QuizClassification,
    degraded: string[],
    retrievalScore: number | null,
  ): Promise<CoachingTurnResponse> {
    await this.safeLog({
      activity_type: ActivityType.CLARIFICATION_REQUESTED,
      binding,
      sourceScope: SourceScope.NONE,
      metadata: {
        classifier: classification.classifier,
        confidence: classification.confidence,
        signals: classification.signals.join(','),
      },
    });

    await this.maybeLogFalsePositive(request, binding, classification, ProtectionDecision.CLARIFY, lesson);

    return {
      status: CoachingStatus.NEEDS_CLARIFICATION,
      session_id: binding.session_id,
      course_id: binding.course_id,
      lesson_id: lesson?.lesson_id ?? binding.lesson_id,
      source_scope: SourceScope.NONE,
      section_id: null,
      concept_id: null,
      answer: null,
      concept_explanation: null,
      framing: null,
      actions: [],
      quiz_protected: false,
      answer_revealed: false,
      free_form_available: false,
      notice:
        'I am not sure whether you are after the answer to an assessment question or an explanation of the idea behind it. I can do the second one - tell me which concept you want unpacked and I will take it from there.',
      related_lesson_id: null,
      related_lessons: [],
      diagnostics: this.diagnostics({
        lessonLoaded: Boolean(lesson),
        enrollmentVerified: true,
        retrievalScore,
        classification,
        degraded,
      }),
    };
  }

  // ========================================================================= answering ===

  private async answerTurn(
    request: CoachingTurnRequest,
    binding: SessionBinding,
    lesson: LessonContext | null,
    course: CourseSummary | null,
    classification: QuizClassification,
    explanationLevel: ExplanationLevel | null,
    retrieval: ReturnType<CourseCoachingService['safeRetrieve']>,
    degraded: string[],
  ): Promise<CoachingTurnResponse> {
    const isExplainDifferently = request.intent === TurnIntent.EXPLAIN_DIFFERENTLY;

    // --- resolve the target -------------------------------------------------------------
    let match: LessonMatch | null = null;
    let sourceScope: SourceScope = SourceScope.GENERAL;
    let relatedLesson: RelatedLessonRef | null = null;
    let relatedLessonContext: LessonContext | null = null;

    if (lesson && isExplainDifferently) {
      match = await this.resolveExplainDifferentlyTarget(request, binding, lesson, degraded);
      if (match) sourceScope = SourceScope.LESSON;
    }

    if (!match && lesson && retrieval?.bestMatch) {
      match = retrieval.bestMatch;
      sourceScope = SourceScope.LESSON;
    }

    // Cross-lesson: the linked lesson does not cover it, but a REAL related lesson does.
    if (!match && lesson && retrieval?.relatedMatch) {
      relatedLesson = retrieval.relatedMatch.related;
      relatedLessonContext = await this.loadLesson(
        binding.course_id,
        relatedLesson.lesson_id,
        course,
        degraded,
      );
      if (relatedLessonContext) {
        const relatedRetrieval = this.safeRetrieve(request.question, relatedLessonContext, degraded);
        const relatedMatch = relatedRetrieval?.bestMatch ?? relatedRetrieval?.matches[0] ?? null;
        if (relatedMatch) {
          match = relatedMatch;
          sourceScope = SourceScope.COURSE;
        }
      } else {
        degraded.push('related_lesson_content_unavailable');
      }
    }

    if (!match) sourceScope = SourceScope.GENERAL;

    const groundingLesson = sourceScope === SourceScope.COURSE ? relatedLessonContext : lesson;
    const conceptKey = match
      ? this.conceptKey(match, request.question)
      : this.topicKey(request.question);

    // --- generate, rejecting anything effectively identical to earlier attempts ---------
    const generated = await this.generateNonDuplicate({
      sessionId: binding.session_id,
      conceptKey,
      request,
      lesson: groundingLesson,
      match,
      relatedLesson: sourceScope === SourceScope.COURSE ? relatedLesson : null,
      sourceScope,
      explanationLevel,
      courseName: course?.course_name ?? lesson?.course_name ?? null,
      quizSafeMode: false,
      degraded,
    });

    // --- events -------------------------------------------------------------------------
    const status = lesson || !binding.lesson_id ? CoachingStatus.ANSWERED : CoachingStatus.LESSON_UNAVAILABLE;
    const lessonMissing = Boolean(binding.lesson_id) && !lesson;

    if (isExplainDifferently) {
      await this.safeLog({
        activity_type: ActivityType.EXPLAIN_DIFFERENTLY,
        binding,
        sourceScope,
        conceptId: match?.concept?.concept_id ?? null,
        topic: match ? match.section.title : this.topicKey(request.question),
        difficultySignal: true,
        signalType: DifficultySignalType.EXPLAIN_DIFFERENTLY,
        metadata: {
          framing: generated.framing,
          attempt_index: generated.attemptIndex,
          framings_exhausted: generated.exhausted,
        },
      });
    } else if (sourceScope === SourceScope.LESSON || sourceScope === SourceScope.COURSE) {
      await this.safeLog({
        activity_type: ActivityType.CONCEPT_EXPLAINED,
        binding,
        sourceScope,
        conceptId: match?.concept?.concept_id ?? null,
        topic: match ? match.section.title : null,
        metadata: {
          framing: generated.framing,
          section_id: match?.section.section_id ?? null,
          related_lesson_id: relatedLesson?.lesson_id ?? null,
        },
      });
    }

    if (sourceScope === SourceScope.GENERAL) {
      await this.safeLog({
        activity_type: lessonMissing ? ActivityType.LESSON_UNAVAILABLE : ActivityType.OFF_LESSON_QUESTION,
        binding,
        sourceScope: SourceScope.GENERAL,
        topic: this.topicKey(request.question),
        metadata: { reason: lessonMissing ? 'lesson_unavailable' : 'not_covered_by_lesson' },
      });
    }

    // --- response -----------------------------------------------------------------------
    const actions: CoachingAction[] = [CoachingAction.EXPLAIN_DIFFERENTLY];
    if (sourceScope === SourceScope.GENERAL) actions.push(CoachingAction.START_FREE_FORM_SESSION);

    const notice = this.noticeFor(sourceScope, lessonMissing, relatedLesson);

    return {
      status,
      session_id: binding.session_id,
      course_id: binding.course_id,
      lesson_id: binding.lesson_id,
      source_scope: sourceScope,
      section_id: match?.section.section_id ?? null,
      concept_id: match?.concept?.concept_id ?? null,
      answer: generated.text,
      concept_explanation: null,
      framing: generated.framing,
      actions,
      quiz_protected: false,
      answer_revealed: false,
      free_form_available: sourceScope === SourceScope.GENERAL,
      notice,
      related_lesson_id: sourceScope === SourceScope.COURSE ? relatedLesson?.lesson_id ?? null : null,
      related_lessons: this.relatedLessonViews(lesson, retrieval?.relatedMatch?.related ?? null, sourceScope),
      diagnostics: this.diagnostics({
        lessonLoaded: Boolean(lesson),
        enrollmentVerified: true,
        retrievalScore: retrieval?.bestMatch?.score ?? null,
        classification,
        degraded,
        attemptIndex: generated.attemptIndex,
        framingsUsed: generated.framingsUsed,
      }),
    };
  }

  private noticeFor(
    scope: SourceScope,
    lessonMissing: boolean,
    relatedLesson: RelatedLessonRef | null,
  ): string | null {
    if (lessonMissing) {
      return 'The linked lesson content is temporarily unavailable, so this answer comes from general knowledge rather than the lesson.';
    }
    if (scope === SourceScope.GENERAL) {
      return 'This is not covered in the linked lesson, but I can explain the broader topic.';
    }
    if (scope === SourceScope.COURSE && relatedLesson) {
      return `This is covered in "${relatedLesson.title}", another lesson in this course.`;
    }
    return null;
  }

  /**
   * Related lessons surfaced to the caller. Sourced exclusively from the normalized lesson,
   * which has already been filtered against the course catalogue - nothing is invented here.
   */
  private relatedLessonViews(
    lesson: LessonContext | null,
    highlighted: RelatedLessonRef | null,
    scope: SourceScope,
  ): RelatedLessonView[] {
    if (!lesson) return [];
    if (scope === SourceScope.COURSE && highlighted) {
      return [
        {
          lesson_id: highlighted.lesson_id,
          title: highlighted.title,
          relationship: highlighted.relationship,
        },
      ];
    }
    return lesson.related_lessons.map((r) => ({
      lesson_id: r.lesson_id,
      title: r.title,
      relationship: r.relationship,
    }));
  }

  // ============================================================ explain differently =====

  /**
   * Resolve which concept an "explain differently" refers to:
   *   1. a client-supplied concept id, ONLY if it exists in the lesson (never trusted blindly);
   *   2. otherwise the last concept explained in this session;
   *   3. otherwise fall back to retrieval on whatever text was supplied.
   */
  private async resolveExplainDifferentlyTarget(
    request: CoachingTurnRequest,
    binding: SessionBinding,
    lesson: LessonContext,
    degraded: string[],
  ): Promise<LessonMatch | null> {
    if (request.concept_id) {
      const direct = this.deps.retriever.findConcept(request.concept_id, lesson);
      if (direct) return direct;
      degraded.push('unknown_concept_id_ignored');
    }

    try {
      const events = await this.deps.activityRepository.list({ session_id: binding.session_id });
      const priorConcepts = events
        .filter(
          (e) =>
            (e.activity_type === ActivityType.CONCEPT_EXPLAINED ||
              e.activity_type === ActivityType.EXPLAIN_DIFFERENTLY ||
              e.activity_type === ActivityType.QUIZ_PROTECTED) &&
            e.concept_id,
        )
        .sort((a, b) => (a.timestamp < b.timestamp ? -1 : 1));
      const last = priorConcepts[priorConcepts.length - 1];
      if (last?.concept_id) {
        const found = this.deps.retriever.findConcept(last.concept_id, lesson);
        if (found) return found;
      }
    } catch (error) {
      degraded.push('activity_repository_read_failed');
    }

    if (request.question.trim().length > 0) {
      const retrieval = this.safeRetrieve(request.question, lesson, degraded);
      return retrieval?.bestMatch ?? null;
    }
    return null;
  }

  /**
   * Generate an explanation that is NOT effectively identical to any earlier explanation of
   * the same concept in the same session.
   *
   *   - framings are walked in "unused first, then least-recently-used" order;
   *   - each candidate is fingerprinted and rejected if it collides (exactly or near-exactly);
   *   - if every framing is exhausted, the LRU framing is used and a distinct closing line
   *     guarantees the response is not byte-identical to a previous one.
   */
  private async generateNonDuplicate(params: {
    sessionId: string;
    conceptKey: string;
    request: CoachingTurnRequest;
    lesson: LessonContext | null;
    match: LessonMatch | null;
    relatedLesson: RelatedLessonRef | null;
    sourceScope: SourceScope;
    explanationLevel: ExplanationLevel | null;
    courseName: string | null;
    quizSafeMode: boolean;
    degraded: string[];
  }): Promise<{
    text: string;
    framing: FramingType;
    attemptIndex: number;
    exhausted: boolean;
    framingsUsed: FramingType[];
  }> {
    let attempts: Awaited<ReturnType<ExplanationHistoryStore['listAttempts']>> = [];
    let historyAvailable = true;
    try {
      attempts = await this.deps.explanationHistory.listAttempts(params.sessionId, params.conceptKey);
    } catch (error) {
      historyAvailable = false;
      params.degraded.push('explanation_history_read_failed');
    }

    const plan = this.framings.plan(attempts);
    const candidates = plan.candidates;

    for (let index = 0; index < candidates.length; index += 1) {
      const framing = candidates[index] as FramingType;
      const generated = await this.deps.explanationEngine.explain({
        question: params.request.question,
        framing,
        sourceScope: params.sourceScope,
        explanationLevel: params.explanationLevel,
        lesson: params.lesson,
        section: params.match?.section ?? null,
        concept: params.match?.concept ?? null,
        relatedLesson: params.relatedLesson,
        courseName: params.courseName,
        variantSeed: attempts.length + index,
        quizSafeMode: params.quizSafeMode,
      });

      const fingerprint = fingerprintExplanation(generated.text);
      const verdict = isEffectivelyIdentical(fingerprint, attempts);
      if (!verdict.isDuplicate) {
        await this.recordAttempt(params, framing, fingerprint, historyAvailable);
        return {
          text: generated.text,
          framing,
          attemptIndex: attempts.length,
          exhausted: plan.exhausted,
          framingsUsed: plan.used,
        };
      }
      params.degraded.push(`duplicate_rejected:${framing}:${verdict.reason}`);
    }

    // Every framing produced something we have already said. Use the least-recently-used
    // framing and make the response demonstrably distinct rather than repeating verbatim.
    const framing = candidates[0] as FramingType;
    const fallback = await this.deps.explanationEngine.explain({
      question: params.request.question,
      framing,
      sourceScope: params.sourceScope,
      explanationLevel: params.explanationLevel,
      lesson: params.lesson,
      section: params.match?.section ?? null,
      concept: params.match?.concept ?? null,
      relatedLesson: params.relatedLesson,
      courseName: params.courseName,
      variantSeed: attempts.length + candidates.length,
      quizSafeMode: params.quizSafeMode,
    });

    const ordinal = ORDINALS[Math.min(attempts.length, ORDINALS.length) - 1] ?? `angle-${attempts.length}`;
    const text = `${fallback.text}\n\nThat is the ${ordinal} angle I have on this from the lesson itself. If it still is not landing, the useful next step is a free-form session where I am not held to this lesson's material.`;

    const fingerprint = fingerprintExplanation(text);
    await this.recordAttempt(params, framing, fingerprint, historyAvailable);

    return {
      text,
      framing,
      attemptIndex: attempts.length,
      exhausted: true,
      framingsUsed: plan.used,
    };
  }

  private async recordAttempt(
    params: { sessionId: string; conceptKey: string; degraded: string[] },
    framing: FramingType,
    fingerprint: { fingerprint: string; tokens: string[] },
    historyAvailable: boolean,
  ): Promise<void> {
    if (!historyAvailable) return;
    try {
      await this.deps.explanationHistory.record({
        session_id: params.sessionId,
        concept_id: params.conceptKey,
        framing_type: framing,
        explanation_fingerprint: fingerprint.fingerprint,
        fingerprint_tokens: fingerprint.tokens,
        timestamp: this.deps.clock.nowIso(),
      });
    } catch (error) {
      params.degraded.push('explanation_history_write_failed');
    }
  }

  // ============================================================ logging & response ======

  private async maybeLogFalsePositive(
    request: CoachingTurnRequest,
    binding: SessionBinding,
    classification: QuizClassification,
    decision: ProtectionDecision,
    lesson: LessonContext | null,
  ): Promise<void> {
    // A turn is a SUSPECTED false positive when protection fired even though the learner
    // showed learning intent, or named a concept the lesson actually teaches, or the
    // classifier itself was unsure. These are exactly the cases worth re-reviewing.
    const learningIntent = classification.signals.includes('LEARNING_INTENT');
    const namesLessonConcept = this.mentionsLessonConcept(request.question, lesson);
    const suspect =
      classification.label !== QuizIntentLabel.CONCEPT_LEARNING_REQUEST &&
      (learningIntent || namesLessonConcept || classification.label === QuizIntentLabel.UNCERTAIN);
    if (!suspect) return;

    try {
      await this.deps.falsePositiveLog.record({
        record_id: this.deps.ids.next('fp'),
        session_id: binding.session_id,
        user_id: binding.user_id,
        // The learner's own question is required for tuning. No lesson content is stored.
        question: request.question,
        classifier_result: classification.label,
        classifier_confidence: classification.confidence,
        classifier_signals: [...classification.signals],
        final_decision: decision,
        timestamp: this.deps.clock.nowIso(),
      });
    } catch (error) {
      // A tuning log outage must never affect the learner's turn.
    }
  }

  /** Activity logging is best-effort by design: a logging outage cannot fail a turn. */
  private async safeLog(params: {
    activity_type: ActivityType;
    binding: SessionBinding;
    sourceScope: SourceScope;
    conceptId?: string | null;
    topic?: string | null;
    difficultySignal?: boolean;
    signalType?: DifficultySignalType | null;
    metadata?: Record<string, string | number | boolean | null>;
  }): Promise<void> {
    const event: ActivityEvent = {
      event_id: this.deps.ids.next('evt'),
      activity_type: params.activity_type,
      user_id: params.binding.user_id,
      session_id: params.binding.session_id,
      course_id: params.binding.course_id,
      lesson_id: params.binding.lesson_id,
      concept_id: params.conceptId ?? null,
      topic: params.topic ?? null,
      source_scope: params.sourceScope,
      timestamp: this.deps.clock.nowIso(),
      difficulty_signal: params.difficultySignal ?? false,
      signal_type: params.signalType ?? null,
      metadata: params.metadata ?? {},
    };
    try {
      await this.deps.activityRepository.append(event);
    } catch (error) {
      // Swallowed on purpose - see contract note on ActivityRepository.
    }
  }

  /** True when the question names a concept the supplied lesson actually teaches. */
  private mentionsLessonConcept(question: string, lesson: LessonContext | null): boolean {
    if (!lesson) return false;
    const asked = new Set(uniqueTokens(question));
    if (asked.size === 0) return false;
    for (const concept of lesson.concepts) {
      const nameTokens = uniqueTokens(concept.name);
      if (nameTokens.length > 0 && nameTokens.every((t) => asked.has(t))) return true;
      for (const keyword of concept.keywords) {
        const kwTokens = uniqueTokens(keyword);
        // Multi-word keywords only: a single common word ("rights") is far too loose a hook.
        if (kwTokens.length > 1 && kwTokens.every((t) => asked.has(t))) return true;
      }
    }
    return false;
  }

  private conceptKey(match: LessonMatch, _question: string): string {
    // Section-level key when the section matched but no single concept carried the question.
    if (match.concept) return match.concept.concept_id;
    return `section:${match.section.section_id}`;
  }

  private topicKey(question: string): string {
    const tokens = uniqueTokens(question).slice(0, 8).sort();
    if (tokens.length === 0) return 'topic:unspecified';
    return `topic:${stableHash(tokens.join(' '))}`;
  }

  private diagnostics(params: {
    lessonLoaded: boolean;
    enrollmentVerified: boolean;
    retrievalScore: number | null;
    classification: QuizClassification | null;
    degraded: string[];
    attemptIndex?: number;
    framingsUsed?: FramingType[];
  }): CoachingDiagnostics {
    return {
      lesson_loaded: params.lessonLoaded,
      enrollment_verified: params.enrollmentVerified,
      retrieval_score: params.retrievalScore,
      quiz_label: params.classification?.label ?? null,
      quiz_confidence: params.classification?.confidence ?? null,
      explanation_attempt_index: params.attemptIndex ?? null,
      framings_used: params.framingsUsed ?? [],
      degraded: [...params.degraded],
    };
  }

  /** Terminal responses: blocked, unavailable or forbidden. Never carry lesson content. */
  private terminal(
    request: CoachingTurnRequest,
    status: CoachingStatus,
    courseId: string | null,
    lessonId: string | null,
    options: { notice: string; degraded: string[]; freeForm?: boolean },
  ): CoachingTurnResponse {
    return {
      status,
      session_id: request.session_id,
      course_id: courseId,
      lesson_id: lessonId,
      source_scope: options.freeForm ? SourceScope.GENERAL : SourceScope.NONE,
      section_id: null,
      concept_id: null,
      answer: null,
      concept_explanation: null,
      framing: null,
      actions: options.freeForm ? [CoachingAction.START_FREE_FORM_SESSION] : [],
      quiz_protected: false,
      answer_revealed: false,
      free_form_available: Boolean(options.freeForm),
      notice: options.notice,
      related_lesson_id: null,
      related_lessons: [],
      diagnostics: this.diagnostics({
        lessonLoaded: false,
        enrollmentVerified: status !== CoachingStatus.ENROLLMENT_REQUIRED && status !== CoachingStatus.ENROLLMENT_UNVERIFIED,
        retrievalScore: null,
        classification: null,
        degraded: options.degraded,
      }),
    };
  }
}
