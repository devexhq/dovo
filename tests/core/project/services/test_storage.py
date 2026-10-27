"""Tests for project-aware runtime storage resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dovo.common.filesystem import WorkspaceNotInitializedError
from dovo.common.filesystem.models import GlobalPaths, RepositoryPaths
from dovo.core.project.models import ProjectIdentity
from dovo.core.project.services.identity import save_project_identity
from dovo.core.project.services.storage import build_workspace_paths, resolve_workspace_paths
from tests.harness.diagnostic_log import read_single_event

FORCE_HINT = "Or run `dovo init --id <project-id> --force` to replace the unusable identity."


class ResolveWorkspacePathsTests:
    """Contract tests for resolving project-aware workspace paths."""

    def test_missing_identity_raises_and_creates_nothing(self, tmp_path: Path) -> None:
        repository = tmp_path / "repository"

        with pytest.raises(WorkspaceNotInitializedError) as exc_info:
            resolve_workspace_paths(RepositoryPaths.from_root(repository), GlobalPaths.from_root(tmp_path / "global"))

        identity_path = repository / ".dovo" / "project.json"
        assert str(exc_info.value) == (
            f"Workspace is not initialized: no valid project identity at '{identity_path}'.\n"
            "Fix: Run `dovo init` to initialize this workspace."
        )
        assert not (repository / ".dovo").exists()

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(b"\xff\xfe\x00invalid", id="undecodable"),
            pytest.param(b"not json", id="malformed"),
            pytest.param(b'{"id": 5}', id="schema-invalid"),
        ],
    )
    def test_unusable_identity_raises_with_force_hint(
        self, tmp_path: Path, frozen_diagnostic_clock: Path, content: bytes
    ) -> None:
        """Raise with the force hint and record nothing, since none of these causes is an OSError."""
        repository = tmp_path / "repository"
        identity_path = repository / ".dovo" / "project.json"
        identity_path.parent.mkdir(parents=True)
        identity_path.write_bytes(content)

        with pytest.raises(WorkspaceNotInitializedError) as exc_info:
            resolve_workspace_paths(RepositoryPaths.from_root(repository), GlobalPaths.from_root(tmp_path / "global"))

        assert str(exc_info.value).endswith(FORCE_HINT)
        assert not frozen_diagnostic_clock.exists()

    def test_unreadable_identity_records_oserror_and_raises_not_initialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_diagnostic_clock: Path
    ) -> None:
        """Raise with today's message and force hint, and record the PermissionError as one error-level event."""
        repository = tmp_path / "repository"
        identity_path = repository / ".dovo" / "project.json"
        save_project_identity(
            identity_path, ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        real_read_text = Path.read_text

        def _denied_read(
            self: Path, encoding: str | None = None, errors: str | None = None, newline: str | None = None
        ) -> str:
            if self.name == "project.json":
                raise PermissionError("denied")
            return real_read_text(self, encoding=encoding, errors=errors, newline=newline)

        monkeypatch.setattr(Path, "read_text", _denied_read)

        with pytest.raises(WorkspaceNotInitializedError) as exc_info:
            resolve_workspace_paths(RepositoryPaths.from_root(repository), GlobalPaths.from_root(tmp_path / "global"))

        assert str(exc_info.value) == (
            f"Workspace is not initialized: no valid project identity at '{identity_path}'.\n"
            f"Fix: Run `dovo init` to initialize this workspace.\n{FORCE_HINT}"
        )
        event = read_single_event(frozen_diagnostic_clock)
        assert event["level"] == "error"
        assert event["operation"] == "project.resolve_workspace_paths"
        assert event["context"] == {"path": str(identity_path)}
        assert event["exception_type"] == "PermissionError"

    def test_dovo_dir_that_is_global_home_raises_without_reading_its_identity(self, tmp_path: Path) -> None:
        """Raise the not-initialized error without the force hint even when the global home holds a valid project.json."""
        home = tmp_path / "home"
        global_root = home / ".dovo"
        identity_path = global_root / "project.json"
        save_project_identity(
            identity_path, ProjectIdentity(id="stray-id", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        )

        with pytest.raises(WorkspaceNotInitializedError) as exc_info:
            resolve_workspace_paths(RepositoryPaths.from_root(home), GlobalPaths.from_root(global_root))

        assert str(exc_info.value) == (
            f"Workspace is not initialized: no valid project identity at '{identity_path.resolve()}'.\n"
            "Fix: Run `dovo init` to initialize this workspace."
        )

    def test_valid_identity_resolves_runtime_under_global_storage(self, tmp_path: Path) -> None:
        global_root = tmp_path / "global"
        repository = tmp_path / "repository"
        identity = ProjectIdentity(id="project-626", created_at=datetime(2026, 1, 1, tzinfo=UTC))
        save_project_identity(repository / ".dovo" / "project.json", identity)

        paths = resolve_workspace_paths(RepositoryPaths.from_root(repository), GlobalPaths.from_root(global_root))

        project_storage = global_root / "storage" / "projects" / "project-626"
        assert paths.project_id == "project-626"
        assert paths.runtime_root == project_storage
        assert paths.sessions_dir == project_storage / "sessions"
        assert paths.artifacts_dir == project_storage / "artifacts"
        assert paths.logs_dir == project_storage / "logs"
        assert paths.tmp_dir == project_storage / "tmp"
        assert paths.worktrees_dir == repository / ".dovo" / "worktrees"
        assert paths.database_file == global_root / "data" / "dovo.db"


class BuildWorkspacePathsTests:
    """Contract tests for the pure workspace paths builder."""

    def test_build_workspace_paths_is_pure_and_matches_resolve(self, tmp_path: Path) -> None:
        repository = tmp_path / "repository"
        repository_paths = RepositoryPaths.from_root(repository)
        global_paths = GlobalPaths.from_root(tmp_path / "global")

        built = build_workspace_paths(repository_paths, global_paths, "project-p")

        assert not repository.exists()
        assert not (tmp_path / "global").exists()
        save_project_identity(
            repository / ".dovo" / "project.json",
            ProjectIdentity(id="project-p", created_at=datetime(2026, 1, 1, tzinfo=UTC)),
        )
        assert resolve_workspace_paths(repository_paths, global_paths) == built
