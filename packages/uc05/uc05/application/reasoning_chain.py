"""Reasoning-chain assembly.

Section 5.6: at the cap, the direct answer is delivered "plus a brief
explanation of the reasoning chain -- what each guiding question was probing
and how it connected to the answer", and "the reasoning chain is assembled from
the recorded dialogue, not regenerated from scratch. It must reflect what was
actually asked."

That constraint is why nothing in this module calls a generator.  Every string
here is either copied verbatim from an ``ExchangeRecord`` or composed from
copied fields by a fixed template.  If the recorded questions change, the chain
changes; if a generator later invents a different account of the dialogue, it
cannot get into the chain, because no generator is consulted.
"""

from __future__ import annotations

from ..domain.models import Dialogue, ReasoningChainStep

#: A-REASONING-TEMPLATE.  Position-dependent so the chain reads as a chain
#: rather than a list.
_FIRST = (
    "This opened the reasoning by testing {probing}. Where you took it set up "
    "the question that followed."
)
_MIDDLE = (
    "This narrowed the reasoning by testing {probing}, building on what your "
    "previous answer had established."
)
_LAST = (
    "This was the final step, testing {probing}. The explanation below picks up "
    "from exactly this point."
)
_ONLY = (
    "This was the single step taken, testing {probing}. The explanation below "
    "picks up from it."
)


def build_reasoning_chain(dialogue: Dialogue) -> list[ReasoningChainStep]:
    """Assemble the chain from the record.  No generation, no invention."""
    total = len(dialogue.exchanges)
    steps: list[ReasoningChainStep] = []

    for position, exchange in enumerate(dialogue.exchanges, start=1):
        if total == 1:
            template = _ONLY
        elif position == 1:
            template = _FIRST
        elif position == total:
            template = _LAST
        else:
            template = _MIDDLE

        steps.append(
            ReasoningChainStep(
                exchange_number=exchange.exchange_number,
                guiding_question=exchange.guiding_question,
                probing=exchange.probing_focus,
                learner_response=exchange.learner_response,
                connection_to_answer=template.format(probing=exchange.probing_focus),
            )
        )

    return steps
