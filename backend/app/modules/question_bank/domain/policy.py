"""Business rules a hosting deployment may reasonably want to tune.

UC-02 lists "Explanation" and "Topics" among the required fields of all five question types
(§9–§13), so both are enforced by default. They are surfaced here rather than hard-coded in
the validator so the rule can be relaxed at merge time in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QuestionPolicy:
    #: Every question must carry a non-empty explanation.
    require_explanation: bool = True
    #: Every question must be tagged with at least one topic.
    require_at_least_one_topic: bool = True
    #: Unknown topic names referenced by a question are created on demand.
    auto_create_topics: bool = True
    #: Reject a new question whose content duplicates an existing non-retired question.
    reject_duplicate_content: bool = True
    max_topics_per_question: int = 20
    max_topic_name_length: int = 80
    max_option_label_length: int = 32


question_policy = QuestionPolicy()

#: Option labels appear inside the pipe/colon-delimited CSV option syntax, so the character
#: set is restricted to keep that format unambiguous and round-trippable.
OPTION_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
