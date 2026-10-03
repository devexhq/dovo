"""Observer notification dispatch that swallows every failure."""

from __future__ import annotations

from dovo.engine.models import RunObserver


def safe_notify(observer: RunObserver | None, method_name: str, *args: object, **kwargs: object) -> None:
    """Call observer.<method_name>(*args, **kwargs) if observer and method exist; swallow any exception it raises."""
    if observer is None:
        return
    method = getattr(observer, method_name, None)
    if method is None:
        return
    try:
        method(*args, **kwargs)
    except Exception:
        pass
