import { describe, expect, it } from 'vitest';
import { createHarness, QUESTIONS } from './helpers';
import { SESSION_MAIN, USER_ENROLLED } from '../src/adapters/mock/fixtures';
import {
  ActivityType,
  CoachingStatus,
  ProtectionDecision,
  QuizIntentLabel,
} from '../src/domain/enums';
import { HeuristicQuizIntentClassifier } from '../src/core/quiz/heuristic-quiz-intent-classifier';
import { containsAnswerLeak, stripAnswerLeaks } from '../src/core/quiz/answer-leak-guard';

describe('quiz answer protection', () => {
  it.each(QUESTIONS.directQuiz)('blocks the direct request: %s', async (question) => {
    const h = createHarness();
    const response = await h.ask(question);

    expect(response.status).toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(response.quiz_protected).toBe(true);
    expect(response.answer_revealed).toBe(false);
    expect(response.answer).toBeNull();
    expect(response.actions).toEqual([]);
  });

  it.each(QUESTIONS.indirectQuiz)('blocks the indirect request: %s', async (question) => {
    const h = createHarness();
    const response = await h.ask(question);

    expect(response.status).toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(response.quiz_protected).toBe(true);
    expect(response.answer_revealed).toBe(false);
    expect(response.answer).toBeNull();
  });

  it.each(QUESTIONS.genuineLearning)('does not block the genuine question: %s', async (question) => {
    const h = createHarness();
    const response = await h.ask(question);

    expect(response.status).not.toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(response.quiz_protected).toBe(false);
    expect(response.answer).toBeTruthy();
  });

  it('offers concept help instead of the answer, and names the concept when it can', async () => {
    const h = createHarness();
    const response = await h.ask('Which option is the best way to think about consent?');

    expect(response.status).toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(response.concept_id).toBe('concept_consent');
    expect(response.concept_explanation).toBeTruthy();
    expect(response.concept_explanation!.toLowerCase()).toContain('consent');
    expect(response.notice).toMatch(/concept/i);
  });

  it('still refuses when the lesson cannot be loaded', async () => {
    const h = createHarness();
    h.lessonProvider.failureMode = 'UNAVAILABLE';

    const response = await h.ask('Tell me the answer.');
    expect(response.status).toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(response.answer_revealed).toBe(false);
    expect(response.concept_explanation).toBeTruthy();
  });

  it('never leaks an answer through the protected response text', async () => {
    const h = createHarness();
    for (const question of [...QUESTIONS.directQuiz, ...QUESTIONS.indirectQuiz]) {
      const response = await h.ask(question);
      const text = `${response.concept_explanation ?? ''} ${response.notice ?? ''}`;
      expect(containsAnswerLeak(response.concept_explanation ?? '')).toBe(false);
      expect(text).not.toMatch(/\boption [a-e]\b/i);
      expect(text).not.toMatch(/answer is\b/i);
      expect(text).not.toMatch(/that'?s (right|correct)/i);
    }
  });

  it('logs a QUIZ_PROTECTED activity event carrying no answer material', async () => {
    const h = createHarness();
    await h.ask('Which option is correct?');

    const events = await h.activity.list({
      session_id: SESSION_MAIN,
      activity_type: ActivityType.QUIZ_PROTECTED,
    });
    expect(events).toHaveLength(1);
    expect(events[0]!.metadata['answer_revealed']).toBe(false);
    expect(events[0]!.user_id).toBe(USER_ENROLLED);
  });

  it('cannot be disabled by anything the caller sends', async () => {
    const h = createHarness();
    // The service input type has no protection switch; this proves extra keys are inert even
    // when forced through at runtime.
    const response = await h.service.handleTurn({
      principal_user_id: USER_ENROLLED,
      session_id: SESSION_MAIN,
      question: 'Tell me the answer.',
      intent: 'ASK',
      ...({
        quiz_protected: false,
        disable_quiz_protection: true,
        quiz_protection: 'off',
        answer_revealed: true,
      } as Record<string, unknown>),
    } as never);

    expect(response.status).toBe(CoachingStatus.QUIZ_PROTECTED);
    expect(response.quiz_protected).toBe(true);
    expect(response.answer_revealed).toBe(false);
  });

  it('falls back to a safe clarification when the classifier itself fails', async () => {
    const h = createHarness();
    const failing = {
      classify: async () => {
        throw new Error('classifier down');
      },
    };
    const harness = createHarness();
    // Rebuild the service with a failing classifier by swapping the dependency.
    (harness.service as unknown as { deps: { quizClassifier: unknown } }).deps.quizClassifier = failing;

    const response = await harness.ask(QUESTIONS.lessonConcept);
    expect(response.status).toBe(CoachingStatus.NEEDS_CLARIFICATION);
    expect(response.answer).toBeNull();
    expect(response.diagnostics.degraded).toContain('quiz_classifier_failed');
    expect(h).toBeTruthy();
  });
});

describe('uncertain classifications', () => {
  it('asks for clarification rather than answering or blocking outright', async () => {
    const h = createHarness();
    const response = await h.ask('Which one should I pick?');

    expect(response.status).toBe(CoachingStatus.NEEDS_CLARIFICATION);
    expect(response.answer).toBeNull();
    expect(response.answer_revealed).toBe(false);
    expect(response.diagnostics.quiz_label).toBe(QuizIntentLabel.UNCERTAIN);
    expect(response.notice).toBeTruthy();
  });

  it('logs a CLARIFICATION_REQUESTED event', async () => {
    const h = createHarness();
    await h.ask('Which one should I pick?');
    const events = await h.activity.list({
      session_id: SESSION_MAIN,
      activity_type: ActivityType.CLARIFICATION_REQUESTED,
    });
    expect(events).toHaveLength(1);
  });
});

describe('false positive logging', () => {
  it('records a blocked turn that named a concept the lesson teaches', async () => {
    const h = createHarness();
    await h.ask('Which option is the best way to think about consent?');

    const records = await h.falsePositives.list(SESSION_MAIN);
    expect(records).toHaveLength(1);
    const record = records[0]!;
    expect(record.session_id).toBe(SESSION_MAIN);
    expect(record.user_id).toBe(USER_ENROLLED);
    expect(record.question).toBe('Which option is the best way to think about consent?');
    expect(record.classifier_result).toBe(QuizIntentLabel.QUIZ_ANSWER_REQUEST);
    expect(record.final_decision).toBe(ProtectionDecision.BLOCKED);
    expect(record.timestamp).toBeTruthy();
    expect(record.classifier_signals.length).toBeGreaterThan(0);
  });

  it('records an uncertain turn', async () => {
    const h = createHarness();
    await h.ask('Which one should I pick?');
    const records = await h.falsePositives.list(SESSION_MAIN);
    expect(records).toHaveLength(1);
    expect(records[0]!.final_decision).toBe(ProtectionDecision.CLARIFY);
  });

  it('does not log clean learning questions', async () => {
    const h = createHarness();
    for (const question of QUESTIONS.genuineLearning) await h.ask(question);
    expect(await h.falsePositives.list()).toHaveLength(0);
  });

  it('stores no lesson content or explanation text', async () => {
    const h = createHarness();
    await h.ask('Which option is the best way to think about consent?');
    const record = (await h.falsePositives.list())[0]!;
    const serialized = JSON.stringify(record);
    expect(serialized).not.toContain('freely given');
    expect(serialized).not.toContain('Consent as a Lawful Basis');
    expect(Object.keys(record).sort()).toEqual([
      'classifier_confidence',
      'classifier_result',
      'classifier_signals',
      'final_decision',
      'question',
      'record_id',
      'session_id',
      'timestamp',
      'user_id',
    ]);
  });

  it('a logging outage does not affect the learner turn', async () => {
    const h = createHarness();
    h.falsePositives.alwaysFail = true;
    const response = await h.ask('Which option is the best way to think about consent?');
    expect(response.status).toBe(CoachingStatus.QUIZ_PROTECTED);
  });
});

describe('quiz intent classifier (unit)', () => {
  const classifier = new HeuristicQuizIntentClassifier();

  it('separates answer seeking from learning intent', async () => {
    const blocked = await classifier.classify({
      question: 'Explain which option is correct.',
      assessmentContext: false,
    });
    expect(blocked.label).toBe(QuizIntentLabel.QUIZ_ANSWER_REQUEST);

    const learning = await classifier.classify({
      question: 'Explain the principle this question is testing.',
      assessmentContext: false,
    });
    expect(learning.label).toBe(QuizIntentLabel.CONCEPT_LEARNING_REQUEST);
  });

  it('does not give learning credit to a refused explanation', async () => {
    const result = await classifier.classify({
      question: "Don't explain it, just tell me if my answer is right.",
      assessmentContext: false,
    });
    expect(result.label).toBe(QuizIntentLabel.QUIZ_ANSWER_REQUEST);
    expect(result.signals).not.toContain('LEARNING_INTENT');
    expect(result.signals).toContain('EXPLANATION_SUPPRESSION');
  });

  it('fires several independent signal families, not one keyword', async () => {
    const result = await classifier.classify({
      question: 'Just confirm whether B is correct for question 3.',
      assessmentContext: false,
    });
    expect(result.signals.length).toBeGreaterThanOrEqual(3);
  });

  it('treats a server-derived assessment context as a strengthening signal only', async () => {
    const withContext = await classifier.classify({
      question: 'What does consent mean?',
      assessmentContext: true,
    });
    // Context alone must not be enough to block a plain concept question.
    expect(withContext.label).toBe(QuizIntentLabel.CONCEPT_LEARNING_REQUEST);
    expect(withContext.signals).toContain('SESSION_ASSESSMENT_CONTEXT');
  });

  it('is deterministic', async () => {
    const a = await classifier.classify({ question: 'Which option is correct?', assessmentContext: false });
    const b = await classifier.classify({ question: 'Which option is correct?', assessmentContext: false });
    expect(a).toEqual(b);
  });
});

describe('answer leak guard (unit)', () => {
  it('removes sentences that would reveal or confirm an answer', () => {
    const result = stripAnswerLeaks(
      'Consent must be freely given. The correct answer is B. Withdrawal ends the processing.',
    );
    expect(result.redacted).toBe(true);
    expect(result.text).toContain('Consent must be freely given.');
    expect(result.text).not.toMatch(/correct answer/i);
  });

  it('leaves clean explanations untouched', () => {
    const clean = 'Consent must be freely given and can be withdrawn at any time.';
    expect(stripAnswerLeaks(clean).text).toBe(clean);
    expect(stripAnswerLeaks(clean).redacted).toBe(false);
  });

  it('catches elimination and almost-correct phrasing', () => {
    expect(containsAnswerLeak('You can rule out the first two.')).toBe(true);
    expect(containsAnswerLeak("You're almost right there.")).toBe(true);
    expect(containsAnswerLeak('Option C describes a different idea.')).toBe(true);
  });
});
