"""Topic-description registry.

Descriptions are *looked up*, never generated. There is no model, no template
authoring at runtime beyond a single configured fallback string, and no
inference from question text (which UC-07 never reads).
"""

from __future__ import annotations

import json
from pathlib import Path

from uc07.domain.enums import DescriptionSource
from uc07.domain.errors import ConfigurationError


class TopicDescriptionRegistry:
    """Immutable topic_tag -> description lookup with a configured fallback."""

    def __init__(self, descriptions: dict[str, str], default_template: str) -> None:
        if "{topic_tag}" not in default_template:
            raise ConfigurationError(
                "topic description default_template must contain '{topic_tag}'"
            )
        for tag, text in descriptions.items():
            if not tag.strip() or not text.strip():
                raise ConfigurationError(
                    "topic description registry contains an empty tag or description"
                )
        self._descriptions = dict(descriptions)
        self._default_template = default_template

    @classmethod
    def from_path(cls, path: Path) -> "TopicDescriptionRegistry":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"topic description registry not found at '{path}'"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"topic description registry at '{path}' is not valid JSON"
            ) from exc
        if not isinstance(raw, dict):
            raise ConfigurationError("topic description registry must be a JSON object")
        descriptions = raw.get("descriptions")
        default_template = raw.get("default_template")
        if not isinstance(descriptions, dict) or not isinstance(default_template, str):
            raise ConfigurationError(
                "topic description registry needs 'descriptions' (object) and "
                "'default_template' (string)"
            )
        return cls(descriptions=descriptions, default_template=default_template)

    def describe(self, topic_tag: str) -> tuple[str, DescriptionSource]:
        """Return ``(description, source)`` for a topic tag consumed as supplied."""
        configured = self._descriptions.get(topic_tag)
        if configured is not None:
            return configured, DescriptionSource.REGISTRY
        return (
            self._default_template.format(topic_tag=topic_tag),
            DescriptionSource.REGISTRY_DEFAULT,
        )

    @property
    def known_tags(self) -> frozenset[str]:
        return frozenset(self._descriptions)
