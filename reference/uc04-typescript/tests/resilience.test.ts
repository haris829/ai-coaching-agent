import { describe, expect, it } from 'vitest';
import { createHarness, QUESTIONS } from './helpers';
import type { FailureMode } from '../src/adapters/mock/mock-providers';
import { CoachingStatus, SourceScope, TurnIntent } from '../src/domain/enums';
import { USER_ENROLLED, SESSION_MAIN } from '../src/adapters/mock/fixtures';

const FAILURE_MODES: FailureMode[] = ['UNAVAILABLE', 'TIMEOUT', 'THROW_PLAIN'];

describe('failure resilience', () => {
  it.each(FAILURE_MODES)('survives a courses provider failure (%s)', async (mode) => {
    const h = createHarness();
    h.courseProvider.failureMode = mode;

    const response = await h.ask(QUESTIONS.lessonConcept);
    // Course metadata is a nice-to-have; the lesson still answers.
    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.diagnostics.degraded.some((d) => d.startsWith('course_provider_'))).toBe(true);
  });

  it.each(FAILURE_MODES)('survives an enrollment provider failure (%s), failing closed', async (mode) => {
    const h = createHarness();
    h.enrollmentProvider.failureMode = mode;

    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.ENROLLMENT_UNVERIFIED);
    expect(response.answer).toBeNull();
    expect(h.lessonProvider.calls).toHaveLength(0);
  });

  it.each(FAILURE_MODES)('survives a lesson content provider failure (%s)', async (mode) => {
    const h = createHarness();
    h.lessonProvider.failureMode = mode;

    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.LESSON_UNAVAILABLE);
    expect(response.source_scope).toBe(SourceScope.GENERAL);
    expect(response.free_form_available).toBe(true);
  });

  it.each(FAILURE_MODES)('survives a context provider failure (%s), exposing no lesson', async (mode) => {
    const h = createHarness();
    h.contextProvider.failureMode = mode;

    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.CONTEXT_UNAVAILABLE);
    expect(response.answer).toBeNull();
    expect(response.section_id).toBeNull();
    expect(h.lessonProvider.calls).toHaveLength(0);
  });

  it('survives a retriever that throws', async () => {
    const h = createHarness();
    (h.service as unknown as { deps: { retriever: unknown } }).deps.retriever = {
      retrieve() {
        throw new Error('retriever exploded');
      },
      findConcept() {
        return null;
      },
    };

    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.source_scope).toBe(SourceScope.GENERAL);
    expect(response.diagnostics.degraded).toContain('retriever_failed');
  });

  it('survives every dependency failing at once without throwing', async () => {
    const h = createHarness();
    h.courseProvider.failureMode = 'THROW_PLAIN';
    h.lessonProvider.failureMode = 'THROW_PLAIN';
    h.contextProvider.learnerFailureMode = 'THROW_PLAIN';
    h.activity.alwaysFail = true;
    h.history.alwaysFail = true;
    h.falsePositives.alwaysFail = true;

    const response = await h.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.LESSON_UNAVAILABLE);
    expect(response.source_scope).toBe(SourceScope.GENERAL);
    expect(response.notice).toBeTruthy();

    // Quiz protection still holds under total degradation.
    const blocked = await h.ask('Tell me the answer.');
    expect(blocked.status).toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(blocked.answer_revealed).toBe(false);
  });

  const brokenDependency: [string, (h: ReturnType<typeof createHarness>) => void][] = [
    ['context provider', (h) => (h.contextProvider.failureMode = 'UNAVAILABLE')],
    ['enrollment provider', (h) => (h.enrollmentProvider.failureMode = 'UNAVAILABLE')],
    ['lesson content provider', (h) => (h.lessonProvider.failureMode = 'UNAVAILABLE')],
  ];

  it.each(brokenDependency)('returns no lesson content when the %s is down', async (_name, breakIt) => {
    const h = createHarness();
    breakIt(h);

    const response = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_MAIN,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });

    expect(response.section_id).toBeNull();
    expect(response.concept_id).toBeNull();
    // Distinctive phrases that only appear in the fixture lesson body.
    expect(response.answer ?? '').not.toMatch(/pre-ticked|freely given/i);
    expect(response.concept_explanation ?? '').not.toMatch(/pre-ticked|freely given/i);
  });

  it('handles an empty question on an ASK turn without crashing', async () => {
    const h = createHarness();
    const response = await h.ask('');
    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.source_scope).toBe(SourceScope.GENERAL);
  });

  it('handles an explain-differently turn with no prior history', async () => {
    const h = createHarness();
    const response = await h.explainDifferently();
    expect(response.status).toBe(CoachingStatus.ANSWERED);
    expect(response.answer).toBeTruthy();
  });
});
