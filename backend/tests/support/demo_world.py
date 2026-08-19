"""A complete, coherent demo dataset for the UC-03 end-to-end walkthrough.

This was UC-03's ``app/seed.py`` when it ran as a standalone service: a full quiz, configuration,
enrolment and question bank covering all five structures, written into the provisional ``ext_*``
tables so the API could be driven by hand.

It has moved here, and now writes into the port fakes, for two reasons:

* the merged system's demo seeder is :mod:`scripts.seed`, which populates the **real** UC-01 and
  UC-02 — a second seeder writing a parallel dataset into app code would be exactly the kind of
  duplication this integration set out to remove;
* the dataset is still genuinely useful as a *test* fixture. ``test_end_to_end.py`` walks the whole
  lifecycle against a dataset nobody tuned for the assertion at hand, which catches the
  configuration-coherence mistakes that hand-built per-test fixtures hide.
"""

from __future__ import annotations

from app.core.question_types import QuestionPresentation, QuestionType
from app.modules.attempt_delivery.container import RequestContext
from app.modules.attempt_delivery.integration.uc02.types import (
    BankQuestion,
    QuestionOption,
    QuestionOrderItem,
    ScenarioSubQuestion,
)
from app.modules.identity.enums import EnrolmentStatus
from tests.support.fixtures import COURSE_ID, LEARNER_ID, OTHER_LEARNER_ID, QUIZ_ID

#: The learners the demo dataset enrols. Shared with the rest of the suite so the seeded world and
#: the authenticated API clients agree on who is who.
LEARNERS = (LEARNER_ID, OTHER_LEARNER_ID)
ACTIVATED_AT = "2026-01-01T00:00:00Z"


def _questions() -> list[BankQuestion]:
    """A small bank covering every supported question structure."""
    questions: list[BankQuestion] = []

    for index in range(1, 7):
        qid = f"q-sc-{index:02d}"
        questions.append(
            BankQuestion(
                question_id=qid,
                version=1,
                type=QuestionType.SINGLE_CHOICE,
                prompt=f"({index}) What is the first action on hearing the fire alarm?",
                quiz_id=QUIZ_ID,
                course_id=COURSE_ID,
                topic_id="topic-evacuation",
                options=(
                    QuestionOption(f"{qid}-o1", "Leave by the nearest fire exit", is_correct=True),
                    QuestionOption(f"{qid}-o2", "Collect your belongings first", is_correct=False),
                    QuestionOption(f"{qid}-o3", "Use the lift", is_correct=False),
                    QuestionOption(f"{qid}-o4", "Wait for a second alarm", is_correct=False),
                ),
                points=1.0,
            )
        )

    for index in range(1, 5):
        qid = f"q-tf-{index:02d}"
        questions.append(
            BankQuestion(
                question_id=qid,
                version=1,
                type=QuestionType.TRUE_FALSE,
                prompt=f"({index}) A CO2 extinguisher is suitable for electrical fires.",
                quiz_id=QUIZ_ID,
                course_id=COURSE_ID,
                topic_id="topic-extinguishers",
                points=1.0,
            )
        )

    for index in range(1, 4):
        qid = f"q-ms-{index:02d}"
        questions.append(
            BankQuestion(
                question_id=qid,
                version=1,
                type=QuestionType.MULTI_SELECT,
                prompt=f"({index}) Which of the following are fire hazards?",
                quiz_id=QUIZ_ID,
                course_id=COURSE_ID,
                topic_id="topic-hazards",
                options=(
                    QuestionOption(f"{qid}-o1", "Overloaded socket", is_correct=True),
                    QuestionOption(f"{qid}-o2", "Blocked fire door", is_correct=True),
                    QuestionOption(f"{qid}-o3", "Closed window", is_correct=False),
                    QuestionOption(f"{qid}-o4", "Frayed cable", is_correct=True),
                ),
                min_selections=1,
                max_selections=3,
                points=2.0,
            )
        )

    for index in range(1, 3):
        qid = f"q-do-{index:02d}"
        questions.append(
            BankQuestion(
                question_id=qid,
                version=1,
                type=QuestionType.DRAG_TO_ORDER,
                prompt=f"({index}) Put the evacuation steps in the correct order.",
                quiz_id=QUIZ_ID,
                course_id=COURSE_ID,
                topic_id="topic-procedure",
                order_items=(
                    QuestionOrderItem(f"{qid}-i1", "Raise the alarm", correct_position=1),
                    QuestionOrderItem(f"{qid}-i2", "Evacuate the building", correct_position=2),
                    QuestionOrderItem(f"{qid}-i3", "Go to the assembly point", correct_position=3),
                    QuestionOrderItem(f"{qid}-i4", "Await the roll call", correct_position=4),
                ),
                points=3.0,
            )
        )

    for index in range(1, 3):
        qid = f"q-sn-{index:02d}"
        questions.append(
            BankQuestion(
                question_id=qid,
                version=1,
                type=QuestionType.SCENARIO,
                prompt=f"({index}) Respond to the situation described below.",
                quiz_id=QUIZ_ID,
                course_id=COURSE_ID,
                topic_id="topic-scenarios",
                scenario_text=(
                    "Smoke is coming from a server cabinet on the second floor. The "
                    "corridor is clear and the alarm has not yet sounded."
                ),
                sub_questions=(
                    ScenarioSubQuestion(
                        sub_question_id=f"{qid}-s1",
                        type=QuestionType.SINGLE_CHOICE,
                        prompt="What is your first action?",
                        options=(
                            QuestionOption(
                                f"{qid}-s1-o1", "Activate the nearest call point", is_correct=True
                            ),
                            QuestionOption(
                                f"{qid}-s1-o2", "Open the cabinet to investigate", is_correct=False
                            ),
                            QuestionOption(f"{qid}-s1-o3", "Finish your task", is_correct=False),
                        ),
                    ),
                    ScenarioSubQuestion(
                        sub_question_id=f"{qid}-s2",
                        type=QuestionType.TRUE_FALSE,
                        prompt="A water extinguisher is appropriate here.",
                    ),
                    ScenarioSubQuestion(
                        sub_question_id=f"{qid}-s3",
                        type=QuestionType.MULTI_SELECT,
                        prompt="Who must be informed?",
                        options=(
                            QuestionOption(f"{qid}-s3-o1", "The fire warden", is_correct=True),
                            QuestionOption(
                                f"{qid}-s3-o2", "The IT on-call engineer", is_correct=True
                            ),
                            QuestionOption(f"{qid}-s3-o3", "The catering team", is_correct=False),
                        ),
                        min_selections=1,
                    ),
                ),
                points=4.0,
            )
        )

    # One retired question, to demonstrate that retired content is never selected.
    questions.append(
        BankQuestion(
            question_id="q-sc-retired",
            version=2,
            type=QuestionType.SINGLE_CHOICE,
            prompt="(retired) This question has been withdrawn by UC-02.",
            quiz_id=QUIZ_ID,
            course_id=COURSE_ID,
            topic_id="topic-evacuation",
            options=(
                QuestionOption("q-sc-retired-o1", "Option A", is_correct=True),
                QuestionOption("q-sc-retired-o2", "Option B", is_correct=False),
            ),
            retired=True,
        )
    )

    return questions


def seed_demo_world(ctx: RequestContext) -> dict[str, object]:
    """Write the demo dataset. Idempotent: safe to run repeatedly."""
    ctx.seedable_configurations.upsert_quiz(
        quiz_id=QUIZ_ID,
        course_id=COURSE_ID,
        title="Fire Safety - Final Assessment",
        available=True,
    )

    for learner_id in LEARNERS:
        ctx.seedable_enrolments.upsert_enrolment(
            learner_id=learner_id, course_id=COURSE_ID, status=EnrolmentStatus.ACTIVE
        )

    questions = _questions()
    for question in questions:
        ctx.seedable_question_bank.upsert_question(question)

    configuration = ctx.seedable_configurations.publish_version(
        configuration_version_id="cfg-fire-safety-v1",
        quiz_id=QUIZ_ID,
        course_id=COURSE_ID,
        version=1,
        activated_at=ACTIVATED_AT,
        rules={
            "questionCount": 6,
            "questionTypeQuotas": [
                {"type": str(QuestionType.SINGLE_CHOICE), "count": 2},
                {"type": str(QuestionType.TRUE_FALSE), "count": 1},
                {"type": str(QuestionType.MULTI_SELECT), "count": 1},
                {"type": str(QuestionType.DRAG_TO_ORDER), "count": 1},
                {"type": str(QuestionType.SCENARIO), "count": 1},
            ],
            "timeLimitSeconds": 1800,
            "passMarkPercentage": 70,
            "maxAttempts": 3,
            "randomiseQuestionOrder": True,
            "randomiseOptionOrder": True,
            "questionPresentation": str(QuestionPresentation.ALL_AT_ONCE),
            "allowIncompleteSubmission": True,
        },
    )

    return {
        "quizId": QUIZ_ID,
        "courseId": COURSE_ID,
        "learners": list(LEARNERS),
        "configurationVersionId": configuration.configuration_version_id,
        "questionCount": len(questions),
    }
