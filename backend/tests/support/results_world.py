"""Alias for :mod:`tests.support.results`.

One harness, two names: the rule suites read better importing the question builders directly, and
the service suites read better importing the module as ``support``. Everything is defined once, in
``results``; nothing is re-implemented here.
"""

from __future__ import annotations

from tests.support.results import *  # noqa: F401,F403
from tests.support.results import (  # noqa: F401
    ALL_TYPES,
    ATTEMPT_ID,
    CONFIGURATION_VERSION_ID,
    COURSE_ID,
    COURSE_NAME,
    EXPLANATION,
    LEARNER_ID,
    OTHER_LEARNER_ID,
    PASS_MARK,
    QUIZ_ID,
    QUIZ_TITLE,
    STARTED_AT,
    SUBMITTED_AT,
    Built,
    ControllableCertificateService,
    ControllableCpdService,
    ControllableCpdSync,
    FakeAnswerKeys,
    FakeAttemptSource,
    FakeQuestionContent,
    ResultsWorld,
    answer_key,
    build_world,
    delivered,
    drag_to_order,
    elapsed_seconds,
    expected_time_taken,
    instant,
    multi_select,
    option,
    order_item,
    scenario,
    single_choice,
    submitted_attempt,
    true_false,
    world_fixture,
)
