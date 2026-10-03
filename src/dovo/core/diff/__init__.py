"""Core diff domain package."""

from dovo.core.diff.facade import Diff
from dovo.core.diff.models import DiffResult, DiffStatus

__all__ = [
    "Diff",
    "DiffResult",
    "DiffStatus",
]
