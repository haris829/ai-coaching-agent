/**
 * Quiz configuration screen (UC-01).
 *
 * Saving creates a NEW immutable version — nothing is ever edited in place. The form validates
 * with the mirrored rules for instant feedback and shows a live capacity report from the question
 * bank, but the backend is the gate: its field errors are rendered inline next to the inputs that
 * caused them.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import { ApiError, api } from '../api/client';
import { QUESTION_TYPES } from '../api/types';
import type {
  CapacityReport,
  ConfigurationVersion,
  QuestionType,
  QuestionTypeSelection,
  QuizConfigurationInput,
  QuizSummary,
  Topic,
} from '../api/types';
import { ErrorSummary, Spinner, formatDate, useToast } from '../components/ui';
import {
  DELIVERY_MODE_LABELS,
  DELIVERY_MODES,
  QUESTION_TYPE_LABELS,
  evaluateCapacity,
  toFormInput,
  validateQuizConfiguration,
} from '../lib/configurationRules';

const EMPTY_FORM: QuizConfigurationInput = {
  questionCount: 10,
  timeLimitMinutes: 30,
  passMark: 60,
  maxAttempts: 3,
  deliveryMode: 'assessment',
  randomiseQuestions: false,
  questionTypes: [
    { type: 'SINGLE_CHOICE', quota: null },
    { type: 'TRUE_FALSE', quota: null },
  ],
  topicIds: [],
};

export function QuizConfigurationPage(): ReactNode {
  const toast = useToast();

  const [quizzes, setQuizzes] = useState<QuizSummary[]>([]);
  const [quizId, setQuizId] = useState<number | null>(null);
  const [active, setActive] = useState<ConfigurationVersion | null>(null);
  const [versions, setVersions] = useState<ConfigurationVersion[]>([]);
  const [availability, setAvailability] = useState<Record<string, number>>({});
  const [serverCapacity, setServerCapacity] = useState<CapacityReport | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);

  const [form, setForm] = useState<QuizConfigurationInput>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);

  // --- loading -------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [quizList, topicList] = await Promise.all([api.listQuizzes(), api.listTopics()]);
        if (cancelled) return;
        setQuizzes(quizList.quizzes);
        setTopics(topicList);
        setQuizId(quizList.quizzes[0]?.id ?? null);
      } catch (cause) {
        if (!cancelled) setLoadError(cause);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadQuiz = useCallback(
    async (id: number) => {
      setLoadError(null);
      try {
        const [configuration, history] = await Promise.all([
          api.getConfiguration(id),
          api.listConfigurationVersions(id),
        ]);
        setActive(configuration.configuration);
        setServerCapacity(configuration.capacity);
        setVersions(history.versions);
        setForm(configuration.configuration ? toFormInput(configuration.configuration) : EMPTY_FORM);
      } catch (cause) {
        setLoadError(cause);
      }
    },
    [],
  );

  useEffect(() => {
    if (quizId !== null) void loadQuiz(quizId);
  }, [quizId, loadQuiz]);

  // Availability depends on the topic scope, so it reloads when the scope changes.
  useEffect(() => {
    if (quizId === null) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await api.questionBankAvailability(quizId, form.topicIds ?? []);
        if (!cancelled) setAvailability(response.availableByType);
      } catch {
        if (!cancelled) setAvailability({});
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [quizId, JSON.stringify(form.topicIds ?? [])]);

  // --- derived state -------------------------------------------------------

  const validation = useMemo(() => validateQuizConfiguration(form), [form]);

  /** Pre-save capacity warning, computed with the same arithmetic the server uses. */
  const localCapacity = useMemo(() => {
    if (!validation.value) return null;
    return evaluateCapacity(
      { questionCount: validation.value.questionCount, questionTypes: validation.value.questionTypes },
      availability,
    );
  }, [validation.value, availability]);

  const serverErrors = useMemo(() => saveError?.byField() ?? new Map<string, string[]>(), [saveError]);

  function errorFor(field: string): string | null {
    const fromServer = serverErrors.get(field);
    if (fromServer?.length) return fromServer.join(' ');
    // Local errors are only shown once the administrator has tried to save, to avoid shouting at a
    // half-filled form.
    if (!saveError) return null;
    const local = validation.errors.filter((error) => error.field === field);
    return local.length ? local.map((error) => error.message).join(' ') : null;
  }

  const selectedTypes = new Set(form.questionTypes.map((entry) => entry.type));
  const usesQuotas =
    form.questionTypes.length > 0 && form.questionTypes.every((entry) => entry.quota !== null);

  // --- editing -------------------------------------------------------------

  function update(patch: Partial<QuizConfigurationInput>): void {
    setForm((current) => ({ ...current, ...patch }));
  }

  function toggleType(type: QuestionType): void {
    setForm((current) => {
      const exists = current.questionTypes.some((entry) => entry.type === type);
      const next: QuestionTypeSelection[] = exists
        ? current.questionTypes.filter((entry) => entry.type !== type)
        : [...current.questionTypes, { type, quota: usesQuotas ? 1 : null }];
      return { ...current, questionTypes: next };
    });
  }

  function setQuota(type: QuestionType, raw: string): void {
    setForm((current) => ({
      ...current,
      questionTypes: current.questionTypes.map((entry) =>
        entry.type === type
          ? { ...entry, quota: raw.trim() === '' ? null : Number(raw) }
          : entry,
      ),
    }));
  }

  function setQuotaMode(enabled: boolean): void {
    setForm((current) => ({
      ...current,
      questionTypes: current.questionTypes.map((entry) => ({
        ...entry,
        quota: enabled ? (entry.quota ?? 1) : null,
      })),
    }));
  }

  function toggleTopic(topicId: string): void {
    setForm((current) => {
      const scope = current.topicIds ?? [];
      return {
        ...current,
        topicIds: scope.includes(topicId)
          ? scope.filter((id) => id !== topicId)
          : [...scope, topicId],
      };
    });
  }

  // --- saving --------------------------------------------------------------

  async function save(): Promise<void> {
    if (quizId === null) return;
    setSaving(true);
    setSaveError(null);
    try {
      const response = await api.saveConfiguration(quizId, form);
      if (response.created) {
        toast.success(`Version ${response.configuration.versionNumber} created.`);
      } else {
        toast.info('Nothing changed — no new version was created.');
      }
      await loadQuiz(quizId);
    } catch (cause) {
      if (cause instanceof ApiError) {
        setSaveError(cause);
        toast.error(cause.message);
      } else {
        toast.error('The configuration could not be saved.');
      }
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <Spinner label="Loading quizzes…" />;

  const quiz = quizzes.find((item) => item.id === quizId) ?? null;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Quiz configuration</h1>
          <p>
            Every meaningful change creates a new <strong>immutable version</strong>. Existing
            attempts keep running on the version that was active when they started; new attempts use
            the latest. A configuration can only be saved when the active question bank can satisfy
            it — retired questions do not count.
          </p>
        </div>
      </div>

      <ErrorSummary error={loadError} />

      {quizzes.length === 0 ? (
        <div className="empty">
          No quizzes to configure. Run <code>python -m scripts.seed</code> in the backend directory.
        </div>
      ) : (
        <>
          <div className="card">
            <div className="card-header">
              <h2>Quiz</h2>
            </div>
            <div className="card-body">
              <div className="field">
                <label htmlFor="quiz">Quiz to configure</label>
                <select
                  id="quiz"
                  value={quizId ?? ''}
                  onChange={(event) => setQuizId(Number(event.target.value))}
                >
                  {quizzes.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.courseTitle} — {item.title}
                    </option>
                  ))}
                </select>
              </div>
              {active ? (
                <p className="field-hint">
                  Active: <strong>version {active.versionNumber}</strong>, created{' '}
                  {formatDate(active.createdAt)}
                  {active.createdBy ? ` by ${active.createdBy}` : ''} ·{' '}
                  {active.attemptCount} attempt(s) locked to it.
                </p>
              ) : (
                <p className="field-hint">
                  {quiz?.title} has never been configured. The first save creates version 1.
                </p>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h2>Rules</h2>
            </div>
            <div className="card-body">
              <div className="grid-2">
                <NumberField
                  id="questionCount"
                  label="Question count"
                  required
                  value={form.questionCount}
                  error={errorFor('questionCount')}
                  onChange={(value) => update({ questionCount: value })}
                />
                <NumberField
                  id="passMark"
                  label="Pass mark (%)"
                  required
                  value={form.passMark}
                  error={errorFor('passMark')}
                  onChange={(value) => update({ passMark: value })}
                />
                <NumberField
                  id="timeLimitMinutes"
                  label="Time limit (minutes)"
                  hint="Leave empty for no limit. Required for exam delivery."
                  value={form.timeLimitMinutes ?? ''}
                  error={errorFor('timeLimitMinutes')}
                  onChange={(value) => update({ timeLimitMinutes: value === '' ? null : value })}
                />
                <NumberField
                  id="maxAttempts"
                  label="Maximum attempts"
                  required
                  value={form.maxAttempts}
                  error={errorFor('maxAttempts')}
                  onChange={(value) => update({ maxAttempts: value })}
                />
              </div>

              <div className="field">
                <label className="required" htmlFor="deliveryMode">
                  Delivery mode
                </label>
                <select
                  id="deliveryMode"
                  value={form.deliveryMode}
                  onChange={(event) =>
                    update({ deliveryMode: event.target.value as QuizConfigurationInput['deliveryMode'] })
                  }
                >
                  <option value="">Select a delivery mode…</option>
                  {DELIVERY_MODES.map((mode) => (
                    <option key={mode} value={mode}>
                      {DELIVERY_MODE_LABELS[mode]}
                    </option>
                  ))}
                </select>
                {errorFor('deliveryMode') && (
                  <p className="field-error">{errorFor('deliveryMode')}</p>
                )}
              </div>

              <div className="field">
                <label htmlFor="randomise">
                  <input
                    id="randomise"
                    type="checkbox"
                    checked={form.randomiseQuestions}
                    onChange={(event) => update({ randomiseQuestions: event.target.checked })}
                  />{' '}
                  Randomise question order
                </label>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h2>Question types</h2>
              <label>
                <input
                  type="checkbox"
                  checked={usesQuotas}
                  onChange={(event) => setQuotaMode(event.target.checked)}
                />{' '}
                Set a quota per type
              </label>
            </div>
            <div className="card-body">
              <div className="stack">
                {QUESTION_TYPES.map((type) => {
                  const selection = form.questionTypes.find((entry) => entry.type === type);
                  const available = availability[type] ?? 0;
                  return (
                    <div className="row spread" key={type}>
                      <label>
                        <input
                          type="checkbox"
                          checked={selectedTypes.has(type)}
                          onChange={() => toggleType(type)}
                        />{' '}
                        {QUESTION_TYPE_LABELS[type]}{' '}
                        <span className="muted">({available} eligible)</span>
                      </label>
                      {selection && usesQuotas && (
                        <input
                          type="number"
                          min={1}
                          style={{ maxWidth: 110 }}
                          value={selection.quota ?? ''}
                          aria-label={`Quota for ${QUESTION_TYPE_LABELS[type]}`}
                          onChange={(event) => setQuota(type, event.target.value)}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
              {errorFor('questionTypes') && (
                <p className="field-error">{errorFor('questionTypes')}</p>
              )}
              <p className="field-hint">
                Eligible counts come from the question bank and exclude retired and draft questions.
              </p>
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h2>Topic scope (optional)</h2>
            </div>
            <div className="card-body">
              {topics.length === 0 ? (
                <p className="muted">No topics yet.</p>
              ) : (
                <div className="row" style={{ flexWrap: 'wrap', gap: 12 }}>
                  {topics.map((topic) => (
                    <label key={topic.id} className="tag-static">
                      <input
                        type="checkbox"
                        checked={(form.topicIds ?? []).includes(topic.id)}
                        onChange={() => toggleTopic(topic.id)}
                      />{' '}
                      {topic.name}
                    </label>
                  ))}
                </div>
              )}
              <p className="field-hint">
                Leave empty to draw from the whole active bank. A scope is frozen onto the version,
                so renaming or deleting a topic later cannot change what a past version meant.
              </p>
              {errorFor('topicIds') && <p className="field-error">{errorFor('topicIds')}</p>}
            </div>
          </div>

          <CapacityPanel local={localCapacity} server={serverCapacity} saveError={saveError} />

          <div className="card">
            <div className="card-body row spread">
              <button
                type="button"
                className="btn btn-primary"
                onClick={save}
                disabled={saving}
              >
                {saving ? 'Saving…' : 'Save configuration'}
              </button>
              {saveError?.code === 'PERSISTENCE_FAILED' && (
                <span className="alert alert-warning">
                  Nothing was saved. <strong>Retry</strong> is safe.
                </span>
              )}
            </div>
          </div>

          <VersionHistoryCard versions={versions} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function NumberField({
  id,
  label,
  value,
  onChange,
  error,
  hint,
  required = false,
}: {
  id: string;
  label: string;
  value: number | string;
  onChange: (value: string) => void;
  error: string | null;
  hint?: string;
  required?: boolean;
}): ReactNode {
  return (
    <div className="field">
      <label className={required ? 'required' : undefined} htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="number"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && <p className="field-hint">{hint}</p>}
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}

function CapacityPanel({
  local,
  server,
  saveError,
}: {
  local: CapacityReport | null;
  server: CapacityReport | null;
  saveError: ApiError | null;
}): ReactNode {
  // The server's own report, when a save was rejected for capacity, is the authoritative one.
  const rejected =
    saveError?.code === 'QUESTION_BANK_INSUFFICIENT'
      ? saveError.extraAs<CapacityReport>('capacity')
      : null;
  const report = rejected ?? local ?? server;
  if (!report) return null;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Question-bank capacity</h2>
        <span className={report.satisfiable ? 'badge badge-active' : 'badge badge-retired'}>
          {report.satisfiable ? 'Satisfiable' : 'Not satisfiable'}
        </span>
      </div>
      <div className="card-body">
        <p className="muted">
          {report.requestedTotal} requested · {report.availableTotal} eligible
          {report.totalShortfall > 0 ? ` · ${report.totalShortfall} short` : ''}
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Requested</th>
                <th>Eligible</th>
                <th>Shortfall</th>
              </tr>
            </thead>
            <tbody>
              {report.breakdown.map((entry) => (
                <tr key={entry.type}>
                  <td>{QUESTION_TYPE_LABELS[entry.type]}</td>
                  <td>{entry.requested ?? '—'}</td>
                  <td>{entry.available}</td>
                  <td>{entry.shortfall > 0 ? entry.shortfall : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {report.messages.map((message) => (
          <p className="alert alert-warning" key={message}>
            {message}
          </p>
        ))}
      </div>
    </div>
  );
}

function VersionHistoryCard({ versions }: { versions: ConfigurationVersion[] }): ReactNode {
  return (
    <div className="card">
      <div className="card-header">
        <h2>Version history</h2>
        <span className="muted">{versions.length} version(s)</span>
      </div>
      <div className="card-body">
        {versions.length === 0 ? (
          <p className="muted">No versions yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Questions</th>
                  <th>Pass mark</th>
                  <th>Time limit</th>
                  <th>Attempts allowed</th>
                  <th>Mode</th>
                  <th>Types</th>
                  <th>Scope</th>
                  <th>Locked attempts</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((version) => (
                  <tr key={version.id}>
                    <td>
                      v{version.versionNumber}{' '}
                      {version.isActive && <span className="badge badge-active">active</span>}
                    </td>
                    <td>{version.questionCount}</td>
                    <td>{version.passMark}%</td>
                    <td>{version.timeLimitMinutes ?? 'none'}</td>
                    <td>{version.maxAttempts}</td>
                    <td>{version.deliveryMode}</td>
                    <td className="cell-sub">
                      {version.questionTypes
                        .map(
                          (entry) =>
                            `${QUESTION_TYPE_LABELS[entry.type]}${entry.quota === null ? '' : ` ×${entry.quota}`}`,
                        )
                        .join(', ')}
                    </td>
                    <td className="cell-sub">
                      {version.topics.length === 0
                        ? 'whole bank'
                        : version.topics.map((topic) => topic.name).join(', ')}
                    </td>
                    <td>{version.attemptCount}</td>
                    <td className="cell-sub">{formatDate(version.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="field-hint">
          Historical versions are read-only — the database rejects an update to any of them.
        </p>
      </div>
    </div>
  );
}
