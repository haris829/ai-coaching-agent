/**
 * Answer shaping for UC-03, kept as pure functions.
 *
 * The backend is the authority on whether an answer is valid — this module never duplicates that
 * judgement. What it does own is the three things a *client* needs and a server cannot do for it:
 *
 *  1. build the payload shape each question type expects, from whatever the inputs hold;
 *  2. decide whether an answer has genuinely changed, so autosave sends only dirty answers;
 *  3. decide whether an answer looks complete, so the navigator can colour a question before the
 *     next round-trip.
 *
 * (2) matters more than it looks. Without it a 20-question paper re-sends every answer on every
 * autosave tick, and the "unsaved changes" warning never clears because something is always in
 * flight. It is pure and unit-tested for the same reason.
 *
 * Local completeness is deliberately *optimistic-but-conservative*: it marks complete only what the
 * server would also accept, and the server's own view always wins on the next read.
 */

import type {
  AnswerResponse,
  AttemptQuestion,
  AttemptQuestionType,
  AttemptSubQuestion,
  SubAnswerResponse,
} from '../api/attemptTypes';

/** Human labels for the five structures. Mirrors the backend's `QUESTION_TYPE_LABELS`. */
export const ATTEMPT_TYPE_LABELS: Record<AttemptQuestionType, string> = {
  SINGLE_CHOICE: 'Single choice',
  TRUE_FALSE: 'True / false',
  MULTI_SELECT: 'Multiple select',
  DRAG_TO_ORDER: 'Drag to order',
  SCENARIO: 'Scenario',
};

export const PRESENTATION_LABELS = {
  ALL_AT_ONCE: 'All questions at once',
  ONE_AT_A_TIME: 'One question at a time',
} as const;

// ---------------------------------------------------------------------------
// Empty values
// ---------------------------------------------------------------------------

/**
 * The starting value for a question with no saved answer.
 *
 * `null` rather than an empty structure: sending `{selectedOptionIds: []}` would be a deliberate
 * "I select nothing", whereas `null` means "not answered", and the backend distinguishes the two.
 */
export function emptyResponse(): AnswerResponse {
  return null;
}

// ---------------------------------------------------------------------------
// Reading a response
// ---------------------------------------------------------------------------

export function selectedOptionId(response: AnswerResponse): string | null {
  if (response && typeof response === 'object' && 'selectedOptionId' in response) {
    const value = (response as { selectedOptionId: unknown }).selectedOptionId;
    return typeof value === 'string' ? value : null;
  }
  return null;
}

export function selectedOptionIds(response: AnswerResponse): string[] {
  if (response && typeof response === 'object' && 'selectedOptionIds' in response) {
    const value = (response as { selectedOptionIds: unknown }).selectedOptionIds;
    return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];
  }
  return [];
}

export function booleanValue(response: AnswerResponse): boolean | null {
  if (response && typeof response === 'object' && 'value' in response) {
    const value = (response as { value: unknown }).value;
    return typeof value === 'boolean' ? value : null;
  }
  return null;
}

export function orderedItemIds(response: AnswerResponse): string[] {
  if (response && typeof response === 'object' && 'orderedItemIds' in response) {
    const value = (response as { orderedItemIds: unknown }).orderedItemIds;
    return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];
  }
  return [];
}

/** The sub-answer for one scenario sub-question, or `null` when it is unanswered. */
export function subResponse(response: AnswerResponse, subQuestionId: string): SubAnswerResponse | null {
  if (!response || typeof response !== 'object' || !('responses' in response)) return null;
  const entries = (response as { responses: unknown }).responses;
  if (!Array.isArray(entries)) return null;
  for (const entry of entries) {
    if (entry && typeof entry === 'object' && (entry as { subQuestionId?: unknown }).subQuestionId === subQuestionId) {
      return ((entry as { answer?: SubAnswerResponse }).answer ?? null) as SubAnswerResponse | null;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Writing a response
// ---------------------------------------------------------------------------

export function withSelectedOption(optionId: string): AnswerResponse {
  return { selectedOptionId: optionId };
}

export function withBoolean(value: boolean): AnswerResponse {
  return { value };
}

/**
 * Toggle one option of a multi-select.
 *
 * Selecting nothing collapses to `null`, not to an empty list: a learner who unticks their last box
 * has returned the question to unanswered, which is what the navigator should show.
 */
export function toggleOption(response: AnswerResponse, optionId: string): AnswerResponse {
  const current = selectedOptionIds(response);
  const next = current.includes(optionId)
    ? current.filter((entry) => entry !== optionId)
    : [...current, optionId];
  return next.length === 0 ? null : { selectedOptionIds: next };
}

/** Move one item of a drag-to-order question by `delta` places, clamped to the list. */
export function moveItem(response: AnswerResponse, items: string[], itemId: string, delta: number): AnswerResponse {
  const current = orderedItemIds(response);
  // An unanswered ordering starts from the delivered order, so the first move is a real edit rather
  // than a jump from nothing.
  const ordering = current.length === items.length ? [...current] : [...items];
  const from = ordering.indexOf(itemId);
  if (from === -1) return response;
  const to = from + delta;
  if (to < 0 || to >= ordering.length) return response;
  ordering.splice(to, 0, ...ordering.splice(from, 1));
  return { orderedItemIds: ordering };
}

/** The delivered order, as an explicit answer. Used when a learner accepts the order as shown. */
export function acceptDeliveredOrder(items: string[]): AnswerResponse {
  return { orderedItemIds: [...items] };
}

/**
 * Replace one sub-answer within a scenario response.
 *
 * A sub-answer set to `null` is dropped rather than sent as an explicit null, and a scenario with no
 * sub-answers left collapses to `null` — the same "back to unanswered" rule as multi-select.
 */
export function withSubResponse(
  response: AnswerResponse,
  subQuestions: AttemptSubQuestion[],
  subQuestionId: string,
  answer: SubAnswerResponse | null,
): AnswerResponse {
  const existing = new Map<string, SubAnswerResponse>();
  for (const sub of subQuestions) {
    const found = subResponse(response, sub.subQuestionId);
    if (found !== null) existing.set(sub.subQuestionId, found);
  }
  if (answer === null) existing.delete(subQuestionId);
  else existing.set(subQuestionId, answer);

  if (existing.size === 0) return null;
  // Emitted in delivered order so two equal answers always serialise identically, which is what
  // makes the change detection below reliable.
  return {
    responses: subQuestions
      .filter((sub) => existing.has(sub.subQuestionId))
      .map((sub) => ({ subQuestionId: sub.subQuestionId, answer: existing.get(sub.subQuestionId)! })),
  };
}

// ---------------------------------------------------------------------------
// Change detection
// ---------------------------------------------------------------------------

/**
 * Whether two responses are the same answer.
 *
 * Multi-select is compared as a **set**, matching the backend, which canonicalises selection order:
 * re-ticking the same boxes in a different order is not a change and must not burn a revision.
 * Everything else is compared structurally.
 */
export function sameResponse(left: AnswerResponse, right: AnswerResponse): boolean {
  if (left === null || right === null) return left === right;

  const leftIds = selectedOptionIds(left);
  const rightIds = selectedOptionIds(right);
  if (leftIds.length > 0 || rightIds.length > 0) {
    if (leftIds.length !== rightIds.length) return false;
    const set = new Set(leftIds);
    return rightIds.every((id) => set.has(id));
  }

  return canonical(left) === canonical(right);
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, entry]) => entry !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([key, entry]) => `${key}:${canonical(entry)}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

// ---------------------------------------------------------------------------
// Local completeness
// ---------------------------------------------------------------------------

/**
 * Whether an answer looks complete for this question.
 *
 * Used only to colour the navigator between saves; the server's `complete` flag is authoritative and
 * replaces this on every read. Deliberately conservative: a partly answered scenario is incomplete,
 * and a multi-select below its declared minimum is incomplete, because that is what the server says.
 */
export function looksComplete(question: AttemptQuestion, response: AnswerResponse): boolean {
  if (response === null) return false;

  switch (question.questionType) {
    case 'SINGLE_CHOICE':
      return selectedOptionId(response) !== null;
    case 'TRUE_FALSE':
      return booleanValue(response) !== null;
    case 'MULTI_SELECT': {
      const chosen = selectedOptionIds(response);
      const minimum = question.minSelections ?? 1;
      return chosen.length >= minimum;
    }
    case 'DRAG_TO_ORDER': {
      const items = question.orderItems ?? [];
      const ordering = orderedItemIds(response);
      return items.length > 0 && ordering.length === items.length;
    }
    case 'SCENARIO': {
      const subs = question.subQuestions ?? [];
      if (subs.length === 0) return false;
      return subs.every((sub) => subLooksComplete(sub, subResponse(response, sub.subQuestionId)));
    }
    default:
      return false;
  }
}

function subLooksComplete(sub: AttemptSubQuestion, answer: SubAnswerResponse | null): boolean {
  if (answer === null) return false;
  switch (sub.type) {
    case 'SINGLE_CHOICE':
      return selectedOptionId(answer) !== null;
    case 'TRUE_FALSE':
      return booleanValue(answer) !== null;
    case 'MULTI_SELECT':
      return selectedOptionIds(answer).length >= (sub.minSelections ?? 1);
    case 'DRAG_TO_ORDER':
      return orderedItemIds(answer).length === (sub.orderItems ?? []).length && (sub.orderItems ?? []).length > 0;
    default:
      return false;
  }
}

/** Whether the learner has put *anything* against this question. */
export function looksAnswered(response: AnswerResponse): boolean {
  return response !== null;
}
