"""ADAPTER TEMPLATE - copy this file, fill in the TODOs, delete the rest.

You should not need to read any other file in this repository to finish this.

    1. Copy this file to uc06/adapters/real/<yoursystem>_<port>.py
    2. Keep the method signatures exactly as they are. They are the port.
    3. Fill in the four TODO blocks: endpoint, auth, payload mapping, errors.
    4. Add ONE line to PROVIDER_REGISTRY in uc06/composition.py:
           "<yourname>": "uc06.adapters.real.<yourfile>:<YourClass>",
    5. Set the matching environment variable (see docs/INTEGRATION.md).
    6. Run the conformance suite for the port. No new test needs writing.

THE FOUR RULES THIS FILE EXISTS TO ENFORCE

  * This adapter is the ONLY place your upstream's payload shape is known. No
    upstream field name, nesting, error string or provider name may escape it.
    Everything returned must be a uc06.domain.models type.
  * It NEVER invents data. A missing value maps to the documented default with
    its source field marked accordingly - never to a plausible-looking guess.
    A value that maps to no member of a platform enum is
    ProviderInvalidResponse, not a rounded-down neighbour.
  * Authorisation stays server-side, inside this adapter. Do not accept an
    access decision, a user identity or a role from a request body.
  * If the real payload cannot be mapped to the platform contract, that is a
    contract conversation, not an adapter workaround. Raise it. Do not bend the
    domain model to fit an upstream quirk.

FAILURE CONTRACT - raise these three and nothing else:

    ProviderUnavailable      unreachable, refused, 5xx, connection error
    ProviderTimeout          exceeded the caller's budget
    ProviderInvalidResponse  answered, but unmappable or unusable

Never let an upstream exception (httpx.HTTPError, KeyError, JSONDecodeError,
your SDK's error type) propagate: it carries payload text, and payload text
carries confidential case content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ...config import Settings
from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ...domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from ...domain.models import (
    CASE_PREP_AGENT_ORIGIN,
    AccessRecord,
    CaseFact,
    CaseFile,
    Charge,
    EvidenceItem,
    LearnerContext,
    LegislationNote,
)

PORT_NAME = "case_file_provider"  # TODO: the port key this adapter implements.


class TemplateCaseFileAdapter:
    """Implements CaseFileProvider. READ ONLY.

    Do not add a create, update, delete, patch or write method. The architecture
    test asserts that no adapter for this port has one, and it will fail the
    build if you do.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        # TODO 1 - ENDPOINT
        # Read the base URL from Settings (add the key to uc06/config.py's
        # Settings and ENV_KEYS if your system needs a new one). Never hard-code
        # a URL, a key or a timeout here.
        self._base_url = "TODO://replace-me"
        self._timeout_ms = settings.generation_timeout_ms

        # TODO 2 - AUTH
        # Build the credential ONCE, here, from configuration or from your
        # platform's service-identity mechanism. Never read it from a request.
        # Never log it. Never return it.
        self._auth_headers: Mapping[str, str] = {}

    # -- port ---------------------------------------------------------------
    def verify_read_access(self, user_id: str, case_file_id: str) -> AccessRecord:
        """Authoritative read-access decision, taken upstream, on every request.

        Do not cache. Do not accept a client-asserted decision. Authorisation may
        be revoked between two questions in the same session.
        """
        try:
            raw = self._get(f"/TODO-access-path/{case_file_id}", params={"user": user_id})
        except TimeoutError as exc:  # TODO: your client's timeout type
            raise ProviderTimeout(PORT_NAME, "access_check_timeout") from exc
        except Exception as exc:  # TODO: narrow to your client's transport errors
            raise ProviderUnavailable(PORT_NAME, "access_service_unreachable") from exc

        # TODO 3 - PAYLOAD MAPPING
        # Map the upstream decision onto AccessRecord. A response your mapping
        # does not understand is ProviderInvalidResponse - never a default of
        # "granted".
        granted = raw.get("TODO_permission_field")
        if not isinstance(granted, bool):
            raise ProviderInvalidResponse(PORT_NAME, "access_decision_not_boolean")
        return AccessRecord(
            user_id=user_id,
            case_file_id=case_file_id,
            granted=granted,
            checked_at=datetime.now(timezone.utc),
            reason_code="ok" if granted else "not_on_matter",
        )

    def get_case_file(self, case_file_id: str) -> CaseFile:
        try:
            raw = self._get(f"/TODO-case-path/{case_file_id}")
        except TimeoutError as exc:
            raise ProviderTimeout(PORT_NAME, "case_read_timeout") from exc
        except Exception as exc:
            raise ProviderUnavailable(PORT_NAME, "case_service_unreachable") from exc

        # TODO 3 (continued) - PAYLOAD MAPPING
        # Every fact MUST carry a stable identifier. If your upstream has no
        # stable per-fact identifier, stop: that is a contract conversation, not
        # a place to synthesise one. Explanations are verified against these
        # identifiers, and a synthesised id makes that verification meaningless.
        try:
            facts = tuple(
                CaseFact(
                    fact_id=str(item["TODO_fact_id"]),
                    text=str(item["TODO_fact_text"]),
                    category=str(item.get("TODO_fact_category", "general")),
                )
                for item in raw.get("TODO_facts", [])
            )
            charges = tuple(
                Charge(str(c["TODO_charge_id"]), str(c["TODO_charge_label"]), c.get("TODO_statute"))
                for c in raw.get("TODO_charges", [])
            )
            evidence = tuple(
                EvidenceItem(str(e["TODO_evidence_id"]), str(e["TODO_evidence_label"]))
                for e in raw.get("TODO_evidence", [])
            )
            notes = tuple(
                LegislationNote(str(n["TODO_note_id"]), str(n["TODO_citation"]), str(n["TODO_summary"]))
                for n in raw.get("TODO_legislation", [])
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Note what is NOT in the message: the payload, the field values, the
            # upstream error text.
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_case_payload") from exc

        # TODO: how does your system express "this came from the Case Prep
        # Agent"? Check the assumptions register (row A-05) against the real
        # system BEFORE writing this line. Do not map an unrelated field onto it.
        origin = CASE_PREP_AGENT_ORIGIN if raw.get("TODO_origin") == "TODO" else "unknown"

        return CaseFile(
            case_file_id=case_file_id,
            origin_system=origin,
            practice_area=str(raw.get("TODO_practice_area", "unknown")),
            charges=charges,
            facts=facts,
            evidence=evidence,
            legislation_notes=notes,
            # `empty` and `unavailable` are DIFFERENT states and must never be
            # conflated: empty means the upstream answered and held nothing.
            source_status=(
                SourceStatus.EMPTY
                if not facts and not charges
                else SourceStatus.PARTIAL
                if not notes
                else SourceStatus.AVAILABLE
            ),
        )

    # -- transport ----------------------------------------------------------
    def _get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        # TODO 4 - TRANSPORT AND ERROR TRANSLATION
        # Issue the request with self._timeout_ms and self._auth_headers, and
        # translate transport failures into the three contract exceptions above.
        # Do not retry silently on a non-idempotent call. Do not log the body.
        raise ProviderUnavailable(PORT_NAME, "template_adapter_not_implemented")


class TemplateLearnerContextAdapter:
    """Implements LearnerContextProvider. Shown to make the enum rule concrete."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        raw: dict[str, Any] = {}  # TODO: fetch from your system.

        # TODO 3 - VALUE NORMALISATION
        # Map the upstream's representation onto the platform enum. A value that
        # maps to no member is ProviderInvalidResponse - NOT a nearest neighbour,
        # NOT the default. The default is applied by the service, which then
        # marks naric_level_source="default"; an adapter that quietly substitutes
        # a level makes that distinction a lie.
        upstream_level = raw.get("TODO_level")
        mapping = {
            "TODO_upstream_value_for_3": NaricLevel.LEVEL_3,
            # ... one line per upstream value ...
        }
        level = mapping.get(str(upstream_level))
        if level is None:
            raise ProviderInvalidResponse("learner_context_provider", "naric_level_not_in_enum")

        return LearnerContext(
            session_id=session_id,
            user_id=user_id,
            naric_level=level,
            naric_level_source=NaricLevelSource.RETRIEVED,
            source_status=SourceStatus.AVAILABLE,
            practice_area=raw.get("TODO_practice_area"),
            case_linked_mode=bool(raw.get("TODO_case_linked")),
            case_file_id=raw.get("TODO_case_file_id"),
        )
