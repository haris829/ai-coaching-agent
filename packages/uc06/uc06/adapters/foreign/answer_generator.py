"""ForeignAnswerGeneratorAdapter - Mattersphere's writer service.

Its response nests under envelope.completion, calls the citation list
"citations" with a "sourceRef" key, and uses its own marker syntax
(<<ref:p.1>>). Translating that to the platform's [[fact:p.1]] marker form is
this adapter's job, and no other file knows the foreign syntax exists.
"""

from __future__ import annotations

import re

from ...config import Settings
from ...domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from ...domain.models import GenerationRequest, GenerationResult
from . import _upstream

PORT_NAME = "answer_generator"


class ForeignAnswerGeneratorAdapter:
    """Implements AnswerGenerator."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def generate(self, request: GenerationRequest) -> GenerationResult:
        bundle = {
            # The upstream wants its own key names. They stop here.
            "instructionBlock": request.system_instructions,
            "userTurn": request.question_text,
            "sourceRefs": list(request.available_fact_ids),
            "sourceBodies": [{"ref": fid, "body": text} for fid, text in request.fact_digest],
            "register": request.profile,
            "deadlineMillis": request.timeout_ms,
        }
        try:
            raw = _upstream.complete(bundle)
        except _upstream.MatterSphereError as exc:
            raise ProviderUnavailable(PORT_NAME, "generation_service_unreachable") from exc
        except TimeoutError as exc:
            raise ProviderTimeout(PORT_NAME, "generation_deadline_exceeded") from exc

        completion = raw.get("envelope", {}).get("completion")
        if not isinstance(completion, dict) or not isinstance(completion.get("body"), str):
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_generation_payload")

        body = _translate_markers(completion["body"])
        citations = completion.get("citations") or []
        try:
            fact_ids = tuple(str(c["sourceRef"]) for c in citations)
        except (KeyError, TypeError) as exc:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_generation_citations") from exc

        return GenerationResult(
            content=body,
            fact_ids_referenced=fact_ids,
            supplied_disclaimer=None,
            # The engine name is an upstream detail. It is kept out of the
            # response body and out of logs; it lives only on the result object
            # for local debugging.
            model_id="foreign",
            prompt_version=request.prompt_version,
        )


_FOREIGN_MARKER = re.compile(
    re.escape(_upstream.FOREIGN_MARKER_OPEN) + r"([A-Za-z0-9_.\-]{1,64})" + re.escape(_upstream.FOREIGN_MARKER_CLOSE)
)


def _translate_markers(text: str) -> str:
    return _FOREIGN_MARKER.sub(lambda m: "[[fact:" + m.group(1) + "]]", text)
