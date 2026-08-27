import { describe, expect, it } from 'vitest';
import { createHarness, QUESTIONS } from './helpers';
import {
  SESSION_MAIN,
  SESSION_SECOND,
  USER_ENROLLED,
} from '../src/adapters/mock/fixtures';
import {
  ActivityType,
  DEFAULT_FRAMING_ORDER,
  DifficultySignalType,
  FramingType,
  TurnIntent,
} from '../src/domain/enums';
import {
  fingerprintExplanation,
  isEffectivelyIdentical,
} from '../src/core/explanation/fingerprint';
import { FramingSelector } from '../src/core/explanation/framing-selector';

describe('explain differently', () => {
  it('changes the framing on every request', async () => {
    const h = createHarness();
    const first = await h.ask(QUESTIONS.lessonConcept);
    const second = await h.explainDifferently();
    const third = await h.explainDifferently();

    expect(first.framing).toBe(FramingType.DIRECT);
    expect(second.framing).not.toBe(first.framing);
    expect(third.framing).not.toBe(second.framing);
    expect(third.framing).not.toBe(first.framing);
    // All three are still about the same concept.
    expect(new Set([first.concept_id, second.concept_id, third.concept_id]).size).toBe(1);
  });

  it('never repeats an explanation within a session, even past framing exhaustion', async () => {
    const h = createHarness();
    const answers: string[] = [];

    const first = await h.ask(QUESTIONS.lessonConcept);
    answers.push(first.answer!);

    // Six framings are configured; go well past that.
    for (let i = 0; i < 11; i += 1) {
      const next = await h.explainDifferently();
      expect(next.answer).toBeTruthy();
      answers.push(next.answer!);
    }

    expect(new Set(answers).size).toBe(answers.length);
  });

  it('uses every configured framing before reusing any', async () => {
    const h = createHarness();
    const framings: (FramingType | null)[] = [];
    framings.push((await h.ask(QUESTIONS.lessonConcept)).framing);
    for (let i = 0; i < DEFAULT_FRAMING_ORDER.length - 1; i += 1) {
      framings.push((await h.explainDifferently()).framing);
    }
    expect(new Set(framings).size).toBe(DEFAULT_FRAMING_ORDER.length);
  });

  it('falls back to the least recently used framing once all are exhausted', async () => {
    const h = createHarness();
    const seen: (FramingType | null)[] = [];
    seen.push((await h.ask(QUESTIONS.lessonConcept)).framing);
    for (let i = 0; i < DEFAULT_FRAMING_ORDER.length; i += 1) {
      seen.push((await h.explainDifferently()).framing);
    }
    // The 7th turn reuses the framing used longest ago - the very first one.
    expect(seen[DEFAULT_FRAMING_ORDER.length]).toBe(seen[0]);
    expect(seen[DEFAULT_FRAMING_ORDER.length]).toBe(FramingType.DIRECT);
  });

  it('scopes explanation history to the session', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept);
    await h.explainDifferently();

    const sessionOne = await h.history.listAttempts(SESSION_MAIN, 'concept_consent');
    expect(sessionOne).toHaveLength(2);

    // A different session for the SAME user and concept starts clean.
    const otherSession = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_SECOND,
      question: QUESTIONS.lessonConcept,
      intent: TurnIntent.ASK,
    });
    expect(otherSession.framing).toBe(FramingType.DIRECT);
    expect(await h.history.listAttempts(SESSION_SECOND, 'concept_consent')).toHaveLength(1);
    expect(await h.history.listAttempts(SESSION_MAIN, 'concept_consent')).toHaveLength(2);
  });

  it('records every explain-differently request as a difficulty signal', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept);
    await h.explainDifferently();
    await h.explainDifferently();

    const events = await h.activity.list({
      session_id: SESSION_MAIN,
      activity_type: ActivityType.EXPLAIN_DIFFERENTLY,
    });
    expect(events).toHaveLength(2);
    for (const event of events) {
      expect(event.difficulty_signal).toBe(true);
      expect(event.signal_type).toBe(DifficultySignalType.EXPLAIN_DIFFERENTLY);
      expect(event.concept_id).toBe('concept_consent');
      expect(event.lesson_id).toBeTruthy();
      expect(event.session_id).toBe(SESSION_MAIN);
    }
  });

  it('targets the last explained concept when no question text is supplied', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConceptOther); // balancing test
    const again = await h.explainDifferently();
    expect(again.concept_id).toBe('concept_balancing_test');
  });

  it('accepts a valid concept_id hint and ignores an invented one', async () => {
    const h = createHarness();
    await h.ask(QUESTIONS.lessonConcept); // consent

    const targeted = await h.explainDifferently({ concept_id: 'concept_lawful_basis' });
    expect(targeted.concept_id).toBe('concept_lawful_basis');

    const bogus = await h.explainDifferently({ concept_id: 'concept_totally_made_up' });
    expect(bogus.concept_id).not.toBe('concept_totally_made_up');
    expect(bogus.diagnostics.degraded).toContain('unknown_concept_id_ignored');
  });

  it('works for a general (off-lesson) answer too', async () => {
    const h = createHarness();
    const first = await h.ask(QUESTIONS.offLesson);
    const second = await h.explainDifferently({ question: QUESTIONS.offLesson });
    expect(second.answer).not.toBe(first.answer);
    expect(second.framing).not.toBe(first.framing);
  });
});

describe('explanation fingerprinting', () => {
  it('produces a stable fingerprint for the same content', () => {
    const a = fingerprintExplanation('Consent must be freely given and withdrawable.');
    const b = fingerprintExplanation('Consent must be freely given and withdrawable.');
    expect(a.fingerprint).toBe(b.fingerprint);
  });

  it('ignores reordering and punctuation, which would otherwise fake novelty', () => {
    const a = fingerprintExplanation('Consent must be freely given and withdrawable.');
    const b = fingerprintExplanation('Withdrawable, and freely given: consent must be!');
    expect(a.fingerprint).toBe(b.fingerprint);
  });

  it('flags an exact repeat and a near-duplicate rewording', () => {
    const original = fingerprintExplanation(
      'Consent is a freely given, specific, informed and unambiguous indication of wishes.',
    );
    const history = [
      {
        explanation_fingerprint: original.fingerprint,
        fingerprint_tokens: original.tokens,
      },
    ];

    expect(isEffectivelyIdentical(original, history).reason).toBe('EXACT');

    const reworded = fingerprintExplanation(
      'Consent is an unambiguous, informed, specific and freely given indication of the wishes.',
    );
    expect(isEffectivelyIdentical(reworded, history).isDuplicate).toBe(true);
  });

  it('accepts a genuinely different explanation', () => {
    const original = fingerprintExplanation(
      'Consent is a freely given, specific, informed and unambiguous indication of wishes.',
    );
    const different = fingerprintExplanation(
      'Think of it like an invitation to your house: offered freely, withdrawn at will, and the visit ends when it is.',
    );
    expect(
      isEffectivelyIdentical(different, [
        { explanation_fingerprint: original.fingerprint, fingerprint_tokens: original.tokens },
      ]).isDuplicate,
    ).toBe(false);
  });
});

describe('framing selection policy', () => {
  const selector = new FramingSelector();

  it('offers unused framings first, in preference order', () => {
    const plan = selector.plan([
      {
        session_id: 's',
        concept_id: 'c',
        framing_type: FramingType.DIRECT,
        explanation_fingerprint: 'x',
        fingerprint_tokens: [],
        timestamp: '2026-01-01T00:00:00.000Z',
      },
    ]);
    expect(plan.candidates[0]).toBe(FramingType.ANALOGY);
    expect(plan.exhausted).toBe(false);
    expect(plan.used).toEqual([FramingType.DIRECT]);
  });

  it('falls back to least-recently-used order once exhausted', () => {
    const attempts = DEFAULT_FRAMING_ORDER.map((framing, index) => ({
      session_id: 's',
      concept_id: 'c',
      framing_type: framing,
      explanation_fingerprint: `fp_${index}`,
      fingerprint_tokens: [],
      timestamp: `2026-01-01T00:0${index}:00.000Z`,
    }));

    const plan = selector.plan(attempts);
    expect(plan.exhausted).toBe(true);
    expect(plan.candidates[0]).toBe(DEFAULT_FRAMING_ORDER[0]); // oldest
    expect(plan.candidates[plan.candidates.length - 1]).toBe(
      DEFAULT_FRAMING_ORDER[DEFAULT_FRAMING_ORDER.length - 1],
    ); // newest
  });
});
