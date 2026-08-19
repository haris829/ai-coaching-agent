/**
 * Take a quiz — UC-03 attempt delivery.
 *
 * The learner-facing half of the system, and the one screen where the guarantees UC-03 makes have to
 * be visible rather than merely implemented:
 *
 *  * **Resume, not restart.** Loading this page looks for an open attempt first. A refresh, a closed
 *    laptop or a flat battery costs nothing: the answers are on the server, and the page rebuilds
 *    from them. Starting an attempt is a separate, deliberate action.
 *  * **The server owns the clock.** The countdown interpolates between server readings and resyncs
 *    regularly; a device clock is never trusted, only compared and reported.
 *  * **Autosave that admits failure.** Saves are batched, only dirty answers are sent, and a failure
 *    raises a warning that *stays* until a save succeeds, with a manual retry beside it. A silent
 *    autosave failure is the worst outcome available to this screen, so it is the one thing the UI
 *    refuses to be quiet about.
 *  * **Submitting is a two-step.** A server-built preview, then an explicit confirmation carrying an
 *    idempotency key.
 *
 * The rules themselves live in the backend. This page never decides whether an answer is valid,
 * whether time has run out, or whether a submission may proceed — it asks, and renders the answer.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { ApiError, api, attempts as attemptApi, results as resultsApi } from '../api/client';
import type {
  AnswerResponse,
  Attempt,
  AttemptEligibility,
  AttemptQuestion,
  AttemptState,
  QuestionOutlineEntry,
  SubmissionPreview,
  SubmissionStatus,
} from '../api/attemptTypes';
import type {
  FeedbackResponse,
  OutcomeResponse,
  ResultResponse,
} from '../api/resultTypes';
import type { QuizSummary } from '../api/types';
import { AttemptReview, QuestionNavigator, type ReviewFilter } from '../components/attempt/AttemptReview';
import { QuestionInput } from '../components/attempt/QuestionInputs';
import { PendingSubmissionPanel, SubmitPanel } from '../components/attempt/SubmitPanel';
import { CoachingPanel } from '../components/attempt/CoachingPanel';
import { FeedbackCard, OutcomeCard, ScoreCard } from '../components/attempt/ResultPanel';
import { ErrorSummary, Spinner, formatDate, useToast } from '../components/ui';
import { ATTEMPT_TYPE_LABELS, PRESENTATION_LABELS, looksComplete, sameResponse } from '../lib/attemptAnswers';
import { countdown, formatRemaining, urgency, type TimingSample } from '../lib/attemptTimer';

/** Local edit state for one question, alongside what the server last confirmed. */
interface AnswerSlot {
  /** What the inputs are bound to. */
  local: AnswerResponse;
  /** What the server last confirmed it holds. The difference is what autosave sends. */
  saved: AnswerResponse;
  /** The server's revision counter, sent as `expectedRevision` to detect a concurrent change. */
  revision: number;
}

type Screen = 'answering' | 'review' | 'submitting';

/**
 * Fetch one stage of the result chain, driving it only when it needs driving.
 *
 * `read` is the `GET`; `drive` is the idempotent `POST`. The `POST` is used when the stage has never
 * run (the `GET` answers `404`), when the caller explicitly asked (a retry button), or when what came
 * back is still pending. Anything else is served from the read, so simply looking at a submitted
 * attempt does not write.
 */
async function read<T>(
  get: () => Promise<T>,
  post: () => Promise<T>,
  drive: boolean,
  needsDriving: (payload: T) => boolean,
): Promise<T> {
  if (drive) return post();
  try {
    const payload = await get();
    return needsDriving(payload) ? post() : payload;
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return post();
    throw cause;
  }
}

export function AttemptPage(): ReactNode {
  const toast = useToast();

  // ---- quiz selection ------------------------------------------------------
  const [quizzes, setQuizzes] = useState<QuizSummary[]>([]);
  const [quizId, setQuizId] = useState<string | null>(null);
  const [loadingQuizzes, setLoadingQuizzes] = useState(true);

  // ---- attempt -------------------------------------------------------------
  const [eligibility, setEligibility] = useState<AttemptEligibility | null>(null);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [state, setState] = useState<AttemptState | null>(null);
  const [questions, setQuestions] = useState<AttemptQuestion[]>([]);
  const [answers, setAnswers] = useState<Map<string, AnswerSlot>>(new Map());
  const [position, setPosition] = useState(1);
  const [screen, setScreen] = useState<Screen>('answering');
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>('all');

  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // ---- saving --------------------------------------------------------------
  const [saving, setSaving] = useState(false);
  /**
   * The last save failure, held until a save succeeds.
   *
   * Not a toast: a toast disappears, and a learner who looked away would keep answering into a
   * client that is no longer persisting anything.
   */
  const [saveError, setSaveError] = useState<string | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);

  // ---- timing --------------------------------------------------------------
  const [sample, setSample] = useState<TimingSample | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  /**
   * Load the result chain for a locked attempt.
   *
   * **Reads first.** Submission already ran the chain, so each stage is fetched with a `GET`; the
   * idempotent `POST` is used only when there is nothing recorded yet (`404`) or the stage is still
   * pending — which is exactly what its documented retry path is for. Opening this screen therefore
   * writes nothing in the normal case, and `drive: true` (the buttons) forces the retry.
   */
  const loadChain = useCallback(
    async (id: string, { drive = false }: { drive?: boolean } = {}): Promise<void> => {
      setChainBusy(true);
      try {
        const scored = await read(
          () => resultsApi.result(id),
          () => resultsApi.score(id),
          drive,
          (payload) => payload.result.status !== 'SCORED',
        );
        setResultView(scored);

        if (scored.result.status !== 'SCORED') {
          // "Submitted — Pending Score": there is nothing to gate or explain yet, and saying so is
          // more useful than a blank screen.
          setOutcomeView(null);
          setFeedbackView(null);
          return;
        }

        setOutcomeView(
          await read(
            () => resultsApi.outcome(id),
            () => resultsApi.determine(id),
            drive,
            (payload) =>
              payload.certificate?.status === 'PENDING' || payload.cpd?.status === 'PENDING',
          ),
        );
        setFeedbackView(
          await read(
            () => resultsApi.feedback(id),
            () => resultsApi.generateFeedback(id),
            drive,
            (payload) => payload.status !== 'GENERATED',
          ),
        );
      } catch (cause) {
        toast.error(cause instanceof ApiError ? cause.message : 'The result could not be loaded.');
      } finally {
        setChainBusy(false);
      }
    },
    [toast],
  );

  const retryCertificate = useCallback(
    async (id: string): Promise<void> => {
      setChainBusy(true);
      try {
        setOutcomeView(await resultsApi.retryCertificate(id));
        toast.success('Certificate issued.');
      } catch (cause) {
        toast.error(
          cause instanceof ApiError ? cause.message : 'The certificate could not be issued.',
        );
      } finally {
        setChainBusy(false);
      }
    },
    [toast],
  );

  const retryCpd = useCallback(
    async (id: string): Promise<void> => {
      setChainBusy(true);
      try {
        setOutcomeView(await resultsApi.retryCpd(id));
        toast.success('CPD record synchronised.');
      } catch (cause) {
        toast.error(cause instanceof ApiError ? cause.message : 'The CPD record failed to sync.');
      } finally {
        setChainBusy(false);
      }
    },
    [toast],
  );

  // ---- submission ----------------------------------------------------------
  const [preview, setPreview] = useState<SubmissionPreview | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submissionStatus, setSubmissionStatus] = useState<SubmissionStatus | null>(null);

  // ---- the result chain (UC-04 → UC-05 → UC-06) ---------------------------
  //
  // Read, not computed. The chain runs on the server inside submission; this screen fetches what it
  // produced. Each POST below is the documented idempotent retry, which is why re-requesting is safe.
  const [resultView, setResultView] = useState<ResultResponse | null>(null);
  const [outcomeView, setOutcomeView] = useState<OutcomeResponse | null>(null);
  const [feedbackView, setFeedbackView] = useState<FeedbackResponse | null>(null);
  const [chainBusy, setChainBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const attemptId = attempt?.attemptId ?? null;
  const oneAtATime = attempt?.questionPresentation === 'ONE_AT_A_TIME';
  const locked = attempt !== null && attempt.status !== 'ACTIVE';

  // -------------------------------------------------------------------------
  // Quizzes
  // -------------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await api.listQuizzes();
        if (cancelled) return;
        setQuizzes(response.quizzes);
        // UC-01 numbers its quizzes; UC-03 treats the id as an opaque string. The adapter maps
        // between them, so the string form of the id is the right thing to send.
        setQuizId(response.quizzes[0] ? String(response.quizzes[0].id) : null);
      } catch (cause) {
        if (!cancelled) setError(cause);
      } finally {
        if (!cancelled) setLoadingQuizzes(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // -------------------------------------------------------------------------
  // Loading an attempt
  // -------------------------------------------------------------------------

  const applyAnswerSheet = useCallback(
    (entries: { questionId: string; response: AnswerResponse; revision: number }[]) => {
      // The server's answers replace local state outright. Merging would risk resurrecting an edit
      // the server rejected, which is exactly the kind of divergence a reload is meant to end.
      setAnswers(
        new Map(
          entries.map((entry) => [
            entry.questionId,
            { local: entry.response, saved: entry.response, revision: entry.revision },
          ]),
        ),
      );
    },
    [],
  );

  const loadQuestions = useCallback(async (loaded: Attempt) => {
    if (loaded.questionPresentation === 'ALL_AT_ONCE') {
      const response = await attemptApi.questions(loaded.attemptId);
      setQuestions(response.questions);
      return;
    }
    // One at a time: the server refuses to hand over the whole paper, so only the current question
    // is fetched — and the cursor it resumes from is the server's, not this tab's.
    const response = await attemptApi.questionAt(loaded.attemptId, loaded.currentPosition || 1);
    setQuestions([response.question]);
  }, []);

  const refreshState = useCallback(async (id: string) => {
    const response = await attemptApi.state(id);
    setState(response.state);
    setSample({ timing: response.state.timing, receivedAtEpochMs: Date.now() });
    return response.state;
  }, []);

  const adoptAttempt = useCallback(
    async (loaded: Attempt) => {
      setAttempt(loaded);
      setPosition(loaded.currentPosition || 1);
      if (loaded.timing) setSample({ timing: loaded.timing, receivedAtEpochMs: Date.now() });

      const sheet = await attemptApi.answerSheet(loaded.attemptId);
      applyAnswerSheet(sheet.answers);
      await loadQuestions(loaded);
      await refreshState(loaded.attemptId);

      if (loaded.status === 'SUBMISSION_PENDING') {
        setSubmissionStatus(await attemptApi.submissionStatus(loaded.attemptId));
      }
    },
    [applyAnswerSheet, loadQuestions, refreshState],
  );

  /** Look for an open attempt before offering to start one. This is the resume path. */
  const load = useCallback(
    async (id: string) => {
      setLoading(true);
      setError(null);
      setAttempt(null);
      setState(null);
      setQuestions([]);
      setAnswers(new Map());
      setSample(null);
      setSaveError(null);
      setSubmissionStatus(null);
      setScreen('answering');

      try {
        const report = await attemptApi.eligibility(id);
        setEligibility(report.eligibility);

        try {
          const active = await attemptApi.active(id);
          await adoptAttempt(active.attempt);
        } catch (cause) {
          // No open attempt is an ordinary state, not an error: the eligibility panel is shown and
          // the learner can start one.
          if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
        }
      } catch (cause) {
        setError(cause);
      } finally {
        setLoading(false);
      }
    },
    [adoptAttempt],
  );

  useEffect(() => {
    if (quizId !== null) void load(quizId);
  }, [quizId, load]);

  async function start(): Promise<void> {
    if (quizId === null) return;
    setStarting(true);
    try {
      const created = await attemptApi.start(quizId);
      // `adoptAttempt` fetches the paper itself, choosing the endpoint from the locked presentation.
      await adoptAttempt(created.attempt);
      toast.success(
        `Attempt ${created.attempt.attemptNumber} started, locked to configuration ${created.attempt.configuration.configurationVersionId}.`,
      );
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The attempt could not be started.');
      if (quizId) {
        // Re-read eligibility: the refusal usually has a reason worth showing.
        try {
          setEligibility((await attemptApi.eligibility(quizId)).eligibility);
        } catch {
          // Leave the previous report in place; the toast already explained the failure.
        }
      }
    } finally {
      setStarting(false);
    }
  }

  // -------------------------------------------------------------------------
  // Saving
  // -------------------------------------------------------------------------

  const dirtyEntries = useMemo(
    () =>
      [...answers.entries()]
        .filter(([, slot]) => !sameResponse(slot.local, slot.saved))
        .map(([questionId, slot]) => ({
          questionId,
          response: slot.local,
          expectedRevision: slot.revision,
        })),
    [answers],
  );

  // Read inside the interval callback, so the timer does not need to be torn down and rebuilt on
  // every keystroke just to see the latest answers.
  const dirtyRef = useRef(dirtyEntries);
  dirtyRef.current = dirtyEntries;

  const save = useCallback(
    async (source: 'MANUAL' | 'AUTOSAVE'): Promise<void> => {
      const pending = dirtyRef.current;
      if (attemptId === null || pending.length === 0 || locked) return;

      setSaving(true);
      try {
        const result = await attemptApi.saveAnswers(attemptId, pending, source);
        setAnswers((current) => {
          const next = new Map(current);
          for (const view of result.saved) {
            const slot = next.get(view.questionId);
            if (!slot) continue;
            // `saved` becomes what the server confirmed. `local` is left alone: the learner may have
            // edited it again while the request was in flight, and that edit must not be discarded.
            next.set(view.questionId, { ...slot, saved: view.response, revision: view.revision });
          }
          return next;
        });
        setLastSavedAt(result.persistedAt);
        setSaveError(null);
        setSample({ timing: result.timing, receivedAtEpochMs: Date.now() });
        if (source === 'MANUAL') toast.success('Answers saved.');
        void refreshState(attemptId).catch(() => undefined);
      } catch (cause) {
        await handleSaveFailure(cause);
      } finally {
        setSaving(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [attemptId, locked, refreshState, toast],
  );

  async function handleSaveFailure(cause: unknown): Promise<void> {
    if (!(cause instanceof ApiError)) {
      setSaveError('Your answers could not be saved. They are still here — use “Save now” to retry.');
      return;
    }

    if (cause.code === 'ANSWER_REVISION_CONFLICT' && attemptId) {
      // Another tab or device moved this answer on. The server's copy wins, and saying so is more
      // useful than silently overwriting somebody's work.
      try {
        const sheet = await attemptApi.answerSheet(attemptId);
        applyAnswerSheet(sheet.answers);
        setSaveError(
          'This attempt was changed somewhere else — perhaps another tab. The answers shown have been reloaded from the server.',
        );
      } catch {
        setSaveError('This attempt was changed elsewhere and could not be reloaded. Refresh the page.');
      }
      return;
    }

    if (cause.code === 'ATTEMPT_EXPIRED' || cause.code === 'ATTEMPT_ALREADY_SUBMITTED') {
      // Not a save problem: the attempt is over. Re-read it so the UI stops offering to save.
      setSaveError(null);
      if (attemptId) void reloadAttempt(attemptId);
      return;
    }

    const retryable = cause.extraAs<boolean>('retryable') ?? cause.status >= 500;
    setSaveError(
      retryable
        ? `${cause.message} Your answers are still here — use “Save now” to retry.`
        : `${cause.message} Correct the answer and save again.`,
    );
  }

  const reloadAttempt = useCallback(
    async (id: string) => {
      try {
        const response = await attemptApi.get(id);
        setAttempt(response.attempt);
        if (response.attempt.timing) {
          setSample({ timing: response.attempt.timing, receivedAtEpochMs: Date.now() });
        }
        await refreshState(id);
        if (response.attempt.status === 'SUBMISSION_PENDING') {
          setSubmissionStatus(await attemptApi.submissionStatus(id));
        }
      } catch (cause) {
        setError(cause);
      }
    },
    [refreshState],
  );

  /** The periodic autosave, at the cadence the server publishes. */
  useEffect(() => {
    if (attemptId === null || locked) return;
    const seconds = sample?.timing.autosaveIntervalSeconds ?? 20;
    const handle = window.setInterval(() => {
      if (dirtyRef.current.length > 0) void save('AUTOSAVE');
    }, Math.max(5, seconds) * 1000);
    return () => window.clearInterval(handle);
  }, [attemptId, locked, sample?.timing.autosaveIntervalSeconds, save]);

  /** A last save on the way out, so closing the tab does not lose the current question. */
  useEffect(() => {
    function onHide(): void {
      if (document.visibilityState === 'hidden' && dirtyRef.current.length > 0) void save('AUTOSAVE');
    }
    document.addEventListener('visibilitychange', onHide);
    return () => document.removeEventListener('visibilitychange', onHide);
  }, [save]);

  /** Warn before a reload that would drop unsaved edits. */
  useEffect(() => {
    if (dirtyEntries.length === 0 || locked) return;
    function onBeforeUnload(event: BeforeUnloadEvent): void {
      event.preventDefault();
      event.returnValue = '';
    }
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirtyEntries.length, locked]);

  // -------------------------------------------------------------------------
  // Timing
  // -------------------------------------------------------------------------

  const view = countdown(sample, nowMs);

  /** One local tick per second, purely to redraw the countdown between server readings. */
  useEffect(() => {
    if (attemptId === null || locked) return;
    const handle = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(handle);
  }, [attemptId, locked]);

  /** Resync with the server, reporting this device's clock so the response can flag the skew. */
  useEffect(() => {
    if (attemptId === null || locked || !view.needsResync) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await attemptApi.timing(attemptId, new Date().toISOString());
        if (cancelled) return;
        setSample({ timing: response.timing, receivedAtEpochMs: Date.now() });
      } catch {
        // A failed resync is not worth interrupting the learner over: the interpolated countdown
        // keeps running and the next tick tries again.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attemptId, locked, view.needsResync]);

  /**
   * When the countdown reaches zero, ask the server what happened.
   *
   * The client never decides that an attempt has expired — it notices, and reads the outcome. The
   * server settles expiry (submitting the saved answers) on the next request that touches the attempt.
   */
  useEffect(() => {
    if (attemptId === null || locked || !view.expired) return;
    void reloadAttempt(attemptId);
  }, [attemptId, locked, view.expired, reloadAttempt]);

  /**
   * Once an attempt is locked and fully submitted, read what the result chain produced.
   *
   * Deliberately *after* submission rather than as part of it: the chain runs inside UC-03's hand-off,
   * so by the time the attempt reads SUBMITTED the score, the verdict and the report already exist.
   * Fetching them here is what makes the screen a demonstration of the backend rather than a
   * re-implementation of it.
   */
  useEffect(() => {
    if (attemptId === null || !locked) return;
    if (attempt?.status !== 'SUBMITTED') return;
    if (resultView !== null && resultView.result.attemptId === attemptId) return;
    void loadChain(attemptId);
  }, [attemptId, locked, attempt?.status, resultView, loadChain]);

  // -------------------------------------------------------------------------
  // Navigation
  // -------------------------------------------------------------------------

  const goToPosition = useCallback(
    async (next: number) => {
      if (attempt === null) return;
      setScreen('answering');
      setPosition(next);

      if (attempt.questionPresentation === 'ALL_AT_ONCE') {
        document.getElementById(`question-${next}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }

      // Save before moving: leaving a question is exactly when a learner expects their answer to be
      // kept, and the cursor write is the server's record of where they were.
      if (dirtyRef.current.length > 0) await save('AUTOSAVE');
      try {
        const [question] = await Promise.all([
          attemptApi.questionAt(attempt.attemptId, next),
          attemptApi.setCursor(attempt.attemptId, next).catch(() => undefined),
        ]);
        setQuestions([question.question]);
        await refreshState(attempt.attemptId);
      } catch (cause) {
        toast.error(cause instanceof ApiError ? cause.message : 'That question could not be loaded.');
      }
    },
    [attempt, refreshState, save, toast],
  );

  const goToEntry = useCallback(
    (entry: QuestionOutlineEntry) => void goToPosition(entry.position),
    [goToPosition],
  );

  async function toggleFlag(entry: QuestionOutlineEntry): Promise<void> {
    if (attemptId === null) return;
    try {
      await attemptApi.setFlag(attemptId, entry.questionId, !entry.flagged);
      await refreshState(attemptId);
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The flag could not be changed.');
    }
  }

  // -------------------------------------------------------------------------
  // Submission
  // -------------------------------------------------------------------------

  async function openSubmit(): Promise<void> {
    if (attemptId === null) return;
    setScreen('submitting');
    setLoadingPreview(true);
    try {
      // Save first: a preview of stale answers would tell the learner the wrong thing.
      if (dirtyRef.current.length > 0) await save('MANUAL');
      setPreview((await attemptApi.previewSubmission(attemptId)).preview);
    } catch (cause) {
      setScreen('review');
      toast.error(cause instanceof ApiError ? cause.message : 'The submission summary could not be loaded.');
    } finally {
      setLoadingPreview(false);
    }
  }

  async function confirmSubmit(): Promise<void> {
    if (attemptId === null || preview === null) return;
    setSubmitting(true);
    try {
      // The server's suggested key, so a double-click or a retry collapses into one submission.
      await attemptApi.confirmSubmission(attemptId, preview.suggestedIdempotencyKey);
      await reloadAttempt(attemptId);
      setScreen('answering');
      toast.success('Attempt submitted.');
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'SUBMISSION_FAILED') {
        // The answers are frozen; the hand-off is what failed. Show the retry path.
        await reloadAttempt(attemptId);
        setScreen('answering');
      } else {
        toast.error(cause instanceof ApiError ? cause.message : 'The attempt could not be submitted.');
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function retrySubmission(): Promise<void> {
    if (attemptId === null) return;
    setRetrying(true);
    try {
      await attemptApi.retrySubmission(attemptId);
      await reloadAttempt(attemptId);
      toast.success('Submission completed.');
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The submission could not be completed.');
    } finally {
      setRetrying(false);
    }
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  if (loadingQuizzes) return <Spinner label="Loading quizzes…" />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Take a quiz</h1>
          <p>
            The learner surface for <strong>UC-03</strong>. Answers are saved on the server as you go,
            so refreshing this page resumes the same attempt rather than starting a new one.
          </p>
        </div>
      </div>

      {quizzes.length > 0 && (
        <div className="card">
          <div className="card-body">
            <div className="field">
              <label htmlFor="attempt-quiz">Quiz</label>
              <select
                id="attempt-quiz"
                value={quizId ?? ''}
                onChange={(event) => setQuizId(event.target.value)}
              >
                {quizzes.map((quiz) => (
                  <option key={quiz.id} value={String(quiz.id)}>
                    {quiz.courseTitle} — {quiz.title}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}

      <ErrorSummary error={error} />

      {loading && <Spinner label="Looking for an attempt in progress…" />}

      {!loading && attempt === null && eligibility !== null && (
        <EligibilityCard
          eligibility={eligibility}
          starting={starting}
          onStart={start}
        />
      )}

      {attempt !== null && (
        <>
          <AttemptHeader
            attempt={attempt}
            state={state}
            remainingSeconds={view.remainingSeconds}
            timed={view.timed}
            clockOutOfSync={view.clockOutOfSync}
            skewSeconds={view.skewSeconds}
            saving={saving}
            dirtyCount={dirtyEntries.length}
            lastSavedAt={lastSavedAt}
          />

          {saveError !== null && (
            <div className="alert alert-error" role="alert">
              <strong>Your latest answers are not saved.</strong> {saveError}{' '}
              <button type="button" className="btn btn-sm" disabled={saving} onClick={() => void save('MANUAL')}>
                {saving ? 'Saving…' : 'Save now'}
              </button>
            </div>
          )}

          {view.clockOutOfSync && (
            <p className="alert alert-warning">
              This device’s clock is {Math.abs(view.skewSeconds)} second(s)
              {view.skewSeconds > 0 ? ' ahead of' : ' behind'} the server. The countdown follows the
              server, so your remaining time is unaffected — but other times shown on this device may
              look wrong.
            </p>
          )}

          {attempt.status === 'SUBMISSION_PENDING' && submissionStatus !== null && (
            <PendingSubmissionPanel status={submissionStatus} retrying={retrying} onRetry={retrySubmission} />
          )}

          {locked && attempt.status !== 'SUBMISSION_PENDING' && (
            <>
              <FinishedCard attempt={attempt} onStartAnother={() => quizId && void load(quizId)} />

              {/* UC-04 → UC-05 → UC-06, in the order the backend produces them. */}
              {resultView !== null && (
                <ScoreCard
                  result={resultView.result}
                  scores={resultView.questionScores}
                  busy={chainBusy}
                  onRescore={() => void loadChain(attempt.attemptId, { drive: true })}
                />
              )}

              {outcomeView !== null && (
                <OutcomeCard
                  outcome={outcomeView}
                  busy={chainBusy}
                  onRetryCertificate={() => void retryCertificate(attempt.attemptId)}
                  onRetryCpd={() => void retryCpd(attempt.attemptId)}
                />
              )}

              {feedbackView !== null && (
                <FeedbackCard
                  feedback={feedbackView}
                  busy={chainBusy}
                  onRegenerate={() => void loadChain(attempt.attemptId, { drive: true })}
                />
              )}

              {/*
                UC-07. Mounted here and nowhere else: coaching is a post-submission conversation, so
                there is no coaching markup on the answering screen to hide. The rule is still the
                backend's — it refuses an unsubmitted attempt — and the panel asks it rather than
                assuming, which is why it decides for itself whether to show anything.
              */}
              <CoachingPanel attemptId={attempt.attemptId} />
            </>
          )}

          {!locked && screen === 'submitting' && (
            <SubmitPanel
              preview={preview}
              loading={loadingPreview}
              submitting={submitting}
              onConfirm={confirmSubmit}
              onCancel={() => setScreen('answering')}
              onGoToPosition={(next) => void goToPosition(next)}
            />
          )}

          {!locked && screen === 'review' && state !== null && (
            <>
              <AttemptReview
                state={state}
                filter={reviewFilter}
                onFilterChange={setReviewFilter}
                onGoToQuestion={goToEntry}
                onToggleFlag={(entry) => void toggleFlag(entry)}
                busy={saving}
              />
              <div className="row" style={{ gap: 8 }}>
                <button type="button" className="btn" onClick={() => setScreen('answering')}>
                  Back to questions
                </button>
                <button type="button" className="btn btn-primary" onClick={() => void openSubmit()}>
                  Submit…
                </button>
              </div>
            </>
          )}

          {!locked && screen === 'answering' && (
            <>
              {oneAtATime && state !== null && (
                <QuestionNavigator state={state} currentPosition={position} onGoToQuestion={goToEntry} />
              )}

              {questions.map((question) => (
                <QuestionCard
                  key={question.questionId}
                  question={question}
                  response={answers.get(question.questionId)?.local ?? null}
                  flagged={state?.questions.find((entry) => entry.questionId === question.questionId)?.flagged ?? false}
                  dirty={(() => {
                    const slot = answers.get(question.questionId);
                    return slot ? !sameResponse(slot.local, slot.saved) : false;
                  })()}
                  totalQuestions={attempt.totalQuestions}
                  onChange={(next) =>
                    setAnswers((current) => {
                      const map = new Map(current);
                      const slot = map.get(question.questionId) ?? { local: null, saved: null, revision: 0 };
                      map.set(question.questionId, { ...slot, local: next });
                      return map;
                    })
                  }
                  onToggleFlag={() => {
                    const entry = state?.questions.find((item) => item.questionId === question.questionId);
                    if (entry) void toggleFlag(entry);
                  }}
                />
              ))}

              <div className="card">
                <div className="card-body row spread" style={{ flexWrap: 'wrap', gap: 8 }}>
                  <span className="row" style={{ gap: 8 }}>
                    {oneAtATime && (
                      <>
                        <button
                          type="button"
                          className="btn"
                          disabled={position <= 1}
                          onClick={() => void goToPosition(position - 1)}
                        >
                          ← Previous
                        </button>
                        <button
                          type="button"
                          className="btn"
                          disabled={position >= attempt.totalQuestions}
                          onClick={() => void goToPosition(position + 1)}
                        >
                          Next →
                        </button>
                      </>
                    )}
                  </span>
                  <span className="row" style={{ gap: 8 }}>
                    <button
                      type="button"
                      className="btn"
                      disabled={saving || dirtyEntries.length === 0}
                      onClick={() => void save('MANUAL')}
                    >
                      {saving ? 'Saving…' : 'Save now'}
                    </button>
                    <button type="button" className="btn" onClick={() => setScreen('review')}>
                      Review answers
                    </button>
                    <button type="button" className="btn btn-primary" onClick={() => void openSubmit()}>
                      Submit…
                    </button>
                  </span>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

function EligibilityCard({
  eligibility,
  starting,
  onStart,
}: {
  eligibility: AttemptEligibility;
  starting: boolean;
  onStart: () => void;
}): ReactNode {
  return (
    <div className="card">
      <div className="card-header">
        <h2>Before you start</h2>
        <span className={`badge ${eligibility.eligible ? 'badge-active' : 'badge-warning'}`}>
          {eligibility.eligible ? 'Eligible' : 'Not eligible'}
        </span>
      </div>
      <div className="card-body">
        <div className="kv">
          <div className="stat">
            <span className="stat-label">Attempts used</span>
            <span className="stat-value">{eligibility.attemptsUsed}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Remaining</span>
            <span className="stat-value">
              {eligibility.attemptsRemaining === null ? 'Unlimited' : eligibility.attemptsRemaining}
            </span>
          </div>
          <div className="stat">
            <span className="stat-label">Enrolment</span>
            <span className="stat-value">{eligibility.enrolmentStatus ?? 'None'}</span>
          </div>
        </div>

        {/* Every reason, not just the first: two things can be wrong at once. */}
        {eligibility.reasons.map((reason) => (
          <p key={reason.code} className="alert alert-warning" style={{ marginTop: 12 }}>
            {reason.message}
          </p>
        ))}

        <button
          type="button"
          className="btn btn-primary"
          disabled={!eligibility.eligible || starting}
          onClick={onStart}
        >
          {starting ? 'Starting…' : 'Start attempt'}
        </button>
        <p className="field-hint">
          Starting locks this attempt to the configuration version active right now. A later
          configuration change will not affect it.
        </p>
      </div>
    </div>
  );
}

function AttemptHeader({
  attempt,
  state,
  remainingSeconds,
  timed,
  clockOutOfSync,
  skewSeconds,
  saving,
  dirtyCount,
  lastSavedAt,
}: {
  attempt: Attempt;
  state: AttemptState | null;
  remainingSeconds: number | null;
  timed: boolean;
  clockOutOfSync: boolean;
  skewSeconds: number;
  saving: boolean;
  dirtyCount: number;
  lastSavedAt: string | null;
}): ReactNode {
  const level = urgency(remainingSeconds);

  return (
    <div className="card">
      <div className="card-header">
        <h2>
          Attempt {attempt.attemptNumber} · {PRESENTATION_LABELS[attempt.questionPresentation]}
        </h2>
        <span className={`badge ${attempt.status === 'ACTIVE' ? 'badge-active' : 'badge-neutral'}`}>
          {attempt.status}
        </span>
      </div>
      <div className="card-body">
        <div className="kv">
          <div className="stat">
            <span className="stat-label">{timed ? 'Time remaining' : 'Time limit'}</span>
            <span className={`stat-value timer timer-${level}`}>
              {timed ? formatRemaining(remainingSeconds) : 'Untimed'}
            </span>
          </div>
          <div className="stat">
            <span className="stat-label">Progress</span>
            <span className="stat-value">
              {state ? `${state.completeCount} / ${state.totalQuestions}` : '—'}
            </span>
          </div>
          <div className="stat">
            <span className="stat-label">Flagged</span>
            <span className="stat-value">{state?.flaggedCount ?? 0}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Pass mark</span>
            <span className="stat-value">
              {attempt.configuration.passMarkPercentage === null
                ? '—'
                : `${attempt.configuration.passMarkPercentage}%`}
            </span>
          </div>
        </div>

        <p className="field-hint" style={{ marginTop: 12 }}>
          Locked to configuration <code className="mono">{attempt.configurationVersionId}</code> (v
          {attempt.configuration.version}). Started {formatDate(attempt.startedAt)}
          {attempt.expiresAt ? `, due ${formatDate(attempt.expiresAt)}` : ', untimed'}.
        </p>
        <p className="field-hint">
          {saving
            ? 'Saving…'
            : dirtyCount > 0
              ? `${dirtyCount} answer(s) not yet saved.`
              : lastSavedAt
                ? `All answers saved at ${formatDate(lastSavedAt)}.`
                : 'No changes to save yet.'}
          {clockOutOfSync && ` Device clock differs from the server by ${Math.abs(skewSeconds)}s.`}
        </p>
      </div>
    </div>
  );
}

function QuestionCard({
  question,
  response,
  flagged,
  dirty,
  totalQuestions,
  onChange,
  onToggleFlag,
}: {
  question: AttemptQuestion;
  response: AnswerResponse;
  flagged: boolean;
  dirty: boolean;
  totalQuestions: number;
  onChange: (next: AnswerResponse) => void;
  onToggleFlag: () => void;
}): ReactNode {
  const complete = looksComplete(question, response);

  return (
    <div className="card" id={`question-${question.position}`}>
      <div className="card-header">
        <h2>
          Question {question.position} of {totalQuestions}
        </h2>
        <span className="row" style={{ gap: 8 }}>
          <span className="badge badge-neutral">
            {ATTEMPT_TYPE_LABELS[question.questionType] ?? question.questionType}
          </span>
          <span className={`badge ${complete ? 'badge-active' : 'badge-warning'}`}>
            {complete ? 'Answered' : 'Unanswered'}
          </span>
          {dirty && <span className="badge badge-warning">Unsaved</span>}
          <button
            type="button"
            className={`btn btn-sm${flagged ? ' btn-primary' : ''}`}
            onClick={onToggleFlag}
          >
            {flagged ? 'Flagged' : 'Flag for review'}
          </button>
        </span>
      </div>
      <div className="card-body">
        {question.scenarioText && <p className="scenario-text pre-wrap">{question.scenarioText}</p>}
        <p className="question-prompt">{question.prompt}</p>
        <QuestionInput question={question} response={response} onChange={onChange} />
        <p className="field-hint">
          {question.points} point(s). Question version {question.questionVersion} — the exact wording
          delivered to you, kept even if the question is later edited or retired.
        </p>
      </div>
    </div>
  );
}

function FinishedCard({
  attempt,
  onStartAnother,
}: {
  attempt: Attempt;
  onStartAnother: () => void;
}): ReactNode {
  const expired = attempt.submissionReason === 'TIME_EXPIRED' || attempt.status === 'EXPIRED';

  return (
    <div className="card">
      <div className="card-header">
        <h2>{expired ? 'Time is up' : 'Attempt submitted'}</h2>
        <span className="badge badge-neutral">{attempt.status}</span>
      </div>
      <div className="card-body">
        <p className={expired ? 'alert alert-warning' : 'alert alert-success'}>
          {expired
            ? 'The time limit passed, so the attempt was submitted automatically with the answers saved up to that point.'
            : 'Your answers have been submitted and the attempt is locked.'}
        </p>
        <p className="field-hint">
          Submitted {formatDate(attempt.submittedAt)}
          {attempt.submissionReason ? ` · reason ${attempt.submissionReason}` : ''}. Marking ran as
          part of the submission: the score, the pass/fail outcome and the feedback report already
          exist.
        </p>
        <button type="button" className="btn" onClick={onStartAnother}>
          Check for another attempt
        </button>
      </div>
    </div>
  );
}
