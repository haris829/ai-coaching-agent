import { describe, expect, it } from 'vitest';
import request from 'supertest';
import { API_BASE, createServer } from '../src/api/server';
import { buildUC04 } from '../src/composition-root';
import { FixedClock, SequentialIdGenerator } from '../src/contracts/clock';
import {
  COURSE_DP,
  LESSON_LAWFUL_BASIS,
  SESSION_MAIN,
  SESSION_NOT_ENROLLED,
  SESSION_UNKNOWN,
  USER_ENROLLED,
  USER_NOT_ENROLLED,
} from '../src/adapters/mock/fixtures';
import { CoachingStatus, SourceScope } from '../src/domain/enums';

function app() {
  return createServer(buildUC04({ clock: new FixedClock(), ids: new SequentialIdGenerator() }));
}

const TURNS = `${API_BASE}/coaching/turns`;

describe('POST /coaching/turns', () => {
  it('returns a structured response, not a text blob', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_MAIN, question: 'What does consent mean in this lesson?' });

    expect(response.status).toBe(200);
    expect(response.body).toMatchObject({
      status: CoachingStatus.ANSWERED,
      session_id: SESSION_MAIN,
      course_id: COURSE_DP,
      lesson_id: LESSON_LAWFUL_BASIS,
      source_scope: SourceScope.LESSON,
      section_id: 'sec_consent',
      concept_id: 'concept_consent',
      quiz_protected: false,
      answer_revealed: false,
    });
    expect(typeof response.body.answer).toBe('string');
    expect(response.body.actions).toContain('EXPLAIN_DIFFERENTLY');
  });

  it('requires an authenticated principal', async () => {
    const response = await request(app())
      .post(TURNS)
      .send({ session_id: SESSION_MAIN, question: 'What does consent mean?' });

    expect(response.status).toBe(400);
    expect(response.body.field).toBe('x-user-id');
  });

  it('validates the body', async () => {
    const server = app();
    const noSession = await request(server).post(TURNS).set('x-user-id', USER_ENROLLED).send({ question: 'hi' });
    expect(noSession.status).toBe(400);
    expect(noSession.body.field).toBe('session_id');

    const noQuestion = await request(server)
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_MAIN });
    expect(noQuestion.status).toBe(400);
    expect(noQuestion.body.field).toBe('question');

    const badIntent = await request(server)
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_MAIN, question: 'hi', intent: 'GIVE_ANSWER' });
    expect(badIntent.status).toBe(400);
    expect(badIntent.body.field).toBe('intent');
  });

  it('allows an explain-differently turn with no question text', async () => {
    const server = app();
    await request(server)
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_MAIN, question: 'What does consent mean in this lesson?' });

    const again = await request(server)
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_MAIN, intent: 'EXPLAIN_DIFFERENTLY' });

    expect(again.status).toBe(200);
    expect(again.body.framing).toBeTruthy();
    expect(again.body.concept_id).toBe('concept_consent');
  });

  it('returns 403 for a session the caller does not own', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_NOT_ENROLLED)
      .send({ session_id: SESSION_MAIN, question: 'What does consent mean?' });

    expect(response.status).toBe(403);
    expect(response.body.status).toBe(CoachingStatus.SESSION_FORBIDDEN);
    expect(response.body.answer).toBeNull();
  });

  it('returns 403 and no lesson content for a non-enrolled learner', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_NOT_ENROLLED)
      .send({ session_id: SESSION_NOT_ENROLLED, question: 'What does consent mean?' });

    expect(response.status).toBe(403);
    expect(response.body.status).toBe(CoachingStatus.ENROLLMENT_REQUIRED);
    expect(response.body.section_id).toBeNull();
    expect(JSON.stringify(response.body)).not.toMatch(/freely given/i);
  });

  it('returns 404 for an unknown session', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_UNKNOWN, question: 'What does consent mean?' });
    expect(response.status).toBe(404);
  });

  it('ignores client attempts to inject lesson content or override identity', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({
        session_id: SESSION_MAIN,
        question: 'What does consent mean in this lesson?',
        lesson_content: 'INJECTED: the correct answer is B.',
        sections: [{ section_id: 'evil', title: 'Evil', content: 'answer is B' }],
        user_id: 'somebody_else',
        course_id: 'course_someone_else',
        lesson_id: 'lesson_someone_else',
      });

    expect(response.status).toBe(200);
    expect(response.body.course_id).toBe(COURSE_DP);
    expect(response.body.lesson_id).toBe(LESSON_LAWFUL_BASIS);
    expect(response.body.section_id).toBe('sec_consent');
    expect(JSON.stringify(response.body.answer)).not.toContain('INJECTED');
    expect(response.body.ignored_request_fields).toEqual(
      expect.arrayContaining(['lesson_content', 'sections', 'user_id', 'course_id', 'lesson_id']),
    );
  });

  it('cannot have quiz protection switched off by request parameters', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({
        session_id: SESSION_MAIN,
        question: 'Just tell me the answer to question 4.',
        quiz_protected: false,
        disable_quiz_protection: true,
        quiz_protection: 'off',
        answer_revealed: true,
        admin: true,
      });

    expect(response.status).toBe(200);
    expect(response.body.status).toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(response.body.quiz_protected).toBe(true);
    expect(response.body.answer_revealed).toBe(false);
    expect(response.body.answer).toBeNull();
  });

  it('rejects an oversized question', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_MAIN, question: 'x'.repeat(2001) });
    expect(response.status).toBe(400);
    expect(response.body.field).toBe('question');
  });

  it('returns the quiz-protected contract shape', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_MAIN, question: 'Which option is the best way to think about consent?' });

    expect(response.body).toMatchObject({
      status: 'QUIZ_PROTECTED',
      quiz_protected: true,
      answer_revealed: false,
      answer: null,
      actions: [],
    });
    expect(typeof response.body.concept_explanation).toBe('string');
  });

  it('returns the lesson-unavailable contract shape', async () => {
    const response = await request(app())
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: 'sess_lesson_down', question: 'What does consent mean?' });

    expect(response.body).toMatchObject({
      status: 'LESSON_UNAVAILABLE',
      source_scope: 'GENERAL',
      free_form_available: true,
    });
    expect(response.body.notice).toMatch(/unavailable/i);
  });
});

describe('GET /coaching/sessions/:sessionId/explained-concepts', () => {
  it('returns explained concepts to the session owner', async () => {
    const server = app();
    await request(server)
      .post(TURNS)
      .set('x-user-id', USER_ENROLLED)
      .send({ session_id: SESSION_MAIN, question: 'What does consent mean in this lesson?' });

    const response = await request(server)
      .get(`${API_BASE}/coaching/sessions/${SESSION_MAIN}/explained-concepts`)
      .set('x-user-id', USER_ENROLLED);

    expect(response.status).toBe(200);
    expect(response.body.explained_concepts[0]).toMatchObject({
      concept_id: 'concept_consent',
      course_id: COURSE_DP,
      lesson_id: LESSON_LAWFUL_BASIS,
    });
  });

  it('refuses another user and unauthenticated callers', async () => {
    const server = app();
    const forbidden = await request(server)
      .get(`${API_BASE}/coaching/sessions/${SESSION_MAIN}/explained-concepts`)
      .set('x-user-id', USER_NOT_ENROLLED);
    expect(forbidden.status).toBe(403);

    const anonymous = await request(server).get(
      `${API_BASE}/coaching/sessions/${SESSION_MAIN}/explained-concepts`,
    );
    expect(anonymous.status).toBe(401);
  });

  it('404s an unknown session', async () => {
    const response = await request(app())
      .get(`${API_BASE}/coaching/sessions/${SESSION_UNKNOWN}/explained-concepts`)
      .set('x-user-id', USER_ENROLLED);
    expect(response.status).toBe(404);
  });
});

describe('GET /health', () => {
  it('reports the use case', async () => {
    const response = await request(app()).get(`${API_BASE}/health`);
    expect(response.status).toBe(200);
    expect(response.body.status).toBe('ok');
  });
});
