/**
 * Add / edit question screen (UC-02 §23), covering all five types.
 *
 * A retired question opens read-only: its content is immutable (the backend enforces this too),
 * but topics can still be managed and the version history stays visible for reporting.
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import {
  ALLOWED_SCORING_STRATEGIES,
  DIFFICULTIES,
  QUESTION_TYPES,
  QUESTION_TYPE_LABELS,
  SCORING_STRATEGY_LABELS,
  type Question,
  type QuestionSnapshot,
  type QuestionType,
  type ScoringStrategy,
  type Usage,
} from '../api/types';
import { OptionsEditor } from '../components/OptionsEditor';
import { TopicPicker } from '../components/TopicPicker';
import {
  ErrorSummary,
  Modal,
  Spinner,
  StatusBadge,
  TypeBadge,
  formatDate,
  useToast,
} from '../components/ui';
import {
  changeType,
  describeClientIssues,
  emptyForm,
  fromQuestion,
  toPayload,
  type QuestionForm,
} from '../lib/questionForm';

export function QuestionFormPage(): ReactNode {
  const { id } = useParams<{ id: string }>();
  const isNew = !id || id === 'new';
  const navigate = useNavigate();
  const toast = useToast();

  const [form, setForm] = useState<QuestionForm>(() => emptyForm('SINGLE_CHOICE'));
  const [question, setQuestion] = useState<Question | null>(null);
  const [versions, setVersions] = useState<QuestionSnapshot[]>([]);
  const [usages, setUsages] = useState<Usage[]>([]);
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [showRetire, setShowRetire] = useState(false);
  const [retireReason, setRetireReason] = useState('');

  const readOnly = question?.status === 'RETIRED';

  useEffect(() => {
    if (isNew) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const loaded = await api.getQuestion(id!);
        if (cancelled) return;
        setQuestion(loaded);
        setForm(fromQuestion(loaded));
        const [snapshots, usageRows] = await Promise.all([
          api.listVersions(loaded.id).catch(() => []),
          api.listUsages(loaded.id).catch(() => []),
        ]);
        if (cancelled) return;
        setVersions(snapshots);
        setUsages(usageRows);
      } catch (cause) {
        if (!cancelled) setError(cause);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, isNew]);

  const clientIssues = useMemo(() => describeClientIssues(form), [form]);
  const fieldErrors = useMemo(
    () => (error instanceof ApiError ? error.byField() : new Map<string, string[]>()),
    [error],
  );

  const allowedStrategies = ALLOWED_SCORING_STRATEGIES[form.type];

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      const payload = toPayload(form);
      if (isNew) {
        const created = await api.createQuestion(payload);
        toast.success(`${created.reference} created.`);
        navigate(`/questions/${created.id}`, { replace: true });
        setQuestion(created);
        setForm(fromQuestion(created));
        setVersions(await api.listVersions(created.id).catch(() => []));
      } else {
        const updated = await api.updateQuestion(question!.id, payload);
        toast.success(
          updated.version > (question?.version ?? 1)
            ? `${updated.reference} saved as version ${updated.version}. Existing attempts keep the version they were delivered.`
            : `${updated.reference} saved.`,
        );
        setQuestion(updated);
        setForm(fromQuestion(updated));
        setVersions(await api.listVersions(updated.id).catch(() => []));
      }
    } catch (cause) {
      setError(cause);
      toast.error(
        cause instanceof ApiError ? cause.message : 'The question could not be saved.',
      );
    } finally {
      setSaving(false);
    }
  }

  async function retire(): Promise<void> {
    if (!question) return;
    setSaving(true);
    try {
      const updated = await api.retireQuestion(question.id, retireReason.trim());
      setQuestion(updated);
      setForm(fromQuestion(updated));
      setShowRetire(false);
      toast.success(`${updated.reference} retired — withheld from future quizzes, history intact.`);
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The question could not be retired.');
    } finally {
      setSaving(false);
    }
  }

  async function reactivate(): Promise<void> {
    if (!question) return;
    setSaving(true);
    try {
      const updated = await api.reactivateQuestion(question.id);
      setQuestion(updated);
      setForm(fromQuestion(updated));
      toast.success(`${updated.reference} is active again.`);
    } catch (cause) {
      toast.error(
        cause instanceof ApiError ? cause.message : 'The question could not be reactivated.',
      );
    } finally {
      setSaving(false);
    }
  }

  async function saveTopics(topics: string[]): Promise<void> {
    setForm((current) => ({ ...current, topics }));
    // On a retired question the form cannot be saved, so persist tag changes immediately.
    if (readOnly && question) {
      try {
        const updated = await api.assignTopics(question.id, { topicNames: topics, replace: true });
        setQuestion(updated);
        toast.success('Topics updated.');
      } catch (cause) {
        toast.error(cause instanceof ApiError ? cause.message : 'Topics could not be updated.');
      }
    }
  }

  if (loading) {
    return (
      <div className="page">
        <Spinner label="Loading question…" />
      </div>
    );
  }

  if (error && !question && !isNew) {
    return (
      <div className="page">
        <ErrorSummary error={error} />
        <Link className="btn" to="/questions" style={{ marginTop: 16 }}>
          Back to the question bank
        </Link>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>{isNew ? 'New question' : `Edit ${question?.reference}`}</h1>
          {question && (
            <div className="row tight" style={{ marginTop: 8 }}>
              <StatusBadge status={question.status} />
              <TypeBadge type={question.type} />
              <span className="subtle">
                Version {question.version}
                {question.usage ? ` · used by ${question.usage.total} attempt(s)` : ''}
              </span>
            </div>
          )}
        </div>
        <div className="row tight">
          <Link className="btn" to="/questions">
            Back to list
          </Link>
          {question && question.status !== 'RETIRED' && (
            <button type="button" className="btn" onClick={() => setShowRetire(true)}>
              Retire
            </button>
          )}
          {question && question.status === 'RETIRED' && (
            <button type="button" className="btn" onClick={reactivate} disabled={saving}>
              Reactivate
            </button>
          )}
          {!readOnly && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={save}
              disabled={saving || clientIssues.length > 0}
              title={clientIssues.length > 0 ? 'Resolve the outstanding items first' : undefined}
            >
              {saving ? 'Saving…' : isNew ? 'Create question' : 'Save changes'}
            </button>
          )}
        </div>
      </div>

      {readOnly && (
        <div className="alert alert-warning" style={{ marginBottom: 16 }}>
          <strong>This question is retired and its content is read-only.</strong>
          It is excluded from every future quiz but remains fully available for historical
          reporting. Topics can still be managed. Reactivate it to edit the content again.
        </div>
      )}

      {question && question.usage?.hasHistory && !readOnly && (
        <div className="alert alert-info" style={{ marginBottom: 16 }}>
          <strong>
            This question has been used by {question.usage.total} attempt(s) ({question.usage.completed}{' '}
            completed).
          </strong>
          Saving a content change creates a new version. Attempts already recorded stay pinned to
          the version they were delivered, so existing reports are unaffected.
        </div>
      )}

      {error ? <div style={{ marginBottom: 16 }}><ErrorSummary error={error} /></div> : null}

      <div className="card">
        <div className="card-header">
          <h2>Question</h2>
        </div>
        <div className="card-body">
          <div className="grid-2">
            <div className="field">
              <label className="required" htmlFor="q-type">
                Question type
              </label>
              <select
                id="q-type"
                value={form.type}
                disabled={readOnly}
                onChange={(event) =>
                  setForm((current) => changeType(current, event.target.value as QuestionType))
                }
              >
                {QUESTION_TYPES.map((value) => (
                  <option key={value} value={value}>
                    {QUESTION_TYPE_LABELS[value]}
                  </option>
                ))}
              </select>
              <div className="field-hint">
                Changing the type re-shapes the answer options for the new type.
              </div>
            </div>

            <div className="field">
              <label htmlFor="q-difficulty">Difficulty</label>
              <select
                id="q-difficulty"
                value={form.difficulty}
                disabled={readOnly}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    difficulty: event.target.value as QuestionForm['difficulty'],
                  }))
                }
              >
                <option value="">Not set</option>
                {DIFFICULTIES.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {form.type === 'SCENARIO' && (
            <div className={`field${fieldErrors.has('scenarioText') ? ' field-error' : ''}`}>
              <label className="required" htmlFor="q-scenario">
                Scenario / vignette
              </label>
              <textarea
                id="q-scenario"
                rows={6}
                value={form.scenarioText}
                disabled={readOnly}
                placeholder="Describe the situation the learner must reason about…"
                onChange={(event) =>
                  setForm((current) => ({ ...current, scenarioText: event.target.value }))
                }
              />
              <div className="field-hint">
                Shown before the question. At least 20 characters.
              </div>
              {fieldErrors.get('scenarioText')?.map((message) => (
                <div className="inline-error" key={message}>
                  {message}
                </div>
              ))}
            </div>
          )}

          <div className={`field${fieldErrors.has('questionText') ? ' field-error' : ''}`}>
            <label className="required" htmlFor="q-text">
              Question text
            </label>
            <textarea
              id="q-text"
              rows={3}
              value={form.questionText}
              disabled={readOnly}
              onChange={(event) =>
                setForm((current) => ({ ...current, questionText: event.target.value }))
              }
            />
            {fieldErrors.get('questionText')?.map((message) => (
              <div className="inline-error" key={message}>
                {message}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Answers</h2>
        </div>
        <div className="card-body">
          <OptionsEditor
            form={form}
            onChange={(next) => setForm(next)}
            disabled={readOnly}
          />
          {[...fieldErrors.entries()]
            .filter(([field]) => field.startsWith('options'))
            .map(([field, messages]) =>
              messages.map((message) => (
                <div className="inline-error" key={`${field}-${message}`}>
                  {message}
                </div>
              )),
            )}
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Explanation, topics and scoring</h2>
        </div>
        <div className="card-body">
          <div className={`field${fieldErrors.has('explanation') ? ' field-error' : ''}`}>
            <label className="required" htmlFor="q-explanation">
              Explanation
            </label>
            <textarea
              id="q-explanation"
              rows={3}
              value={form.explanation}
              disabled={readOnly}
              placeholder="Why the correct answer is correct."
              onChange={(event) =>
                setForm((current) => ({ ...current, explanation: event.target.value }))
              }
            />
            {fieldErrors.get('explanation')?.map((message) => (
              <div className="inline-error" key={message}>
                {message}
              </div>
            ))}
          </div>

          <div className={`field${fieldErrors.has('topics') ? ' field-error' : ''}`}>
            <span className="field-label required">Topics</span>
            <TopicPicker selected={form.topics} onChange={saveTopics} />
            {fieldErrors.get('topics')?.map((message) => (
              <div className="inline-error" key={message}>
                {message}
              </div>
            ))}
          </div>

          <div className="divider" />

          <div className="grid-3">
            <div className={`field${fieldErrors.has('scoring.points') ? ' field-error' : ''}`}>
              <label className="required" htmlFor="q-points">
                Points
              </label>
              <input
                id="q-points"
                type="number"
                min="0.1"
                step="0.5"
                value={form.points}
                disabled={readOnly}
                onChange={(event) => setForm((current) => ({ ...current, points: event.target.value }))}
              />
            </div>

            <div className="field">
              <label className="required" htmlFor="q-strategy">
                Scoring strategy
              </label>
              <select
                id="q-strategy"
                value={form.scoringStrategy}
                disabled={readOnly || allowedStrategies.length === 1}
                onChange={(event) => {
                  const scoringStrategy = event.target.value as ScoringStrategy;
                  setForm((current) => ({
                    ...current,
                    scoringStrategy,
                    penaltyPerIncorrect:
                      scoringStrategy === 'PARTIAL_CREDIT_WITH_PENALTY'
                        ? current.penaltyPerIncorrect
                        : '',
                  }));
                }}
              >
                {allowedStrategies.map((value) => (
                  <option key={value} value={value}>
                    {SCORING_STRATEGY_LABELS[value]}
                  </option>
                ))}
              </select>
              {allowedStrategies.length === 1 && (
                <div className="field-hint">
                  {QUESTION_TYPE_LABELS[form.type]} questions are always all-or-nothing.
                </div>
              )}
            </div>

            {form.scoringStrategy === 'PARTIAL_CREDIT_WITH_PENALTY' && (
              <div
                className={`field${fieldErrors.has('scoring.penaltyPerIncorrect') ? ' field-error' : ''}`}
              >
                <label className="required" htmlFor="q-penalty">
                  Penalty per incorrect
                </label>
                <input
                  id="q-penalty"
                  type="number"
                  min="0"
                  step="0.25"
                  value={form.penaltyPerIncorrect}
                  disabled={readOnly}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, penaltyPerIncorrect: event.target.value }))
                  }
                />
              </div>
            )}
          </div>

          <div className="grid-2" style={{ marginTop: 16 }}>
            <div className="field">
              <label htmlFor="q-external">External reference</label>
              <input
                id="q-external"
                type="text"
                value={form.externalRef}
                disabled={readOnly}
                placeholder="Optional key from your source system"
                onChange={(event) =>
                  setForm((current) => ({ ...current, externalRef: event.target.value }))
                }
              />
            </div>
            {isNew && (
              <div className="field">
                <label htmlFor="q-status">Initial status</label>
                <select
                  id="q-status"
                  value={form.status}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      status: event.target.value as QuestionForm['status'],
                    }))
                  }
                >
                  <option value="ACTIVE">Active — available to future quizzes</option>
                  <option value="DRAFT">Draft — not delivered</option>
                </select>
              </div>
            )}
          </div>
        </div>
      </div>

      {!readOnly && clientIssues.length > 0 && (
        <div className="alert alert-warning" style={{ marginTop: 16 }}>
          <strong>Before this can be saved:</strong>
          <ul>
            {clientIssues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      )}

      {question && versions.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h2>Version history</h2>
            <span className="subtle">
              Each version is frozen; attempts report the version they were delivered.
            </span>
          </div>
          <div className="card-body tight">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Version</th>
                    <th className="cell-main">Question text at that version</th>
                    <th>Points</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((snapshot) => (
                    <tr key={snapshot.id}>
                      <td className="mono">
                        v{snapshot.version}
                        {snapshot.version === question.version && (
                          <div className="cell-sub">current</div>
                        )}
                      </td>
                      <td className="cell-main pre-wrap">{snapshot.questionText}</td>
                      <td className="subtle">{snapshot.points}</td>
                      <td className="subtle">{formatDate(snapshot.createdAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {usages.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h2>Attempt history</h2>
            <span className="subtle">These references survive retirement.</span>
          </div>
          <div className="card-body tight">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Attempt</th>
                    <th>Learner</th>
                    <th>Version delivered</th>
                    <th>Status</th>
                    <th>Result</th>
                    <th>Delivered</th>
                  </tr>
                </thead>
                <tbody>
                  {usages.map((usage) => (
                    <tr key={usage.id}>
                      <td>
                        <Link className="mono" to={`/reports/${encodeURIComponent(usage.attemptRef)}`}>
                          {usage.attemptRef}
                        </Link>
                      </td>
                      <td className="subtle">{usage.learnerRef ?? '—'}</td>
                      <td className="mono">v{usage.snapshotVersion}</td>
                      <td>
                        <span className="badge badge-neutral">{usage.attemptStatus}</span>
                      </td>
                      <td className="subtle">
                        {usage.isCorrect === null
                          ? '—'
                          : `${usage.isCorrect ? 'Correct' : 'Incorrect'} · ${usage.awardedPoints ?? 0}/${usage.maxPoints ?? 0}`}
                      </td>
                      <td className="subtle">{formatDate(usage.deliveredAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {showRetire && question && (
        <Modal
          title={`Retire ${question.reference}?`}
          onClose={() => setShowRetire(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setShowRetire(false)} disabled={saving}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" onClick={retire} disabled={saving}>
                {saving ? 'Retiring…' : 'Retire question'}
              </button>
            </>
          }
        >
          <div className="alert alert-info" style={{ marginBottom: 16 }}>
            <strong>The question is preserved, not deleted.</strong>
            It keeps its identity and every completed attempt continues to report it in full. It
            simply stops being offered to new quizzes.
          </div>
          <label className="field-label" htmlFor="retire-reason-detail">
            Reason (optional)
          </label>
          <textarea
            id="retire-reason-detail"
            value={retireReason}
            placeholder="e.g. Superseded by the 2026 syllabus"
            onChange={(event) => setRetireReason(event.target.value)}
          />
        </Modal>
      )}
    </div>
  );
}
