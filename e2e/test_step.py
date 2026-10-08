"""End-to-end tests for dovo step catalog management across all tiers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.conftest import DOVO_HOME, DovoRunner


@pytest.mark.e2e
@pytest.mark.fixture
class StepCliTests:
    """E2E tests for step creation, validation, catalog listing, inspection, and deletion."""

    def test_step_create_creates_repo_tier_step(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Create a project step at the repository tier.

        Given an initialized Dovo workspace
        When dovo step create --name custom-test-step --format json is executed
        Then the command exits 0, returns a CatalogCreateResult envelope, and creates the step YAML file
        """
        result = run_dovo(
            ["step", "create", "--name", "custom-test-step", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogCreateResult"
        payload = envelope.get("payload", {})
        assert payload.get("item", {}).get("name") == "custom-test-step"

        step_file = initialized_project / ".dovo" / "catalog" / "steps" / "custom-test-step.yml"
        assert step_file.is_file()

    def test_step_validate_succeeds_for_valid_definition(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Validate a valid step definition.

        Given an initialized workspace containing a newly created step
        When dovo step validate custom-valid-step --format json is executed
        Then the command exits 0 and reports valid status PASSED in the validation payload
        """
        run_dovo(["step", "create", "--name", "custom-valid-step"], cwd=initialized_project)

        result = run_dovo(
            ["step", "validate", "custom-valid-step", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogValidateResult"
        payload = envelope.get("payload", {})
        assert payload.get("valid") is True
        assert payload.get("status_label") == "PASSED"

    def test_step_validate_exit_codes_for_invalid_schema_and_missing_target(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Validate exit codes for invalid schema and missing target files.

        Given an initialized workspace
        When dovo step validate is run on an invalid schema, it exits 1; when run on a missing target, it exits 2
        Then the exit codes correctly distinguish schema violations (1) from missing catalog items (2)
        """
        # Invalid schema exit code 1
        bad_step_path = initialized_project / "invalid-step.yml"
        bad_step_path.write_text(
            "id: bad-step\ntype: unknown_type\n",
            encoding="utf-8",
        )
        invalid_res = run_dovo(["step", "validate", str(bad_step_path)], cwd=initialized_project)
        assert invalid_res.exit_code == 1

        # Missing target exit code 2
        missing_res = run_dovo(["step", "validate", "non-existent-step-target"], cwd=initialized_project)
        assert missing_res.exit_code == 2

    def test_step_list_includes_created_step(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: List step catalog items across tiers.

        Given an initialized workspace with a newly created step
        When dovo step list --format json is executed
        Then the command exits 0 and the created step appears in the catalog items array
        """
        run_dovo(["step", "create", "--name", "listed-test-step"], cwd=initialized_project)

        result = run_dovo(["step", "list", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogListResult"
        items = envelope.get("payload", {}).get("items", [])
        names = [item["name"] for item in items]
        assert "listed-test-step" in names

    def test_step_show_returns_metadata_and_yaml_content(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Show metadata and definition content of a step.

        Given an initialized workspace with a created step
        When dovo step show shown-test-step --format json is executed
        Then the command exits 0 and returns an envelope containing item metadata and raw YAML content
        """
        run_dovo(["step", "create", "--name", "shown-test-step"], cwd=initialized_project)

        result = run_dovo(["step", "show", "shown-test-step", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogShowResult"
        payload = envelope.get("payload", {})
        assert payload.get("item", {}).get("name") == "shown-test-step"
        assert "content" in payload
        assert "shown-test-step" in payload["content"]

    def test_step_create_user_and_global_tiers(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Create step templates in user and global catalog tiers.

        Given an isolated DOVO_HOME environment
        When dovo step create is executed with --user and --global flags
        Then steps are created under DOVO_HOME/user/catalog/ and DOVO_HOME/global/catalog/ respectively
        """
        res_user = run_dovo(
            ["step", "create", "--name", "tier-user-step", "--user", "--format", "json"],
            cwd=initialized_project,
        )
        assert res_user.exit_code == 0
        user_file = DOVO_HOME / "user" / "catalog" / "steps" / "tier-user-step.yml"
        assert user_file.is_file()

        res_global = run_dovo(
            ["step", "create", "--name", "tier-global-step", "--global", "--format", "json"],
            cwd=initialized_project,
        )
        assert res_global.exit_code == 0
        global_file = DOVO_HOME / "global" / "catalog" / "steps" / "tier-global-step.yml"
        assert global_file.is_file()

    def test_step_delete_removes_repo_tier_step(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Delete a repository-tier step file from catalog.

        Given an initialized workspace with an existing step
        When dovo step delete delete-test-step --force --format json is executed
        Then the command exits 0, reports deleted=True, and removes the file from disk
        """
        run_dovo(["step", "create", "--name", "delete-test-step"], cwd=initialized_project)
        step_path = initialized_project / ".dovo" / "catalog" / "steps" / "delete-test-step.yml"
        assert step_path.is_file()

        result = run_dovo(
            ["step", "delete", "delete-test-step", "--force", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogDeleteResult"
        assert envelope.get("payload", {}).get("deleted") is True
        assert not step_path.exists()
