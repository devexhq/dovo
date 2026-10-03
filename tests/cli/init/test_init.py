"""Single-tier CLI integration tests for dovo init."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.constants import DOVO_GITIGNORE_CONTENT, DOVO_GITIGNORE_TRACKED_ENTRIES
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.core.bootstrap.models import BootstrapOutcome
from dovo.core.db.connection import resolve_db_path
from dovo.core.project.models import PROJECT_ID_REGEX, ProjectIdentityProvisionStatus

_SEEDED_TEMPLATE_RELATIVE_PATHS = [
    "catalog/blueprints/dovo/fix-tests.yml",
    "catalog/blueprints/dovo/review-fix.yml",
    "catalog/steps/dovo/ai-code-patcher.yml",
    "catalog/steps/dovo/ai-planner.yml",
    "catalog/steps/dovo/ai-reviewer.yml",
    "catalog/steps/dovo/download-artifact.yml",
    "catalog/steps/dovo/git-sync-base.yml",
    "catalog/steps/dovo/run-tests.yml",
    "catalog/steps/dovo/upload-artifact.yml",
]


def _init_git_repo(path: Path) -> None:
    """Initialize a bare git repository at path with a committable identity."""
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True, text=True
    )


def _dovo_dir(workspace: Path) -> Path:
    return workspace / ".dovo"


def _seeded_template_paths(workspace: Path) -> list[Path]:
    return [_dovo_dir(workspace) / rel for rel in _SEEDED_TEMPLATE_RELATIVE_PATHS]


class InitCliIntegrationTests:
    """Typer runner integration tests for dovo init."""

    def test_init_cli_fresh_git_repo_creates_workspace_exits_zero(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo init: fresh git repo, exit 0, config.json created and centralized database initialized, dispatched WorkspaceInitResult.bootstrap_result.outcome=INITIALIZED."""
        _init_git_repo(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        dovo_dir = _dovo_dir(tmp_path)
        assert (dovo_dir / "config.json").exists()
        assert resolve_db_path(resolve_global_paths(None)).is_file()
        assert len(dispatch_spy) == 1
        res = dispatch_spy[0]
        assert res.bootstrap_result.root_path == dovo_dir
        assert res.bootstrap_result.outcome == BootstrapOutcome.INITIALIZED
        assert res.bootstrap_result.root_created is True
        assert res.bootstrap_result.dirs_created == [dovo_dir / ".meta"]
        assert res.bootstrap_result.dirs_existing == []
        assert res.bootstrap_result.repaired is False
        assert res.bootstrap_result.gitignore_created is True

        assert res.identity_result is not None
        assert res.identity_result.status == ProjectIdentityProvisionStatus.CREATED
        assert res.identity_result.identity is not None

        assert res.config_result.created is True
        assert res.config_result.skipped_existing is False
        assert res.config_result.config_path == dovo_dir / "config.json"

        assert res.seed_result.created_files == _seeded_template_paths(tmp_path)
        assert res.seed_result.skipped_existing_files == []

        assert res.failure_mode is None
        assert res.errors == []
        assert res.warnings == []
        assert res.fixes == []

    def test_init_cli_rerun_without_flags_skips_existing_exits_zero(
        self, cli_runner: CliRunner, tmp_path: Path, dispatch_spy: list[Any]
    ) -> None:
        """dovo init: second invocation with no flags exits 0, dispatched WorkspaceInitResult.bootstrap_result.outcome=ALREADY_INITIALIZED."""
        _init_git_repo(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init"])
        dispatch_spy.clear()

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        dovo_dir = _dovo_dir(tmp_path)
        assert len(dispatch_spy) == 1
        res = dispatch_spy[0]
        assert res.bootstrap_result.root_path == dovo_dir
        assert res.bootstrap_result.outcome == BootstrapOutcome.ALREADY_INITIALIZED
        assert res.bootstrap_result.root_created is False
        assert res.bootstrap_result.dirs_created == []
        assert res.bootstrap_result.dirs_existing == [dovo_dir / ".meta"]
        assert res.bootstrap_result.repaired is False
        assert res.bootstrap_result.gitignore_created is False

        assert res.identity_result is not None
        assert res.identity_result.status == ProjectIdentityProvisionStatus.PRESERVED

        assert res.config_result.created is False
        assert res.config_result.skipped_existing is True
        assert res.config_result.config_path == dovo_dir / "config.json"

        assert res.seed_result.created_files == []
        assert res.seed_result.skipped_existing_files == _seeded_template_paths(tmp_path)

        assert res.failure_mode is None
        assert res.errors == []
        assert res.warnings == []
        assert res.fixes == []

    def test_init_cli_overwrite_flag_regenerates_default_config_exits_zero(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """dovo init --overwrite: reverts a mutated project.name back to the workspace directory name, exit 0."""
        _init_git_repo(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init"])
        config_path = tmp_path / ".dovo" / "config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_data["project"]["name"] = "mutated-name"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--overwrite"])

        assert result.exit_code == 0
        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        assert persisted["project"]["name"] == tmp_path.name

    def test_init_cli_outside_git_repo_exits_one(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """dovo init: non-git directory, exit 1, 'not a valid Git repository' in stdout, no .dovo/ created."""
        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 1
        assert "not a valid Git repository" in result.stdout
        assert not (tmp_path / ".dovo").exists()

    def test_init_cli_json_format_emits_literal_wire_payload(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """dovo init --format json: fresh git repo with an explicit --id, stdout equals the literal WorkspaceInitView envelope."""
        _init_git_repo(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "test-project", "--format", "json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "event_type": "WorkspaceInitResult",
            "payload": {
                "ok": True,
                "root_path": str(tmp_path / ".dovo"),
                "root_path_relative": ".dovo",
                "bootstrap_outcome": "initialized",
                "dirs_created": [".dovo/.meta"],
                "project_id": "test-project",
                "identity_path_relative": ".dovo/project.json",
                "identity_preserved": False,
                "gitignore_path_relative": ".dovo/.gitignore",
                "gitignore_tracked_entries": list(DOVO_GITIGNORE_TRACKED_ENTRIES),
                "config_created": True,
                "config_overwritten": False,
                "config_repaired": False,
                "config_skipped_existing": False,
                "config_path_relative": ".dovo/config.json",
                "inserted_keys": [],
                "seeded_files": [
                    ".dovo/catalog/blueprints/dovo/fix-tests.yml",
                    ".dovo/catalog/blueprints/dovo/review-fix.yml",
                    ".dovo/catalog/steps/dovo/ai-code-patcher.yml",
                    ".dovo/catalog/steps/dovo/ai-planner.yml",
                    ".dovo/catalog/steps/dovo/ai-reviewer.yml",
                    ".dovo/catalog/steps/dovo/download-artifact.yml",
                    ".dovo/catalog/steps/dovo/git-sync-base.yml",
                    ".dovo/catalog/steps/dovo/run-tests.yml",
                    ".dovo/catalog/steps/dovo/upload-artifact.yml",
                ],
                "skipped_seed_files": [],
                "overwritten_seed_files": [],
                "failure_mode": None,
                "errors": [],
                "warnings": [],
                "fixes": [],
            },
        }

    def test_init_cli_with_id_flag_creates_project_json_with_explicit_id(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """dovo init --id: exit 0; .dovo/project.json contains the explicit id."""
        _init_git_repo(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "custom-id"])

        assert result.exit_code == 0
        project_json = json.loads((_dovo_dir(tmp_path) / "project.json").read_text(encoding="utf-8"))
        assert project_json["id"] == "custom-id"

    def test_init_cli_with_invalid_id_flag_exits_two(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """dovo init --id: malformed slug exits 2, stdout contains the literal invalid-id message, no project.json written."""
        _init_git_repo(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "Bad Id!"])

        assert result.exit_code == 2
        assert "Invalid project ID: must match" in result.stdout
        assert "Pass a valid --id matching" in result.stdout
        assert not (_dovo_dir(tmp_path) / "project.json").exists()

    def test_init_cli_without_id_flag_generates_slug(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """dovo init: without --id, .dovo/project.json's id matches PROJECT_ID_REGEX."""
        _init_git_repo(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        project_json = json.loads((_dovo_dir(tmp_path) / "project.json").read_text(encoding="utf-8"))
        assert re.match(PROJECT_ID_REGEX, project_json["id"]) is not None

    def test_init_cli_force_without_id_preserves_identity_and_config(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """dovo init --force (no --id): rerun leaves project.json's id and a prior mutated config.json project.name both unchanged, exit 0."""
        _init_git_repo(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init"])
        project_json_path = _dovo_dir(tmp_path) / "project.json"
        original_id = json.loads(project_json_path.read_text(encoding="utf-8"))["id"]
        config_path = _dovo_dir(tmp_path) / "config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_data["project"]["name"] = "mutated-name"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--force"])

        assert result.exit_code == 0
        assert json.loads(project_json_path.read_text(encoding="utf-8"))["id"] == original_id
        assert json.loads(config_path.read_text(encoding="utf-8"))["project"]["name"] == "mutated-name"

    def test_init_cli_force_with_id_overwrites_identity_only(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """dovo init --id new-id --force: rerun rewrites project.json's id to new-id but leaves a prior mutated config.json project.name unchanged, exit 0."""
        _init_git_repo(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init"])
        config_path = _dovo_dir(tmp_path) / "config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_data["project"]["name"] = "mutated-name"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init", "--id", "new-id", "--force"])

        assert result.exit_code == 0
        project_json = json.loads((_dovo_dir(tmp_path) / "project.json").read_text(encoding="utf-8"))
        assert project_json["id"] == "new-id"
        assert json.loads(config_path.read_text(encoding="utf-8"))["project"]["name"] == "mutated-name"

    def test_init_cli_fresh_git_repo_seeds_dovo_gitignore(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """dovo init: exit 0; .dovo/.gitignore exists with the literal DOVO_GITIGNORE_CONTENT; repository-root .gitignore is not created/modified."""
        _init_git_repo(tmp_path)
        root_gitignore = tmp_path / ".gitignore"
        assert not root_gitignore.exists()

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        assert (_dovo_dir(tmp_path) / ".gitignore").read_text(encoding="utf-8") == DOVO_GITIGNORE_CONTENT
        assert not root_gitignore.exists()

    def test_init_cli_rerun_without_force_preserves_existing_project_id(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """dovo init: rerun without --force, .dovo/project.json's id unchanged across two dovo init invocations."""
        _init_git_repo(tmp_path)
        cli_runner.invoke(app, ["-p", str(tmp_path), "init"])
        project_json_path = _dovo_dir(tmp_path) / "project.json"
        original_id = json.loads(project_json_path.read_text(encoding="utf-8"))["id"]

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        assert json.loads(project_json_path.read_text(encoding="utf-8"))["id"] == original_id

    def test_init_cli_fresh_git_repo_reports_gitignore_tracked_entries(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """dovo init: fresh git repo stdout reports .dovo/.gitignore populated and names config.json, project.json, catalog/ as tracked."""
        _init_git_repo(tmp_path)

        result = cli_runner.invoke(app, ["-p", str(tmp_path), "init"])

        assert result.exit_code == 0
        assert "tracking only" in result.stdout
        for entry in DOVO_GITIGNORE_TRACKED_ENTRIES:
            assert entry in result.stdout
