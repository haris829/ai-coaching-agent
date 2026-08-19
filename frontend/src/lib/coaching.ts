/**
 * What the coaching panel should show, derived from what the backend said.
 *
 * The panel renders; this decides. Every rule below is a *presentation* rule — whether to show a
 * button, which label to put on it, what to say when coaching is off. None of them is a coaching rule:
 * the backend decides eligibility, the exchange count, the five-exchange transition and whether a
 * session may accept a message, and this module never second-guesses any of it.
 *
 * That split is the whole point of UC-07's §4 and §8. A frontend that worked out for itself whether
 * coaching was allowed would eventually disagree with the backend, and the version a learner sees
 * would be the wrong one. So:
 *
 * * `coachingAvailable` is **read**, never computed — see `visibility` below;
 * * "hide coaching during an attempt" is not a rule this file knows; it falls out of the backend
 *   answering `ATTEMPT_NOT_SUBMITTED`, and of the panel only being mounted on the result screen;
 * * the direct-explanation choice appears when the backend says `directExplanationAvailable`, not when
 *   this code counts five messages.
 *
 * Kept separate from the component, and unit-tested, for the same reason `attemptTimer` and
 * `configurationRules` are: these are the decisions worth having tests for, and a React component is
 * an awkward place to test a decision.
 */

import type {
  CoachingEligibility,
  CoachingSession,
  ReviewItem,
  ReviewQueue,
} from '../api/coachingTypes';

/** The defined message for each reason coaching is not being offered. */
export const UNAVAILABLE_MESSAGES: Readonly<Record<string, string>> = {
  ATTEMPT_NOT_SUBMITTED:
    'Review with Larry becomes available once you have submitted this quiz.',
  SCORE_NOT_CONFIRMED:
    'Your score is still being confirmed. Review with Larry will be available shortly.',
  FEEDBACK_UNAVAILABLE:
    'Review with Larry becomes available once your detailed feedback has been released.',
  QUESTION_NOT_INCORRECT: 'You answered this question correctly, so there is nothing to review.',
  QUESTION_NOT_IN_ATTEMPT: 'This question is not part of this attempt.',
  ATTEMPT_NOT_FOUND: 'This attempt could not be found.',
  NOT_ATTEMPT_OWNER: 'This attempt belongs to a different learner.',
  // §27's defined temporary-unavailable message. The learner is told plainly that the coach is off
  // and, just as importantly, that nothing about their result is affected by it.
  SERVICE_UNAVAILABLE:
    'AI coaching is temporarily unavailable. Your quiz result and feedback are unaffected — please try again shortly.',
};

const GENERIC_UNAVAILABLE =
  'AI coaching is not available for this attempt at the moment. Your quiz result and feedback are unaffected.';

/** The message to show when coaching is not being offered, for any reason. */
export function unavailableMessage(reason: string | null | undefined): string {
  if (!reason) return GENERIC_UNAVAILABLE;
  return UNAVAILABLE_MESSAGES[reason] ?? GENERIC_UNAVAILABLE;
}

/** Whether the panel should show the coaching action at all, and what to say if not. */
export interface Visibility {
  /** Show the "Review with Larry" action. */
  readonly offer: boolean;
  /** How many questions could be reviewed. */
  readonly incorrectCount: number;
  /** The message to show in place of the action. Empty when the action is offered. */
  readonly message: string;
  /** True when trying again later could change the answer, so a Retry control is worth showing. */
  readonly retryable: boolean;
}

/**
 * Read the backend's verdict.
 *
 * `eligibility` being `null` means the check has not come back yet — treated as "do not offer",
 * because offering a button that is about to be refused is worse than a moment of nothing.
 *
 * A learner with no incorrect answers is not offered coaching either, and that is a pleasant reason
 * rather than a refusal: there is nothing to review.
 */
export function visibility(eligibility: CoachingEligibility | null): Visibility {
  if (eligibility === null) {
    return { offer: false, incorrectCount: 0, message: '', retryable: false };
  }

  if (!eligibility.coachingAvailable) {
    return {
      offer: false,
      incorrectCount: eligibility.incorrectQuestionCount,
      // The backend's own message when it supplied one — it knows which precondition is outstanding
      // — and the defined wording for that reason otherwise.
      message: eligibility.message ?? unavailableMessage(eligibility.reason),
      retryable: eligibility.retryable,
    };
  }

  if (eligibility.incorrectQuestionCount === 0) {
    return {
      offer: false,
      incorrectCount: 0,
      message: 'You answered every question correctly — there is nothing to review.',
      retryable: false,
    };
  }

  return {
    offer: true,
    incorrectCount: eligibility.incorrectQuestionCount,
    message: '',
    retryable: false,
  };
}

/** The label for the action that opens the review, given the queue's progress. */
export function reviewActionLabel(queue: ReviewQueue | null): string {
  if (queue === null || queue.totalIncorrect === 0) return 'Review with Larry';
  if (queue.finished) return 'Review again with Larry';
  if (queue.completedCount > 0) {
    return `Continue reviewing with Larry (${queue.remainingCount} left)`;
  }
  if (queue.totalIncorrect === 1) return 'Review with Larry';
  return `Review all ${queue.totalIncorrect} wrong answers with Larry`;
}

/** The label for one question's own action, from that item's status. */
export function itemActionLabel(item: ReviewItem): string {
  switch (item.status) {
    case 'COMPLETED':
      return 'Reviewed';
    case 'IN_PROGRESS':
      return 'Continue';
    default:
      return 'Review with Larry';
  }
}

/** Which question the review should open on: the one in progress, else the first not started. */
export function focusQuestion(queue: ReviewQueue | null): ReviewItem | null {
  if (queue === null) return null;
  const inProgress = queue.items.find((item) => item.status === 'IN_PROGRESS');
  if (inProgress) return inProgress;
  return queue.items.find((item) => item.status === 'PENDING') ?? null;
}

/** What the conversation's controls should allow, given the session the backend returned. */
export interface Controls {
  /** The learner may type. */
  readonly canSend: boolean;
  /** The learner may ask for the concept to be explained directly. */
  readonly canAskForExplanation: boolean;
  /** The learner may go back to being asked questions. */
  readonly canReturnToSocratic: boolean;
  /** A coach turn failed and can be retried. */
  readonly canRetry: boolean;
  /** The learner may finish with this question. */
  readonly canComplete: boolean;
  /** A one-line hint about the five-exchange transition, or null when there is nothing to say. */
  readonly transitionHint: string | null;
}

export function controls(session: CoachingSession | null): Controls {
  if (session === null) {
    return {
      canSend: false,
      canAskForExplanation: false,
      canReturnToSocratic: false,
      canRetry: false,
      canComplete: false,
      transitionHint: null,
    };
  }

  const live = session.status === 'ACTIVE';
  const stalled = session.status === 'FAILED' || session.status === 'UNAVAILABLE';

  return {
    canSend: live,
    // Read, not counted. The threshold is the backend's, and it is the one in force for *this*
    // session — changing the configuration cannot move it under a conversation already running.
    canAskForExplanation:
      session.directExplanationAvailable && session.mode === 'SOCRATIC' && live,
    canReturnToSocratic: session.mode === 'DIRECT_EXPLANATION' && live,
    canRetry: stalled,
    canComplete: session.status !== 'COMPLETED',
    transitionHint: transitionHint(session),
  };
}

function transitionHint(session: CoachingSession): string | null {
  if (session.status === 'COMPLETED') return null;
  if (session.directExplanationAvailable) {
    return session.mode === 'DIRECT_EXPLANATION'
      ? 'Larry is explaining the concept. You can go back to working it through whenever you like.'
      : 'You can keep working this through, or ask Larry to explain the concept directly.';
  }
  const remaining = session.exchangesUntilChoice;
  if (remaining <= 0) return null;
  return remaining === 1
    ? 'One more exchange and you can ask Larry to explain the concept directly.'
    : `After ${remaining} more exchanges you can ask Larry to explain the concept directly.`;
}

/**
 * A short, human line describing what the sanitiser removed.
 *
 * Shown in the panel because it is the visible evidence of the answer-key boundary: a reviewer can
 * read on screen that the key was excluded rather than taking it on trust. It reports **names and
 * counts only** — putting a removed value on screen would recreate the leak in the one place everyone
 * looks.
 */
export function sanitizationSummary(
  sanitization: { readonly removedFields: ReadonlyArray<string>; readonly answerKeyExcluded: boolean } | null,
): string | null {
  if (sanitization === null) return null;
  const removed = sanitization.removedFields.length;
  if (!sanitization.answerKeyExcluded) return null;
  return removed === 0
    ? 'Answer key excluded from the coaching context.'
    : `Answer key excluded from the coaching context (${removed} answer-bearing field${removed === 1 ? '' : 's'} removed).`;
}
