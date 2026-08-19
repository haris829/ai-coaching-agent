/**
 * Countdown arithmetic, kept pure so the clock rules are testable without waiting.
 *
 * The rule this module exists to enforce: **the server owns the clock.** The device clock is used
 * only to interpolate between server readings — never as a source of truth — because a learner can
 * change it, and a laptop that slept for ten minutes has a plausible-looking but wrong idea of now.
 *
 * So the countdown is `serverRemaining - (localNow - localNowWhenServerAnswered)`, and every
 * `resyncAfterSeconds` it is replaced outright by a fresh server reading. The interpolation can
 * drift a little; the resync bounds how far.
 *
 * `skewSeconds` compares the device clock with the server's for one purpose only: telling the
 * learner their clock is wrong. It never adjusts the remaining time. The backend takes the same
 * position — it accepts a `clientTime` parameter purely to echo the skew back.
 */

import type { AttemptTiming } from '../api/attemptTypes';

/** A server timing reading, stamped with the device clock at the moment it arrived. */
export interface TimingSample {
  timing: AttemptTiming;
  /** `Date.now()` when the response was received. Monotonic-ish anchor for interpolation. */
  receivedAtEpochMs: number;
}

export interface CountdownView {
  /** Seconds left, floored at 0. `null` for an untimed attempt. */
  remainingSeconds: number | null;
  /** True once a timed attempt's deadline has passed, by the server's reckoning plus elapsed. */
  expired: boolean;
  timed: boolean;
  /** How far the device clock is from the server's, positive when the device is ahead. */
  skewSeconds: number;
  /** True when the skew exceeds the threshold the server published. */
  clockOutOfSync: boolean;
  /** True when it is time to fetch fresh timing from the server. */
  needsResync: boolean;
}

/**
 * How often to replace the interpolated countdown with a real server reading.
 *
 * Frequent enough that drift stays imperceptible, rare enough that it is not a poll: a 30-second
 * cadence on a 30-minute quiz is 60 requests, against a countdown nobody can see drift by more than
 * a second or so.
 */
export const RESYNC_INTERVAL_SECONDS = 30;

export function countdown(
  sample: TimingSample | null,
  localNowEpochMs: number,
  resyncAfterSeconds = RESYNC_INTERVAL_SECONDS,
): CountdownView {
  if (sample === null) {
    return {
      remainingSeconds: null,
      expired: false,
      timed: false,
      skewSeconds: 0,
      clockOutOfSync: false,
      needsResync: true,
    };
  }

  const { timing, receivedAtEpochMs } = sample;
  const sinceSampleSeconds = Math.max(0, Math.floor((localNowEpochMs - receivedAtEpochMs) / 1000));

  // Positive when the device clock runs ahead of the server's.
  const skewSeconds = Math.round((receivedAtEpochMs - timing.serverTimeEpochMs) / 1000);

  const remainingSeconds =
    timing.remainingSeconds === null ? null : Math.max(0, timing.remainingSeconds - sinceSampleSeconds);

  return {
    remainingSeconds,
    // A locally interpolated zero is enough to *show* "time is up", but only the server can settle
    // the attempt — the UI reacts by fetching, never by deciding.
    expired: timing.expired || (timing.timed && remainingSeconds === 0),
    timed: timing.timed,
    skewSeconds,
    clockOutOfSync: Math.abs(skewSeconds) > timing.clockResyncThresholdSeconds,
    needsResync: sinceSampleSeconds >= resyncAfterSeconds,
  };
}

/** `mm:ss`, or `h:mm:ss` past an hour. `null` renders as an em dash. */
export function formatRemaining(seconds: number | null): string {
  if (seconds === null) return '—';
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  const pad = (value: number): string => String(value).padStart(2, '0');
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${pad(minutes)}:${pad(secs)}`;
}

/**
 * How urgent the remaining time is, for styling.
 *
 * The thresholds are deliberately generous: a warning at five minutes gives a learner time to act on
 * it, and one at sixty seconds matches the backend's own `TIME_ALMOST_ELAPSED` submission warning, so
 * the two never disagree about whether time is nearly up.
 */
export function urgency(seconds: number | null): 'none' | 'warning' | 'critical' {
  if (seconds === null) return 'none';
  if (seconds <= 60) return 'critical';
  if (seconds <= 300) return 'warning';
  return 'none';
}
