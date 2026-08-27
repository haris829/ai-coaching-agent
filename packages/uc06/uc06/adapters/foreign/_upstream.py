"""A deliberately foreign fictional upstream: "Mattersphere".

Nothing here resembles the mock. Different field names, different nesting,
different value representations, different marker syntax, different error shape.
It exists so the swap can be demonstrated rather than asserted: the unmodified
service runs against this family in tests/test_integration_swap.py and produces
correct results.

This module is the fake wire format only. The mapping lives in the adapters.
"""

from __future__ import annotations

from typing import Any

MATTER_STANDARD = "MAT-2291/A"
MATTER_NO_AUTHORITIES = "MAT-2291/B"
MATTER_BLOCKED = "MAT-2291/C"
MATTER_OTHER_ORIGIN = "MAT-2291/D"
MATTER_GONE = "MAT-2291/E"
MATTER_GARBLED = "MAT-2291/F"
MATTER_EMPTY = "MAT-2291/G"
MATTER_SLOW = "MAT-2291/H"

#: The upstream's own marker syntax. The adapter translates it.
FOREIGN_MARKER_OPEN = "<<ref:"
FOREIGN_MARKER_CLOSE = ">>"


class MatterSphereError(RuntimeError):
    """The upstream's own exception type. Must never escape an adapter."""

    def __init__(self, status: str, body: str) -> None:
        super().__init__(f"{status}: {body}")
        self.status = status
        self.body = body


def fetch_matter(matter_ref: str) -> dict[str, Any]:
    if matter_ref == MATTER_GONE:
        raise MatterSphereError("UPSTREAM_503", "mattersphere node pool draining, retry later")
    if matter_ref == MATTER_SLOW:
        raise TimeoutError("mattersphere read exceeded the caller deadline")
    if matter_ref == MATTER_GARBLED:
        return {"envelope": {"schemaVersion": "9.9", "record": "not-an-object"}}
    if matter_ref == MATTER_EMPTY:
        return {
            "envelope": {
                "schemaVersion": "9.9",
                "record": {
                    "matterRef": matter_ref,
                    "provenance": {"producedBy": "casePrepAgent/v3", "sealed": True},
                    "practiceGroup": "CRIME",
                    "counts": [],
                    "particulars": [],
                    "exhibits": [],
                    "authorities": [],
                },
            }
        }

    particulars = [
        {
            "ref": "p.1",
            "narrative": (
                "The client states that two men approached him outside the depot on 14 March and said "
                "his brother would be hurt that night unless he opened the rear gate."
            ),
            "kind": "CLIENT_ACCOUNT",
        },
        {
            "ref": "p.2",
            "narrative": "Camera 4 at 23:41 records the rear gate opening from the inside.",
            "kind": "EXHIBIT_SUMMARY",
        },
        {
            "ref": "p.3",
            "narrative": "No contact was made with police in the six hours before the gate was opened.",
            "kind": "CHRONOLOGY",
        },
    ]
    authorities = [
        {"authRef": "a.1", "cite": "Theft Act 1968, s.8", "headnote": "Robbery."},
        {"authRef": "a.2", "cite": "R v Hasan [2005] UKHL 22", "headnote": "Duress and voluntary association."},
    ]
    return {
        "envelope": {
            "schemaVersion": "9.9",
            "record": {
                "matterRef": matter_ref,
                "provenance": {
                    "producedBy": "casePrepAgent/v3" if matter_ref != MATTER_OTHER_ORIGIN else "bulkImport/v1",
                    "sealed": True,
                },
                "practiceGroup": "CRIME",
                "counts": [
                    {"countRef": "c.1", "descriptor": "Robbery", "provision": "Theft Act 1968, s.8"},
                ],
                "particulars": particulars,
                "exhibits": [{"exhRef": "x.1", "descriptor": "Gate camera export", "particularRefs": ["p.2"]}],
                "authorities": [] if matter_ref == MATTER_NO_AUTHORITIES else authorities,
            },
        }
    }


def fetch_permission(actor: str, matter_ref: str) -> dict[str, Any]:
    if matter_ref == MATTER_GONE:
        raise MatterSphereError("UPSTREAM_503", "mattersphere permission service unavailable")
    if matter_ref == MATTER_SLOW:
        raise TimeoutError("mattersphere permission check exceeded the caller deadline")
    # Value representation differs from the platform: a string verdict, not a bool.
    verdict = "DENY" if matter_ref == MATTER_BLOCKED else "PERMIT"
    return {"envelope": {"decision": {"verdict": verdict, "matterRef": matter_ref, "actor": actor}}}


def fetch_learner(session_ref: str, actor: str) -> dict[str, Any]:
    if session_ref.startswith("ms-down"):
        raise MatterSphereError("UPSTREAM_503", "learner directory unavailable")
    if session_ref.startswith("ms-slow"):
        raise TimeoutError("mattersphere learner read exceeded the caller deadline")
    band = "band-seven-plus"
    if session_ref.startswith("ms-junior"):
        band = "band-three"
    if session_ref.startswith("ms-unknown-band"):
        band = "band-eleven"
    profile: dict[str, Any] = {
        "actor": actor,
        "eqfBand": band,
        "bandOrigin": "ASSESSED",
        "sessionMode": "CASE_LINKED",
        "specialism": "CRIME",
    }
    if session_ref.startswith("ms-no-specialism"):
        profile["specialism"] = None
    return {"envelope": {"profile": profile}}


def complete(prompt_bundle: dict[str, Any]) -> dict[str, Any]:
    """The upstream generator's own response shape."""
    refs = list(prompt_bundle.get("sourceRefs", []))[:2]
    body_parts = [
        "The governing framework is the defence of duress, and the material below is set against its elements.",
        "The court works through the elements in order and stops at the first that is not made out.",
    ]
    for ref in refs:
        body_parts.append(
            f"{FOREIGN_MARKER_OPEN}{ref}{FOREIGN_MARKER_CLOSE} bears on whether that element is supported, "
            "and on what further evidence a court would want before treating it as established."
        )
    body_parts.append(
        "Each element remains a question for the tribunal. This sets out how the framework operates on "
        "material of this kind."
    )
    return {
        "envelope": {
            "completion": {
                "body": "\n\n".join(body_parts),
                "citations": [{"sourceRef": ref} for ref in refs],
                "engine": "mattersphere-writer-2",
            }
        }
    }
