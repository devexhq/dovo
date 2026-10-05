"""Worktree/session lifecycle management for the multi-step run engine."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from dovo.core.config import ConfigLoadError
from dovo.core.db import RunStatus, WorktreesRepository
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.git.runner import GitRunner
from dovo.core.worktree import Worktree, WorktreeApplyStrategy, WorktreeSession
from dovo.engine.models import RunSettings
from dovo.engine.notify import safe_notify
from dovo.engine.storage_bridge import create_storage_bridge, remove_storage_bridge
from dovo.engine.writer import get_session_dir, write_session_diff


@dataclass(frozen=True)
class Workspace:
    """Worktree/session lifecycle for one run's context: setup, cleanup, and session-scratch directories."""

    context: RunSettings

    def _setup_retained_worktree(
        self,
        worktree_id: str,
    ) -> tuple[Path, Worktree | None, WorktreeSession | None, str | None]:
        """Rebuild the retained worktree session from paths.worktree_dir(worktree_id) and its worktrees row, or return a setup error."""
        path = self.context.paths.worktree_dir(worktree_id)
        if not path.exists():
            return self.context.cwd.resolve(), None, None, f"Git worktree is missing: {path}"

        worktrees = WorktreesRepository(
            db_path=self.context.paths.database_file, project_id=self.context.paths.project_id
        )
        record = worktrees.get(worktree_id)
        if record is None:
            return self.context.cwd.resolve(), None, None, f"Git worktree record is missing: {worktree_id}"

        session = WorktreeSession(
            session_id=worktree_id,
            target_branch=record.branch_name,
            worktree_path=path,
            base_commit=record.base_commit,
            name=record.name,
            created_at=record.created_at,
        )
        manager = Worktree(self.context.paths, db=worktrees)
        safe_notify(self.context.observer, "on_worktree_ready", path, active=True)
        return path, manager, session, None

    def setup(self) -> tuple[Path, Worktree | None, WorktreeSession | None, str | None]:
        """Create an optional worktree and return the execution directory.

        Returns:
            Tuple of (target_dir, manager, session, error_message).
            ``error_message`` is set when worktree setup fails.
        """
        if not self.context.use_worktree:
            target_dir = self.context.cwd.resolve()
            safe_notify(self.context.observer, "on_worktree_ready", target_dir, active=False)
            return target_dir, None, None, None

        if self.context.worktree_id is not None:
            return self._setup_retained_worktree(self.context.worktree_id)

        manager = Worktree(
            self.context.paths,
            db=WorktreesRepository(db_path=self.context.paths.database_file, project_id=self.context.paths.project_id),
        )
        try:
            create_result = manager.create(session_id=self.context.session_id)
        except ConfigLoadError as exc:
            return self.context.cwd.resolve(), None, None, f"Git worktree creation failed: {exc}"
        if not create_result.ok or create_result.session is None:
            detail = create_result.errors[0] if create_result.errors else "Worktree creation failed."
            return self.context.cwd.resolve(), None, None, f"Git worktree creation failed: {detail}"

        session = create_result.session
        target_dir = session.worktree_path
        safe_notify(self.context.observer, "on_worktree_ready", target_dir, active=True)
        return target_dir, manager, session, None

    def link_session_dir(
        self,
        manager: Worktree | None,
        session: WorktreeSession | None,
        warnings: list[str],
    ) -> str | None:
        """Link a freshly created run worktree to its session directory; on failure discard the worktree and return the prefixed error.

        A retained worktree (context.worktree_id set) is never linked.
        """
        if manager is None or session is None or self.context.worktree_id is not None:
            return None
        if self.context.session_id is None:
            return None

        link_error = create_storage_bridge(self.context.paths, self.context.session_id, session.worktree_path, warnings)
        if link_error is None:
            return None

        self._discard_worktree(manager, session)
        safe_notify(self.context.observer, "on_worktree_cleanup", kept=False, path=session.worktree_path)
        return f"Worktree storage bridge failed: {link_error}"

    def _discard_worktree(self, manager: Worktree, session: WorktreeSession) -> None:
        """Best-effort Worktree.cleanup that never raises."""
        try:
            manager.cleanup(session)
        except Exception:
            # Best-effort cleanup: worktree removal is independent of run outcome.
            pass

    def cleanup(
        self,
        manager: Worktree | None,
        session: WorktreeSession | None,
        target_dir: Path,
        warnings: list[str],
    ) -> bool:
        """Clean up worktree unless keep is requested. Returns whether it was kept."""
        if manager is None or session is None:
            safe_notify(self.context.observer, "on_worktree_cleanup", kept=False, path=target_dir)
            return False

        if self.context.keep:
            safe_notify(self.context.observer, "on_worktree_cleanup", kept=True, path=session.worktree_path)
            return True

        unlink_warning = remove_storage_bridge(session.worktree_path)
        if unlink_warning is not None:
            warnings.append(unlink_warning)

        self._discard_worktree(manager, session)
        safe_notify(self.context.observer, "on_worktree_cleanup", kept=False, path=session.worktree_path)
        return False

    def handle_auto_apply(
        self,
        manager: Worktree | None,
        session: WorktreeSession | None,
        errors: list[str],
        warnings: list[str],
    ) -> tuple[RunStatus | None, bool]:
        """Apply worktree changes on completed runs when auto_apply is enabled.

        Returns:
            Tuple of (new_status_or_None, apply_failed_boolean).
        """
        if not (self.context.auto_apply and session is not None and manager is not None):
            return None, False

        apply_result = manager.apply(
            session.session_id,
            strategy=WorktreeApplyStrategy.PATCH,
        )
        warnings.extend(apply_result.warnings)
        if not apply_result.ok:
            errors.extend(apply_result.errors)
            return RunStatus.FAILED, True

        return None, False

    def capture_and_persist_diff(
        self,
        session: WorktreeSession | None,
        warnings: list[str],
    ) -> None:
        """Capture cumulative unified diff from worktree and persist diff.patch."""
        if session is None or self.context.session_id is None or not Path(session.worktree_path).is_dir():
            return

        session_id = self.context.session_id
        try:
            GitRunner.add_intent_to_add(session.worktree_path, target=".")
            diff_text = GitRunner.diff(session.worktree_path, base_commit=session.base_commit, binary=True)
            session_dir = get_session_dir(self.context.paths, session_id)
            write_session_diff(session_dir, diff_text)
        except Exception as exc:
            warnings.append(f"Failed to persist session diff artifact: {exc}")

    def finalize_cleanup(
        self,
        manager: Worktree | None,
        session: WorktreeSession | None,
        target_dir: Path,
        status: RunStatus,
        apply_failed: bool,
        warnings: list[str],
    ) -> bool:
        """Clean up or keep the worktree based on run status."""
        if status == RunStatus.PAUSED or apply_failed:
            kept_path = session.worktree_path if session is not None else target_dir
            safe_notify(self.context.observer, "on_worktree_cleanup", kept=True, path=kept_path)
            return True

        return self.cleanup(manager, session, target_dir, warnings)

    def prepare_session_tmp_dir(self, warnings: list[str]) -> Path | None:
        """Resolve and create the session scratch directory tree, or warn and return None."""
        if self.context.session_id is None:
            return None
        tmp_dir = self.context.paths.tmp_dir
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
        artifacts_dir = self.context.paths.artifacts_dir
        return artifacts_dir, ArtifactsRepository(
            db_path=self.context.paths.database_file, project_id=self.context.paths.project_id
        )

    def prepare_session_log_dir(self, warnings: list[str]) -> Path | None:
        """Resolve and create the session log directory, or warn and return None."""
        if self.context.session_id is None:
            return None
        logs_dir = self.context.paths.logs_dir
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
