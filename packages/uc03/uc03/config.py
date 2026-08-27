"""Server-side configuration. None of these values are client-settable."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UC03_", extra="ignore")

    # --- Latency (company requirement 12/13) ---
    thinking_after_ms: int = 1_500
    timeout_ms: int = 10_000
    p95_target_ms: int = 3_000

    # --- Input validation (requirement 14) ---
    max_question_chars: int = 2_000
    min_question_chars: int = 3

    # --- Authority integrity (requirement 5) ---
    no_authority_message: str = (
        "We could not confirm a verified legal authority for this question, so "
        "none is shown. We do not provide unverified citations. Please verify "
        "the position yourself using Westlaw or BAILII before relying on it."
    )
    verification_routes: tuple[str, ...] = ("Westlaw", "BAILII")

    # --- Out of scope (requirement 6) ---
    out_of_scope_message: str = (
        "This assistant is here to help you learn legal concepts, processes and "
        "definitions. That question falls outside legal learning, so I can't "
        "answer it here — but ask me about a legal concept and I'll explain it."
    )

    # --- Failure surface ---
    generation_error_message: str = (
        "Something went wrong while preparing your answer. No partial answer is "
        "shown, because an incomplete legal explanation can mislead. Please try "
        "again."
    )
    timeout_message: str = (
        "This one is taking longer than expected, so we stopped rather than show "
        "an incomplete answer. Please try again."
    )

    framings_exhausted_message: str = (
        "I have now explained this concept every distinct way I have. Repeating "
        "one of them reworded would not help you. We can go deeper on the same "
        "concept, or move on to a related one - which would you prefer?"
    )
    paraphrase_rejected_message: str = (
        "The new explanation came back too close to one you have already seen, so "
        "it was not shown. Please try again."
    )

    #: Content-word overlap at or above which a new explanation counts as a
    #: reworded repeat of one already shown for the same concept.
    paraphrase_threshold: float = 0.60

    citation_guard_enabled: bool = True

    dev_credentials: dict[str, str] = Field(
        default_factory=lambda: {"dev-token-alice": "user-alice", "dev-token-bob": "user-bob"}
    )


settings = Settings()
