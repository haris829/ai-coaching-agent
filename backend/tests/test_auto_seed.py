"""Seeding an empty database on start-up, and refusing to on a populated or deployed one.

``AUTO_SEED`` was a declared setting, documented in ``.env.example``, set to ``true``, and read by
nothing. Pointing the application at a fresh database therefore produced 401 on every request while
the configuration claimed seeding was on — which reads as a broken build rather than a missing
step, and is exactly what happens when a deployment is given a new PostgreSQL instance.

It now works, and these tests are mostly about when it must **not**. Seeding creates accounts whose
bearer tokens come from configuration and default to literally ``admin-token``; that is fine on a
laptop and a serious hole anywhere else, so every guard matters more than the feature does.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.main import _auto_seed_if_empty


class Recorder:
    """Stands in for ``scripts.seed.seed``, recording whether it was called."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {}


@pytest.fixture
def seeder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr("scripts.seed.seed", recorder)
    return recorder


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "auto_seed": True,
        "demo_identities": True,
        "environment": "development",
    }
    base.update(overrides)
    if str(base["environment"]).strip().lower().startswith("prod"):
        # `Settings` refuses to be built for a production environment with these unset — a guard
        # that predates this one and is stricter than it. Supplying them is what makes it possible
        # to test the auto-seed refusal *at all*, rather than being handed the earlier refusal.
        base.setdefault("admin_api_token", "a-real-admin-token")
        base.setdefault("system_api_token", "a-real-system-token")
    return Settings(**base)


# ---------------------------------------------------------------------------
# When it refuses — the part that matters
# ---------------------------------------------------------------------------


class TestWhenItRefuses:
    def test_it_does_nothing_when_auto_seed_is_off(self, seeder: Recorder) -> None:
        """The operator has to ask for it. Off is the default."""
        assert _auto_seed_if_empty(_settings(auto_seed=False)) is False
        assert seeder.calls == 0

    def test_it_does_nothing_when_demo_identities_are_off(self, seeder: Recorder) -> None:
        """The switch that governs whether demo accounts exist at all.

        Auto-seeding must not introduce them behind that switch's back — a deployment that turned
        demo identities off has said what it wants.
        """
        assert _auto_seed_if_empty(_settings(demo_identities=False)) is False
        assert seeder.calls == 0

    def test_it_refuses_in_production_even_when_asked(self, seeder: Recorder) -> None:
        """Belt and braces.

        The other two guards are environment variables, and environment variables get copied from
        one deployment to another. This one cannot be satisfied by accident.
        """
        assert _auto_seed_if_empty(_settings(environment="production")) is False
        assert seeder.calls == 0

    @pytest.mark.parametrize("name", ["production", "PRODUCTION", "prod", "Production"])
    def test_the_production_check_is_not_case_or_spelling_sensitive(
        self, seeder: Recorder, name: str
    ) -> None:
        assert _auto_seed_if_empty(_settings(environment=name)) is False
        assert seeder.calls == 0

    def test_it_does_nothing_when_the_database_already_has_users(
        self, seeder: Recorder, db: Any
    ) -> None:
        """It never adds to, alters or resets an existing set of users.

        This is the guard that holds even if every other one were wrong: a database with accounts
        in it is left completely alone.
        """
        from app.modules.identity.models import User
        from app.modules.identity.principal import Role

        db.add(
            User(
                email="real.person@example.com",
                display_name="A Real Account",
                role=Role.ADMIN.value,
                api_token="a-real-token",
            )
        )
        db.commit()

        assert _auto_seed_if_empty(_settings()) is False
        assert seeder.calls == 0
        assert db.query(User).count() == 1


# ---------------------------------------------------------------------------
# When it acts
# ---------------------------------------------------------------------------


class TestWhenItActs:
    def test_it_seeds_an_empty_database(self, seeder: Recorder) -> None:
        assert _auto_seed_if_empty(_settings()) is True
        assert seeder.calls == 1

    def test_a_seeding_failure_does_not_stop_the_application_starting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A seeding problem must leave the operator with an API, not a stack trace.

        They can then run ``python -m scripts.seed`` and read the real error. A crash during
        ``create_app`` gives them neither.
        """

        def explode(**kwargs: Any) -> None:
            raise RuntimeError("the database went away")

        monkeypatch.setattr("scripts.seed.seed", explode)

        assert _auto_seed_if_empty(_settings()) is False
