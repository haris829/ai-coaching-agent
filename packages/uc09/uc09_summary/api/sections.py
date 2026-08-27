"""Section descriptors for the structured response.

The four sections are described as data - key, title, orientation, status,
count and note - so that a frontend renders them without inventing an order,
a heading or, most importantly, an orientation. Whether a section records what
happened or suggests what might happen next is a property of the content, not
a styling choice available to a client.
"""

from __future__ import annotations

from uc09_summary.domain.enums import SourceStatus
from uc09_summary.domain.models import SummaryRecord

RETROSPECTIVE = "retrospective"
FORWARD_LOOKING = "forward_looking"

#: (key, title, orientation), in document order.
SECTION_DESCRIPTORS: tuple[tuple[str, str, str], ...] = (
    ("topics_covered", "Topics Covered", RETROSPECTIVE),
    ("key_concepts", "Key Concepts", RETROSPECTIVE),
    ("resources_referenced", "Resources Referenced", RETROSPECTIVE),
    ("next_steps", "Recommended Next Steps", FORWARD_LOOKING),
)


def describe_sections(record: SummaryRecord) -> list:
    """Return one descriptor per section, always all four, in fixed order."""
    from uc09_summary.api.schemas import SectionOut

    counts = {
        "topics_covered": len(record.topics_covered),
        "key_concepts": len(record.key_concepts),
        "resources_referenced": len(record.resources_referenced),
        "next_steps": len(record.next_steps),
    }
    return [
        SectionOut(
            key=key,
            title=title,
            orientation=orientation,
            status=record.source_status.get(key, SourceStatus.EMPTY),
            item_count=counts[key],
            note=record.section_notes.get(key),
        )
        for key, title, orientation in SECTION_DESCRIPTORS
    ]
