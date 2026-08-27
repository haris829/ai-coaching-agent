"""Deterministic fixture data.

Content is authored here for testing. Sections carry five key points apiece, deliberately more
than the extraction budget allows, so the budget visibly binds in the extraction test rather
than passing because the fixture happens to be small.
"""

from __future__ import annotations

from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ...domain.models import (
    CourseLessonRef,
    CourseStructure,
    LearnerContext,
    LessonConcept,
    LessonContent,
    LessonSection,
    QuizItem,
)

COURSE_EVIDENCE = "course_evi_201"
COURSE_EMPTY = "course_empty_900"
COURSE_UNAVAILABLE = "course_unavailable_500"
COURSE_UNKNOWN = "course_does_not_exist"

LESSON_HEARSAY = "lesson_evi_01"
LESSON_WITNESS = "lesson_evi_02"
LESSON_PRIVILEGE = "lesson_evi_03"
LESSON_NO_QUIZ = "lesson_evi_04"
LESSON_SPARSE = "lesson_evi_05"
LESSON_UNAVAILABLE = "lesson_unavailable"
LESSON_TIMEOUT = "lesson_timeout"
LESSON_INVALID = "lesson_invalid"
LESSON_UNKNOWN = "lesson_does_not_exist"
LESSON_GHOST = "lesson_evi_99_ghost"

USER_ENROLLED = "user_solicitor_1"
USER_LEVEL_3 = "user_paralegal_3"
USER_LEVEL_7 = "user_barrister_7"
USER_LEVEL_INVALID = "user_bad_level"
USER_NO_CONTEXT = "user_no_context"
USER_CONTEXT_DOWN = "user_context_down"
USER_NOT_ENROLLED = "user_outsider"
USER_LAPSED = "user_lapsed"
USER_ENROLMENT_DOWN = "user_enrolment_down"

SESSION_MAIN = "sess_main_1"
SESSION_SECOND = "sess_second_2"


_HEARSAY = LessonContent(
    course_id=COURSE_EVIDENCE,
    lesson_id=LESSON_HEARSAY,
    title="The Rule Against Hearsay",
    revision="rev-2026-05-02",
    sections=(
        LessonSection(
            section_id="sec_hearsay_definition",
            title="What Counts as Hearsay",
            order=0,
            body=(
                "Hearsay is a statement made otherwise than by a person giving oral evidence in the "
                "proceedings, which is tendered as evidence of the matters stated. The defining "
                "feature is the purpose for which the statement is offered, not where it was made. "
                "A statement offered to prove that it was made, rather than that its contents are "
                "true, is not hearsay at all. The rule exists because the maker cannot be "
                "cross-examined on a statement they did not give in the proceedings."
            ),
            key_points=(
                "Hearsay turns on the purpose the statement is offered for, not where it was made",
                "A statement offered only to prove it was made is not hearsay",
                "The rule exists because the maker cannot be cross-examined on it",
                "Written and oral out-of-court statements are treated alike",
                "The tribunal decides admissibility before weight",
            ),
            concept_tags=("hearsay",),
        ),
        LessonSection(
            section_id="sec_hearsay_exceptions",
            title="Exceptions to the Rule",
            order=1,
            body=(
                "Several categories of out-of-court statement are admissible despite the rule. "
                "Business records are admissible where the document was created in the ordinary "
                "course of a business or profession. Res gestae covers statements so closely "
                "connected to an event that concoction can be excluded. In civil proceedings the "
                "rule has been substantially relaxed by statute, with notice provisions replacing "
                "outright exclusion."
            ),
            key_points=(
                "Business records are admissible where created in the ordinary course of business",
                "Res gestae covers statements too closely connected to the event to be concocted",
                "In civil proceedings the rule is relaxed by statute with notice provisions",
                "The party asserting the exception carries the burden of establishing it",
                "Admissibility under an exception does not settle the weight given",
            ),
            concept_tags=("hearsay_exception",),
        ),
        LessonSection(
            section_id="sec_hearsay_proof",
            title="Burden and Standard of Proof on Admissibility",
            order=2,
            body=(
                "Where admissibility is disputed, the party seeking to adduce the statement bears "
                "the burden. In civil proceedings the standard is the balance of probabilities. The "
                "burden on admissibility is distinct from the burden on the substantive issue."
            ),
            key_points=(
                "The party adducing the statement bears the burden on admissibility",
                "In civil proceedings the standard is the balance of probabilities",
                "The admissibility burden is distinct from the substantive burden",
                "A ruling on admissibility can be revisited if the basis changes",
                "Reasons for an admissibility ruling should be recorded",
            ),
            concept_tags=("burden_of_proof", "standard_of_proof"),
        ),
    ),
    concepts=(
        LessonConcept(
            concept_tag="hearsay",
            name="Hearsay",
            section_id="sec_hearsay_definition",
            summary=(
                "Hearsay is an out-of-court statement tendered as evidence of the truth of the "
                "matters it states."
            ),
            keywords=("hearsay", "out of court", "statement", "truth of the matter", "oral evidence"),
        ),
        LessonConcept(
            concept_tag="hearsay_exception",
            name="Hearsay exception",
            section_id="sec_hearsay_exceptions",
            summary=(
                "A hearsay exception is a recognised category in which an out-of-court statement "
                "is admissible despite the general rule."
            ),
            keywords=("exception", "business records", "res gestae", "admissible", "statute"),
        ),
        LessonConcept(
            concept_tag="burden_of_proof",
            name="Burden of proof",
            section_id="sec_hearsay_proof",
            summary="The burden of proof is the obligation on a party to establish a disputed matter.",
            keywords=("burden", "who must prove", "adducing party", "obligation"),
        ),
        LessonConcept(
            concept_tag="standard_of_proof",
            name="Standard of proof",
            section_id="sec_hearsay_proof",
            summary=(
                "The standard of proof is the degree of persuasion required, which in civil "
                "proceedings is the balance of probabilities."
            ),
            keywords=("standard", "balance of probabilities", "degree of persuasion"),
        ),
    ),
    quiz_items=(
        QuizItem(
            quiz_item_id="quiz_evi_01_q1",
            question_text=(
                "Which of the following statements is admissible as an exception to the rule "
                "against hearsay?"
            ),
            option_ids=("a", "b", "c", "d"),
            correct_option_id="c",
            concept_tag="hearsay_exception",
        ),
        QuizItem(
            quiz_item_id="quiz_evi_01_q2",
            question_text="A witness repeats what a colleague told them. Is that hearsay, and why?",
            option_ids=("a", "b", "c", "d"),
            correct_option_id="a",
            concept_tag="hearsay",
        ),
    ),
)

_WITNESS = LessonContent(
    course_id=COURSE_EVIDENCE,
    lesson_id=LESSON_WITNESS,
    title="Witness Evidence, Competence and Compellability",
    sections=(
        LessonSection(
            section_id="sec_competence",
            title="Competence and Compellability",
            order=0,
            body=(
                "A competent witness is one who may lawfully give evidence. A compellable witness "
                "is one who may be required to do so. The two are distinct: a witness may be "
                "competent but not compellable."
            ),
            key_points=(
                "Competence is whether a witness may give evidence at all",
                "Compellability is whether a witness can be required to give it",
                "A witness may be competent but not compellable",
                "Capacity to understand questions is the usual competence test",
                "Objections to competence are taken before the witness is sworn",
            ),
            concept_tags=("witness_competence", "witness_compellability"),
        ),
    ),
    concepts=(
        LessonConcept(
            concept_tag="witness_competence",
            name="Competence",
            section_id="sec_competence",
            summary="Competence is whether a person may lawfully give evidence in the proceedings.",
            keywords=("competence", "competent", "may give evidence", "capacity"),
        ),
        LessonConcept(
            concept_tag="witness_compellability",
            name="Compellability",
            section_id="sec_competence",
            summary="Compellability is whether a competent witness can be required to give evidence.",
            keywords=("compellability", "compellable", "required to give evidence", "summons"),
        ),
    ),
)

_PRIVILEGE = LessonContent(
    course_id=COURSE_EVIDENCE,
    lesson_id=LESSON_PRIVILEGE,
    title="Privilege and Standard Disclosure",
    sections=(
        LessonSection(
            section_id="sec_privilege",
            title="Legal Advice and Litigation Privilege",
            order=0,
            body=(
                "Legal advice privilege protects confidential communications between lawyer and "
                "client for the purpose of giving or receiving legal advice. Litigation privilege "
                "additionally protects communications with third parties where litigation is in "
                "reasonable contemplation and the dominant purpose is that litigation."
            ),
            key_points=(
                "Legal advice privilege covers lawyer-client communications for legal advice",
                "Litigation privilege extends to third parties where litigation is contemplated",
                "The dominant purpose test governs litigation privilege",
                "Privilege belongs to the client and only the client can waive it",
                "Privilege survives the end of the retainer",
            ),
            concept_tags=("legal_advice_privilege", "litigation_privilege"),
        ),
        LessonSection(
            section_id="sec_disclosure",
            title="Standard Disclosure",
            order=1,
            body=(
                "Standard disclosure requires a party to disclose the documents on which it relies "
                "and those which adversely affect its own or another party's case, subject to a "
                "reasonable search."
            ),
            key_points=(
                "Standard disclosure covers documents relied on and those adversely affecting a case",
                "The obligation is subject to a reasonable and proportionate search",
                "Privileged documents are listed but need not be produced",
                "The duty of disclosure continues until proceedings end",
                "A disclosure statement certifies the extent of the search",
            ),
            concept_tags=("standard_disclosure",),
        ),
    ),
    concepts=(
        LessonConcept(
            concept_tag="legal_advice_privilege",
            name="Legal advice privilege",
            section_id="sec_privilege",
            summary=(
                "Legal advice privilege protects confidential lawyer-client communications made "
                "for the purpose of giving or receiving legal advice."
            ),
            keywords=("legal advice privilege", "lawyer client", "confidential", "advice"),
        ),
        LessonConcept(
            concept_tag="litigation_privilege",
            name="Litigation privilege",
            section_id="sec_privilege",
            summary=(
                "Litigation privilege protects communications with third parties where litigation "
                "is in reasonable contemplation and is the dominant purpose."
            ),
            keywords=("litigation privilege", "dominant purpose", "third parties", "contemplation"),
        ),
        LessonConcept(
            concept_tag="standard_disclosure",
            name="Standard disclosure",
            section_id="sec_disclosure",
            summary=(
                "Standard disclosure is the obligation to disclose documents relied on and those "
                "adversely affecting a party's case, subject to a reasonable search."
            ),
            keywords=("standard disclosure", "documents", "reasonable search", "list"),
        ),
    ),
)

#: A lesson the Courses Agent exposes without quiz items - known-item matching is unavailable.
_NO_QUIZ = LessonContent(
    course_id=COURSE_EVIDENCE,
    lesson_id=LESSON_NO_QUIZ,
    title="Expert Evidence",
    sections=(
        LessonSection(
            section_id="sec_expert",
            title="Duties of an Expert Witness",
            order=0,
            body="An expert's overriding duty is to the court, not to the party instructing them.",
            key_points=(
                "The expert's overriding duty is to the court",
                "The duty overrides any obligation to the instructing party",
                "An expert must state the range of opinion where one exists",
            ),
            concept_tags=("expert_evidence",),
        ),
    ),
    concepts=(
        LessonConcept(
            concept_tag="expert_evidence",
            name="Expert evidence",
            section_id="sec_expert",
            summary="Expert evidence is opinion evidence given by a witness qualified in a field.",
            keywords=("expert", "opinion", "overriding duty", "instructing party"),
        ),
    ),
)

#: Sparse content: a section with prose but no curated key points and no concept summary. This
#: is the shape that used to trigger verbatim body recitation.
_SPARSE = LessonContent(
    course_id=COURSE_EVIDENCE,
    lesson_id=LESSON_SPARSE,
    title="Without Prejudice Correspondence",
    sections=(
        LessonSection(
            section_id="sec_wp",
            title="Without Prejudice",
            order=0,
            body=(
                "PROPRIETARY SENTENCE ONE about without prejudice correspondence. PROPRIETARY "
                "SENTENCE TWO about the settlement privilege. PROPRIETARY SENTENCE THREE about "
                "the exceptions."
            ),
            key_points=(),
            concept_tags=("without_prejudice",),
        ),
    ),
    concepts=(
        LessonConcept(
            concept_tag="without_prejudice",
            name="Without prejudice",
            section_id="sec_wp",
            summary="",
            keywords=("without prejudice", "settlement correspondence"),
        ),
    ),
)

LESSONS: dict[str, LessonContent] = {
    LESSON_HEARSAY: _HEARSAY,
    LESSON_WITNESS: _WITNESS,
    LESSON_PRIVILEGE: _PRIVILEGE,
    LESSON_NO_QUIZ: _NO_QUIZ,
    LESSON_SPARSE: _SPARSE,
}

COURSE_STRUCTURES: dict[str, CourseStructure] = {
    COURSE_EVIDENCE: CourseStructure(
        course_id=COURSE_EVIDENCE,
        title="Evidence in Civil Litigation",
        lessons=(
            CourseLessonRef(lesson_id=LESSON_HEARSAY, title="The Rule Against Hearsay", order=0),
            CourseLessonRef(lesson_id=LESSON_WITNESS, title="Witness Evidence, Competence and Compellability", order=1),
            CourseLessonRef(lesson_id=LESSON_PRIVILEGE, title="Privilege and Standard Disclosure", order=2),
            CourseLessonRef(lesson_id=LESSON_NO_QUIZ, title="Expert Evidence", order=3),
            CourseLessonRef(lesson_id=LESSON_SPARSE, title="Without Prejudice Correspondence", order=4),
        ),
    ),
    #: A single-lesson course, for the structure scenario matrix.
    COURSE_EMPTY: CourseStructure(course_id=COURSE_EMPTY, title="Course With No Lessons", lessons=()),
}

ENROLMENTS: dict[str, set[str]] = {
    USER_ENROLLED: {COURSE_EVIDENCE, COURSE_EMPTY, COURSE_UNKNOWN},
    USER_LEVEL_3: {COURSE_EVIDENCE},
    USER_LEVEL_7: {COURSE_EVIDENCE},
    USER_LEVEL_INVALID: {COURSE_EVIDENCE},
    USER_NO_CONTEXT: {COURSE_EVIDENCE},
    USER_CONTEXT_DOWN: {COURSE_EVIDENCE},
    USER_ENROLMENT_DOWN: {COURSE_EVIDENCE},
    USER_NOT_ENROLLED: set(),
    USER_LAPSED: set(),
}

LEARNER_CONTEXTS: dict[str, LearnerContext] = {
    USER_ENROLLED: LearnerContext(
        user_id=USER_ENROLLED,
        naric_level=NaricLevel.LEVEL_6,
        naric_level_source=NaricLevelSource.RETRIEVED,
        practice_area="civil_litigation",
        source_status=SourceStatus.AVAILABLE,
    ),
    USER_LEVEL_3: LearnerContext(
        user_id=USER_LEVEL_3,
        naric_level=NaricLevel.LEVEL_3,
        naric_level_source=NaricLevelSource.RETRIEVED,
        practice_area=None,
        source_status=SourceStatus.PARTIAL,
    ),
    USER_LEVEL_7: LearnerContext(
        user_id=USER_LEVEL_7,
        naric_level=NaricLevel.LEVEL_7,
        naric_level_source=NaricLevelSource.RETRIEVED,
        practice_area="commercial_litigation",
        source_status=SourceStatus.AVAILABLE,
    ),
}
