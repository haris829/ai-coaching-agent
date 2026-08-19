/**
 * The submission confirmation step.
 *
 * Submitting is irreversible, so it takes two deliberate actions: open this panel (which fetches a
 * server-built preview and writes nothing), then confirm. The backend enforces the same thing —
 * `POST /submission` without `confirmed: true` is rejected — so this is not a UI-only courtesy that
 * a stray request could bypass.
 *
 * Three details matter more than they look:
 *
 *  * **Blockers vs warnings** come from the server. A blocker disables confirmation (unanswered
 *    questions where the configuration forbids incomplete submission); a warning is shown and
 *    proceeds. The client never decides which is which.
 *  * **The idempotency key** is the server's own suggestion, held for the life of the panel, so a
 *    double-click, an impatient retry and a reconnect all collapse into one submission.
 *  * **A pending submission** is a distinct outcome, not a failure. The answers are safe and frozen;
 *    what failed was the downstream hand-off, and retrying is the correct action.
 */

import type { ReactNode } from 'react';

import type { SubmissionPreview, SubmissionStatus } from '../../api/attemptTypes';
import { formatRemaining } from '../../lib/attemptTimer';
import { Spinner } from '../ui';

export function SubmitPanel({
  preview,
  loading,
  submitting,
  onConfirm,
  onCancel,
  onGoToPosition,
}: {
  preview: SubmissionPreview | null;
  loading: boolean;
  submitting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onGoToPosition: (position: number) => void;
}): ReactNode {
  if (loading || preview === null) return <Spinner label="Checking your answers…" />;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Submit this attempt</h2>
        <span className="badge badge-neutral">
          {preview.completeCount} of {preview.totalQuestions} complete
        </span>
      </div>
      <div className="card-body">
        {preview.blockers.map((blocker) => (
          <p key={blocker.code} className="alert alert-error">
            {blocker.message}
          </p>
        ))}
        {preview.warnings.map((warning) => (
          <p key={warning.code} className="alert alert-warning">
            {warning.message}
          </p>
        ))}

        {preview.unanswered.length > 0 && (
          <div className="field">
            <span className="stat-label">Unanswered</span>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              {preview.unanswered.map((entry) => (
                <button
                  key={entry.questionId}
                  type="button"
                  className="btn btn-sm"
                  onClick={() => onGoToPosition(entry.position)}
                >
                  Q{entry.position}
                </button>
              ))}
            </div>
          </div>
        )}

        {preview.flagged.length > 0 && (
          <div className="field">
            <span className="stat-label">Still flagged</span>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              {preview.flagged.map((entry) => (
                <button
                  key={entry.questionId}
                  type="button"
                  className="btn btn-sm"
                  onClick={() => onGoToPosition(entry.position)}
                >
                  Q{entry.position}
                </button>
              ))}
            </div>
          </div>
        )}

        {preview.timing.timed && (
          <p className="field-hint">
            Time remaining: {formatRemaining(preview.timing.remainingSeconds)}. If the time limit
            passes, the attempt is submitted automatically with whatever has been saved.
          </p>
        )}

        <p className="field-hint">
          Submitting is final — the attempt is locked and its answers can no longer be changed.
        </p>

        <div className="row" style={{ gap: 8 }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!preview.canSubmit || submitting}
            onClick={onConfirm}
          >
            {submitting ? 'Submitting…' : 'Confirm and submit'}
          </button>
          <button type="button" className="btn" disabled={submitting} onClick={onCancel}>
            Keep working
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The outcome of a submission that could not be handed downstream.
 *
 * Deliberately reassuring and specific: the learner's answers were accepted and frozen, so the only
 * thing outstanding is the hand-off. Retry is safe because it reuses the same idempotency key.
 */
export function PendingSubmissionPanel({
  status,
  retrying,
  onRetry,
}: {
  status: SubmissionStatus;
  retrying: boolean;
  onRetry: () => void;
}): ReactNode {
  const latest = status.history[status.history.length - 1] ?? null;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Submission is still being finalised</h2>
        <span className="badge badge-warning">{latest?.state ?? status.status}</span>
      </div>
      <div className="card-body">
        <p className="alert alert-warning">
          Your answers have been accepted and locked — nothing is lost. What has not completed yet is
          handing them on for marking. Retrying is safe: it continues the same submission rather than
          creating a second one.
        </p>
        {latest && (
          <p className="field-hint">
            Attempted {latest.attempts} time(s). Reference <code className="mono">{latest.submissionId}</code>.
          </p>
        )}
        <button type="button" className="btn btn-primary" disabled={retrying} onClick={onRetry}>
          {retrying ? 'Retrying…' : 'Retry submission'}
        </button>
      </div>
    </div>
  );
}
