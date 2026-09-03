/**
 * Wire types for the generate-and-mark contract.
 *
 * Two shapes for a question, and the difference is the point: `QuizQuestion` is what a learner
 * receives and carries **no answer**; `KeyedQuestion` carries the key and comes back only from the
 * administrator-only routes. Keeping them as separate types means a component that renders a quiz
 * for sitting cannot accidentally be handed keys — the compiler stops it.
 */

export type QuizOption = {
  label: string;
  text: string;
};

/** A question as it is sat. No `answer` field exists on this type at all. */
export type QuizQuestion = {
  sequence: number;
  questionId: string;
  question: string;
  options: QuizOption[];
};

/** A question with its key. Returned only to an administrator. */
export type KeyedQuestion = QuizQuestion & {
  answer: string;
  explanation: string | null;
};

export type GenerateQuizInput = {
  topic: string;
  count: number;
  courseRef?: string | null;
  passMark?: number;
};

export type GeneratedQuiz = {
  quizId: string;
  topic: string;
  courseRef: string | null;
  passMark: number;
  requestedCount: number;
  questionCount: number;
  questions: KeyedQuestion[];
  /** Questions the model produced that validation refused. Shown, not hidden. */
  rejected: number;
  reasons: string[];
};

export type SittableQuiz = {
  quizId: string;
  topic: string;
  courseRef: string | null;
  passMark: number;
  questionCount: number;
  questions: QuizQuestion[];
};

/**
 * How one answer was marked.
 *
 * There is deliberately no `correct` field. The backend never sends the right answer back from the
 * marking route — otherwise submitting guesses and reading the corrections would be a way to read
 * the whole answer key.
 */
export type MarkedAnswer = {
  sequence: number;
  questionId: string;
  given: string | null;
  isCorrect: boolean;
};

export type QuizResult = {
  quizId: string;
  total: number;
  correct: number;
  percentage: number;
  passMark: number;
  passed: boolean;
  outcome: 'PASS' | 'FAIL';
  answers: MarkedAnswer[];
};
