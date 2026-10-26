"""Worktree/session lifecycle management for the multi-step run engine."""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from dovo.common.diagnostic_log import DiagnosticRecord
from dovo.common.formatters import format_remediations
from dovo.common.lock import LockTimeoutError
from dovo.common.models import Remediation
from dovo.common.redact import SecretRedactor
from dovo.common.utils import display_path
from dovo.core.artifacts.artifacts import Artifacts
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.config import ConfigLoadError
from dovo.core.db import SessionStatus, WorktreesRepository
from dovo.core.git.exceptions import GitError
from dovo.core.git.tree_diff import diff_work_tree
from dovo.core.worktree import Worktree, WorktreeApplyStrategy, WorktreeCreateResult, WorktreeSession
from dovo.engine.executors.failure_log import logged_failure
from dovo.engine.models import RunSettings
from dovo.engine.notify import safe_notify
from dovo.engine.storage_bridge import create_storage_bridge, remove_storage_bridge
from dovo.engine.writer import get_session_dir, write_session_diff


def _creation_failure_message(create_result: WorktreeCreateResult) -> str:
    """Return `Git worktree creation failed: <detail>` plus the result's `Fix:` block when it has remediations."""
    detail = create_result.errors[0] if create_result.errors else "Worktree creation failed."
    message = f"Git worktree creation failed: {detail}"
    if create_result.remediations:
        message += "\n" + format_remediations(create_result.remediations)

    return message


def _kept_remediations(path: str, worktree_id: str) -> list[Remediation]:
    """Return the inspect and remove steps for a run worktree kept for inspection, shell-quoting path in the inspect command."""
    inspect_command = f"git -C {shlex.quote(path)} status"
    return [
        Remediation(message=f"Inspect it with '{inspect_command}'", command=inspect_command),
        Remediation(
            message=f"Remove it with 'dovo worktree delete {worktree_id}'",
            command=f"dovo worktree delete {worktree_id}",
        ),
    ]


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
        manager = Worktree(
            self.context.paths, git=self.context.git, clock=self.context.clock, new_id=self.context.new_id, db=worktrees
        )
        safe_notify(self.context.observer, "on_worktree_ready", path, active=True)
        return path, manager, session, None

    def setup(
        self, warnings: list[str], diagnostics: list[DiagnosticRecord]
    ) -> tuple[Path, Worktree | None, WorktreeSession | None, str | None]:
        """Create an optional worktree and return the execution directory, adding create's warnings and records to the lists.

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
            git=self.context.git,
            clock=self.context.clock,
            new_id=self.context.new_id,
            db=WorktreesRepository(db_path=self.context.paths.database_file, project_id=self.context.paths.project_id),
        )
        try:
            create_result = manager.create(session_id=self.context.session_id)
        except ConfigLoadError as exc:
            return self.context.cwd.resolve(), None, None, f"Git worktree creation failed: {exc}"
        diagnostics.extend(create_result.diagnostics)
        if not create_result.ok or create_result.session is None:
            return self.context.cwd.resolve(), None, None, _creation_failure_message(create_result)
        warnings.extend(create_result.warnings)

        session = create_result.session
        target_dir = session.worktree_path
        safe_notify(self.context.observer, "on_worktree_ready", target_dir, active=True)
        return target_dir, manager, session, None

    def link_session_dir(
        self,
        manager: Worktree | None,
        session: WorktreeSession | None,
        warnings: list[str],
        diagnostics: list[DiagnosticRecord],
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

        # The discard's warnings and records are reported alongside the bridge error, which stays the returned cause.
        warnings.extend(self._discard_worktree(manager, session, diagnostics))
        safe_notify(self.context.observer, "on_worktree_cleanup", kept=False, path=session.worktree_path)
        return f"Worktree storage bridge failed: {link_error}"

    def _retains(self, status: SessionStatus) -> bool:
        """Return True when the worktree outlives the run: --discard removes, --keep keeps, otherwise only FAILED keeps."""
        if self.context.discard:
            return False

        return self.context.keep or status == SessionStatus.FAILED

    def _discard_worktree(
        self, manager: Worktree, session: WorktreeSession, diagnostics: list[DiagnosticRecord]
    ) -> list[str]:
        """Remove the worktree, add its records to diagnostics, and return `Failed to remove worktree '<path>': <problem>` for each of its errors and warnings."""
        message = f"Failed to remove worktree '{session.worktree_path}'"
        try:
            removal = manager.cleanup(session)
        except LockTimeoutError as exc:
            # No record: lock contention is expected; the message names the cause.
            return [f"{message}: {exc}"]
        diagnostics.extend(removal.diagnostics)
        return [f"{message}: {problem}" for problem in [*removal.errors, *removal.warnings]]

    def cleanup(
        self,
        manager: Worktree | None,
        session: WorktreeSession | None,
        target_dir: Path,
        status: SessionStatus,
        warnings: list[str],
        diagnostics: list[DiagnosticRecord],
    ) -> bool:
        """Keep or remove the worktree per `_retains(status)`, appending removal warnings and records on failure; return whether it was kept."""
        if manager is None or session is None:
            safe_notify(self.context.observer, "on_worktree_cleanup", kept=False, path=target_dir)
            return False

        if self._retains(status):
            safe_notify(
                self.context.observer,
                "on_worktree_cleanup",
                kept=True,
                path=session.worktree_path,
                worktree_id=session.session_id,
                branch=session.target_branch,
                remediations=_kept_remediations(display_path(session.worktree_path, Path.cwd()), session.session_id),
            )
            return True

        unlink_warning = remove_storage_bridge(session.worktree_path)
        if unlink_warning is not None:
            warnings.append(unlink_warning)

        warnings.extend(self._discard_worktree(manager, session, diagnostics))

        safe_notify(self.context.observer, "on_worktree_cleanup", kept=False, path=session.worktree_path)
        return False

    def handle_auto_apply(
        self,
        manager: Worktree | None,
        session: WorktreeSession | None,
        errors: list[str],
        warnings: list[str],
    ) -> tuple[SessionStatus | None, bool]:
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
            return SessionStatus.FAILED, True

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
            self._persist_diff(session, session_id)
        except GitError as exc:
            # No record: git failures diffing the worktree are expected; the warning names the cause.
            warnings.append(f"Failed to persist session diff artifact: {exc}")
        except Exception as exc:
            warnings.append(
                logged_failure(
                    f"Failed to persist session diff artifact: {exc}",
                    exc,
                    operation="engine.workspace",
                    context={"session_id": session_id, "path": str(session.worktree_path)},
                )
            )

    def _persist_diff(self, session: WorktreeSession, session_id: str) -> None:
        """Write the worktree's cumulative diff against its base commit to the session's diff.patch."""
        diff_text = diff_work_tree(session.worktree_path, session.base_commit, git=self.context.git).diff_text
        session_dir = get_session_dir(self.context.paths, session_id)
        redactor = SecretRedactor.from_environment(
            credential_envs=self.context.sensitive_variables, env_file_dir=self.context.paths.root_dir
        )
        write_session_diff(session_dir, diff_text, redactor)

    def finalize_cleanup(
        self,
        manager: Worktree | None,
        session: WorktreeSession | None,
        target_dir: Path,
        status: SessionStatus,
        apply_failed: bool,
        warnings: list[str],
        diagnostics: list[DiagnosticRecord],
    ) -> bool:
        """Clean up or keep the worktree based on run status.

        Paused and apply-failed runs keep the worktree even with --discard.
        """
        if status == SessionStatus.PAUSED or apply_failed:
            kept_path = session.worktree_path if session is not None else target_dir
            safe_notify(self.context.observer, "on_worktree_cleanup", kept=True, path=kept_path)
            return True

        return self.cleanup(manager, session, target_dir, status, warnings, diagnostics)

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

    def prepare_session_artifacts(self) -> ArtifactStore | None:
        """Build the run's ArtifactStore once, or return None without a session."""
        if self.context.session_id is None:
            return None

        return ArtifactStore(Artifacts(self.context.paths), self.context.session_id)

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

    def cleanup_session_tmp_dir(self, session_tmp_dir: Path | None, *, keep: bool, status: SessionStatus) -> None:
        """Best-effort delete of the session scratch directory on completed, unkept runs."""
        if session_tmp_dir is None or keep or status != SessionStatus.COMPLETED:
            return
        try:
            shutil.rmtree(session_tmp_dir)
        except OSError:
            # Best-effort cleanup: scratch directory removal is independent of run outcome.
            pass
