"""Contract tests for worktree/session lifecycle management."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.lock import LockTimeoutError
from dovo.core.agents import AgentInvocationContext, allocate_invocation_paths, new_invocation_id
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.config import ConfigLoadError
from dovo.core.config.loader import ConfigLoadResult, ConfigLoadStatus
from dovo.core.db import SessionStatus, WorktreesRepository, WorktreeStatus
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.git.exceptions import GitError
from dovo.core.git.runner import GitRunner
from dovo.core.worktree import Worktree, WorktreeApplyResult, WorktreeApplyStatus, WorktreeSession
from dovo.core.worktree.exceptions import WorktreeError
from dovo.core.worktree.models import WorktreeCreateResult, WorktreeCreateStatus
from dovo.engine.executors.models import ExecutionIdentity
from dovo.engine.models import RunSettings
from dovo.engine.workspace import Workspace
from tests.engine.conftest import WorktreeSetupFactory
from tests.harness.builders import WorkspaceBuilder
from tests.harness.diagnostic_log import diagnostic_hint, read_single_event
from tests.harness.git_index import snapshot_index
from tests.harness.sessions import RecordingRunObserver
from tests.harness.workspace_paths import initialized_workspace_paths


def _create_returns_git_failure(self: Worktree, *args: object, **kwargs: object) -> WorktreeCreateResult:
    return WorktreeCreateResult(status=WorktreeCreateStatus.GIT_FAILED, errors=["git worktree add failed"])


def _create_raises_config_load_error(self: Worktree, *args: object, **kwargs: object) -> WorktreeCreateResult:
    load_result = ConfigLoadResult(status=ConfigLoadStatus.MALFORMED_JSON, config_path=Path("config.json"))
    raise ConfigLoadError("malformed config.json", load_result)


class WorkspaceSetupTests:
    @pytest.mark.parametrize(
        "worktree_id",
        [pytest.param(None, id="no-worktree-id"), pytest.param("sess-1", id="worktree-id-ignored")],
    )
    def test_no_worktree_returns_resolved_cwd_and_notifies_inactive(
        self, tmp_path: Path, worktree_id: str | None
    ) -> None:
        observer = RecordingRunObserver()
        context = RunSettings(
            cwd=tmp_path,
            use_worktree=False,
            worktree_id=worktree_id,
            observer=observer,
            paths=initialized_workspace_paths(tmp_path),
        )

        target_dir, manager, session, error = Workspace(context).setup()

        assert (target_dir, manager, session, error) == (tmp_path.resolve(), None, None, None)
        assert observer.calls == [("worktree_ready", tmp_path.resolve(), False)]

    def test_use_worktree_creates_worktree_and_notifies_active(self, worktree_setup: WorktreeSetupFactory) -> None:
        observer = RecordingRunObserver()

        setup = worktree_setup(observer=observer)

        assert setup.error is None
        assert setup.manager is not None
        assert setup.session is not None
        assert setup.target_dir == setup.session.worktree_path
        assert setup.target_dir.exists()
        assert observer.calls == [("worktree_ready", setup.target_dir, True)]

    def test_fresh_run_worktree_identity_is_session_id_not_blueprint_key(
        self, worktree_setup: WorktreeSetupFactory, worktree_paths: WorkspacePaths
    ) -> None:
        paths = worktree_paths

        setup = worktree_setup(
            session_id="blueprint_367e1e88", identity=ExecutionIdentity(blueprint_name="Lint", blueprint_key="lint")
        )

        session = setup.session
        assert setup.error is None
        assert session is not None
        assert session.session_id == "blueprint_367e1e88"
        assert session.worktree_path == paths.worktree_dir("blueprint_367e1e88").resolve()
        assert session.target_branch == "dovo/blueprint_367e1e88"
        row = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).get("blueprint_367e1e88")
        assert row is not None
        assert row.branch_name == "dovo/blueprint_367e1e88"
        assert not paths.worktree_dir("lint").exists()

    @pytest.mark.parametrize(
        ("create", "expected_error"),
        [
            pytest.param(
                _create_returns_git_failure,
                "Git worktree creation failed: git worktree add failed",
                id="git-failed",
            ),
            pytest.param(
                _create_raises_config_load_error,
                "Git worktree creation failed: malformed config.json",
                id="config-load-error",
            ),
        ],
    )
    def test_worktree_creation_failure_returns_error_message(
        self,
        worktree_setup: WorktreeSetupFactory,
        worktree_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        create: Callable[..., WorktreeCreateResult],
        expected_error: str,
    ) -> None:
        monkeypatch.setattr(Worktree, "create", create)

        setup = worktree_setup()

        assert setup.target_dir == worktree_root.resolve()
        assert setup.manager is None
        assert setup.session is None
        assert setup.error == expected_error

    def test_capacity_failure_message_includes_fix_line(self, tmp_path: Path) -> None:
        workspace_root = (
            WorkspaceBuilder(tmp_path / "workspace")
            .with_git()
            .with_database()
            .with_config(
                data={
                    "version": 1,
                    "project": {"name": "capacity"},
                    "worktree": {"base_ref": "HEAD", "max_active_worktrees": 1, "default_timeout_seconds": 900},
                }
            )
            .build()
        )
        paths = initialized_workspace_paths(workspace_root)
        first = Workspace(RunSettings(cwd=workspace_root, use_worktree=True, session_id="first", paths=paths))
        assert first.setup()[3] is None

        _, manager, session, error = Workspace(
            RunSettings(cwd=workspace_root, use_worktree=True, session_id="second", paths=paths)
        ).setup()

        assert manager is None
        assert session is None
        assert error == (
            "Git worktree creation failed: Maximum active worktrees reached (1/1).\n"
            "Fix: Remove a kept worktree with `dovo worktree delete <worktree-id>` (see `dovo worktree list`), "
            "or Raise worktree.max_active_worktrees in .dovo/config.json"
        )


class WorkspaceRetainedWorktreeTests:
    def test_retained_worktree_id_rebuilds_session_from_row(
        self, worktree_setup: WorktreeSetupFactory, worktree_paths: WorkspacePaths
    ) -> None:
        paths = worktree_paths
        created = Worktree(paths).create(name="retained")
        assert created.session is not None
        retained_id = created.session.session_id

        setup = worktree_setup(worktree_id=retained_id)

        session = setup.session
        assert setup.error is None
        assert setup.manager is not None
        assert setup.target_dir == paths.worktree_dir(retained_id)
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
        self,
        worktree_setup: WorktreeSetupFactory,
        worktree_root: Path,
        worktree_paths: WorkspacePaths,
        fault: str,
        expected_error: str,
    ) -> None:
        worktree_id = "orphan"
        if fault == "no-record":
            worktree_paths.worktree_dir(worktree_id).mkdir(parents=True)

        setup = worktree_setup(worktree_id=worktree_id)

        assert setup.target_dir == worktree_root.resolve()
        assert setup.manager is None
        assert setup.session is None
        assert setup.error == expected_error.format(path=worktree_paths.worktree_dir(worktree_id))


class WorkspaceCleanupTests:
    def test_missing_manager_or_session_returns_false_and_notifies_not_kept(self, tmp_path: Path) -> None:
        observer = RecordingRunObserver()
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, observer=observer, paths=initialized_workspace_paths(tmp_path)
        )

        warnings: list[str] = []

        kept = Workspace(context).cleanup(None, None, tmp_path, SessionStatus.COMPLETED, warnings)

        assert kept is False
        assert [call[:3] for call in observer.calls] == [("worktree_cleanup", False, tmp_path)]

    @pytest.mark.parametrize(
        ("error", "recorded"),
        [
            pytest.param(RuntimeError, True, id="runtime"),
            pytest.param(LockTimeoutError, False, id="lock-timeout"),
            pytest.param(GitError, False, id="git"),
            pytest.param(WorktreeError, False, id="worktree"),
        ],
    )
    def test_removal_failure_appends_warning_with_path_and_returns_false(
        self,
        worktree_setup: WorktreeSetupFactory,
        monkeypatch: pytest.MonkeyPatch,
        frozen_diagnostic_clock: Path,
        error: type[Exception],
        recorded: bool,
    ) -> None:
        """Append the removal warning; only an unexpected error hints it and writes one engine.workspace record."""
        workspace, target_dir, manager, session, _ = worktree_setup()
        assert session is not None

        def _raise(self: Worktree, *args: object, **kwargs: object) -> list[str]:
            raise error("worktree removal failed")

        monkeypatch.setattr(Worktree, "cleanup", _raise)
        warnings: list[str] = []

        kept = workspace.cleanup(manager, session, target_dir, SessionStatus.COMPLETED, warnings)

        assert kept is False
        hint = diagnostic_hint(frozen_diagnostic_clock) if recorded else ""
        assert warnings == [f"Failed to remove worktree '{session.worktree_path}': worktree removal failed{hint}"]
        if recorded:
            event = read_single_event(frozen_diagnostic_clock)
            assert (event["level"], event["operation"]) == ("error", "engine.workspace")
            assert event["context"] == {"session_id": None, "path": str(session.worktree_path)}
        else:
            assert not frozen_diagnostic_clock.parent.parent.exists()

    def test_removal_reporting_problems_appends_first_problem_and_returns_false(
        self, worktree_setup: WorktreeSetupFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, target_dir, manager, session, _ = worktree_setup()
        assert session is not None
        monkeypatch.setattr(Worktree, "cleanup", lambda self, *args, **kwargs: ["partial failure", "second"])
        warnings: list[str] = []

        kept = workspace.cleanup(manager, session, target_dir, SessionStatus.COMPLETED, warnings)

        assert kept is False
        assert warnings == [f"Failed to remove worktree '{session.worktree_path}': partial failure"]

    def test_kept_cleanup_notifies_worktree_id_and_branch(self, worktree_setup: WorktreeSetupFactory) -> None:
        observer = RecordingRunObserver()
        workspace, target_dir, manager, session, _ = worktree_setup(session_id="kept-run", observer=observer)
        assert session is not None

        kept = workspace.cleanup(manager, session, target_dir, SessionStatus.FAILED, [])

        assert kept is True
        assert observer.calls[-1][3:] == ("kept-run", "dovo/kept-run")

    def test_cleanup_of_linked_worktree_preserves_session_contents(
        self, worktree_setup: WorktreeSetupFactory, worktree_paths: WorkspacePaths
    ) -> None:
        workspace, target_dir, manager, session, _ = worktree_setup(session_id="cleanup-link")
        assert session is not None
        setup_warnings: list[str] = []
        assert workspace.link_session_dir(manager, session, setup_warnings) is None
        sentinel_path = worktree_paths.session_dir("cleanup-link") / "sentinel.txt"
        sentinel_path.write_text("preserve me", encoding="utf-8")
        warnings: list[str] = []

        kept = workspace.cleanup(manager, session, target_dir, SessionStatus.COMPLETED, warnings)

        assert kept is False
        assert warnings == []
        assert not target_dir.exists()
        assert sentinel_path.read_text(encoding="utf-8") == "preserve me"

    def test_cleanup_unlink_failure_appends_warning_and_still_removes_worktree(
        self, worktree_setup: WorktreeSetupFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, target_dir, manager, session, _ = worktree_setup(session_id="cleanup-busy")
        assert session is not None
        message = f"Failed to unlink worktree storage bridge at '{target_dir / '.dovo' / 'run'}': busy"
        monkeypatch.setattr("dovo.engine.workspace.remove_storage_bridge", lambda worktree_path: message)
        warnings: list[str] = []

        kept = workspace.cleanup(manager, session, target_dir, SessionStatus.COMPLETED, warnings)

        assert kept is False
        assert warnings == [message]
        assert not target_dir.exists()


def _commit_source_path(workspace_root: Path, rel_path: str, message: str) -> None:
    """Force-add and commit rel_path in the source repository so new worktrees check it out."""
    GitRunner.run(["add", "-f", rel_path], path=workspace_root)
    GitRunner.run(["commit", "-m", message], path=workspace_root)


class WorkspaceLinkSessionDirTests:
    def test_link_after_setup_links_run_to_session_directory(
        self, worktree_setup: WorktreeSetupFactory, worktree_paths: WorkspacePaths
    ) -> None:
        paths = worktree_paths
        workspace, target_dir, manager, session, _ = worktree_setup(session_id="link-1")
        warnings: list[str] = []

        error = workspace.link_session_dir(manager, session, warnings)

        assert error is None
        assert warnings == []
        assert (target_dir / ".dovo" / "run").resolve() == paths.session_dir("link-1").resolve()
        assert paths.session_dir("link-1").is_dir()

    def test_link_replaces_committed_broken_symlink(
        self, tmp_path: Path, worktree_setup: WorktreeSetupFactory, worktree_root: Path, worktree_paths: WorkspacePaths
    ) -> None:
        (worktree_root / ".dovo" / "run").symlink_to(tmp_path / "missing-session")
        _commit_source_path(worktree_root, ".dovo/run", "Add stale storage bridge")
        workspace, target_dir, manager, session, _ = worktree_setup(session_id="link-stale")

        error = workspace.link_session_dir(manager, session, [])

        assert error is None
        assert (target_dir / ".dovo" / "run").is_symlink()
        assert (target_dir / ".dovo" / "run").resolve() == worktree_paths.session_dir("link-stale").resolve()

    def test_link_with_committed_directory_returns_not_a_symlink_error_and_preserves_content(
        self, worktree_setup: WorktreeSetupFactory, worktree_root: Path, worktree_paths: WorkspacePaths
    ) -> None:
        paths = worktree_paths
        source_sentinel = worktree_root / ".dovo" / "run" / "sentinel.txt"
        source_sentinel.parent.mkdir(parents=True)
        source_sentinel.write_text("do not delete", encoding="utf-8")
        _commit_source_path(worktree_root, ".dovo/run/sentinel.txt", "Add storage bridge collision")
        session_file = paths.session_dir("link-collision") / "session.json"
        session_file.parent.mkdir(parents=True)
        session_file.write_text("preserve me too", encoding="utf-8")
        observer = RecordingRunObserver()
        workspace, target_dir, manager, session, _ = worktree_setup(session_id="link-collision", observer=observer)

        error = workspace.link_session_dir(manager, session, [])

        record = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).get("link-collision")
        assert error == (
            f"Worktree storage bridge failed: Worktree storage bridge path '{target_dir / '.dovo' / 'run'}' "
            "is not a symlink."
        )
        assert not target_dir.exists()
        assert [call[:3] for call in observer.calls] == [
            ("worktree_ready", target_dir, True),
            ("worktree_cleanup", False, target_dir),
        ]
        assert "dovo/link-collision" not in GitRunner.list_branches(worktree_root)
        assert record is not None
        assert record.status == WorktreeStatus.CLEANED
        assert source_sentinel.read_text(encoding="utf-8") == "do not delete"
        assert session_file.read_text(encoding="utf-8") == "preserve me too"

    def test_link_symlink_failure_returns_prefixed_error_and_discards_worktree(
        self,
        worktree_setup: WorktreeSetupFactory,
        worktree_root: Path,
        worktree_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        paths = worktree_paths
        workspace, target_dir, manager, session, _ = worktree_setup(session_id="link-fail")

        def _symlink_fails(self: Path, target: Path, target_is_directory: bool = False) -> None:
            raise OSError("storage device I/O failure")

        monkeypatch.setattr(Path, "symlink_to", _symlink_fails)

        error = workspace.link_session_dir(manager, session, [])

        record = WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id).get("link-fail")
        assert error is not None
        assert error.startswith("Worktree storage bridge failed: Unable to create worktree storage bridge at '")
        assert not target_dir.exists()
        assert "dovo/link-fail" not in GitRunner.list_branches(worktree_root)
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
        self, worktree_setup: WorktreeSetupFactory, worktree_paths: WorkspacePaths, scenario: str
    ) -> None:
        paths = worktree_paths
        if scenario == "no-worktree":
            setup = worktree_setup(use_worktree=False, session_id="link-skip")
        elif scenario == "retained-worktree":
            created = Worktree(
                paths, db=WorktreesRepository(db_path=paths.database_file, project_id=paths.project_id)
            ).create(session_id="retained")
            assert created.session is not None
            setup = worktree_setup(session_id="link-skip", worktree_id=created.session.session_id)
        else:
            setup = worktree_setup(session_id=None)
        workspace, target_dir, manager, session, _ = setup
        warnings: list[str] = []

        error = workspace.link_session_dir(manager, session, warnings)

        assert error is None
        assert warnings == []
        assert not (target_dir / ".dovo" / "run").is_symlink()
        assert not (target_dir / ".dovo" / "run").exists()


class WorkspaceHandleAutoApplyTests:
    def test_auto_apply_disabled_returns_none_and_false(self, tmp_path: Path) -> None:
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, auto_apply=False, paths=initialized_workspace_paths(tmp_path)
        )
        errors: list[str] = []
        warnings: list[str] = []

        new_status, apply_failed = Workspace(context).handle_auto_apply(None, None, errors, warnings)

        assert (new_status, apply_failed) == (None, False)
        assert (errors, warnings) == ([], [])

    def test_auto_apply_success_returns_none_and_false(
        self, worktree_setup: WorktreeSetupFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, _, manager, session, _ = worktree_setup(auto_apply=True)
        assert session is not None
        ok_result = WorktreeApplyResult(worktree_id=session.session_id, status=WorktreeApplyStatus.OK)
        monkeypatch.setattr(Worktree, "apply", lambda self, *args, **kwargs: ok_result)
        errors: list[str] = []
        warnings: list[str] = []

        new_status, apply_failed = workspace.handle_auto_apply(manager, session, errors, warnings)

        assert (new_status, apply_failed) == (None, False)
        assert errors == []

    def test_auto_apply_conflict_returns_failed_status_and_appends_errors_and_warnings(
        self, worktree_setup: WorktreeSetupFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace, _, manager, session, _ = worktree_setup(auto_apply=True)
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

        new_status, apply_failed = workspace.handle_auto_apply(manager, session, errors, warnings)

        assert (new_status, apply_failed) == (SessionStatus.FAILED, True)
        assert errors == ["merge conflict"]
        assert warnings == ["hunk rejected"]


class WorkspaceCaptureAndPersistDiffTests:
    @pytest.mark.parametrize(
        ("session_present", "session_id"),
        [
            pytest.param(False, "sess-1", id="no_session"),
            pytest.param(True, None, id="no_session_id"),
        ],
    )
    def test_missing_session_or_session_id_is_noop(
        self, worktree_setup: WorktreeSetupFactory, session_present: bool, session_id: str | None
    ) -> None:
        setup = worktree_setup(session_id=session_id)
        warnings: list[str] = []

        setup.workspace.capture_and_persist_diff(setup.session if session_present else None, warnings)

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
        worktree_setup: WorktreeSetupFactory,
        worktree_paths: WorkspacePaths,
        monkeypatch: pytest.MonkeyPatch,
        names: tuple[str, ...],
        expected_line: str,
        value_visible: bool,
    ) -> None:
        monkeypatch.setenv("DB_PIN", "1234")
        workspace, _, _, session, _ = worktree_setup(session_id="sess-1", sensitive_variables=names)
        assert session is not None
        (session.worktree_path / "new_file.txt").write_text("pin=1234\n", encoding="utf-8")

        workspace.capture_and_persist_diff(session, [])

        patch_text = (worktree_paths.session_dir("sess-1") / "diff.patch").read_text(encoding="utf-8")
        assert expected_line in patch_text
        assert ("1234" in patch_text) is value_visible

    def test_capture_and_persist_diff_masks_worktree_secret_in_patch_file(
        self, worktree_setup: WorktreeSetupFactory, worktree_paths: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SVC_TOKEN", "s3cr3t-value")
        workspace, _, _, session, _ = worktree_setup(session_id="sess-1")
        assert session is not None
        (session.worktree_path / "new_file.txt").write_text("token=s3cr3t-value\n", encoding="utf-8")
        warnings: list[str] = []

        workspace.capture_and_persist_diff(session, warnings)

        patch_text = (worktree_paths.session_dir("sess-1") / "diff.patch").read_text(encoding="utf-8")
        assert "+token=[REDACTED:SVC_TOKEN]" in patch_text
        assert "s3cr3t-value" not in patch_text
        assert warnings == []

    def test_capture_leaves_worktree_index_and_status_unchanged(
        self, worktree_setup: WorktreeSetupFactory, worktree_paths: WorkspacePaths
    ) -> None:
        workspace, _, _, session, _ = worktree_setup(session_id="sess-1")
        assert session is not None
        (session.worktree_path / "new.txt").write_text("untracked\n", encoding="utf-8")
        before = snapshot_index(session.worktree_path)
        warnings: list[str] = []

        workspace.capture_and_persist_diff(session, warnings)

        patch_text = (worktree_paths.session_dir("sess-1") / "diff.patch").read_text(encoding="utf-8")
        assert "diff --git a/new.txt b/new.txt" in patch_text
        assert warnings == []
        assert snapshot_index(session.worktree_path) == before
        assert "?? new.txt" in before.status.splitlines()

    def test_persist_failure_records_error_and_hints_warning(
        self, worktree_setup: WorktreeSetupFactory, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Append the hinted persist failure and write one engine.workspace record when writing diff.patch raises."""
        workspace, _, _, session, _ = worktree_setup(session_id="sess-1")
        assert session is not None

        def _disk_full(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("dovo.engine.workspace.write_session_diff", _disk_full)
        warnings: list[str] = []

        workspace.capture_and_persist_diff(session, warnings)

        assert warnings == [
            f"Failed to persist session diff artifact: disk full{diagnostic_hint(frozen_diagnostic_clock)}"
        ]
        event = read_single_event(frozen_diagnostic_clock)
        assert (event["level"], event["operation"]) == ("error", "engine.workspace")
        assert event["context"] == {"session_id": "sess-1", "path": str(session.worktree_path)}

    def test_git_failure_keeps_warning_without_record(
        self, worktree_setup: WorktreeSetupFactory, worktree_paths: WorkspacePaths, frozen_diagnostic_clock: Path
    ) -> None:
        """Append an unhinted warning, write no diff.patch and leave <DOVO_HOME>/logs absent for an unknown base commit."""
        workspace, _, _, session, _ = worktree_setup(session_id="sess-1")
        assert session is not None
        unknown_base = session.model_copy(update={"base_commit": "0" * 40})
        warnings: list[str] = []

        workspace.capture_and_persist_diff(unknown_base, warnings)

        assert len(warnings) == 1
        assert warnings[0].startswith("Failed to persist session diff artifact: ")
        assert "Details logged:" not in warnings[0]
        assert not (worktree_paths.session_dir("sess-1") / "diff.patch").exists()
        assert not frozen_diagnostic_clock.parent.parent.exists()


class WorkspaceFinalizeCleanupTests:
    @pytest.mark.parametrize(
        ("status", "keep", "discard", "expected_kept"),
        [
            pytest.param(SessionStatus.COMPLETED, False, False, False, id="completed-default"),
            pytest.param(SessionStatus.COMPLETED, True, False, True, id="completed-keep"),
            pytest.param(SessionStatus.COMPLETED, False, True, False, id="completed-discard"),
            pytest.param(SessionStatus.FAILED, False, False, True, id="failed-default"),
            pytest.param(SessionStatus.FAILED, True, False, True, id="failed-keep"),
            pytest.param(SessionStatus.FAILED, False, True, False, id="failed-discard"),
            pytest.param(SessionStatus.CANCELLED, False, False, False, id="cancelled-default"),
            pytest.param(SessionStatus.CANCELLED, True, False, True, id="cancelled-keep"),
            pytest.param(SessionStatus.CANCELLED, False, True, False, id="cancelled-discard"),
        ],
    )
    def test_retention_matrix_matches_issue_table(
        self,
        worktree_setup: WorktreeSetupFactory,
        status: SessionStatus,
        keep: bool,
        discard: bool,
        expected_kept: bool,
    ) -> None:
        observer = RecordingRunObserver()
        workspace, target_dir, manager, session, _ = worktree_setup(keep=keep, discard=discard, observer=observer)
        assert session is not None

        kept = workspace.finalize_cleanup(manager, session, target_dir, status, apply_failed=False, warnings=[])

        assert kept is expected_kept
        assert target_dir.exists() is expected_kept
        assert observer.calls[-1][:3] == ("worktree_cleanup", expected_kept, session.worktree_path)

    @pytest.mark.parametrize(
        ("status", "apply_failed"),
        [
            pytest.param(SessionStatus.PAUSED, False, id="paused"),
            pytest.param(SessionStatus.COMPLETED, True, id="apply-failed"),
        ],
    )
    def test_discard_does_not_override_paused_or_apply_failed(
        self, worktree_setup: WorktreeSetupFactory, status: SessionStatus, apply_failed: bool
    ) -> None:
        observer = RecordingRunObserver()
        workspace, target_dir, manager, session, _ = worktree_setup(discard=True, observer=observer)
        assert session is not None

        kept = workspace.finalize_cleanup(manager, session, target_dir, status, apply_failed=apply_failed, warnings=[])

        assert kept is True
        assert target_dir.exists()
        assert observer.calls[-1][:3] == ("worktree_cleanup", True, session.worktree_path)


@dataclass(frozen=True)
class _PrepareSessionDirCase:
    prepare: Callable[[Workspace, list[str]], Path | None]
    expected_dir: Callable[[WorkspacePaths], Path]
    created: Path
    failure_warning: str


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _PrepareSessionDirCase(
                prepare=Workspace.prepare_session_tmp_dir,
                expected_dir=lambda paths: paths.tmp_dir / "session-abc",
                created=Path("steps"),
                failure_warning="Failed to create session scratch directory: disk full",
            ),
            id="tmp-dir",
        ),
        pytest.param(
            _PrepareSessionDirCase(
                prepare=Workspace.prepare_session_log_dir,
                expected_dir=lambda paths: paths.logs_dir / "session-abc",
                created=Path(),
                failure_warning="Failed to create session log directory: disk full",
            ),
            id="log-dir",
        ),
    ],
)
class WorkspacePrepareSessionDirTests:
    def test_no_session_id_returns_none(self, tmp_path: Path, case: _PrepareSessionDirCase) -> None:
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, session_id=None, paths=initialized_workspace_paths(tmp_path)
        )
        warnings: list[str] = []

        result = case.prepare(Workspace(context), warnings)

        assert result is None
        assert warnings == []

    def test_creates_and_returns_session_directory(self, tmp_path: Path, case: _PrepareSessionDirCase) -> None:
        paths = initialized_workspace_paths(tmp_path)
        context = RunSettings(cwd=tmp_path, use_worktree=False, session_id="session-abc", paths=paths)
        warnings: list[str] = []

        result = case.prepare(Workspace(context), warnings)

        assert result == case.expected_dir(paths)
        assert (case.expected_dir(paths) / case.created).is_dir()
        assert warnings == []

    def test_mkdir_failure_appends_warning_and_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _PrepareSessionDirCase
    ) -> None:
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, session_id="session-abc", paths=initialized_workspace_paths(tmp_path)
        )

        def _raise(self: Path, *args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _raise)
        warnings: list[str] = []

        result = case.prepare(Workspace(context), warnings)

        assert result is None
        assert warnings == [case.failure_warning]


class WorkspacePrepareSessionArtifactsTests:
    def test_no_session_id_returns_no_store(self, tmp_path: Path) -> None:
        context = RunSettings(
            cwd=tmp_path, use_worktree=False, session_id=None, paths=initialized_workspace_paths(tmp_path)
        )

        assert Workspace(context).prepare_session_artifacts() is None

    def test_session_id_set_returns_store_bound_to_session(self, tmp_path: Path) -> None:
        workspace_root = WorkspaceBuilder(tmp_path / "artifacts_workspace").with_database().build()
        paths = initialized_workspace_paths(workspace_root)
        context = RunSettings(cwd=workspace_root, use_worktree=False, session_id="session-abc", paths=paths)
        (workspace_root / "PLAN.md").write_text("plan", encoding="utf-8")

        store = Workspace(context).prepare_session_artifacts()

        assert store is not None
        published = store.publish(
            workspace_root, name="plan", path_glob="PLAN.md", retention_days=None, provenance=ArtifactProvenance()
        )
        assert published.ok
        record = ArtifactsRepository(db_path=paths.database_file, project_id=paths.project_id).get(
            "session-abc", "plan"
        )
        assert record is not None
        assert record.path.is_relative_to(paths.artifacts_dir)


class WorkspaceSinglePathResolutionTests:
    def test_prepare_session_methods_use_context_paths_without_reresolving(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_root = WorkspaceBuilder(tmp_path / "path_snapshot_workspace").with_git().with_database().build()
        paths = initialized_workspace_paths(workspace_root)
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
        store = workspace.prepare_session_artifacts()
        assert store is not None
        assert workspace.prepare_session_log_dir(warnings) == paths.logs_dir / "path-snapshot"
        workspace.capture_and_persist_diff(session, warnings)
        assert warnings == []


class ScratchLifecycleCleanupTests:
    @staticmethod
    def _allocate(tmp_path: Path, session_name: str) -> tuple[Path, AgentInvocationContext]:
        session_tmp_dir = tmp_path / session_name
        session_tmp_dir.mkdir()
        result = allocate_invocation_paths(
            invocation_id=new_invocation_id(),
            session_tmp_dir=session_tmp_dir,
            step_id="build",
            worktree_path=tmp_path / "worktree",
            main_checkout=tmp_path / "main",
        )
        assert result.context is not None

        return session_tmp_dir, result.context

    def test_completed_unkept_run_removes_session_tree_including_invocation_directories(self, tmp_path: Path) -> None:
        session_tmp_dir, _ = self._allocate(tmp_path, "session-a")
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=False, status=SessionStatus.COMPLETED)

        assert not session_tmp_dir.exists()

    @pytest.mark.parametrize(
        ("keep", "status"),
        [
            pytest.param(False, SessionStatus.FAILED, id="failed"),
            pytest.param(False, SessionStatus.PAUSED, id="paused"),
            pytest.param(False, SessionStatus.CANCELLED, id="cancelled"),
            pytest.param(True, SessionStatus.COMPLETED, id="keep-completed"),
        ],
    )
    def test_retained_runs_keep_invocation_directories(self, tmp_path: Path, keep: bool, status: SessionStatus) -> None:
        session_tmp_dir, invocation = self._allocate(tmp_path, "session-a")
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=keep, status=status)

        assert invocation.scratch_path.is_dir()
        assert invocation.control_path.is_dir()

    def test_removal_failure_is_swallowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        session_tmp_dir, _ = self._allocate(tmp_path, "session-a")
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))

        def _raise(*args: object, **kwargs: object) -> None:
            raise OSError("busy")

        monkeypatch.setattr(shutil, "rmtree", _raise)

        Workspace(context).cleanup_session_tmp_dir(session_tmp_dir, keep=False, status=SessionStatus.COMPLETED)

        assert session_tmp_dir.exists()

    def test_cleanup_does_not_touch_another_sessions_directories(self, tmp_path: Path) -> None:
        session_a, _ = self._allocate(tmp_path, "session-a")
        _, invocation_b = self._allocate(tmp_path, "session-b")
        (invocation_b.scratch_path / "notes.txt").write_text("keep", encoding="utf-8")
        context = RunSettings(cwd=tmp_path, use_worktree=False, paths=initialized_workspace_paths(tmp_path))

        Workspace(context).cleanup_session_tmp_dir(session_a, keep=False, status=SessionStatus.COMPLETED)

        assert not session_a.exists()
        assert (invocation_b.scratch_path / "notes.txt").read_text(encoding="utf-8") == "keep"
        assert invocation_b.control_path.is_dir()
