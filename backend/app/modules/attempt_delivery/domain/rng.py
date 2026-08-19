"""Deterministic, seeded randomisation.

Question and option randomisation must be *reproducible*. The seed used for an
attempt is persisted on the attempt row, so a selection can be re-derived for
auditing and asserted on in tests without flakiness. That is why this module
exists instead of calling :func:`random.shuffle` on the global generator.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def make_rng(seed: str) -> random.Random:
    """Build an independent generator from a string seed.

    The seed is hashed with SHA-256 rather than passed to ``Random`` directly so
    that structurally similar seeds (``"attempt-1"`` / ``"attempt-2"``) produce
    thoroughly different streams.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest, "big"))


def shuffled(items: Sequence[T], rng: random.Random) -> list[T]:
    """Return a shuffled copy of ``items``, leaving the input untouched."""
    out = list(items)
    rng.shuffle(out)
    return out


def sample_without_replacement(items: Sequence[T], count: int, rng: random.Random) -> list[T]:
    """Draw ``count`` items at random.

    Returns a shuffled copy of everything when ``count`` meets or exceeds the pool
    size, so callers never have to special-case a pool that exactly matches the
    requested number of questions.
    """
    if count >= len(items):
        return shuffled(items, rng)
    return rng.sample(list(items), count)
