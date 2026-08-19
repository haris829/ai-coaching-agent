/**
 * Form state for the question editor, and the mapping to/from the API payload.
 *
 * Pure functions with no React dependency, so the type-specific shaping rules — which are the
 * fiddly part of a five-type editor — are unit-testable on their own.
 *
 * The frontend rules here exist for UX only. The backend re-validates everything and remains
 * authoritative; `describeClientIssues` deliberately mirrors a subset of the server rules so the
 * admin gets fast feedback, never so the server can be bypassed.
 */

import {
  ALLOWED_SCORING_STRATEGIES,
  type Difficulty,
  type OptionPayload,
  type Question,
  type QuestionPayload,
  type QuestionType,
  type ScoringStrategy,
} from '../api/types';

export interface OptionForm {
  /** Local key for React lists; never sent to the API. */
  key: string;
  label: string;
  text: string;
  isCorrect: boolean;
  isPrimary: boolean;
  feedback: string;
}

export interface QuestionForm {
  type: QuestionType;
  questionText: string;
  scenarioText: string;
  explanation: string;
  difficulty: '' | Difficulty;
  status: 'DRAFT' | 'ACTIVE';
  externalRef: string;
  topics: string[];
  points: string;
  scoringStrategy: ScoringStrategy;
  penaltyPerIncorrect: string;
  /**
   * Choice types: the option set, in presentation order.
   * DRAG_TO_ORDER: the items, held in CORRECT ANSWER ORDER — the editor reorders this array and
   * `correctPosition` is derived from the index. Presentation order is a delivery concern.
   */
  options: OptionForm[];
}

let keyCounter = 0;
export function nextKey(): string {
  keyCounter += 1;
  return `opt-${keyCounter}`;
}

export const DEFAULT_LABELS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'] as const;

export function blankOption(index: number): OptionForm {
  return {
    key: nextKey(),
    label: DEFAULT_LABELS[index] ?? `O${index + 1}`,
    text: '',
    isCorrect: false,
    isPrimary: false,
    feedback: '',
  };
}

function trueFalseOptions(): OptionForm[] {
  return [
    { key: nextKey(), label: 'TRUE', text: 'True', isCorrect: false, isPrimary: false, feedback: '' },
    { key: nextKey(), label: 'FALSE', text: 'False', isCorrect: false, isPrimary: false, feedback: '' },
  ];
}

/** How many blank options a freshly chosen type should start with. */
export function defaultOptionCount(type: QuestionType): number {
  switch (type) {
    case 'SINGLE_CHOICE':
      return 4;
    case 'TRUE_FALSE':
      return 2;
    case 'MULTI_SELECT':
      return 4;
    case 'SCENARIO':
      return 4;
    case 'DRAG_TO_ORDER':
      return 4;
  }
}

export function emptyForm(type: QuestionType = 'SINGLE_CHOICE'): QuestionForm {
  return {
    type,
    questionText: '',
    scenarioText: '',
    explanation: '',
    difficulty: '',
    status: 'ACTIVE',
    externalRef: '',
    topics: [],
    points: '1',
    scoringStrategy: 'ALL_OR_NOTHING',
    penaltyPerIncorrect: '',
    options:
      type === 'TRUE_FALSE'
        ? trueFalseOptions()
        : Array.from({ length: defaultOptionCount(type) }, (_, index) => blankOption(index)),
  };
}

/**
 * Re-shape the form when the admin changes the question type.
 *
 * Keeps the text the admin has already typed, but resets whatever cannot survive the change
 * (True/False's fixed pair, an option count the new type forbids, a now-illegal strategy).
 */
export function changeType(form: QuestionForm, type: QuestionType): QuestionForm {
  const allowed = ALLOWED_SCORING_STRATEGIES[type];
  const scoringStrategy = allowed.includes(form.scoringStrategy) ? form.scoringStrategy : allowed[0]!;

  let options = form.options;
  if (type === 'TRUE_FALSE') {
    options = trueFalseOptions();
  } else if (form.type === 'TRUE_FALSE') {
    options = Array.from({ length: defaultOptionCount(type) }, (_, index) => blankOption(index));
  } else if (type === 'SINGLE_CHOICE' && options.length !== 4) {
    // Single choice must be exactly four: trim extras, pad shortfalls.
    options = options
      .slice(0, 4)
      .concat(Array.from({ length: Math.max(0, 4 - options.length) }, (_, i) => blankOption(options.length + i)));
  }

  if (type === 'SINGLE_CHOICE' || type === 'SCENARIO') {
    // At most one correct answer survives the change.
    let seen = false;
    options = options.map((option) => {
      const keep = option.isCorrect && !seen;
      if (keep) seen = true;
      return { ...option, isCorrect: keep, isPrimary: keep && type === 'SCENARIO' };
    });
  }
  if (type === 'DRAG_TO_ORDER') {
    options = options.map((option) => ({ ...option, isCorrect: false, isPrimary: false }));
  }

  return {
    ...form,
    type,
    options,
    scoringStrategy,
    penaltyPerIncorrect:
      scoringStrategy === 'PARTIAL_CREDIT_WITH_PENALTY' ? form.penaltyPerIncorrect : '',
    scenarioText: type === 'SCENARIO' ? form.scenarioText : '',
  };
}

/** Mark exactly one option correct (single-answer types). */
export function selectSingleCorrect(form: QuestionForm, key: string): QuestionForm {
  return {
    ...form,
    options: form.options.map((option) => ({
      ...option,
      isCorrect: option.key === key,
      isPrimary: form.type === 'SCENARIO' ? option.key === key : option.isPrimary,
    })),
  };
}

/** Toggle one option's correctness (multi-select). */
export function toggleCorrect(form: QuestionForm, key: string): QuestionForm {
  return {
    ...form,
    options: form.options.map((option) =>
      option.key === key ? { ...option, isCorrect: !option.isCorrect } : option,
    ),
  };
}

/** Move an item within the correct-order list (drag-to-order). */
export function moveOption(form: QuestionForm, from: number, to: number): QuestionForm {
  if (from === to || from < 0 || to < 0 || from >= form.options.length || to >= form.options.length) {
    return form;
  }
  const options = [...form.options];
  const [moved] = options.splice(from, 1);
  options.splice(to, 0, moved!);
  return { ...form, options };
}

export function addOption(form: QuestionForm): QuestionForm {
  return { ...form, options: [...form.options, blankOption(form.options.length)] };
}

export function removeOption(form: QuestionForm, key: string): QuestionForm {
  return { ...form, options: form.options.filter((option) => option.key !== key) };
}

export function updateOption(form: QuestionForm, key: string, patch: Partial<OptionForm>): QuestionForm {
  return {
    ...form,
    options: form.options.map((option) => (option.key === key ? { ...option, ...patch } : option)),
  };
}

// ---------------------------------------------------------------------------
// Form <-> API
// ---------------------------------------------------------------------------

export function toPayload(form: QuestionForm): QuestionPayload {
  const isOrdering = form.type === 'DRAG_TO_ORDER';

  const options: OptionPayload[] = form.options.map((option, index) => {
    const base: OptionPayload = {
      label: option.label.trim(),
      text: option.text.trim(),
      position: index + 1,
      feedback: option.feedback.trim() || null,
    };
    if (isOrdering) {
      // The list is held in correct-answer order, so the index IS the answer key. `position`
      // above is only the default presentation order.
      return { ...base, correctPosition: index + 1, isCorrect: false, isPrimary: false };
    }
    return {
      ...base,
      isCorrect: option.isCorrect,
      isPrimary: form.type === 'SCENARIO' ? option.isPrimary : false,
      correctPosition: null,
    };
  });

  const points = Number.parseFloat(form.points);
  const penalty = Number.parseFloat(form.penaltyPerIncorrect);

  return {
    type: form.type,
    questionText: form.questionText.trim(),
    scenarioText: form.type === 'SCENARIO' ? form.scenarioText.trim() || null : null,
    explanation: form.explanation.trim() || null,
    difficulty: form.difficulty || null,
    status: form.status,
    externalRef: form.externalRef.trim() || null,
    options,
    topics: form.topics,
    scoring: {
      points: Number.isFinite(points) ? points : 0,
      scoringStrategy: form.scoringStrategy,
      penaltyPerIncorrect:
        form.scoringStrategy === 'PARTIAL_CREDIT_WITH_PENALTY' && Number.isFinite(penalty)
          ? penalty
          : 0,
    },
  };
}

export function fromQuestion(question: Question): QuestionForm {
  const isOrdering = question.type === 'DRAG_TO_ORDER';

  // For an ordering question, load the items in CORRECT order so the editor's list position and
  // the answer key stay the same concept throughout editing.
  const source = isOrdering
    ? [...question.options].sort(
        (a, b) => (a.correctPosition ?? 0) - (b.correctPosition ?? 0),
      )
    : [...question.options].sort((a, b) => a.position - b.position);

  return {
    type: question.type,
    questionText: question.questionText,
    scenarioText: question.scenarioText ?? '',
    explanation: question.explanation ?? '',
    difficulty: question.difficulty ?? '',
    status: question.status === 'DRAFT' ? 'DRAFT' : 'ACTIVE',
    externalRef: question.externalRef ?? '',
    topics: question.topics.map((topic) => topic.name),
    points: String(question.scoring.points),
    scoringStrategy: question.scoring.scoringStrategy,
    penaltyPerIncorrect:
      question.scoring.penaltyPerIncorrect > 0 ? String(question.scoring.penaltyPerIncorrect) : '',
    options: source.map((option) => ({
      key: option.id,
      label: option.label,
      text: option.text,
      isCorrect: option.isCorrect,
      isPrimary: option.isPrimary,
      feedback: option.feedback ?? '',
    })),
  };
}

// ---------------------------------------------------------------------------
// Client-side hints (UX only — the backend is authoritative)
// ---------------------------------------------------------------------------

export function describeClientIssues(form: QuestionForm): string[] {
  const issues: string[] = [];
  const correct = form.options.filter((option) => option.isCorrect);

  if (!form.questionText.trim()) issues.push('Question text is required.');
  if (!form.explanation.trim()) issues.push('An explanation is required.');
  if (form.topics.length === 0) issues.push('Assign at least one topic.');

  const points = Number.parseFloat(form.points);
  if (!Number.isFinite(points) || points <= 0) issues.push('Points must be a number greater than zero.');

  if (form.scoringStrategy === 'PARTIAL_CREDIT_WITH_PENALTY') {
    const penalty = Number.parseFloat(form.penaltyPerIncorrect);
    if (!Number.isFinite(penalty) || penalty <= 0) {
      issues.push('Partial credit with penalty needs a penalty greater than zero.');
    } else if (penalty > points) {
      issues.push('The penalty may not exceed the question points.');
    }
  }

  if (form.options.some((option) => !option.text.trim())) issues.push('Every option needs text.');
  if (form.options.some((option) => !option.label.trim())) issues.push('Every option needs a label.');

  const labels = form.options.map((option) => option.label.trim().toUpperCase());
  if (new Set(labels).size !== labels.length) issues.push('Option labels must be unique.');

  switch (form.type) {
    case 'SINGLE_CHOICE':
      if (form.options.length !== 4) issues.push('Single choice requires exactly four options.');
      if (correct.length !== 1) issues.push('Select exactly one correct answer.');
      break;
    case 'TRUE_FALSE':
      if (correct.length !== 1) issues.push('Choose whether the answer is True or False.');
      break;
    case 'MULTI_SELECT':
      if (form.options.length < 3) issues.push('Multi-select requires at least three options.');
      if (correct.length < 1) issues.push('Select at least one correct answer.');
      if (correct.length === form.options.length) {
        issues.push('At least one option must be a distractor.');
      }
      if (form.scoringStrategy !== 'ALL_OR_NOTHING' && correct.length < 2) {
        issues.push('Partial credit needs at least two correct answers.');
      }
      break;
    case 'SCENARIO':
      if (form.scenarioText.trim().length < 20) {
        issues.push('Scenario questions need a vignette of at least 20 characters.');
      }
      if (form.options.length < 2) issues.push('Scenario questions need at least two options.');
      if (correct.length < 1) issues.push('Mark the primary answer.');
      break;
    case 'DRAG_TO_ORDER': {
      if (form.options.length < 2) issues.push('Drag-to-order needs at least two items.');
      const texts = form.options.map((option) => option.text.trim().toLowerCase()).filter(Boolean);
      if (new Set(texts).size !== texts.length) issues.push('Ordered items must be unique.');
      break;
    }
  }

  return issues;
}
