/**
 * Mirror of the authoritative quiz-configuration rules.
 *
 * The backend is the gate: nothing is persisted without passing
 * `backend/app/modules/quiz_configuration/domain/rules.py`. This file exists so the admin form can
 * give instant feedback on every keystroke without a round trip, and so it can warn about an
 * impossible configuration before the request is even sent.
 *
 * The two are pinned together by `backend/tests/quiz_configuration/test_frontend_contract_sync.py`,
 * which fails if the limits, the vocabularies or the labels drift. `GET /api/meta` publishes the
 * same values at runtime, so the UI can also read them rather than trust this copy.
 *
 * Pure and dependency-free, which is what makes it unit-testable without a DOM.
 */

import { QUESTION_TYPES } from '../api/types';
import type {
  CapacityReport,
  DeliveryMode,
  QuestionType,
  QuestionTypeSelection,
  QuizConfigurationInput,
} from '../api/types';

export const DELIVERY_MODES = ['practice', 'assessment', 'exam'] as const;

/** Numeric bounds. Mirrors `LIMITS` in the backend's rules module. */
export const CONFIGURATION_LIMITS = {
  questionCount: { min: 1, max: 100 },
  timeLimitMinutes: { min: 1, max: 480 },
  passMark: { min: 1, max: 100 },
  maxAttempts: { min: 1, max: 50 },
  questionQuota: { min: 1, max: 100 },
} as const;

/** Mirrors `QUESTION_TYPE_LABELS` in the question bank's domain enums. */
export const QUESTION_TYPE_LABELS: Record<QuestionType, string> = {
  SINGLE_CHOICE: 'Single choice',
  TRUE_FALSE: 'True / False',
  MULTI_SELECT: 'Multi-select',
  SCENARIO: 'Scenario',
  DRAG_TO_ORDER: 'Drag-to-order',
};

/** Mirrors `DELIVERY_MODE_LABELS` in the quiz-configuration domain enums. */
export const DELIVERY_MODE_LABELS: Record<DeliveryMode, string> = {
  practice: 'Practice (immediate feedback, untimed friendly)',
  assessment: 'Assessment (graded, feedback after submission)',
  exam: 'Exam (graded, no feedback, timed)',
};

export interface FieldError {
  field: string;
  code: string;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: FieldError[];
  /** Normalised, type-safe configuration. Only present when `valid` is true. */
  value: NormalisedConfiguration | null;
}

export interface NormalisedConfiguration {
  questionCount: number;
  timeLimitMinutes: number | null;
  passMark: number;
  maxAttempts: number;
  deliveryMode: DeliveryMode;
  randomiseQuestions: boolean;
  questionTypes: QuestionTypeSelection[];
  topicIds: string[];
}

export function isQuestionType(value: unknown): value is QuestionType {
  return typeof value === 'string' && (QUESTION_TYPES as readonly string[]).includes(value);
}

export function isDeliveryMode(value: unknown): value is DeliveryMode {
  return typeof value === 'string' && (DELIVERY_MODES as readonly string[]).includes(value);
}

/** `exam` delivery is always time-boxed; a missing limit is a configuration error. */
export function deliveryModeRequiresTimeLimit(mode: DeliveryMode): boolean {
  return mode === 'exam';
}

const INVALID = Symbol('invalid');

/** Accepts the raw strings an HTML form produces as well as real numbers. */
function coerceInteger(value: unknown): number | typeof INVALID {
  if (typeof value === 'number' && Number.isInteger(value) && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value.trim());
    if (Number.isInteger(parsed)) return parsed;
  }
  return INVALID;
}

function isBlank(value: unknown): boolean {
  return value === undefined || value === null || value === '';
}

/** Canonical ordering, so equivalent configurations compare equal. */
export function sortQuestionTypes(types: QuestionTypeSelection[]): QuestionTypeSelection[] {
  const order = new Map(QUESTION_TYPES.map((type, index) => [type, index]));
  return [...types].sort((a, b) => (order.get(a.type) ?? 0) - (order.get(b.type) ?? 0));
}

interface BoundedIntOptions {
  field: string;
  label: string;
  bounds: { readonly min: number; readonly max: number };
  required: boolean;
  unit?: string;
  rangeSuffix?: string;
}

function boundedInt(
  raw: unknown,
  options: BoundedIntOptions,
  push: (field: string, code: string, message: string) => void,
): number | null {
  const { field, label, bounds, required, unit = '', rangeSuffix = '' } = options;

  if (isBlank(raw)) {
    if (required) push(field, 'REQUIRED', `${label} is required.`);
    return null;
  }

  const parsed = coerceInteger(raw);
  if (parsed === INVALID) {
    push(field, 'NOT_AN_INTEGER', `${label} must be a whole number.`);
    return null;
  }

  if (parsed < bounds.min || parsed > bounds.max) {
    push(
      field,
      'OUT_OF_RANGE',
      `${label} must be between ${bounds.min} and ${bounds.max}${unit}${rangeSuffix}.`,
    );
    return null;
  }

  return parsed;
}

/**
 * Validates a configuration payload against the UC-01 product rules.
 *
 * Collects *every* problem rather than failing on the first, so an administrator can fix the whole
 * form in one pass. Question-bank capacity is not covered here — that needs live counts and lives
 * in `evaluateCapacity`.
 */
export function validateQuizConfiguration(input: unknown): ValidationResult {
  if (input === null || typeof input !== 'object' || Array.isArray(input)) {
    return {
      valid: false,
      errors: [
        { field: '_root', code: 'NOT_AN_OBJECT', message: 'A configuration object is required.' },
      ],
      value: null,
    };
  }

  const raw = input as Record<string, unknown>;
  const errors: FieldError[] = [];
  const push = (field: string, code: string, message: string) =>
    errors.push({ field, code, message });

  const questionCount = boundedInt(
    raw.questionCount,
    {
      field: 'questionCount',
      label: 'Question count',
      bounds: CONFIGURATION_LIMITS.questionCount,
      required: true,
    },
    push,
  );

  const timeLimitMinutes = boundedInt(
    raw.timeLimitMinutes,
    {
      field: 'timeLimitMinutes',
      label: 'Time limit',
      bounds: CONFIGURATION_LIMITS.timeLimitMinutes,
      required: false,
      unit: ' minutes',
      rangeSuffix: ', or left empty for no limit',
    },
    push,
  );

  const passMark = boundedInt(
    raw.passMark,
    {
      field: 'passMark',
      label: 'Pass mark',
      bounds: CONFIGURATION_LIMITS.passMark,
      required: true,
      unit: '%',
    },
    push,
  );

  const maxAttempts = boundedInt(
    raw.maxAttempts,
    {
      field: 'maxAttempts',
      label: 'Maximum attempts',
      bounds: CONFIGURATION_LIMITS.maxAttempts,
      required: true,
    },
    push,
  );

  // --- delivery mode --------------------------------------------------------
  let deliveryMode: DeliveryMode | null = null;
  if (isBlank(raw.deliveryMode)) {
    push('deliveryMode', 'REQUIRED', 'Delivery mode is required.');
  } else if (!isDeliveryMode(raw.deliveryMode)) {
    push(
      'deliveryMode',
      'INVALID_VALUE',
      `Delivery mode must be one of: ${DELIVERY_MODES.join(', ')}.`,
    );
  } else {
    deliveryMode = raw.deliveryMode;
  }

  // --- randomisation --------------------------------------------------------
  let randomiseQuestions = false;
  if (typeof raw.randomiseQuestions === 'boolean') {
    randomiseQuestions = raw.randomiseQuestions;
  } else if (raw.randomiseQuestions === 'true' || raw.randomiseQuestions === 'false') {
    randomiseQuestions = raw.randomiseQuestions === 'true';
  } else if (raw.randomiseQuestions !== undefined && raw.randomiseQuestions !== null) {
    push(
      'randomiseQuestions',
      'INVALID_VALUE',
      'Randomisation must be either enabled or disabled.',
    );
  }

  const questionTypes = validateQuestionTypes(raw.questionTypes, questionCount, push);
  const topicIds = validateTopicIds(raw.topicIds, push);

  // --- cross-field rules ----------------------------------------------------
  if (
    deliveryMode !== null &&
    deliveryModeRequiresTimeLimit(deliveryMode) &&
    timeLimitMinutes === null
  ) {
    push(
      'timeLimitMinutes',
      'TIME_LIMIT_REQUIRED',
      'A time limit is required when the delivery mode is "exam".',
    );
  }

  if (errors.length > 0) return { valid: false, errors, value: null };

  return {
    valid: true,
    errors: [],
    value: {
      questionCount: questionCount as number,
      timeLimitMinutes,
      passMark: passMark as number,
      maxAttempts: maxAttempts as number,
      deliveryMode: deliveryMode as DeliveryMode,
      randomiseQuestions,
      questionTypes: sortQuestionTypes(questionTypes),
      topicIds,
    },
  };
}

function validateQuestionTypes(
  rawTypes: unknown,
  questionCount: number | null,
  push: (field: string, code: string, message: string) => void,
): QuestionTypeSelection[] {
  const selections: QuestionTypeSelection[] = [];

  if (!Array.isArray(rawTypes) || rawTypes.length === 0) {
    push('questionTypes', 'NO_QUESTION_TYPE_SELECTED', 'Select at least one question type.');
    return selections;
  }

  const seen = new Set<string>();
  let quotaShapeError = false;

  for (const entry of rawTypes) {
    // Accept both `'SINGLE_CHOICE'` and `{ type: 'SINGLE_CHOICE', quota: 10 }`.
    const rawType = typeof entry === 'string' ? entry : (entry as Record<string, unknown>)?.type;
    const rawQuota =
      typeof entry === 'string' ? null : ((entry as Record<string, unknown>)?.quota ?? null);

    const type = typeof rawType === 'string' ? rawType.trim().toUpperCase() : rawType;
    if (!isQuestionType(type)) {
      push(
        'questionTypes',
        'INVALID_QUESTION_TYPE',
        `"${String(rawType)}" is not a supported question type. Supported types: ${QUESTION_TYPES.join(', ')}.`,
      );
      continue;
    }
    if (seen.has(type)) {
      push(
        'questionTypes',
        'DUPLICATE_QUESTION_TYPE',
        `${QUESTION_TYPE_LABELS[type]} is selected more than once.`,
      );
      continue;
    }
    seen.add(type);

    let quota: number | null = null;
    if (!isBlank(rawQuota)) {
      const field = `questionTypes.${type}.quota`;
      const parsed = coerceInteger(rawQuota);
      if (parsed === INVALID) {
        push(field, 'NOT_AN_INTEGER', 'Question quota must be a whole number.');
        quotaShapeError = true;
        continue;
      }
      if (parsed < CONFIGURATION_LIMITS.questionQuota.min) {
        push(
          field,
          'OUT_OF_RANGE',
          `Quota for ${QUESTION_TYPE_LABELS[type]} must be at least ${CONFIGURATION_LIMITS.questionQuota.min}.`,
        );
        quotaShapeError = true;
        continue;
      }
      quota = parsed;
    }

    selections.push({ type, quota });
  }

  if (quotaShapeError || selections.length === 0) return selections;

  // Quotas are all-or-nothing and, when present, must add up to the question count.
  const withQuota = selections.filter((entry) => entry.quota !== null);
  if (withQuota.length > 0 && withQuota.length !== selections.length) {
    const missing = selections
      .filter((entry) => entry.quota === null)
      .map((entry) => QUESTION_TYPE_LABELS[entry.type])
      .join(', ');
    push(
      'questionTypes',
      'QUOTA_SHAPE',
      `Set a per-type quota for every selected type or none at all. Missing: ${missing}.`,
    );
  } else if (withQuota.length === selections.length && questionCount !== null) {
    const sum = withQuota.reduce((total, entry) => total + (entry.quota ?? 0), 0);
    if (sum !== questionCount) {
      push(
        'questionTypes',
        'QUOTA_SUM_MISMATCH',
        `Per-type quotas add up to ${sum} but the quiz is configured for ${questionCount} questions.`,
      );
    }
  }

  return selections;
}

/** Maximum number of topics a configuration may scope to. Mirrors the backend constant. */
export const MAX_CONFIGURATION_TOPICS = 20;

function validateTopicIds(
  raw: unknown,
  push: (field: string, code: string, message: string) => void,
): string[] {
  if (isBlank(raw)) return [];
  if (!Array.isArray(raw)) {
    push('topicIds', 'INVALID_VALUE', 'Topic scope must be a list of topic ids.');
    return [];
  }

  const ids: string[] = [];
  for (const item of raw) {
    if (typeof item !== 'string' || item.trim() === '') {
      push('topicIds', 'INVALID_VALUE', 'Every topic id must be a non-empty string.');
      continue;
    }
    const value = item.trim();
    if (!ids.includes(value)) ids.push(value);
  }

  if (ids.length > MAX_CONFIGURATION_TOPICS) {
    push(
      'topicIds',
      'TOO_MANY_TOPICS',
      `A configuration may scope to at most ${MAX_CONFIGURATION_TOPICS} topics.`,
    );
    return ids.slice(0, MAX_CONFIGURATION_TOPICS);
  }

  return ids;
}

/**
 * Compares a requested configuration against question-bank availability.
 *
 * Runs the same arithmetic the server runs, so the pre-save warning and the authoritative answer
 * cannot disagree. The availability counts must come from the server (`GET
 * /api/admin/quizzes/{id}/question-bank`), because only it knows which questions are deliverable.
 */
export function evaluateCapacity(
  config: { questionCount: number; questionTypes: QuestionTypeSelection[] },
  availableByType: Partial<Record<string, number>>,
): CapacityReport {
  const usesQuotas =
    config.questionTypes.length > 0 && config.questionTypes.every((t) => t.quota !== null);

  const breakdown = config.questionTypes.map((selection) => {
    const available = availableByType[selection.type] ?? 0;
    const requested = usesQuotas ? (selection.quota as number) : null;
    const shortfall = requested === null ? 0 : Math.max(0, requested - available);
    return { type: selection.type, requested, available, shortfall };
  });

  const availableTotal = breakdown.reduce((sum, entry) => sum + entry.available, 0);
  const messages: string[] = [];
  let satisfiable = true;

  if (usesQuotas) {
    for (const entry of breakdown) {
      if (entry.shortfall > 0) {
        satisfiable = false;
        messages.push(
          `${QUESTION_TYPE_LABELS[entry.type]}: ${entry.requested} requested but only ${entry.available} available in the question bank (${entry.shortfall} short).`,
        );
      }
    }
  } else if (availableTotal < config.questionCount) {
    satisfiable = false;
    messages.push(
      `The quiz requires ${config.questionCount} questions but only ${availableTotal} are available across the selected question types (${config.questionCount - availableTotal} short).`,
    );
  }

  const totalShortfall = usesQuotas
    ? breakdown.reduce((sum, entry) => sum + entry.shortfall, 0)
    : Math.max(0, config.questionCount - availableTotal);

  return {
    satisfiable,
    requestedTotal: config.questionCount,
    availableTotal,
    totalShortfall,
    breakdown,
    messages,
  };
}

/**
 * True when saving `next` over `current` would not be a meaningful change.
 *
 * Only used to tell the administrator there is nothing to save; the server makes the
 * authoritative decision from its own stored fingerprint.
 */
export function isEquivalentConfiguration(
  current: NormalisedConfiguration,
  next: NormalisedConfiguration,
): boolean {
  const canonical = (config: NormalisedConfiguration) =>
    JSON.stringify({
      questionCount: config.questionCount,
      timeLimitMinutes: config.timeLimitMinutes,
      passMark: config.passMark,
      randomiseQuestions: config.randomiseQuestions,
      maxAttempts: config.maxAttempts,
      deliveryMode: config.deliveryMode,
      questionTypes: sortQuestionTypes(config.questionTypes).map((t) => [t.type, t.quota]),
      topicIds: [...config.topicIds].sort(),
    });
  return canonical(current) === canonical(next);
}

/** Build a form payload from an existing version, for editing. */
export function toFormInput(source: {
  questionCount: number;
  timeLimitMinutes: number | null;
  passMark: number;
  maxAttempts: number;
  deliveryMode: DeliveryMode;
  randomiseQuestions: boolean;
  questionTypes: QuestionTypeSelection[];
  topics?: { id: string }[];
}): QuizConfigurationInput {
  return {
    questionCount: source.questionCount,
    timeLimitMinutes: source.timeLimitMinutes,
    passMark: source.passMark,
    maxAttempts: source.maxAttempts,
    deliveryMode: source.deliveryMode,
    randomiseQuestions: source.randomiseQuestions,
    questionTypes: source.questionTypes.map((entry) => ({ ...entry })),
    topicIds: (source.topics ?? []).map((topic) => topic.id),
  };
}
