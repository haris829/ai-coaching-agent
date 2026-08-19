import { describe, expect, it } from 'vitest';

import type { AttemptTiming } from '../api/attemptTypes';
import { countdown, formatRemaining, urgency, type TimingSample } from './attemptTimer';

const SERVER_EPOCH_MS = Date.UTC(2026, 2, 1, 9, 0, 0);

function timing(overrides: Partial<AttemptTiming> = {}): AttemptTiming {
  return {
    serverTime: '2026-03-01T09:00:00Z',
    serverTimeEpochMs: SERVER_EPOCH_MS,
    status: 'ACTIVE',
    startedAt: '2026-03-01T09:00:00Z',
    expiresAt: '2026-03-01T09:30:00Z',
    timeLimitSeconds: 1800,
    timed: true,
    elapsedSeconds: 0,
    remainingSeconds: 1800,
    expired: false,
    submittedAt: null,
    clockResyncThresholdSeconds: 5,
    autosaveIntervalSeconds: 20,
    ...overrides,
  };
}

function sample(overrides: Partial<AttemptTiming> = {}, receivedAtEpochMs = SERVER_EPOCH_MS): TimingSample {
  return { timing: timing(overrides), receivedAtEpochMs };
}

describe('countdown', () => {
  it('interpolates between server readings', () => {
    const view = countdown(sample(), SERVER_EPOCH_MS + 10_000);
    expect(view.remainingSeconds).toBe(1790);
    expect(view.expired).toBe(false);
  });

  it('floors at zero rather than going negative', () => {
    const view = countdown(sample({ remainingSeconds: 5 }), SERVER_EPOCH_MS + 60_000);
    expect(view.remainingSeconds).toBe(0);
    expect(view.expired).toBe(true);
  });

  it('never lets a device clock jumped backwards add time', () => {
    // A learner who winds their clock back would otherwise gain time through the interpolation.
    // The elapsed term is clamped at zero, so the countdown can only ever fall.
    const view = countdown(sample(), SERVER_EPOCH_MS - 600_000);
    expect(view.remainingSeconds).toBe(1800);
  });

  it('reports a device clock ahead of the server as positive skew', () => {
    const view = countdown(sample({}, SERVER_EPOCH_MS + 9_000), SERVER_EPOCH_MS + 9_000);
    expect(view.skewSeconds).toBe(9);
    expect(view.clockOutOfSync).toBe(true);
  });

  it('reports a device clock behind the server as negative skew', () => {
    const view = countdown(sample({}, SERVER_EPOCH_MS - 9_000), SERVER_EPOCH_MS - 9_000);
    expect(view.skewSeconds).toBe(-9);
    expect(view.clockOutOfSync).toBe(true);
  });

  it('leaves a skew within the published threshold alone', () => {
    const view = countdown(sample({}, SERVER_EPOCH_MS + 3_000), SERVER_EPOCH_MS + 3_000);
    expect(view.clockOutOfSync).toBe(false);
  });

  it('does not let skew change the remaining time', () => {
    // The whole point: the skew is advisory. A device 10 minutes ahead sees the same countdown.
    const honest = countdown(sample(), SERVER_EPOCH_MS + 5_000);
    const skewed = countdown(sample({}, SERVER_EPOCH_MS + 600_000), SERVER_EPOCH_MS + 605_000);
    expect(skewed.remainingSeconds).toBe(honest.remainingSeconds);
  });

  it('asks for a resync once the sample is stale', () => {
    expect(countdown(sample(), SERVER_EPOCH_MS + 29_000).needsResync).toBe(false);
    expect(countdown(sample(), SERVER_EPOCH_MS + 30_000).needsResync).toBe(true);
  });

  it('reports an untimed attempt as having no deadline', () => {
    const view = countdown(sample({ timed: false, remainingSeconds: null, expiresAt: null }), SERVER_EPOCH_MS);
    expect(view.remainingSeconds).toBeNull();
    expect(view.timed).toBe(false);
    expect(view.expired).toBe(false);
  });

  it('trusts the server when it says the attempt already expired', () => {
    const view = countdown(sample({ expired: true, remainingSeconds: 0 }), SERVER_EPOCH_MS);
    expect(view.expired).toBe(true);
  });

  it('asks for a reading when it has none', () => {
    expect(countdown(null, SERVER_EPOCH_MS).needsResync).toBe(true);
  });
});

describe('formatRemaining', () => {
  it('pads minutes and seconds', () => {
    expect(formatRemaining(65)).toBe('01:05');
    expect(formatRemaining(9)).toBe('00:09');
  });

  it('adds hours past an hour', () => {
    expect(formatRemaining(3_665)).toBe('1:01:05');
  });

  it('renders an untimed attempt as a dash', () => {
    expect(formatRemaining(null)).toBe('—');
  });
});

describe('urgency', () => {
  it('matches the backend threshold for "time is nearly up"', () => {
    // The backend warns at 60 seconds via TIME_ALMOST_ELAPSED; the UI must not disagree.
    expect(urgency(60)).toBe('critical');
    expect(urgency(61)).toBe('warning');
    expect(urgency(300)).toBe('warning');
    expect(urgency(301)).toBe('none');
    expect(urgency(null)).toBe('none');
  });
});
