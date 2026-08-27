"""Educational content for guard redirects, built in-domain.

When the outcome-prediction / litigation-strategy guard fires, the redirect is
composed HERE - deterministically, from a versioned in-code library - and never
by the generator. Two reasons:

1. The redirect is not configurable and must not vary. A model could drift into
   the very prediction the guard exists to prevent.
2. The redirect must be substantive - an explanation of the legal test the court
   would apply, with its elements and how a court approaches them - not a refusal.

Content is calibrated by explanation profile. The legal content below is
illustrative teaching material for England & Wales and is an assumption
(docs/assumptions.md row A-08): the company must supply the authoritative content
library before release.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .enums import ExplanationProfile, GuardClass

LEGAL_TEST_LIBRARY_VERSION: Final[str] = "legal-tests/2026-08-24"


@dataclass(frozen=True, slots=True)
class LegalTest:
    topic_tag: str
    name: str
    elements: tuple[str, ...]
    court_approach: str
    burden: str
    authorities: tuple[str, ...]
    doctrinal_note: str
    keywords: tuple[str, ...]


_TESTS: Final[tuple[LegalTest, ...]] = (
    LegalTest(
        topic_tag="duress",
        name="the defence of duress",
        elements=(
            "there was a threat of death or serious injury",
            "the threat was directed at the defendant or someone for whom the defendant was reasonably responsible",
            "the defendant genuinely believed the threat would be carried out immediately or almost immediately",
            "a sober person of reasonable firmness sharing the relevant characteristics would have acted the same way",
            "there was no evasive action reasonably open to the defendant",
            "the defendant did not voluntarily associate with those making the threat",
        ),
        court_approach=(
            "The court works through the elements in order and stops at the first one that is not made out. "
            "The first three limbs are assessed largely on what the defendant believed. The fourth is objective, "
            "measured against a person of reasonable firmness, and only limited characteristics are attributed "
            "to that person."
        ),
        burden=(
            "The defendant carries an evidential burden to raise the defence. Once raised, the prosecution must "
            "disprove it to the criminal standard."
        ),
        authorities=(
            "R v Graham [1982] 1 WLR 294",
            "R v Hasan [2005] UKHL 22",
            "R v Bowen [1997] 1 WLR 372",
        ),
        doctrinal_note=(
            "Hasan narrowed the defence considerably at the voluntary-association limb. The objective limb in "
            "Graham imports only characteristics affecting the gravity of the threat, not general susceptibility."
        ),
        keywords=("duress", "threat", "coerced", "coercion", "forced to"),
    ),
    LegalTest(
        topic_tag="self_defence",
        name="the test for self-defence and reasonable force",
        elements=(
            "the defendant honestly believed force was necessary in the circumstances as they believed them to be",
            "the degree of force used was reasonable in those believed circumstances",
            "any mistaken belief about the circumstances was honestly held",
        ),
        court_approach=(
            "The first limb is subjective: the court asks what the defendant actually believed, not what a careful "
            "person would have believed. The second limb is objective, but it is applied to the facts as the "
            "defendant believed them, and allowance is made for someone acting in the heat of the moment who "
            "cannot weigh to a nicety the exact measure of necessary action."
        ),
        burden=(
            "The defendant raises the issue. The prosecution must then disprove self-defence to the criminal standard."
        ),
        authorities=(
            "R v Palmer [1971] AC 814",
            "R v Owino [1996] 2 Cr App R 128",
            "Criminal Justice and Immigration Act 2008, s.76",
        ),
        doctrinal_note=(
            "Section 76 of the 2008 Act codifies the common law without altering it, and expressly preserves the "
            "Palmer allowance for a defendant acting under pressure."
        ),
        keywords=("self-defence", "self defence", "reasonable force", "defend himself", "defend herself"),
    ),
    LegalTest(
        topic_tag="dishonesty",
        name="the test for dishonesty",
        elements=(
            "what the defendant actually knew or believed as to the facts",
            "whether, given that state of knowledge or belief, the conduct was dishonest by the standards of "
            "ordinary decent people",
        ),
        court_approach=(
            "The court establishes the defendant's subjective state of knowledge first, as a question of fact, then "
            "applies a single objective standard to it. There is no separate requirement that the defendant "
            "appreciated that ordinary people would regard the conduct as dishonest."
        ),
        burden="The prosecution must prove dishonesty to the criminal standard as an element of the offence.",
        authorities=(
            "Ivey v Genting Casinos [2017] UKSC 67",
            "R v Barton and Booth [2020] EWCA Crim 575",
            "Theft Act 1968, s.2",
        ),
        doctrinal_note=(
            "Ivey removed the second, subjective limb of Ghosh. Barton and Booth confirmed that Ivey binds the "
            "criminal courts. Section 2 of the Theft Act still supplies negative statutory cases in which an "
            "appropriation is not dishonest."
        ),
        keywords=("dishonest", "dishonesty", "theft", "fraud", "false representation"),
    ),
    LegalTest(
        topic_tag="causation",
        name="the test for factual and legal causation",
        elements=(
            "factual causation: but for the conduct, would the result have occurred as and when it did",
            "legal causation: the conduct was an operating and substantial cause of the result",
            "no intervening act broke the chain of causation",
        ),
        court_approach=(
            "The court begins with the but-for question, which is a filter rather than the answer. It then asks "
            "whether the contribution was more than minimal and still operating at the relevant time. Intervening "
            "conduct breaks the chain only if it is free, deliberate and informed, or so independent and potent "
            "that the original conduct is no longer a substantial cause."
        ),
        burden="The prosecution must prove causation to the criminal standard.",
        authorities=(
            "R v White [1910] 2 KB 124",
            "R v Smith [1959] 2 QB 35",
            "R v Kennedy (No 2) [2007] UKHL 38",
        ),
        doctrinal_note=(
            "Kennedy (No 2) draws the line at the free, deliberate and informed act of a fully informed adult. "
            "Medical treatment cases such as Cheshire set a high threshold before treatment is treated as "
            "independent and potent."
        ),
        keywords=("causation", "caused", "chain of events", "but for", "intervening"),
    ),
    LegalTest(
        topic_tag="breach_of_duty",
        name="the test for breach of duty in negligence",
        elements=(
            "a duty of care was owed on an established category or on the recognised incremental criteria",
            "the standard of care expected of the reasonable person in the position the defendant occupied",
            "the conduct fell below that standard",
            "the breach caused damage of a foreseeable kind",
        ),
        court_approach=(
            "The court sets the standard objectively, adjusted for the role assumed rather than for the individual, "
            "and weighs the magnitude of the risk and the seriousness of potential harm against the cost and "
            "practicability of precautions and the social utility of the activity."
        ),
        burden="The claimant must prove each element on the balance of probabilities.",
        authorities=(
            "Bolton v Stone [1951] AC 850",
            "Bolam v Friern Hospital Management Committee [1957] 1 WLR 582",
            "Bolitho v City and Hackney HA [1998] AC 232",
        ),
        doctrinal_note=(
            "Bolam defers to a responsible body of professional opinion. Bolitho makes that deference conditional "
            "on the opinion withstanding logical analysis."
        ),
        keywords=("negligence", "duty of care", "breach of duty", "reasonable care", "standard of care"),
    ),
    LegalTest(
        topic_tag="general",
        name="the framework a court applies to a contested issue of this kind",
        elements=(
            "identify the rule or offence and break it into its constituent elements",
            "identify which party bears the burden on each element, and to what standard",
            "map the available material onto each element and note where an element is unsupported",
            "consider any statutory or common law defence, which has elements of its own",
        ),
        court_approach=(
            "A court reasons element by element. It does not weigh a case in the round first. It asks whether each "
            "required element is established on the applicable standard, and an element left unsupported is "
            "decisive however strong the surrounding material appears."
        ),
        burden="Which party bears the burden, and to what standard, is determined by the rule in question.",
        authorities=("Woolmington v DPP [1935] AC 462",),
        doctrinal_note=(
            "Woolmington states the golden thread: subject to statutory exceptions and the defence of insanity, "
            "the burden of proof rests on the prosecution throughout."
        ),
        keywords=(),
    ),
)

_BY_TAG: Final[dict[str, LegalTest]] = {t.topic_tag: t for t in _TESTS}


def all_topic_tags() -> tuple[str, ...]:
    return tuple(_BY_TAG)


def resolve_topic(
    question: str,
    practice_area: str | None = None,
    charges: tuple[str, ...] = (),
) -> LegalTest:
    """Deterministic topic resolution: question keywords first, then charges.

    Topic resolution reads the question in memory only. Only the resulting
    topic_tag is ever logged.
    """
    haystack = question.lower()
    for test in _TESTS:
        if any(k in haystack for k in test.keywords):
            return test
    charge_text = " ".join(charges).lower()
    for test in _TESTS:
        if any(k in charge_text for k in test.keywords):
            return test
    if practice_area and practice_area.lower().startswith("civil"):
        return _BY_TAG["breach_of_duty"]
    return _BY_TAG["general"]


def get_test(topic_tag: str) -> LegalTest:
    return _BY_TAG.get(topic_tag, _BY_TAG["general"])


_GUARD_OPENING: Final[dict[GuardClass, str]] = {
    GuardClass.OUTCOME_PREDICTION: (
        "Coaching does not predict how a matter will be decided, and no responsible answer to that question "
        "exists in the abstract. What is useful, and what a court actually does, is to work through the test "
        "that governs the issue so that you can weigh the material yourself."
    ),
    GuardClass.LITIGATION_STRATEGY: (
        "Choosing how to run a matter is a decision for the conduct of the case, not for coaching. What coaching "
        "can do is set out the legal framework any such decision would have to be reasoned against, so that the "
        "choice made is an informed one."
    ),
}


def build_redirect(
    guard_class: GuardClass,
    test: LegalTest,
    profile: ExplanationProfile,
    fact_lines: tuple[str, ...] = (),
) -> str:
    """Compose a substantive educational redirect. Never a bare refusal.

    `fact_lines` are pre-rendered lines for facts already verified against the
    loaded case file. Empty on the non-case-linked path.
    """
    parts: list[str] = [_GUARD_OPENING[guard_class]]
    parts.append("Here is " + test.name + ", and how a court approaches it.")

    if profile is ExplanationProfile.BASIC:
        parts.append("The court asks these questions, in order:")
        parts.extend(
            str(i) + ". " + _plain(element) + "."
            for i, element in enumerate(test.elements, 1)
        )
        parts.append("Who has to prove what: " + test.burden)
        parts.append(_first_two_sentences(test.court_approach))
    elif profile is ExplanationProfile.INTERMEDIATE:
        parts.append("The elements the court works through are:")
        parts.extend(str(i) + ". " + element + "." for i, element in enumerate(test.elements, 1))
        parts.append("How the court approaches it: " + test.court_approach)
        parts.append("Burden and standard: " + test.burden)
        parts.append("Key authorities: " + "; ".join(test.authorities) + ".")
    else:
        parts.append("The elements the court works through are:")
        parts.extend(str(i) + ". " + element + "." for i, element in enumerate(test.elements, 1))
        parts.append("How the court approaches it: " + test.court_approach)
        parts.append("Burden and standard: " + test.burden)
        parts.append("Authorities: " + "; ".join(test.authorities) + ".")
        parts.append("Doctrinal note: " + test.doctrinal_note)

    if fact_lines:
        parts.append(
            "Mapping the material in the case file onto those elements, as a structure for your own analysis "
            "and not as a conclusion about this matter:"
        )
        parts.extend(fact_lines)

    parts.append(
        "Working through each element against the material you hold, and identifying which element is least "
        "supported, is the reasoning a court would recognise. The conclusion is yours to reach."
    )
    return "\n\n".join(parts)


def _plain(element: str) -> str:
    """Basic-profile phrasing: drop the labelled prefix, keep one clause."""
    text = element.split(":")[-1].strip()
    return text[0].upper() + text[1:]


def _first_two_sentences(text: str) -> str:
    sentences = [s.strip() for s in text.split(". ") if s.strip()]
    return " ".join(s.rstrip(".") + "." for s in sentences[:2])
