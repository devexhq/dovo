"""Contract tests for worktree/session lifecycle management."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.config import ConfigLoadError
from dovo.core.config.loader import ConfigLoadResult, ConfigLoadStatus
from dovo.core.db import SessionStatus, WorktreesRepository, WorktreeStatus
from dovo.core.git.runner import GitRunner
from dovo.core.project.services.storage import resolve_workspace_paths
from dovo.core.worktree import Worktree, WorktreeApplyResult, WorktreeApplyStatus, WorktreeSession
from dovo.core.worktree.models import WorktreeCreateResult, WorktreeCreateStatus
from dovo.engine.executors.models import ExecutionIdentity
from dovo.engine.models import RunObserver, RunSettings
from dovo.engine.workspace import Workspace
from tests.harness.builders import WorkspaceBuilder


class _RecordingRunObserver(RunObserver):
    """Test double implementing RunObserver, recording worktree lifecycle events in call order."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def on_worktree_ready(self, path: Path, active: bool) -> None:
        self.events.append(("worktree_ready", path, active))

    def on_step_start(self, *args: object, **kwargs: object) -> None:
        pass

    def on_step_output(self, *args: object, **kwargs: object) -> None:
        pass

    def on_step_done(self, *args: object, **kwargs: object) -> None:
        pass

    def on_loop_start(self, *args: object, **kwargs: object) -> None:
        pass

    def on_loop_iteration_start(self, *args: object, **kwargs: object) -> None:
        pass

    def on_loop_conditions_evaluated(self, *args: object, **kwargs: object) -> None:
        pass

    def on_loop_done(self, *args: object, **kwargs: object) -> None:
        pass

    def on_worktree_cleanup(self, kept: bool, path: Path) -> None:
        self.events.append(("worktree_cleanup", kept, path))

    def on_run_started(self, *args: object, **kwargs: object) -> None:
        pass

    def on_run_completed(self, *args: object, **kwargs: object) -> None:
        pass


def _worktree_workspace(tmp_path: Path) -> Path:
    return WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


class WorkspaceSetupTests:
    """[tier-1/integration] Workspace.setup: worktree creation and no-worktree passthrough."""

    def test_no_worktree_returns_resolved_cwd_and_notifies_inactive(self, tmp_path: Path) -> None:
        """[tier-1/integration] setup: use_worktree=False returns (cwd.resolve(), None, None, None) and notifies on_worktree_ready(active=False)."""
        observer = _RecordingRunObserver()
        context = RunSettings(cwd=tmp_path, use_worktree=False, observer=observer, paths=_paths_for(tmp_path))

        target_dir, manager, session, error = Workspace(context).setup()

        assert (target_dir, manager, session, error) == (tmp_path.resolve(), None, None, None)
        assert observer.events == [("worktree_ready", tmp_path.resolve(), False)]

    def test_use_worktree_creates_worktree_and_notifies_active(self, tmp_path: Path) -> None:
        """[tier-1/integration] setup: use_worktree=True creates a real worktree and returns it as target_dir."""
        observer = _RecordingRunObserver()
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root, use_worktree=True, observer=observer, paths=_paths_for(workspace_root)
        )

        target_dir, manager, session, error = Workspace(context).setup()

        assert error is None
        assert manager is not None
        assert session is not None
        assert target_dir == session.worktree_path
        assert target_dir.exists()
        assert observer.events == [("worktree_ready", target_dir, True)]

    def test_fresh_run_worktree_identity_is_session_id_not_blueprint_key(self, tmp_path: Path) -> None:
        """[tier-1/integration] Workspace.setup: RunSettings(session_id='blueprint_367e1e88', identity.blueprint_key='lint') returns a session with session_id 'blueprint_367e1e88', worktree_path == paths.worktree_dir('blueprint_367e1e88').resolve(), target_branch 'dovo/blueprint_367e1e88', a worktrees row with id 'blueprint_367e1e88', and no worktrees_dir/'lint' directory."""
        workspace_root = _worktree_workspace(tmp_path)
        paths = _paths_for(workspace_root)
        context = RunSettings(
            cwd=workspace_root,
            use_worktree=True,
            session_id="blueprint_367e1e88",
            identity=ExecutionIdentity(blueprint_name="Lint", blueprint_key="lint"),
            paths=paths,
        )

        _, _, session, error = Workspace(context).setup()

        assert error is None
        assert session is not None
        assert session.session_id == "blueprint_367e1e88"
        assert session.worktree_path == paths.worktree_dir("blueprint_367e1e88").resolve()
        assert session.target_branch == "dovo/blueprint_367e1e88"
        row = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).get("blueprint_367e1e88")
        assert row is not None
        assert row.branch_name == "dovo/blueprint_367e1e88"
        assert not paths.worktree_dir("lint").exists()

    def test_worktree_creation_failure_returns_error_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] setup: a failed WorktreeCreateResult returns cwd, no manager/session, and an error naming the failure detail."""
        workspace_root = _worktree_workspace(tmp_path)
        failed_result = WorktreeCreateResult(status=WorktreeCreateStatus.GIT_FAILED, errors=["git worktree add failed"])
        monkeypatch.setattr(Worktree, "create", lambda self, *args, **kwargs: failed_result)
        context = RunSettings(cwd=workspace_root, use_worktree=True, paths=_paths_for(workspace_root))

        target_dir, manager, session, error = Workspace(context).setup()

        assert target_dir == workspace_root.resolve()
        assert manager is None
        assert session is None
        assert error == "Git worktree creation failed: git worktree add failed"

    def test_worktree_creation_config_load_error_returns_error_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] setup: manager.create raising ConfigLoadError is caught and returned as an error, not propagated."""
        workspace_root = _worktree_workspace(tmp_path)

        load_result = ConfigLoadResult(
            status=ConfigLoadStatus.MALFORMED_JSON, config_path=workspace_root / "config.json"
        )

        def _raise(self: Worktree, *args: object, **kwargs: object) -> WorktreeCreateResult:
            raise ConfigLoadError("malformed config.json", load_result)

        monkeypatch.setattr(Worktree, "create", _raise)
        context = RunSettings(cwd=workspace_root, use_worktree=True, paths=_paths_for(workspace_root))

        _, manager, session, error = Workspace(context).setup()

        assert manager is None
        assert session is None
        assert error == "Git worktree creation failed: malformed config.json"

    def test_worktree_id_with_use_worktree_false_returns_resolved_cwd(self, tmp_path: Path) -> None:
        """[tier-1/integration] setup: use_worktree=False ignores a retained worktree_id and returns cwd.resolve() without reconstructing a session."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, worktree_id="sess-1", paths=_paths_for(tmp_path))

        target_dir, manager, session, error = Workspace(context).setup()

        assert (target_dir, manager, session, error) == (tmp_path.resolve(), None, None, None)


class WorkspaceRetainedWorktreeTests:
    """[tier-1/integration] Workspace.setup: a retained worktree_id rebuilds its session from the worktrees row."""

    def test_retained_worktree_id_rebuilds_session_from_row(self, tmp_path: Path) -> None:
        """[tier-1/integration] Workspace.setup: RunSettings(worktree_id=X) with an existing directory and a worktrees row returns a WorktreeSession whose target_branch, base_commit, and name equal the row's."""
        workspace_root = _worktree_workspace(tmp_path)
        paths = _paths_for(workspace_root)
        created = Worktree(paths).create(name="retained")
        assert created.session is not None
        retained_id = created.session.session_id
        context = RunSettings(cwd=workspace_root, use_worktree=True, worktree_id=retained_id, paths=paths)

        target_dir, manager, session, error = Workspace(context).setup()

        assert error is None
        assert manager is not None
        assert target_dir == paths.worktree_dir(retained_id)
        assert session is not None
        assert session.session_id == retained_id
        assert session.target_branch == created.session.target_branch
        assert session.base_commit == created.session.base_commit
        assert session.name == "retained"

    @pytest.mark.parametrize(
        ("fault", "expected_error"),
        [
            pytest.param("no-directory", "Git worktree is missing: {path}", id="no-directory"),
            pytest.param("no-record", "Git worktree record is missing: orphan", id="no-record"),
        ],
    )
    def test_retained_worktree_missing_returns_setup_error(
        self, tmp_path: Path, fault: str, expected_error: str
    ) -> None:
        """[tier-1/integration] Workspace.setup: a retained worktree_id with a missing directory returns error "Git worktree is missing: <path>", and with a missing row "Git worktree record is missing: <id>"."""
        workspace_root = _worktree_workspace(tmp_path)
        paths = _paths_for(workspace_root)
        worktree_id = "orphan"
        if fault == "no-record":
            paths.worktree_dir(worktree_id).mkdir(parents=True)
        context = RunSettings(cwd=workspace_root, use_worktree=True, worktree_id=worktree_id, paths=paths)

        target_dir, manager, session, error = Workspace(context).setup()

        assert target_dir == workspace_root.resolve()
        assert manager is None
        assert session is None
        assert error == expected_error.format(path=paths.worktree_dir(worktree_id))


class WorkspaceCleanupTests:
    """[tier-1/integration] Workspace.cleanup: worktree teardown or keep-in-place, never raising."""

    def test_missing_manager_or_session_returns_false_and_notifies_not_kept(self, tmp_path: Path) -> None:
        """[tier-1/integration] cleanup: manager=None or session=None short-circuits, returns False, and notifies kept=False."""
        observer = _RecordingRunObserver()
        context = RunSettings(cwd=tmp_path, use_worktree=False, observer=observer, paths=_paths_for(tmp_path))

        warnings: list[str] = []

        kept = Workspace(context).cleanup(None, None, tmp_path, warnings)

        assert kept is False
        assert observer.events == [("worktree_cleanup", False, tmp_path)]

    def test_keep_true_returns_true_and_notifies_kept_without_removing_worktree(self, tmp_path: Path) -> None:
        """[tier-1/integration] cleanup: context.keep=True returns True and leaves the worktree on disk."""
        observer = _RecordingRunObserver()
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root,
            use_worktree=True,
            keep=True,
            observer=observer,
            paths=_paths_for(workspace_root),
        )
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        warnings: list[str] = []

        kept = Workspace(context).cleanup(manager, session, target_dir, warnings)

        assert kept is True
        assert target_dir.exists()
        assert observer.events[-1] == ("worktree_cleanup", True, session.worktree_path)

    def test_keep_false_removes_worktree_and_returns_false(self, tmp_path: Path) -> None:
        """[tier-1/integration] cleanup: context.keep=False removes the worktree and returns False."""
        observer = _RecordingRunObserver()
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root,
            use_worktree=True,
            keep=False,
            observer=observer,
            paths=_paths_for(workspace_root),
        )
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        warnings: list[str] = []

        kept = Workspace(context).cleanup(manager, session, target_dir, warnings)

        assert kept is False
        assert not target_dir.exists()
        assert observer.events[-1] == ("worktree_cleanup", False, session.worktree_path)

    def test_cleanup_exception_is_swallowed_and_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] cleanup: manager.cleanup raising is a best-effort no-op; cleanup still returns False."""
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(cwd=workspace_root, use_worktree=True, paths=_paths_for(workspace_root))
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        def _raise(self: Worktree, *args: object, **kwargs: object) -> list[str]:
            raise RuntimeError("worktree removal failed")

        monkeypatch.setattr(Worktree, "cleanup", _raise)

        warnings: list[str] = []

        kept = Workspace(context).cleanup(manager, session, target_dir, warnings)

        assert kept is False

    def test_cleanup_of_linked_worktree_preserves_session_contents(self, tmp_path: Path) -> None:
        """[tier-1/integration] Workspace.cleanup: keep=False on a linked worktree with sentinel.txt written into paths.session_dir(id) returns False, removes the worktree, appends no warnings, and sentinel.txt still reads its original content."""
        workspace_root = _worktree_workspace(tmp_path)
        paths = _paths_for(workspace_root)
        context = RunSettings(cwd=workspace_root, use_worktree=True, session_id="cleanup-link", paths=paths)
        workspace = Workspace(context)
        target_dir, manager, session, _ = workspace.setup()
        assert session is not None
        setup_warnings: list[str] = []
        assert workspace.link_session_dir(manager, session, setup_warnings) is None
        sentinel_path = paths.session_dir("cleanup-link") / "sentinel.txt"
        sentinel_path.write_text("preserve me", encoding="utf-8")
        warnings: list[str] = []

        kept = workspace.cleanup(manager, session, target_dir, warnings)

        assert kept is False
        assert warnings == []
        assert not target_dir.exists()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"

    def test_cleanup_unlink_failure_appends_warning_and_still_removes_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] Workspace.cleanup: remove_storage_bridge returning "Failed to unlink worktree storage bridge at '<p>': busy" appends exactly that message to warnings, the worktree directory is still removed, and cleanup returns False."""
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root, use_worktree=True, session_id="cleanup-busy", paths=_paths_for(workspace_root)
        )
        workspace = Workspace(context)
        target_dir, manager, session, _ = workspace.setup()
        assert session is not None
        message = f"Failed to unlink worktree storage bridge at '{target_dir / '.dovo' / 'run'}': busy"
        monkeypatch.setattr("dovo.engine.workspace.remove_storage_bridge", lambda worktree_path: message)
        warnings: list[str] = []

        kept = workspace.cleanup(manager, session, target_dir, warnings)

        assert kept is False
        assert warnings == [message]
        assert not target_dir.exists()


def _commit_source_path(workspace_root: Path, rel_path: str, message: str) -> None:
    """Force-add and commit rel_path in the source repository so new worktrees check it out."""
    GitRunner.run(["add", "-f", rel_path], path=workspace_root)
    GitRunner.run(["commit", "-m", message], path=workspace_root)


class WorkspaceLinkSessionDirTests:
    """[tier-1/integration] Workspace.link_session_dir: linking a fresh run worktree to its session directory."""

    def test_link_after_setup_links_run_to_session_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] Workspace.link_session_dir: after setup() creates a worktree for session_id 'link-1', it returns None with warnings == [] and <worktree>/.dovo/run resolves to paths.session_dir('link-1'), which exists."""
        workspace_root = _worktree_workspace(tmp_path)
        paths = _paths_for(workspace_root)
        workspace = Workspace(RunSettings(cwd=workspace_root, use_worktree=True, session_id="link-1", paths=paths))
        target_dir, manager, session, _ = workspace.setup()
        warnings: list[str] = []

        error = workspace.link_session_dir(manager, session, warnings)

        assert error is None
        assert warnings == []
        assert (target_dir / ".dovo" / "run").resolve() == paths.session_dir("link-1").resolve()
        assert paths.session_dir("link-1").is_dir()

    def test_link_replaces_committed_broken_symlink(self, tmp_path: Path) -> None:
        """[tier-1/integration] Workspace.link_session_dir: a broken symlink committed at .dovo/run in the repository is replaced in the new worktree, returns None, and <worktree>/.dovo/run resolves to paths.session_dir(session_id)."""
        workspace_root = _worktree_workspace(tmp_path)
        (workspace_root / ".dovo" / "run").symlink_to(tmp_path / "missing-session")
        _commit_source_path(workspace_root, ".dovo/run", "Add stale storage bridge")
        paths = _paths_for(workspace_root)
        workspace = Workspace(RunSettings(cwd=workspace_root, use_worktree=True, session_id="link-stale", paths=paths))
        target_dir, manager, session, _ = workspace.setup()

        error = workspace.link_session_dir(manager, session, [])

        assert error is None
        assert (target_dir / ".dovo" / "run").is_symlink()
        assert (target_dir / ".dovo" / "run").resolve() == paths.session_dir("link-stale").resolve()

    def test_link_with_committed_directory_returns_not_a_symlink_error_and_preserves_content(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/integration] Workspace.link_session_dir: a regular directory with sentinel.txt committed at .dovo/run returns "Worktree storage bridge failed: Worktree storage bridge path '<worktree>/.dovo/run' is not a symlink.", removes the worktree directory and the dovo/<id> branch, sets the worktrees row status to WorktreeStatus.CLEANED, notifies the observer of worktree_ready then worktree_cleanup with kept False, and leaves the repository's sentinel.txt and a pre-existing file in the session directory unchanged."""
        workspace_root = _worktree_workspace(tmp_path)
        source_sentinel = workspace_root / ".dovo" / "run" / "sentinel.txt"
        source_sentinel.parent.mkdir(parents=True)
        source_sentinel.write_text("do not delete", encoding="utf-8")
        _commit_source_path(workspace_root, ".dovo/run/sentinel.txt", "Add storage bridge collision")
        paths = _paths_for(workspace_root)
        session_file = paths.session_dir("link-collision") / "session.json"
        session_file.parent.mkdir(parents=True)
        session_file.write_text("preserve me too", encoding="utf-8")
        observer = _RecordingRunObserver()
        workspace = Workspace(
            RunSettings(
                cwd=workspace_root, use_worktree=True, session_id="link-collision", observer=observer, paths=paths
            )
        )
        target_dir, manager, session, _ = workspace.setup()

        error = workspace.link_session_dir(manager, session, [])

        record = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).get("link-collision")
        assert error == (
            f"Worktree storage bridge failed: Worktree storage bridge path '{target_dir / '.dovo' / 'run'}' "
            "is not a symlink."
        )
        assert not target_dir.exists()
        assert observer.events == [("worktree_ready", target_dir, True), ("worktree_cleanup", False, target_dir)]
        assert "dovo/link-collision" not in GitRunner.list_branches(workspace_root)
        assert record is not None
        assert record.status == WorktreeStatus.CLEANED
        assert source_sentinel.read_text(encoding="utf-8") == "do not delete"
        assert session_file.read_text(encoding="utf-8") == "preserve me too"

    def test_link_symlink_failure_returns_prefixed_error_and_discards_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] Workspace.link_session_dir: Path.symlink_to raising OSError("storage device I/O failure") returns a message starting "Worktree storage bridge failed: Unable to create worktree storage bridge at '", removes the worktree directory and the dovo/<id> branch, and sets the worktrees row status to WorktreeStatus.CLEANED."""
        workspace_root = _worktree_workspace(tmp_path)
        paths = _paths_for(workspace_root)
        workspace = Workspace(RunSettings(cwd=workspace_root, use_worktree=True, session_id="link-fail", paths=paths))
        target_dir, manager, session, _ = workspace.setup()

        def _symlink_fails(self: Path, target: Path, target_is_directory: bool = False) -> None:
            raise OSError("storage device I/O failure")

        monkeypatch.setattr(Path, "symlink_to", _symlink_fails)

        error = workspace.link_session_dir(manager, session, [])

        record = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).get("link-fail")
        assert error is not None
        assert error.startswith("Worktree storage bridge failed: Unable to create worktree storage bridge at '")
        assert not target_dir.exists()
        assert "dovo/link-fail" not in GitRunner.list_branches(workspace_root)
        assert record is not None
        assert record.status == WorktreeStatus.CLEANED

    @pytest.mark.parametrize(
        "scenario",
        [
            pytest.param("no-worktree", id="no-worktree"),
            pytest.param("retained-worktree", id="retained-worktree"),
            pytest.param("no-session-id", id="no-session-id"),
        ],
    )
    def test_link_without_fresh_run_worktree_returns_none_and_creates_no_link(
        self, tmp_path: Path, scenario: str
    ) -> None:
        """[tier-1/integration] Workspace.link_session_dir: use_worktree=False, a retained worktree_id, or session_id=None returns None, appends no warnings, and no <worktree>/.dovo/run exists."""
        workspace_root = _worktree_workspace(tmp_path)
        paths = _paths_for(workspace_root)
        if scenario == "no-worktree":
            context = RunSettings(cwd=workspace_root, use_worktree=False, session_id="link-skip", paths=paths)
        elif scenario == "retained-worktree":
            created = Worktree(
                paths, db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
            ).create(session_id="retained")
            assert created.session is not None
            context = RunSettings(
                cwd=workspace_root,
                use_worktree=True,
                session_id="link-skip",
                worktree_id=created.session.session_id,
                paths=paths,
            )
        else:
            context = RunSettings(cwd=workspace_root, use_worktree=True, session_id=None, paths=paths)
        workspace = Workspace(context)
        target_dir, manager, session, _ = workspace.setup()
        warnings: list[str] = []

        error = workspace.link_session_dir(manager, session, warnings)

        assert error is None
        assert warnings == []
        assert not (target_dir / ".dovo" / "run").is_symlink()
        assert not (target_dir / ".dovo" / "run").exists()


class WorkspaceHandleAutoApplyTests:
    """[tier-1/integration] Workspace.handle_auto_apply: applying worktree changes when auto_apply is enabled."""

    def test_auto_apply_disabled_returns_none_and_false(self, tmp_path: Path) -> None:
        """[tier-1/integration] handle_auto_apply: context.auto_apply=False returns (None, False) without calling manager.apply."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, auto_apply=False, paths=_paths_for(tmp_path))
        errors: list[str] = []
        warnings: list[str] = []

        new_status, apply_failed = Workspace(context).handle_auto_apply(None, None, errors, warnings)

        assert (new_status, apply_failed) == (None, False)
        assert (errors, warnings) == ([], [])

    def test_auto_apply_success_returns_none_and_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/integration] handle_auto_apply: a successful apply returns (None, False) and appends no errors."""
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(cwd=workspace_root, use_worktree=True, auto_apply=True, paths=_paths_for(workspace_root))
        _, manager, session, _ = Workspace(context).setup()
        assert session is not None
        ok_result = WorktreeApplyResult(worktree_id=session.session_id, status=WorktreeApplyStatus.OK)
        monkeypatch.setattr(Worktree, "apply", lambda self, *args, **kwargs: ok_result)
        errors: list[str] = []
        warnings: list[str] = []

        new_status, apply_failed = Workspace(context).handle_auto_apply(manager, session, errors, warnings)

        assert (new_status, apply_failed) == (None, False)
        assert errors == []

    def test_auto_apply_conflict_returns_failed_status_and_appends_errors_and_warnings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] handle_auto_apply: a conflicting apply returns (SessionStatus.FAILED, True) and extends errors/warnings from the apply result."""
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(cwd=workspace_root, use_worktree=True, auto_apply=True, paths=_paths_for(workspace_root))
        _, manager, session, _ = Workspace(context).setup()
        assert session is not None
        conflict_result = WorktreeApplyResult(
            worktree_id=session.session_id,
            status=WorktreeApplyStatus.CONFLICT,
            errors=["merge conflict"],
            warnings=["hunk rejected"],
        )
        monkeypatch.setattr(Worktree, "apply", lambda self, *args, **kwargs: conflict_result)
        errors: list[str] = []
        warnings: list[str] = []

        new_status, apply_failed = Workspace(context).handle_auto_apply(manager, session, errors, warnings)

        assert (new_status, apply_failed) == (SessionStatus.FAILED, True)
        assert errors == ["merge conflict"]
        assert warnings == ["hunk rejected"]


class WorkspaceCaptureAndPersistDiffTests:
    """[tier-1/integration] Workspace.capture_and_persist_diff: cumulative worktree diff persistence."""

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
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root, use_worktree=True, session_id=session_id, paths=_paths_for(workspace_root)
        )
        session = None
        if session_present:
            _, _, session, _ = Workspace(context).setup()
        warnings: list[str] = []

        Workspace(context).capture_and_persist_diff(session, warnings)

        assert warnings == []

    def test_writes_diff_patch_under_session_directory(self, tmp_path: Path) -> None:
        """[tier-1/integration] capture_and_persist_diff: with an active worktree and session_id set, writes diff.patch under the resolved session directory."""
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root, use_worktree=True, session_id="sess-1", paths=_paths_for(workspace_root)
        )
        _, _, session, _ = Workspace(context).setup()
        assert session is not None
        (session.worktree_path / "new_file.txt").write_text("hello\n", encoding="utf-8")
        warnings: list[str] = []

        Workspace(context).capture_and_persist_diff(session, warnings)

        session_dir = _paths_for(workspace_root).session_dir("sess-1")
        assert (session_dir / "diff.patch").exists()
        assert warnings == []

    @pytest.mark.parametrize(
        ("names", "expected_line", "value_visible"),
        [
            pytest.param(("DB_PIN",), "+pin=[REDACTED:DB_PIN]", False, id="listed_name_masked"),
            pytest.param((), "+pin=1234", True, id="unlisted_name_kept"),
        ],
    )
    def test_capture_and_persist_diff_masks_listed_name_in_patch_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        names: tuple[str, ...],
        expected_line: str,
        value_visible: bool,
    ) -> None:
        """[tier-1/integration] capture_and_persist_diff: RunSettings(sensitive_variables=("DB_PIN",)), DB_PIN="1234", worktree file adding "pin=1234" yields diff.patch containing "+pin=[REDACTED:DB_PIN]" and not "1234"; with sensitive_variables=() it contains "+pin=1234"."""
        monkeypatch.setenv("DB_PIN", "1234")
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root,
            use_worktree=True,
            session_id="sess-1",
            sensitive_variables=names,
            paths=_paths_for(workspace_root),
        )
        _, _, session, _ = Workspace(context).setup()
        assert session is not None
        (session.worktree_path / "new_file.txt").write_text("pin=1234\n", encoding="utf-8")

        Workspace(context).capture_and_persist_diff(session, [])

        patch_text = (_paths_for(workspace_root).session_dir("sess-1") / "diff.patch").read_text(encoding="utf-8")
        assert expected_line in patch_text
        assert ("1234" in patch_text) is value_visible

    def test_capture_and_persist_diff_masks_worktree_secret_in_patch_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] capture_and_persist_diff: a worktree file adding "token=s3cr3t-value" with SVC_TOKEN="s3cr3t-value" yields a diff.patch containing "[REDACTED:SVC_TOKEN]" and not "s3cr3t-value"."""
        monkeypatch.setenv("SVC_TOKEN", "s3cr3t-value")
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root, use_worktree=True, session_id="sess-1", paths=_paths_for(workspace_root)
        )
        _, _, session, _ = Workspace(context).setup()
        assert session is not None
        (session.worktree_path / "new_file.txt").write_text("token=s3cr3t-value\n", encoding="utf-8")
        warnings: list[str] = []

        Workspace(context).capture_and_persist_diff(session, warnings)

        patch_text = (_paths_for(workspace_root).session_dir("sess-1") / "diff.patch").read_text(encoding="utf-8")
        assert "+token=[REDACTED:SVC_TOKEN]" in patch_text
        assert "s3cr3t-value" not in patch_text
        assert warnings == []

    def test_git_failure_appends_warning_instead_of_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/integration] capture_and_persist_diff: a git failure while capturing the diff appends a warning naming the failure."""
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root, use_worktree=True, session_id="sess-1", paths=_paths_for(workspace_root)
        )
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

    def test_paused_status_keeps_worktree_and_notifies(self, tmp_path: Path) -> None:
        """[tier-1/integration] finalize_cleanup: SessionStatus.PAUSED keeps the worktree regardless of context.keep."""
        observer = _RecordingRunObserver()
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(
            cwd=workspace_root,
            use_worktree=True,
            keep=False,
            observer=observer,
            paths=_paths_for(workspace_root),
        )
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        warnings: list[str] = []

        kept = Workspace(context).finalize_cleanup(
            manager, session, target_dir, SessionStatus.PAUSED, apply_failed=False, warnings=warnings
        )

        assert kept is True
        assert target_dir.exists()
        assert observer.events[-1] == ("worktree_cleanup", True, session.worktree_path)

    def test_apply_failed_keeps_worktree_even_when_completed(self, tmp_path: Path) -> None:
        """[tier-1/integration] finalize_cleanup: apply_failed=True keeps the worktree even for SessionStatus.COMPLETED."""
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(cwd=workspace_root, use_worktree=True, keep=False, paths=_paths_for(workspace_root))
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        warnings: list[str] = []

        kept = Workspace(context).finalize_cleanup(
            manager, session, target_dir, SessionStatus.COMPLETED, apply_failed=True, warnings=warnings
        )

        assert kept is True
        assert target_dir.exists()

    def test_completed_status_without_apply_failure_delegates_to_cleanup(self, tmp_path: Path) -> None:
        """[tier-1/integration] finalize_cleanup: SessionStatus.COMPLETED with apply_failed=False removes the worktree per Workspace.cleanup."""
        workspace_root = _worktree_workspace(tmp_path)
        context = RunSettings(cwd=workspace_root, use_worktree=True, keep=False, paths=_paths_for(workspace_root))
        target_dir, manager, session, _ = Workspace(context).setup()
        assert session is not None

        warnings: list[str] = []

        kept = Workspace(context).finalize_cleanup(
            manager, session, target_dir, SessionStatus.COMPLETED, apply_failed=False, warnings=warnings
        )

        assert kept is False
        assert not target_dir.exists()


class WorkspacePrepareSessionTmpDirTests:
    """[tier-1/unit] Workspace.prepare_session_tmp_dir: session scratch directory creation."""

    def test_no_session_id_returns_none(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_tmp_dir: context.session_id=None returns None without touching disk."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, session_id=None, paths=_paths_for(tmp_path))
        warnings: list[str] = []

        result = Workspace(context).prepare_session_tmp_dir(warnings)

        assert result is None
        assert warnings == []

    def test_creates_steps_subdirectory_and_returns_session_tmp_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_tmp_dir: creates <tmp_dir>/<session_id>/steps and returns <tmp_dir>/<session_id>."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, session_id="session-abc", paths=_paths_for(tmp_path))
        warnings: list[str] = []

        result = Workspace(context).prepare_session_tmp_dir(warnings)

        assert result == _paths_for(tmp_path).tmp_dir / "session-abc"
        assert result is not None
        assert (result / "steps").is_dir()
        assert warnings == []

    def test_mkdir_failure_appends_warning_and_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] prepare_session_tmp_dir: an OSError creating the scratch directory appends a warning and returns None."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, session_id="session-abc", paths=_paths_for(tmp_path))

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
        context = RunSettings(cwd=tmp_path, use_worktree=False, session_id=None, paths=_paths_for(tmp_path))

        artifacts_dir, artifacts_db = Workspace(context).prepare_session_artifacts()

        assert (artifacts_dir, artifacts_db) == (None, None)

    def test_session_id_set_returns_artifacts_dir_and_repository(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_artifacts: context.session_id set returns the resolved artifacts_dir and a bound ArtifactsRepository."""
        workspace_root = WorkspaceBuilder(tmp_path / "artifacts_workspace").with_database().build()
        paths = _paths_for(workspace_root)
        context = RunSettings(cwd=workspace_root, use_worktree=False, session_id="session-abc", paths=paths)

        artifacts_dir, artifacts_db = Workspace(context).prepare_session_artifacts()

        assert artifacts_dir == context.paths.artifacts_dir
        assert artifacts_db is not None
        assert artifacts_db.db_path == context.paths.database_file
        assert artifacts_db.project_id == context.paths.project_id


class WorkspaceSinglePathResolutionTests:
    """[tier-2/unit] Session preparation uses the RunSettings path snapshot exclusively."""

    def test_prepare_session_methods_use_context_paths_without_reresolving(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All four preparation methods run without invoking the workspace-path resolver."""
        workspace_root = WorkspaceBuilder(tmp_path / "path_snapshot_workspace").with_git().with_database().build()
        paths = _paths_for(workspace_root)
        context = RunSettings(cwd=workspace_root, use_worktree=False, session_id="path-snapshot", paths=paths)

        def _unexpected_resolution(*args: object, **kwargs: object) -> WorkspacePaths:
            raise AssertionError("workspace paths must be resolved once at the command boundary")

        monkeypatch.setattr("dovo.core.project.services.storage.resolve_workspace_paths", _unexpected_resolution)
        workspace = Workspace(context)
        warnings: list[str] = []
        session = WorktreeSession(
            session_id="path-snapshot",
            target_branch="main",
            worktree_path=workspace_root,
            base_commit="HEAD",
            created_at="",
        )

        assert workspace.prepare_session_tmp_dir(warnings) == paths.tmp_dir / "path-snapshot"
        artifacts_dir, artifacts_db = workspace.prepare_session_artifacts()
        assert artifacts_dir == paths.artifacts_dir
        assert artifacts_db is not None
        assert workspace.prepare_session_log_dir(warnings) == paths.logs_dir / "path-snapshot"
        workspace.capture_and_persist_diff(session, warnings)
        assert warnings == []


class WorkspacePrepareSessionLogDirTests:
    """[tier-1/unit] Workspace.prepare_session_log_dir: session log directory creation."""

    def test_no_session_id_returns_none(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_log_dir: context.session_id=None returns None without touching disk."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, session_id=None, paths=_paths_for(tmp_path))
        warnings: list[str] = []

        result = Workspace(context).prepare_session_log_dir(warnings)

        assert result is None
        assert warnings == []

    def test_creates_and_returns_session_log_dir(self, tmp_path: Path) -> None:
        """[tier-1/unit] prepare_session_log_dir: creates and returns <logs_dir>/<session_id>."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, session_id="session-abc", paths=_paths_for(tmp_path))
        warnings: list[str] = []

        result = Workspace(context).prepare_session_log_dir(warnings)

        assert result == _paths_for(tmp_path).logs_dir / "session-abc"
        assert result is not None
        assert result.is_dir()
        assert warnings == []

    def test_mkdir_failure_appends_warning_and_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] prepare_session_log_dir: an OSError creating the log directory appends a warning and returns None."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, session_id="session-abc", paths=_paths_for(tmp_path))

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
            pytest.param(False, False, SessionStatus.COMPLETED, id="no_directory"),
            pytest.param(True, True, SessionStatus.COMPLETED, id="keep_true"),
            pytest.param(True, False, SessionStatus.FAILED, id="not_completed"),
        ],
    )
    def test_noop_branches_leave_directory_untouched(
        self, tmp_path: Path, session_tmp_dir_present: bool, keep: bool, status: SessionStatus
    ) -> None:
        """[tier-1/unit] cleanup_session_tmp_dir: session_tmp_dir=None, keep=True, or a non-COMPLETED status all skip removal."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))
        session_tmp_dir = None
        if session_tmp_dir_present:
            session_tmp_dir = tmp_path / "scratch"
            session_tmp_dir.mkdir()

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=keep, status=status)

        if session_tmp_dir is not None:
            assert session_tmp_dir.exists()

    def test_completed_and_not_kept_removes_directory(self, tmp_path: Path) -> None:
        """[tier-1/unit] cleanup_session_tmp_dir: SessionStatus.COMPLETED with keep=False deletes the scratch directory tree."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))
        session_tmp_dir = tmp_path / "scratch"
        session_tmp_dir.mkdir()

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=False, status=SessionStatus.COMPLETED)

        assert not session_tmp_dir.exists()

    def test_removal_failure_is_swallowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] cleanup_session_tmp_dir: an OSError removing the directory is swallowed, never raised."""
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=_paths_for(tmp_path))
        session_tmp_dir = tmp_path / "scratch"
        session_tmp_dir.mkdir()

        def _raise(*args: object, **kwargs: object) -> None:
            raise OSError("busy")

        monkeypatch.setattr(shutil, "rmtree", _raise)

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=False, status=SessionStatus.COMPLETED)

        assert session_tmp_dir.exists()
