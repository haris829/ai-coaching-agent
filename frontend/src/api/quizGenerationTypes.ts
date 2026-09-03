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
 * The verdict a learner receives.
 *
 * **No per-answer detail at all** — not which answers were right, and not what the right ones
 * were. Two reasons: per-question corrections would let anyone read the whole answer key by
 * submitting guesses twice, and the company's contract is `Response {Pass / Fail}` — the learner is
 * not told their answers.
 *
 * `correct` is a count, so the percentage means something without saying which questions it refers
 * to. The full detail is recorded in the database and read back by an administrator through
 * `submissions`.
 */
export type QuizResult = {
  submissionId: string;
  quizId: string;
  total: number;
  correct: number;
  percentage: number;
  passMark: number;
  passed: boolean;
  outcome: 'PASS' | 'FAIL';
};

/**
 * One answer of a stored sitting, as an administrator reads it back.
 *
 * The only shape that pairs a learner's answer with the correct one, and it comes only from the
 * administrator-only submissions route.
 */
export type MarkedAnswer = {
  sequence: number;
  questionId: string;
  given: string | null;
  correct: string;
  isCorrect: boolean;
};

export type StoredSubmission = QuizResult & {
  answers: MarkedAnswer[];
};

export type SubmissionList = {
  quizId: string;
  submissions: StoredSubmission[];
};

/**
 * A course to choose from when generating.
 *
 * `hasBrief` is the field worth surfacing. A course with a description and a level generates
 * questions pitched where the learner is actually assessed; one with only a title produces
 * noticeably more generic ones. Showing that distinction is more useful than hiding it.
 */
export type CourseSummary = {
  code: string;
  title: string;
  rqfLevel: number | null;
  subjectArea: string | null;
  hasBrief: boolean;
  generatedCount: number;
};

export type CourseList = {
  courses: CourseSummary[];
};
