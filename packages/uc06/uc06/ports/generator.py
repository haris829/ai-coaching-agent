"""AnswerGenerator - produces the educational explanation text.

The generator is untrusted in three specific ways, all handled by the caller:

* It is never asked to append a disclaimer, and its output is never scanned for
  one. Generated content and the disclaimer are separate fields joined only at
  the boundary. Anything disclaimer-shaped it emits is captured in
  GenerationResult.supplied_disclaimer for drift detection and discarded.
* Its claimed fact references are verified against the loaded case file. An
  unresolvable reference is fabricated evidence about a live matter and is
  treated as ProviderInvalidResponse.
* Its output is scanned for outcome-prediction language before emission.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import GenerationRequest, GenerationResult


@runtime_checkable
class AnswerGenerator(Protocol):
    """Failure contract: ProviderUnavailable, ProviderTimeout,
    ProviderInvalidResponse only.

    An implementation must honour request.timeout_ms and must not read prompts,
    system instructions or guardrails from anywhere but the GenerationRequest it
    is handed: prompts live server-side in a versioned registry and the learner
    cannot supply, append to or override one.
    """

    def generate(self, request: GenerationRequest) -> GenerationResult:
        ...
