/**
 * UC-07 — "Review with Larry": post-submission Socratic coaching on the questions a learner got wrong.
 *
 * A verification surface, not a product screen, like the rest of this UI. So alongside the
 * conversation it shows the machine-readable state a reviewer needs: the session's status and mode, the
 * exchange count against its threshold, the reason code behind any refusal, and the sanitisation
 * report proving the answer key was excluded.
 *
 * WHERE THIS COMPONENT IS MOUNTED IS PART OF THE REQUIREMENT
 * ----------------------------------------------------------
 * It renders only on the post-submission result screen. That is how "coaching controls are unavailable
 * during an active quiz session" looks from the front: there is no coaching markup on the answering
 * screen to hide. The rule itself is not enforced here, though, and must not be — the backend refuses
 * an unsubmitted attempt with `ATTEMPT_NOT_SUBMITTED`, and this panel would render that refusal if it
 * were ever mounted somewhere it should not be. A hidden button is not protection.
 *
 * NOTHING HERE DECIDES ANYTHING
 * -----------------------------
 * Whether to offer coaching, which questions are eligible, when the five-exchange choice appears,
 * whether a session may take a message: every one of those arrives decided, and `lib/coaching.ts`
 * turns the answer into labels. This file is the rendering and the fetching, and that is all.
 *
 * **There is no correct answer on this screen.** The learner reads that on their feedback report,
 * which is a different card with a different purpose. Coaching is about working it out.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import { ApiError, coaching } from '../../api/client';
import type {
  CoachingEligibility,
  CoachingExchange,
  CoachingMessage,
  ReviewItem,
  ReviewQueue,
  Sanitization,
  SessionState,
  StartCoaching,
} from '../../api/coachingTypes';
import {
  controls,
  focusQuestion,
  itemActionLabel,
  reviewActionLabel,
  sanitizationSummary,
  visibility,
} from '../../lib/coaching';
import { formatDate } from '../ui';

interface Conversation {
  readonly state: SessionState;
  readonly sanitization: Sanitization | null;
  /** The reason code when the coach could not speak. Never a provider message. */
  readonly unavailableReason: string | null;
  readonly retryable: boolean;
}

function conversationOf(payload: StartCoaching | CoachingExchange | SessionState): Conversation {
  const unavailable = 'reason' in payload ? payload.reason : null;
  return {
    state: { session: payload.session, messageCount: payload.messageCount, messages: payload.messages },
    sanitization: 'sanitization' in payload ? payload.sanitization : null,
    unavailableReason: unavailable ?? null,
    retryable: 'retryable' in payload ? payload.retryable : false,
  };
}

export function CoachingPanel({ attemptId }: { attemptId: string }): ReactNode {
  const [eligibility, setEligibility] = useState<CoachingEligibility | null>(null);
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const view = visibility(eligibility);
  const session = conversation?.state.session ?? null;
  const available = controls(session);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const check = await coaching.eligibility(attemptId);
      setEligibility(check);
      // The queue is only meaningful once the gate has opened; asking for it otherwise would produce
      // a refusal the eligibility call has already explained.
      setQueue(check.coachingAvailable ? await coaching.review(attemptId) : null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Coaching could not be checked.');
    } finally {
      setLoaded(true);
    }
  }, [attemptId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /** Run one coaching call, keeping the panel's error and busy state honest. */
  const run = useCallback(
    async (action: () => Promise<Conversation | null>, { reloadQueue = true } = {}) => {
      setBusy(true);
      setError(null);
      try {
        const next = await action();
        if (next !== null) setConversation(next);
        if (reloadQueue) setQueue(await coaching.review(attemptId));
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : 'The coaching request failed.');
      } finally {
        setBusy(false);
      }
    },
    [attemptId],
  );

  const start = (questionId: string) =>
    run(async () => conversationOf(await coaching.start(attemptId, questionId)));

  const send = () => {
    const message = draft.trim();
    if (!message || session === null) return;
    setDraft('');
    return run(async () => conversationOf(await coaching.send(session.sessionId, message)), {
      reloadQueue: false,
    });
  };

  const setMode = (mode: 'SOCRATIC' | 'DIRECT_EXPLANATION') =>
    session === null
      ? undefined
      : run(async () => conversationOf(await coaching.selectMode(session.sessionId, mode)), {
          reloadQueue: false,
        });

  const retry = () =>
    session === null
      ? undefined
      : run(async () => conversationOf(await coaching.retry(session.sessionId)), {
          reloadQueue: false,
        });

  const finishQuestion = () =>
    session === null
      ? undefined
      : run(async () => conversationOf(await coaching.complete(session.sessionId)));

  /** Finish with this question and open the next one, which is the review-all-wrong-answers flow. */
  const nextQuestion = () =>
    run(async () => {
      const advanced = await coaching.nextQuestion(attemptId);
      setQueue(advanced.review);
      if (advanced.nextQuestion === null) {
        setConversation(null);
        return null;
      }
      return conversationOf(await coaching.start(attemptId, advanced.nextQuestion.questionId));
    }, { reloadQueue: false });

  if (!loaded) return null;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Review with Larry</h2>
        <span className={`badge ${view.offer ? 'badge-active' : 'badge-neutral'}`}>
          {view.offer ? `${view.incorrectCount} to review` : (eligibility?.reason ?? 'unavailable')}
        </span>
      </div>
      <div className="card-body">
        {error !== null && (
          <p className="alert alert-error" role="alert">
            {error}
          </p>
        )}

        {/* The action is not offered. Say why, in the defined wording, and say what is unaffected. */}
        {!view.offer && (
          <>
            <p className={view.retryable ? 'alert alert-warning' : 'field-hint'}>{view.message}</p>
            {view.retryable && (
              <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void refresh()}>
                Check again
              </button>
            )}
          </>
        )}

        {view.offer && (
          <>
            <p className="field-hint">
              Larry coaches by asking, not by telling — he has never been given the answer key. Work
              through a question with him, and after{' '}
              {session?.directExplanationThreshold ?? 5} exchanges you can ask for the concept to be
              explained directly.
            </p>

            <ReviewQueueList
              queue={queue}
              busy={busy}
              activeQuestionId={session?.questionId ?? null}
              onOpen={(questionId) => void start(questionId)}
            />

            {conversation === null && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || focusQuestion(queue) === null}
                onClick={() => {
                  const focus = focusQuestion(queue);
                  if (focus) void start(focus.questionId);
                }}
              >
                {busy ? 'Opening…' : reviewActionLabel(queue)}
              </button>
            )}

            {conversation !== null && session !== null && (
              <ConversationView
                conversation={conversation}
                busy={busy}
                draft={draft}
                controls={available}
                onDraft={setDraft}
                onSend={() => void send()}
                onAskForExplanation={() => void setMode('DIRECT_EXPLANATION')}
                onReturnToSocratic={() => void setMode('SOCRATIC')}
                onRetry={() => void retry()}
                onFinish={() => void finishQuestion()}
                onNextQuestion={() => void nextQuestion()}
                hasMoreQuestions={(queue?.remainingCount ?? 0) > 0}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Every incorrect question, in delivery order, with its own coaching progress. */
function ReviewQueueList({
  queue,
  busy,
  activeQuestionId,
  onOpen,
}: {
  queue: ReviewQueue | null;
  busy: boolean;
  activeQuestionId: string | null;
  onOpen: (questionId: string) => void;
}): ReactNode {
  if (queue === null || queue.items.length === 0) return null;

  return (
    <table className="table" style={{ marginBottom: '0.75rem' }}>
      <thead>
        <tr>
          <th>#</th>
          <th>Topic</th>
          <th>Status</th>
          <th>Exchanges</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {queue.items.map((item) => (
          <ReviewRow
            key={item.questionId}
            item={item}
            busy={busy}
            active={item.questionId === activeQuestionId}
            onOpen={onOpen}
          />
        ))}
      </tbody>
    </table>
  );
}

const ITEM_TONE: Record<ReviewItem['status'], string> = {
  PENDING: 'badge-neutral',
  IN_PROGRESS: 'badge-warning',
  COMPLETED: 'badge-active',
};

function ReviewRow({
  item,
  busy,
  active,
  onOpen,
}: {
  item: ReviewItem;
  busy: boolean;
  active: boolean;
  onOpen: (questionId: string) => void;
}): ReactNode {
  return (
    <tr>
      <td>{item.position}</td>
      <td>{item.topic ?? '—'}</td>
      <td>
        <span className={`badge ${ITEM_TONE[item.status]}`}>{item.status}</span>
      </td>
      <td>{item.exchangeCount}</td>
      <td>
        <button
          type="button"
          className="btn btn-sm"
          // `coachingAvailable` is the backend's per-question flag, read rather than derived.
          disabled={busy || active || !item.coachingAvailable}
          onClick={() => onOpen(item.questionId)}
        >
          {active ? 'Open' : itemActionLabel(item)}
        </button>
      </td>
    </tr>
  );
}

/** One question's coaching conversation. */
function ConversationView({
  conversation,
  busy,
  draft,
  controls: available,
  onDraft,
  onSend,
  onAskForExplanation,
  onReturnToSocratic,
  onRetry,
  onFinish,
  onNextQuestion,
  hasMoreQuestions,
}: {
  conversation: Conversation;
  busy: boolean;
  draft: string;
  controls: ReturnType<typeof controls>;
  onDraft: (value: string) => void;
  onSend: () => void;
  onAskForExplanation: () => void;
  onReturnToSocratic: () => void;
  onRetry: () => void;
  onFinish: () => void;
  onNextQuestion: () => void;
  hasMoreQuestions: boolean;
}): ReactNode {
  const { session, messages } = conversation.state;
  const bottom = useRef<HTMLDivElement | null>(null);
  const sanitised = sanitizationSummary(conversation.sanitization);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'nearest' });
  }, [messages.length]);

  return (
    <div className="card" style={{ marginTop: '0.75rem' }}>
      <div className="card-header">
        <h3>
          Question {session.questionPosition ?? '—'}
          {session.topic ? ` · ${session.topic}` : ''}
        </h3>
        <span className={`badge ${session.status === 'ACTIVE' ? 'badge-active' : 'badge-warning'}`}>
          {session.status} · {session.mode}
        </span>
      </div>
      <div className="card-body">
        <p className="field-hint">
          {session.exchangeCount} of {session.directExplanationThreshold} exchanges before the
          direct-explanation choice · session {session.sessionId} · updated{' '}
          {formatDate(session.updatedAt)}
          {sanitised ? ` · ${sanitised}` : ''}
        </p>

        {/* An AI outage. The session, the conversation and the learner's message are all intact. */}
        {conversation.unavailableReason !== null && (
          <p className="alert alert-warning" role="alert">
            <strong>Larry could not reply just now</strong> ({conversation.unavailableReason}). Your
            quiz result and feedback are unaffected, and nothing you have typed has been lost.
            {conversation.retryable ? ' Try again in a moment.' : ''}
          </p>
        )}

        <div
          className="conversation"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
            maxHeight: '22rem',
            overflowY: 'auto',
            marginBottom: '0.75rem',
          }}
        >
          {messages.length === 0 && (
            <p className="field-hint">Larry has not spoken yet. Retry to open the conversation.</p>
          )}
          {messages.map((message) => (
            <Turn key={message.index} message={message} />
          ))}
          <div ref={bottom} />
        </div>

        {available.transitionHint !== null && (
          <p className="field-hint">{available.transitionHint}</p>
        )}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            onSend();
          }}
          style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}
        >
          <input
            type="text"
            value={draft}
            disabled={!available.canSend || busy}
            placeholder={
              available.canSend
                ? 'Tell Larry what you were thinking…'
                : 'This conversation is not accepting messages.'
            }
            onChange={(event) => onDraft(event.target.value)}
            style={{ flex: 1 }}
          />
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!available.canSend || busy || draft.trim() === ''}
          >
            {busy ? 'Sending…' : 'Send'}
          </button>
        </form>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
          {available.canAskForExplanation && (
            <button type="button" className="btn btn-sm" disabled={busy} onClick={onAskForExplanation}>
              Explain the concept instead
            </button>
          )}
          {available.canReturnToSocratic && (
            <button type="button" className="btn btn-sm" disabled={busy} onClick={onReturnToSocratic}>
              Go back to working it through
            </button>
          )}
          {available.canRetry && (
            <button type="button" className="btn btn-sm" disabled={busy} onClick={onRetry}>
              Retry
            </button>
          )}
          {available.canComplete && (
            <button type="button" className="btn btn-sm" disabled={busy} onClick={onFinish}>
              Finish with this question
            </button>
          )}
          {hasMoreQuestions && (
            <button type="button" className="btn btn-sm" disabled={busy} onClick={onNextQuestion}>
              Next wrong answer
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Turn({ message }: { message: CoachingMessage }): ReactNode {
  const fromCoach = message.role === 'COACH';
  return (
    <div
      style={{
        alignSelf: fromCoach ? 'flex-start' : 'flex-end',
        maxWidth: '85%',
        padding: '0.5rem 0.75rem',
        borderRadius: '0.5rem',
        background: fromCoach ? 'var(--surface-2, #f3f4f6)' : 'var(--accent-soft, #e0e7ff)',
      }}
    >
      <strong style={{ display: 'block', fontSize: '0.75rem', opacity: 0.7 }}>
        {fromCoach ? `Larry${message.mode ? ` · ${message.mode}` : ''}` : 'You'}
      </strong>
      <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span>
    </div>
  );
}
