/**
 * Generate a quiz from a course, sit it, and see pass or fail.
 *
 * A deliberately plain screen whose only job is to make the new backend contract clickable end to
 * end: ask for questions about a course, watch them come back, answer them, get a verdict. The
 * rules live in the backend — the pass mark, the marking, what may see an answer key — and nothing
 * here re-implements any of them.
 *
 * TWO THINGS THIS SCREEN DEMONSTRATES ON PURPOSE
 * ----------------------------------------------
 * **Generating needs an administrator, sitting does not.** Switch the identity to a learner and
 * "Generate" returns 403 from the server. That refusal comes from the endpoint, not from hiding a
 * button.
 *
 * **The quiz is re-fetched before it is sat.** The generate response contains the answer key, and
 * this screen throws it away and asks for the quiz again through the learner route. That is not
 * ceremony: it means the questions being answered are the ones a learner would really be sent, so
 * an answer key leaking into that payload would show up here as a visible field rather than as a
 * silent one. The key is shown only under "Reveal the answer key", which calls the
 * administrator-only route for it.
 *
 * WHAT THE RESULT DOES NOT SHOW
 * -----------------------------
 * Which answers were right. The marking response carries the verdict and the score and nothing
 * else, so there is nothing here to render per question — the learner is not told their answers.
 * Every answer *is* recorded and marked in the database; "Stored submissions" reads it back through
 * the administrator-only route, which is the point: stored, not returned.
 */

import { useEffect, useState, type ReactNode } from 'react';

import { ApiError, generatedQuizzes } from '../api/client';
import type {
  CourseSummary,
  GeneratedQuiz,
  QuizResult,
  SittableQuiz,
  StoredSubmission,
} from '../api/quizGenerationTypes';
import { ErrorSummary, Spinner, useToast } from '../components/ui';

export function GeneratedQuizPage(): ReactNode {
  const toast = useToast();

  const [topic, setTopic] = useState('Anti-money laundering for fee earners');
  const [courseRef, setCourseRef] = useState('');
  const [courses, setCourses] = useState<CourseSummary[]>([]);
  const [count, setCount] = useState(5);
  const [passMark, setPassMark] = useState(50);

  const [generating, setGenerating] = useState(false);
  const [meta, setMeta] = useState<Pick<
    GeneratedQuiz,
    'quizId' | 'requestedCount' | 'questionCount' | 'rejected' | 'reasons'
  > | null>(null);
  const [quiz, setQuiz] = useState<SittableQuiz | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [result, setResult] = useState<QuizResult | null>(null);
  const [keys, setKeys] = useState<GeneratedQuiz | null>(null);
  const [stored, setStored] = useState<StoredSubmission[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  // The courses already in the catalogue, loaded by name and title so a course is chosen from a
  // list rather than by knowing its code. Silently empty for a non-administrator, whose 403 here
  // is expected and not worth an error banner — the Generate button will refuse them anyway.
  useEffect(() => {
    let cancelled = false;
    generatedQuizzes
      .courses()
      .then((list) => {
        if (!cancelled) setCourses(list.courses);
      })
      .catch(() => {
        if (!cancelled) setCourses([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Choosing a course fills the topic in with its title, which is what generation is aimed at. */
  function chooseCourse(code: string): void {
    setCourseRef(code);
    const course = courses.find((candidate) => candidate.code === code);
    if (course) setTopic(course.title);
  }

  async function generate(): Promise<void> {
    setGenerating(true);
    setError(null);
    setQuiz(null);
    setResult(null);
    setKeys(null);
    setStored(null);
    setAnswers({});
    setMeta(null);
    try {
      const generated = await generatedQuizzes.generate({
        topic,
        count,
        courseRef: courseRef.trim() || null,
        passMark,
      });
      setMeta({
        quizId: generated.quizId,
        requestedCount: generated.requestedCount,
        questionCount: generated.questionCount,
        rejected: generated.rejected,
        reasons: generated.reasons,
      });
      // Deliberately discarding `generated.questions` — see the note at the top of this file.
      setQuiz(await generatedQuizzes.read(generated.quizId));
      toast.success(`Generated ${generated.questionCount} question(s).`);
    } catch (cause) {
      setError(cause);
    } finally {
      setGenerating(false);
    }
  }

  async function submit(): Promise<void> {
    if (!quiz) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await generatedQuizzes.submit(quiz.quizId, answers));
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function loadStored(): Promise<void> {
    if (!quiz) return;
    setBusy(true);
    setError(null);
    try {
      const list = await generatedQuizzes.submissions(quiz.quizId);
      setStored(list.submissions);
    } catch (cause) {
      // A learner identity gets 403 here, which is the point rather than a bug.
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function revealKeys(): Promise<void> {
    if (!quiz) return;
    setBusy(true);
    setError(null);
    try {
      setKeys(await generatedQuizzes.answers(quiz.quizId));
    } catch (cause) {
      // A learner identity gets a 403 here, which is the point rather than a bug.
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  const answered = Object.keys(answers).length;

  return (
    <main className="page">
      <h1>Quiz generator</h1>
      <p className="muted">
        Generate multiple-choice questions from a course, sit them, and get a pass or fail.
        Generated questions are stored as <strong>DRAFT</strong> in the question bank, so nothing a
        model wrote can reach a real attempt until an administrator activates it.
      </p>
      <p className="muted">
        Generating the same course twice does not produce the same paper. Each run is told what has
        already been asked and instructed to test different points, and anything that comes back
        repeated is refused and counted in <code>rejected</code>.
      </p>

      <section className="card">
        <h2>1. Generate</h2>
        <div className="grid">
          <label>
            Topic
            <input
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder="e.g. Anti-money laundering"
            />
          </label>
          <label>
            Course <span className="muted">(optional)</span>
            <select value={courseRef} onChange={(event) => chooseCourse(event.target.value)}>
              <option value="">— generate from the topic alone —</option>
              {courses.map((course) => (
                <option key={course.code} value={course.code}>
                  {course.title}
                  {course.rqfLevel ? ` · RQF ${course.rqfLevel}` : ''}
                  {course.hasBrief ? '' : ' · title only'}
                  {course.generatedCount ? ` · ${course.generatedCount} generated` : ''}
                </option>
              ))}
            </select>
            <small className="muted">
              {courses.length === 0
                ? 'No courses loaded — generation will use the topic alone.'
                : 'A course marked “title only” has no description in the catalogue, so its ' +
                  'questions come from its name alone and are noticeably more generic. ' +
                  '“N generated” is how many quizzes it has already had.'}
            </small>
          </label>
          <label>
            How many
            <input
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(event) => setCount(Number(event.target.value))}
            />
          </label>
          <label>
            Pass mark (%)
            <input
              type="number"
              min={0}
              max={100}
              value={passMark}
              onChange={(event) => setPassMark(Number(event.target.value))}
            />
            <small className="muted">Frozen onto the quiz. 50% passes.</small>
          </label>
        </div>
        <button type="button" onClick={generate} disabled={generating || !topic.trim()}>
          {generating ? 'Generating…' : 'Generate'}
        </button>
        {generating && <Spinner label="Asking the model — this can take up to a minute" />}
      </section>

      {error ? <ErrorSummary error={error} /> : null}
      {error instanceof ApiError && error.status === 403 && (
        <p className="muted">
          That refusal came from the server: generating a quiz and reading an answer key both
          require an administrator token. Switch the identity above and try again.
        </p>
      )}

      {meta && (
        <section className="card">
          <h2>What came back</h2>
          <dl className="kv">
            <dt>Quiz id</dt>
            <dd>
              <code>{meta.quizId}</code>
            </dd>
            <dt>Asked for</dt>
            <dd>{meta.requestedCount}</dd>
            <dt>Stored</dt>
            <dd>{meta.questionCount}</dd>
            <dt>Refused by validation</dt>
            <dd>{meta.rejected}</dd>
          </dl>
          {meta.reasons.length > 0 && (
            <>
              <p className="muted">
                Why some were refused. Nothing malformed is repaired — a repaired question is how a
                plausible wrong answer reaches a certificate.
              </p>
              <ul>
                {meta.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {quiz && (
        <section className="card">
          <h2>2. Sit it</h2>
          <p className="muted">
            {quiz.questionCount} question(s) · pass mark {quiz.passMark}% · answered {answered} of{' '}
            {quiz.questionCount}. This payload came from the learner route and carries no answers.
          </p>

          {quiz.questions.map((question) => (
            <article key={question.questionId} className="question">
              <h3>
                Q{question.sequence}. {question.question}
              </h3>
              {question.options.map((option) => (
                <label key={option.label} className="option">
                  <input
                    type="radio"
                    name={`q${question.sequence}`}
                    value={option.label}
                    checked={answers[String(question.sequence)] === option.label}
                    disabled={Boolean(result)}
                    onChange={() =>
                      setAnswers((current) => ({
                        ...current,
                        [String(question.sequence)]: option.label,
                      }))
                    }
                  />
                  <strong>{option.label}.</strong> {option.text}
                </label>
              ))}
            </article>
          ))}

          {!result && (
            <button type="button" onClick={submit} disabled={busy}>
              {busy ? 'Marking…' : 'Submit answers'}
            </button>
          )}
          {!result && answered < quiz.questionCount && (
            <p className="muted">
              A question left unanswered is marked wrong, not skipped.
            </p>
          )}
        </section>
      )}

      {result && (
        <section className="card">
          <h2>3. Result</h2>
          <p className={result.passed ? 'pass' : 'fail'}>
            <strong>{result.outcome}</strong> — {result.correct} of {result.total} correct (
            {result.percentage}%), pass mark {result.passMark}%
          </p>
          <p className="muted">
            The marking response carries this verdict and nothing else — not which answers were
            right, and not what the right ones were. Marking happened in the database against the
            stored key, and every answer was recorded there. Stored submission{' '}
            <code>{result.submissionId}</code>.
          </p>

          {!keys && (
            <button type="button" onClick={revealKeys} disabled={busy}>
              Reveal the answer key (administrator only)
            </button>
          )}
          {keys && (
            <ol>
              {keys.questions.map((question) => (
                <li key={question.questionId}>
                  <strong>{question.answer}</strong> — {question.question}
                  {question.explanation && <div className="muted">{question.explanation}</div>}
                </li>
              ))}
            </ol>
          )}

          {!stored && (
            <button type="button" onClick={loadStored} disabled={busy}>
              Stored submissions (administrator only)
            </button>
          )}
          {stored && (
            <>
              <p className="muted">
                Read from the database, newest first. This is the detail the learner was not given.
              </p>
              {stored.map((submission) => (
                <div key={submission.submissionId}>
                  <p className={submission.passed ? 'pass' : 'fail'}>
                    <strong>{submission.outcome}</strong> — {submission.correct}/
                    {submission.total} ({submission.percentage}%)
                  </p>
                  <table>
                    <thead>
                      <tr>
                        <th>Q</th>
                        <th>Answered</th>
                        <th>Correct</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {submission.answers.map((answer) => (
                        <tr key={answer.questionId}>
                          <td>{answer.sequence}</td>
                          <td>{answer.given ?? <span className="muted">not answered</span>}</td>
                          <td>{answer.correct}</td>
                          <td className={answer.isCorrect ? 'pass' : 'fail'}>
                            {answer.isCorrect ? 'right' : 'wrong'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </>
          )}
        </section>
      )}

      {!quiz && !generating && (
        <p className="muted">
          No AI provider configured? Generating returns <strong>503</strong> and writes nothing.
          Set <code>COACHING_LLM_PROVIDER</code>, <code>COACHING_LLM_API_KEY</code> and{' '}
          <code>COACHING_LLM_MODEL</code>, and restart.
        </p>
      )}
    </main>
  );
}
