import { describe, expect, it } from 'vitest';
import { createHarness, QUESTIONS } from './helpers';
import { COURSE_DP, LESSON_LAWFUL_BASIS, SESSION_MAIN, USER_ENROLLED } from '../src/adapters/mock/fixtures';
import { ActivityType, CoachingStatus, SourceScope } from '../src/domain/enums';

describe('progress and activity logging', () => {
  it('logs LESSON_LOADED with the identity a future UC needs', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept);

    const [event] = await h.activity.list({ activity_type: ActivityType.LESSON_LOADED });
    expect(event).toBeDefined();
    expect(event!.user_id).toBe(USER_ENROLLED);
    expect(event!.session_id).toBe(SESSION_MAIN);
    expect(event!.course_id).toBe(COURSE_DP);
    expect(event!.lesson_id).toBe(LESSON_LAWFUL_BASIS);
    expect(event!.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it('logs CONCEPT_EXPLAINED against user, session, course, lesson and concept', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept);

    const [event] = await h.activity.list({ activity_type: ActivityType.CONCEPT_EXPLAINED });
    expect(event).toBeDefined();
    expect(event!.concept_id).toBe('concept_consent');
    expect(event!.course_id).toBe(COURSE_DP);
    expect(event!.lesson_id).toBe(LESSON_LAWFUL_BASIS);
    expect(event!.source_scope).toBe(SourceScope.LESSON);
    expect(event!.difficulty_signal).toBe(false);
  });

  it('exposes explained concepts for future gap tracking', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept);
    await h.ask(QUESTIONS.lessonConceptOther);
    await h.explainDifferently();

    const concepts = await h.activity.listExplainedConcepts({ session_id: SESSION_MAIN });
    const byConcept = new Map(concepts.map((c) => [c.concept_id, c]));

    expect(byConcept.has('concept_consent')).toBe(true);
    expect(byConcept.has('concept_balancing_test')).toBe(true);

    const balancing = byConcept.get('concept_balancing_test')!;
    expect(balancing.explanation_count).toBe(2); // explained, then re-explained
    expect(balancing.difficulty_signal_count).toBe(1);
    expect(balancing.user_id).toBe(USER_ENROLLED);
    expect(balancing.course_id).toBe(COURSE_DP);
  });

  it('serves explained concepts only to the session owner', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept);

    const owned = await h.service.listExplainedConcepts(USER_ENROLLED, SESSION_MAIN);
    expect(owned.length).toBeGreaterThan(0);

    await expect(h.service.listExplainedConcepts('user_outsider', SESSION_MAIN)).rejects.toThrow(
      /does not belong/i,
    );
  });

  it('does not write lesson prose into event metadata', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept);
    const events = await h.activity.list({ session_id: SESSION_MAIN });
    const serialized = JSON.stringify(events);
    expect(serialized).not.toContain('freely given');
    expect(serialized).not.toContain('pre-ticked');
  });

  it('keeps working when the activity repository is completely down', async () => {
    const h = createHarness();
    h.activity.alwaysFail = true;

    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.answer).toBeTruthy();

    const explainAgain = await h.explainDifferently({ question: QUESTIONS.lessonConcept });
    expect(explainAgain.answer).toBeTruthy();
    expect(explainAgain.diagnostics.degraded).toContain('activity_repository_read_failed');
  });

  it('keeps working when the explanation history store is down', async () => {
    const h = createHarness();
    h.history.alwaysFail = true;

    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.answer).toBeTruthy();
    expect(response.diagnostics.degraded).toContain('explanation_history_read_failed');
  });

  it('records events in chronological order with unique ids', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept);
    await h.explainDifferently();

    const events = await h.activity.list({ session_id: SESSION_MAIN });
    const ids = events.map((e) => e.event_id);
    expect(new Set(ids).size).toBe(ids.length);
    const timestamps = events.map((e) => e.timestamp);
    expect([...timestamps].sort()).toEqual(timestamps);
  });
});
