"""Question text utilities shared by the core and the rule-based adapters.

Lives outside `adapters/` so `uc03.service` can derive a concept key without
importing a concrete adapter.
"""

from __future__ import annotations

import re

_LEADING_PHRASES: tuple[str, ...] = (
    "can you please explain",
    "can you explain",
    "could you explain",
    "please explain",
    "tell me about",
    "what is meant by",
    "what is the definition of",
    "what does it mean by",
    "what does",
    "what is the",
    "what is a",
    "what is an",
    "what is",
    "what are the",
    "what are",
    "how do i",
    "how does one",
    "how do you",
    "how to",
    "explain",
    "define",
    "why is",
    "why does",
    "why do",
)


def normalise_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def extract_subject(question: str) -> str:
    """Reduce a question to its subject phrase."""
    text = normalise_question(question).rstrip("?.! ")
    for phrase in _LEADING_PHRASES:
        if text.startswith(phrase):
            text = text[len(phrase) :].strip()
            break
    text = re.sub(r"\bmean(s|ing)?$", "", text).strip()
    text = re.sub(r"^(the|a|an)\s+", "", text).strip()
    text = text.strip("'\"")
    return text or "this topic"
