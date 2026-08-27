"""UC-04 application service.

Expressed against ports and domain models only - no HTTP, no provider payloads, no company
API. Replacing a mock adapter with a real one does not touch this file.

The invariants it owns:

* enrolment is verified server-side, on every request, before any lesson content is loaded;
* quiz protection runs on every turn and cannot be influenced by anything the caller sends;
* the protected path and the normal path converge on explaining the concept - no bare refusals;
* grounding is decided here and recorded, never inferred from the generator's prose;
* a cross-lesson reference that does not resolve against the course structure is stripped;
* verbatim lesson material leaves only through the extraction budget;
* no provider failure escapes as an untyped exception, and none of them leaves the learner
  with nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.enums import (
    ExplanationProfile,
    FramingStrategy,
    Grounding,
    NaricLevel,
    NaricLevelSource,
    QuestionClass,
    QuizIntentLabel,
    RatingState,
    ResponseAction,
    ResponseStatus,
    SectionRefStatus,
    SourceStatus,
    UNCLASSIFIED,
)
from ..domain.errors import (
    AccessDenied,
    NotEnrolled,
    NotFound,
    ProviderError,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    SessionIdentityError,
)
from ..domain.models import (
    CoachingResponse,
    ConceptTag,
    CourseStructure,
    CrossLessonRef,
    FalsePositiveRecord,
    GenerationRequest,
    GenerationResult,
    InteractionRecord,
    LearnerContext,
    LessonConcept,
    LessonContent,
    LessonSection,
    QuizAssessment,
    QuizIntentResult,
    SectionReference,
    default_learner_context,
)
from ..domain.vocabularies import is_known_concept, topic_for_concept
from ..ports import (
    AnswerGenerator,
    Clock,
    ConceptTagger,
    CoursesProvider,
    FramingRegistry,
    IdGenerator,
    InteractionLogRepository,
    LearnerContextProvider,
    QuizIntentClassifier,
)
from .calibration import profile_for
from .cross_lesson import verify_references
from .extraction import quotable_material, spans_for_response
from .fingerprint import fingerprint, is_repeat
from .framing import FramingSelector
from .privacy import redact_question
from .prompts import select_prompt
from .quiz_protection import KnownItemMatcher, assess
from .section_matcher import MatchAnchor, SectionMatch, SectionMatcher
from .thresholds import QUIZ_TOPIC_MIN_SCORE

_PROFILE_LADDER: tuple[ExplanationProfile, ...] = (
    ExplanationProfile.BASIC,
    ExplanationProfile.INTERMEDIATE,
    ExplanationProfile.ADVANCED,
)


@dataclass
class ServiceDependencies:
    courses: CoursesProvider
    learner_context: LearnerContextProvider
    generator: AnswerGenerator
    quiz_classifier: QuizIntentClassifier
    concept_tagger: ConceptTagger
    interactions: InteractionLogRepository
    framings: FramingRegistry
    clock: Clock
    ids: IdGenerator
    quiz_match_threshold: float = 0.85
    allow_dev_session_ids: bool = False
    selector: FramingSelector = field(default_factory=FramingSelector)
    matcher: SectionMatcher = field(default_factory=SectionMatcher)


@dataclass
class _Loaded:
    """Everything the turn managed to load, with a status for each dependency."""

    lesson: LessonContent | None = None
    structure: CourseStructure | None = None
    context: LearnerContext | None = None
    status: dict[str, SourceStatus] = field(default_factory=dict)


class CoachingService:
    def __init__(self, deps: ServiceDependencies) -> None:
        self._d = deps
        self._known_items = KnownItemMatcher(threshold=deps.quiz_match_threshold)

    # ------------------------------------------------------------------------ public API

    def ask(self, *, session_id: str, user_id: str, course_id: str, lesson_id: str, question: str) -> CoachingResponse:
        self._require_session(session_id)
        loaded = self._load(session_id=session_id, user_id=user_id, course_id=course_id, lesson_id=lesson_id)

        tag = self._tag(question, loaded.lesson, loaded.status)
        match = self._match_section(question, loaded.lesson)
        quiz = self._assess_quiz(question, loaded.lesson)

        # A quiz question that names no section still needs the concept it tests, so fall back
        # to a name-anchored match below the normal threshold. Nothing returned reveals an answer.
        if match is None and quiz.intent_detected and loaded.lesson is not None:
            match = self._quiz_topic_match(question, loaded.lesson)

        concept_tag = self._resolve_concept_tag(tag, match, quiz)
        grounding = Grounding.LESSON if match is not None else Grounding.GENERAL_KNOWLEDGE

        return self._explain(
            session_id=session_id,
            user_id=user_id,
            course_id=course_id,
            lesson_id=lesson_id,
            question=question,
            loaded=loaded,
            match=match,
            concept_tag=concept_tag,
            topic_tag=tag.topic_tag,
            grounding=grounding,
            quiz=quiz,
            follow_up_of=None,
            profile_override=None,
            counts_as_explain_differently=False,
        )

    def explain_differently(self, *, interaction_id: str, user_id: str) -> CoachingResponse:
        prior = self._load_prior(interaction_id, user_id)
        loaded = self._load(
            session_id=prior.session_id, user_id=user_id, course_id=prior.course_id, lesson_id=prior.lesson_id
        )
        match = self._rematch(prior, loaded.lesson)
        quiz = self._no_quiz_signal()

        return self._explain(
            session_id=prior.session_id,
            user_id=user_id,
            course_id=prior.course_id,
            lesson_id=prior.lesson_id,
            question="",
            loaded=loaded,
            match=match,
            concept_tag=prior.concept_tag,
            topic_tag=prior.topic_tag,
            grounding=prior.grounding if match is not None else Grounding.GENERAL_KNOWLEDGE,
            quiz=quiz,
            follow_up_of=prior.interaction_id,
            profile_override=None,
            counts_as_explain_differently=True,
        )

    def go_deeper(self, *, interaction_id: str, user_id: str) -> CoachingResponse:
        """Re-explain the same concept one depth step further.

        Depth is a separate axis from framing: going deeper does not spend a framing strategy
        and is not a difficulty signal. It is capped by the profile ladder, and it draws on the
        same budgeted spans, so it cannot widen content exposure.
        """
        prior = self._load_prior(interaction_id, user_id)
        loaded = self._load(
            session_id=prior.session_id, user_id=user_id, course_id=prior.course_id, lesson_id=prior.lesson_id
        )
        match = self._rematch(prior, loaded.lesson)
        context = loaded.context or default_learner_context(user_id, SourceStatus.UNAVAILABLE)
        deeper = self._deepen(profile_for(context.naric_level))

        return self._explain(
            session_id=prior.session_id,
            user_id=user_id,
            course_id=prior.course_id,
            lesson_id=prior.lesson_id,
            question="",
            loaded=loaded,
            match=match,
            concept_tag=prior.concept_tag,
            topic_tag=prior.topic_tag,
            grounding=prior.grounding if match is not None else Grounding.GENERAL_KNOWLEDGE,
            quiz=self._no_quiz_signal(),
            follow_up_of=prior.interaction_id,
            profile_override=deeper,
            counts_as_explain_differently=False,
        )

    def list_session_interactions(self, *, session_id: str, user_id: str) -> list[InteractionRecord]:
        records = self._d.interactions.list_for_session(session_id)
        for record in records:
            if record.user_id != user_id:
                raise AccessDenied("session belongs to another user")
        return records

    # -------------------------------------------------------------------------- loading

    def _require_session(self, session_id: str) -> None:
        if session_id and session_id.strip():
            return
        # UC-04 receives a session id. It never mints one on a production path.
        raise SessionIdentityError("session_id is required; UC-04 does not create sessions")

    def _load(self, *, session_id: str, user_id: str, course_id: str, lesson_id: str) -> _Loaded:
        loaded = _Loaded()

        # ---- enrolment, before anything that touches content ---------------------------
        try:
            enrolment = self._d.courses.verify_enrolment(user_id, course_id)
            loaded.status["enrolment"] = SourceStatus.AVAILABLE
            if not enrolment.enrolled:
                raise NotEnrolled(user_id=user_id, course_id=course_id, reason=enrolment.reason)
        except (ProviderUnavailable, ProviderTimeout):
            # Cannot authorise, so no lesson content is loaded. General coaching is still
            # offered below: general knowledge is not the company's intellectual property.
            loaded.status["enrolment"] = SourceStatus.UNAVAILABLE
            loaded.context = self._load_context(session_id, user_id, loaded.status)
            return loaded
        except ProviderInvalidResponse:
            loaded.status["enrolment"] = SourceStatus.INVALID
            loaded.context = self._load_context(session_id, user_id, loaded.status)
            return loaded

        loaded.lesson, loaded.status["lesson"] = self._load_lesson(course_id, lesson_id)
        loaded.structure, loaded.status["course_structure"] = self._load_structure(course_id)
        loaded.context = self._load_context(session_id, user_id, loaded.status)
        return loaded

    def _load_lesson(self, course_id: str, lesson_id: str) -> tuple[LessonContent | None, SourceStatus]:
        try:
            lesson = self._d.courses.get_lesson(course_id, lesson_id)
        except (ProviderUnavailable, ProviderTimeout, NotFound):
            return None, SourceStatus.UNAVAILABLE
        except ProviderInvalidResponse:
            return None, SourceStatus.INVALID

        if not lesson.sections and not lesson.concepts:
            # Loaded successfully and genuinely carries nothing. Not the same as unavailable.
            return lesson, SourceStatus.EMPTY
        if not lesson.concepts or not lesson.sections:
            return lesson, SourceStatus.PARTIAL
        return lesson, SourceStatus.AVAILABLE

    def _load_structure(self, course_id: str) -> tuple[CourseStructure | None, SourceStatus]:
        try:
            structure = self._d.courses.get_course_structure(course_id)
        except (ProviderUnavailable, ProviderTimeout, NotFound):
            return None, SourceStatus.UNAVAILABLE
        except ProviderInvalidResponse:
            return None, SourceStatus.INVALID
        if not structure.lessons:
            return structure, SourceStatus.EMPTY
        return structure, SourceStatus.AVAILABLE

    def _load_context(self, session_id: str, user_id: str, status: dict[str, SourceStatus]) -> LearnerContext:
        try:
            context = self._d.learner_context.get_context(session_id, user_id)
        except (ProviderUnavailable, ProviderTimeout, NotFound):
            status["learner_context"] = SourceStatus.UNAVAILABLE
            return default_learner_context(user_id, SourceStatus.UNAVAILABLE)
        except ProviderInvalidResponse:
            status["learner_context"] = SourceStatus.INVALID
            return default_learner_context(user_id, SourceStatus.INVALID)
        status["learner_context"] = context.source_status
        return context

    def _load_prior(self, interaction_id: str, user_id: str) -> InteractionRecord:
        prior = self._d.interactions.get(interaction_id)
        if prior is None:
            raise NotFound("interaction_log", "no such interaction")
        if prior.user_id != user_id:
            raise AccessDenied("interaction belongs to another user")
        return prior

    # ------------------------------------------------------------------------ analysis

    def _tag(self, question: str, lesson: LessonContent | None, status: dict[str, SourceStatus]) -> ConceptTag:
        try:
            return self._d.concept_tagger.tag(question, lesson)
        except ProviderError:
            status["concept_tagger"] = SourceStatus.UNAVAILABLE
            return ConceptTag(concept_tag=UNCLASSIFIED, topic_tag=UNCLASSIFIED, matched=False)

    def _match_section(self, question: str, lesson: LessonContent | None) -> SectionMatch | None:
        if lesson is None:
            return None
        return self._d.matcher.match(question, lesson).best

    def _quiz_topic_match(self, question: str, lesson: LessonContent) -> SectionMatch | None:
        ranked = self._d.matcher.match(question, lesson).ranked
        return next(
            (m for m in ranked if m.anchor is MatchAnchor.NAME and m.score >= QUIZ_TOPIC_MIN_SCORE),
            None,
        )

    def _assess_quiz(self, question: str, lesson: LessonContent | None) -> QuizAssessment:
        known = self._known_items.match(question, lesson)
        try:
            intent = self._d.quiz_classifier.classify(question, lesson)
        except Exception:  # noqa: BLE001
            # Quiz protection is a safety control, so this is the one place a bare catch is
            # correct: an adapter that violates the error contract must not become a crash, and
            # must not become an unprotected answer either. Anything unusable is treated as
            # ambiguous, which routes through the protected path - and that path still explains
            # the concept. Contract-typed failures land here too; the outcome is identical.
            intent = QuizIntentResult(
                label=QuizIntentLabel.AMBIGUOUS.value, confidence=0.0, signals=("classifier_error",), classifier="unavailable"
            )
        return assess(intent, known, lesson.has_quiz_items if lesson else False)

    def _no_quiz_signal(self) -> QuizAssessment:
        """Follow-ups carry no new learner text, so there is nothing new to classify."""
        return assess(
            QuizIntentResult(
                label=QuizIntentLabel.CONCEPT_LEARNING_REQUEST.value, confidence=1.0, signals=(), classifier="not_applicable"
            ),
            None,
            False,
        )

    def _resolve_concept_tag(self, tag: ConceptTag, match: SectionMatch | None, quiz: QuizAssessment) -> str:
        """Resolve the concept, preferring the most authoritative signal available.

        A matched known quiz item names the concept the item tests, which beats a lexical tag:
        the item is authored against that concept, so it settles what the learner is really
        asking about. Otherwise the tagger is the vocabulary authority, and the lesson's own
        tag is the fallback.
        """
        if quiz.known_item is not None and quiz.known_item.concept_tag and is_known_concept(quiz.known_item.concept_tag):
            return quiz.known_item.concept_tag
        if tag.matched and is_known_concept(tag.concept_tag):
            return tag.concept_tag
        if match is not None and match.concept is not None and is_known_concept(match.concept.concept_tag):
            return match.concept.concept_tag
        return UNCLASSIFIED

    def _rematch(self, prior: InteractionRecord, lesson: LessonContent | None) -> SectionMatch | None:
        if lesson is None:
            return None
        if prior.concept_tag != UNCLASSIFIED:
            found = self._d.matcher.find_concept(prior.concept_tag, lesson)
            if found is not None:
                return found
        if prior.lesson_section_id:
            section = next((s for s in lesson.sections if s.section_id == prior.lesson_section_id), None)
            if section is not None:
                concept = next((c for c in lesson.concepts if c.section_id == section.section_id), None)
                return SectionMatch(section=section, concept=concept, score=1.0, anchor=MatchAnchor.NAME, matched_tokens=0)
        return None

    def _deepen(self, profile: ExplanationProfile) -> ExplanationProfile:
        index = _PROFILE_LADDER.index(profile)
        return _PROFILE_LADDER[min(index + 1, len(_PROFILE_LADDER) - 1)]

    # ----------------------------------------------------------------------- explaining

    def _explain(
        self,
        *,
        session_id: str,
        user_id: str,
        course_id: str,
        lesson_id: str,
        question: str,
        loaded: _Loaded,
        match: SectionMatch | None,
        concept_tag: str,
        topic_tag: str,
        grounding: Grounding,
        quiz: QuizAssessment,
        follow_up_of: str | None,
        profile_override: ExplanationProfile | None,
        counts_as_explain_differently: bool,
    ) -> CoachingResponse:
        context = loaded.context or default_learner_context(user_id, SourceStatus.UNAVAILABLE)
        profile = profile_override or profile_for(context.naric_level)

        section = match.section if match else None
        concept = match.concept if match else None
        material = quotable_material(section, concept)

        # Nothing curated to transform means we say so rather than reciting the body.
        if grounding is Grounding.LESSON and material.empty:
            grounding = Grounding.GENERAL_KNOWLEDGE

        history = self._framing_history(session_id, concept_tag)
        plan = self._d.selector.plan(history)

        if counts_as_explain_differently and plan.exhausted:
            return self._exhausted_response(
                session_id=session_id,
                user_id=user_id,
                course_id=course_id,
                lesson_id=lesson_id,
                loaded=loaded,
                match=match,
                concept_tag=concept_tag,
                topic_tag=topic_tag,
                grounding=grounding,
                context=context,
                profile=profile,
                plan_used=plan.used,
                follow_up_of=follow_up_of,
            )

        candidates = plan.candidates or (self._d.selector.order[0],)
        generated, framing_used, rejected = self._generate_non_repeat(
            question=question,
            profile=profile,
            grounding=grounding,
            loaded=loaded,
            section=section,
            concept=concept,
            material=material,
            candidates=candidates,
            history=history,
            suppress_echo=quiz.intent_detected,
        )

        if generated is None:
            # Every remaining framing produced a repeat: honest exhaustion, never a re-run.
            return self._exhausted_response(
                session_id=session_id,
                user_id=user_id,
                course_id=course_id,
                lesson_id=lesson_id,
                loaded=loaded,
                match=match,
                concept_tag=concept_tag,
                topic_tag=topic_tag,
                grounding=grounding,
                context=context,
                profile=profile,
                plan_used=plan.used,
                follow_up_of=follow_up_of,
                paraphrase_rejected=rejected,
            )

        verified = verify_references(generated.cross_lesson_refs, loaded.structure, lesson_id)

        self._record_framing(session_id, concept_tag, framing_used, generated.explanation)
        count = self._framing_count(session_id, concept_tag)
        interaction_id = self._d.ids.next_id("int")
        response_id = self._d.ids.next_id("res")

        self._write_record(
            interaction_id=interaction_id,
            response_id=response_id,
            session_id=session_id,
            user_id=user_id,
            course_id=course_id,
            lesson_id=lesson_id,
            question=question,
            topic_tag=topic_tag,
            concept_tag=concept_tag,
            section_id=section.section_id if section else None,
            grounding=grounding,
            quiz=quiz,
            framing=framing_used,
            explain_differently_count=count,
            follow_up_of=follow_up_of,
            level=context.naric_level,
        )
        self._maybe_log_false_positive(interaction_id, session_id, user_id, quiz, concept_tag)

        return CoachingResponse(
            status=ResponseStatus.ANSWERED,
            interaction_id=interaction_id,
            session_id=session_id,
            course_id=course_id,
            lesson_id=lesson_id,
            grounding=grounding,
            explanation=generated.explanation,
            section_reference=self._section_reference(section, grounding),
            concept_tag=concept_tag,
            topic_tag=topic_tag,
            framing_used=framing_used,
            explain_differently_count=count,
            cross_lesson_references=verified.verified,
            actions=self._actions(grounding, plan.candidates, framing_used, profile),
            notice=self._notice(grounding, loaded.status),
            naric_level=context.naric_level,
            naric_level_source=context.naric_level_source,
            explanation_profile=profile,
            quiz_intent_detected=quiz.intent_detected,
            source_status=dict(loaded.status),
            rating_state=RatingState.PENDING,
        )

    def _framing_history(self, session_id: str, concept_tag: str) -> list:
        try:
            return self._d.framings.used_framings(session_id, concept_tag)
        except ProviderError:
            # A registry outage costs the non-repetition memory for this turn, not the answer.
            return []

    def _record_framing(self, session_id: str, concept_tag: str, framing: FramingStrategy, text: str) -> None:
        prints = fingerprint(text)
        try:
            self._d.framings.record(
                session_id=session_id,
                concept_tag=concept_tag,
                framing=framing,
                fingerprint=prints.value,
                fingerprint_tokens=prints.tokens,
                recorded_at=self._d.clock.now(),
            )
        except ProviderError:
            pass

    def _framing_count(self, session_id: str, concept_tag: str) -> int:
        try:
            return self._d.framings.explain_differently_count(session_id, concept_tag)
        except ProviderError:
            return 0

    def _generate_non_repeat(
        self,
        *,
        question: str,
        profile: ExplanationProfile,
        grounding: Grounding,
        loaded: _Loaded,
        section: LessonSection | None,
        concept: LessonConcept | None,
        material,
        candidates: tuple[FramingStrategy, ...],
        history: list,
        suppress_echo: bool,
    ) -> tuple[GenerationResult | None, FramingStrategy, int]:
        """Walk unused framings until one produces something that is not a repeat.

        A paraphrase counts as a repeat. Nothing is returned twice: if every remaining framing
        paraphrases an earlier answer, the caller reports exhaustion instead.
        """
        prompt = select_prompt(grounding, profile)
        rejected = 0
        # Rotate the budgeted spans by how many explanations this concept has already had, not
        # by position in the candidate list - otherwise every turn opens on the same two spans
        # and only the framing sentence changes. The budget itself is unaffected: the same small
        # set is rotated, never widened.
        attempt_offset = len(history)
        for index, framing in enumerate(candidates):
            request = GenerationRequest(
                question=question,
                profile=profile,
                framing=framing,
                grounding=grounding,
                lesson_title=loaded.lesson.title if loaded.lesson else None,
                course_title=loaded.structure.title if loaded.structure else None,
                section=section,
                concept=concept,
                quotable_spans=spans_for_response(material, attempt_offset + index),
                budget_exhausted=material.empty,
                suppress_question_echo=suppress_echo,
                candidate_cross_lesson_refs=self._candidate_refs(loaded, section),
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
            )
            result = self._d.generator.generate(request)
            self._validate(result, framing)
            verdict = is_repeat(fingerprint(result.explanation), history)
            if not verdict.is_repeat:
                return result, framing, rejected
            rejected += 1
        return None, candidates[0], rejected

    def _validate(self, result: object, framing: FramingStrategy) -> None:
        if not isinstance(result, GenerationResult):
            raise ProviderInvalidResponse("answer_generator", "generator returned an unexpected type")
        if not result.explanation or not result.explanation.strip():
            raise ProviderInvalidResponse("answer_generator", "generator returned an empty explanation")
        if result.framing_used is not framing:
            raise ProviderInvalidResponse("answer_generator", "generator ignored the requested framing")

    def _candidate_refs(self, loaded: _Loaded, section: LessonSection | None) -> tuple[CrossLessonRef, ...]:
        """Offer the generator only lessons that already exist in the loaded structure."""
        if loaded.structure is None or loaded.lesson is None or section is None:
            return ()
        return tuple(
            CrossLessonRef(lesson_id=ref.lesson_id, title=ref.title, reason="same course")
            for ref in loaded.structure.lessons
            if ref.lesson_id != loaded.lesson.lesson_id
        )

    # ------------------------------------------------------------------------ responses

    def _exhausted_response(
        self,
        *,
        session_id: str,
        user_id: str,
        course_id: str,
        lesson_id: str,
        loaded: _Loaded,
        match: SectionMatch | None,
        concept_tag: str,
        topic_tag: str,
        grounding: Grounding,
        context: LearnerContext,
        profile: ExplanationProfile,
        plan_used: tuple[FramingStrategy, ...],
        follow_up_of: str | None,
        paraphrase_rejected: int = 0,
    ) -> CoachingResponse:
        """Honest exhaustion. No new framing, no recycled one, and still not a dead end."""
        section = match.section if match else None
        used = ", ".join(f.value.replace("_", " ") for f in plan_used) or "every approach available"
        detail = (
            "I have now explained this concept from every angle I have available "
            f"({used}). Re-running one of them would only repeat myself."
        )
        if paraphrase_rejected:
            detail += " The remaining approaches came out as restatements of what I already said."
        explanation = (
            f"{detail} If it still is not landing, the useful next steps are to go deeper on the "
            "same point, or to move on and come back to it."
        )

        interaction_id = self._d.ids.next_id("int")
        response_id = self._d.ids.next_id("res")
        count = self._framing_count(session_id, concept_tag)

        self._write_record(
            interaction_id=interaction_id,
            response_id=response_id,
            session_id=session_id,
            user_id=user_id,
            course_id=course_id,
            lesson_id=lesson_id,
            question="",
            topic_tag=topic_tag,
            concept_tag=concept_tag,
            section_id=section.section_id if section else None,
            grounding=grounding,
            quiz=self._no_quiz_signal(),
            framing=None,
            explain_differently_count=count,
            follow_up_of=follow_up_of,
            level=context.naric_level,
        )

        return CoachingResponse(
            status=ResponseStatus.FRAMINGS_EXHAUSTED,
            interaction_id=interaction_id,
            session_id=session_id,
            course_id=course_id,
            lesson_id=lesson_id,
            grounding=grounding,
            explanation=explanation,
            section_reference=self._section_reference(section, grounding),
            concept_tag=concept_tag,
            topic_tag=topic_tag,
            framing_used=None,
            explain_differently_count=count,
            cross_lesson_references=(),
            actions=(ResponseAction.GO_DEEPER, ResponseAction.START_FREE_FORM_SESSION),
            notice="Every explanation approach for this concept has been used in this session.",
            naric_level=context.naric_level,
            naric_level_source=context.naric_level_source,
            explanation_profile=profile,
            quiz_intent_detected=False,
            source_status=dict(loaded.status),
            rating_state=RatingState.PENDING,
        )

    def _section_reference(self, section: LessonSection | None, grounding: Grounding) -> SectionReference:
        if grounding is Grounding.LESSON and section is not None:
            return SectionReference(status=SectionRefStatus.RESOLVED, lesson_section_id=section.section_id)
        # Never guess an identifier.
        return SectionReference(status=SectionRefStatus.UNRESOLVED, lesson_section_id=None)

    def _actions(
        self,
        grounding: Grounding,
        remaining: tuple[FramingStrategy, ...],
        used: FramingStrategy,
        profile: ExplanationProfile,
    ) -> tuple[ResponseAction, ...]:
        actions: list[ResponseAction] = []
        if len([f for f in remaining if f is not used]) > 0:
            actions.append(ResponseAction.EXPLAIN_DIFFERENTLY)
        if profile is not ExplanationProfile.ADVANCED:
            actions.append(ResponseAction.GO_DEEPER)
        if grounding is Grounding.GENERAL_KNOWLEDGE:
            actions.append(ResponseAction.START_FREE_FORM_SESSION)
        return tuple(actions)

    def _notice(self, grounding: Grounding, status: dict[str, SourceStatus]) -> str | None:
        if status.get("enrolment") in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
            return (
                "Your enrolment could not be verified just now, so I cannot open this lesson's "
                "content. This answer comes from general knowledge."
            )
        if status.get("lesson") in (SourceStatus.UNAVAILABLE, SourceStatus.INVALID):
            return (
                "The linked lesson could not be accessed, so this answer covers the general topic "
                "rather than the lesson's own material."
            )
        if grounding is Grounding.GENERAL_KNOWLEDGE:
            return "This is not covered by the linked lesson, so I am answering from general knowledge."
        return None

    # -------------------------------------------------------------------------- logging

    def _write_record(
        self,
        *,
        interaction_id: str,
        response_id: str,
        session_id: str,
        user_id: str,
        course_id: str,
        lesson_id: str,
        question: str,
        topic_tag: str,
        concept_tag: str,
        section_id: str | None,
        grounding: Grounding,
        quiz: QuizAssessment,
        framing: FramingStrategy | None,
        explain_differently_count: int,
        follow_up_of: str | None,
        level: NaricLevel,
    ) -> None:
        record = InteractionRecord(
            interaction_id=interaction_id,
            session_id=session_id,
            user_id=user_id,
            asked_at=self._d.clock.now(),
            question_text=redact_question(question),
            topic_tag=topic_tag,
            question_class=self._question_class(grounding, quiz),
            naric_level=level,
            response_id=response_id,
            course_id=course_id,
            lesson_id=lesson_id,
            lesson_section_id=section_id,
            concept_tag=concept_tag,
            grounding=grounding,
            quiz_intent_detected=quiz.intent_detected,
            quiz_detection_confirmed=quiz.detection_confirmed,
            framing_used=framing,
            explain_differently_count=explain_differently_count,
            follow_up_of=follow_up_of,
            rating_state=RatingState.PENDING,
        )
        try:
            self._d.interactions.append(record)
        except ProviderError:
            # A log outage degrades observability, never the learner's answer.
            pass

    def _question_class(self, grounding: Grounding, quiz: QuizAssessment) -> QuestionClass:
        if quiz.intent.label == QuizIntentLabel.AMBIGUOUS.value and quiz.known_item is None:
            return QuestionClass.AMBIGUOUS
        if quiz.intent_detected:
            return QuestionClass.QUIZ_ANSWER_SEEKING
        if grounding is Grounding.GENERAL_KNOWLEDGE:
            return QuestionClass.OUT_OF_LESSON
        return QuestionClass.CONCEPT_EXPLANATION

    def _maybe_log_false_positive(
        self, interaction_id: str, session_id: str, user_id: str, quiz: QuizAssessment, concept_tag: str
    ) -> None:
        if not quiz.suspected_false_positive:
            return
        record = FalsePositiveRecord(
            record_id=self._d.ids.next_id("fp"),
            interaction_id=interaction_id,
            session_id=session_id,
            user_id=user_id,
            recorded_at=self._d.clock.now(),
            classifier_label=quiz.intent.label,
            classifier_confidence=quiz.intent.confidence,
            classifier_signals=quiz.intent.signals,
            known_item_matched=quiz.known_item is not None,
            concept_tag=concept_tag,
            explanation_delivered=True,
        )
        try:
            self._d.interactions.append_false_positive(record)
        except ProviderError:
            pass
