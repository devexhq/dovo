"""Sandbox/session lifecycle management for the multi-step run engine."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from worktree.core.config import ConfigLoadError
from worktree.core.db import RunStatus, SandboxesRepository
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.diff.writer import get_session_dir, write_session_diff
from worktree.core.git.runner import GitRunner
from worktree.core.project.services.storage import resolve_project_filesystem_paths
from worktree.core.runtime.models import RunCheckpoint, RunContext
from worktree.core.runtime.notify import safe_notify
from worktree.core.sandbox import Sandbox, SandboxApplyStrategy, SandboxSession


@dataclass(frozen=True)
class Workspace:
    """Sandbox/session lifecycle for one run's context: setup, cleanup, and session-scratch directories."""

    context: RunContext

    @staticmethod
    def _session_from_checkpoint(checkpoint: RunCheckpoint, path: Path) -> SandboxSession:
        """Reconstruct SandboxSession from saved checkpoint fields."""
        return SandboxSession(
            session_id=checkpoint.sandbox_id or "resumed",
            target_branch=checkpoint.sandbox_branch or "worktree/sandbox-resumed",
            sandbox_path=path,
            base_commit=checkpoint.sandbox_base_commit or "HEAD",
            name=checkpoint.sandbox_name,
            created_at="",
        )

    def _setup_resumed_sandbox(
        self,
        checkpoint: RunCheckpoint,
    ) -> tuple[Path, Sandbox | None, SandboxSession | None, str | None]:
        """Validate and prepare resumed sandbox session from checkpoint."""
        if not checkpoint.use_sandbox:
            target_dir = self.context.cwd.resolve()
            safe_notify(self.context.observer, "on_sandbox_ready", target_dir, active=False)
            return target_dir, None, None, None

        path = Path(checkpoint.sandbox_path or "")
        if not path.exists():
            return self.context.cwd.resolve(), None, None, f"Git sandbox is missing: {path}"

        session = self._session_from_checkpoint(checkpoint, path)
        manager = Sandbox(self.context.cwd.resolve(), db=SandboxesRepository(self.context.cwd.resolve()))
        safe_notify(self.context.observer, "on_sandbox_ready", path, active=True)
        return path, manager, session, None

    def setup(self) -> tuple[Path, Sandbox | None, SandboxSession | None, str | None]:
        """Create an optional sandbox and return the execution directory.

        Returns:
            Tuple of (target_dir, manager, session, error_message).
            ``error_message`` is set when sandbox setup fails.
        """
        if self.context.resume_from is not None:
            return self._setup_resumed_sandbox(self.context.resume_from)

        if not self.context.use_sandbox:
            target_dir = self.context.cwd.resolve()
            safe_notify(self.context.observer, "on_sandbox_ready", target_dir, active=False)
            return target_dir, None, None, None

        manager = Sandbox(self.context.cwd.resolve(), db=SandboxesRepository(self.context.cwd.resolve()))
        session_id = None
        if self.context.identity is not None:
            session_id = self.context.identity.blueprint_key or None
        try:
            create_result = manager.create(session_id=session_id)
        except ConfigLoadError as exc:
            return self.context.cwd.resolve(), None, None, f"Git sandbox creation failed: {exc}"
        if not create_result.ok or create_result.session is None:
            detail = create_result.errors[0] if create_result.errors else "Sandbox creation failed."
            return self.context.cwd.resolve(), None, None, f"Git sandbox creation failed: {detail}"

        session = create_result.session
        target_dir = session.sandbox_path
        safe_notify(self.context.observer, "on_sandbox_ready", target_dir, active=True)
        return target_dir, manager, session, None

    def cleanup(
        self,
        manager: Sandbox | None,
        session: SandboxSession | None,
        target_dir: Path,
    ) -> bool:
        """Clean up sandbox unless keep is requested. Returns whether it was kept."""
        if manager is None or session is None:
            safe_notify(self.context.observer, "on_sandbox_cleanup", kept=False, path=target_dir)
            return False

        if self.context.keep:
            safe_notify(self.context.observer, "on_sandbox_cleanup", kept=True, path=session.sandbox_path)
            return True

        try:
            manager.cleanup(session)
        except Exception:
            # Best-effort cleanup: worktree removal is independent of run outcome.
            pass
        safe_notify(self.context.observer, "on_sandbox_cleanup", kept=False, path=session.sandbox_path)
        return False

    def handle_auto_apply(
        self,
        manager: Sandbox | None,
        session: SandboxSession | None,
        errors: list[str],
        warnings: list[str],
    ) -> tuple[RunStatus | None, bool]:
        """Apply sandbox changes on completed runs when auto_apply is enabled.

        Returns:
            Tuple of (new_status_or_None, apply_failed_boolean).
        """
        if not (self.context.auto_apply and session is not None and manager is not None):
            return None, False

        apply_result = manager.apply(
            session.session_id,
            strategy=SandboxApplyStrategy.PATCH,
        )
        warnings.extend(apply_result.warnings)
        if not apply_result.ok:
            errors.extend(apply_result.errors)
            return RunStatus.FAILED, True

        return None, False

    def capture_and_persist_diff(
        self,
        session: SandboxSession | None,
        warnings: list[str],
    ) -> None:
        """Capture cumulative unified diff from sandbox and persist diff.patch."""
        if session is None or self.context.session_id is None or not Path(session.sandbox_path).is_dir():
            return

        session_id = self.context.session_id
        try:
            GitRunner.add_intent_to_add(session.sandbox_path, target=".")
            diff_text = GitRunner.diff(session.sandbox_path, base_commit=session.base_commit, binary=True)
            session_dir = get_session_dir(self.context.cwd, session_id)
            write_session_diff(session_dir, diff_text)
        except Exception as exc:
            warnings.append(f"Failed to persist session diff artifact: {exc}")

    def finalize_cleanup(
        self,
        manager: Sandbox | None,
        session: SandboxSession | None,
        target_dir: Path,
        status: RunStatus,
        apply_failed: bool,
    ) -> bool:
        """Clean up or keep the sandbox worktree based on run status."""
        if status == RunStatus.PAUSED or apply_failed:
            kept_path = session.sandbox_path if session is not None else target_dir
            safe_notify(self.context.observer, "on_sandbox_cleanup", kept=True, path=kept_path)
            return True

        return self.cleanup(manager, session, target_dir)

    def prepare_session_tmp_dir(self, warnings: list[str]) -> Path | None:
        """Resolve and create the session scratch directory tree, or warn and return None."""
        if self.context.session_id is None:
            return None
        tmp_dir = resolve_project_filesystem_paths(self.context.cwd).tmp_dir
        session_tmp_dir = tmp_dir / self.context.session_id
        try:
            (session_tmp_dir / "steps").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"Failed to create session scratch directory: {exc}")
            return None
        return session_tmp_dir

    def prepare_session_artifacts(self) -> tuple[Path | None, ArtifactsRepository | None]:
        """Resolve the project artifacts_dir and construct a per-run ArtifactsRepository once, or (None, None) without a session."""
        if self.context.session_id is None:
            return None, None
        artifacts_dir = resolve_project_filesystem_paths(self.context.cwd).artifacts_dir
        return artifacts_dir, ArtifactsRepository(self.context.cwd.resolve())

    def prepare_session_log_dir(self, warnings: list[str]) -> Path | None:
        """Resolve and create the session log directory, or warn and return None."""
        if self.context.session_id is None:
            return None
        logs_dir = resolve_project_filesystem_paths(self.context.cwd).logs_dir
        session_log_dir = logs_dir / self.context.session_id
        try:
            session_log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"Failed to create session log directory: {exc}")
            return None
        return session_log_dir

    def cleanup_session_tmp_dir(self, session_tmp_dir: Path | None, *, keep: bool, status: RunStatus) -> None:
        """Best-effort delete of the session scratch directory on completed, unkept runs."""
        if session_tmp_dir is None or keep or status != RunStatus.COMPLETED:
            return
        try:
            shutil.rmtree(session_tmp_dir)
        except OSError:
            # Best-effort cleanup: scratch directory removal is independent of run outcome.
            pass
