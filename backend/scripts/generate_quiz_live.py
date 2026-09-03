"""Generate real questions for a real course, through the real model, and store them.

    python -m scripts.generate_quiz_live --course LL-37533 --count 5 --dry-run
    python -m scripts.generate_quiz_live --course LL-37533 --count 20

The unit tests prove the parser refuses malformed output. This proves the thing they cannot: that a
real model, given a real course from the catalogue, produces questions that survive both the parser
**and** UC-02's own validator — and that what lands in the question bank is DRAFT, so nothing
reaches a learner unreviewed.

``--dry-run`` calls the model and prints what would be stored without writing anything, which is the
mode to use when judging question quality.

Writes to **our** database (``DATABASE_URL``), never to the company's. The course is read from our
own ``qc_courses``, which the import script populated.
"""

from __future__ import annotations

import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", help="course code, e.g. LL-37533. Omit to list courses.")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.db.session import session_scope
    from app.modules.question_bank.models import Question
    from app.modules.quiz_configuration.models import Course
    from app.modules.quiz_generation.domain.generation import (
        CourseBrief,
        build_prompt,
        parse_questions,
    )
    from app.modules.quiz_generation.integration.llm import build_generator
    from app.modules.quiz_generation.integration.question_bank import QuestionBankSink
    from app.modules.quiz_generation.services.generation_service import (
        QuestionGenerationService,
    )

    settings = get_settings()
    generator = build_generator(settings)
    if not getattr(generator, "configured", False):
        sys.exit(
            "no model configured — set COACHING_LLM_PROVIDER=bedrock, COACHING_LLM_API_KEY "
            "and COACHING_LLM_MODEL"
        )

    with session_scope() as session:
        if not args.course:
            print("courses in our database:\n")
            for course in session.query(Course).order_by(Course.code).limit(40):
                # Flag the ones with a real brief: those are the ones worth generating from.
                mark = "*" if course.description else " "
                level = f"RQF {course.rqf_level}" if course.rqf_level else ""
                print(f" {mark}{course.code:<12} {course.title[:60]:<62}{level}")
            print("\n  * has a description/level — generation will be markedly better")
            return 0

        course = session.query(Course).filter(Course.code == args.course).one_or_none()
        if course is None:
            sys.exit(f"no course with code {args.course}")

        brief = CourseBrief(
            course_id=course.code,
            name=course.title,
            # Populated by `import_platform_courses` from their platform's own course rows. A course
            # imported from the scraped catalogue has none of these, and its questions are visibly
            # more generic as a result — which is the argument for using the platform courses.
            description=course.description,
            rqf_level=course.rqf_level,
            subject_area=course.subject_area,
        )
        print(f"course : {course.code} — {course.title}")
        print(f"asking : {args.count} question(s)\n")

        if args.dry_run:
            prompt = build_prompt(brief, args.count)
            text = generator.complete(prompt, max_tokens=max(1500, args.count * 320))
            report = parse_questions(text, wanted=args.count)
            print(f"accepted {report.count}, rejected {report.rejected}")
            if report.reasons:
                print(f"reasons: {list(report.reasons)}")
            for index, question in enumerate(report.accepted, start=1):
                print(f"\nQ{index}. {question.question_text}")
                for option in question.options:
                    print(f"   {option.label}. {option.text}")
                print(f"   Answer: {question.answer_label}")
                if question.explanation:
                    print(f"   Why: {question.explanation}")
            print("\ndry run — nothing written")
            return 0

        service = QuestionGenerationService(generator, QuestionBankSink(session))
        outcome = service.generate(brief, count=args.count, actor="generate_quiz_live")

        print(f"created  : {outcome.created} DRAFT question(s)")
        print(f"rejected : {outcome.rejected}")
        if outcome.reasons:
            print(f"reasons  : {list(outcome.reasons)}")

        # Read them back, so the report is about rows that exist rather than about a return value.
        stored = (
            session.query(Question)
            .filter(Question.id.in_(outcome.question_ids))
            .all()
            if outcome.question_ids
            else []
        )
        print(f"\nverified in the database: {len(stored)} row(s)")
        statuses = {question.status for question in stored}
        print(f"statuses: {statuses or '—'}")
        if statuses and statuses != {"DRAFT"}:
            print("UNEXPECTED: a generated question is not DRAFT")
            return 1
        for question in stored[:3]:
            correct = [option.label for option in question.options if option.is_correct]
            print(f"\n  {question.reference}  {question.question_text[:90]}")
            print(f"    options: {len(question.options)}   correct: {correct}")

        print("\nAll DRAFT — no generated question can reach a learner until it is activated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
