"""Contract tests for sandbox/session lifecycle management."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.harness.builders import WorkspaceBuilder
from worktree.core.config import ConfigLoadError
from worktree.core.config.loader import ConfigLoadResult, ConfigLoadStatus
from worktree.core.db import RunStatus
from worktree.core.db.repositories.artifacts import ArtifactsRepository
from worktree.core.git.runner import GitRunner
from worktree.core.project.services.storage import resolve_project_filesystem_paths
from worktree.core.runtime.models import RunCheckpoint, RunContext, RunObserver
from worktree.core.runtime.workspace import Workspace
from worktree.core.sandbox import Sandbox, SandboxApplyResult, SandboxApplyStatus
from worktree.core.sandbox.models import SandboxCreateResult, SandboxCreateStatus


class _RecordingRunObserver(RunObserver):
    """Test double implementing RunObserver, recording sandbox lifecycle events in call order."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def on_sandbox_ready(self, path: Path, active: bool) -> None:
        self.events.append(("sandbox_ready", path, active))

    def on_step_start(self, *args: object, **kwargs: object) -> None:
        pass

    def on_step_output(self, *args: object, **kwargs: object) -> None:
        pass

    def on_step_done(self, *args: object, **kwargs: object) -> None:
        pass

    def on_loop_start(self, *args: object, **kwargs: object) -> None:
        pass

    def on_loop_turn_start(self, *args: object, **kwargs: object) -> None:
        pass

    def on_loop_conditions_evaluated(self, *args: object, **kwargs: object) -> None:
        pass

    def on_loop_done(self, *args: object, **kwargs: object) -> None:
        pass

    def on_sandbox_cleanup(self, kept: bool, path: Path) -> None:
        self.events.append(("sandbox_cleanup", kept, path))


def _sandboxed_workspace(tmp_path: Path) -> Path:
    return WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()


class WorkspaceSetupTests:
    """[tier-1/integration] Workspace.setup: sandbox creation, resume reconstruction, and no-sandbox passthrough."""

    def test_no_sandbox_returns_resolved_cwd_and_notifies_inactive(self, tmp_path: Path) -> None:
        """[tier-1/integration] setup: use_sandbox=False returns (cwd.resolve(), None, None, None) and notifies on_sandbox_ready(active=False)."""
        observer = _RecordingRunObserver()
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, observer=observer)

        target_dir, manager, session, error = Workspace(context).setup()

        assert (target_dir, manager, session, error) == (tmp_path.resolve(), None, None, None)
        assert observer.events == [("sandbox_ready", tmp_path.resolve(), False)]

    def test_use_sandbox_creates_worktree_and_notifies_active(self, tmp_path: Path) -> None:
        """[tier-1/integration] setup: use_sandbox=True creates a real sandbox worktree and returns it as target_dir."""
        observer = _RecordingRunObserver()
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, observer=observer)

        target_dir, manager, session, error = Workspace(context).setup()

        assert error is None
        assert manager is not None
        assert session is not None
        assert target_dir == session.sandbox_path
        assert target_dir.exists()
        assert observer.events == [("sandbox_ready", target_dir, True)]

    def test_sandbox_creation_failure_returns_error_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] setup: a failed SandboxCreateResult returns cwd, no manager/session, and an error naming the failure detail."""
        workspace_root = _sandboxed_workspace(tmp_path)
        failed_result = SandboxCreateResult(status=SandboxCreateStatus.GIT_FAILED, errors=["git worktree add failed"])
        monkeypatch.setattr(Sandbox, "create", lambda self, *args, **kwargs: failed_result)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True)

        target_dir, manager, session, error = Workspace(context).setup()

        assert target_dir == workspace_root.resolve()
        assert manager is None
        assert session is None
        assert error == "Git sandbox creation failed: git worktree add failed"

    def test_sandbox_creation_config_load_error_returns_error_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] setup: manager.create raising ConfigLoadError is caught and returned as an error, not propagated."""
        workspace_root = _sandboxed_workspace(tmp_path)

        load_result = ConfigLoadResult(
            status=ConfigLoadStatus.MALFORMED_JSON, config_path=workspace_root / "config.json"
        )

        def _raise(self: Sandbox, *args: object, **kwargs: object) -> SandboxCreateResult:
            raise ConfigLoadError("malformed config.json", load_result)

        monkeypatch.setattr(Sandbox, "create", _raise)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True)

        _, manager, session, error = Workspace(context).setup()

        assert manager is None
        assert session is None
        assert error == "Git sandbox creation failed: malformed config.json"

    def test_resume_without_sandbox_returns_resolved_cwd(self, tmp_path: Path) -> None:
        """[tier-1/integration] setup: resume_from with use_sandbox=False returns cwd.resolve() without reconstructing a session."""
        checkpoint = RunCheckpoint(next_step_index=0, pending_step_id="s1", diagnostic="d", use_sandbox=False)
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=True, resume_from=checkpoint)

        target_dir, manager, session, error = Workspace(context).setup()

        assert (target_dir, manager, session, error) == (tmp_path.resolve(), None, None, None)

    def test_resume_with_missing_sandbox_path_returns_error(self, tmp_path: Path) -> None:
        """[tier-1/integration] setup: resume_from pointing at a sandbox_path that no longer exists on disk returns an error naming the missing path."""
        missing_path = tmp_path / "gone"
        checkpoint = RunCheckpoint(
            next_step_index=0,
            pending_step_id="s1",
            diagnostic="d",
            use_sandbox=True,
            sandbox_path=str(missing_path),
        )
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=True, resume_from=checkpoint)

        target_dir, manager, session, error = Workspace(context).setup()

        assert target_dir == tmp_path.resolve()
        assert manager is None
        assert session is None
        assert error == f"Git sandbox is missing: {missing_path}"

    def test_resume_with_existing_sandbox_path_reconstructs_session(self, tmp_path: Path) -> None:
        """[tier-1/integration] setup: resume_from pointing at an existing sandbox_path reconstructs a SandboxSession from the checkpoint fields."""
        sandbox_dir = tmp_path / "sandbox"
        sandbox_dir.mkdir()
        checkpoint = RunCheckpoint(
            next_step_index=0,
            pending_step_id="s1",
            diagnostic="d",
            use_sandbox=True,
            sandbox_path=str(sandbox_dir),
            sandbox_id="sess-1",
            sandbox_name="my-sandbox",
            sandbox_branch="worktree/sandbox-sess-1",
            sandbox_base_commit="abc123",
        )
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=True, resume_from=checkpoint)

        target_dir, manager, session, error = Workspace(context).setup()

        assert error is None
        assert manager is not None
        assert target_dir == sandbox_dir
        assert session is not None
        assert session.session_id == "sess-1"
        assert session.name == "my-sandbox"
        assert session.target_branch == "worktree/sandbox-sess-1"
        assert session.base_commit == "abc123"


class WorkspaceCleanupTests:
    """[tier-1/integration] Workspace.cleanup: sandbox teardown or keep-in-place, never raising."""

    def test_missing_manager_or_session_returns_false_and_notifies_not_kept(self, tmp_path: Path) -> None:
        """[tier-1/integration] cleanup: manager=None or session=None short-circuits, returns False, and notifies kept=False."""
        observer = _RecordingRunObserver()
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, observer=observer)

        kept = Workspace(context).cleanup(None, None, tmp_path)

        assert kept is False
        assert observer.events == [("sandbox_cleanup", False, tmp_path)]

    def test_keep_true_returns_true_and_notifies_kept_without_removing_worktree(self, tmp_path: Path) -> None:
        """[tier-1/integration] cleanup: context.keep=True returns True and leaves the sandbox worktree on disk."""
        observer = _RecordingRunObserver()
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, keep=True, observer=observer)
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        kept = Workspace(context).cleanup(manager, session, target_dir)

        assert kept is True
        assert target_dir.exists()
        assert observer.events[-1] == ("sandbox_cleanup", True, session.sandbox_path)

    def test_keep_false_removes_worktree_and_returns_false(self, tmp_path: Path) -> None:
        """[tier-1/integration] cleanup: context.keep=False removes the sandbox worktree and returns False."""
        observer = _RecordingRunObserver()
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, keep=False, observer=observer)
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        kept = Workspace(context).cleanup(manager, session, target_dir)

        assert kept is False
        assert not target_dir.exists()
        assert observer.events[-1] == ("sandbox_cleanup", False, session.sandbox_path)

    def test_cleanup_exception_is_swallowed_and_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] cleanup: manager.cleanup raising is a best-effort no-op; cleanup still returns False."""
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True)
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        def _raise(self: Sandbox, *args: object, **kwargs: object) -> list[str]:
            raise RuntimeError("worktree removal failed")

        monkeypatch.setattr(Sandbox, "cleanup", _raise)

        kept = Workspace(context).cleanup(manager, session, target_dir)

        assert kept is False


class WorkspaceHandleAutoApplyTests:
    """[tier-1/integration] Workspace.handle_auto_apply: applying sandbox changes when auto_apply is enabled."""

    def test_auto_apply_disabled_returns_none_and_false(self, tmp_path: Path) -> None:
        """[tier-1/integration] handle_auto_apply: context.auto_apply=False returns (None, False) without calling manager.apply."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, auto_apply=False)
        errors: list[str] = []
        warnings: list[str] = []

        new_status, apply_failed = Workspace(context).handle_auto_apply(None, None, errors, warnings)

        assert (new_status, apply_failed) == (None, False)
        assert (errors, warnings) == ([], [])

    def test_auto_apply_success_returns_none_and_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/integration] handle_auto_apply: a successful apply returns (None, False) and appends no errors."""
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, auto_apply=True)
        _, manager, session, _ = Workspace(context).setup()
        assert session is not None
        ok_result = SandboxApplyResult(sandbox_id=session.session_id, status=SandboxApplyStatus.OK)
        monkeypatch.setattr(Sandbox, "apply", lambda self, *args, **kwargs: ok_result)
        errors: list[str] = []
        warnings: list[str] = []

        new_status, apply_failed = Workspace(context).handle_auto_apply(manager, session, errors, warnings)

        assert (new_status, apply_failed) == (None, False)
        assert errors == []

    def test_auto_apply_conflict_returns_failed_status_and_appends_errors_and_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] handle_auto_apply: a conflicting apply returns (RunStatus.FAILED, True) and extends errors/warnings from the apply result."""
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, auto_apply=True)
        _, manager, session, _ = Workspace(context).setup()
        assert session is not None
        conflict_result = SandboxApplyResult(
            sandbox_id=session.session_id,
            status=SandboxApplyStatus.CONFLICT,
            errors=["merge conflict"],
            warnings=["hunk rejected"],
        )
        monkeypatch.setattr(Sandbox, "apply", lambda self, *args, **kwargs: conflict_result)
        errors: list[str] = []
        warnings: list[str] = []

        new_status, apply_failed = Workspace(context).handle_auto_apply(manager, session, errors, warnings)

        assert (new_status, apply_failed) == (RunStatus.FAILED, True)
        assert errors == ["merge conflict"]
        assert warnings == ["hunk rejected"]


class WorkspaceCaptureAndPersistDiffTests:
    """[tier-1/integration] Workspace.capture_and_persist_diff: cumulative sandbox diff persistence."""

    @pytest.mark.parametrize(
        ("session_present", "session_id"),
        [
            pytest.param(False, "sess-1", id="no_session"),
            pytest.param(True, None, id="no_session_id"),
        ],
    )
    def test_missing_session_or_session_id_is_noop(
        self, tmp_path: Path, session_present: bool, session_id: str | None
    ) -> None:
        """[tier-1/integration] capture_and_persist_diff: session=None or context.session_id=None writes nothing and appends no warnings."""
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, session_id=session_id)
        session = None
        if session_present:
            _, _, session, _ = Workspace(context).setup()
        warnings: list[str] = []

        Workspace(context).capture_and_persist_diff(session, warnings)

        assert warnings == []

    def test_writes_diff_patch_under_session_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] capture_and_persist_diff: with an active sandbox and session_id set, writes diff.patch under the resolved session directory."""
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, session_id="sess-1")
        _, _, session, _ = Workspace(context).setup()
        assert session is not None
        (session.sandbox_path / "new_file.txt").write_text("hello\n", encoding="utf-8")
        warnings: list[str] = []

        Workspace(context).capture_and_persist_diff(session, warnings)

        session_dir = resolve_project_filesystem_paths(workspace_root).session_dir("sess-1")
        assert (session_dir / "diff.patch").exists()
        assert warnings == []

    def test_git_failure_appends_warning_instead_of_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] capture_and_persist_diff: a git failure while capturing the diff appends a warning naming the failure."""
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, session_id="sess-1")
        _, _, session, _ = Workspace(context).setup()
        assert session is not None

        def _raise(*args: object, **kwargs: object) -> str:
            raise RuntimeError("git diff failed")

        monkeypatch.setattr(GitRunner, "diff", staticmethod(_raise))
        warnings: list[str] = []

        Workspace(context).capture_and_persist_diff(session, warnings)

        assert len(warnings) == 1
        assert "git diff failed" in warnings[0]


class WorkspaceFinalizeCleanupTests:
    """[tier-1/integration] Workspace.finalize_cleanup: keep-on-pause/apply-failure vs delegate-to-cleanup."""

    def test_paused_status_keeps_sandbox_and_notifies(self, tmp_path: Path) -> None:
        """[tier-1/integration] finalize_cleanup: RunStatus.PAUSED keeps the sandbox regardless of context.keep."""
        observer = _RecordingRunObserver()
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, keep=False, observer=observer)
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        kept = Workspace(context).finalize_cleanup(manager, session, target_dir, RunStatus.PAUSED, apply_failed=False)

        assert kept is True
        assert target_dir.exists()
        assert observer.events[-1] == ("sandbox_cleanup", True, session.sandbox_path)

    def test_apply_failed_keeps_sandbox_even_when_completed(self, tmp_path: Path) -> None:
        """[tier-1/integration] finalize_cleanup: apply_failed=True keeps the sandbox even for RunStatus.COMPLETED."""
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, keep=False)
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        kept = Workspace(context).finalize_cleanup(manager, session, target_dir, RunStatus.COMPLETED, apply_failed=True)

        assert kept is True
        assert target_dir.exists()

    def test_completed_status_without_apply_failure_delegates_to_cleanup(self, tmp_path: Path) -> None:
        """[tier-1/integration] finalize_cleanup: RunStatus.COMPLETED with apply_failed=False removes the sandbox per Workspace.cleanup."""
        workspace_root = _sandboxed_workspace(tmp_path)
        context = RunContext(steps=[], cwd=workspace_root, use_sandbox=True, keep=False)
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        kept = Workspace(context).finalize_cleanup(
            manager, session, target_dir, RunStatus.COMPLETED, apply_failed=False
        )

        assert kept is False
        assert not target_dir.exists()


class WorkspacePrepareSessionTmpDirTests:
    """[tier-1/unit] Workspace.prepare_session_tmp_dir: session scratch directory creation."""

    def test_no_session_id_returns_none(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_tmp_dir: context.session_id=None returns None without touching disk."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id=None)
        warnings: list[str] = []

        result = Workspace(context).prepare_session_tmp_dir(warnings)

        assert result is None
        assert warnings == []

    def test_creates_steps_subdirectory_and_returns_session_tmp_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_tmp_dir: creates <tmp_dir>/<session_id>/steps and returns <tmp_dir>/<session_id>."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id="session-abc")
        warnings: list[str] = []

        result = Workspace(context).prepare_session_tmp_dir(warnings)

        assert result == resolve_project_filesystem_paths(tmp_path).tmp_dir / "session-abc"
        assert result is not None
        assert (result / "steps").is_dir()
        assert warnings == []

    def test_mkdir_failure_appends_warning_and_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] prepare_session_tmp_dir: an OSError creating the scratch directory appends a warning and returns None."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id="session-abc")

        def _raise(self: Path, *args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _raise)
        warnings: list[str] = []

        result = Workspace(context).prepare_session_tmp_dir(warnings)

        assert result is None
        assert len(warnings) == 1
        assert "disk full" in warnings[0]


class WorkspacePrepareSessionArtifactsTests:
    """[tier-1/unit] Workspace.prepare_session_artifacts: per-run ArtifactsRepository construction."""

    def test_no_session_id_returns_none_none(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_artifacts: context.session_id=None returns (None, None)."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id=None)

        artifacts_dir, artifacts_db = Workspace(context).prepare_session_artifacts()

        assert (artifacts_dir, artifacts_db) == (None, None)

    def test_session_id_set_returns_artifacts_dir_and_repository(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_artifacts: context.session_id set returns the resolved artifacts_dir and a bound ArtifactsRepository."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id="session-abc")

        artifacts_dir, artifacts_db = Workspace(context).prepare_session_artifacts()

        assert artifacts_dir == resolve_project_filesystem_paths(tmp_path).artifacts_dir
        assert isinstance(artifacts_db, ArtifactsRepository)


class WorkspacePrepareSessionLogDirTests:
    """[tier-1/unit] Workspace.prepare_session_log_dir: session log directory creation."""

    def test_no_session_id_returns_none(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_log_dir: context.session_id=None returns None without touching disk."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id=None)
        warnings: list[str] = []

        result = Workspace(context).prepare_session_log_dir(warnings)

        assert result is None
        assert warnings == []

    def test_creates_and_returns_session_log_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_log_dir: creates and returns <logs_dir>/<session_id>."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id="session-abc")
        warnings: list[str] = []

        result = Workspace(context).prepare_session_log_dir(warnings)

        assert result == resolve_project_filesystem_paths(tmp_path).logs_dir / "session-abc"
        assert result is not None
        assert result.is_dir()
        assert warnings == []

    def test_mkdir_failure_appends_warning_and_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] prepare_session_log_dir: an OSError creating the log directory appends a warning and returns None."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False, session_id="session-abc")

        def _raise(self: Path, *args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _raise)
        warnings: list[str] = []

        result = Workspace(context).prepare_session_log_dir(warnings)

        assert result is None
        assert len(warnings) == 1
        assert "disk full" in warnings[0]


class WorkspaceCleanupSessionTmpDirTests:
    """[tier-1/unit] Workspace.cleanup_session_tmp_dir: best-effort scratch directory removal."""

    @pytest.mark.parametrize(
        ("session_tmp_dir_present", "keep", "status"),
        [
            pytest.param(False, False, RunStatus.COMPLETED, id="no_directory"),
            pytest.param(True, True, RunStatus.COMPLETED, id="keep_true"),
            pytest.param(True, False, RunStatus.FAILED, id="not_completed"),
        ],
    )
    def test_noop_branches_leave_directory_untouched(
        self, tmp_path: Path, session_tmp_dir_present: bool, keep: bool, status: RunStatus
    ) -> None:
        """[tier-1/unit] cleanup_session_tmp_dir: session_tmp_dir=None, keep=True, or a non-COMPLETED status all skip removal."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False)
        session_tmp_dir = None
        if session_tmp_dir_present:
            session_tmp_dir = tmp_path / "scratch"
            session_tmp_dir.mkdir()

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=keep, status=status)

        if session_tmp_dir is not None:
            assert session_tmp_dir.exists()

    def test_completed_and_not_kept_removes_directory(self, tmp_path: Path) -> None:
        """[tier-1/unit] cleanup_session_tmp_dir: RunStatus.COMPLETED with keep=False deletes the scratch directory tree."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False)
        session_tmp_dir = tmp_path / "scratch"
        session_tmp_dir.mkdir()

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=False, status=RunStatus.COMPLETED)

        assert not session_tmp_dir.exists()

    def test_removal_failure_is_swallowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] cleanup_session_tmp_dir: an OSError removing the directory is swallowed, never raised."""
        context = RunContext(steps=[], cwd=tmp_path, use_sandbox=False)
        session_tmp_dir = tmp_path / "scratch"
        session_tmp_dir.mkdir()

        def _raise(*args: object, **kwargs: object) -> None:
            raise OSError("busy")

        monkeypatch.setattr(shutil, "rmtree", _raise)

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=False, status=RunStatus.COMPLETED)

        assert session_tmp_dir.exists()
