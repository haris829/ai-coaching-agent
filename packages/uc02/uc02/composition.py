"""Composition root.

Every concrete choice -- which adapter, which repository, which identity shim --
is made here and nowhere else. Wiring is FastAPI ``Depends`` plus module-level
singletons; there is no DI framework.

Tests replace collaborators with ``app.dependency_overrides[...]``, which is the
same mechanism the integration engineer can use to A/B a real adapter against a
mock before flipping the config value.
"""

from __future__ import annotations

import uuid
from functools import lru_cache

from uc02.application.context_assembly_service import ContextAssemblyService
from uc02.domain.errors import SessionIdRequired
from uc02.domain.ports.identity import CurrentUserProvider
from uc02.domain.ports.repository import SessionContextRepository
from uc02.infrastructure.config.settings import Settings, get_settings
from uc02.infrastructure.identity.development_user_provider import DevelopmentUserProvider
from uc02.infrastructure.providers.factory import ProviderBundle, build_providers
from uc02.infrastructure.repositories.in_memory_context_repository import (
    InMemorySessionContextRepository,
)

#: Prefix marking a session id UC-02 minted for itself. Its presence in a
#: production log is a bug: production session ids come from UC-01.
DEV_SESSION_ID_PREFIX = "dev-session"


@lru_cache(maxsize=1)
def get_provider_bundle() -> ProviderBundle:
    return build_providers(get_settings())


@lru_cache(maxsize=1)
def get_repository() -> SessionContextRepository:
    settings = get_settings()
    return InMemorySessionContextRepository(ttl_hours=settings.context_ttl_hours)


@lru_cache(maxsize=1)
def get_current_user_provider() -> CurrentUserProvider:
    # Replace with the platform's real auth adapter. See docs/integration.md.
    return DevelopmentUserProvider(header_name=get_settings().dev_user_id_header)


def get_assembly_service() -> ContextAssemblyService:
    settings = get_settings()
    providers = get_provider_bundle()
    return ContextAssemblyService(
        naric=providers.naric,
        courses=providers.courses,
        legal=providers.legal,
        history=providers.history,
        repository=get_repository(),
        settings=settings,
        # composition-root note: the assembly service is stateless, so building
        # one per request is cheap. The providers and repository are singletons.
    )


def resolve_session_id(supplied: str | None, settings: Settings) -> tuple[str, str]:
    """Return ``(session_id, origin)``.

    UC-02 never invents a session id in production. When the caller supplies
    none, minting happens only if ``ALLOW_DEV_SESSION_IDS`` is on, which must be
    false in production (asserted by ``Settings.production_guard_violations``).
    """
    if supplied:
        return supplied, "caller"
    if not settings.allow_dev_session_ids:
        raise SessionIdRequired(
            "session_id is required. UC-02 does not create sessions; the caller "
            "(UC-01 in production) supplies the id. Set ALLOW_DEV_SESSION_IDS=true "
            "for local development only."
        )
    return f"{DEV_SESSION_ID_PREFIX}-{uuid.uuid4()}", "dev-minted"


def reset_singletons() -> None:
    """Clear cached singletons. Used by tests that change configuration."""
    get_provider_bundle.cache_clear()
    get_repository.cache_clear()
    get_current_user_provider.cache_clear()
    get_settings.cache_clear()
