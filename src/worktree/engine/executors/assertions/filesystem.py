"""Filesystem assertion evaluators for step results.

Paths are resolved under ``sandbox_or_root_path`` (git sandbox or plain cwd under
``--no-sandbox``). Entries that escape that root fail closed.
"""

from __future__ import annotations

from pathlib import Path


def evaluate_file_exists(paths: str | list[str], sandbox_or_root_path: Path) -> list[str]:
    """Return failures when each path is missing, a directory, or escapes the root."""
    failures: list[str] = []
    for rel_path in _normalize_path_list(paths):
        candidate = _resolve_path_candidate(rel_path, sandbox_or_root_path)
        if candidate is None:
            failures.append(f"file_exists: path '{rel_path}' escapes the root path")
            continue
        if not candidate.exists():
            failures.append(f"file_exists: path '{rel_path}' does not exist")
        elif candidate.is_dir():
            failures.append(f"file_exists: path '{rel_path}' is a directory, not a file")
    return failures


def evaluate_file_not_exists(paths: str | list[str], sandbox_or_root_path: Path) -> list[str]:
    """Return failures when each path exists under the root or escapes it."""
    failures: list[str] = []
    for rel_path in _normalize_path_list(paths):
        candidate = _resolve_path_candidate(rel_path, sandbox_or_root_path)
        if candidate is None:
            failures.append(f"file_not_exists: path '{rel_path}' escapes the root path")
            continue
        if candidate.exists():
            failures.append(f"file_not_exists: path '{rel_path}' exists but must not")
    return failures


def evaluate_file_not_empty(paths: str | list[str], sandbox_or_root_path: Path) -> list[str]:
    """Return failures when each path is missing, a directory, empty, or escapes the root."""
    failures: list[str] = []
    for rel_path in _normalize_path_list(paths):
        candidate = _resolve_path_candidate(rel_path, sandbox_or_root_path)
        if candidate is None:
            failures.append(f"file_not_empty: path '{rel_path}' escapes the root path")
            continue
        if not candidate.exists():
            failures.append(f"file_not_empty: path '{rel_path}' does not exist")
        elif candidate.is_dir():
            failures.append(f"file_not_empty: path '{rel_path}' is a directory, not a file")
        elif candidate.stat().st_size == 0:
            failures.append(f"file_not_empty: path '{rel_path}' is empty (0 bytes)")
    return failures


def _normalize_path_list(paths: str | list[str]) -> list[str]:
    """Normalize a single path string or list of paths into a list of paths."""
    return paths if isinstance(paths, list) else [paths]


def _resolve_path_candidate(rel_path: str, sandbox_or_root_path: Path) -> Path | None:
    """Resolve ``rel_path`` under ``sandbox_or_root_path``, or ``None`` if it escapes the root."""
    root_resolved = sandbox_or_root_path.resolve()
    candidate = (sandbox_or_root_path / rel_path).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate
