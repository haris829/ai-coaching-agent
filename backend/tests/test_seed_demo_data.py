"""The seeded world must be usable by a reviewer, not merely present.

WHY THIS EXISTS
---------------
A seed is easy to get *almost* right, and the failures are quiet. Two have already reached a live
deployment:

* the seed created no **assessor**, so UC-09's review-and-approve workflow — the thing that decides
  whether a formal pass is ever certificated — could not be reached at all;
* the seed enrolled learners with no **cohort**, so UC-10's cohort filter had nothing to match. The
  filter was working perfectly and returning the empty set, which to a reviewer clicking it is
  indistinguishable from broken.

Neither was a defect in any capability. Both made a capability undemonstrable, which for a review
deployment is the same cost. These checks assert the *shape* of the demo data rather than any
business rule, so they stay true whatever the rules do.
"""

from __future__ import annotations

import scripts.seed as seed_module


def test_the_seed_provides_every_role_the_system_distinguishes() -> None:
    """Four identities, three roles — and the assessor is the one that is easy to forget.

    An administrator credential is deliberately *refused* on the assessor endpoints, so without an
    assessor identity UC-09 cannot be exercised by anyone.
    """
    roles = [role.value for _email, _name, role, _token in seed_module.SEED_USERS]
    assert "admin" in roles
    assert "assessor" in roles, (
        "UC-09's review queue refuses an administrator by design, so a deployment with no assessor "
        "identity cannot demonstrate certificate gating at all"
    )
    assert roles.count("learner") >= 2, (
        "two learners are needed: one to sit, and one to prove cross-learner isolation and the "
        "cohort partition"
    )


def test_every_learner_is_assigned_a_cohort() -> None:
    """UC-10 filters analytics by cohort, and reads it from the enrolment row.

    A learner with no cohort matches no cohort filter. That is correct behaviour, and it means a
    seed that assigns none makes the filter look inert.
    """
    learner_emails = [
        email
        for email, _name, role, _token in seed_module.SEED_USERS
        if role.value == "learner"
    ]
    assert learner_emails, "no learners in the seed"
    for email in learner_emails:
        assert seed_module.SEED_COHORTS.get(email), f"{email} has no cohort assigned"


def test_the_learners_are_in_different_cohorts() -> None:
    """A partition, not a single bucket.

    With every learner in one cohort the filter returns everything and looks like it does nothing;
    the point of the demo data is that filtering by one cohort visibly narrows the population.
    """
    cohorts = {
        seed_module.SEED_COHORTS[email]
        for email, _name, role, _token in seed_module.SEED_USERS
        if role.value == "learner" and email in seed_module.SEED_COHORTS
    }
    assert len(cohorts) >= 2, f"all seeded learners share a cohort: {cohorts}"


def test_the_seed_offers_a_configured_quiz_and_a_formal_one() -> None:
    """Three quizzes, each for a reason — see the module docstring in scripts/seed.py.

    One deliberately unconfigured (so versioning is visible from the first save), one sittable
    immediately (so the learner journey needs no administrator step first), and one formal (the
    only way UC-09 is reachable).
    """
    configured = [
        (slug, configuration)
        for slug, _title, configuration in seed_module.QUIZZES
        if configuration is not None
    ]
    assert any(
        configuration is None for _slug, _title, configuration in seed_module.QUIZZES
    ), "no unconfigured quiz, so UC-01's first-save versioning cannot be demonstrated"
    assert configured, "no pre-configured quiz, so a learner cannot sit anything without an admin"
    assert any(
        configuration.get("isFormalAssessment") for _slug, configuration in configured
    ), "no formal assessment, so UC-09 is unreachable on a fresh deployment"


def test_the_published_default_tokens_are_recognised_as_unsafe() -> None:
    """The fallbacks are in this repository's history, so they must never reach a deployment.

    ``seed()`` refuses them outside development. This asserts the *list* stays in step with the
    identities, because a new identity whose fallback was not listed would slip through that guard.
    """
    listed = set(seed_module.UNSAFE_DEFAULT_TOKENS)
    for _email, _name, _role, token in seed_module.SEED_USERS:
        # Only the built-in fallbacks are checked: an environment-supplied token is by definition
        # not one of the published ones.
        if token.endswith("-token"):
            assert token in listed, (
                f"{token!r} is a built-in fallback but is not in UNSAFE_DEFAULT_TOKENS, so the "
                "production guard would not catch it"
            )
