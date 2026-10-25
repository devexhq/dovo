"""Allocation of the private scratch and control directories for one agent attempt."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from dovo.common.error_codes import ErrorCode
from dovo.common.models import Remediation
from dovo.core.agents.exceptions import AgentError
from dovo.core.agents.models import AgentInvocationContext

_INVOCATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_REMEDIATION = Remediation(message="Restore access to the session temp directory or correct its storage path.")


def new_invocation_id() -> str:
    """Return a fresh UUID4 hex invocation id (32 lowercase hex characters)."""
    return uuid.uuid4().hex


def scratch_unavailable_message(step_id: str, detail: str) -> str:
    """Return the AGENT_SCRATCH_UNAVAILABLE diagnostic for the step."""
    return f"Cannot prepare private scratch for agent step '{step_id}' (AGENT_SCRATCH_UNAVAILABLE): {detail}"


def allocate_invocation_paths(
    *,
    invocation_id: str,
    session_tmp_dir: Path | None,
    step_id: str,
    worktree_path: Path,
    main_checkout: Path,
) -> AgentInvocationContext:
    """Create and verify the invocation's scratch and control directories and return their context.

    Never creates session_tmp_dir or anything above its steps/ directory.

    Raises:
        AgentError: AGENT_SCRATCH_UNAVAILABLE, with the step's diagnostic and the storage remediation, when the step
            or invocation id is unusable, the session temp directory is missing, overlaps a checkout, or cannot hold
            the directories.
    """
    detail = _unsafe_step_id(step_id)
    if detail is None and _INVOCATION_ID_PATTERN.fullmatch(invocation_id) is None:
        detail = f"invocation id {invocation_id!r} is not 32 lowercase hex characters"

    if detail is not None:
        raise _unavailable(step_id, detail)

    if session_tmp_dir is None:
        raise _unavailable(step_id, "the session temp directory is unavailable")

    return _allocate(invocation_id, session_tmp_dir, step_id, worktree_path, main_checkout)


def _unavailable(step_id: str, detail: str) -> AgentError:
    """Return the AGENT_SCRATCH_UNAVAILABLE error for detail."""
    return AgentError(ErrorCode.AGENT_SCRATCH_UNAVAILABLE, scratch_unavailable_message(step_id, detail), [_REMEDIATION])


def _allocate(
    invocation_id: str, session_tmp_dir: Path, step_id: str, worktree_path: Path, main_checkout: Path
) -> AgentInvocationContext:
    """Resolve the invocation root, reject checkout overlap, and create its directories, raising AGENT_SCRATCH_UNAVAILABLE."""
    try:
        session_root = session_tmp_dir.resolve(strict=True)
        invocation_root = session_root / "steps" / step_id / "agent" / invocation_id
        overlap = _overlap_error(invocation_root, worktree_path.resolve(), main_checkout.resolve())
    except OSError as exc:
        raise _unavailable(step_id, f"cannot resolve the session temp directory: {exc}") from exc

    if overlap is not None:
        raise _unavailable(step_id, overlap)

    return _create_invocation_dirs(invocation_root, step_id)


def _unsafe_step_id(step_id: str) -> str | None:
    """Return why step_id cannot be one safe path segment (empty, separator, '.' or '..', NUL), or None."""
    if not step_id or step_id in {".", ".."}:
        return f"step id {step_id!r} is not a usable directory name"

    if any(char in step_id for char in ("/", "\\", "\x00")):
        return f"step id {step_id!r} contains a path separator or NUL"

    return None


def _overlap_error(invocation_root: Path, worktree_path: Path, main_checkout: Path) -> str | None:
    """Return why invocation_root lies inside, equals, or contains the worktree or main checkout, or None."""
    for label, checkout in (("worktree", worktree_path), ("main checkout", main_checkout)):
        if invocation_root.is_relative_to(checkout) or checkout.is_relative_to(invocation_root):
            return f"the session temp directory overlaps the {label} ({checkout})"

    return None


def _create_invocation_dirs(invocation_root: Path, step_id: str) -> AgentInvocationContext:
    """Create invocation_root, scratch, and control without reusing an existing directory, and return the verified context, raising AGENT_SCRATCH_UNAVAILABLE."""
    scratch_path = invocation_root / "scratch"
    control_path = invocation_root / "control"

    try:
        for shared in invocation_root.parents[:3]:
            if shared.is_symlink():
                raise _unavailable(step_id, f"{shared} is a symlink")

        invocation_root.parent.mkdir(parents=True, exist_ok=True)
        invocation_root.mkdir()
        scratch_path.mkdir()
        control_path.mkdir()

        for created in (invocation_root, scratch_path, control_path):
            if created.resolve(strict=True) != created:
                raise _unavailable(step_id, f"{created} resolves outside the session temp directory")
    except OSError as exc:
        raise _unavailable(step_id, f"cannot create {invocation_root}: {exc}") from exc

    return AgentInvocationContext(
        invocation_id=invocation_root.name, scratch_path=scratch_path, control_path=control_path
    )
