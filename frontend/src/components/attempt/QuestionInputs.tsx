/**
 * Inputs for the five question structures UC-03 delivers.
 *
 * Each renderer is controlled: it receives the current response and reports the next one, and holds
 * no state of its own. That is what makes a restored attempt render identically to a live one — the
 * inputs are a function of the persisted answer, so there is no second copy of the learner's work to
 * fall out of step.
 *
 * None of these know how to *validate*; the backend does that against the frozen question snapshot
 * and returns per-field messages. They only shape the payload.
 *
 * Accessibility is basic but real: fieldset/legend grouping, labels tied to inputs, and reorder
 * controls that work from the keyboard — a drag-only ordering question would be unusable without
 * them, and this UI is meant to be operable while testing.
 */

import type { ReactNode } from 'react';

import type {
  AnswerResponse,
  AttemptQuestion,
  AttemptSubQuestion,
  SubAnswerResponse,
} from '../../api/attemptTypes';
import {
  ATTEMPT_TYPE_LABELS,
  acceptDeliveredOrder,
  booleanValue,
  moveItem,
  orderedItemIds,
  selectedOptionId,
  selectedOptionIds,
  subResponse,
  toggleOption,
  withBoolean,
  withSelectedOption,
  withSubResponse,
} from '../../lib/attemptAnswers';

interface InputProps {
  question: AttemptQuestion;
  response: AnswerResponse;
  onChange: (next: AnswerResponse) => void;
  disabled?: boolean;
}

/** Dispatch on the delivered type. An unknown type is reported, never silently skipped. */
export function QuestionInput({ question, response, onChange, disabled }: InputProps): ReactNode {
  switch (question.questionType) {
    case 'SINGLE_CHOICE':
      return <SingleChoiceInput {...{ question, response, onChange, disabled }} />;
    case 'TRUE_FALSE':
      return <TrueFalseInput {...{ question, response, onChange, disabled }} />;
    case 'MULTI_SELECT':
      return <MultiSelectInput {...{ question, response, onChange, disabled }} />;
    case 'DRAG_TO_ORDER':
      return <DragToOrderInput {...{ question, response, onChange, disabled }} />;
    case 'SCENARIO':
      return <ScenarioInput {...{ question, response, onChange, disabled }} />;
    default:
      return (
        <p className="alert alert-warning">
          This question is of a type this UI does not render ({String(question.questionType)}). The
          attempt is unaffected — the answer simply cannot be entered here.
        </p>
      );
  }
}

// ---------------------------------------------------------------------------
// Single choice
// ---------------------------------------------------------------------------

function SingleChoiceInput({ question, response, onChange, disabled }: InputProps): ReactNode {
  const chosen = selectedOptionId(response);
  const name = `q-${question.questionId}`;

  return (
    <fieldset className="answer-group">
      <legend className="sr-only">Select one option</legend>
      {(question.options ?? []).map((option) => (
        <label key={option.optionId} className={`choice${chosen === option.optionId ? ' choice-selected' : ''}`}>
          <input
            type="radio"
            name={name}
            value={option.optionId}
            checked={chosen === option.optionId}
            disabled={disabled}
            onChange={() => onChange(withSelectedOption(option.optionId))}
          />
          <span>{option.text}</span>
        </label>
      ))}
    </fieldset>
  );
}

// ---------------------------------------------------------------------------
// True / false
// ---------------------------------------------------------------------------

function TrueFalseInput({ question, response, onChange, disabled }: InputProps): ReactNode {
  const value = booleanValue(response);
  const name = `q-${question.questionId}`;

  return (
    <fieldset className="answer-group">
      <legend className="sr-only">True or false</legend>
      {[
        { label: 'True', value: true },
        { label: 'False', value: false },
      ].map((entry) => (
        <label key={entry.label} className={`choice${value === entry.value ? ' choice-selected' : ''}`}>
          <input
            type="radio"
            name={name}
            checked={value === entry.value}
            disabled={disabled}
            onChange={() => onChange(withBoolean(entry.value))}
          />
          <span>{entry.label}</span>
        </label>
      ))}
    </fieldset>
  );
}

// ---------------------------------------------------------------------------
// Multiple select
// ---------------------------------------------------------------------------

function MultiSelectInput({ question, response, onChange, disabled }: InputProps): ReactNode {
  const chosen = new Set(selectedOptionIds(response));
  const max = question.maxSelections ?? null;
  const min = question.minSelections ?? null;
  // At the ceiling, unticking stays available but ticking a new box does not — the same rule the
  // server enforces, surfaced before the round-trip rather than as a rejection after it.
  const atCeiling = max !== null && chosen.size >= max;

  return (
    <fieldset className="answer-group">
      <legend className="sr-only">Select all that apply</legend>
      {(question.options ?? []).map((option) => {
        const selected = chosen.has(option.optionId);
        return (
          <label
            key={option.optionId}
            className={`choice${selected ? ' choice-selected' : ''}`}
          >
            <input
              type="checkbox"
              checked={selected}
              disabled={disabled || (!selected && atCeiling)}
              onChange={() => onChange(toggleOption(response, option.optionId))}
            />
            <span>{option.text}</span>
          </label>
        );
      })}
      {(min !== null || max !== null) && (
        <p className="field-hint">
          {min !== null && max !== null
            ? `Select between ${min} and ${max}.`
            : min !== null
              ? `Select at least ${min}.`
              : `Select at most ${max}.`}{' '}
          {chosen.size} selected.
        </p>
      )}
    </fieldset>
  );
}

// ---------------------------------------------------------------------------
// Drag to order
// ---------------------------------------------------------------------------

function DragToOrderInput({ question, response, onChange, disabled }: InputProps): ReactNode {
  const items = question.orderItems ?? [];
  const itemIds = items.map((item) => item.itemId);
  const ordering = orderedItemIds(response);
  const placed = ordering.length === itemIds.length;
  // Before the learner touches it, show the delivered order — but do not pretend it is an answer.
  const shown = placed ? ordering : itemIds;
  const byId = new Map(items.map((item) => [item.itemId, item]));

  return (
    <div className="answer-group">
      {!placed && (
        <p className="field-hint">
          Shown in the order delivered. Reorder the steps, or confirm the order as shown, to answer.
        </p>
      )}
      <ol className="order-list">
        {shown.map((itemId, index) => (
          <li key={itemId} className="order-item">
            <span className="order-position">{index + 1}</span>
            <span className="order-text">{byId.get(itemId)?.text ?? itemId}</span>
            <span className="order-controls">
              <button
                type="button"
                className="btn btn-sm"
                disabled={disabled || index === 0}
                aria-label={`Move "${byId.get(itemId)?.text ?? itemId}" up`}
                onClick={() => onChange(moveItem(response, itemIds, itemId, -1))}
              >
                ↑
              </button>
              <button
                type="button"
                className="btn btn-sm"
                disabled={disabled || index === shown.length - 1}
                aria-label={`Move "${byId.get(itemId)?.text ?? itemId}" down`}
                onClick={() => onChange(moveItem(response, itemIds, itemId, 1))}
              >
                ↓
              </button>
            </span>
          </li>
        ))}
      </ol>
      {!placed && (
        <button
          type="button"
          className="btn btn-sm"
          disabled={disabled}
          onClick={() => onChange(acceptDeliveredOrder(itemIds))}
        >
          Confirm this order
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scenario
// ---------------------------------------------------------------------------

function ScenarioInput({ question, response, onChange, disabled }: InputProps): ReactNode {
  const subs = question.subQuestions ?? [];

  return (
    <div className="answer-group scenario">
      {subs.map((sub, index) => (
        <div key={sub.subQuestionId} className="sub-question">
          <div className="sub-question-header">
            <span className="badge badge-neutral">
              {index + 1}. {ATTEMPT_TYPE_LABELS[sub.type] ?? sub.type}
            </span>
            <p className="sub-prompt">{sub.prompt}</p>
          </div>
          <SubQuestionInput
            sub={sub}
            answer={subResponse(response, sub.subQuestionId)}
            disabled={disabled}
            onChange={(next) => onChange(withSubResponse(response, subs, sub.subQuestionId, next))}
          />
        </div>
      ))}
    </div>
  );
}

/**
 * One sub-question of a scenario.
 *
 * Reuses the top-level renderers by presenting the sub-question as a question — the shapes are the
 * same, and a second set of renderers would be the same code twice.
 */
function SubQuestionInput({
  sub,
  answer,
  onChange,
  disabled,
}: {
  sub: AttemptSubQuestion;
  answer: SubAnswerResponse | null;
  onChange: (next: SubAnswerResponse | null) => void;
  disabled?: boolean;
}): ReactNode {
  const asQuestion: AttemptQuestion = {
    questionId: sub.subQuestionId,
    position: 0,
    questionType: sub.type,
    questionVersion: 0,
    points: 0,
    prompt: sub.prompt,
    options: sub.options,
    orderItems: sub.orderItems,
    minSelections: sub.minSelections,
    maxSelections: sub.maxSelections,
  };

  return (
    <QuestionInput
      question={asQuestion}
      response={answer}
      disabled={disabled}
      onChange={(next) => onChange(next as SubAnswerResponse | null)}
    />
  );
}
