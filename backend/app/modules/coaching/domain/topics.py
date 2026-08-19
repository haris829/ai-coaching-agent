"""Topic resolution.

The topic is what a coaching session is *about* (§14), what a knowledge gap is recorded against
(§21), and what a review queue is labelled with (§19). It comes from two places, so the precedence
is stated once, here, rather than three times in three files that could drift apart.

UC-06 first: it resolves topics against the live question bank when it builds a feedback report, so
its list is the better one. UC-03's delivered topics are the fallback that keeps coaching working
when a feedback record is thin.

A topic is not answer-bearing. "Reporting concerns" says what the question was about; it does not
say which option was right.
"""

from __future__ import annotations

from app.modules.coaching.integration.uc03 import DeliveredQuestion
from app.modules.coaching.integration.uc06 import QuestionFeedback


def resolve_topics(
    question: DeliveredQuestion | None, feedback: QuestionFeedback | None
) -> tuple[str, ...]:
    """UC-06's topics, then UC-03's, de-duplicated and order-preserving."""
    ordered: dict[str, None] = {}
    if feedback is not None:
        for topic in feedback.topics:
            if topic and topic.strip():
                ordered.setdefault(topic.strip(), None)
    if question is not None:
        for topic in question.topics:
            if topic and topic.strip():
                ordered.setdefault(topic.strip(), None)
    return tuple(ordered)


def primary_topic(
    question: DeliveredQuestion | None, feedback: QuestionFeedback | None
) -> str | None:
    """The single topic used for labelling and knowledge-gap tracking, or ``None``.

    ``None`` is returned rather than a placeholder like "Unknown": a knowledge-gap record with an
    invented topic in it is worse than one that admits the question was untagged (§21).
    """
    topics = resolve_topics(question, feedback)
    return topics[0] if topics else None
