/**
 * Unit tests for the configuration-rules mirror.
 *
 * These check the mirror behaves like the backend it mirrors. The *messages and limits* are pinned
 * to the Python source separately, by `backend/tests/quiz_configuration/test_frontend_contract_sync.py` —
 * that test fails if this file and the backend drift apart.
 */

import { describe, expect, it } from 'vitest';

import {
  CONFIGURATION_LIMITS,
  evaluateCapacity,
  isEquivalentConfiguration,
  sortQuestionTypes,
  toFormInput,
  validateQuizConfiguration,
} from './configurationRules';

function valid(overrides: Record<string, unknown> = {}) {
  return {
    questionCount: 10,
    timeLimitMinutes: 30,
    passMark: 60,
    maxAttempts: 3,
    deliveryMode: 'assessment',
    randomiseQuestions: false,
    questionTypes: [
      { type: 'SINGLE_CHOICE', quota: null },
      { type: 'TRUE_FALSE', quota: null },
    ],
    ...overrides,
  };
}

function fieldsOf(result: ReturnType<typeof validateQuizConfiguration>): string[] {
  return [...new Set(result.errors.map((error) => error.field))].sort();
}

describe('validateQuizConfiguration', () => {
  it('accepts a valid configuration and normalises form strings', () => {
    const result = validateQuizConfiguration(
      valid({ questionCount: '10', passMark: '70', maxAttempts: '2', randomiseQuestions: 'true' }),
    );
    expect(result.valid).toBe(true);
    expect(result.value).toMatchObject({
      questionCount: 10,
      passMark: 70,
      maxAttempts: 2,
      randomiseQuestions: true,
      deliveryMode: 'assessment',
    });
  });

  it('puts question types in canonical order regardless of input order', () => {
    const result = validateQuizConfiguration(
      valid({
        questionTypes: [
          { type: 'DRAG_TO_ORDER', quota: null },
          { type: 'SINGLE_CHOICE', quota: null },
        ],
      }),
    );
    expect(result.value?.questionTypes.map((entry) => entry.type)).toEqual([
      'SINGLE_CHOICE',
      'DRAG_TO_ORDER',
    ]);
  });

  it('accepts all five question types', () => {
    const result = validateQuizConfiguration(
      valid({
        questionCount: 5,
        questionTypes: [
          { type: 'SINGLE_CHOICE', quota: 1 },
          { type: 'TRUE_FALSE', quota: 1 },
          { type: 'MULTI_SELECT', quota: 1 },
          { type: 'SCENARIO', quota: 1 },
          { type: 'DRAG_TO_ORDER', quota: 1 },
        ],
      }),
    );
    expect(result.valid).toBe(true);
    expect(result.value?.questionTypes).toHaveLength(5);
  });

  it('treats a blank time limit as "no limit"', () => {
    expect(validateQuizConfiguration(valid({ timeLimitMinutes: '' })).value?.timeLimitMinutes).toBe(
      null,
    );
  });

  it('requires a time limit for exam delivery', () => {
    const result = validateQuizConfiguration(
      valid({ deliveryMode: 'exam', timeLimitMinutes: null }),
    );
    expect(result.valid).toBe(false);
    expect(result.errors[0].code).toBe('TIME_LIMIT_REQUIRED');
  });

  it.each([
    ['question count of 0', { questionCount: 0 }, 'questionCount'],
    ['question count above maximum', { questionCount: 500 }, 'questionCount'],
    ['fractional question count', { questionCount: 10.5 }, 'questionCount'],
    ['pass mark of 0', { passMark: 0 }, 'passMark'],
    ['pass mark above 100', { passMark: 101 }, 'passMark'],
    ['maximum attempts of 0', { maxAttempts: 0 }, 'maxAttempts'],
    ['time limit of 0', { timeLimitMinutes: 0 }, 'timeLimitMinutes'],
    ['unsupported delivery mode', { deliveryMode: 'telepathy' }, 'deliveryMode'],
    ['no question types', { questionTypes: [] }, 'questionTypes'],
    ['unsupported question type', { questionTypes: [{ type: 'essay', quota: null }] }, 'questionTypes'],
  ])('rejects %s', (_label, override, expectedField) => {
    const result = validateQuizConfiguration(valid(override));
    expect(result.valid).toBe(false);
    expect(fieldsOf(result)).toContain(expectedField);
  });

  it('rejects the pre-merge question-type vocabulary', () => {
    for (const legacy of ['mcq', 'short_answer']) {
      const result = validateQuizConfiguration(valid({ questionTypes: [{ type: legacy, quota: null }] }));
      expect(result.valid).toBe(false);
      expect(result.errors.some((error) => error.code === 'INVALID_QUESTION_TYPE')).toBe(true);
    }
  });

  it('requires quotas to be all-or-nothing', () => {
    const result = validateQuizConfiguration(
      valid({
        questionTypes: [
          { type: 'SINGLE_CHOICE', quota: 10 },
          { type: 'TRUE_FALSE', quota: null },
        ],
      }),
    );
    expect(result.errors[0].code).toBe('QUOTA_SHAPE');
  });

  it('requires quotas to add up to the question count', () => {
    const result = validateQuizConfiguration(
      valid({
        questionCount: 20,
        questionTypes: [
          { type: 'SINGLE_CHOICE', quota: 10 },
          { type: 'TRUE_FALSE', quota: 5 },
        ],
      }),
    );
    expect(result.errors[0].code).toBe('QUOTA_SUM_MISMATCH');
    expect(result.errors[0].message).toContain('add up to 15');
  });

  it('rejects a duplicated question type', () => {
    const result = validateQuizConfiguration(
      valid({ questionTypes: [{ type: 'SINGLE_CHOICE', quota: null }, 'SINGLE_CHOICE'] }),
    );
    expect(result.errors[0].code).toBe('DUPLICATE_QUESTION_TYPE');
  });

  it('collects every problem in one pass', () => {
    const result = validateQuizConfiguration({
      questionCount: 0,
      passMark: 400,
      maxAttempts: 0,
      deliveryMode: 'nope',
      questionTypes: [],
      timeLimitMinutes: 30,
      randomiseQuestions: false,
    });
    expect(fieldsOf(result)).toEqual([
      'deliveryMode',
      'maxAttempts',
      'passMark',
      'questionCount',
      'questionTypes',
    ]);
  });

  it('rejects a non-object payload', () => {
    expect(validateQuizConfiguration('nope').errors[0].field).toBe('_root');
  });

  it('deduplicates the optional topic scope', () => {
    const result = validateQuizConfiguration(valid({ topicIds: ['a', 'b', 'a'] }));
    expect(result.value?.topicIds).toEqual(['a', 'b']);
  });

  it('states the exact pass-mark bounds', () => {
    const result = validateQuizConfiguration(valid({ passMark: 101 }));
    expect(result.errors[0].message).toBe(
      `Pass mark must be between ${CONFIGURATION_LIMITS.passMark.min} and ${CONFIGURATION_LIMITS.passMark.max}%.`,
    );
  });
});

describe('evaluateCapacity', () => {
  it('reports a per-type shortfall when quotas are used', () => {
    const report = evaluateCapacity(
      {
        questionCount: 20,
        questionTypes: [
          { type: 'SINGLE_CHOICE', quota: 10 },
          { type: 'TRUE_FALSE', quota: 10 },
        ],
      },
      { SINGLE_CHOICE: 5, TRUE_FALSE: 10 },
    );
    expect(report.satisfiable).toBe(false);
    expect(report.breakdown[0]).toEqual({
      type: 'SINGLE_CHOICE',
      requested: 10,
      available: 5,
      shortfall: 5,
    });
    expect(report.totalShortfall).toBe(5);
    expect(report.messages[0]).toContain('Single choice');
  });

  it('reports a total shortfall when no quotas are set', () => {
    const report = evaluateCapacity(
      {
        questionCount: 15,
        questionTypes: [
          { type: 'SINGLE_CHOICE', quota: null },
          { type: 'TRUE_FALSE', quota: null },
        ],
      },
      { SINGLE_CHOICE: 4, TRUE_FALSE: 4, MULTI_SELECT: 100 },
    );
    // The unselected type is irrelevant.
    expect(report.availableTotal).toBe(8);
    expect(report.totalShortfall).toBe(7);
    expect(report.satisfiable).toBe(false);
  });

  it('is satisfiable when the bank covers the request', () => {
    const report = evaluateCapacity(
      { questionCount: 4, questionTypes: [{ type: 'SCENARIO', quota: 4 }] },
      { SCENARIO: 9 },
    );
    expect(report.satisfiable).toBe(true);
    expect(report.messages).toHaveLength(0);
  });

  it('treats a missing type as zero available rather than unconstrained', () => {
    const report = evaluateCapacity(
      { questionCount: 1, questionTypes: [{ type: 'DRAG_TO_ORDER', quota: 1 }] },
      {},
    );
    expect(report.satisfiable).toBe(false);
    expect(report.breakdown[0].available).toBe(0);
  });
});

describe('isEquivalentConfiguration', () => {
  const base = validateQuizConfiguration(valid()).value!;

  it('ignores question-type ordering', () => {
    const reordered = {
      ...base,
      questionTypes: [...base.questionTypes].reverse(),
    };
    expect(isEquivalentConfiguration(base, reordered)).toBe(true);
  });

  it('ignores topic ordering', () => {
    expect(
      isEquivalentConfiguration({ ...base, topicIds: ['a', 'b'] }, { ...base, topicIds: ['b', 'a'] }),
    ).toBe(true);
  });

  it('detects a real change', () => {
    expect(isEquivalentConfiguration(base, { ...base, passMark: base.passMark + 1 })).toBe(false);
  });
});

describe('helpers', () => {
  it('sortQuestionTypes does not mutate its input', () => {
    const input = [
      { type: 'TRUE_FALSE' as const, quota: null },
      { type: 'SINGLE_CHOICE' as const, quota: null },
    ];
    const sorted = sortQuestionTypes(input);
    expect(input[0].type).toBe('TRUE_FALSE');
    expect(sorted[0].type).toBe('SINGLE_CHOICE');
  });

  it('toFormInput round-trips a stored version into an editable payload', () => {
    const form = toFormInput({
      questionCount: 12,
      timeLimitMinutes: null,
      passMark: 80,
      maxAttempts: 2,
      deliveryMode: 'practice',
      randomiseQuestions: true,
      questionTypes: [{ type: 'MULTI_SELECT', quota: 12 }],
      topics: [{ id: 'topic-1' }],
    });
    expect(form).toEqual({
      questionCount: 12,
      timeLimitMinutes: null,
      passMark: 80,
      maxAttempts: 2,
      deliveryMode: 'practice',
      randomiseQuestions: true,
      questionTypes: [{ type: 'MULTI_SELECT', quota: 12 }],
      topicIds: ['topic-1'],
    });
    expect(validateQuizConfiguration(form).valid).toBe(true);
  });
});
