import { describe, expect, it } from 'vitest';

import type {
  AnswerResponse,
  AttemptQuestion,
  AttemptSubQuestion,
  SubAnswerResponse,
} from '../api/attemptTypes';
import {
  acceptDeliveredOrder,
  booleanValue,
  looksComplete,
  moveItem,
  orderedItemIds,
  sameResponse,
  selectedOptionIds,
  subResponse,
  toggleOption,
  withBoolean,
  withSelectedOption,
  withSubResponse,
} from './attemptAnswers';

/**
 * A sub-answer, narrowed.
 *
 * `withSelectedOption` and `withBoolean` return the wider `AnswerResponse` because they also build
 * top-level answers; a scenario sub-answer can never be another scenario, and the types say so.
 */
function sub(answer: AnswerResponse): SubAnswerResponse {
  return answer as SubAnswerResponse;
}

function question(overrides: Partial<AttemptQuestion>): AttemptQuestion {
  return {
    questionId: 'q1',
    position: 1,
    questionType: 'SINGLE_CHOICE',
    questionVersion: 1,
    points: 1,
    prompt: 'A question',
    ...overrides,
  };
}

const SUBS: AttemptSubQuestion[] = [
  {
    subQuestionId: 's1',
    type: 'SINGLE_CHOICE',
    prompt: 'First action?',
    options: [
      { optionId: 's1-a', text: 'Raise the alarm' },
      { optionId: 's1-b', text: 'Investigate' },
    ],
  },
  { subQuestionId: 's2', type: 'TRUE_FALSE', prompt: 'Water is appropriate.' },
  {
    subQuestionId: 's3',
    type: 'MULTI_SELECT',
    prompt: 'Who is informed?',
    options: [
      { optionId: 's3-a', text: 'Fire warden' },
      { optionId: 's3-b', text: 'IT on-call' },
    ],
    minSelections: 1,
  },
];

describe('multi-select toggling', () => {
  it('adds and removes options', () => {
    let response = toggleOption(null, 'a');
    expect(selectedOptionIds(response)).toEqual(['a']);
    response = toggleOption(response, 'b');
    expect(selectedOptionIds(response)).toEqual(['a', 'b']);
    response = toggleOption(response, 'a');
    expect(selectedOptionIds(response)).toEqual(['b']);
  });

  it('collapses to unanswered when the last option is removed', () => {
    // Not `{selectedOptionIds: []}`: unticking the last box returns the question to unanswered,
    // rather than asserting "I deliberately select nothing".
    const response = toggleOption(toggleOption(null, 'a'), 'a');
    expect(response).toBeNull();
  });
});

describe('change detection', () => {
  it('treats a reordered multi-select as unchanged', () => {
    // The backend canonicalises selection order, so re-ticking the same boxes in a different order
    // must not count as a change — otherwise every autosave burns a revision.
    expect(sameResponse({ selectedOptionIds: ['a', 'b'] }, { selectedOptionIds: ['b', 'a'] })).toBe(true);
  });

  it('treats a different selection as changed', () => {
    expect(sameResponse({ selectedOptionIds: ['a', 'b'] }, { selectedOptionIds: ['a'] })).toBe(false);
  });

  it('distinguishes unanswered from answered', () => {
    expect(sameResponse(null, { value: false })).toBe(false);
    expect(sameResponse(null, null)).toBe(true);
  });

  it('compares booleans by value, not by truthiness', () => {
    expect(sameResponse({ value: false }, { value: true })).toBe(false);
    expect(sameResponse(withBoolean(true), { value: true })).toBe(true);
  });

  it('treats a reordered drag-to-order as changed', () => {
    // Order *is* the answer here, unlike multi-select.
    expect(sameResponse({ orderedItemIds: ['a', 'b'] }, { orderedItemIds: ['b', 'a'] })).toBe(false);
  });

  it('ignores the order sub-answers happen to be listed in', () => {
    const forwards = withSubResponse(null, SUBS, 's1', sub(withSelectedOption('s1-a')));
    const withSecond = withSubResponse(forwards, SUBS, 's2', sub(withBoolean(true)));
    const backwards = withSubResponse(
      withSubResponse(null, SUBS, 's2', sub(withBoolean(true))),
      SUBS,
      's1',
      sub(withSelectedOption('s1-a')),
    );
    expect(sameResponse(withSecond, backwards)).toBe(true);
  });
});

describe('drag to order', () => {
  const items = ['i1', 'i2', 'i3'];

  it('starts from the delivered order so the first move is a real edit', () => {
    const moved = moveItem(null, items, 'i3', -1);
    expect(orderedItemIds(moved)).toEqual(['i1', 'i3', 'i2']);
  });

  it('refuses to move past either end', () => {
    const start = acceptDeliveredOrder(items);
    expect(orderedItemIds(moveItem(start, items, 'i1', -1))).toEqual(items);
    expect(orderedItemIds(moveItem(start, items, 'i3', 1))).toEqual(items);
  });

  it('ignores an unknown item', () => {
    const start = acceptDeliveredOrder(items);
    expect(moveItem(start, items, 'nope', 1)).toBe(start);
  });
});

describe('scenario sub-answers', () => {
  it('reads back the sub-answer it stored', () => {
    const response = withSubResponse(null, SUBS, 's2', sub(withBoolean(false)));
    expect(booleanValue(subResponse(response, 's2'))).toBe(false);
    expect(subResponse(response, 's1')).toBeNull();
  });

  it('collapses to unanswered when the last sub-answer is cleared', () => {
    const response = withSubResponse(null, SUBS, 's2', sub(withBoolean(false)));
    expect(withSubResponse(response, SUBS, 's2', null)).toBeNull();
  });
});

describe('local completeness', () => {
  it('requires a selection for single choice', () => {
    const q = question({ options: [{ optionId: 'a', text: 'A' }] });
    expect(looksComplete(q, null)).toBe(false);
    expect(looksComplete(q, withSelectedOption('a'))).toBe(true);
  });

  it('treats false as an answer for true/false', () => {
    // The bug this guards: `if (!response.value)` would call a deliberate "false" unanswered.
    const q = question({ questionType: 'TRUE_FALSE' });
    expect(looksComplete(q, withBoolean(false))).toBe(true);
  });

  it('honours the declared minimum for multi-select', () => {
    const q = question({
      questionType: 'MULTI_SELECT',
      options: [
        { optionId: 'a', text: 'A' },
        { optionId: 'b', text: 'B' },
      ],
      minSelections: 2,
    });
    expect(looksComplete(q, { selectedOptionIds: ['a'] })).toBe(false);
    expect(looksComplete(q, { selectedOptionIds: ['a', 'b'] })).toBe(true);
  });

  it('requires every item to be placed for drag to order', () => {
    const q = question({
      questionType: 'DRAG_TO_ORDER',
      orderItems: [
        { itemId: 'i1', text: 'One' },
        { itemId: 'i2', text: 'Two' },
      ],
    });
    expect(looksComplete(q, { orderedItemIds: ['i1'] })).toBe(false);
    expect(looksComplete(q, { orderedItemIds: ['i1', 'i2'] })).toBe(true);
  });

  it('requires every sub-question for a scenario', () => {
    const q = question({ questionType: 'SCENARIO', subQuestions: SUBS });
    let response = withSubResponse(null, SUBS, 's1', sub(withSelectedOption('s1-a')));
    expect(looksComplete(q, response)).toBe(false);
    response = withSubResponse(response, SUBS, 's2', sub(withBoolean(true)));
    expect(looksComplete(q, response)).toBe(false);
    response = withSubResponse(response, SUBS, 's3', { selectedOptionIds: ['s3-a'] });
    expect(looksComplete(q, response)).toBe(true);
  });
});
