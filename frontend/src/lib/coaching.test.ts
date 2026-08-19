import { describe, expect, it } from 'vitest';

import type {
  CoachingEligibility,
  CoachingSession,
  ReviewItem,
  ReviewQueue,
} from '../api/coachingTypes';
import {
  controls,
  focusQuestion,
  itemActionLabel,
  reviewActionLabel,
  sanitizationSummary,
  unavailableMessage,
  visibility,
} from './coaching';

function eligibility(overrides: Partial<CoachingEligibility> = {}): CoachingEligibility {
  return {
    attemptId: 'attempt-1',
    coachingAvailable: true,
    reason: 'ELIGIBLE',
    message: null,
    retryable: false,
    details: null,
    questions: [],
    incorrectQuestionCount: 3,
    ...overrides,
  };
}

function item(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    questionId: 'q-1',
    position: 1,
    status: 'PENDING',
    topic: 'Reporting concerns',
    sessionId: null,
    exchangeCount: 0,
    coachingAvailable: true,
    ...overrides,
  };
}

function queue(overrides: Partial<ReviewQueue> = {}): ReviewQueue {
  const items = overrides.items ?? [item()];
  return {
    attemptId: 'attempt-1',
    totalIncorrect: items.length,
    completedCount: items.filter((entry) => entry.status === 'COMPLETED').length,
    remainingCount: items.filter((entry) => entry.status !== 'COMPLETED').length,
    finished: items.length > 0 && items.every((entry) => entry.status === 'COMPLETED'),
    items,
    nextQuestionId: items.find((entry) => entry.status !== 'COMPLETED')?.questionId ?? null,
    ...overrides,
  };
}

function session(overrides: Partial<CoachingSession> = {}): CoachingSession {
  return {
    sessionId: 'session-1',
    learnerId: '2',
    attemptId: 'attempt-1',
    courseId: '1',
    questionId: 'q-1',
    questionPosition: 1,
    topic: 'Reporting concerns',
    mode: 'SOCRATIC',
    status: 'ACTIVE',
    exchangeCount: 0,
    directExplanationAvailable: false,
    directExplanationOffered: false,
    directExplanationThreshold: 5,
    exchangesUntilChoice: 5,
    startedAt: '2026-03-01T09:00:00Z',
    updatedAt: '2026-03-01T09:00:00Z',
    completedAt: null,
    lastFailureCode: null,
    revision: 1,
    ...overrides,
  };
}

describe('visibility', () => {
  it('offers coaching when the backend says it is available and there is something to review', () => {
    const result = visibility(eligibility());

    expect(result.offer).toBe(true);
    expect(result.incorrectCount).toBe(3);
    expect(result.message).toBe('');
  });

  it('offers nothing before the eligibility check has answered', () => {
    // A button that is about to be refused is worse than a moment of nothing.
    expect(visibility(null).offer).toBe(false);
  });

  it('hides coaching during an active attempt, with the reason the backend gave', () => {
    // The active-quiz protection is the backend's; this only renders what it said.
    const result = visibility(
      eligibility({
        coachingAvailable: false,
        reason: 'ATTEMPT_NOT_SUBMITTED',
        message: null,
        retryable: true,
        incorrectQuestionCount: 0,
      }),
    );

    expect(result.offer).toBe(false);
    expect(result.message).toContain('submitted');
    expect(result.retryable).toBe(true);
  });

  it('prefers the backend’s own message when it supplied one', () => {
    const result = visibility(
      eligibility({
        coachingAvailable: false,
        reason: 'FEEDBACK_UNAVAILABLE',
        message: 'Feedback for this attempt has not been released yet.',
      }),
    );

    expect(result.message).toBe('Feedback for this attempt has not been released yet.');
  });

  it('reports the defined temporary-unavailable message when the service is down', () => {
    const result = visibility(
      eligibility({ coachingAvailable: false, reason: 'SERVICE_UNAVAILABLE', retryable: true }),
    );

    expect(result.offer).toBe(false);
    expect(result.message).toContain('temporarily unavailable');
    // The learner is told their result is unaffected, which is the part that matters to them.
    expect(result.message).toContain('unaffected');
    expect(result.retryable).toBe(true);
  });

  it('says there is nothing to review when every answer was correct', () => {
    const result = visibility(eligibility({ incorrectQuestionCount: 0 }));

    expect(result.offer).toBe(false);
    expect(result.message).toContain('nothing to review');
  });
});

describe('unavailableMessage', () => {
  it('falls back to a generic line for an unrecognised reason', () => {
    // A new backend reason must not render as an empty box.
    expect(unavailableMessage('SOMETHING_NEW')).toContain('not available');
    expect(unavailableMessage(null)).toContain('not available');
  });
});

describe('reviewActionLabel', () => {
  it('names the number of wrong answers when the review has not started', () => {
    expect(reviewActionLabel(queue({ items: [item(), item({ questionId: 'q-2' })] }))).toBe(
      'Review all 2 wrong answers with Larry',
    );
  });

  it('does not count when there is only one', () => {
    expect(reviewActionLabel(queue())).toBe('Review with Larry');
  });

  it('offers to continue a review in progress, with what is left', () => {
    const label = reviewActionLabel(
      queue({
        items: [item({ status: 'COMPLETED' }), item({ questionId: 'q-2' }), item({ questionId: 'q-3' })],
      }),
    );

    expect(label).toBe('Continue reviewing with Larry (2 left)');
  });

  it('offers a fresh pass once everything has been reviewed', () => {
    expect(reviewActionLabel(queue({ items: [item({ status: 'COMPLETED' })] }))).toBe(
      'Review again with Larry',
    );
  });
});

describe('itemActionLabel', () => {
  it('reflects each item’s own progress', () => {
    expect(itemActionLabel(item())).toBe('Review with Larry');
    expect(itemActionLabel(item({ status: 'IN_PROGRESS' }))).toBe('Continue');
    expect(itemActionLabel(item({ status: 'COMPLETED' }))).toBe('Reviewed');
  });
});

describe('focusQuestion', () => {
  it('returns the learner to the conversation they were having', () => {
    // An in-progress question wins over an unstarted one: stepping away and coming back should not
    // open a new conversation.
    const focused = focusQuestion(
      queue({
        items: [
          item({ questionId: 'q-1', status: 'COMPLETED' }),
          item({ questionId: 'q-2' }),
          item({ questionId: 'q-3', status: 'IN_PROGRESS' }),
        ],
      }),
    );

    expect(focused?.questionId).toBe('q-3');
  });

  it('otherwise opens the first question not yet started', () => {
    const focused = focusQuestion(
      queue({ items: [item({ questionId: 'q-1', status: 'COMPLETED' }), item({ questionId: 'q-2' })] }),
    );

    expect(focused?.questionId).toBe('q-2');
  });

  it('returns nothing when the review is finished', () => {
    expect(focusQuestion(queue({ items: [item({ status: 'COMPLETED' })] }))).toBeNull();
  });
});

describe('controls', () => {
  it('lets a learner talk to an active session', () => {
    const result = controls(session());

    expect(result.canSend).toBe(true);
    expect(result.canComplete).toBe(true);
    expect(result.canRetry).toBe(false);
  });

  it('does not offer a direct explanation until the backend says it is available', () => {
    // Read, never counted here: five messages on screen is not the rule, the flag is.
    expect(controls(session({ exchangeCount: 5 })).canAskForExplanation).toBe(false);
    expect(
      controls(session({ exchangeCount: 5, directExplanationAvailable: true }))
        .canAskForExplanation,
    ).toBe(true);
  });

  it('offers the way back to Socratic coaching, and not the way there twice', () => {
    const explaining = controls(
      session({ mode: 'DIRECT_EXPLANATION', directExplanationAvailable: true }),
    );

    expect(explaining.canReturnToSocratic).toBe(true);
    expect(explaining.canAskForExplanation).toBe(false);
  });

  it('offers a retry when a session stalled, and does not let the learner type into the void', () => {
    for (const status of ['FAILED', 'UNAVAILABLE'] as const) {
      const stalled = controls(session({ status }));
      expect(stalled.canRetry).toBe(true);
      expect(stalled.canSend).toBe(false);
    }
  });

  it('closes a completed session down', () => {
    const done = controls(session({ status: 'COMPLETED', completedAt: '2026-03-01T09:30:00Z' }));

    expect(done.canSend).toBe(false);
    expect(done.canComplete).toBe(false);
    expect(done.transitionHint).toBeNull();
  });

  it('counts down to the choice, then describes it', () => {
    expect(controls(session({ exchangesUntilChoice: 3 })).transitionHint).toContain('3 more');
    expect(controls(session({ exchangesUntilChoice: 1 })).transitionHint).toContain('One more');
    expect(
      controls(session({ directExplanationAvailable: true, exchangesUntilChoice: 0 }))
        .transitionHint,
    ).toContain('explain the concept directly');
  });

  it('offers nothing without a session', () => {
    expect(controls(null).canSend).toBe(false);
  });
});

describe('sanitizationSummary', () => {
  it('reports that the answer key was excluded, and how much was removed', () => {
    const summary = sanitizationSummary({
      removedFields: ['uc04.question_result.answer_key', 'uc06.question_feedback.explanation'],
      answerKeyExcluded: true,
    });

    expect(summary).toContain('Answer key excluded');
    expect(summary).toContain('2 answer-bearing fields removed');
  });

  it('reports the exclusion even when there was nothing to remove', () => {
    expect(sanitizationSummary({ removedFields: [], answerKeyExcluded: true })).toBe(
      'Answer key excluded from the coaching context.',
    );
  });

  it('says nothing when there is no report', () => {
    expect(sanitizationSummary(null)).toBeNull();
  });
});
