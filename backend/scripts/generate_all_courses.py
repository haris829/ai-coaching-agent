"""Generate a quiz for every course in the catalogue, into our own database.

    python -m scripts.generate_all_courses --dry-run
    python -m scripts.generate_all_courses --questions 20
    python -m scripts.generate_all_courses --questions 20 --course LL-45165
    python -m scripts.generate_all_courses --questions 20 --concurrency 2 --pace 1.5

WHY A SCRIPT AND NOT AN ENDPOINT
--------------------------------
Generating for the whole catalogue is a long, expensive, resumable job: 33 courses at 20 questions
is 660 questions and something like a quarter of an hour of model time. An HTTP request is the
wrong shape for that — it would hold a connection open for minutes, time out behind a proxy, and
leave a half-finished run with no way to continue it. A script can be paced, interrupted, and
restarted.

PACED, BECAUSE THE PROVIDER HAS A LIMIT
---------------------------------------
Measured against a real Bedrock account: 33 requests at five concurrent lost **20 of them** to HTTP
429, and all 20 succeeded when retried one at a time. So this runs **sequentially by default**,
with a pause between courses.

That is not timidity. A run that goes at five concurrent and loses two thirds of the catalogue has
not saved any time — the failures have to be repeated, and the report is a wall of throttling
noise that hides any real problem. ``--concurrency`` is there for an account with a bigger quota;
one is the setting that finishes.

Within a course the batches still run concurrently, so a 20-question course is two simultaneous
calls rather than twenty sequential questions. That is where the speed comes from.

RESUMABLE, BECAUSE LONG JOBS DIE
--------------------------------
By default a course that already has a quiz with at least as many questions is skipped, so an
interrupted run continues where it stopped instead of starting again. ``--regenerate`` overrides
that and asks for a fresh quiz regardless — and because generation is told what a course has
already been asked, a second quiz for the same course gets *different* questions rather than the
same ones back.

WHAT IT WRITES
--------------
Rows in **our** database only. Every question goes through UC-02's validator and lands as
``DRAFT``, so nothing generated here can reach a learner until an administrator activates it.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

#: Seconds to wait between courses. Enough to stay under a modest per-account limit without
#: turning a 33-course run into an afternoon.
DEFAULT_PACE_SECONDS = 1.0


@dataclass
class Outcome:
    code: str
    title: str
    created: int = 0
    rejected: int = 0
    quiz_id: str | None = None
    error: str | None = None
    skipped: bool = False


def main() -> int:  # noqa: PLR0915 - a script that reports what it did, step by step
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=int, default=20, help="questions per course (default 20)"
    )
    parser.add_argument(
        "--course", action="append", default=None, help="only this course code; repeatable"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="courses at once. 1 by default — see the module docstring on rate limits.",
    )
    parser.add_argument("--pace", type=float, default=DEFAULT_PACE_SECONDS)
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="generate even for courses that already have a quiz of this size",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy import func, select

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.modules.quiz_configuration.models import Course
    from app.modules.quiz_generation.integration.catalogue import CatalogueLookup
    from app.modules.quiz_generation.integration.llm import build_generator
    from app.modules.quiz_generation.integration.question_bank import (
        GeneratedHistory,
        QuestionBankSink,
        QuestionBankView,
    )
    from app.modules.quiz_generation.models import GeneratedQuiz
    from app.modules.quiz_generation.services.quiz_service import GeneratedQuizService

    settings = get_settings()
    generator = build_generator(settings)
    if not getattr(generator, "configured", False):
        sys.exit(
            "no model configured — set COACHING_LLM_PROVIDER, COACHING_LLM_API_KEY "
            "and COACHING_LLM_MODEL"
        )

    wanted = max(1, args.questions)

    # --- what to do -------------------------------------------------------
    with SessionLocal() as session:
        statement = select(Course).order_by(Course.title)
        if args.course:
            statement = statement.where(Course.code.in_(args.course))
        courses = [(c.code, c.title) for c in session.scalars(statement).all()]

        existing = dict(
            session.execute(
                select(GeneratedQuiz.course_ref, func.max(GeneratedQuiz.question_count))
                .where(GeneratedQuiz.course_ref.is_not(None))
                .group_by(GeneratedQuiz.course_ref)
            ).all()
        )

    if not courses:
        sys.exit("no matching course in the catalogue")

    todo: list[tuple[str, str]] = []
    skipped: list[Outcome] = []
    for code, title in courses:
        already = int(existing.get(code) or 0)
        if not args.regenerate and already >= wanted:
            skipped.append(
                Outcome(code=code, title=title, created=already, skipped=True)
            )
            continue
        todo.append((code, title))

    print(f"catalogue      : {len(courses)} course(s)")
    print(f"already done   : {len(skipped)} (use --regenerate to redo them)")
    print(f"to generate    : {len(todo)} course(s) x {wanted} question(s)")
    print(f"concurrency    : {args.concurrency}    pace: {args.pace}s between courses")
    estimate = (len(todo) / max(1, args.concurrency)) * (wanted * 1.2 + args.pace)
    print(f"rough estimate : {estimate / 60:.0f} minute(s)\n")

    if args.dry_run:
        for code, title in todo:
            print(f"  would generate  {code:<12} {title[:62]}")
        print("\ndry run — nothing written")
        return 0

    # --- do it ------------------------------------------------------------
    def one(entry: tuple[str, str]) -> Outcome:
        code, title = entry
        # A session per course. Sessions are not safe to share between threads, and a failure on
        # one course must not roll back another's questions.
        with SessionLocal() as session:
            service = GeneratedQuizService(
                session,
                generator=generator,
                sink=QuestionBankSink(session),
                view=QuestionBankView(session),
                courses=CatalogueLookup(session),
                history=GeneratedHistory(session),
            )
            try:
                view = service.create(topic=title, count=wanted, course_ref=code)
            except Exception as error:  # noqa: BLE001 - one course must not sink the run
                detail = getattr(error, "code", None) or type(error).__name__
                return Outcome(code=code, title=title, error=str(detail))
            return Outcome(
                code=code,
                title=title,
                created=len(view.questions),
                rejected=view.rejected,
                quiz_id=view.quiz_id,
            )

    results: list[Outcome] = list(skipped)
    started = time.monotonic()

    if args.concurrency <= 1:
        for index, entry in enumerate(todo, start=1):
            outcome = one(entry)
            results.append(outcome)
            _report(index, len(todo), outcome)
            if index < len(todo) and args.pace > 0:
                time.sleep(args.pace)
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            for index, outcome in enumerate(pool.map(one, todo), start=1):
                results.append(outcome)
                _report(index, len(todo), outcome)

    # --- report -----------------------------------------------------------
    generated = [r for r in results if r.quiz_id]
    failed = [r for r in results if r.error]
    questions = sum(r.created for r in generated)

    print()
    print("=" * 72)
    print(f"generated  : {len(generated)} course(s), {questions} question(s)")
    print(f"skipped    : {len([r for r in results if r.skipped])}")
    print(f"failed     : {len(failed)}")
    print(f"elapsed    : {(time.monotonic() - started) / 60:.1f} minute(s)")
    if failed:
        print("\nfailures — rerun the script to retry only these:")
        for outcome in failed:
            print(f"  {outcome.code:<12} {outcome.title[:48]:<50} {outcome.error}")
        # A throttled run is worth naming as such: it is not a defect and the fix is to slow down.
        if any("UNAVAILABLE" in (o.error or "") for o in failed):
            print(
                "\n  QUESTION_GENERATION_UNAVAILABLE means the provider was unreachable, usually "
                "a rate limit.\n  Retry with --concurrency 1 --pace 3, or raise the account quota."
            )
    print("=" * 72)
    print("\nEvery question is DRAFT. Activate them in the Questions screen before any learner "
          "sits them.")
    return 1 if failed else 0


def _report(index: int, total: int, outcome: Outcome) -> None:
    if outcome.error:
        print(f"  [{index}/{total}] XX {outcome.code:<12} {outcome.error}")
        return
    print(
        f"  [{index}/{total}] OK {outcome.code:<12} {outcome.created} stored, "
        f"{outcome.rejected} rejected   {outcome.quiz_id}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
