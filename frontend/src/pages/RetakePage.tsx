/**
 * Retakes and attempt history (UC-08).
 *
 * Two questions, one screen: *may I sit this again*, and *what happened the last times*. Both are
 * answered entirely by the backend — this page renders `state`, `allowance` and `entries` and
 * derives nothing. A client that counted attempts for itself would eventually tell a learner they
 * had one left and then watch the server refuse them.
 *
 * The administrator panel is on the same screen deliberately. Granting an extra attempt is the
 * remedy for the state the learner is looking at, and seeing the eligibility that motivated it
 * beside the grant that fixes it is how the demo makes the relationship legible. It is only
 * rendered for an administrator; a learner cannot see it, and could not use it if they could.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { api, retakes } from '../api/client';
import type { AttemptHistory, RetakeEligibility } from '../api/deploymentTypes';
import type { QuizSummary } from '../api/types';
import { ErrorSummary, Spinner, formatDate, useToast } from '../components/ui';
import { useRole } from '../lib/useRole';

/** Plain-English readings of UC-08's three states. The distinctions are the requirement. */
const STATE_LABELS: Record<string, string> = {
  ELIGIBLE: 'You can retake this quiz',
  ADDITIONAL_ATTEMPT_AVAILABLE: 'An administrator has granted you another attempt',
  EXHAUSTED: 'You have used all of your attempts',
};

function stateClass(state: string): string {
  if (state === 'EXHAUSTED') return 'badge badge-danger';
  if (state === 'ADDITIONAL_ATTEMPT_AVAILABLE') return 'badge badge-warning';
  return 'badge badge-success';
}

export function RetakePage(): ReactNode {
  const toast = useToast();
  const [quizzes, setQuizzes] = useState<QuizSummary[]>([]);
  const [quizId, setQuizId] = useState<number | null>(null);
  const [eligibility, setEligibility] = useState<RetakeEligibility | null>(null);
  const [history, setHistory] = useState<AttemptHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const { role } = useRole();
  const isAdmin = role === 'admin';

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

  const load = useCallback(async (id: number) => {
    setError(null);
    // Requested together but reported separately: an administrator has no retake eligibility of
    // their own (the endpoint is learner-scoped and refuses them), and that refusal must not blank
    // out the history, which is the half they *can* see.
    const [eligibilityResult, historyResult] = await Promise.allSettled([
      retakes.eligibility(id),
      retakes.history(id),
    ]);
    setEligibility(eligibilityResult.status === 'fulfilled' ? eligibilityResult.value : null);
    setHistory(historyResult.status === 'fulfilled' ? historyResult.value : null);
    if (eligibilityResult.status === 'rejected' && historyResult.status === 'rejected') {
      setError(eligibilityResult.reason);
    }
  }, []);

  useEffect(() => {
    if (quizId !== null) void load(quizId);
  }, [quizId, load]);

  const requestRetake = useCallback(async () => {
    if (quizId === null) return;
    setBusy(true);
    setError(null);
    try {
      const created = await retakes.create(quizId);
      const plan = created.question_plan;
      const fresh = plan ? `${plan.expected_fresh_questions} of ${plan.required_count} unseen` : '';
      toast.success(
        `Attempt ${created.attempt.attempt_number} created${fresh ? ` — ${fresh}` : ''}. Go to “Take a quiz”.`,
      );
      await load(quizId);
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }, [quizId, load, toast]);

  if (loading) return <Spinner label="Loading quizzes…" />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Retakes and attempt history</h1>
          <p>
            Whether a retake is allowed is decided by the backend from three things: how many
            attempts you have used, the maximum on the configuration version your attempts locked,
            and any additional attempts an administrator has granted. A retake is a{' '}
            <strong>new, independent attempt</strong> — it draws a fresh paper where the question
            bank allows, and it cannot change what an earlier attempt recorded.
          </p>
        </div>
      </div>

      {quizzes.length > 0 && (
        <div className="card">
          <div className="card-body">
            <div className="field">
              <label htmlFor="retake-quiz">Quiz</label>
              <select
                id="retake-quiz"
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

      {eligibility && (
        <div className="card">
          <div className="card-header">
            <h2>Eligibility</h2>
            <span className={stateClass(eligibility.state)}>
              {STATE_LABELS[eligibility.state] ?? eligibility.state}
            </span>
          </div>
          <div className="card-body">
            <div className="kv">
              <div className="stat">
                <span className="stat-label">Attempts used</span>
                <span className="stat-value">{eligibility.allowance.attempts_used}</span>
              </div>
              <div className="stat">
                <span className="stat-label">Configured maximum</span>
                <span className="stat-value">
                  {eligibility.allowance.maximum_attempts ?? 'Unlimited'}
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Granted</span>
                <span className="stat-value">{eligibility.allowance.granted_attempts}</span>
              </div>
              <div className="stat">
                <span className="stat-label">Available now</span>
                <span className="stat-value">
                  {eligibility.allowance.unlimited
                    ? 'Unlimited'
                    : (eligibility.allowance.available_attempts ?? 0)}
                </span>
              </div>
            </div>

            {eligibility.allowance.relies_on_grant && (
              <p className="alert alert-warning" style={{ marginTop: 16 }}>
                This attempt exists only because it was granted. The quiz’s own maximum has not
                changed, and no other learner is affected.
              </p>
            )}

            {eligibility.guidance && (
              <p className="alert alert-warning" style={{ marginTop: 16 }}>
                {eligibility.guidance}
              </p>
            )}

            {eligibility.blockers.length > 0 && (
              <ul className="field-hint" style={{ marginTop: 12 }}>
                {eligibility.blockers.map((blocker) => (
                  <li key={blocker.code}>{blocker.message}</li>
                ))}
              </ul>
            )}

            <div className="row" style={{ gap: 8, marginTop: 16 }}>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!eligibility.can_retake || busy}
                onClick={() => void requestRetake()}
              >
                {busy ? 'Requesting…' : 'Request a retake'}
              </button>
              <Link className="btn" to="/attempt">
                Go to the attempt screen
              </Link>
            </div>
            <p className="field-hint">
              Requesting twice is safe: the request is idempotent, so a retry returns the retake you
              already have rather than consuming another attempt.
            </p>
          </div>
        </div>
      )}

      {history && (
        <div className="card">
          <div className="card-header">
            <h2>Attempt history</h2>
            <span className="badge badge-neutral">{history.attempt_count} attempts</span>
          </div>
          <div className="card-body">
            {history.entries.length === 0 ? (
              <div className="empty">No attempts at this quiz yet.</div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Status</th>
                    <th>Config version</th>
                    <th>Score</th>
                    <th>Result</th>
                    <th>Submitted</th>
                    <th>Retake of</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {history.entries.map((entry) => (
                    <tr key={entry.attempt_id}>
                      <td>{entry.attempt_number}</td>
                      <td>{entry.status}</td>
                      <td>v{entry.configuration_version_number ?? '?'}</td>
                      <td>
                        {/* `score_available` is checked rather than assumed: an attempt that is
                            submitted but not yet scored has a null percentage, and printing "0%"
                            would report a failure that has not happened. */}
                        {entry.score_available && entry.percentage !== null
                          ? `${entry.percentage}% (${entry.total_marks}/${entry.maximum_marks})`
                          : 'Not scored'}
                      </td>
                      <td>
                        {entry.pass_fail_available ? (
                          <span
                            className={
                              entry.pass_fail_status === 'PASSED'
                                ? 'badge badge-success'
                                : 'badge badge-danger'
                            }
                          >
                            {entry.pass_fail_status}
                          </span>
                        ) : (
                          <span className="badge badge-neutral">Pending</span>
                        )}
                      </td>
                      <td>{formatDate(entry.submitted_at)}</td>
                      <td>
                        {entry.is_retake ? (
                          <span className="badge badge-neutral">
                            attempt {entry.retake_of_attempt_id ? '↩' : ''} retake
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td>
                        {entry.feedback_available && (
                          <Link to={`/reports/${entry.attempt_id}`}>Report</Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="field-hint">
              Every row is assembled read-only from UC-03, UC-04, UC-05, UC-06 and UC-07. A fact an
              upstream capability has not produced is shown as unavailable rather than guessed —
              which is why an unscored attempt says “Not scored” instead of 0%.
            </p>
          </div>
        </div>
      )}

      {isAdmin && quizId !== null && <GrantPanel quizId={quizId} onGranted={() => void load(quizId)} />}
    </div>
  );
}

/**
 * The administrator's remedy: grant one learner extra attempts at one quiz.
 *
 * Deliberately narrow. It cannot change the quiz's configured maximum, because the backend will not
 * let it — a grant is recorded against the learner and the published configuration version is left
 * exactly as it was. That is the whole point of the feature, and the panel says so rather than
 * leaving an administrator to wonder whether they have just changed the rules for everybody.
 */
function GrantPanel({
  quizId,
  onGranted,
}: {
  quizId: number;
  onGranted: () => void;
}): ReactNode {
  const toast = useToast();
  const [learnerId, setLearnerId] = useState('');
  const [courseId, setCourseId] = useState('');
  const [additional, setAdditional] = useState(1);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await retakes.grant({
        learnerId: learnerId.trim(),
        courseId: courseId.trim(),
        quizId: String(quizId),
        additionalAttempts: additional,
        reason: reason.trim(),
        // A fresh key per submission, so a double-click is one grant rather than two. The API
        // requires one precisely so that a retried request cannot grant twice.
        idempotencyKey: `ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      });
      toast.success(`Granted ${additional} attempt(s) to learner ${learnerId}.`);
      setReason('');
      onGranted();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }, [learnerId, courseId, quizId, additional, reason, toast, onGranted]);

  return (
    <div className="card">
      <div className="card-header">
        <h2>Grant an additional attempt</h2>
        <span className="badge badge-neutral">administrator</span>
      </div>
      <div className="card-body">
        <ErrorSummary error={error} />
        <p className="field-hint">
          Applies to one learner on one quiz. The quiz’s configured maximum is not modified and no
          new configuration version is published, so no other learner is affected. The learner and
          course ids are the platform’s own — the identity switcher shows them.
        </p>
        <div className="field">
          <label htmlFor="grant-learner">Learner id</label>
          <input
            id="grant-learner"
            value={learnerId}
            onChange={(event) => setLearnerId(event.target.value)}
            placeholder="e.g. 2"
          />
        </div>
        <div className="field">
          <label htmlFor="grant-course">Course id</label>
          <input
            id="grant-course"
            value={courseId}
            onChange={(event) => setCourseId(event.target.value)}
            placeholder="e.g. 1"
          />
        </div>
        <div className="field">
          <label htmlFor="grant-count">Additional attempts</label>
          <input
            id="grant-count"
            type="number"
            min={1}
            max={10}
            value={additional}
            onChange={(event) => setAdditional(Number(event.target.value))}
          />
        </div>
        <div className="field">
          <label htmlFor="grant-reason">Reason (recorded on the grant)</label>
          <input
            id="grant-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="e.g. Documented technical fault during attempt 2"
          />
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || !learnerId.trim() || !courseId.trim() || !reason.trim()}
          onClick={() => void submit()}
        >
          {busy ? 'Granting…' : 'Grant'}
        </button>
      </div>
    </div>
  );
}