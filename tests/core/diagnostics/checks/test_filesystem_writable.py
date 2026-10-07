"""Unit tests for dovo.core.diagnostics.checks.filesystem_writable."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspacePaths
from dovo.core.config.models import DovoConfig, ProjectConfig
from dovo.core.diagnostics.checks.filesystem_writable import FilesystemWritableCheck
from dovo.core.diagnostics.models import CheckCategory, CheckStatus, DiagnosticsContext
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity

WorkspacePathsFactory = Callable[[Path, Path | None], WorkspacePaths]


class FilesystemWritableCheckTests:
    """Unit tests for FilesystemWritableCheck diagnostic outcomes."""

    def test_execute_all_configured_paths_writable_returns_ok(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] FilesystemWritableCheck.execute: default paths, all dirs creatable -> OK with verified_paths."""
        config = DovoConfig(version=1, project=ProjectConfig(name="demo"))
        check = FilesystemWritableCheck()
        paths = workspace_paths_factory(tmp_path, None)
        context = DiagnosticsContext(cwd=tmp_path, config=config, paths=paths)

        result = check.execute(context)

        assert result.check_id == "filesystem.writable"
        assert result.category == CheckCategory.FILESYSTEM
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.warnings == []
        assert result.details == {
            "verified_paths": [
                str(tmp_path / ".dovo"),
                str(paths.sessions_dir),
                str(paths.artifacts_dir),
                str(tmp_path / ".dovo/worktrees"),
                str(paths.database_file.parent),
            ]
        }

    def test_execute_readonly_directory_returns_unwritable_failure(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] FilesystemWritableCheck.execute: worktrees_dir pre-created read-only -> FAILED with DOCTOR_FS_UNWRITABLE."""
        worktrees_dir = tmp_path / ".dovo" / "worktrees"
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        worktrees_dir.chmod(0o500)
        config = DovoConfig(version=1, project=ProjectConfig(name="demo"))
        check = FilesystemWritableCheck()
        paths = workspace_paths_factory(tmp_path, None)
        context = DiagnosticsContext(cwd=tmp_path, config=config, paths=paths)

        try:
            result = check.execute(context)
        finally:
            worktrees_dir.chmod(0o700)

        message = "1 configured path(s) are not writable."
        assert result.check_id == "filesystem.writable"
        assert result.category == CheckCategory.FILESYSTEM
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_FS_UNWRITABLE"
        assert result.details == {"unwritable_paths": [str(worktrees_dir)]}
        assert result.errors == [message]

    def test_execute_readonly_parent_directory_returns_unwritable_failure(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] FilesystemWritableCheck.execute: .dovo read-only, worktrees dir not yet created -> probe write and mkdir raise OSError -> FAILED with DOCTOR_FS_UNWRITABLE."""
        config = DovoConfig(version=1, project=ProjectConfig(name="demo"))
        check = FilesystemWritableCheck()
        paths = workspace_paths_factory(tmp_path, None)
        dovo_dir = tmp_path / ".dovo"
        dovo_dir.chmod(0o500)
        context = DiagnosticsContext(cwd=tmp_path, config=config, paths=paths)

        try:
            result = check.execute(context)
        finally:
            dovo_dir.chmod(0o700)

        unwritable_paths = [
            str(tmp_path / ".dovo"),
            str(tmp_path / ".dovo/worktrees"),
        ]
        message = f"{len(unwritable_paths)} configured path(s) are not writable."
        assert result.check_id == "filesystem.writable"
        assert result.category == CheckCategory.FILESYSTEM
        assert result.status == CheckStatus.FAILED
        assert result.error_code == "DOCTOR_FS_UNWRITABLE"
        assert result.details == {"unwritable_paths": unwritable_paths}
        assert result.errors == [message]

    def test_execute_missing_config_falls_back_to_default_paths(
        self, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """[tier-1/unit] FilesystemWritableCheck.execute: context.config=None -> probes deterministic default paths."""
        check = FilesystemWritableCheck()
        paths = workspace_paths_factory(tmp_path, None)
        context = DiagnosticsContext(cwd=tmp_path, config=None, paths=paths)

        result = check.execute(context)

        assert result.check_id == "filesystem.writable"
        assert result.category == CheckCategory.FILESYSTEM
        assert result.status == CheckStatus.OK
        assert result.error_code is None
        assert result.details == {
            "verified_paths": [
                str(tmp_path / ".dovo"),
                str(paths.sessions_dir),
                str(paths.artifacts_dir),
                str(tmp_path / ".dovo/worktrees"),
                str(paths.database_file.parent),
            ]
        }

    def test_execute_with_project_identity_probes_global_runtime_paths(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workspace_paths_factory: WorkspacePathsFactory
    ) -> None:
        """An identified project probes global runtime paths and local workspace state."""
        global_root = tmp_path / "global"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        config = DovoConfig(version=1, project=ProjectConfig(name="demo"))
        monkeypatch.setenv("DOVO_HOME", str(global_root))
        save_project_identity(tmp_path / ".dovo" / "project.json", identity)
        paths = workspace_paths_factory(tmp_path, None)

        result = FilesystemWritableCheck().execute(DiagnosticsContext(cwd=tmp_path, config=config, paths=paths))

        project_storage = global_root / "storage" / "projects" / "project-626"
        assert result.status == CheckStatus.OK
        assert result.details == {
            "verified_paths": [
                str(tmp_path / ".dovo"),
                str(project_storage / "sessions"),
                str(project_storage / "artifacts"),
                str(tmp_path / ".dovo" / "worktrees"),
                str(paths.database_file.parent),
            ]
        }
        assert not (tmp_path / ".dovo" / "sessions").exists()
        assert not (tmp_path / ".dovo" / "artifacts").exists()
