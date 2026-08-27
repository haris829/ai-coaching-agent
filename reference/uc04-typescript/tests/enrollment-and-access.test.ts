import { describe, expect, it } from 'vitest';
import { createHarness, QUESTIONS } from './helpers';
import {
  COURSE_DP,
  LESSON_LAWFUL_BASIS,
  SESSION_BAD_COURSE,
  SESSION_MAIN,
  SESSION_NOT_ENROLLED,
  SESSION_UNKNOWN,
  USER_ENROLLED,
  USER_NOT_ENROLLED,
} from '../src/adapters/mock/fixtures';
import { ActivityType, CoachingStatus, SourceScope, TurnIntent } from '../src/domain/enums';

describe('enrollment guard and session access control', () => {
  it('lets an enrolled user load the linked lesson and answer from it', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.lessonConcept);

    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.source_scope).toBe(SourceScope.LESSON);
    expect(response.lesson_id).toBe(LESSON_LAWFUL_BASIS);
    expect(response.diagnostics.lesson_loaded).toBe(true);
    expect(response.diagnostics.enrollment_verified).toBe(true);
    expect(response.answer).toBeTruthy();

    const events = await h.activity.list({ session_id: SESSION_MAIN });
    expect(events.map((e) => e.activity_type)).toContain(ActivityType.LESSON_LOADED);
  });

  it('refuses a non-enrolled user and returns no lesson content', async () => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_NOT_ENROLLED,
      session_id: SESSION_NOT_ENROLLED,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });

    expect(response.status).toBe(CoachingStatus.ENROLLMENT_REQUIRED);
    expect(response.answer).toBeNull();
    expect(response.concept_explanation).toBeNull();
    expect(response.section_id).toBeNull();
    expect(response.lesson_id).toBeNull();
    expect(response.diagnostics.enrollment_verified).toBe(false);
  });

  it('never calls the lesson content provider before enrollment verification succeeds', async () => {
    const h = createHarness();
    await h.service.handleTurn({
      principal_user_id: USER_NOT_ENROLLED,
      session_id: SESSION_NOT_ENROLLED,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });

    // The strongest available evidence: the content provider was not touched at all.
    expect(h.lessonProvider.calls).toHaveLength(0);

    await h.ask(QUESTIONS.lessonConcept);
    expect(h.lessonProvider.calls).toEqual([{ courseId: COURSE_DP, lessonId: LESSON_LAWFUL_BASIS }]);
  });

  it('fails closed when the enrollment service is unavailable', async () => {
    const h = createHarness();
    h.enrollmentProvider.failureMode = 'UNAVAILABLE';

    const response = await h.ask(QUESTIONS.lessonConcept);

    expect(response.status).toBe(CoachingStatus.ENROLLMENT_UNVERIFIED);
    expect(response.answer).toBeNull();
    expect(h.lessonProvider.calls).toHaveLength(0);
    expect(response.free_form_available).toBe(true);
  });

  it('blocks a session that belongs to another user', async () => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_NOT_ENROLLED,
      session_id: SESSION_MAIN, // owned by USER_ENROLLED
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });

    expect(response.status).toBe(CoachingStatus.SESSION_FORBIDDEN);
    expect(response.answer).toBeNull();
    expect(response.course_id).toBeNull();
    expect(h.lessonProvider.calls).toHaveLength(0);
  });

  it('rejects a client that asserts a different course or lesson than the session binding', async () => {
    const h = createHarness();

    const wrongCourse = await h.ask(QUESTIONS.lessonConcept, { expected_course_id: 'course_someone_else' });
    expect(wrongCourse.status).toBe(CoachingStatus.SESSION_FORBIDDEN);

    const wrongLesson = await h.ask(QUESTIONS.lessonConcept, { expected_lesson_id: 'lesson_someone_else' });
    expect(wrongLesson.status).toBe(CoachingStatus.SESSION_FORBIDDEN);

    expect(h.lessonProvider.calls).toHaveLength(0);
  });

  it('accepts a matching course/lesson assertion', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.lessonConcept, {
      expected_course_id: COURSE_DP,
      expected_lesson_id: LESSON_LAWFUL_BASIS,
    });
    expect(response.status).toBe(CoachingStatus.ANSWERED);
  });

  it('returns SESSION_NOT_FOUND for an unknown session', async () => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_UNKNOWN,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });
    expect(response.status).toBe(CoachingStatus.SESSION_NOT_FOUND);
    expect(h.lessonProvider.calls).toHaveLength(0);
  });

  it('returns COURSE_NOT_FOUND when the bound course does not exist', async () => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_BAD_COURSE,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });
    expect(response.status).toBe(CoachingStatus.COURSE_NOT_FOUND);
    expect(response.answer).toBeNull();
  });
});
