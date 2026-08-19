/**
 * Unit tests for the form <-> payload mapping.
 *
 * The type-specific shaping is where a five-type editor is most likely to go wrong — especially
 * the drag-to-order distinction between presentation order and correct answer order — so it is
 * tested directly rather than only through the UI.
 */

import { describe, expect, it } from 'vitest';

import type { Question } from '../api/types';
import {
  addOption,
  changeType,
  describeClientIssues,
  emptyForm,
  fromQuestion,
  moveOption,
  selectSingleCorrect,
  toPayload,
  toggleCorrect,
  updateOption,
} from './questionForm';

describe('emptyForm', () => {
  it('starts single choice with exactly four options', () => {
    expect(emptyForm('SINGLE_CHOICE').options).toHaveLength(4);
  });

  it('starts true/false with the fixed TRUE/FALSE pair', () => {
    const form = emptyForm('TRUE_FALSE');
    expect(form.options.map((option) => option.label)).toEqual(['TRUE', 'FALSE']);
    expect(form.options.every((option) => !option.isCorrect)).toBe(true);
  });
});

describe('changeType', () => {
  it('replaces the option set when switching to true/false', () => {
    const form = changeType(emptyForm('SINGLE_CHOICE'), 'TRUE_FALSE');
    expect(form.options.map((option) => option.label)).toEqual(['TRUE', 'FALSE']);
  });

  it('pads back to four options when switching to single choice', () => {
    let form = emptyForm('MULTI_SELECT');
    form = addOption(addOption(form)); // 6 options
    form = changeType(form, 'SINGLE_CHOICE');
    expect(form.options).toHaveLength(4);
  });

  it('keeps at most one correct answer when leaving multi-select', () => {
    let form = emptyForm('MULTI_SELECT');
    form = toggleCorrect(form, form.options[0]!.key);
    form = toggleCorrect(form, form.options[1]!.key);
    form = changeType(form, 'SINGLE_CHOICE');
    expect(form.options.filter((option) => option.isCorrect)).toHaveLength(1);
  });

  it('resets a scoring strategy the new type disallows', () => {
    let form = emptyForm('MULTI_SELECT');
    form = { ...form, scoringStrategy: 'PARTIAL_CREDIT_WITH_PENALTY', penaltyPerIncorrect: '0.5' };
    form = changeType(form, 'SINGLE_CHOICE');
    expect(form.scoringStrategy).toBe('ALL_OR_NOTHING');
    expect(form.penaltyPerIncorrect).toBe('');
  });

  it('clears scenario text when leaving the scenario type', () => {
    let form = emptyForm('SCENARIO');
    form = { ...form, scenarioText: 'A long vignette describing the situation in detail.' };
    expect(changeType(form, 'SINGLE_CHOICE').scenarioText).toBe('');
  });

  it('clears correctness flags when switching to drag-to-order', () => {
    let form = emptyForm('SINGLE_CHOICE');
    form = selectSingleCorrect(form, form.options[1]!.key);
    form = changeType(form, 'DRAG_TO_ORDER');
    expect(form.options.every((option) => !option.isCorrect)).toBe(true);
  });
});

describe('toPayload', () => {
  it('sends exactly one correct label for single choice', () => {
    let form = emptyForm('SINGLE_CHOICE');
    form = { ...form, questionText: 'Q', explanation: 'E', topics: ['T'] };
    form.options.forEach((option, index) => {
      form = updateOption(form, option.key, { text: `Option ${index}` });
    });
    form = selectSingleCorrect(form, form.options[2]!.key);

    const payload = toPayload(form);
    const correct = payload.options.filter((option) => option.isCorrect);
    expect(correct).toHaveLength(1);
    expect(correct[0]!.label).toBe('C');
    expect(payload.options.every((option) => option.correctPosition === null)).toBe(true);
  });

  it('derives correctPosition from list order for drag-to-order', () => {
    let form = emptyForm('DRAG_TO_ORDER');
    ['First', 'Second', 'Third', 'Fourth'].forEach((text, index) => {
      form = updateOption(form, form.options[index]!.key, { text });
    });

    const payload = toPayload(form);
    expect(payload.options.map((option) => option.correctPosition)).toEqual([1, 2, 3, 4]);
    // Ordering questions carry no isCorrect flags.
    expect(payload.options.every((option) => option.isCorrect === false)).toBe(true);
  });

  it('reordering the list changes the answer key, not just the display', () => {
    let form = emptyForm('DRAG_TO_ORDER');
    ['First', 'Second', 'Third', 'Fourth'].forEach((text, index) => {
      form = updateOption(form, form.options[index]!.key, { text });
    });

    const before = toPayload(form);
    expect(
      before.options.map((option) => [option.label, option.correctPosition]),
    ).toEqual([
      ['A', 1],
      ['B', 2],
      ['C', 3],
      ['D', 4],
    ]);

    // Drag the last item to the front.
    const after = toPayload(moveOption(form, 3, 0));
    const byLabel = new Map(after.options.map((option) => [option.label, option.correctPosition]));
    expect(byLabel.get('D')).toBe(1);
    expect(byLabel.get('A')).toBe(2);
    // The correct positions remain a complete 1..n sequence.
    expect([...byLabel.values()].sort()).toEqual([1, 2, 3, 4]);
  });

  it('marks the primary answer for a scenario question', () => {
    let form = emptyForm('SCENARIO');
    form = selectSingleCorrect(form, form.options[1]!.key);
    const payload = toPayload(form);
    const primary = payload.options.filter((option) => option.isPrimary);
    expect(primary).toHaveLength(1);
    expect(primary[0]!.isCorrect).toBe(true);
  });

  it('omits the penalty unless the strategy uses one', () => {
    let form = emptyForm('MULTI_SELECT');
    form = { ...form, scoringStrategy: 'PARTIAL_CREDIT', penaltyPerIncorrect: '0.5' };
    expect(toPayload(form).scoring.penaltyPerIncorrect).toBe(0);

    form = { ...form, scoringStrategy: 'PARTIAL_CREDIT_WITH_PENALTY' };
    expect(toPayload(form).scoring.penaltyPerIncorrect).toBe(0.5);
  });

  it('nulls scenarioText for non-scenario types', () => {
    const form = { ...emptyForm('SINGLE_CHOICE'), scenarioText: 'leftover text' };
    expect(toPayload(form).scenarioText).toBeNull();
  });
});

describe('fromQuestion', () => {
  const base: Question = {
    id: 'q1',
    reference: 'Q-000001',
    seq: 1,
    externalRef: null,
    type: 'DRAG_TO_ORDER',
    status: 'ACTIVE',
    questionText: 'Order the steps.',
    scenarioText: null,
    explanation: 'Because.',
    difficulty: 'HARD',
    scoring: { points: 4, scoringStrategy: 'PARTIAL_CREDIT', penaltyPerIncorrect: 0 },
    version: 1,
    contentHash: 'hash',
    options: [
      // Stored with a presentation order that differs from the correct order.
      { id: 'o1', label: 'D', text: 'Fourth', position: 1, isCorrect: false, isPrimary: false, correctPosition: 4, feedback: null },
      { id: 'o2', label: 'A', text: 'First', position: 2, isCorrect: false, isPrimary: false, correctPosition: 1, feedback: null },
      { id: 'o3', label: 'C', text: 'Third', position: 3, isCorrect: false, isPrimary: false, correctPosition: 3, feedback: null },
      { id: 'o4', label: 'B', text: 'Second', position: 4, isCorrect: false, isPrimary: false, correctPosition: 2, feedback: null },
    ],
    topics: [{ id: 't1', slug: 'process', name: 'Process' }],
    correctLabels: [],
    correctOrder: ['A', 'B', 'C', 'D'],
    primaryLabel: null,
    retiredAt: null,
    retiredReason: null,
    retiredBy: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    createdBy: null,
    updatedBy: null,
    importId: null,
    importRowNumber: null,
    isDeliverable: true,
    usage: null,
  };

  it('loads ordering items in correct-answer order, not presentation order', () => {
    const form = fromQuestion(base);
    expect(form.options.map((option) => option.label)).toEqual(['A', 'B', 'C', 'D']);
  });

  it('round-trips an ordering question without changing the answer key', () => {
    const payload = toPayload(fromQuestion(base));
    const byLabel = new Map(payload.options.map((option) => [option.label, option.correctPosition]));
    expect(byLabel.get('A')).toBe(1);
    expect(byLabel.get('B')).toBe(2);
    expect(byLabel.get('C')).toBe(3);
    expect(byLabel.get('D')).toBe(4);
  });

  it('loads choice options in presentation order', () => {
    const choice: Question = {
      ...base,
      type: 'SINGLE_CHOICE',
      correctOrder: [],
      correctLabels: ['A'],
      options: base.options.map((option) => ({
        ...option,
        correctPosition: null,
        isCorrect: option.label === 'A',
      })),
    };
    expect(fromQuestion(choice).options.map((option) => option.label)).toEqual([
      'D',
      'A',
      'C',
      'B',
    ]);
  });
});

describe('describeClientIssues', () => {
  it('reports the obvious omissions on a blank form', () => {
    const issues = describeClientIssues(emptyForm('SINGLE_CHOICE'));
    expect(issues).toContain('Question text is required.');
    expect(issues).toContain('An explanation is required.');
    expect(issues).toContain('Assign at least one topic.');
    expect(issues).toContain('Select exactly one correct answer.');
  });

  it('accepts a complete single-choice question', () => {
    let form = emptyForm('SINGLE_CHOICE');
    form = { ...form, questionText: 'Q?', explanation: 'Because.', topics: ['Networking'] };
    form.options.forEach((option, index) => {
      form = updateOption(form, option.key, { text: `Option ${index + 1}` });
    });
    form = selectSingleCorrect(form, form.options[0]!.key);
    expect(describeClientIssues(form)).toEqual([]);
  });

  it('flags duplicate ordered items', () => {
    let form = emptyForm('DRAG_TO_ORDER');
    form = { ...form, questionText: 'Q?', explanation: 'E', topics: ['T'], points: '4' };
    form = updateOption(form, form.options[0]!.key, { text: 'Same' });
    form = updateOption(form, form.options[1]!.key, { text: 'Same' });
    form = updateOption(form, form.options[2]!.key, { text: 'Other' });
    form = updateOption(form, form.options[3]!.key, { text: 'Another' });
    expect(describeClientIssues(form)).toContain('Ordered items must be unique.');
  });

  it('flags a multi-select where every option is correct', () => {
    let form = emptyForm('MULTI_SELECT');
    form = { ...form, questionText: 'Q?', explanation: 'E', topics: ['T'] };
    form.options.forEach((option, index) => {
      form = updateOption(form, option.key, { text: `Option ${index}` });
      form = toggleCorrect(form, option.key);
    });
    expect(describeClientIssues(form)).toContain('At least one option must be a distractor.');
  });
});
