"""Global pytest fixtures and test execution harness configuration."""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.common.constants import REQUIRED_SUBDIRS
from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.agents.registry import PROVIDERS
from dovo.core.artifacts import Artifacts
from dovo.core.artifacts.models import ArtifactProvenance
from dovo.core.artifacts.store import ArtifactStore
from dovo.core.config.models import ConfigTier
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.project.services.identity import generate_project_identity, save_project_identity
from tests.harness import initialized_workspace_paths, patch_claude_probe
from tests.harness.builders import WorkspaceBuilder, init_git_repository

PublishArtifact = Callable[[str, dict[str, str]], None]


@pytest.fixture(autouse=True)
def _isolated_dovo_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect DOVO_HOME to an ephemeral per-test directory.

    The centralized database and other global-path resolution default to
    DOVO_HOME (or ~/.dovo). Without this override every test would
    read and write the real machine's global Dovo directory.
    """
    monkeypatch.setenv("DOVO_HOME", str(tmp_path_factory.mktemp("dovo_home")))


@pytest.fixture(autouse=True)
def _logged_out_claude_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the Claude login probe subprocess as logged out so no test spawns a real `claude`."""

    def _logged_out(argv: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"")

    patch_claude_probe(monkeypatch, _logged_out)


@pytest.fixture
def model_required_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch PROVIDERS['copilot'] so requires_model is True for the test."""
    monkeypatch.setitem(PROVIDERS, "copilot", dataclasses.replace(PROVIDERS["copilot"], requires_model=True))


@pytest.fixture
def workspace_paths_factory() -> Callable[[Path, Path | None], WorkspacePaths]:
    """Return a builder that resolves a WorkspacePaths snapshot for an arbitrary repository/global root pair."""

    def _build(root: Path, global_root: Path | None = None) -> WorkspacePaths:
        return initialized_workspace_paths(root, global_root)

    return _build


@pytest.fixture
def isolated_workspace(tmp_path: Path) -> Path:
    """Create a clean filesystem root with standard .dovo/ structure.

    Args:
        tmp_path: Ephemeral pytest directory fixture.

    Returns:
        Path to the isolated workspace root containing .dovo/.
    """
    workspace = tmp_path / "workspace"
    dot_dovo = workspace / ".dovo"
    dot_dovo.mkdir(parents=True, exist_ok=True)

    for subdir in REQUIRED_SUBDIRS:
        (dot_dovo / subdir).mkdir(parents=True, exist_ok=True)

    (dot_dovo / "worktrees").mkdir(parents=True, exist_ok=True)
    (dot_dovo / "catalog").mkdir(parents=True, exist_ok=True)

    save_project_identity(dot_dovo / "project.json", generate_project_identity())

    return workspace


@pytest.fixture
def workspace_paths(
    isolated_workspace: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for isolated_workspace's initialized project identity."""
    return workspace_paths_factory(isolated_workspace, None)


@pytest.fixture
def artifacts_paths(tmp_path: Path) -> WorkspacePaths:
    """Return WorkspacePaths for a freshly initialized, project-scoped workspace."""
    workspace_root = WorkspaceBuilder(tmp_path / "artifacts_repo_workspace").build()
    return initialized_workspace_paths(workspace_root)


@pytest.fixture
def artifacts_repository(artifacts_paths: WorkspacePaths) -> ArtifactsRepository:
    """ArtifactsRepository bound to artifacts_paths' project database."""
    return ArtifactsRepository(db_path=artifacts_paths.database_file, project_id=artifacts_paths.project_id)


@pytest.fixture
def artifact_store(artifacts_paths: WorkspacePaths, artifacts_repository: ArtifactsRepository) -> ArtifactStore:
    """ArtifactStore for session 'wf_1' over artifacts_paths and artifacts_repository."""
    return ArtifactStore(Artifacts(artifacts_paths, artifacts_repository), "wf_1")


@pytest.fixture
def publish_artifact(artifact_store: ArtifactStore, tmp_path: Path) -> PublishArtifact:
    """Publish a named artifact holding the given files as the store's next revision."""
    counter = {"count": 0}

    def publish(name: str, files: dict[str, str]) -> None:
        counter["count"] += 1
        worktree = tmp_path / f"worktree-{counter['count']}"
        worktree.mkdir()
        for relative_path, content in files.items():
            (worktree / relative_path).write_text(content, encoding="utf-8")
        result = artifact_store.publish(
            worktree, name=name, path_glob="*", retention_days=None, provenance=ArtifactProvenance()
        )
        assert result.ok

    return publish


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialize a bare-minimum Git repository with user identity and root commit on main.

    Args:
        tmp_path: Ephemeral pytest directory fixture.

    Returns:
        Path to the initialized Git repository root.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    init_git_repository(repo, initial_files={"README.md": "# Test Repo\n"})
    return repo


@pytest.fixture
def write_tier_config() -> Callable[[ConfigTier, dict[str, Any] | str], Path]:
    """Write a Global or User tier config.json under the test's isolated DOVO_HOME; returns its path."""

    def _write(tier: ConfigTier, payload: dict[str, Any] | str) -> Path:
        global_paths = resolve_global_paths(None)
        tier_dir = global_paths.global_dir if tier is ConfigTier.GLOBAL else global_paths.user_dir
        config_path = tier_dir / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        return config_path

    return _write


@pytest.fixture
def cli_runner() -> CliRunner:
    """Provide a preconfigured Typer CliRunner with width 160 and NO_COLOR=1.

    Returns:
        CliRunner instance configured with NO_COLOR=1 and COLUMNS=160.
    """
    return CliRunner(env={"NO_COLOR": "1", "COLUMNS": "160"})
