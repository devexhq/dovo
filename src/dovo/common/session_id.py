"""Session id generation shared by the engine and worktree creation."""

from __future__ import annotations

import uuid
from collections.abc import Callable

type SessionIdFactory = Callable[[str], str]


def new_session_id(kind: str) -> str:
    """Return a new session id of the form <kind>_<8 hex>."""
    return f"{kind}_{uuid.uuid4().hex[:8]}"
