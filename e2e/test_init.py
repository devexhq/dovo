"""End-to-end tests for dovo init and project workspace setup using git repository fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.conftest import DOVO_HOME, DovoRunner


@pytest.mark.e2e
@pytest.mark.fixture
class InitCliTests:
    """E2E tests for dovo init flags, path resolution, and identity management."""

    def test_init_on_git_project_provisions_state_and_honors_dovo_home(
        self, run_dovo: DovoRunner, git_project: Path
    ) -> None:
        """Scenario: Initialize new project in a Git repository.

        Given a clean, uninitialized Git repository and an isolated DOVO_HOME
        When dovo init is executed in the repository root
        Then the command exits 0, creates local project configuration files, and leaves global state isolated
        """
        result = run_dovo(["init"], cwd=git_project)

        assert result.exit_code == 0
        assert "Initialized Dovo at .dovo" in result.stdout

        project_json_path = git_project / ".dovo" / "project.json"
        config_json_path = git_project / ".dovo" / "config.json"
        gitignore_path = git_project / ".dovo" / ".gitignore"

        assert project_json_path.is_file()
        assert config_json_path.is_file()
        assert gitignore_path.is_file()

        with open(project_json_path, encoding="utf-8") as f:
            project_data = json.load(f)
        assert "id" in project_data
        assert project_data["id"] != ""

        # Verify global state resides in DOVO_HOME, not user home
        assert DOVO_HOME.is_dir()
        assert not (DOVO_HOME / ".git").exists()

    def test_init_with_path_option_targets_workspace_from_external_cwd(
        self, run_dovo: DovoRunner, git_project: Path, clean_e2e_env: Path
    ) -> None:
        """Scenario: Target external workspace directory using path option.

        Given an uninitialized Git repository and an external working directory
        When dovo is invoked with -p targeting the repository path
        Then the targeted workspace is initialized and the caller's working directory remains unmutated
        """
        result = run_dovo(["-p", str(git_project), "init"], cwd=clean_e2e_env)

        assert result.exit_code == 0
        assert (git_project / ".dovo" / "project.json").is_file()
        assert not (clean_e2e_env / ".dovo").exists()

    def test_re_init_preserves_existing_project_identity(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Re-initializing an existing workspace preserves project identity.

        Given a previously initialized Dovo workspace with an established project ID
        When dovo init is executed again in the same workspace
        Then the command exits 0 and the existing project ID in project.json is preserved
        """
        project_json_path = initialized_project / ".dovo" / "project.json"
        with open(project_json_path, encoding="utf-8") as f:
            initial_project_data = json.load(f)
        initial_id = initial_project_data["id"]

        result = run_dovo(["init"], cwd=initialized_project)

        assert result.exit_code == 0
        with open(project_json_path, encoding="utf-8") as f:
            re_read_project_data = json.load(f)

        assert re_read_project_data["id"] == initial_id

    def test_init_with_custom_id_and_display_name_provisions_identity(
        self, run_dovo: DovoRunner, git_project: Path
    ) -> None:
        """Scenario: Initialize workspace with custom ID and display name.

        Given an uninitialized Git repository
        When dovo init is executed with explicit --id and --display-name options
        Then the command exits 0 and provisions the specified identity in project.json
        """
        result = run_dovo(
            ["init", "--id", "custom-slug-e2e", "--display-name", "Custom E2E Display Name"],
            cwd=git_project,
        )

        assert result.exit_code == 0
        project_json_path = git_project / ".dovo" / "project.json"
        with open(project_json_path, encoding="utf-8") as f:
            project_data = json.load(f)

        assert project_data["id"] == "custom-slug-e2e"
        assert project_data.get("display_name") == "Custom E2E Display Name"

    def test_init_with_invalid_slug_rejects_with_exit_code_2(self, run_dovo: DovoRunner, git_project: Path) -> None:
        """Scenario: Reject invalid slug during project initialization.

        Given an uninitialized Git repository
        When dovo init is executed with an invalid slug containing spaces and special characters
        Then the command exits 2 and creates no project.json file
        """
        result = run_dovo(["init", "--id", "Invalid Slug!"], cwd=git_project)

        assert result.exit_code == 2
        assert not (git_project / ".dovo" / "project.json").exists()

    def test_uninitialized_git_project_status_reports_uninitialized(
        self, run_dovo: DovoRunner, git_project: Path
    ) -> None:
        """Scenario: Inspect status of uninitialized Git repository.

        Given an uninitialized Git repository
        When dovo status --format json is executed
        Then the command exits 0, reports uninitialized health in JSON, and creates no project state files
        """
        result = run_dovo(["status", "--format", "json"], cwd=git_project)

        assert result.exit_code == 0
        status_envelope = json.loads(result.stdout)
        payload = status_envelope.get("payload", {})
        assert payload.get("health") == "uninitialized"
        assert payload.get("config_status") == "not_found"
        assert not (git_project / ".dovo" / "project.json").exists()
        assert not (git_project / ".dovo" / "config.json").exists()

    def test_init_repair_preserves_configured_values(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Repair configuration while preserving customized values.

        Given an initialized workspace with a missing section and customized settings in config.json
        When dovo init --repair is executed
        Then the command exits 0, restores missing configuration keys, and preserves existing customized values
        """
        config_path = initialized_project / ".dovo" / "config.json"
        with open(config_path, encoding="utf-8") as f:
            config_data = json.load(f)

        # Modify a known value and remove a required section
        config_data["agent"]["model"] = "custom-test-model-e2e"
        config_data.pop("worktree", None)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f)

        result = run_dovo(["init", "--repair"], cwd=initialized_project)

        assert result.exit_code == 0
        with open(config_path, encoding="utf-8") as f:
            repaired_config = json.load(f)

        assert repaired_config["agent"]["model"] == "custom-test-model-e2e"
        assert "worktree" in repaired_config

    def test_init_overwrite_replaces_config_preserving_identity(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Overwrite configuration while preserving project identity.

        Given an initialized workspace with custom configuration
        When dovo init --overwrite is executed
        Then the command exits 0, resets config.json to defaults, and preserves the project ID in project.json
        """
        project_path = initialized_project / ".dovo" / "project.json"
        config_path = initialized_project / ".dovo" / "config.json"

        with open(project_path, encoding="utf-8") as f:
            initial_project = json.load(f)
        initial_id = initial_project["id"]

        with open(config_path, encoding="utf-8") as f:
            config_data = json.load(f)
        config_data["agent"]["model"] = "temporary-override"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f)

        result = run_dovo(["init", "--overwrite"], cwd=initialized_project)

        assert result.exit_code == 0
        with open(project_path, encoding="utf-8") as f:
            re_read_project = json.load(f)
        with open(config_path, encoding="utf-8") as f:
            re_read_config = json.load(f)

        assert re_read_project["id"] == initial_id
        assert re_read_config["agent"]["model"] != "temporary-override"

    def test_init_force_with_new_id_overrides_project_identity(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Force project ID override on existing workspace.

        Given an initialized workspace with an existing project ID
        When dovo init is executed with --force and a new --id option
        Then the command exits 0 and updates project.json with the new project ID
        """
        result = run_dovo(["init", "--force", "--id", "forced-new-slug"], cwd=initialized_project)

        assert result.exit_code == 0
        project_path = initialized_project / ".dovo" / "project.json"
        with open(project_path, encoding="utf-8") as f:
            project_data = json.load(f)

        assert project_data["id"] == "forced-new-slug"

    def test_initialized_git_project_status_reports_ok(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Inspect status of initialized workspace.

        Given a fully initialized Dovo workspace
        When dovo status --format json is executed
        Then the command exits 0 and reports ok health and config status in the JSON envelope
        """
        result = run_dovo(["status", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        payload = envelope.get("payload", {})
        assert payload.get("health") == "ok"
        assert payload.get("config_status") == "ok"

    def test_initialized_git_project_doctor_emits_parseable_json(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Run diagnostic health checks on initialized workspace.

        Given an initialized Dovo workspace
        When dovo doctor --format json is executed
        Then the command emits a parseable DiagnosticsReport JSON payload containing check results
        """
        result = run_dovo(["doctor", "--format", "json"], cwd=initialized_project)

        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "DiagnosticsReport"
        payload = envelope.get("payload", {})
        assert "checks" in payload
        assert len(payload["checks"]) > 0
