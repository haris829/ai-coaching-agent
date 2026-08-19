/**
 * The three screens after a submission: score (UC-04), pass/fail (UC-05), feedback (UC-06).
 *
 * A verification surface, not a product screen. It exists so the whole backend flow can be walked in a
 * browser, so it shows the machine-readable state as well as the human wording: the score's status and
 * `PENDING_SCORE` reason, the certificate's status and retry counter, the CPD record's status, and the
 * per-option mark contributions on a multi-select.
 *
 * Nothing here computes anything. Every number and every verdict arrives decided by the backend — the
 * percentage, the pass/fail, the remaining attempts, the explanations, the lesson references. A test UI
 * that recomputed a score would be able to disagree with the system it exists to demonstrate.
 */

import type { ReactNode } from 'react';

import type {
  AttemptResult,
  Certificate,
  CpdRecord,
  FeedbackItem,
  FeedbackOption,
  FeedbackResponse,
  OutcomeResponse,
  QuestionScore,
} from '../../api/resultTypes';
import { formatDate } from '../ui';

function marks(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function duration(seconds: number | null): string {
  if (seconds === null) return '—';
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
}

function answerText(answer: {
  readonly labels?: readonly string[];
  readonly summary?: string;
}): string {
  if (answer.summary) return answer.summary;
  const labels = answer.labels ?? [];
  return labels.length > 0 ? labels.join(', ') : '—';
}

const OUTCOME_TONE: Record<string, string> = {
  CORRECT: 'badge-active',
  PARTIALLY_CORRECT: 'badge-warning',
  INCORRECT: 'badge-retired',
  UNANSWERED: 'badge-neutral',
  NOT_SCORED: 'badge-warning',
};

/** UC-04 — the score. */
export function ScoreCard({
  result,
  scores,
  onRescore,
  busy,
}: {
  result: AttemptResult;
  scores: readonly QuestionScore[];
  onRescore: () => void;
  busy: boolean;
}): ReactNode {
  const pending = result.status === 'PENDING_SCORE';

  return (
    <div className="card">
      <div className="card-header">
        <h2>Score</h2>
        <span className={`badge ${pending ? 'badge-warning' : 'badge-active'}`}>
          {result.statusLabel}
        </span>
      </div>
      <div className="card-body">
        {pending ? (
          <>
            <p className="alert alert-warning">
              The attempt is submitted and safe, but it could not be scored yet
              {result.failureCode ? ` (${result.failureCode})` : ''}. Scoring can be retried once the
              data is fixed; nothing about the submission is lost.
            </p>
            {result.anomalies.length > 0 && (
              <ul className="field-hint">
                {result.anomalies.map((anomaly, index) => (
                  <li key={`${anomaly.code}-${index}`}>
                    {anomaly.code}
                    {anomaly.questionId ? ` · question ${anomaly.questionId}` : ''}
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <>
            <p>
              <strong>
                {marks(result.totalMarks)} / {marks(result.maximumMarks)} marks
              </strong>{' '}
              · <strong>{result.percentage}%</strong> · pass mark {result.passMarkPercentage}%
            </p>
            <p className="field-hint">
              {result.correctCount} correct · {result.incorrectCount} incorrect ·{' '}
              {result.unansweredCount} unanswered · time taken {duration(result.timeTakenSeconds)} ·
              scored {formatDate(result.scoredAt)}
            </p>
            <p className="field-hint">
              Scored against configuration version {result.configurationVersion} — the version this
              attempt was locked to, not the quiz's current one.
            </p>

            <div className="table-wrap">
              <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Question</th>
                  <th>Type</th>
                  <th>Marks</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {scores.map((score) => (
                  <tr key={score.questionId}>
                    <td>{score.position}</td>
                    <td>{score.questionText}</td>
                    <td>{score.questionType}</td>
                    <td>
                      {marks(score.awardedMarks)} / {marks(score.maximumMarks)}
                    </td>
                    <td>
                      <span className={`badge ${OUTCOME_TONE[score.outcome] ?? 'badge-neutral'}`}>
                        {score.outcome}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
</div>
          </>
        )}

        <button type="button" className="btn" disabled={busy} onClick={onRescore}>
          {pending ? 'Retry scoring' : 'Re-run scoring (replays the confirmed score)'}
        </button>
      </div>
    </div>
  );
}

/** UC-05 — pass/fail, the certificate and the CPD record. */
export function OutcomeCard({
  outcome,
  onRetryCertificate,
  onRetryCpd,
  busy,
}: {
  outcome: OutcomeResponse;
  onRetryCertificate: () => void;
  onRetryCpd: () => void;
  busy: boolean;
}): ReactNode {
  const passed = outcome.outcome.passed;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Pass / fail</h2>
        <span className={`badge ${passed ? 'badge-active' : 'badge-retired'}`}>
          {outcome.outcome.outcomeLabel}
        </span>
      </div>
      <div className="card-body">
        <p className={passed ? 'alert alert-success' : 'alert alert-warning'}>
          {outcome.outcome.percentage}% against a pass mark of {outcome.outcome.passMarkPercentage}%
          {passed ? ' — passed.' : ' — not passed.'}
        </p>
        <p className="field-hint">
          Determined {formatDate(outcome.outcome.determinedAt)} against the pass mark of the
          attempt's own configuration version. Attempts used {outcome.attemptsUsed}
          {outcome.maxAttempts === null
            ? ' of unlimited'
            : ` of ${outcome.maxAttempts} · ${outcome.attemptsRemaining} remaining`}
          {!passed && outcome.mayReattempt ? ' — the quiz can be re-sat.' : ''}
        </p>

        {outcome.certificate !== null && <CertificateBlock certificate={outcome.certificate} />}
        {outcome.certificate === null && (
          <p className="field-hint">No certificate: one is only issued for a passing attempt.</p>
        )}
        {outcome.cpd !== null && <CpdBlock record={outcome.cpd} />}

        <div className="row tight">
          {outcome.certificate !== null && outcome.certificate.status !== 'ISSUED' && (
            <button type="button" className="btn" disabled={busy} onClick={onRetryCertificate}>
              Retry certificate
            </button>
          )}
          {outcome.cpd !== null && outcome.cpd.status !== 'SYNCHRONISED' && (
            <button type="button" className="btn" disabled={busy} onClick={onRetryCpd}>
              Retry CPD sync
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function CertificateBlock({ certificate }: { certificate: Certificate }): ReactNode {
  const tone =
    certificate.status === 'ISSUED'
      ? 'badge-active'
      : certificate.status === 'PENDING'
        ? 'badge-warning'
        : 'badge-retired';
  return (
    <p>
      <span className={`badge ${tone}`}>Certificate {certificate.status}</span>{' '}
      {certificate.certificateNumber ?? '—'} · {certificate.courseName}
      {certificate.issuedAt ? ` · issued ${formatDate(certificate.issuedAt)}` : ''}
      {certificate.failureMessage ? (
        <>
          <br />
          <span className="field-hint">{certificate.failureMessage}</span>
        </>
      ) : null}
    </p>
  );
}

function CpdBlock({ record }: { record: CpdRecord }): ReactNode {
  const tone =
    record.status === 'SYNCHRONISED'
      ? 'badge-active'
      : record.status === 'PENDING'
        ? 'badge-warning'
        : 'badge-retired';
  return (
    <p>
      <span className={`badge ${tone}`}>CPD {record.status}</span> {record.courseName} ·{' '}
      {record.scorePercentage}% · {record.passed ? 'pass' : 'fail'} ·{' '}
      {formatDate(record.attemptDate)}
      {record.externalReference ? ` · ${record.externalReference}` : ''}
      {record.failureCode ? (
        <>
          <br />
          <span className="field-hint">{record.failureCode}</span>
        </>
      ) : null}
    </p>
  );
}

/** UC-06 — the detailed feedback report. */
export function FeedbackCard({
  feedback,
  onRegenerate,
  busy,
}: {
  feedback: FeedbackResponse;
  onRegenerate: () => void;
  busy: boolean;
}): ReactNode {
  const ready = feedback.status === 'GENERATED';

  return (
    <div className="card">
      <div className="card-header">
        <h2>Feedback</h2>
        <span className={`badge ${ready ? 'badge-active' : 'badge-warning'}`}>
          {feedback.statusLabel}
        </span>
      </div>
      <div className="card-body">
        {!ready && (
          <p className="alert alert-warning">
            The report is not generated yet
            {feedback.failureCode ? ` (${feedback.failureCode})` : ''}. The score and the pass/fail
            result are unaffected, and generation can be retried.
          </p>
        )}

        {ready && (
          <>
            <p className="field-hint">
              {feedback.summary.totalScore} / {feedback.summary.maximumMarks} marks ·{' '}
              {feedback.summary.percentage}% ·{' '}
              {feedback.summary.passed === null
                ? 'pass/fail not determined'
                : feedback.summary.passed
                  ? 'passed'
                  : 'not passed'}{' '}
              · time taken {duration(feedback.summary.timeTakenSeconds)} ·{' '}
              {feedback.summary.correctCount} correct · {feedback.summary.incorrectCount} incorrect ·{' '}
              {feedback.summary.unansweredCount} unanswered · generated{' '}
              {formatDate(feedback.generatedAt)}
            </p>

            {feedback.items.map((item) => (
              <FeedbackItemBlock key={item.questionId} item={item} />
            ))}
          </>
        )}

        <button type="button" className="btn" disabled={busy} onClick={onRegenerate}>
          {ready ? 'Re-request feedback (replays the frozen report)' : 'Retry feedback'}
        </button>
      </div>
    </div>
  );
}

function FeedbackItemBlock({ item }: { item: FeedbackItem }): ReactNode {
  return (
    <div className="card" style={{ marginBottom: '0.75rem' }}>
      <div className="card-header">
        <h3>
          {item.position}. {item.question}
        </h3>
        <span className={`badge ${OUTCOME_TONE[item.outcome] ?? 'badge-neutral'}`}>
          {marks(item.questionScore)} / {marks(item.maximumMarks)}
        </span>
      </div>
      <div className="card-body">
        {item.scenarioText && <p className="field-hint">{item.scenarioText}</p>}
        <p>
          <strong>Your answer:</strong> {answerText(item.learnerAnswer)}
          <br />
          <strong>Correct answer:</strong> {answerText(item.correctAnswer)}
        </p>
        <p>
          <strong>Explanation:</strong> {item.explanation}
        </p>
        <p className="field-hint">{item.lessonReference}</p>

        {item.optionBreakdown.length > 0 && <OptionBreakdown options={item.optionBreakdown} />}
      </div>
    </div>
  );
}

function OptionBreakdown({ options }: { options: readonly FeedbackOption[] }): ReactNode {
  return (
    <div className="table-wrap">
      <table>
      <thead>
        <tr>
          <th>Option</th>
          <th>Chosen</th>
          <th>Correct</th>
          <th>Marks</th>
        </tr>
      </thead>
      <tbody>
        {options.map((option, index) => (
          <tr key={`${option.optionId}-${index}`}>
            <td>
              {option.text}
              {option.feedback ? (
                <>
                  <br />
                  <span className="field-hint">{option.feedback}</span>
                </>
              ) : null}
            </td>
            <td>{option.selected ? 'yes' : 'no'}</td>
            <td>{option.correct ? 'yes' : 'no'}</td>
            <td>
              {option.markContribution > 0 ? '+' : ''}
              {marks(option.markContribution)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
</div>
  );
}
