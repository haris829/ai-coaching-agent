"""Conditions acknowledgement (§1) and identity confirmation (§2).

The two gates a formal attempt cannot start without. The interesting assertions are the negative ones: an
incomplete acknowledgement is not "true enough", a name that differs by case does not match, and an unreachable
profile source does not become a confirmed identity.
"""

from __future__ import annotations

import pytest

from app.modules.formal_assessment.domain.conditions import (
    FORMAL_CONDITIONS,
    REQUIRED_CONDITION_CODES,
    is_acknowledgement_complete,
    missing_conditions,
    normalise_condition_codes,
)
from app.modules.formal_assessment.domain.enums import FormalAttemptState
from app.modules.formal_assessment.domain.errors import (
    ConditionsAcknowledgementIncompleteError,
    ConditionsNotAcknowledgedError,
    EmailNotConfirmedError,
    IdentityMismatchError,
    IdentityNotConfirmedError,
    LearnerProfileNotFoundError,
    LearnerProfileUnavailableError,
    QuizNotFormalAssessmentError,
    QuizNotFoundError,
)
from app.modules.formal_assessment.domain.identity import (
    IdentitySubmission,
    LearnerIdentityProfile,
    check_identity,
    normalise_email,
    normalise_name,
)
from tests.formal_assessment.conftest import ALL_CONDITION_CODES, FormalFlow
from tests.formal_assessment.fakes import DEFAULT_LEARNER, DEFAULT_NAME, DEFAULT_QUIZ

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# The conditions themselves (§1)
# ---------------------------------------------------------------------------


def test_all_seven_specified_conditions_exist():
    """§1 names seven conditions; each has a code, a title and a statement."""
    codes = {condition.code.value for condition in FORMAL_CONDITIONS}
    assert codes == {
        "IDENTITY_CONFIRMATION",
        "SINGLE_DEVICE",
        "NO_PAUSE_OR_RESUME",
        "AUTO_SUBMIT_ON_DISCONNECT",
        "NO_AI_COACHING",
        "HUMAN_REVIEW",
        "CERTIFICATE_APPROVAL",
    }
    for condition in FORMAL_CONDITIONS:
        assert condition.title and condition.statement


def test_the_required_set_is_derived_from_the_conditions():
    assert frozenset(c.code for c in FORMAL_CONDITIONS) == REQUIRED_CONDITION_CODES


def test_a_partial_acknowledgement_is_not_complete():
    """The backend derives ``conditions_acknowledged``; six of seven is false, not "close enough"."""
    partial = normalise_condition_codes(ALL_CONDITION_CODES[:-1])
    assert is_acknowledgement_complete(partial) is False
    assert len(missing_conditions(partial)) == 1


def test_unknown_codes_cannot_stand_in_for_required_ones():
    codes = normalise_condition_codes(["NOT_A_CONDITION", "ANOTHER_MADE_UP_ONE"])
    assert codes == ()
    assert is_acknowledgement_complete(codes) is False


def test_codes_are_case_and_whitespace_tolerant():
    codes = normalise_condition_codes([f"  {code.lower()} " for code in ALL_CONDITION_CODES])
    assert is_acknowledgement_complete(codes) is True


# ---------------------------------------------------------------------------
# The identity comparison (§2)
# ---------------------------------------------------------------------------


def _profile(**overrides) -> LearnerIdentityProfile:
    defaults = {
        "learner_id": DEFAULT_LEARNER,
        "full_name": DEFAULT_NAME,
        "email": "john.smith@example.com",
        "email_confirmed": True,
    }
    return LearnerIdentityProfile(**{**defaults, **overrides})


def test_an_exact_name_match_is_accepted():
    check = check_identity(
        submission=IdentitySubmission(full_name="John Smith"), profile=_profile()
    )
    assert check.confirmed is True
    assert check.mismatched_fields == ()


def test_a_lowercase_name_does_not_match():
    """§2: the match is exact. Case is significant, and no configuration relaxes it."""
    check = check_identity(
        submission=IdentitySubmission(full_name="john smith"), profile=_profile()
    )
    assert check.confirmed is False
    assert check.mismatch_codes == ("FULL_NAME",)


def test_surrounding_and_repeated_whitespace_is_normalised():
    """The project already strips whitespace on every request string; this extends it to internal runs."""
    check = check_identity(
        submission=IdentitySubmission(full_name="  John   Smith  "), profile=_profile()
    )
    assert check.confirmed is True


def test_no_fuzzy_matching_is_applied():
    for entered in ("Jon Smith", "John Smyth", "J Smith", "Smith John", "JohnSmith"):
        check = check_identity(
            submission=IdentitySubmission(full_name=entered), profile=_profile()
        )
        assert check.confirmed is False, entered


def test_a_profile_with_no_name_can_never_be_matched():
    """Two empty strings are equal, and that must not read as a confirmed identity."""
    check = check_identity(submission=IdentitySubmission(full_name=""), profile=_profile(full_name=""))
    assert check.confirmed is False
    assert "FULL_NAME" in check.mismatch_codes


def test_an_unconfirmed_account_email_blocks_confirmation_even_with_a_matching_name():
    check = check_identity(
        submission=IdentitySubmission(full_name=DEFAULT_NAME),
        profile=_profile(email_confirmed=False),
    )
    assert check.confirmed is False
    assert check.mismatched_fields == ()
    assert check.email_confirmed is False


def test_a_supplied_email_must_match_but_is_case_insensitive():
    matching = check_identity(
        submission=IdentitySubmission(full_name=DEFAULT_NAME, email="John.Smith@Example.com"),
        profile=_profile(),
    )
    assert matching.confirmed is True

    wrong = check_identity(
        submission=IdentitySubmission(full_name=DEFAULT_NAME, email="someone.else@example.com"),
        profile=_profile(),
    )
    assert wrong.confirmed is False
    assert wrong.mismatch_codes == ("EMAIL",)


def test_both_mismatches_are_reported_together():
    check = check_identity(
        submission=IdentitySubmission(full_name="jane doe", email="jane@example.com"),
        profile=_profile(),
    )
    assert set(check.mismatch_codes) == {"FULL_NAME", "EMAIL"}


def test_the_verdict_never_carries_the_expected_values():
    """A confirmation endpoint must not become a way to read a learner's registered details."""
    check = check_identity(
        submission=IdentitySubmission(full_name="wrong", email="wrong@example.com"),
        profile=_profile(),
    )
    rendered = str(check.as_dict())
    assert DEFAULT_NAME not in rendered
    assert "john.smith@example.com" not in rendered


def test_normalisers_are_narrow():
    assert normalise_name(" A  B ") == "A B"
    assert normalise_name(None) == ""
    assert normalise_email(" A@B.COM ") == "a@b.com"


# ---------------------------------------------------------------------------
# Acknowledging through the service (§1)
# ---------------------------------------------------------------------------


async def test_acknowledging_creates_the_formal_attempt_record(flow: FormalFlow, audit):
    record = await flow.acknowledge()
    assert record.state is FormalAttemptState.CONDITIONS_ACKNOWLEDGED
    assert record.conditions_acknowledged is True
    assert record.conditions is not None
    assert record.conditions.conditions_version == "2026.1"
    assert "FORMAL_CONDITIONS_ACKNOWLEDGED" in audit.codes()


async def test_an_incomplete_acknowledgement_is_refused_and_writes_nothing(flow: FormalFlow, container):
    with pytest.raises(ConditionsAcknowledgementIncompleteError) as error:
        await container.services.conditions.acknowledge(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            acknowledged_codes=ALL_CONDITION_CODES[:3],
        )
    assert error.value.code == "CONDITIONS_ACKNOWLEDGEMENT_INCOMPLETE"
    assert error.value.status_code == 422
    assert await container.services.attempts.find_open(DEFAULT_LEARNER, DEFAULT_QUIZ) is None


async def test_acknowledging_twice_converges_on_one_record(flow: FormalFlow, container):
    first = await flow.acknowledge()
    second = await flow.acknowledge()
    assert first.formal_attempt_id == second.formal_attempt_id
    assert len(await container.repositories.formal_attempts.list_for_learner(DEFAULT_LEARNER)) == 1


async def test_a_non_formal_quiz_is_refused(container, policies):
    policies.publish("quiz-ordinary", formal=False)
    with pytest.raises(QuizNotFormalAssessmentError):
        await container.services.conditions.acknowledge(
            learner_id=DEFAULT_LEARNER,
            quiz_id="quiz-ordinary",
            acknowledged_codes=ALL_CONDITION_CODES,
        )


async def test_an_unknown_quiz_is_refused(container):
    with pytest.raises(QuizNotFoundError):
        await container.services.conditions.acknowledge(
            learner_id=DEFAULT_LEARNER,
            quiz_id="quiz-nope",
            acknowledged_codes=ALL_CONDITION_CODES,
        )


async def test_a_withdrawn_quiz_cannot_be_acknowledged(container, policies):
    policies.withdraw()
    with pytest.raises(QuizNotFoundError):
        await container.services.conditions.acknowledge(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            acknowledged_codes=ALL_CONDITION_CODES,
        )


async def test_an_acknowledgement_of_a_superseded_version_does_not_satisfy_the_gate(
    flow: FormalFlow, container
):
    """§1: the acknowledgement is versioned, so re-issuing the conditions invalidates the old ones."""
    record = await flow.acknowledge()
    container.services.conditions._conditions_version = "2026.2"  # the deployment re-issued the text
    with pytest.raises(ConditionsNotAcknowledgedError) as error:
        container.services.conditions.require_acknowledged(record)
    assert error.value.context["acknowledged_conditions_version"] == "2026.1"
    assert error.value.context["required_conditions_version"] == "2026.2"


async def test_the_conditions_description_reports_the_policy(container):
    payload = await container.services.conditions.describe(DEFAULT_QUIZ)
    assert payload["is_formal_assessment"] is True
    assert payload["requires_assessor_approval"] is True
    assert len(payload["conditions"]) == 7


# ---------------------------------------------------------------------------
# Confirming identity through the service (§2)
# ---------------------------------------------------------------------------


async def test_confirming_identity_moves_the_record_on(flow: FormalFlow, audit):
    await flow.acknowledge()
    record = await flow.confirm_identity()
    assert record.state is FormalAttemptState.IDENTITY_CONFIRMED
    assert record.identity is not None
    assert record.identity.email_confirmed is True
    assert "IDENTITY_CONFIRMED" in audit.codes()


async def test_identity_cannot_be_confirmed_before_the_conditions(container):
    with pytest.raises(IdentityNotConfirmedError):
        await container.services.identity.confirm(
            learner_id=DEFAULT_LEARNER,
            quiz_id=DEFAULT_QUIZ,
            submission=IdentitySubmission(full_name=DEFAULT_NAME),
        )


async def test_a_mismatched_name_is_refused_and_counted(flow: FormalFlow, audit):
    await flow.acknowledge()
    with pytest.raises(IdentityMismatchError) as error:
        await flow.confirm_identity(name="john smith")
    assert error.value.code == "IDENTITY_MISMATCH"
    assert error.value.status_code == 422
    assert error.value.context["mismatched_fields"] == ["FULL_NAME"]

    record = await flow.record()
    assert record.state is FormalAttemptState.CONDITIONS_ACKNOWLEDGED, "a typo is not a state change"
    assert record.pending_identity_rejections == 1
    assert "IDENTITY_REJECTED" in audit.codes()


async def test_rejections_before_a_success_become_an_anomaly_for_the_assessor(flow: FormalFlow):
    await flow.acknowledge()
    for _ in range(2):
        with pytest.raises(IdentityMismatchError):
            await flow.confirm_identity(name="Wrong Name")
    record = await flow.confirm_identity()
    assert record.identity is not None
    assert record.identity.rejected_attempts == 2
    assert [item.code.value for item in record.anomalies] == ["IDENTITY_CONFIRMATION_RETRIED"]


async def test_an_unconfirmed_email_refuses_the_start_gate(flow: FormalFlow, profiles, audit):
    profiles.unconfirm_email()
    await flow.acknowledge()
    with pytest.raises(EmailNotConfirmedError) as error:
        await flow.confirm_identity()
    assert error.value.code == "EMAIL_NOT_CONFIRMED"
    assert error.value.status_code == 409
    assert "EMAIL_NOT_CONFIRMED" in [
        fields.get("reason") for fields in audit.fields_for("IDENTITY_REJECTED")
    ]


async def test_an_unknown_learner_is_refused(flow: FormalFlow, profiles):
    profiles.profiles.clear()
    await flow.acknowledge()
    with pytest.raises(LearnerProfileNotFoundError):
        await flow.confirm_identity()


async def test_an_unreachable_profile_source_never_confirms(flow: FormalFlow, profiles):
    """"We could not check the learner's name" must not become "the name matched"."""
    await flow.acknowledge()
    profiles.break_provider()
    with pytest.raises(LearnerProfileUnavailableError) as error:
        await flow.confirm_identity()
    assert error.value.status_code == 503
    assert error.value.retryable is True

    record = await flow.record()
    assert record.identity_confirmed is False


async def test_no_personal_data_reaches_the_audit_trail(flow: FormalFlow, audit, profiles):
    await flow.acknowledge()
    await flow.confirm_identity(email="john.smith@example.com")
    rendered = str(audit.events)
    assert DEFAULT_NAME not in rendered
    assert "john.smith@example.com" not in rendered


async def test_the_identity_record_holds_no_name_or_email(flow: FormalFlow):
    await flow.acknowledge()
    record = await flow.confirm_identity(email="john.smith@example.com")
    rendered = str(record.as_dict())
    assert DEFAULT_NAME not in rendered
    assert "john.smith@example.com" not in rendered
