import { describe, expect, it } from 'vitest';
import { createHarness, QUESTIONS } from './helpers';
import {
  LESSON_SUBJECT_RIGHTS,
  SESSION_ADVANCED,
  SESSION_BARE_LESSON,
  SESSION_MAIN,
  SESSION_MALFORMED_LESSON,
  SESSION_MISSING_LESSON,
  SESSION_NO_CONTEXT_USER,
  SESSION_TIMEOUT_LESSON,
  SESSION_UNAVAILABLE_LESSON,
  USER_ENROLLED,
  USER_ENROLLED_ADVANCED,
  USER_NO_CONTEXT,
} from '../src/adapters/mock/fixtures';
import {
  ActivityType,
  CoachingAction,
  CoachingStatus,
  SourceScope,
  TurnIntent,
} from '../src/domain/enums';

describe('source scope', () => {
  it('marks a lesson-covered answer as LESSON and cites a real section', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.lessonConcept);

    expect(response.source_scope).toBe(SourceScope.LESSON);
    expect(response.section_id).toBe('sec_consent');
    expect(response.concept_id).toBe('concept_consent');
    expect(response.free_form_available).toBe(false);
    expect(response.notice).toBeNull();
  });

  it('marks an off-lesson answer as GENERAL and says so plainly', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.offLesson);

    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.source_scope).toBe(SourceScope.GENERAL);
    expect(response.section_id).toBeNull();
    expect(response.concept_id).toBeNull();
    expect(response.notice).toMatch(/not covered in the linked lesson/i);
    expect(response.answer).toMatch(/general knowledge/i);
  });

  it('offers the free-form action when a question goes beyond the lesson', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.offLesson);

    expect(response.actions).toContain(CoachingAction.START_FREE_FORM_SESSION);
    expect(response.free_form_available).toBe(true);
  });

  it('does not offer the free-form action for a lesson-covered answer', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.actions).not.toContain(CoachingAction.START_FREE_FORM_SESSION);
    expect(response.actions).toContain(CoachingAction.EXPLAIN_DIFFERENTLY);
  });

  it('never fabricates a lesson reference for a general answer', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.offLesson);
    expect(response.section_id).toBeNull();
    expect(response.concept_id).toBeNull();
    expect(response.related_lesson_id).toBeNull();
    expect(response.answer).not.toMatch(/from the section/i);
    expect(response.answer).not.toMatch(/the lesson says/i);
  });

  it('logs an OFF_LESSON_QUESTION event', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.offLesson);
    const events = await h.activity.list({
      session_id: SESSION_MAIN,
      activity_type: ActivityType.OFF_LESSON_QUESTION,
    });
    expect(events).toHaveLength(1);
    expect(events[0]!.source_scope).toBe(SourceScope.GENERAL);
  });
});

describe('cross-lesson references', () => {
  it('answers from a real related lesson in the same course and marks it COURSE', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.crossLesson);

    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.source_scope).toBe(SourceScope.COURSE);
    expect(response.related_lesson_id).toBe(LESSON_SUBJECT_RIGHTS);
    expect(response.concept_id).toBe('concept_subject_access');
    expect(response.notice).toMatch(/another lesson in this course/i);
  });

  it('surfaces only related lessons that exist in the course catalogue', async () => {
    const h = createHarness();
    const response = await h.ask(QUESTIONS.lessonConcept);

    const ids = response.related_lessons.map((r) => r.lesson_id);
    expect(ids).toContain(LESSON_SUBJECT_RIGHTS);
    expect(ids).not.toContain('lesson_dp_ghost');
    for (const related of response.related_lessons) {
      expect(related.title).toBeTruthy();
      expect(related.lesson_id).toMatch(/^lesson_dp_/);
    }
  });

  it('degrades to GENERAL rather than inventing content when the related lesson will not load', async () => {
    const h = createHarness();
    // First call loads the linked lesson; make every later content call fail.
    const original = h.lessonProvider.getLesson.bind(h.lessonProvider);
    let calls = 0;
    h.lessonProvider.getLesson = async (courseId: string, lessonId: string) => {
      calls += 1;
      if (calls === 1) return original(courseId, lessonId);
      throw new Error('related lesson fetch exploded');
    };

    const response = await h.ask(QUESTIONS.crossLesson);

    expect(response.source_scope).toBe(SourceScope.GENERAL);
    expect(response.section_id).toBeNull();
    expect(response.diagnostics.degraded).toContain('related_lesson_content_unavailable');
    expect(response.answer).toBeTruthy();
  });
});

describe('lesson unavailable fallback', () => {
  const unavailableSessions: [string, string][] = [
    ['provider unavailable', SESSION_UNAVAILABLE_LESSON],
    ['provider timeout', SESSION_TIMEOUT_LESSON],
    ['lesson missing', SESSION_MISSING_LESSON],
    ['malformed payload', SESSION_MALFORMED_LESSON],
  ];

  it.each(unavailableSessions)('returns a safe fallback when the lesson is %s', async (_label, sessionId) => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: sessionId,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });

    expect(response.status).toBe(CoachingStatus.LESSON_UNAVAILABLE);
    expect(response.source_scope).toBe(SourceScope.GENERAL);
    expect(response.free_form_available).toBe(true);
    expect(response.notice).toMatch(/temporarily unavailable/i);
    expect(response.diagnostics.lesson_loaded).toBe(false);
    // The session is not blocked: general coaching still happens.
    expect(response.answer).toBeTruthy();
  });

  it('does not pretend the general fallback came from the lesson', async () => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_UNAVAILABLE_LESSON,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });

    expect(response.section_id).toBeNull();
    expect(response.concept_id).toBeNull();
    expect(response.answer).toMatch(/not available|general knowledge/i);
    expect(response.answer).not.toMatch(/from the section/i);
    expect(response.answer).not.toMatch(/the lesson anchors/i);
  });

  it('logs a LESSON_UNAVAILABLE event', async () => {
    const h = createHarness();
    await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_UNAVAILABLE_LESSON,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });
    const events = await h.activity.list({ activity_type: ActivityType.LESSON_UNAVAILABLE });
    expect(events.length).toBeGreaterThan(0);
  });

  it('handles a lesson that loads but carries no sections', async () => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_BARE_LESSON,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });

    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.source_scope).toBe(SourceScope.GENERAL);
    expect(response.diagnostics.lesson_loaded).toBe(true);
    expect(response.diagnostics.degraded).toContain('lesson_warning:lesson_has_no_sections');
  });
});

describe('missing learner context', () => {
  it('answers normally when the context service has nothing on file for the user', async () => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_NO_CONTEXT,
      session_id: SESSION_NO_CONTEXT_USER,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });

    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.source_scope).toBe(SourceScope.LESSON);
    expect(response.diagnostics.degraded).toContain('learner_context_unavailable');
  });

  it('answers normally when the learner context call throws', async () => {
    const h = createHarness();
    h.contextProvider.learnerFailureMode = 'TIMEOUT';

    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.diagnostics.degraded).toContain('learner_context_timeout');
  });

  it('adapts the explanation to the learner level when the context service supplies one', async () => {
    const h = createHarness();

    const beginner = await h.ask(QUESTIONS.lessonConcept); // USER_ENROLLED is BEGINNER
    expect(beginner.answer).toMatch(/unfamiliar/i);

    const advanced = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED_ADVANCED,
      session_id: SESSION_ADVANCED,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });
    expect(advanced.answer).toMatch(/edge cases and caveats/i);
    expect(advanced.answer).not.toMatch(/unfamiliar/i);
  });

  it('omits the level-specific coaching line when no level is on file', async () => {
    const h = createHarness();
    const response = await h.service.handleTurn({
      principal_user_id: USER_NO_CONTEXT,
      session_id: SESSION_NO_CONTEXT_USER,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });
    expect(response.answer).toBeTruthy();
    expect(response.answer).not.toMatch(/unfamiliar/i);
    expect(response.answer).not.toMatch(/edge cases and caveats/i);
  });
});
