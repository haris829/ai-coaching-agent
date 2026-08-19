/**
 * API client for all three capabilities.
 *
 * One place that knows about URLs, the error envelope and the auth header. Every failure is
 * surfaced as an `ApiError` carrying the backend's machine-readable `code` and its field-level
 * `details`, so forms can render per-field messages produced by the authoritative validator
 * rather than guessing.
 *
 * `api` covers UC-01 (quiz configuration), UC-02 (question bank) and the shared meta/session
 * endpoints; `attempts` covers UC-03 (attempt delivery); `results` covers UC-04/05/06; and `coaching`
 * covers UC-07. They all share one error envelope, so one `ApiError` type is enough for the whole
 * surface.
 */

import { authHeaders } from './session';
import type {
  AttemptReport,
  ImportListItem,
  ImportResult,
  FieldIssue,
  Paged,
  Question,
  QuestionListItem,
  QuestionPayload,
  QuestionSnapshot,
  QuizConfigurationResponse,
  QuizConfigurationInput,
  QuizRules,
  QuizSummary,
  SaveConfigurationResponse,
  TemplateGuide,
  Topic,
  Usage,
  VersionHistory,
} from './types';
import type { ConfigurationMeta, QuestionBankAvailability } from './types';
import type {
  AnswerResponse,
  AnswerSheet,
  AnswerSource,
  Attempt as DeliveredAttempt,
  AttemptCreated,
  AttemptEligibility,
  AttemptQuestion,
  AttemptState,
  AttemptTiming,
  BatchSaveResult,
  SaveAnswerResult,
  SubmissionPreview,
  SubmissionStatus,
} from './attemptTypes';
import type {
  FeedbackResponse,
  OutcomeResponse,
  ResultResponse,
} from './resultTypes';
import type {
  CoachingEligibility,
  CoachingExchange,
  CoachingMode,
  ReviewAdvance,
  ReviewQueue,
  SessionState,
  StartCoaching,
} from './coachingTypes';
import type { SessionInfo } from './session';

const API = '/api';
const BASE = `${API}/question-bank`;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: FieldIssue[];
  /**
   * Structured context the backend attached beyond the field errors — a `capacity` report on
   * `QUESTION_BANK_INSUFFICIENT`, `retryable` on `PERSISTENCE_FAILED`, `attemptId` on
   * `ATTEMPT_IN_PROGRESS`. Read it with {@link extraAs}.
   */
  readonly extra: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    details: FieldIssue[] = [],
    extra: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.extra = extra;
  }

  /** Typed read of one `extra` key. Returns `null` when absent. */
  extraAs<T>(key: string): T | null {
    return (this.extra[key] as T | undefined) ?? null;
  }

  /** Field path -> messages, for rendering inline next to inputs. */
  byField(): Map<string, string[]> {
    const map = new Map<string, string[]>();
    for (const issue of this.details) {
      const existing = map.get(issue.field) ?? [];
      existing.push(issue.message);
      map.set(issue.field, existing);
    }
    return map;
  }
}

/** Delegated so the selected identity is read per request, not captured at module load. */
function headers(extra: Record<string, string> = {}): Record<string, string> {
  return authHeaders(extra);
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = 'HTTP_ERROR';
  let message = `Request failed with status ${response.status}.`;
  let details: FieldIssue[] = [];
  let extra: Record<string, unknown> = {};
  try {
    const body = await response.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = Array.isArray(body.error.details) ? body.error.details : [];
      // Everything else in the envelope is structured context (capacity, retryable, attemptId…).
      const { code: _code, message: _message, details: _details, ...rest } = body.error;
      extra = rest as Record<string, unknown>;
    }
  } catch {
    // A non-JSON error body (e.g. a proxy failure) keeps the generic message above.
  }
  return new ApiError(response.status, code, message, details, extra);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (cause) {
    throw new ApiError(
      0,
      'NETWORK_ERROR',
      'The server could not be reached. Check that the backend is running on port 8000.',
    );
  }

  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: headers({ 'Content-Type': 'application/json' }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function get<T>(path: string): Promise<T> {
  return request<T>(path, { headers: headers() });
}

function query(params: Record<string, string | number | boolean | undefined | null | string[]>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) {
      for (const entry of value) if (entry) search.append(key, entry);
    } else {
      search.append(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

// ---------------------------------------------------------------------------
// Questions
// ---------------------------------------------------------------------------

export interface QuestionFilters {
  search?: string;
  type?: string[];
  status?: string[];
  topicId?: string;
  difficulty?: string;
  deliverableOnly?: boolean;
  page?: number;
  pageSize?: number;
  sortBy?: string;
  sortDir?: 'asc' | 'desc';
}

export const api = {
  listQuestions(filters: QuestionFilters = {}): Promise<Paged<QuestionListItem>> {
    return get(`${BASE}/questions${query({ ...filters })}`);
  },

  getQuestion(id: string): Promise<Question> {
    return get(`${BASE}/questions/${encodeURIComponent(id)}`);
  },

  createQuestion(payload: QuestionPayload): Promise<Question> {
    return json(`${BASE}/questions`, 'POST', payload);
  },

  updateQuestion(id: string, payload: Partial<QuestionPayload>): Promise<Question> {
    return json(`${BASE}/questions/${encodeURIComponent(id)}`, 'PATCH', payload);
  },

  retireQuestion(id: string, reason: string): Promise<Question> {
    return json(`${BASE}/questions/${encodeURIComponent(id)}/retire`, 'POST', { reason });
  },

  reactivateQuestion(id: string): Promise<Question> {
    return json(`${BASE}/questions/${encodeURIComponent(id)}/reactivate`, 'POST');
  },

  deleteQuestion(id: string): Promise<{ deleted: boolean; message: string }> {
    return json(`${BASE}/questions/${encodeURIComponent(id)}`, 'DELETE');
  },

  listVersions(id: string): Promise<QuestionSnapshot[]> {
    return get(`${BASE}/questions/${encodeURIComponent(id)}/versions`);
  },

  listUsages(id: string): Promise<Usage[]> {
    return get(`${BASE}/questions/${encodeURIComponent(id)}/usages`);
  },

  assignTopics(
    id: string,
    body: { topicIds?: string[]; topicNames?: string[]; replace?: boolean },
  ): Promise<Question> {
    return json(`${BASE}/questions/${encodeURIComponent(id)}/topics`, 'POST', body);
  },

  removeTopic(id: string, topicId: string): Promise<Question> {
    return json(
      `${BASE}/questions/${encodeURIComponent(id)}/topics/${encodeURIComponent(topicId)}`,
      'DELETE',
    );
  },

  // -------------------------------------------------------------------------
  // Topics
  // -------------------------------------------------------------------------

  listTopics(search?: string): Promise<Topic[]> {
    return get(`${BASE}/topics${query({ search })}`);
  },

  createTopic(body: { name: string; description?: string | null }): Promise<Topic> {
    return json(`${BASE}/topics`, 'POST', body);
  },

  updateTopic(
    id: string,
    body: { name?: string; description?: string | null; isActive?: boolean },
  ): Promise<Topic> {
    return json(`${BASE}/topics/${encodeURIComponent(id)}`, 'PATCH', body);
  },

  deleteTopic(id: string, force = false): Promise<{ message: string }> {
    return json(`${BASE}/topics/${encodeURIComponent(id)}${query({ force })}`, 'DELETE');
  },

  // -------------------------------------------------------------------------
  // CSV import
  // -------------------------------------------------------------------------

  importCsv(file: File): Promise<ImportResult> {
    const form = new FormData();
    form.append('file', file, file.name);
    return request(`${BASE}/imports`, { method: 'POST', headers: headers(), body: form });
  },

  listImports(): Promise<{ items: ImportListItem[]; total: number }> {
    return get(`${BASE}/imports`);
  },

  getImport(id: string): Promise<ImportResult> {
    return get(`${BASE}/imports/${encodeURIComponent(id)}`);
  },

  templateGuide(): Promise<TemplateGuide> {
    return get(`${BASE}/imports/template/guide`);
  },

  templateUrl(): string {
    return `${BASE}/imports/template`;
  },

  // -------------------------------------------------------------------------
  // Delivery + historical reporting
  // -------------------------------------------------------------------------

  deliveryPool(limit = 25): Promise<{ items: unknown[]; totalAvailable: number }> {
    return get(`${BASE}/delivery/pool${query({ limit })}`);
  },

  attemptReport(attemptRef: string): Promise<AttemptReport> {
    return get(`${BASE}/reporting/attempts/${encodeURIComponent(attemptRef)}`);
  },
  // -------------------------------------------------------------------------
  // Shared: identity + configuration vocabulary
  // -------------------------------------------------------------------------

  session(): Promise<SessionInfo> {
    return get(`${API}/session`);
  },

  configurationMeta(): Promise<ConfigurationMeta> {
    return get(`${API}/meta`);
  },

  // -------------------------------------------------------------------------
  // UC-01: quiz configuration (admin)
  // -------------------------------------------------------------------------

  listQuizzes(): Promise<{ quizzes: QuizSummary[] }> {
    return get(`${API}/admin/quizzes`);
  },

  getConfiguration(quizId: number): Promise<QuizConfigurationResponse> {
    return get(`${API}/admin/quizzes/${quizId}/configuration`);
  },

  /** Saves by creating a NEW immutable version. `created: false` means it was a no-op re-save. */
  saveConfiguration(
    quizId: number,
    payload: QuizConfigurationInput,
  ): Promise<SaveConfigurationResponse> {
    return json(`${API}/admin/quizzes/${quizId}/configuration`, 'PUT', payload);
  },

  listConfigurationVersions(quizId: number): Promise<VersionHistory> {
    return get(`${API}/admin/quizzes/${quizId}/configuration/versions`);
  },

  questionBankAvailability(quizId: number, topicIds: string[] = []): Promise<QuestionBankAvailability> {
    return get(`${API}/admin/quizzes/${quizId}/question-bank${query({ topicId: topicIds })}`);
  },

  // -------------------------------------------------------------------------
  // UC-01: learner-visible rules
  //
  // Attempts are NOT here. UC-03 owns the attempt lifecycle, and `attempts` below is the only way
  // to create or read one — UC-01's own attempt endpoints were removed when the two were merged,
  // so that there is exactly one owner of an attempt.
  // -------------------------------------------------------------------------

  quizRules(quizId: number): Promise<QuizRules> {
    return get(`${API}/quizzes/${quizId}/rules`);
  },
};

// ---------------------------------------------------------------------------
// UC-03: quiz attempt delivery
// ---------------------------------------------------------------------------

const V1 = `${API}/v1`;

/**
 * Attempt delivery.
 *
 * A separate object from `api` because the capability boundary is real: UC-03 identifies quizzes
 * and learners by opaque string, versions its routes under `/api/v1`, and is the sole owner of the
 * attempt lifecycle. Folding it into `api` would blur exactly the seam the backend keeps sharp.
 *
 * Two habits this client enforces, because they are the difference between a demo and something
 * that survives a flaky network:
 *
 *  * every write carries an explicit `source`, so an autosave stays distinguishable from a
 *    deliberate save in the audit trail;
 *  * submission always sends an idempotency key, so a double-click or a retry after a timeout
 *    cannot produce two submissions.
 */
export const attempts = {
  eligibility(quizId: string): Promise<{ eligibility: AttemptEligibility }> {
    return get(`${V1}/quizzes/${encodeURIComponent(quizId)}/attempt-eligibility`);
  },

  /**
   * Creates an attempt, locking the quiz's currently active configuration version onto it.
   *
   * Returns the attempt and a delivery descriptor, not the questions — fetch those with
   * {@link questions} or {@link questionAt}, per the locked presentation.
   */
  start(quizId: string): Promise<AttemptCreated> {
    return json(`${V1}/attempts`, 'POST', { quizId });
  },

  /** The resume path: the learner's open attempt for this quiz, or 404 when there is none. */
  active(quizId: string): Promise<{ attempt: DeliveredAttempt }> {
    return get(`${V1}/attempts/active${query({ quizId })}`);
  },

  get(attemptId: string): Promise<{ attempt: DeliveredAttempt }> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}`);
  },

  state(attemptId: string): Promise<{ state: AttemptState }> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/state`);
  },

  /**
   * Server-authoritative timing.
   *
   * `clientTime` is sent so the response can report the observed skew; it never influences the
   * remaining time, so this cannot be used to extend an attempt.
   */
  timing(attemptId: string, clientTime?: string): Promise<{ timing: AttemptTiming }> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/timing${query({ clientTime })}`);
  },

  /** All questions. Refused with 409 for a one-at-a-time attempt — use {@link questionAt}. */
  questions(attemptId: string): Promise<{ questions: AttemptQuestion[] }> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/questions`);
  },

  questionAt(attemptId: string, position: number): Promise<{ question: AttemptQuestion }> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/questions/at/${position}`);
  },

  currentQuestion(attemptId: string): Promise<{ question: AttemptQuestion }> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/questions/current`);
  },

  /** Persists the resume position, so a reload returns to the same question. */
  setCursor(attemptId: string, position: number): Promise<{ attempt: DeliveredAttempt }> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/cursor`, 'PUT', { position });
  },

  answerSheet(attemptId: string): Promise<AnswerSheet> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/answers`);
  },

  saveAnswer(
    attemptId: string,
    questionId: string,
    response: AnswerResponse,
    source: AnswerSource = 'MANUAL',
    expectedRevision?: number,
  ): Promise<SaveAnswerResult> {
    return json(
      `${V1}/attempts/${encodeURIComponent(attemptId)}/questions/${encodeURIComponent(
        questionId,
      )}/answer`,
      'PUT',
      { response, source, expectedRevision },
    );
  },

  /** Batch save — what the periodic autosave uses, so one request covers every dirty answer. */
  saveAnswers(
    attemptId: string,
    entries: { questionId: string; response: AnswerResponse; expectedRevision?: number }[],
    source: AnswerSource = 'AUTOSAVE',
  ): Promise<BatchSaveResult> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/answers`, 'POST', {
      answers: entries,
      source,
    });
  },

  setFlag(attemptId: string, questionId: string, flagged: boolean): Promise<unknown> {
    return json(
      `${V1}/attempts/${encodeURIComponent(attemptId)}/questions/${encodeURIComponent(
        questionId,
      )}/flag`,
      'PUT',
      { flagged },
    );
  },

  /** Read-only summary of what would be submitted. Never submits, however often it is called. */
  previewSubmission(attemptId: string): Promise<{ preview: SubmissionPreview }> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/submission/preview`);
  },

  confirmSubmission(attemptId: string, idempotencyKey: string): Promise<unknown> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/submission`, 'POST', {
      confirmed: true,
      idempotencyKey,
    });
  },

  /** Completes a submission left PENDING by a downstream failure. Safe to call repeatedly. */
  retrySubmission(attemptId: string, idempotencyKey?: string): Promise<unknown> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/submission/retry`, 'POST', {
      idempotencyKey,
    });
  },

  submissionStatus(attemptId: string): Promise<SubmissionStatus> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/submission`);
  },
};

/**
 * The result chain: UC-04 scoring, UC-05 pass/fail and certificate, UC-06 feedback.
 *
 * Separate from `attempts` for the same reason the backend keeps three modules: these are three
 * capabilities, and the only thing they share with the attempt is its id.
 *
 * Each `POST` here is **idempotent**, and each is also the retry path. Scoring, pass/fail and feedback
 * all normally happen automatically inside submission; these exist so the UI can drive a stage that a
 * transient failure left pending, and so this screen can prove the whole chain without waiting.
 */
export const results = {
  /** UC-04 — the score, with the marks awarded per question. */
  result(attemptId: string): Promise<ResultResponse> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/result`);
  },

  /** UC-04 — score the attempt, or replay the score it already has. */
  score(attemptId: string): Promise<ResultResponse> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/result`, 'POST');
  },

  /** UC-05 — pass/fail, the certificate and the CPD record. */
  outcome(attemptId: string): Promise<OutcomeResponse> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/outcome`);
  },

  /** UC-05 — determine pass/fail, or return the determination already recorded. */
  determine(attemptId: string): Promise<OutcomeResponse> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/outcome`, 'POST');
  },

  /** UC-05 — drive a pending certificate. Never mints a second one. */
  retryCertificate(attemptId: string): Promise<OutcomeResponse> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/outcome/certificate/retry`, 'POST');
  },

  /** UC-05 — drive a pending CPD synchronisation. Never double-logs. */
  retryCpd(attemptId: string): Promise<OutcomeResponse> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/outcome/cpd/retry`, 'POST');
  },

  /** UC-06 — the frozen feedback report. */
  feedback(attemptId: string): Promise<FeedbackResponse> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/feedback`);
  },

  /** UC-06 — generate the report, or return the one already generated. */
  generateFeedback(attemptId: string): Promise<FeedbackResponse> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/feedback`, 'POST');
  },
};


// ---------------------------------------------------------------------------
// UC-07 — AI Coaching Review Mode
// ---------------------------------------------------------------------------

/**
 * One coaching call that treats **503 as data**.
 *
 * UC-07's coaching turns return 503 with a full body — the session, the stored conversation and
 * `coachingAvailable: false` with a reason code — because the learner has not lost anything and the
 * client needs the session id to retry with. `request` throws on any non-2xx, which is right
 * everywhere else and wrong here: throwing would discard exactly the state the panel has to render.
 *
 * A 503 that is *not* a coaching body (a proxy, a dead backend) has no `session` in it and is thrown
 * as usual.
 */
async function coachingTurn<T extends { readonly session: unknown }>(
  path: string,
  method: string,
  body?: unknown,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: headers({ 'Content-Type': 'application/json' }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      0,
      'NETWORK_ERROR',
      'The server could not be reached. Check that the backend is running on port 8000.',
    );
  }

  if (response.status === 503) {
    const payload = await response.clone().json().catch(() => null);
    if (payload && typeof payload === 'object' && 'session' in payload) return payload as T;
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

/**
 * UC-07: post-submission Socratic coaching on the questions a learner got wrong.
 *
 * Separate from `results` because it is a different capability with a different lifecycle: the result
 * chain runs once, automatically, inside submission, whereas coaching is a conversation the learner
 * chooses to have afterwards.
 *
 * Nothing here can change a score, a verdict or a report — every endpoint behind it is read-only
 * towards UC-03, UC-04 and UC-06.
 */
export const coaching = {
  /**
   * May coaching be offered, and for which questions?
   *
   * The call this UI makes *before* rendering anything. It never fails for an ineligible attempt: an
   * unsubmitted attempt, an unreleased report and a correctly answered question all come back as
   * reasons, which is what lets the panel explain itself instead of disappearing.
   */
  eligibility(attemptId: string, questionId?: string): Promise<CoachingEligibility> {
    return get(
      `${V1}/attempts/${encodeURIComponent(attemptId)}/coaching/eligibility${query({ questionId })}`,
    );
  },

  /** Every incorrectly answered question, in delivery order, with coaching progress. */
  review(attemptId: string): Promise<ReviewQueue> {
    return get(`${V1}/attempts/${encodeURIComponent(attemptId)}/coaching/review`);
  },

  /** Finish with the current question and hand back the next one. Idempotent. */
  nextQuestion(attemptId: string, completeCurrent = true): Promise<ReviewAdvance> {
    return json(`${V1}/attempts/${encodeURIComponent(attemptId)}/coaching/review/next`, 'POST', {
      completeCurrent,
    });
  },

  /** Start — or resume — coaching for one incorrect question. Safe to call twice. */
  start(attemptId: string, questionId: string): Promise<StartCoaching> {
    return coachingTurn(
      `${V1}/attempts/${encodeURIComponent(attemptId)}/coaching/questions/${encodeURIComponent(questionId)}`,
      'POST',
    );
  },

  /** The session and its conversation. Readable during an AI outage. */
  session(sessionId: string): Promise<SessionState> {
    return get(`${V1}/coaching/sessions/${encodeURIComponent(sessionId)}`);
  },

  /** One exchange: the learner's message answered by one coach turn. */
  send(sessionId: string, message: string): Promise<CoachingExchange> {
    return coachingTurn(`${V1}/coaching/sessions/${encodeURIComponent(sessionId)}/messages`, 'POST', {
      message,
    });
  },

  /** Choose Socratic coaching or a direct concept explanation. */
  selectMode(sessionId: string, mode: CoachingMode): Promise<CoachingExchange> {
    return coachingTurn(`${V1}/coaching/sessions/${encodeURIComponent(sessionId)}/mode`, 'POST', {
      mode,
    });
  },

  /** Retry a coach turn that could not be produced. Never duplicates a session or an exchange. */
  retry(sessionId: string): Promise<CoachingExchange> {
    return coachingTurn(`${V1}/coaching/sessions/${encodeURIComponent(sessionId)}/retry`, 'POST');
  },

  /** Finish with this question, which advances the review queue past it. Idempotent. */
  complete(sessionId: string): Promise<SessionState> {
    return json(`${V1}/coaching/sessions/${encodeURIComponent(sessionId)}/complete`, 'POST');
  },
};
