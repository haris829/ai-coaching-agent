/**
 * Formal Assessment Mode (UC-09) — the supervised sitting, and the assessor's review.
 *
 * This screen exists to make three things visible that are otherwise hard to believe from an API
 * trace alone:
 *
 * 1. **The pre-start sequence is a gate, not a wizard.** Conditions must be acknowledged before an
 *    identity may be confirmed, and an identity before a device may claim the assessment. The
 *    buttons below are disabled in that order because the backend refuses them in that order —
 *    the UI is mirroring a rule, not implementing one. Skipping a step produces the server's
 *    refusal, which is worth seeing.
 * 2. **One device, and only one.** The session token is handed out exactly once, at start, and
 *    every write carries it. Opening this page in a second tab and starting again is refused,
 *    which is the single-device rule doing its job.
 * 3. **A pass is not a certificate.** A formal pass produces a review for a named assessor, and no
 *    certificate exists until that assessor approves. Switch to the assessor identity and the queue
 *    at the bottom of this page is where that happens.
 *
 * The disconnect button is deliberately exposed. A reviewer needs to be able to see that dropping
 * out mid-examination commits the autosaved work rather than losing it — and that path was broken
 * in every deployment until UC-11 found it (F-16), which is the best argument for keeping it one
 * click away rather than only in a test.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';

import { ApiError, api, assessor, attempts, formal } from '../api/client';
import type {
  FormalConditions,
  FormalReview,
  FormalStarted,
} from '../api/deploymentTypes';
import type { AttemptQuestion } from '../api/attemptTypes';
import type { QuizSummary } from '../api/types';
import { ErrorSummary, Spinner, formatDate, useToast } from '../components/ui';
import { useRole } from '../lib/useRole';

/**
 * A stable-ish identifier for this browser, used as the device fingerprint.
 *
 * Not a security control and not presented as one — a real deployment would use whatever the
 * invigilation platform provides. It only needs to be *stable within a device and different between
 * devices*, which is enough to demonstrate the lock: reload and you keep your session, open another
 * browser and you are refused.
 */
function deviceFingerprint(): string {
  const key = 'quiz-agent.device';
  try {
    const existing = window.localStorage.getItem(key);
    if (existing) return existing;
    const created = `web-${Math.random().toString(36).slice(2, 12)}`;
    window.localStorage.setItem(key, created);
    return created;
  } catch {
    return 'web-ephemeral';
  }
}

/** The session token, kept per formal attempt so a reload does not lose the device's claim. */
function storeSession(formalAttemptId: string, token: string): void {
  try {
    window.sessionStorage.setItem(`quiz-agent.formal.${formalAttemptId}`, token);
  } catch {
    /* in-memory only for this page load */
  }
}

function readSession(formalAttemptId: string): string | null {
  try {
    return window.sessionStorage.getItem(`quiz-agent.formal.${formalAttemptId}`);
  } catch {
    return null;
  }
}

export function FormalAssessmentPage(): ReactNode {
  const { role } = useRole();

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Formal assessment</h1>
          <p>
            A supervised sitting. It cannot be paused, it runs on one device, AI coaching is
            unavailable while it is in progress, and a pass is held for a named assessor before any
            certificate exists.
          </p>
        </div>
      </div>

      {role === 'assessor' ? <AssessorQueue /> : <LearnerSitting />}

      {role !== 'assessor' && (
        <p className="field-hint">
          To review a submitted assessment, switch to the <strong>assessor</strong> identity in the
          top bar. An administrator credential is refused on the assessor endpoints by design — a
          review exists so that a named person signs off on a learner’s result.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The learner's half
// ---------------------------------------------------------------------------

function LearnerSitting(): ReactNode {
  const toast = useToast();
  const [quizzes, setQuizzes] = useState<QuizSummary[]>([]);
  const [quizId, setQuizId] = useState<number | null>(null);
  const [conditions, setConditions] = useState<FormalConditions | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [formalAttemptId, setFormalAttemptId] = useState<string | null>(null);
  const [identityConfirmed, setIdentityConfirmed] = useState(false);
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [started, setStarted] = useState<FormalStarted | null>(null);
  const [questions, setQuestions] = useState<AttemptQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
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

  // Reset the whole sequence when the quiz changes: a half-completed acknowledgement for one quiz
  // must not appear to apply to another.
  useEffect(() => {
    setConditions(null);
    setAccepted(false);
    setFormalAttemptId(null);
    setIdentityConfirmed(false);
    setStarted(null);
    setQuestions([]);
    setAnswers({});
    setError(null);
    if (quizId === null) return;
    (async () => {
      try {
        setConditions(await formal.conditions(quizId));
      } catch (cause) {
        setConditions(null);
        setError(cause);
      }
    })();
  }, [quizId]);

  const acknowledge = useCallback(async () => {
    if (quizId === null || !conditions) return;
    setBusy(true);
    setError(null);
    try {
      const result = await formal.acknowledge(
        quizId,
        conditions.conditions.map((condition) => condition.code),
      );
      setFormalAttemptId(result.formal_attempt_id);
      toast.success(`Conditions ${result.conditions_version} acknowledged.`);
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }, [quizId, conditions, toast]);

  const confirmIdentity = useCallback(async () => {
    if (quizId === null) return;
    setBusy(true);
    setError(null);
    try {
      const result = await formal.confirmIdentity(quizId, fullName, email);
      setIdentityConfirmed(result.identity_check.confirmed);
      if (result.identity_check.confirmed) {
        toast.success('Identity confirmed against the platform directory.');
      } else {
        toast.error(result.identity_check.reason ?? 'The details did not match the directory.');
      }
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }, [quizId, fullName, email, toast]);

  const start = useCallback(async () => {
    if (quizId === null) return;
    setBusy(true);
    setError(null);
    try {
      const result = await formal.start(quizId, deviceFingerprint());
      setStarted(result);
      storeSession(result.formal_attempt_id, result.session.session_token);
      const paper = await attempts.questions(result.attempt_id);
      setQuestions(paper.questions);
      toast.success('This device now holds the assessment.');
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }, [quizId, toast]);

  const sessionToken = started ? (readSession(started.formal_attempt_id) ?? started.session.session_token) : null;

  const save = useCallback(async () => {
    if (!started || !sessionToken) return;
    setBusy(true);
    setError(null);
    try {
      await formal.autosave(
        started.formal_attempt_id,
        sessionToken,
        Object.entries(answers).map(([questionId, optionId]) => ({
          question_id: questionId,
          response: { selectedOptionId: optionId },
        })),
      );
      toast.success('Answers saved.');
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }, [started, sessionToken, answers, toast]);

  const finish = useCallback(
    async (mode: 'submit' | 'disconnect') => {
      if (!started || !sessionToken) return;
      setBusy(true);
      setError(null);
      try {
        if (mode === 'submit') {
          await formal.submit(started.formal_attempt_id, sessionToken);
          toast.success('Submitted. A pass now waits for an assessor.');
        } else {
          await formal.disconnect(started.formal_attempt_id, sessionToken, 'NETWORK_LOSS');
          toast.success(
            'Disconnect reported. Whatever was saved has been submitted; the assessment cannot be resumed.',
          );
        }
        setStarted(null);
        setQuestions([]);
      } catch (cause) {
        setError(cause);
      } finally {
        setBusy(false);
      }
    },
    [started, sessionToken, toast],
  );

  if (loading) return <Spinner label="Loading quizzes…" />;

  const notFormal = conditions !== null && !conditions.is_formal_assessment;

  return (
    <>
      {quizzes.length > 0 && (
        <div className="card">
          <div className="card-body">
            <div className="field">
              <label htmlFor="formal-quiz">Quiz</label>
              <select
                id="formal-quiz"
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
            {notFormal && (
              <p className="alert alert-warning">
                This quiz is not configured as a formal assessment, so none of the supervised rules
                apply to it. Configure <strong>Formal assessment</strong> on a quiz, or pick the
                seeded <strong>Supervised Final Examination</strong>.
              </p>
            )}
          </div>
        </div>
      )}

      <ErrorSummary error={error} />

      {conditions?.is_formal_assessment && (
        <div className="card">
          <div className="card-header">
            <h2>1 — Conditions</h2>
            <span className="badge badge-neutral">version {conditions.conditions_version}</span>
          </div>
          <div className="card-body">
            <ul>
              {conditions.conditions.map((condition) => (
                <li key={condition.code}>
                  <strong>{condition.title}</strong>
                  {condition.detail ? ` — ${condition.detail}` : ''}
                </li>
              ))}
            </ul>
            <label className="row" style={{ gap: 8, alignItems: 'center', marginTop: 12 }}>
              <input
                type="checkbox"
                checked={accepted}
                onChange={(event) => setAccepted(event.target.checked)}
              />
              <span>I have read and accept every condition above.</span>
            </label>
            <div className="row" style={{ gap: 8, marginTop: 12 }}>
              <button
                type="button"
                className="btn btn-primary"
                disabled={!accepted || busy || formalAttemptId !== null}
                onClick={() => void acknowledge()}
              >
                {formalAttemptId ? 'Acknowledged' : 'Acknowledge'}
              </button>
            </div>
            <p className="field-hint">
              The version is recorded on the acknowledgement, so “which conditions did this learner
              agree to?” stays answerable after the wording changes.
            </p>
          </div>
        </div>
      )}

      {formalAttemptId && (
        <div className="card">
          <div className="card-header">
            <h2>2 — Identity</h2>
            {identityConfirmed && <span className="badge badge-success">confirmed</span>}
          </div>
          <div className="card-body">
            <p className="field-hint">
              Matched exactly against the platform directory, after whitespace normalisation. There
              is no configuration switch to relax it. Use the name and email of the identity you are
              acting as — the switcher in the top bar shows both.
            </p>
            <div className="field">
              <label htmlFor="formal-name">Full name</label>
              <input
                id="formal-name"
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                placeholder="e.g. Ada Learner"
              />
            </div>
            <div className="field">
              <label htmlFor="formal-email">Email</label>
              <input
                id="formal-email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="e.g. learner@example.com"
              />
            </div>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || !fullName.trim() || !email.trim()}
              onClick={() => void confirmIdentity()}
            >
              Confirm identity
            </button>
          </div>
        </div>
      )}

      {identityConfirmed && !started && (
        <div className="card">
          <div className="card-header">
            <h2>3 — Claim the assessment for this device</h2>
          </div>
          <div className="card-body">
            <p className="field-hint">
              The session token is issued once, here. Every save afterwards carries it, which is what
              makes the sitting single-device. Starting again from another browser is refused.
            </p>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => void start()}
            >
              {busy ? 'Starting…' : 'Start the assessment'}
            </button>
          </div>
        </div>
      )}

      {started && (
        <div className="card">
          <div className="card-header">
            <h2>4 — Sitting</h2>
            <span className="badge badge-warning">cannot be paused</span>
          </div>
          <div className="card-body">
            <p className="field-hint">
              Attempt <code>{started.attempt_id}</code> · device session{' '}
              <code>{started.session.session_id}</code>
            </p>
            {questions.map((question, index) => (
              <div key={question.questionId} className="field">
                <label>
                  {index + 1}. {question.prompt}
                </label>
                {/* Only single-choice is rendered here: the seeded formal quiz is single-choice,
                    and reimplementing the full five-type renderer would duplicate the attempt
                    screen for no demonstrative gain. The API accepts every type. */}
                {(question.options ?? []).map((option) => (
                  <label
                    key={option.optionId}
                    className="row"
                    style={{ gap: 8, alignItems: 'center' }}
                  >
                    <input
                      type="radio"
                      name={question.questionId}
                      checked={answers[question.questionId] === option.optionId}
                      onChange={() =>
                        setAnswers((current) => ({
                          ...current,
                          [question.questionId]: option.optionId,
                        }))
                      }
                    />
                    <span>{option.text}</span>
                  </label>
                ))}
              </div>
            ))}
            <div className="row" style={{ gap: 8, marginTop: 12 }}>
              <button type="button" className="btn" disabled={busy} onClick={() => void save()}>
                Save answers
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => void finish('submit')}
              >
                Submit
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={() => void finish('disconnect')}
              >
                Simulate a disconnect
              </button>
            </div>
            <p className="field-hint">
              A disconnect submits the last saved state and prevents any resume. It is here so the
              behaviour can be seen: work already saved is work already counted.
            </p>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// The assessor's half
// ---------------------------------------------------------------------------

function AssessorQueue(): ReactNode {
  const toast = useToast();
  const [reviews, setReviews] = useState<FormalReview[]>([]);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await assessor.pending();
      setReviews(response.reviews);
    } catch (cause) {
      setError(cause);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = useCallback(
    async (review: FormalReview, decision: 'APPROVED' | 'REQUIRES_FURTHER_REVIEW') => {
      setBusy(review.review_id);
      setError(null);
      try {
        // Taking the review before deciding it is recorded, so "who looked at this" stays
        // answerable. Tolerated if it has already been taken — that is not a failure.
        try {
          await assessor.startReview(review.review_id);
        } catch (cause) {
          if (!(cause instanceof ApiError) || cause.status >= 500) throw cause;
        }
        await assessor.decide(review.review_id, decision, notes[review.review_id] ?? '');
        if (decision === 'APPROVED') {
          await assessor.certificateWorkflow(review.review_id);
          toast.success('Approved. The certificate has been requested.');
        } else {
          toast.success('Recorded. The certificate stays blocked.');
        }
        await load();
      } catch (cause) {
        setError(cause);
      } finally {
        setBusy(null);
      }
    },
    [notes, toast, load],
  );

  if (loading) return <Spinner label="Loading the review queue…" />;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Assessor review queue</h2>
        <span className="badge badge-neutral">{reviews.length} pending</span>
      </div>
      <div className="card-body">
        <ErrorSummary error={error} />
        {reviews.length === 0 ? (
          <div className="empty">
            Nothing waiting. A formal assessment appears here once a learner has passed one — a
            failing formal assessment reaches no review and produces no certificate.
          </div>
        ) : (
          reviews.map((review) => (
            <div key={review.review_id} className="card" style={{ marginBottom: 12 }}>
              <div className="card-body">
                <div className="kv">
                  <div className="stat">
                    <span className="stat-label">Score</span>
                    <span className="stat-value">
                      {review.percentage === null ? '—' : `${review.percentage}%`}
                    </span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Submitted</span>
                    <span className="stat-value">{formatDate(review.submitted_at)}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Auto-submitted</span>
                    <span className="stat-value">{review.auto_submitted ? 'Yes' : 'No'}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Anomalies</span>
                    <span className="stat-value">{review.anomaly_count}</span>
                  </div>
                </div>
                <p className="field-hint">
                  learner <code>{review.learner_id}</code> · attempt <code>{review.attempt_id}</code>{' '}
                  · state {review.state}
                </p>
                <div className="field">
                  <label htmlFor={`notes-${review.review_id}`}>Decision notes</label>
                  <input
                    id={`notes-${review.review_id}`}
                    value={notes[review.review_id] ?? ''}
                    onChange={(event) =>
                      setNotes((current) => ({
                        ...current,
                        [review.review_id]: event.target.value,
                      }))
                    }
                    placeholder="What you checked, and what you concluded"
                  />
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy === review.review_id}
                    onClick={() => void decide(review, 'APPROVED')}
                  >
                    Approve and issue the certificate
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy === review.review_id}
                    onClick={() => void decide(review, 'REQUIRES_FURTHER_REVIEW')}
                  >
                    Needs further review
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
        <p className="field-hint">
          Approving is what makes a certificate exist. Until then the learner has a recorded pass and
          no certificate, and the retry endpoint cannot produce one either — the gate is asked every
          time.
        </p>
      </div>
    </div>
  );
}