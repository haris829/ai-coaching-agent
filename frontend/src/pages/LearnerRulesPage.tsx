/**
 * Learner rules summary (UC-01).
 *
 * Read-only, by design. It answers "what am I in for, and may I start?" from the quiz's **active
 * configuration version** — question count, pass mark, time limit, attempts left.
 *
 * It no longer starts anything. UC-03 owns the attempt lifecycle, so **Take this quiz** hands over to
 * the attempt screen, which runs the authoritative eligibility check and creates the attempt. Two
 * screens able to create an attempt would be two implementations of the same rule; the counts shown
 * here come from UC-03 through a port, so they agree without this page owning them.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { QuizRules, QuizSummary } from '../api/types';
import { ErrorSummary, Spinner } from '../components/ui';
import { QUESTION_TYPE_LABELS } from '../lib/configurationRules';

const BLOCKED_EXPLANATIONS: Record<string, string> = {
  attempt_in_progress: 'You already have an attempt in progress. Resume it instead of starting a new one.',
  attempt_limit_reached: 'You have used all of your permitted attempts for this quiz.',
  question_bank_insufficient:
    'The question bank can no longer satisfy this quiz’s configuration, so it cannot be started. An administrator needs to add questions or change the configuration.',
};

export function LearnerRulesPage(): ReactNode {
  const [quizzes, setQuizzes] = useState<QuizSummary[]>([]);
  const [quizId, setQuizId] = useState<number | null>(null);
  const [rules, setRules] = useState<QuizRules | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await api.listQuizzes();
        if (cancelled) return;
        setQuizzes(response.quizzes);
        setQuizId(response.quizzes[0]?.id ?? null);
      } catch (cause) {
        if (!cancelled) setError(cause);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadRules = useCallback(async (id: number) => {
    setError(null);
    try {
      setRules(await api.quizRules(id));
    } catch (cause) {
      setRules(null);
      setError(cause);
    }
  }, []);

  useEffect(() => {
    if (quizId !== null) void loadRules(quizId);
  }, [quizId, loadRules]);

  if (loading) return <Spinner label="Loading quizzes…" />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Quiz rules</h1>
          <p>
            Everything below comes from the quiz’s <strong>active configuration version</strong>.
            Viewing this screen creates nothing — <strong>Start quiz</strong> is the only action
            that begins an attempt, and that attempt stays on the version it started with.
          </p>
        </div>
      </div>

      {quizzes.length > 0 && (
        <div className="card">
          <div className="card-body">
            <div className="field">
              <label htmlFor="learner-quiz">Quiz</label>
              <select
                id="learner-quiz"
                value={quizId ?? ''}
                onChange={(event) => setQuizId(Number(event.target.value))}
              >
                {quizzes.map((quiz) => (
                  <option key={quiz.id} value={quiz.id}>
                    {quiz.courseTitle} — {quiz.title}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}

      <ErrorSummary error={error} />

      {rules && (
        <>
          <div className="card">
            <div className="card-header">
              <h2>{rules.quiz.title}</h2>
              <span className="badge badge-neutral">
                configuration v{rules.configurationVersionNumber}
              </span>
            </div>
            <div className="card-body">
              <div className="kv">
                <div className="stat">
                  <span className="stat-label">Questions</span>
                  <span className="stat-value">{rules.questionCount}</span>
                </div>
                <div className="stat">
                  <span className="stat-label">Pass mark</span>
                  <span className="stat-value">{rules.passMark}%</span>
                </div>
                <div className="stat">
                  <span className="stat-label">Time limit</span>
                  <span className="stat-value">
                    {rules.timeLimitMinutes === null ? 'None' : `${rules.timeLimitMinutes} min`}
                  </span>
                </div>
                <div className="stat">
                  <span className="stat-label">Attempts left</span>
                  <span className="stat-value">
                    {rules.remainingAttempts} of {rules.maxAttempts}
                  </span>
                </div>
              </div>

              <dl className="kv" style={{ marginTop: 16 }}>
                <div>
                  <dt className="stat-label">Delivery mode</dt>
                  <dd>{rules.deliveryMode}</dd>
                </div>
                <div>
                  <dt className="stat-label">Question order</dt>
                  <dd>{rules.randomiseQuestions ? 'Randomised' : 'Fixed'}</dd>
                </div>
                <div>
                  <dt className="stat-label">Question types</dt>
                  <dd>
                    {rules.questionTypes
                      .map(
                        (entry) =>
                          `${QUESTION_TYPE_LABELS[entry.type]}${entry.quota === null ? '' : ` ×${entry.quota}`}`,
                      )
                      .join(', ')}
                  </dd>
                </div>
                <div>
                  <dt className="stat-label">Topic scope</dt>
                  <dd>
                    {rules.topics.length === 0
                      ? 'Whole question bank'
                      : rules.topics.map((topic) => topic.name).join(', ')}
                  </dd>
                </div>
              </dl>
            </div>
          </div>

          <div className="card">
            <div className="card-body">
              {rules.blockedReason && (
                <p className="alert alert-warning">
                  {BLOCKED_EXPLANATIONS[rules.blockedReason] ?? rules.blockedReason}
                </p>
              )}
              <div className="row" style={{ gap: 8 }}>
                <Link className="btn btn-primary" to="/attempt">
                  {rules.attemptInProgress ? 'Resume your attempt' : 'Take this quiz'}
                </Link>
              </div>
              <p className="field-hint">
                Attempts used: {rules.attemptsUsed}. Abandoned and expired attempts still count. The
                attempt screen runs the authoritative eligibility check and is the only thing that can
                create an attempt.
              </p>
            </div>
          </div>
        </>
      )}

    </div>
  );
}
