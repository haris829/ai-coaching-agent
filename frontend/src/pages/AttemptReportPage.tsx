/**
 * Historical attempt report (UC-02 §16).
 *
 * Exists to make the historical-preservation guarantee visible to an administrator: everything on
 * this screen is rendered from the frozen question snapshot the attempt was delivered, so a
 * question that has since been edited or retired still reports exactly as the learner saw it.
 */

import { useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import type { AttemptReport } from '../api/types';
import { ErrorSummary, Spinner, StatusBadge, TypeBadge, formatDate } from '../components/ui';

export function AttemptReportPage(): ReactNode {
  const { attemptRef } = useParams<{ attemptRef: string }>();
  const navigate = useNavigate();

  const [lookup, setLookup] = useState(attemptRef ?? '');
  const [report, setReport] = useState<AttemptReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function load(ref: string): Promise<void> {
    if (!ref.trim()) return;
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      setReport(await api.attemptReport(ref.trim()));
    } catch (cause) {
      setError(cause);
    } finally {
      setLoading(false);
    }
  }

  // Load immediately when arrived at via a link with the ref in the URL.
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  if (attemptRef && loadedFor !== attemptRef) {
    setLoadedFor(attemptRef);
    void load(attemptRef);
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Attempt report</h1>
          <p>
            Rendered entirely from the question snapshot each answer was delivered with. Editing or
            retiring a question afterwards cannot change what this report shows.
          </p>
        </div>
        <Link className="btn" to="/questions">
          Question bank
        </Link>
      </div>

      <div className="card">
        <div className="card-body">
          <div className="row tight">
            <input
              type="search"
              value={lookup}
              placeholder="Attempt reference, e.g. attempt-A"
              onChange={(event) => setLookup(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') navigate(`/reports/${encodeURIComponent(lookup.trim())}`);
              }}
              style={{ maxWidth: 340 }}
            />
            <button
              type="button"
              className="btn btn-primary"
              disabled={!lookup.trim() || loading}
              onClick={() => navigate(`/reports/${encodeURIComponent(lookup.trim())}`)}
            >
              Look up
            </button>
          </div>
          <p className="field-hint">
            Attempt references are owned by the quiz-delivery module. The question bank records the
            snapshot each question was delivered with, which is what makes this report stable.
          </p>
        </div>
      </div>

      {loading && (
        <div className="card">
          <div className="empty">
            <Spinner label="Loading report…" />
          </div>
        </div>
      )}

      {error ? (
        <div style={{ marginTop: 16 }}>
          <ErrorSummary error={error} />
          {error instanceof ApiError && error.status === 404 && (
            <p className="field-hint">
              No attempt with that reference has used a question from this bank yet.
            </p>
          )}
        </div>
      ) : null}

      {report && (
        <>
          <div className="card">
            <div className="card-header">
              <h2>
                <span className="mono">{report.attemptRef}</span>
              </h2>
              <span className="badge badge-neutral">{report.attemptStatus}</span>
            </div>
            <div className="card-body">
              <div className="grid-3">
                <div className="stat">
                  <div className="stat-value">{report.questionCount}</div>
                  <div className="stat-label">Questions</div>
                </div>
                <div className="stat">
                  <div className="stat-value">
                    {report.totalAwardedPoints}
                    <span className="subtle" style={{ fontSize: 16 }}>
                      {' '}
                      / {report.totalMaxPoints}
                    </span>
                  </div>
                  <div className="stat-label">Score</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{report.learnerRef ?? '—'}</div>
                  <div className="stat-label">Learner</div>
                </div>
              </div>
            </div>
          </div>

          {report.items.map((item) => {
            const selected = new Set(
              (item.learnerResponse?.selectedLabels ?? []).map((label) => label.toUpperCase()),
            );
            const answerOrder = item.learnerResponse?.orderedLabels ?? [];
            const isOrdering = item.type === 'DRAG_TO_ORDER';

            return (
              <div className="card" key={`${item.questionId}-${item.snapshotVersion}`}>
                <div className="card-header">
                  <div className="row tight">
                    <Link className="mono" to={`/questions/${item.questionId}`}>
                      {item.questionReference}
                    </Link>
                    <TypeBadge type={item.type} />
                    <span className="badge badge-neutral">v{item.snapshotVersion} as delivered</span>
                    <span className="subtle">now:</span>
                    <StatusBadge status={item.currentQuestionStatus} />
                  </div>
                  <span
                    className={`badge ${item.isCorrect ? 'badge-active' : 'badge-retired'}`}
                  >
                    {item.isCorrect === null
                      ? 'NOT ANSWERED'
                      : item.isCorrect
                        ? 'CORRECT'
                        : 'INCORRECT'}
                    {' · '}
                    {item.awardedPoints ?? 0}/{item.maxPoints ?? 0}
                  </span>
                </div>
                <div className="card-body">
                  {item.currentQuestionStatus === 'RETIRED' && (
                    <div className="alert alert-info" style={{ marginBottom: 16 }}>
                      This question has since been retired. It is excluded from future quizzes, and
                      the content below is the version this learner actually saw.
                    </div>
                  )}

                  {item.scenarioText && (
                    <blockquote
                      className="pre-wrap"
                      style={{
                        margin: '0 0 16px',
                        padding: '12px 16px',
                        background: 'var(--c-surface-alt)',
                        borderLeft: '3px solid var(--c-border-strong)',
                        borderRadius: 'var(--radius)',
                      }}
                    >
                      {item.scenarioText}
                    </blockquote>
                  )}

                  <p className="pre-wrap" style={{ marginTop: 0, fontWeight: 550 }}>
                    {item.questionText}
                  </p>

                  {isOrdering ? (
                    <div className="grid-2">
                      <div>
                        <span className="field-label">Correct order</span>
                        <ol className="mono" style={{ margin: 0, paddingLeft: 22 }}>
                          {item.correctOrder.map((label) => (
                            <li key={label}>
                              {label} — {item.options.find((o) => o.label === label)?.text ?? ''}
                            </li>
                          ))}
                        </ol>
                      </div>
                      <div>
                        <span className="field-label">Learner&apos;s order</span>
                        {answerOrder.length === 0 ? (
                          <p className="subtle" style={{ margin: 0 }}>
                            No response recorded.
                          </p>
                        ) : (
                          <ol className="mono" style={{ margin: 0, paddingLeft: 22 }}>
                            {answerOrder.map((label, index) => {
                              const correct = item.correctOrder[index] === label;
                              return (
                                <li
                                  key={`${label}-${index}`}
                                  style={{ color: correct ? 'var(--c-success)' : 'var(--c-danger)' }}
                                >
                                  {label} — {item.options.find((o) => o.label === label)?.text ?? ''}
                                  {correct ? ' ✓' : ' ✗'}
                                </li>
                              );
                            })}
                          </ol>
                        )}
                        {item.presentationOrder && (
                          <div className="field-hint">
                            Shown to the learner in the order:{' '}
                            <span className="mono">{item.presentationOrder.join(' → ')}</span> —
                            recorded separately from the answer key.
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                      {item.options.map((option) => {
                        const chosen = selected.has(option.label.toUpperCase());
                        return (
                          <li
                            key={option.label}
                            className={`option-row${option.isCorrect ? ' is-correct' : ''}`}
                            style={{ marginBottom: 6 }}
                          >
                            <span className="mono" style={{ minWidth: 28 }}>
                              {option.label}
                            </span>
                            <span style={{ flex: 1 }}>{option.text}</span>
                            <span className="row tight" style={{ flexWrap: 'nowrap' }}>
                              {option.isCorrect && <span className="badge badge-active">CORRECT</span>}
                              {chosen && <span className="badge badge-type">LEARNER CHOSE</span>}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  )}

                  {item.explanation && (
                    <>
                      <div className="divider" />
                      <span className="field-label">Explanation</span>
                      <p className="pre-wrap" style={{ margin: 0 }}>
                        {item.explanation}
                      </p>
                    </>
                  )}

                  <div className="divider" />
                  <dl className="kv">
                    <dt>Topics (as recorded)</dt>
                    <dd>{item.topics.length > 0 ? item.topics.join(', ') : '—'}</dd>
                    <dt>Delivered</dt>
                    <dd>{formatDate(item.deliveredAt)}</dd>
                    <dt>Completed</dt>
                    <dd>{formatDate(item.completedAt)}</dd>
                  </dl>
                </div>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
