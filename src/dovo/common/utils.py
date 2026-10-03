"""Shared non-UI utilities for paths and formatting values."""

from pathlib import Path


def display_path(path: Path, cwd: Path | None = None) -> str:
    """Display a path, preferring POSIX-style relative segments when possible."""
    if cwd:
        try:
            return path.relative_to(cwd).as_posix()
        except ValueError:
            return str(path)

    try:
        return path.as_posix()
    except Exception:
        # Intentional fallback for path types that don't support as_posix();
        # display_path has no error-reporting channel.
        return str(path)


def enum_value(value: object) -> str:
    """Return `value.value` when present (enum-like), else `str(value)`."""
    attr = getattr(value, "value", None)
    return attr if isinstance(attr, str) else str(value)
