"""Sandbox lifecycle management service."""

from __future__ import annotations

import platform
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from worktree.common.constants import DEFAULT_MAXIMUM_SANDBOXES_ALLOWED
from worktree.common.filesystem import WorkspacePaths
from worktree.common.lock import WorkspaceLock
from worktree.core.config import Config
from worktree.core.config.models import SandboxConfig
from worktree.core.db import SandboxesRepository, SandboxRecord, SandboxStatus
from worktree.core.git.exceptions import (
    GitCommandError,
    GitNotFoundError,
    GitPlumbingTimeoutError,
)
from worktree.core.git.runner import GitRunner
from worktree.core.sandbox.models import (
    SandboxCreateResult,
    SandboxCreateStatus,
    SandboxSession,
)
from worktree.core.sandbox.services.wip import apply_wip_to_sandbox


def _clean_opt_str(val: str | None) -> str | None:
    """Trim whitespace from string, returning None if empty or None."""
    if val is None:
        return None
    s = val.strip()
    return s if s else None


def _extract_target_metadata(target: SandboxSession | SandboxRecord) -> tuple[Path, str, str]:
    """Extract sandbox path, session id, and branch name from a session or record."""
    if isinstance(target, SandboxRecord):
        return Path(target.sandbox_path), target.id, target.branch_name
    return target.sandbox_path, target.session_id, target.target_branch


def _is_windows_symlink_fallback(exc: OSError) -> bool:
    """Return whether an error is a documented Windows symlink limitation."""
    winerror = getattr(exc, "winerror", None)
    error_code = winerror if isinstance(winerror, int) else exc.errno
    return platform.system() == "Windows" and error_code in (50, 1314)


class SandboxLifecycle:
    """Orchestrates sandbox creation, validation, cleanup, and pruning."""

    def __init__(
        self,
        paths: WorkspacePaths,
        db: SandboxesRepository,
    ) -> None:
        """Initialize lifecycle manager.

        Args:
            paths: Resolved command-invocation workspace paths.
            db: Explicit SandboxesRepository instance.
        """
        self.paths = paths
        self.path = paths.root_dir
        self.db = db

    @property
    def sandbox_base_dir(self) -> Path:
        """Base storage directory for created sandboxes."""
        return self.paths.sandboxes_dir

    def _get_sandbox_config(self) -> SandboxConfig:
        """Return active sandbox configuration."""
        return Config(self.paths).sandbox

    def _ensure_sandbox_dir(self) -> None:
        """Create the parent sandbox storage directory if missing."""
        self.sandbox_base_dir.mkdir(parents=True, exist_ok=True)

    def get_active(self) -> list[Path]:
        """List immediate child directories under the sandbox base path."""
        if not self.sandbox_base_dir.exists():
            return []
        return [p for p in self.sandbox_base_dir.iterdir() if p.is_dir()]

    def discard_partial(self, sandbox_path: Path, temp_branch: str) -> None:
        """Best-effort removal of a partial worktree/branch after failed creation."""
        if sandbox_path.exists():
            try:
                GitRunner.worktree_remove(self.path, sandbox_path, force=True)
            except Exception:
                # Best-effort fallback: remove directory directly if git worktree removal fails.
                shutil.rmtree(sandbox_path, ignore_errors=True)
        try:
            GitRunner.branch_delete(self.path, temp_branch, force=True)
        except Exception:
            # Best-effort cleanup: branch may not have been created yet.
            pass
        try:
            GitRunner.worktree_prune(self.path)
        except Exception:
            # Best-effort cleanup: ignore errors during worktree prune.
            pass

    def _check_capacity(self, max_allowed: int = DEFAULT_MAXIMUM_SANDBOXES_ALLOWED) -> SandboxCreateResult | None:
        """Return an error result when active sandboxes reach configured capacity."""
        active = self.get_active()
        if len(active) >= max_allowed:
            return SandboxCreateResult(
                status=SandboxCreateStatus.CAPACITY_EXCEEDED,
                errors=[f"Maximum active sandboxes reached ({len(active)}/{max_allowed})."],
                fixes=[
                    "Run `wt prune` to remove stale sandboxes, or",
                    "Raise sandbox.max_active_sandboxes in .worktree/config.json",
                ],
            )
        return None

    def _resolve_base_ref(self, override_base_ref: str | None, default_base_ref: str = "HEAD") -> str:
        """Return the git ref to branch the sandbox from."""
        if override_base_ref is not None:
            return override_base_ref
        source_branch = GitRunner.get_current_branch(self.path)
        if source_branch not in ("unknown", "HEAD (detached)"):
            return source_branch
        return default_base_ref

    def _create_worktree(
        self,
        sandbox_path: Path,
        temp_branch: str,
        base_ref: str,
    ) -> SandboxCreateResult | None:
        """Create git worktree and branch, discarding on failure."""
        try:
            GitRunner.worktree_add(self.path, sandbox_path, temp_branch, base_ref)
            return None
        except GitPlumbingTimeoutError as exc:
            self.discard_partial(sandbox_path, temp_branch)
            return SandboxCreateResult(
                status=SandboxCreateStatus.GIT_TIMEOUT,
                errors=[f"Git worktree operation timed out (SANDBOX_GIT_TIMEOUT): {exc}"],
                fixes=["Check for git lock files, credential prompts, or stuck git processes, then retry"],
            )
        except Exception as exc:
            self.discard_partial(sandbox_path, temp_branch)
            return SandboxCreateResult(
                status=SandboxCreateStatus.GIT_FAILED,
                errors=[f"Git worktree operation failed (SANDBOX_GIT_FAILED): {exc}"],
                fixes=["Ensure this directory is a Git repository with a valid base ref"],
            )

    def _resolve_base_commit(
        self,
        sandbox_path: Path,
        temp_branch: str,
    ) -> tuple[str, SandboxCreateResult | None]:
        """Determine base commit SHA for the created sandbox."""
        try:
            commit_sha = GitRunner.rev_parse(sandbox_path, rev="HEAD")
            return commit_sha, None
        except GitPlumbingTimeoutError as exc:
            self.discard_partial(sandbox_path, temp_branch)
            return "", SandboxCreateResult(
                status=SandboxCreateStatus.GIT_TIMEOUT,
                errors=[f"Git worktree operation timed out (SANDBOX_GIT_TIMEOUT): {exc}"],
            )
        except Exception as exc:
            self.discard_partial(sandbox_path, temp_branch)
            return "", SandboxCreateResult(
                status=SandboxCreateStatus.GIT_FAILED,
                errors=[f"Git worktree operation failed (SANDBOX_GIT_FAILED): {exc}"],
            )

    def _overlay_wip(
        self,
        sandbox_path: Path,
        temp_branch: str,
    ) -> tuple[list[str], SandboxCreateResult | None]:
        """Overlay uncommitted working tree changes into sandbox."""
        try:
            paths = apply_wip_to_sandbox(source_root=self.path, sandbox_path=sandbox_path)
            return paths, None
        except GitPlumbingTimeoutError as exc:
            self.discard_partial(sandbox_path, temp_branch)
            return [], SandboxCreateResult(
                status=SandboxCreateStatus.GIT_TIMEOUT,
                errors=[f"Git timed out while overlaying uncommitted WIP (SANDBOX_GIT_TIMEOUT): {exc}"],
                fixes=["Check for git lock files or retry without --wip"],
            )
        except Exception as exc:
            self.discard_partial(sandbox_path, temp_branch)
            return [], SandboxCreateResult(
                status=SandboxCreateStatus.WIP_FAILED,
                errors=[f"Failed to overlay uncommitted WIP into sandbox (SANDBOX_WIP_FAILED): {exc}"],
                fixes=["Resolve local conflicts and retry, or commit changes first"],
            )

    def _persist_session(self, session: SandboxSession) -> list[str]:
        """Insert session into local database; return warnings on failure."""
        try:
            self.db.create(
                id=session.session_id,
                name=session.name,
                branch_name=session.target_branch,
                base_commit=session.base_commit,
                sandbox_path=session.sandbox_path,
            )
            return []
        except Exception as exc:
            return [f"Failed to persist sandbox metadata to the local database: {exc}"]

    def _create_storage_bridge(self, sandbox_path: Path, session_id: str) -> SandboxCreateResult | None:
        """Create the sandbox run bridge or classify an unsafe bridge collision."""
        session_dir = self.paths.session_dir(session_id)
        bridge_dir = sandbox_path / ".worktree"
        bridge_path = bridge_dir / "run"

        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            bridge_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return SandboxCreateResult(
                status=SandboxCreateStatus.STORAGE_BRIDGE_FAILED,
                errors=[f"Unable to prepare sandbox storage bridge at '{bridge_path}': {exc}"],
            )

        if bridge_path.is_symlink():
            try:
                bridge_path.unlink()
            except OSError as exc:
                return SandboxCreateResult(
                    status=SandboxCreateStatus.STORAGE_BRIDGE_FAILED,
                    errors=[f"Unable to replace sandbox storage bridge at '{bridge_path}': {exc}"],
                )
        elif bridge_path.exists():
            return SandboxCreateResult(
                status=SandboxCreateStatus.STORAGE_BRIDGE_FAILED,
                errors=[f"Sandbox storage bridge path '{bridge_path}' is not a symlink."],
            )

        try:
            bridge_path.symlink_to(session_dir, target_is_directory=True)
        except OSError as exc:
            if not _is_windows_symlink_fallback(exc):
                return SandboxCreateResult(
                    status=SandboxCreateStatus.STORAGE_BRIDGE_FAILED,
                    errors=[f"Unable to create sandbox storage bridge at '{bridge_path}': {exc}"],
                )
            return SandboxCreateResult(
                status=SandboxCreateStatus.OK,
                warnings=[f"Unable to create sandbox storage bridge at '{bridge_path}': {exc}"],
            )

        return None

    def _unlink_storage_bridge(self, sandbox_path: Path) -> str | None:
        """Unlink the sandbox run bridge without traversing its target."""
        bridge_path = sandbox_path / ".worktree" / "run"
        if not bridge_path.is_symlink():
            return None

        try:
            bridge_path.unlink()
        except OSError as exc:
            return f"Failed to unlink sandbox storage bridge at '{bridge_path}': {exc}"

        return None

    def create(
        self,
        session_id: str | None = None,
        *,
        include_wip: bool = False,
        name: str | None = None,
        base_ref: str | None = None,
    ) -> SandboxCreateResult:
        """Create an isolated sandbox worktree without raising for classified failures.

        Args:
            session_id: Optional fixed session id; otherwise generated (sbx_ + 8 hex).
            include_wip: When True, overlay uncommitted working tree changes.
            name: Optional human-readable sandbox name.
            base_ref: Optional git ref override for worktree creation.

        Returns:
            Structured SandboxCreateResult containing session on success.
        """
        if self.paths.project_id is None:
            return SandboxCreateResult(
                status=SandboxCreateStatus.NOT_INITIALIZED,
                errors=["Workspace is not initialized."],
                fixes=["Run `wt init` to initialize this workspace."],
            )

        with WorkspaceLock(self.paths.lock_file):
            sandbox_cfg = self._get_sandbox_config()
            self._ensure_sandbox_dir()
            capacity_err = self._check_capacity(sandbox_cfg.max_active_sandboxes)
            if capacity_err is not None:
                return capacity_err

            return self._build_sandbox(
                session_id, include_wip=include_wip, name=name, base_ref=base_ref, sandbox_cfg=sandbox_cfg
            )

    def _build_sandbox(
        self,
        session_id: str | None,
        *,
        include_wip: bool,
        name: str | None,
        base_ref: str | None,
        sandbox_cfg: SandboxConfig,
    ) -> SandboxCreateResult:
        """Create the worktree, resolve its base commit, overlay WIP, and bridge storage for a new sandbox session."""
        resolved_name = _clean_opt_str(name)
        override_base_ref = _clean_opt_str(base_ref)
        sid = session_id or f"sbx_{uuid.uuid4().hex[:8]}"
        sandbox_path = (self.sandbox_base_dir / sid).resolve()
        temp_branch = f"worktree/sandbox-{sid}"
        resolved_base = self._resolve_base_ref(override_base_ref, sandbox_cfg.base_ref)

        worktree_err = self._create_worktree(sandbox_path, temp_branch, resolved_base)
        if worktree_err is not None:
            return worktree_err

        base_commit, commit_err = self._resolve_base_commit(sandbox_path, temp_branch)
        if commit_err is not None:
            return commit_err

        wip_paths: list[str] = []
        if include_wip:
            wip_paths, wip_err = self._overlay_wip(sandbox_path, temp_branch)
            if wip_err is not None:
                return wip_err

        bridge_result = self._create_storage_bridge(sandbox_path, sid)
        if bridge_result is not None and bridge_result.status != SandboxCreateStatus.OK:
            self.discard_partial(sandbox_path, temp_branch)
            return bridge_result

        session = SandboxSession(
            session_id=sid,
            target_branch=temp_branch,
            sandbox_path=sandbox_path,
            base_commit=base_commit,
            name=resolved_name,
            created_at=datetime.now(UTC).isoformat(),
            wip_applied=bool(include_wip),
            wip_paths=wip_paths,
        )

        bridge_warnings = bridge_result.warnings if bridge_result is not None else []
        warnings = [*bridge_warnings, *self._persist_session(session)]
        return SandboxCreateResult(
            status=SandboxCreateStatus.OK,
            session=session,
            warnings=warnings,
        )

    def _remove_worktree_dir(self, sandbox_path: Path, *, force: bool) -> str | None:
        """Remove worktree directory with git worktree remove and rmtree fallback."""
        if not sandbox_path.exists():
            return None
        try:
            GitRunner.worktree_remove(self.path, sandbox_path, force=force)
            return None
        except Exception as exc:
            try:
                shutil.rmtree(sandbox_path)
                return None
            except Exception as rm_exc:
                return f"Failed to remove sandbox worktree directory at '{sandbox_path}': {exc}; fallback removal failed: {rm_exc}"

    def _delete_branch(self, branch_name: str) -> str | None:
        """Delete temporary branch, ignoring expected idempotent not-found errors."""
        try:
            GitRunner.branch_delete(self.path, branch_name, force=True)
            return None
        except (GitCommandError, GitNotFoundError):
            # Best-effort: branch may already be deleted during idempotent cleanup.
            return None
        except Exception as exc:
            return f"Failed to delete branch '{branch_name}': {exc}"

    def cleanup(
        self,
        target: SandboxSession | SandboxRecord,
        *,
        force: bool = True,
    ) -> list[str]:
        """Remove worktree, delete throwaway branch, and prune (idempotent).

        Args:
            target: SandboxSession or SandboxRecord to clean up.
            force: Force removal of worktree even if untracked files exist.

        Returns:
            List of warning messages encountered during cleanup steps.
        """
        with WorkspaceLock(self.paths.lock_file):
            sandbox_path, session_id, branch_name = _extract_target_metadata(target)
            warnings: list[str] = []

            bridge_warning = self._unlink_storage_bridge(sandbox_path)
            if bridge_warning:
                warnings.append(bridge_warning)

            dir_warning = self._remove_worktree_dir(sandbox_path, force=force)
            if dir_warning:
                warnings.append(dir_warning)

            try:
                self.db.update_status(session_id, SandboxStatus.CLEANED)
            except Exception as exc:
                warnings.append(f"Failed to update database status to 'cleaned' for sandbox '{session_id}': {exc}")

            branch_warning = self._delete_branch(branch_name)
            if branch_warning:
                warnings.append(branch_warning)

            try:
                self.prune()
            except Exception as exc:
                warnings.append(f"Failed to prune git worktrees: {exc}")

            return warnings

    def prune(self) -> None:
        """Prune stale Git worktree administrative records."""
        try:
            GitRunner.worktree_prune(self.path)
        except (GitCommandError, GitNotFoundError):
            # Best-effort: ignore harmless git errors during administrative worktree prune.
            pass
