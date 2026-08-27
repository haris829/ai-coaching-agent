"""File-backed adapter family.

Added after the fact, to demonstrate the integration swap rule on a real new
adapter rather than only asserting it. See docs/INTEGRATION.md.
"""

from .json_session_mode import JsonFileSessionModeRepository  # noqa: F401
