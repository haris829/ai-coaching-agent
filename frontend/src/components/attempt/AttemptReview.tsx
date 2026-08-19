/**
 * The review screen: every question, its state, and a way to get back to it.
 *
 * This is the "have I missed anything?" surface. It is driven by the server's own outline
 * (`GET /attempts/{id}/state`), not by local bookkeeping, so what it shows is what will actually be
 * submitted — a client-side tally that disagreed with the server would be worse than none.
 *
 * `answered` and `complete` are shown as different things because they are: a scenario with two of
 * three sub-questions filled in is answered but not complete, and it is precisely that question a
 * learner needs to find here.
 */

import type { ReactNode } from 'react';

import type { AttemptState, QuestionOutlineEntry } from '../../api/attemptTypes';
import { ATTEMPT_TYPE_LABELS } from '../../lib/attemptAnswers';

export type ReviewFilter = 'all' | 'unanswered' | 'flagged' | 'incomplete';

const FILTERS: { key: ReviewFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'unanswered', label: 'Unanswered' },
  { key: 'incomplete', label: 'Partly answered' },
  { key: 'flagged', label: 'Flagged' },
];

export function matchesFilter(entry: QuestionOutlineEntry, filter: ReviewFilter): boolean {
  switch (filter) {
    case 'unanswered':
      return !entry.answered;
    case 'incomplete':
      // Started but not finished — the case a plain answered/unanswered split hides.
      return entry.answered && !entry.complete;
    case 'flagged':
      return entry.flagged;
    default:
      return true;
  }
}

export function AttemptReview({
  state,
  filter,
  onFilterChange,
  onGoToQuestion,
  onToggleFlag,
  busy,
}: {
  state: AttemptState;
  filter: ReviewFilter;
  onFilterChange: (next: ReviewFilter) => void;
  /** Navigate to a question: moves the cursor in one-at-a-time, scrolls to it in all-at-once. */
  onGoToQuestion: (entry: QuestionOutlineEntry) => void;
  onToggleFlag: (entry: QuestionOutlineEntry) => void;
  busy?: boolean;
}): ReactNode {
  const visible = state.questions.filter((entry) => matchesFilter(entry, filter));

  return (
    <div className="card">
      <div className="card-header">
        <h2>Review</h2>
        <span className="badge badge-neutral">
          {state.completeCount} of {state.totalQuestions} complete
        </span>
      </div>
      <div className="card-body">
        <div className="kv">
          <div className="stat">
            <span className="stat-label">Answered</span>
            <span className="stat-value">{state.answeredCount}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Complete</span>
            <span className="stat-value">{state.completeCount}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Unanswered</span>
            <span className="stat-value">{state.unansweredCount}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Flagged</span>
            <span className="stat-value">{state.flaggedCount}</span>
          </div>
        </div>

        <div className="row" style={{ gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
          {FILTERS.map((entry) => (
            <button
              key={entry.key}
              type="button"
              className={`btn btn-sm${filter === entry.key ? ' btn-primary' : ''}`}
              onClick={() => onFilterChange(entry.key)}
            >
              {entry.label}
              {entry.key !== 'all' &&
                ` (${state.questions.filter((item) => matchesFilter(item, entry.key)).length})`}
            </button>
          ))}
        </div>

        {visible.length === 0 ? (
          <p className="empty" style={{ marginTop: 16 }}>
            Nothing matches this filter.
          </p>
        ) : (
          <ul className="review-list">
            {visible.map((entry) => (
              <li key={entry.questionId} className="review-row">
                <button
                  type="button"
                  className="review-jump"
                  onClick={() => onGoToQuestion(entry)}
                  aria-label={`Go to question ${entry.position}`}
                >
                  <span className={`review-dot ${dotClass(entry)}`} aria-hidden="true" />
                  <span className="review-position">Q{entry.position}</span>
                  <span className="review-type">
                    {ATTEMPT_TYPE_LABELS[entry.questionType] ?? entry.questionType}
                  </span>
                  <span className="review-state">{stateLabel(entry)}</span>
                </button>
                <button
                  type="button"
                  className={`btn btn-sm${entry.flagged ? ' btn-primary' : ''}`}
                  disabled={busy}
                  onClick={() => onToggleFlag(entry)}
                >
                  {entry.flagged ? 'Unflag' : 'Flag'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/**
 * A compact navigator, for the header of a one-at-a-time paper.
 *
 * The same data as the review list, sized to sit above the question so a learner can see the shape
 * of the whole paper without leaving the one they are on.
 */
export function QuestionNavigator({
  state,
  currentPosition,
  onGoToQuestion,
}: {
  state: AttemptState;
  currentPosition: number;
  onGoToQuestion: (entry: QuestionOutlineEntry) => void;
}): ReactNode {
  return (
    <nav className="navigator" aria-label="Question navigator">
      {state.questions.map((entry) => (
        <button
          key={entry.questionId}
          type="button"
          className={`nav-pip ${dotClass(entry)}${entry.position === currentPosition ? ' nav-pip-current' : ''}`}
          onClick={() => onGoToQuestion(entry)}
          aria-current={entry.position === currentPosition ? 'true' : undefined}
          aria-label={`Question ${entry.position}, ${stateLabel(entry)}`}
          title={`Q${entry.position} — ${stateLabel(entry)}`}
        >
          {entry.position}
        </button>
      ))}
    </nav>
  );
}

function dotClass(entry: QuestionOutlineEntry): string {
  if (entry.flagged) return 'is-flagged';
  if (entry.complete) return 'is-complete';
  if (entry.answered) return 'is-partial';
  return 'is-empty';
}

function stateLabel(entry: QuestionOutlineEntry): string {
  const parts: string[] = [];
  if (entry.complete) parts.push('complete');
  else if (entry.answered) parts.push('partly answered');
  else parts.push('unanswered');
  if (entry.flagged) parts.push('flagged');
  return parts.join(' · ');
}
