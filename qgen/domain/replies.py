"""Reading what a model sent back.

Two things every reply needs doing to it, and they were written twice before this module existed
- once for questions and once for answers. Two copies of a JSON extractor is two chances to fix a
parsing bug in one of them.
"""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def collapse(text: object, limit: int | None = None) -> str:
    """``text`` with its whitespace flattened to single spaces, optionally truncated.

    Used on everything that arrives from a model or a caller. A stem that differs from another
    only by a line break is the same stem, and comparing them without this makes a duplicate
    look new.
    """
    flattened = " ".join(str(text or "").split())
    return flattened[:limit] if limit is not None else flattened


def json_object(text: str) -> dict[str, Any] | None:
    """The JSON object in a model's reply, or ``None`` if there is not one.

    Tolerates a fenced code block around it, and prose either side of it, because models add both
    however firmly they are asked not to. It does not tolerate anything else: a reply that is not
    JSON returns ``None`` and the caller decides what that means. For a question that means
    refusing it; for an answer it means using the prose and saying the citations are unknown.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate[candidate.index("{") :] if "{" in candidate else candidate

    match = _JSON_BLOCK.search(candidate)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
