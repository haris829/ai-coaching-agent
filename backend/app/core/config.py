"""Application configuration.

Read once at import time from the environment / `.env` so the rest of the code depends on a
typed settings object instead of reaching into ``os.environ``.

DATABASE PORTABILITY
--------------------
The company database has not been provisioned yet, so local development runs against SQLite.
The only thing that needs to change when the company database arrives is ``DATABASE_URL``;
every model is written against portable SQLAlchemy types. See ``docs/DATABASE.md`` for the
switch-over checklist.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Courses Quiz Agent API"
    api_prefix: str = "/api"
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # ---- Database -------------------------------------------------------
    # Local default is a file-backed SQLite database in the backend directory.
    # Swap for the company database URL (e.g. postgresql+psycopg://…) when it exists.
    database_url: str = Field(default=f"sqlite:///{(BACKEND_DIR / 'quiz_agent.db').as_posix()}")
    database_echo: bool = Field(default=False)

    # ---- HTTP -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    # Comma-separated Vite dev-server origins for the admin UI.
    #
    # Kept as a plain string because pydantic-settings JSON-decodes list-typed env vars before
    # any validator runs, which would reject `a,b`. `cors_origins` below does the splitting.
    cors_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )

    # ---- Admin guard ----------------------------------------------------
    # Seam for the platform's real authentication. When empty the token check is skipped so the
    # backend runs standalone in local development; identity still resolves from ``qc_users``.
    admin_api_token: str | None = Field(default=None)

    # ---- CSV import limits ----------------------------------------------
    csv_max_bytes: int = Field(default=5 * 1024 * 1024)
    csv_max_rows: int = Field(default=5_000)

    # ---- Attempt delivery (UC-03) ---------------------------------------
    #: Extra seconds a write is tolerated after an attempt's hard expiry. ``0`` matches the
    #: specification: no modification is accepted once time has elapsed. Raise it to absorb
    #: network latency for in-flight autosaves.
    submission_grace_seconds: int = Field(default=0)
    #: Advertised to clients: if their own clock differs from the server's by more than this,
    #: they must resync from the timing payload.
    clock_resync_threshold_seconds: int = Field(default=5)
    #: Autosave cadence the client is expected to use, advertised in responses.
    autosave_interval_seconds: int = Field(default=30)
    #: Maximum answers accepted in a single batch autosave request.
    max_batch_answers: int = Field(default=500)
    #: Emit error logs. Disabled under test so expected failures stay quiet.
    error_logging: bool = Field(default=True)

    # ---- AI coaching review mode (UC-07) --------------------------------
    #: Completed exchanges after which the learner is offered the choice between continuing
    #: Socratic coaching and a direct concept explanation. Configuration, not a magic number
    #: scattered through the coaching services.
    direct_explanation_threshold: int = Field(default=5, ge=1, le=50)
    #: Hard ceiling on exchanges within one coaching session. Not a teaching rule — a runaway
    #: guard, so a stuck client cannot drive unbounded model spend against one question.
    coaching_max_exchanges: int = Field(default=50, ge=1, le=500)
    #: How many trailing messages are replayed to the model as conversation context.
    coaching_history_window: int = Field(default=20, ge=2, le=200)
    #: Consecutive AI failures after which a session is parked as FAILED and needs an explicit
    #: retry. Below this, a failure leaves the session ACTIVE and untouched.
    coaching_max_consecutive_failures: int = Field(default=3, ge=1, le=10)
    #: Longest learner message accepted into a coaching session.
    coaching_max_message_chars: int = Field(default=4000, ge=100, le=100_000)
    #: In-request regenerations allowed when a coach reply breaks the coaching policy. Exhausting
    #: them fails the exchange; it never falls back to a canned response.
    coaching_policy_retries: int = Field(default=1, ge=0, le=3)

    #: Which AI provider the coaching adapter binds. Empty (the default) binds **nothing**: every
    #: coaching request then reports itself unavailable, which is the truth. It never degrades into
    #: invented teaching text.
    coaching_llm_provider: str = Field(default="")
    coaching_llm_model: str = Field(default="claude-sonnet-5")
    #: Provider credential. Read from the environment only — never committed, never logged, and
    #: never returned by any endpoint. Coaching stays unavailable while it is empty.
    coaching_llm_api_key: str | None = Field(default=None)
    coaching_llm_base_url: str = Field(default="https://api.anthropic.com")
    coaching_llm_timeout_seconds: float = Field(default=20.0, gt=0.0, le=300.0)
    coaching_llm_max_output_tokens: int = Field(default=700, ge=64, le=8192)
    #: Replies longer than this are treated as an invalid model response rather than forwarded.
    coaching_llm_max_output_chars: int = Field(default=4000, ge=200, le=100_000)

    # ---- UC-08 Retake Management ---------------------------------------
    #: Which immutable configuration version a retake locks. ``ACTIVE_AT_RETAKE`` is UC-03's rule
    #: for any new attempt and is the default, so a retake behaves exactly like any other attempt.
    #: ``CARRY_FORWARD_PREVIOUS`` pins it to the version the previous attempt ran under. Either
    #: way the choice is explicit and recorded on the retake; a version is never switched by
    #: accident.
    retake_configuration_policy: str = Field(default="ACTIVE_AT_RETAKE")

    #: Upper bound on a single administrator grant. Not a product rule — a guard against a
    #: mistyped grant handing a learner a thousand attempts.
    max_grant_additional_attempts: int = Field(default=10, ge=1, le=100)

    #: Shown to a learner with no attempts left, so the user-facing layer does not invent its own
    #: wording. Configurable because every deployment names its own contact.
    exhausted_contact_guidance: str = Field(
        default=(
            "You have used all of your attempts for this quiz. "
            "Contact your course administrator if you need an additional attempt."
        )
    )

    # ---- Local development convenience ---------------------------------
    #: Seed a brand new local database so the workflow is demonstrable immediately.
    #: Always off under ``ENVIRONMENT=test``.
    auto_seed: bool = Field(default=True)

    @field_validator("admin_api_token", "coaching_llm_api_key", mode="before")
    @classmethod
    def _blank_token_is_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @property
    def coaching_provider_configured(self) -> bool:
        """Whether an AI coaching provider is bound.

        Both halves are required. A provider name with no credential would build an adapter that
        fails on every call, which is worse than no adapter: coaching should say it is unavailable
        up front rather than after the learner has typed a question.
        """
        return bool(self.coaching_llm_provider.strip()) and bool(self.coaching_llm_api_key)

    @field_validator("retake_configuration_policy", mode="before")
    @classmethod
    def _normalise_retake_policy(cls, value: object) -> object:
        """Reject an unrecognised policy at start-up rather than at the first retake.

        A typo here would otherwise silently fall back to the default, and the deployment that
        meant to pin retakes to the previous version would advance them instead — a difference
        nobody would notice until a learner sat a retake under rules that had changed.
        """
        if isinstance(value, str):
            candidate = value.strip().upper()
            if candidate == "":
                return "ACTIVE_AT_RETAKE"
            if candidate in {"ACTIVE_AT_RETAKE", "CARRY_FORWARD_PREVIOUS"}:
                return candidate
            raise ValueError(
                "retake_configuration_policy must be ACTIVE_AT_RETAKE or CARRY_FORWARD_PREVIOUS."
            )
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_test(self) -> bool:
        return self.environment.lower() == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
