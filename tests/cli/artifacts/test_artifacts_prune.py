"""Single-tier CLI integration tests for dovo artifacts prune."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from dovo.cli import app
from dovo.core.artifacts.models import ArtifactsPruneResult, ArtifactsPruneStatus
from tests.harness.workspace_paths import initialized_workspace_db, initialized_workspace_paths


def _seed_expired_artifact(workspace: Path) -> None:
    """Persist an already-expired artifact row and its on-disk directory."""
    artifacts_dir = initialized_workspace_paths(workspace).artifacts_dir
    artifact_dir = artifacts_dir / "wf_one" / "dist-packages" / "1"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "manifest.json").write_text("{}", encoding="utf-8")
    expired_at = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    initialized_workspace_db(workspace).artifacts.create(
        "wf_one",
        "dist-packages",
        artifact_dir,
        revision=1,
        size_bytes=1,
        file_count=1,
        expires_at=expired_at,
        digest="digest",
        step_id=None,
        attempt=None,
        loop_iteration=None,
    )


def _write_prune_config(workspace: Path, *, remove_expired_artifacts: bool) -> None:
    """Overwrite the workspace config.json to set prune.remove_expired_artifacts."""
    config_path = workspace / ".dovo" / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data.setdefault("prune", {})["remove_expired_artifacts"] = remove_expired_artifacts
    config_path.write_text(json.dumps(data), encoding="utf-8")


class ArtifactsPruneCliIntegrationTests:
    """Typer runner integration tests for dovo artifacts prune."""

    def test_artifacts_prune_cli_no_expired_artifacts_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path, dispatch_spy: list[object]
    ) -> None:
        _write_prune_config(cli_workspace, remove_expired_artifacts=True)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "artifacts", "prune"])

        assert result.exit_code == 0
        prune_results = [item for item in dispatch_spy if isinstance(item, ArtifactsPruneResult)]
        assert prune_results == [
            ArtifactsPruneResult(status=ArtifactsPruneStatus.OK, dry_run=False, force=False, items=[])
        ]

    def test_artifacts_prune_cli_force_flag_bypasses_disabled_config(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _write_prune_config(cli_workspace, remove_expired_artifacts=False)
        _seed_expired_artifact(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "artifacts", "prune", "--force"])

        assert result.exit_code == 0
        assert initialized_workspace_db(cli_workspace).artifacts.get("wf_one", "dist-packages") is None

    def test_artifacts_prune_cli_disabled_config_without_force_exits_zero(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _write_prune_config(cli_workspace, remove_expired_artifacts=False)
        _seed_expired_artifact(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "artifacts", "prune"])

        assert result.exit_code == 0
        assert initialized_workspace_db(cli_workspace).artifacts.get("wf_one", "dist-packages") is not None

    def test_artifacts_prune_cli_dry_run_flag_binds_without_deleting(
        self, cli_runner: CliRunner, cli_workspace: Path
    ) -> None:
        _write_prune_config(cli_workspace, remove_expired_artifacts=True)
        _seed_expired_artifact(cli_workspace)

        result = cli_runner.invoke(app, ["-p", str(cli_workspace), "artifacts", "prune", "--dry-run"])

        assert result.exit_code == 0
        assert initialized_workspace_db(cli_workspace).artifacts.get("wf_one", "dist-packages") is not None
