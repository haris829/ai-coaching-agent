import { describe, expect, it } from 'vitest';
import { buildUC04 } from '../src/composition-root';
import { FixedClock, SequentialIdGenerator } from '../src/contracts/clock';
import type { CourseProvider, CourseSummary } from '../src/contracts/course-provider';
import type { EnrollmentProvider, EnrollmentStatus } from '../src/contracts/enrollment-provider';
import type { ContextProvider } from '../src/contracts/context-provider';
import type { LessonContentProvider, RawLessonPayload } from '../src/contracts/lesson-content-provider';
import type { ActivityEvent, ActivityRepository, ExplainedConceptRecord } from '../src/contracts/activity-repository';
import type { LearnerContext, SessionBinding } from '../src/domain/lesson-context';
import { CoachingStatus, SourceScope, TurnIntent } from '../src/domain/enums';

/**
 * ADAPTER REPLACEMENT PROOF.
 *
 * This suite stands in for the future integration. Every mock is replaced by a differently
 * shaped "company" adapter - different ids, different payload shape (camelCase, nested
 * blocks), a different persistence model - and UC-04 core is used UNCHANGED. Nothing in
 * src/core or src/domain is touched or subclassed here.
 */

const COMPANY_COURSE = 'CRS-99001';
const COMPANY_LESSON = 'LSN-42';
const COMPANY_RELATED = 'LSN-43';
const COMPANY_USER = 'emp-7781';
const COMPANY_SESSION = 'coach-sess-abc';

class CompanyCoursesAdapter implements CourseProvider {
  async getCourse(courseId: string): Promise<CourseSummary> {
    // A real adapter would call the Courses Agent here and map its response.
    const vendor = {
      courseRef: courseId,
      displayName: 'Workplace Fire Safety',
      curriculum: [{ ref: COMPANY_LESSON }, { ref: COMPANY_RELATED }],
    };
    return {
      course_id: vendor.courseRef,
      course_name: vendor.displayName,
      lesson_ids: vendor.curriculum.map((l) => l.ref),
    };
  }
}

class CompanyAccessAdapter implements EnrollmentProvider {
  async isEnrolled(userId: string, courseId: string): Promise<EnrollmentStatus> {
    const grants = [{ employee: COMPANY_USER, course: COMPANY_COURSE, state: 'ACTIVE' }];
    const grant = grants.find((g) => g.employee === userId && g.course === courseId);
    return { enrolled: grant?.state === 'ACTIVE' };
  }
}

class CompanyLessonApiAdapter implements LessonContentProvider {
  async getLesson(courseId: string, lessonId: string): Promise<RawLessonPayload> {
    // Vendor shape: camelCase, nested blocks, different field names entirely.
    const vendorResponse = {
      lessonRef: lessonId,
      courseRef: courseId,
      heading: lessonId === COMPANY_LESSON ? 'Fire Extinguisher Selection' : 'Evacuation Routes',
      blocks:
        lessonId === COMPANY_LESSON
          ? [
              {
                ref: 'blk-1',
                heading: 'Matching the Extinguisher to the Fire Class',
                text: 'Fire classes describe what is burning. Water extinguishers suit Class A solids but are dangerous on live electrical equipment. Carbon dioxide extinguishers displace oxygen and leave no residue.',
                bullets: [
                  'Class A covers ordinary solids such as paper and wood',
                  'Never use water on live electrical equipment',
                ],
                topics: ['tpc-fire-class'],
              },
            ]
          : [
              {
                ref: 'blk-9',
                heading: 'Assembly Points',
                text: 'An assembly point is the designated place occupants gather after evacuating so a roll call can be taken.',
                bullets: ['Roll call confirms everyone is out'],
                topics: ['tpc-assembly-point'],
              },
            ],
      topics:
        lessonId === COMPANY_LESSON
          ? [
              {
                ref: 'tpc-fire-class',
                label: 'Fire class',
                blockRef: 'blk-1',
                definition: 'A fire class is the categorisation of a fire by the material that is burning.',
                tags: ['fire class', 'class a', 'extinguisher'],
              },
            ]
          : [
              {
                ref: 'tpc-assembly-point',
                label: 'Assembly point',
                blockRef: 'blk-9',
                definition: 'An assembly point is the designated gathering place used for roll call after an evacuation.',
                tags: ['assembly point', 'roll call', 'evacuation'],
              },
            ],
      seeAlso:
        lessonId === COMPANY_LESSON
          ? [{ ref: COMPANY_RELATED, label: 'Evacuation Routes', kind: 'follow-on', tags: ['evacuation', 'assembly point', 'roll call'] }]
          : [],
    };

    // The adapter's job: map vendor JSON onto the shape the normalizer understands.
    return {
      lesson_id: vendorResponse.lessonRef,
      course_id: vendorResponse.courseRef,
      title: vendorResponse.heading,
      sections: vendorResponse.blocks.map((b, index) => ({
        section_id: b.ref,
        title: b.heading,
        body: b.text,
        key_points: b.bullets,
        concept_ids: b.topics,
        order: index,
      })),
      concepts: vendorResponse.topics.map((t) => ({
        concept_id: t.ref,
        name: t.label,
        section_id: t.blockRef,
        summary: t.definition,
        keywords: t.tags,
      })),
      related_lessons: vendorResponse.seeAlso.map((s) => ({
        lesson_id: s.ref,
        title: s.label,
        relationship: s.kind,
        keywords: s.tags,
      })),
    };
  }
}

class CompanySessionServiceAdapter implements ContextProvider {
  async getSessionBinding(sessionId: string): Promise<SessionBinding> {
    return {
      session_id: sessionId,
      user_id: COMPANY_USER,
      course_id: COMPANY_COURSE,
      lesson_id: COMPANY_LESSON,
    };
  }

  async getLearnerContext(userId: string): Promise<LearnerContext> {
    return { user_id: userId, explanation_level: null, preferred_language: 'en-GB', available: true };
  }
}

/** A "database" adapter with an entirely different internal shape. */
class CompanyEventStoreAdapter implements ActivityRepository {
  readonly rows: { kind: string; subject: string; payload: string }[] = [];

  async append(event: ActivityEvent): Promise<void> {
    this.rows.push({
      kind: event.activity_type,
      subject: `${event.user_id}/${event.session_id}`,
      payload: JSON.stringify(event),
    });
  }

  async list(): Promise<ActivityEvent[]> {
    return this.rows.map((r) => JSON.parse(r.payload) as ActivityEvent);
  }

  async listExplainedConcepts(): Promise<ExplainedConceptRecord[]> {
    return [];
  }
}

function buildCompanyContainer() {
  const activityRepository = new CompanyEventStoreAdapter();
  const container = buildUC04({
    courseProvider: new CompanyCoursesAdapter(),
    enrollmentProvider: new CompanyAccessAdapter(),
    lessonContentProvider: new CompanyLessonApiAdapter(),
    contextProvider: new CompanySessionServiceAdapter(),
    activityRepository,
    clock: new FixedClock(),
    ids: new SequentialIdGenerator(),
  });
  return { container, activityRepository };
}

describe('adapter replacement requires no change to UC-04 core', () => {
  it('answers from a completely different content source and shape', async () => {
    const { container } = buildCompanyContainer();

    const response = await container.service.handleTurn({
      principal_user_id: COMPANY_USER,
      session_id: COMPANY_SESSION,
      question: 'What is a fire class?',
      intent: TurnIntent.ASK,
    });

    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.source_scope).toBe(SourceScope.LESSON);
    expect(response.course_id).toBe(COMPANY_COURSE);
    expect(response.lesson_id).toBe(COMPANY_LESSON);
    expect(response.section_id).toBe('blk-1');
    expect(response.concept_id).toBe('tpc-fire-class');
    expect(response.answer).toMatch(/fire class/i);
  });

  it('keeps enforcing the enrollment guard against the company access service', async () => {
    const { container } = buildCompanyContainer();
    const response = await container.service.handleTurn({
      principal_user_id: 'emp-0000-not-enrolled',
      session_id: COMPANY_SESSION,
      question: 'What is a fire class?',
      intent: TurnIntent.ASK,
    });
    // The session belongs to COMPANY_USER, so ownership fails first - still no content.
    expect(response.status).toBe(CoachingStatus.SESSION_FORBIDDEN);
    expect(response.answer).toBeNull();
  });

  it('keeps quiz protection working with the company adapters', async () => {
    const { container } = buildCompanyContainer();
    const response = await container.service.handleTurn({
      principal_user_id: COMPANY_USER,
      session_id: COMPANY_SESSION,
      question: 'Just tell me the answer to question 2.',
      intent: TurnIntent.ASK,
    });
    expect(response.status).toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(response.answer_revealed).toBe(false);
  });

  it('keeps explain-differently working with the company adapters', async () => {
    const { container } = buildCompanyContainer();
    const first = await container.service.handleTurn({
      principal_user_id: COMPANY_USER,
      session_id: COMPANY_SESSION,
      question: 'What is a fire class?',
      intent: TurnIntent.ASK,
    });
    const second = await container.service.handleTurn({
      principal_user_id: COMPANY_USER,
      session_id: COMPANY_SESSION,
      question: '',
      intent: TurnIntent.EXPLAIN_DIFFERENTLY,
    });

    expect(second.framing).not.toBe(first.framing);
    expect(second.answer).not.toBe(first.answer);
    expect(second.concept_id).toBe('tpc-fire-class');
  });

  it('follows a related lesson from the company catalogue', async () => {
    const { container } = buildCompanyContainer();
    const response = await container.service.handleTurn({
      principal_user_id: COMPANY_USER,
      session_id: COMPANY_SESSION,
      question: 'Where is the assembly point for roll call after an evacuation?',
      intent: TurnIntent.ASK,
    });

    expect(response.source_scope).toBe(SourceScope.COURSE);
    expect(response.related_lesson_id).toBe(COMPANY_RELATED);
    expect(response.concept_id).toBe('tpc-assembly-point');
  });

  it('writes activity into the company event store', async () => {
    const { container, activityRepository } = buildCompanyContainer();
    await container.service.handleTurn({
      principal_user_id: COMPANY_USER,
      session_id: COMPANY_SESSION,
      question: 'What is a fire class?',
      intent: TurnIntent.ASK,
    });

    expect(activityRepository.rows.length).toBeGreaterThan(0);
    expect(activityRepository.rows.map((r) => r.kind)).toContain('CONCEPT_EXPLAINED');
    expect(activityRepository.rows[0]!.subject).toBe(`${COMPANY_USER}/${COMPANY_SESSION}`);
  });
});
