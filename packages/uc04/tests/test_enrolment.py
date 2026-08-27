"""Enrolment is verified server-side, on every request, before any content is loaded."""

from __future__ import annotations

import pytest

from conftest import IN_LESSON_QUESTION, build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.domain.enums import Grounding, SourceStatus
from uc04.domain.errors import NotEnrolled


def test_enrolled_learner_is_answered_from_the_lesson(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION)
    assert response.grounding is Grounding.LESSON
    assert response.source_status["enrolment"] is SourceStatus.AVAILABLE
    assert response.explanation


def test_unenrolled_learner_is_refused_with_a_distinct_error(harness) -> None:
    with pytest.raises(NotEnrolled) as exc:
        harness.ask(IN_LESSON_QUESTION, user_id=fx.USER_NOT_ENROLLED)
    assert exc.value.course_id == fx.COURSE_EVIDENCE
    assert exc.value.reason == "no_active_enrolment"


def test_lapsed_enrolment_is_refused(harness) -> None:
    with pytest.raises(NotEnrolled) as exc:
        harness.ask(IN_LESSON_QUESTION, user_id=fx.USER_LAPSED)
    assert exc.value.reason == "lapsed"


def test_lesson_content_is_never_fetched_before_enrolment_succeeds(harness) -> None:
    with pytest.raises(NotEnrolled):
        harness.ask(IN_LESSON_QUESTION, user_id=fx.USER_NOT_ENROLLED)
    # The strongest available evidence: the content call was never made at all.
    assert harness.courses.lesson_calls == []
    assert harness.courses.enrolment_calls == [(fx.USER_NOT_ENROLLED, fx.COURSE_EVIDENCE)]


def test_enrolment_is_re_verified_on_every_request(harness) -> None:
    """A decision made on an earlier request is not authorisation for this one."""
    harness.ask(IN_LESSON_QUESTION)
    harness.ask(IN_LESSON_QUESTION)
    harness.ask(IN_LESSON_QUESTION)
    assert len(harness.courses.enrolment_calls) == 3


def test_follow_up_re_verifies_enrolment_too(harness) -> None:
    first = harness.ask(IN_LESSON_QUESTION)
    before = len(harness.courses.enrolment_calls)
    harness.explain_differently(first)
    assert len(harness.courses.enrolment_calls) == before + 1


def test_unverifiable_enrolment_fails_closed_but_still_helps(harness) -> None:
    """The enrolment service being down is not authorisation, and not silence either."""
    response = harness.ask(IN_LESSON_QUESTION, user_id=fx.USER_ENROLMENT_DOWN)
    assert response.source_status["enrolment"] is SourceStatus.UNAVAILABLE
    assert response.grounding is Grounding.GENERAL_KNOWLEDGE
    assert harness.courses.lesson_calls == [], "no content may load without verified enrolment"
    assert response.explanation
    assert "enrolment could not be verified" in (response.notice or "")


def test_a_second_learner_cannot_borrow_the_first_learners_enrolment() -> None:
    harness = build_harness()
    harness.ask(IN_LESSON_QUESTION, user_id=fx.USER_ENROLLED)
    with pytest.raises(NotEnrolled):
        harness.ask(IN_LESSON_QUESTION, user_id=fx.USER_NOT_ENROLLED)
