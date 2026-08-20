/**
 * Analytics and reporting (UC-10).
 *
 * Every number on this screen is computed by the backend from the rows UC-03, UC-04 and UC-05
 * wrote. Nothing is averaged, rated or totalled here. That restraint is the point: a dashboard that
 * recomputed a pass rate client-side would be a second implementation of the same arithmetic, and
 * the two would eventually disagree — with no way for a reader to tell which was wrong.
 *
 * The one piece of judgement this page does exercise is **refusing to render a measurement that
 * does not exist**. `data_state` distinguishes "no attempts" from "attempts, average zero", and
 * every rate is nullable for that reason. Printing `0%` for an unsat quiz would tell an
 * administrator every learner failed. So a null rate renders as "—" and the empty state says so in
 * words.
 *
 * CSV exports are plain links rather than fetches, so the browser's own download handling applies
 * and a large export never has to fit in this page's memory.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import { analytics, api } from '../api/client';
import type {
  AnalyticsFilters,
  FlaggedQuestions,
  OverallAnalytics,
  QuestionAnalyticsPage,
  ReviewActionType,
} from '../api/deploymentTypes';
import type { QuizSummary } from '../api/types';
import { ErrorSummary, Spinner, formatDate, useToast } from '../components/ui';
import { useRole } from '../lib/useRole';

const REVIEW_ACTIONS: { value: ReviewActionType; label: string }[] = [
  { value: 'NO_CHANGE', label: 'Reviewed — no change needed' },
  { value: 'QUESTION_UPDATED', label: 'Question updated' },
  { value: 'QUESTION_RETIRED', label: 'Question retired' },
];

/** A measurement, or an explicit dash. Never a zero standing in for "unknown". */
function metric(value: number | null | undefined, suffix = ''): string {
  return value === null || value === undefined ? '—' : `${value}${suffix}`;
}

export function AnalyticsPage(): ReactNode {
  const toast = useToast();
  const { role, loading: roleLoading } = useRole();
  const [quizzes, setQuizzes] = useState<QuizSummary[]>([]);
  const [courseId, setCourseId] = useState('');
  const [cohortId, setCohortId] = useState('');
  const [assessmentType, setAssessmentType] = useState('');
  const [overall, setOverall] = useState<OverallAnalytics | null>(null);
  const [questions, setQuestions] = useState<QuestionAnalyticsPage | null>(null);
  const [flagged, setFlagged] = useState<FlaggedQuestions | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const filters = useMemo<AnalyticsFilters>(
    () => ({
      course_id: courseId || undefined,
      cohort_id: cohortId || undefined,
      assessment_type:
        assessmentType === 'STANDARD_QUIZ' || assessmentType === 'FORMAL_ASSESSMENT'
          ? assessmentType
          : undefined,
    }),
    [courseId, cohortId, assessmentType],
  );

  useEffect(() => {
    (async () => {
      try {
        const response = await api.listQuizzes();
        setQuizzes(response.quizzes);
      } catch {
        // The course picker is a convenience; analytics works without it.
      }
    })();
  }, []);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    // Requested together, reported independently: the flagged panel legitimately fails on a
    // deployment where no question has enough responses to be assessed yet, and that must not
    // blank out the dashboard beside it.
    const [overallResult, questionsResult, flaggedResult] = await Promise.allSettled([
      analytics.overall(filters),
      analytics.questions({ ...filters, limit: 50 }),
      analytics.flagged(filters),
    ]);
    setOverall(overallResult.status === 'fulfilled' ? overallResult.value : null);
    setQuestions(questionsResult.status === 'fulfilled' ? questionsResult.value : null);
    setFlagged(flaggedResult.status === 'fulfilled' ? flaggedResult.value : null);
    if (overallResult.status === 'rejected') setError(overallResult.reason);
    setLoading(false);
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  const evaluate = useCallback(async () => {
    setBusy(true);
    try {
      await analytics.evaluateFlags(filters);
      toast.success('Flags recalculated from current data.');
      await load();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }, [filters, load, toast]);

  const recordAction = useCallback(
    async (questionId: string, action: ReviewActionType) => {
      setBusy(true);
      try {
        await analytics.recordReviewAction(questionId, action, 'Recorded from the test UI.');
        toast.success('Review action recorded. The audit trail is append-only.');
        await load();
      } catch (cause) {
        setError(cause);
      } finally {
        setBusy(false);
      }
    },
    [load, toast],
  );

  const courses = useMemo(() => {
    const seen = new Map<string, string>();
    for (const quiz of quizzes) {
      seen.set(String(quiz.courseId), quiz.courseTitle);
    }
    return [...seen.entries()];
  }, [quizzes]);

  if (roleLoading) return <Spinner label="Checking your role…" />;

  if (role !== 'admin') {
    return (
      <div className="page">
        <div className="page-header">
          <h1>Analytics</h1>
        </div>
        <div className="card">
          <div className="card-body">
            <p className="alert alert-warning">
              Analytics is an administrator capability end to end — every endpoint reads aggregate
              data across learners, and none of it is learner-facing. Switch to the{' '}
              <strong>administrator</strong> identity in the top bar.
            </p>
            <p className="field-hint">
              This panel is hidden as a courtesy, not as a control: the endpoints refuse a
              non-administrator credential themselves, with 403.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Analytics and reporting</h1>
          <p>
            Aggregate figures over the attempts, scores and outcomes the system has recorded. Every
            number here is the backend’s; this page renders them and recomputes nothing.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-body">
          <div className="row" style={{ gap: 12, flexWrap: 'wrap' }}>
            <div className="field">
              <label htmlFor="an-course">Course</label>
              <select
                id="an-course"
                value={courseId}
                onChange={(event) => setCourseId(event.target.value)}
              >
                <option value="">All courses (platform level)</option>
                {courses.map(([id, title]) => (
                  <option key={id} value={id}>
                    {title}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="an-cohort">Cohort</label>
              <input
                id="an-cohort"
                value={cohortId}
                onChange={(event) => setCohortId(event.target.value)}
                placeholder="e.g. cohort-a"
              />
            </div>
            <div className="field">
              <label htmlFor="an-type">Assessment type</label>
              <select
                id="an-type"
                value={assessmentType}
                onChange={(event) => setAssessmentType(event.target.value)}
              >
                <option value="">Both</option>
                <option value="STANDARD_QUIZ">Standard quiz</option>
                <option value="FORMAL_ASSESSMENT">Formal assessment</option>
              </select>
            </div>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <a className="btn" href={analytics.exportHref('overall', filters)}>
              Export summary CSV
            </a>
            <a className="btn" href={analytics.exportHref('questions', filters)}>
              Export questions CSV
            </a>
            <a className="btn" href={analytics.exportHref('flagged-questions', filters)}>
              Export flagged CSV
            </a>
          </div>
          <p className="field-hint">
            The assessment-type filter reads UC-09’s flag on the attempt itself, so a formal sitting
            is counted as one because it was one — not because of how it was labelled here.
          </p>
        </div>
      </div>

      <ErrorSummary error={error} />

      {loading ? (
        <Spinner label="Loading analytics…" />
      ) : (
        <>
          {overall && (
            <div className="card">
              <div className="card-header">
                <h2>Summary</h2>
                <span
                  className={
                    overall.data_state === 'OK' ? 'badge badge-success' : 'badge badge-neutral'
                  }
                >
                  {overall.data_state}
                </span>
              </div>
              <div className="card-body">
                {overall.data_state !== 'OK' && (
                  <p className="alert alert-warning">
                    No attempts match these filters. The rates below are shown as “—” rather than
                    0% on purpose: nobody has sat this, which is a different statement from
                    everybody having failed.
                  </p>
                )}
                <div className="kv">
                  <div className="stat">
                    <span className="stat-label">Attempts</span>
                    <span className="stat-value">{overall.attempt_volume}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Completed</span>
                    <span className="stat-value">{overall.completed_attempts}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Scored</span>
                    <span className="stat-value">{overall.scored_attempts}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Passed</span>
                    <span className="stat-value">{overall.passed_attempts}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Learners</span>
                    <span className="stat-value">{overall.unique_learners}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Average score</span>
                    <span className="stat-value">{metric(overall.average_score, '%')}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Pass rate</span>
                    <span className="stat-value">{metric(overall.pass_rate, '%')}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Completion rate</span>
                    <span className="stat-value">{metric(overall.completion_rate, '%')}</span>
                  </div>
                </div>
                <p className="field-hint">Calculated {formatDate(overall.calculated_at)}.</p>
              </div>
            </div>
          )}

          {questions && (
            <div className="card">
              <div className="card-header">
                <h2>Question performance</h2>
                <span className="badge badge-neutral">{questions.items.length} questions</span>
              </div>
              <div className="card-body">
                {questions.items.length === 0 ? (
                  <div className="empty">No graded responses yet.</div>
                ) : (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Question</th>
                        <th>Type</th>
                        <th>Graded</th>
                        <th>Accuracy</th>
                        <th>Wrong-answer rate</th>
                        <th>Most common wrong answer</th>
                        <th>Avg time</th>
                        <th>Flag</th>
                      </tr>
                    </thead>
                    <tbody>
                      {questions.items.map((item) => (
                        <tr key={item.question_id}>
                          <td>
                            <code>{item.question_id.slice(0, 8)}</code>
                          </td>
                          {/* The label the backend renders — "Single choice", not the enum name.
                              Mapping it here would be a second vocabulary for the same thing. */}
                          <td>{item.question_type_label}</td>
                          <td>{item.graded_count}</td>
                          <td>{metric(item.accuracy_percentage, '%')}</td>
                          <td>{metric(item.wrong_answer_rate, '%')}</td>
                          <td>{item.most_frequent_wrong_answer ?? '—'}</td>
                          <td>{metric(item.average_time_seconds, 's')}</td>
                          <td>
                            {item.is_flagged ? (
                              <span className="badge badge-danger">flagged</span>
                            ) : item.meets_flag_criteria ? (
                              <span className="badge badge-warning">would flag</span>
                            ) : (
                              '—'
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          <div className="card">
            <div className="card-header">
              <h2>Questions flagged for review</h2>
              {flagged && (
                <span className="badge badge-neutral">
                  above {flagged.threshold_used}% wrong, min {flagged.min_responses_required}{' '}
                  responses
                </span>
              )}
            </div>
            <div className="card-body">
              <div className="row" style={{ gap: 8, marginBottom: 12 }}>
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void evaluate()}
                >
                  {busy ? 'Recalculating…' : 'Recalculate flags'}
                </button>
              </div>
              {!flagged || flagged.items.length === 0 ? (
                <div className="empty">
                  Nothing flagged. A question is only assessed once it has enough graded responses
                  to judge — one wrong answer is not evidence of a bad question.
                </div>
              ) : (
                <table className="table">
                  <thead>
                    <tr>
                      <th>Question</th>
                      <th>Type</th>
                      <th>Wrong-answer rate</th>
                      <th>Graded</th>
                      <th>Record a review action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {flagged.items.map((item) => (
                      <tr key={item.question_id}>
                        <td>
                          <code>{item.question_id.slice(0, 8)}</code>
                        </td>
                        <td>{item.question_type_label}</td>
                        <td>{metric(item.wrong_answer_rate, '%')}</td>
                        <td>{item.graded_count}</td>
                        <td>
                          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                            {REVIEW_ACTIONS.map((action) => (
                              <button
                                key={action.value}
                                type="button"
                                className="btn btn-sm"
                                disabled={busy}
                                onClick={() => void recordAction(item.question_id, action.value)}
                              >
                                {action.label}
                              </button>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="field-hint">
                A flag clears only through a recorded review action, and the record cannot be edited
                afterwards — the audit table rejects any UPDATE at the database level. Retiring a
                question is terminal.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
