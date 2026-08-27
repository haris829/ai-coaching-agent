"""Identifier minting for records this component owns."""

from __future__ import annotations

import uuid


def new_rating_id() -> str:
    return f"rat_{uuid.uuid4().hex}"


def new_flag_id() -> str:
    return f"flg_{uuid.uuid4().hex}"


def new_flag_work_id() -> str:
    return f"fwk_{uuid.uuid4().hex}"


def new_dev_session_id() -> str:
    """Dev-mode only. Gated by ``ALLOW_DEV_SESSION_MINTING``, defaulted off.

    This component receives an opaque ``session_id`` and never creates one on a
    production path; see :func:`uc10.api.deps.mint_dev_session_id`.
    """
    return f"dev_sess_{uuid.uuid4().hex}"
