/**
 * Option editors, one per answer shape.
 *
 * `ChoiceOptionsEditor` covers SINGLE_CHOICE / TRUE_FALSE / MULTI_SELECT / SCENARIO: correctness
 * is a radio (single-answer types) or a checkbox (multi-select).
 *
 * `OrderItemsEditor` covers DRAG_TO_ORDER and is deliberately different: the admin arranges the
 * list into the CORRECT ANSWER ORDER, and the rank shown against each row is the answer key. The
 * order a learner eventually sees is chosen by the delivery module and is not editable here —
 * the two concepts are kept visibly separate.
 */

import { useState, type ReactNode } from 'react';

import {
  addOption,
  moveOption,
  removeOption,
  selectSingleCorrect,
  toggleCorrect,
  updateOption,
  type QuestionForm,
} from '../lib/questionForm';

interface EditorProps {
  form: QuestionForm;
  onChange: (form: QuestionForm) => void;
  disabled?: boolean;
}

export function ChoiceOptionsEditor({ form, onChange, disabled = false }: EditorProps): ReactNode {
  const multi = form.type === 'MULTI_SELECT';
  const fixed = form.type === 'TRUE_FALSE';
  const canAddOrRemove = !fixed && form.type !== 'SINGLE_CHOICE' && !disabled;

  return (
    <div>
      <div className="row spread" style={{ marginBottom: 10 }}>
        <span className="field-label required" style={{ margin: 0 }}>
          {multi ? 'Answer options — tick every correct answer' : 'Answer options — select the correct answer'}
        </span>
        {form.type === 'SINGLE_CHOICE' && <span className="subtle">Exactly four options required</span>}
      </div>

      {form.options.map((option) => (
        <div key={option.key} className={`option-row${option.isCorrect ? ' is-correct' : ''}`}>
          <div className="option-pick">
            <input
              type={multi ? 'checkbox' : 'radio'}
              name={multi ? undefined : 'correct-option'}
              checked={option.isCorrect}
              disabled={disabled}
              aria-label={`Mark option ${option.label} correct`}
              onChange={() =>
                onChange(multi ? toggleCorrect(form, option.key) : selectSingleCorrect(form, option.key))
              }
            />
          </div>

          <input
            className="option-label-input"
            type="text"
            value={option.label}
            disabled={disabled || fixed}
            aria-label={`Option label`}
            placeholder="A"
            onChange={(event) => onChange(updateOption(form, option.key, { label: event.target.value }))}
          />

          <input
            className="option-text-input"
            type="text"
            value={option.text}
            disabled={disabled || fixed}
            aria-label={`Option ${option.label} text`}
            placeholder="Option text"
            onChange={(event) => onChange(updateOption(form, option.key, { text: event.target.value }))}
          />

          {canAddOrRemove && (
            <div className="option-remove">
              <button
                type="button"
                className="btn btn-sm btn-icon"
                title="Remove option"
                aria-label={`Remove option ${option.label}`}
                disabled={form.options.length <= 2}
                onClick={() => onChange(removeOption(form, option.key))}
              >
                ×
              </button>
            </div>
          )}
        </div>
      ))}

      {canAddOrRemove && (
        <button
          type="button"
          className="btn btn-sm"
          style={{ marginTop: 10 }}
          onClick={() => onChange(addOption(form))}
        >
          + Add option
        </button>
      )}

      {form.type === 'SCENARIO' && (
        <p className="field-hint">
          The correct answer is recorded as the question&apos;s <strong>primary answer</strong>.
        </p>
      )}
    </div>
  );
}

export function OrderItemsEditor({ form, onChange, disabled = false }: EditorProps): ReactNode {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);

  function drop(target: number): void {
    if (dragIndex !== null) onChange(moveOption(form, dragIndex, target));
    setDragIndex(null);
    setOverIndex(null);
  }

  return (
    <div>
      <div className="row spread" style={{ marginBottom: 6 }}>
        <span className="field-label required" style={{ margin: 0 }}>
          Ordered items — arrange them in the correct order
        </span>
      </div>
      <p className="field-hint" style={{ marginBottom: 10 }}>
        The rank shown here is the <strong>correct answer order</strong>. Quiz delivery shuffles the
        items before showing them to a learner, so this is the answer key, not the display order.
      </p>

      {form.options.map((option, index) => (
        <div
          key={option.key}
          className={[
            'order-row',
            dragIndex === index ? 'dragging' : '',
            overIndex === index && dragIndex !== null && dragIndex !== index ? 'drop-target' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          draggable={!disabled}
          onDragStart={() => setDragIndex(index)}
          onDragEnd={() => {
            setDragIndex(null);
            setOverIndex(null);
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setOverIndex(index);
          }}
          onDrop={(event) => {
            event.preventDefault();
            drop(index);
          }}
        >
          <span className="order-grip" aria-hidden="true">
            ⠿
          </span>
          <span className="order-rank" title={`Correct position ${index + 1}`}>
            {index + 1}
          </span>

          <input
            className="option-label-input"
            type="text"
            value={option.label}
            disabled={disabled}
            aria-label="Item label"
            placeholder="A"
            onChange={(event) => onChange(updateOption(form, option.key, { label: event.target.value }))}
          />

          <input
            className="option-text-input"
            type="text"
            value={option.text}
            disabled={disabled}
            aria-label={`Item ${option.label} text`}
            placeholder="Item text"
            onChange={(event) => onChange(updateOption(form, option.key, { text: event.target.value }))}
          />

          {!disabled && (
            <div className="row tight" style={{ flexWrap: 'nowrap' }}>
              <button
                type="button"
                className="btn btn-sm btn-icon"
                title="Move up"
                aria-label={`Move ${option.label} up`}
                disabled={index === 0}
                onClick={() => onChange(moveOption(form, index, index - 1))}
              >
                ↑
              </button>
              <button
                type="button"
                className="btn btn-sm btn-icon"
                title="Move down"
                aria-label={`Move ${option.label} down`}
                disabled={index === form.options.length - 1}
                onClick={() => onChange(moveOption(form, index, index + 1))}
              >
                ↓
              </button>
              <button
                type="button"
                className="btn btn-sm btn-icon"
                title="Remove item"
                aria-label={`Remove ${option.label}`}
                disabled={form.options.length <= 2}
                onClick={() => onChange(removeOption(form, option.key))}
              >
                ×
              </button>
            </div>
          )}
        </div>
      ))}

      {!disabled && (
        <button
          type="button"
          className="btn btn-sm"
          style={{ marginTop: 10 }}
          onClick={() => onChange(addOption(form))}
        >
          + Add item
        </button>
      )}
    </div>
  );
}

export function OptionsEditor(props: EditorProps): ReactNode {
  return props.form.type === 'DRAG_TO_ORDER' ? (
    <OrderItemsEditor {...props} />
  ) : (
    <ChoiceOptionsEditor {...props} />
  );
}
