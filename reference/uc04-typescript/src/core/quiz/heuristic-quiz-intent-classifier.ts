import type {
  QuizClassification,
  QuizClassificationInput,
  QuizIntentClassifier,
} from '../../contracts/quiz-intent-classifier';
import { QuizIntentLabel } from '../../domain/enums';
import { normalizeText } from '../text';

/**
 * Default QuizIntentClassifier: a weighted multi-signal detector.
 *
 * Deliberately NOT a flat keyword list. It scores several independent signal families and
 * plays them against each other:
 *
 *   HARD answer-seeking  - asking for the answer, the correct option, a hint that reveals it,
 *                          or an elimination that leaves only one option standing.
 *   SOFT answer-seeking  - confirmation seeking ("am I right"), explanation suppression
 *                          ("don't explain, just tell me"), assessment context markers.
 *   LEARNING intent      - explain / why / how / principle / difference-between / teach.
 *
 * Learning intent discounts the answer-seeking score, but that discount is CAPPED when a hard
 * signal fired, so "explain which option is correct" is still blocked while "explain the
 * principle this question tests" is not.
 *
 * Three outcomes: QUIZ_ANSWER_REQUEST (block), UNCERTAIN (safe clarification), and
 * CONCEPT_LEARNING_REQUEST (answer normally). Replaceable by an ML/LLM classifier that
 * implements the same interface - UC-04 core does not change.
 */

interface SignalRule {
  name: string;
  weight: number;
  hard: boolean;
  patterns: RegExp[];
}

const ANSWER_SIGNALS: SignalRule[] = [
  {
    name: 'DIRECT_ANSWER_SOLICITATION',
    weight: 0.6,
    hard: true,
    patterns: [
      /\b(what|which)\s+(is|are|was)\s+(the\s+)?(correct\s+|right\s+)?answers?\b/,
      /\bthe\s+answers?\s+(to|for|of)\b/,
      /\b(tell|give|show|send)\s+(me\s+)?(the\s+)?(correct\s+|right\s+)?answers?\b/,
      /\banswers?\s+key\b/,
      /\bwhat\s+should\s+i\s+(put|answer|select|choose|tick)\b/,
      /\bsolve\s+(this\s+)?(question|quiz|test|exam)\b/,
    ],
  },
  {
    name: 'OPTION_SELECTION',
    weight: 0.55,
    hard: true,
    patterns: [
      /\bwhich\s+(option|choice|answer|one|of\s+(these|the\s+following))\s+(is|are)\s+(the\s+)?(correct|right|true|best)\b/,
      /\b(the\s+)?(correct|right)\s+(option|choice|answer)\b/,
      /\bis\s+(it|the\s+answer)\s+[a-e]\b/,
      /\b(option|answer|choice)\s+[a-e]\s+(is|the)\s+(correct|right)\b/,
    ],
  },
  {
    name: 'OPTION_CONFIRMATION',
    weight: 0.55,
    hard: true,
    patterns: [
      /\b(confirm|verify|check)\s+(whether|if|that)?\s*(my\s+answer|option\s+)?[a-e]?\b.*\b(correct|right|wrong)\b/,
      /\bis\s+[a-e]\s+(correct|right|wrong|the\s+answer)\b/,
      /\bi\s+(picked|chose|selected|went\s+with)\s+[a-e]\b/,
      /\bis\s+my\s+answer\s+(correct|right|wrong)\b/,
    ],
  },
  {
    name: 'HINT_TO_ANSWER',
    weight: 0.55,
    hard: true,
    patterns: [
      /\bhint\b.*\b(which|correct|right|answer|option)\b/,
      /\b(point|nudge|steer)\s+me\s+(to|toward|towards)\s+(the\s+)?(correct|right|answer|option)\b/,
      /\bnarrow\s+it\s+down\s+to\b/,
      /\bwithout\s+telling\s+me\b.*\b(which|answer)\b/,
    ],
  },
  {
    name: 'ELIMINATION_REQUEST',
    weight: 0.55,
    hard: true,
    patterns: [
      /\b(rule|cross)\s+out\b/,
      /\beliminate\s+(the\s+)?(wrong|incorrect|bad)\b/,
      /\bwhich\s+(ones?|options?)\s+(can|should)\s+i\s+(rule\s+out|eliminate|discard)\b/,
      /\bwhich\s+(ones?|options?)\s+(are|is)\s+(wrong|incorrect)\b/,
    ],
  },
  {
    name: 'ANSWER_CONFIRMATION_SEEKING',
    weight: 0.35,
    hard: false,
    patterns: [
      /\bam\s+i\s+(right|correct|wrong)\b/,
      /\bjust\s+(confirm|tell|say|answer)\b/,
      /\byes\s+or\s+no\b/,
      /\bright\s+or\s+wrong\b/,
      /\bdid\s+i\s+get\s+(it|this|that)\s+(right|correct|wrong)\b/,
    ],
  },
  {
    name: 'EXPLANATION_SUPPRESSION',
    weight: 0.35,
    hard: false,
    patterns: [
      /\b(don'?t|do\s+not|no\s+need\s+to|dont)\s+explain\b/,
      /\bwithout\s+explain(ing)?\b/,
      /\bno\s+explanation\b/,
      /\bskip\s+the\s+explanation\b/,
      /\bjust\s+the\s+answer\b/,
    ],
  },
  {
    name: 'WEAK_OPTION_REFERENCE',
    weight: 0.3,
    hard: false,
    patterns: [
      /\bwhich\s+(ones?|options?|choices?|of\s+(these|the\s+following))\b/,
      /\bshould\s+i\s+(pick|choose|select)\b/,
    ],
  },
];

const ASSESSMENT_CONTEXT_PATTERNS: RegExp[] = [
  /\bquiz\b/,
  /\bexam\b/,
  /\bassessment\b/,
  /\bmcq\b/,
  /\bmultiple\s+choice\b/,
  /\bquestion\s+\d+\b/,
  /\bq\d+\b/,
  /\bthis\s+question\b/,
  /\boption\s+[a-e]\b/,
  /\bmy\s+answer\b/,
  /\bgraded?\b/,
  /\bmarks?\b/,
];

const LEARNING_PATTERNS: RegExp[] = [
  /\bexplain\b/,
  /\bexplanation\s+of\b/,
  /\bwhy\s+(is|are|does|do|would|should)\b/,
  /\bhow\s+(does|do|is|are|would)\b/,
  /\bhelp\s+me\s+understand\b/,
  /\bi\s+(don'?t|do\s+not)\s+understand\b/,
  /\bwhat\s+does\s+.+\s+mean\b/,
  /\bdifference\s+between\b/,
  /\bwalk\s+me\s+through\b/,
  /\bteach\s+me\b/,
  /\bprinciple\b/,
  /\bconcept\b/,
  /\bin\s+your\s+own\s+words\b/,
  /\bis\s+my\s+(understanding|reasoning|thinking)\b/,
  /\bhow\s+it\s+works\b/,
];

/** Phrases where "explain" is being refused, not requested. Stripped before learning detection. */
const NEGATED_EXPLANATION = /\b(don'?t|do\s+not|dont|no\s+need\s+to|without|skip\s+the)\s+explain(ing|ation)?\b/g;

export interface ClassifierThresholds {
  block: number;
  uncertain: number;
}

export const DEFAULT_CLASSIFIER_THRESHOLDS: ClassifierThresholds = {
  block: 0.55,
  uncertain: 0.28,
};

const ASSESSMENT_CONTEXT_WEIGHT = 0.15;
const LEARNING_DISCOUNT = 0.3;
const HARD_SIGNAL_DISCOUNT_CAP = 0.1;

export class HeuristicQuizIntentClassifier implements QuizIntentClassifier {
  static readonly NAME = 'heuristic-multi-signal-v1';

  constructor(private readonly thresholds: ClassifierThresholds = DEFAULT_CLASSIFIER_THRESHOLDS) {}

  async classify(input: QuizClassificationInput): Promise<QuizClassification> {
    const text = normalizeText(input.question);
    const signals: string[] = [];

    let answerScore = 0;
    let hardSignalFired = false;

    for (const rule of ANSWER_SIGNALS) {
      if (rule.patterns.some((p) => p.test(text))) {
        signals.push(rule.name);
        answerScore += rule.weight;
        if (rule.hard) hardSignalFired = true;
      }
    }

    const assessmentInText = ASSESSMENT_CONTEXT_PATTERNS.some((p) => p.test(text));
    if (assessmentInText) signals.push('ASSESSMENT_CONTEXT');
    if (input.assessmentContext) signals.push('SESSION_ASSESSMENT_CONTEXT');
    if (assessmentInText || input.assessmentContext) answerScore += ASSESSMENT_CONTEXT_WEIGHT;

    // Learning intent is measured on text with refused-explanation phrases removed, so
    // "don't explain it" cannot earn learning credit for containing the word "explain".
    const learningText = text.replace(NEGATED_EXPLANATION, ' ');
    const learningHit = LEARNING_PATTERNS.some((p) => p.test(learningText));
    if (learningHit) signals.push('LEARNING_INTENT');

    const discountCap = hardSignalFired ? HARD_SIGNAL_DISCOUNT_CAP : LEARNING_DISCOUNT;
    const discount = learningHit ? Math.min(LEARNING_DISCOUNT, discountCap) : 0;

    const score = clamp01(Math.min(1, answerScore) - discount);

    let label: QuizIntentLabel;
    let confidence: number;
    if (score >= this.thresholds.block) {
      label = QuizIntentLabel.QUIZ_ANSWER_REQUEST;
      confidence = round(Math.min(1, score));
    } else if (score >= this.thresholds.uncertain) {
      label = QuizIntentLabel.UNCERTAIN;
      confidence = round(0.5 + (this.thresholds.block - score) / 4);
    } else {
      label = QuizIntentLabel.CONCEPT_LEARNING_REQUEST;
      confidence = round(1 - score);
    }

    return {
      label,
      confidence,
      signals,
      classifier: HeuristicQuizIntentClassifier.NAME,
    };
  }
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
