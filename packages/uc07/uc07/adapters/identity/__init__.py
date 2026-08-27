"""Replaceable identity seams (not production authentication)."""

from uc07.adapters.identity.header import (
    HeaderCurrentUserProvider,
    StaticCurrentUserProvider,
)

__all__ = ["HeaderCurrentUserProvider", "StaticCurrentUserProvider"]
