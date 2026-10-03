"""Global pytest fixtures and test execution harness configuration."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dovo.common.constants import REQUIRED_SUBDIRS
from dovo.common.filesystem.models import RepositoryPaths, WorkspacePaths
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.config.models import ConfigTier
from dovo.core.project.services.identity import generate_project_identity, save_project_identity
from dovo.core.project.services.storage import resolve_workspace_paths


@pytest.fixture(autouse=True)
def _isolated_dovo_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect DOVO_HOME to an ephemeral per-test directory.

    The centralized database and other global-path resolution default to
    DOVO_HOME (or ~/.dovo). Without this override every test would
    read and write the real machine's global Dovo directory.
    """
    monkeypatch.setenv("DOVO_HOME", str(tmp_path_factory.mktemp("dovo_home")))


@pytest.fixture
def workspace_paths_factory() -> Callable[[Path, Path | None], WorkspacePaths]:
    """Return a builder that resolves a WorkspacePaths snapshot for an arbitrary repository/global root pair."""

    def _build(root: Path, global_root: Path | None = None) -> WorkspacePaths:
        return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(global_root))

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

    (dot_dovo / "sandboxes").mkdir(parents=True, exist_ok=True)
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
def git_repo(tmp_path: Path) -> Path:
    """Initialize a bare-minimum Git repository with user identity and root commit on main.

    Args:
        tmp_path: Ephemeral pytest directory fixture.

    Returns:
        Path to the initialized Git repository root.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    readme_path = repo / "README.md"
    readme_path.write_text("# Test Repo\n", encoding="utf-8")

    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

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
