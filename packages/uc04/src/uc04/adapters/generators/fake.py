"""FakeAnswerGenerator - deterministic, offline, free.

The entire test suite runs against this. No network, no API key, no cost, no flakiness.

It renders a lesson-grounded explanation from templates parameterised on three axes:

* **framing strategy** - the approach taken, which is what "explain differently" varies;
* **explanation profile** - depth, which is what NARIC level drives;
* **quotable spans** - the only lesson text it is allowed to reproduce, already selected and
  truncated by the service's extraction budget.

It has no access to ``section.body``. That is deliberate: the generator physically cannot
recite lesson prose, because the service never hands it any. Where the budget is empty it says
the lesson does not carry enough on the point, rather than inventing or reciting.
"""

from __future__ import annotations

from ...domain.enums import ExplanationProfile, FramingStrategy, Grounding
from ...domain.models import CrossLessonRef, GenerationRequest, GenerationResult
from ...core.text import stable_hash, unique_tokens

_PROFILE_OPENER: dict[ExplanationProfile, str] = {
    ExplanationProfile.BASIC: "Here is the plain version first.",
    ExplanationProfile.INTERMEDIATE: "Here is the working account.",
    ExplanationProfile.ADVANCED: "Taking it at the level you will actually argue it.",
}

#: Basic adds scaffolding; advanced adds the caveats a practitioner needs. These markers are
#: what the calibration tests assert on, so depth is measurable rather than a differing enum.
_PROFILE_CLOSER: dict[ExplanationProfile, str] = {
    ExplanationProfile.BASIC: (
        "In everyday terms: the point of the rule is to keep unreliable material out of the "
        "decision. If a word here is unfamiliar, say which one and I will define it before we "
        "go on."
    ),
    ExplanationProfile.INTERMEDIATE: (
        "Applied in practice, the question to ask yourself is which limb of the rule your facts "
        "actually engage."
    ),
    ExplanationProfile.ADVANCED: (
        "Caveats and edge cases worth holding: the boundary is contested where the categories "
        "overlap, the burden of establishing the exception sits with the party asserting it, and "
        "appellate treatment has narrowed the discretion in recent years. Assume the tribunal "
        "will test the weakest limb."
    ),
}

_FRAMING_LEAD: dict[FramingStrategy, str] = {
    FramingStrategy.FIRST_PRINCIPLES: (
        "Start from why the rule exists at all, and the shape of it follows."
    ),
    FramingStrategy.ANALOGY: (
        "By analogy - and this is my illustration, not the lesson's - it behaves like a filter "
        "on a water supply: it is not there to judge the water, only to stop what cannot be "
        "checked from reaching the tap."
    ),
    FramingStrategy.WORKED_EXAMPLE: (
        "Take a concrete run-through. A party wants to rely on something said outside the "
        "hearing; walk the material through the test one step at a time and see where it lands."
    ),
    FramingStrategy.CONTRAST_NEAR_MISS: (
        "The useful move is to set it against the case that nearly qualifies but does not - the "
        "near miss is where the boundary becomes visible."
    ),
    FramingStrategy.PROCEDURAL_WALKTHROUGH: (
        "Procedurally, in the order you would actually do it: identify the material, identify "
        "the rule engaged, check whether an exception applies, then decide what to file and when."
    ),
    FramingStrategy.MISCONCEPTION_CORRECTION: (
        "The common mistake is worth naming first, because most people arrive holding it: the "
        "rule is routinely stated far more broadly than it actually operates."
    ),
}

_FRAMING_BRIDGE: dict[FramingStrategy, str] = {
    FramingStrategy.FIRST_PRINCIPLES: "Derived that way, what the lesson fixes on is this",
    FramingStrategy.ANALOGY: "Mapping the analogy back onto the material",
    FramingStrategy.WORKED_EXAMPLE: "What the worked example turns on",
    FramingStrategy.CONTRAST_NEAR_MISS: "What separates the two",
    FramingStrategy.PROCEDURAL_WALKTHROUGH: "The step that decides it",
    FramingStrategy.MISCONCEPTION_CORRECTION: "What the lesson actually says",
}


class FakeAnswerGenerator:
    """Deterministic. Same request in, same explanation out, every time."""

    name = "fake"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.grounding is Grounding.GENERAL_KNOWLEDGE:
            text = self._general(request)
        elif request.budget_exhausted:
            text = self._nothing_to_transform(request)
        else:
            text = self._grounded(request)

        return GenerationResult(
            explanation=text,
            section_id=request.section.section_id if request.section else None,
            concept_tag=request.concept.concept_tag if request.concept else None,
            cross_lesson_refs=self._refs(request),
            framing_used=request.framing,
        )

    # ------------------------------------------------------------------------ renderers

    def _grounded(self, request: GenerationRequest) -> str:
        subject = request.concept.name if request.concept else (request.section.title if request.section else "this point")
        parts = [
            f"{subject} - {_PROFILE_OPENER[request.profile]}",
            _FRAMING_LEAD[request.framing],
        ]
        if request.quotable_spans:
            bridge = _FRAMING_BRIDGE[request.framing]
            quoted = "\n".join(f"- {span}" for span in request.quotable_spans)
            parts.append(f"{bridge}:\n{quoted}")
        parts.append(_PROFILE_CLOSER[request.profile])
        return "\n\n".join(parts)

    def _nothing_to_transform(self, request: GenerationRequest) -> str:
        """The budget is empty: say so, do not fall back to reciting the section body."""
        subject = request.section.title if request.section else "this point"
        return (
            f"The linked lesson touches on {subject} but does not set it out in enough depth for "
            "me to explain it a different way from the lesson's own material.\n\n"
            f"{_PROFILE_OPENER[request.profile]} I can take the underlying idea from general "
            "knowledge instead, or we can move to a free-form session where I am not held to "
            "this lesson."
        )

    def _general(self, request: GenerationRequest) -> str:
        # Never echo a question that was flagged as answer-seeking, and never echo an attempt
        # to inject instructions: the learner's own words do not get a return trip.
        topic = (
            "this topic"
            if request.suppress_question_echo
            else (" ".join(unique_tokens(request.question)[:6]) or "this topic")
        )
        lead = _FRAMING_LEAD[request.framing]
        return (
            "This is not covered by the linked lesson, so I am answering from general knowledge "
            "rather than lesson content.\n\n"
            f"On {topic}: {lead}\n\n"
            f"{_PROFILE_CLOSER[request.profile]}\n\n"
            "If you want to take this further than the lesson allows, we can move into a "
            "free-form session."
        )

    def _refs(self, request: GenerationRequest) -> tuple[CrossLessonRef, ...]:
        """Offer at most one cross-lesson reference, chosen deterministically from candidates.

        Only ever from the candidate list the service supplied, which is itself drawn from the
        loaded course structure. The service verifies the result regardless.
        """
        if not request.candidate_cross_lesson_refs or request.grounding is not Grounding.LESSON:
            return ()
        seed = int(stable_hash(request.framing.value)[:4], 16)
        chosen = request.candidate_cross_lesson_refs[seed % len(request.candidate_cross_lesson_refs)]
        return (CrossLessonRef(lesson_id=chosen.lesson_id, title=chosen.title, reason="related material in this course"),)
