"""Single-tier CLI integration tests for wt artifacts prune."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from worktree.cli import app
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.artifacts.models import ArtifactsPruneResult, ArtifactsPruneStatus
from worktree.core.db import WorktreeDb
from worktree.core.project.services.storage import resolve_workspace_paths


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _db_for(workspace: Path) -> WorktreeDb:
    """Construct a WorktreeDb bound to workspace's resolved database file and project id."""
    paths = _paths_for(workspace)
    return WorktreeDb(database_file=paths.database_file, project_id=paths.project_id)


def _seed_expired_artifact(workspace: Path) -> None:
    """Persist an already-expired artifact row and its on-disk directory."""
    artifacts_dir = _paths_for(workspace).artifacts_dir
    artifact_dir = artifacts_dir / "wf_one" / "dist-packages"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "manifest.json").write_text("{}", encoding="utf-8")
    expired_at = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    _db_for(workspace).artifacts.create(
        "wf_one", "dist-packages", artifact_dir, size_bytes=1, file_count=1, expires_at=expired_at
    )


def _write_prune_config(workspace: Path, *, remove_expired_artifacts: bool) -> None:
    """Overwrite the workspace config.json to set prune.remove_expired_artifacts."""
    config_path = workspace / ".worktree" / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data.setdefault("prune", {})["remove_expired_artifacts"] = remove_expired_artifacts
    config_path.write_text(json.dumps(data), encoding="utf-8")


class ArtifactsPruneCliIntegrationTests:
    """Typer runner integration tests for wt artifacts prune."""

    def test_artifacts_prune_cli_no_expired_artifacts_exits_zero(
        self, cli_runner: CliRunner, artifacts_workspace: Path, dispatch_spy: list[Any]
    ) -> None:
        """wt artifacts prune: no expired artifacts exits 0, dispatches ArtifactsPruneResult(status=OK, items=[])."""
        _write_prune_config(artifacts_workspace, remove_expired_artifacts=True)

        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "prune"])

        assert result.exit_code == 0
        prune_results = [item for item in dispatch_spy if isinstance(item, ArtifactsPruneResult)]
        assert prune_results == [
            ArtifactsPruneResult(status=ArtifactsPruneStatus.OK, dry_run=False, force=False, items=[])
        ]

    def test_artifacts_prune_cli_force_flag_bypasses_disabled_config(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """wt artifacts prune --force: with prune.remove_expired_artifacts=False in config.json, an expired artifact is still pruned; exit 0."""
        _write_prune_config(artifacts_workspace, remove_expired_artifacts=False)
        _seed_expired_artifact(artifacts_workspace)

        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "prune", "--force"])

        assert result.exit_code == 0
        assert _db_for(artifacts_workspace).artifacts.get("wf_one", "dist-packages") is None

    def test_artifacts_prune_cli_disabled_config_without_force_exits_zero(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """wt artifacts prune: prune.remove_expired_artifacts=False and no --force leaves the expired artifact untouched, exit 0."""
        _write_prune_config(artifacts_workspace, remove_expired_artifacts=False)
        _seed_expired_artifact(artifacts_workspace)

        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "prune"])

        assert result.exit_code == 0
        assert _db_for(artifacts_workspace).artifacts.get("wf_one", "dist-packages") is not None

    def test_artifacts_prune_cli_dry_run_flag_binds_without_deleting(
        self, cli_runner: CliRunner, artifacts_workspace: Path
    ) -> None:
        """wt artifacts prune --dry-run: with pruning enabled, the expired artifact is reported but not deleted."""
        _write_prune_config(artifacts_workspace, remove_expired_artifacts=True)
        _seed_expired_artifact(artifacts_workspace)

        result = cli_runner.invoke(app, ["-p", str(artifacts_workspace), "artifacts", "prune", "--dry-run"])

        assert result.exit_code == 0
        assert _db_for(artifacts_workspace).artifacts.get("wf_one", "dist-packages") is not None
