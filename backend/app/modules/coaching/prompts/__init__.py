"""Coaching prompts and policy, kept out of the application logic (§24).

Nothing in this package reads a repository, a provider or a setting. It turns a
``SafeCoachingContext`` payload and a mode into strings. That separation is what lets the teaching
policy be reviewed, versioned and changed by someone who is not editing services.
"""

from app.modules.coaching.prompts.coaching_prompt import (
    COACH_NAME,
    PROMPT_VERSION,
    build_system_prompt,
    policy_reminder,
    render_context,
)

__all__ = [
    "COACH_NAME",
    "PROMPT_VERSION",
    "build_system_prompt",
    "policy_reminder",
    "render_context",
]
