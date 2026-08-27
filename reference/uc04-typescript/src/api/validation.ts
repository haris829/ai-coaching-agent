import type { CoachingTurnRequest } from '../domain/coaching';
import { TurnIntent } from '../domain/enums';

/**
 * Request validation for the coaching endpoint.
 *
 * SECURITY POSTURE: this is an ALLOW-LIST. Only the fields below are ever read from the body.
 * Anything else the client sends - `lesson_content`, `quiz_protection`, `skip_quiz_check`,
 * `user_id`, `course_id` - is ignored outright. The principal comes from the authenticated
 * request, and course/lesson identity comes from the server-side session binding.
 */
export const MAX_QUESTION_LENGTH = 2000;

export interface ValidationFailure {
  ok: false;
  error: string;
  field: string;
}

export interface ValidationSuccess {
  ok: true;
  value: CoachingTurnRequest;
  /** Names of ignored body fields, echoed back so integrators notice they had no effect. */
  ignoredFields: string[];
}

const ALLOWED_FIELDS = new Set([
  'session_id',
  'question',
  'intent',
  'concept_id',
  'expected_course_id',
  'expected_lesson_id',
]);

export function validateCoachingRequest(
  principalUserId: string | undefined,
  body: unknown,
): ValidationSuccess | ValidationFailure {
  if (!principalUserId || typeof principalUserId !== 'string' || principalUserId.trim() === '') {
    return { ok: false, error: 'Authenticated principal is required', field: 'x-user-id' };
  }
  if (body === null || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'Request body must be a JSON object', field: 'body' };
  }

  const record = body as Record<string, unknown>;
  const ignoredFields = Object.keys(record).filter((k) => !ALLOWED_FIELDS.has(k));

  const sessionId = record['session_id'];
  if (typeof sessionId !== 'string' || sessionId.trim() === '') {
    return { ok: false, error: 'session_id is required', field: 'session_id' };
  }

  const rawIntent = record['intent'];
  let intent: TurnIntent = TurnIntent.ASK;
  if (rawIntent !== undefined) {
    if (rawIntent !== TurnIntent.ASK && rawIntent !== TurnIntent.EXPLAIN_DIFFERENTLY) {
      return { ok: false, error: 'intent must be ASK or EXPLAIN_DIFFERENTLY', field: 'intent' };
    }
    intent = rawIntent;
  }

  const rawQuestion = record['question'];
  let question = '';
  if (rawQuestion !== undefined) {
    if (typeof rawQuestion !== 'string') {
      return { ok: false, error: 'question must be a string', field: 'question' };
    }
    if (rawQuestion.length > MAX_QUESTION_LENGTH) {
      return { ok: false, error: `question exceeds ${MAX_QUESTION_LENGTH} characters`, field: 'question' };
    }
    question = rawQuestion.trim();
  }
  if (question === '' && intent !== TurnIntent.EXPLAIN_DIFFERENTLY) {
    return { ok: false, error: 'question is required unless intent is EXPLAIN_DIFFERENTLY', field: 'question' };
  }

  const value: CoachingTurnRequest = {
    principal_user_id: principalUserId,
    session_id: sessionId,
    question,
    intent,
  };

  const conceptId = record['concept_id'];
  if (conceptId !== undefined) {
    if (typeof conceptId !== 'string') {
      return { ok: false, error: 'concept_id must be a string', field: 'concept_id' };
    }
    // Accepted as a HINT only - the service validates it against the loaded lesson.
    value.concept_id = conceptId;
  }

  const expectedCourse = record['expected_course_id'];
  if (expectedCourse !== undefined) {
    if (typeof expectedCourse !== 'string') {
      return { ok: false, error: 'expected_course_id must be a string', field: 'expected_course_id' };
    }
    // An ASSERTION checked against the session binding; it can never redirect the lookup.
    value.expected_course_id = expectedCourse;
  }

  const expectedLesson = record['expected_lesson_id'];
  if (expectedLesson !== undefined) {
    if (typeof expectedLesson !== 'string') {
      return { ok: false, error: 'expected_lesson_id must be a string', field: 'expected_lesson_id' };
    }
    value.expected_lesson_id = expectedLesson;
  }

  return { ok: true, value, ignoredFields };
}
