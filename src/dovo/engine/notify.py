"""Observer notification dispatch that records every failure at debug level and never raises."""

from __future__ import annotations

from dovo.common.diagnostic_log import DiagnosticLevel, record_exception
from dovo.engine.models import RunObserver


def safe_notify(observer: RunObserver | None, method_name: str, *args: object, **kwargs: object) -> None:
    """Call observer.<method_name>(*args, **kwargs) if observer and method exist; record any exception it raises at debug level and never raise."""
    if observer is None:
        return
    method = getattr(observer, method_name, None)
    if method is None:
        return
    try:
        method(*args, **kwargs)
    except Exception as exc:
        record_exception(exc, level=DiagnosticLevel.DEBUG, operation="engine.notify", context={"method": method_name})
