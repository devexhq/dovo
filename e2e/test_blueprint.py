"""End-to-end tests for dovo blueprint catalog management across all tiers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.conftest import DOVO_HOME, DovoRunner


@pytest.mark.e2e
@pytest.mark.fixture
class BlueprintCliTests:
    """E2E tests for blueprint creation, validation, catalog listing, inspection, and deletion."""

    def test_blueprint_create_creates_repo_tier_blueprint(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Create a project blueprint at the repository tier.

        Given an initialized Dovo workspace
        When dovo blueprint create --name custom-test-bp --format json is executed
        Then the command exits 0, returns a CatalogCreateResult envelope, and creates the blueprint YAML file
        """
        result = run_dovo(
            ["blueprint", "create", "--name", "custom-test-bp", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogCreateResult"
        payload = envelope.get("payload", {})
        assert payload.get("item", {}).get("name") == "custom-test-bp"

        blueprint_file = initialized_project / ".dovo" / "catalog" / "blueprints" / "custom-test-bp.yml"
        assert blueprint_file.is_file()

    def test_blueprint_validate_succeeds_for_valid_definition(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Validate a valid blueprint definition.

        Given an initialized workspace containing a newly created blueprint
        When dovo blueprint validate custom-valid-bp --format json is executed
        Then the command exits 0 and reports valid status PASSED in the validation payload
        """
        run_dovo(["blueprint", "create", "--name", "custom-valid-bp"], cwd=initialized_project)

        result = run_dovo(
            ["blueprint", "validate", "custom-valid-bp", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogValidateResult"
        payload = envelope.get("payload", {})
        assert payload.get("valid") is True
        assert payload.get("status_label") == "PASSED"

    def test_blueprint_validate_exit_codes_for_invalid_schema_and_missing_target(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Validate exit codes for invalid schema and missing target files.

        Given an initialized workspace
        When dovo blueprint validate is run on an invalid schema, it exits 1; when run on a missing target, it exits 2
        Then the exit codes correctly distinguish schema violations (1) from missing catalog items (2)
        """
        # Invalid schema exit code 1
        bad_bp_path = initialized_project / "invalid-bp.yml"
        bad_bp_path.write_text(
            "version: '1.0'\nname: bad-bp\nsteps:\n  - name: s1\n    type: unknown_type\n",
            encoding="utf-8",
        )
        invalid_res = run_dovo(["blueprint", "validate", str(bad_bp_path)], cwd=initialized_project)
        assert invalid_res.exit_code == 1

        # Missing target exit code 2
        missing_res = run_dovo(["blueprint", "validate", "non-existent-blueprint-target"], cwd=initialized_project)
        assert missing_res.exit_code == 2

    def test_blueprint_list_includes_created_blueprint(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: List blueprint catalog items across tiers.

        Given an initialized workspace with a newly created blueprint
        When dovo blueprint list --format json is executed
        Then the command exits 0 and the created blueprint appears in the catalog items array
        """
        run_dovo(["blueprint", "create", "--name", "listed-test-bp"], cwd=initialized_project)

        result = run_dovo(["blueprint", "list", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogListResult"
        items = envelope.get("payload", {}).get("items", [])
        names = [item["name"] for item in items]
        assert "listed-test-bp" in names

    def test_blueprint_show_returns_metadata_and_yaml_content(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Show metadata and definition content of a blueprint.

        Given an initialized workspace with a created blueprint
        When dovo blueprint show shown-test-bp --format json is executed
        Then the command exits 0 and returns an envelope containing item metadata and raw YAML content
        """
        run_dovo(["blueprint", "create", "--name", "shown-test-bp"], cwd=initialized_project)

        result = run_dovo(["blueprint", "show", "shown-test-bp", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogShowResult"
        payload = envelope.get("payload", {})
        assert payload.get("item", {}).get("name") == "shown-test-bp"
        assert "content" in payload
        assert "name: shown-test-bp" in payload["content"]

    def test_blueprint_create_user_and_global_tiers(self, run_dovo: DovoRunner, initialized_project: Path) -> None:
        """Scenario: Create blueprint templates in user and global catalog tiers.

        Given an isolated DOVO_HOME environment
        When dovo blueprint create is executed with --user and --global flags
        Then blueprints are created under DOVO_HOME/user/catalog/ and DOVO_HOME/global/catalog/ respectively
        """
        res_user = run_dovo(
            ["blueprint", "create", "--name", "tier-user-bp", "--user", "--format", "json"],
            cwd=initialized_project,
        )
        assert res_user.exit_code == 0
        user_file = DOVO_HOME / "user" / "catalog" / "blueprints" / "tier-user-bp.yml"
        assert user_file.is_file()

        res_global = run_dovo(
            ["blueprint", "create", "--name", "tier-global-bp", "--global", "--format", "json"],
            cwd=initialized_project,
        )
        assert res_global.exit_code == 0
        global_file = DOVO_HOME / "global" / "catalog" / "blueprints" / "tier-global-bp.yml"
        assert global_file.is_file()

    def test_blueprint_delete_removes_repo_tier_blueprint(
        self, run_dovo: DovoRunner, initialized_project: Path
    ) -> None:
        """Scenario: Delete a repository-tier blueprint file from catalog.

        Given an initialized workspace with an existing blueprint
        When dovo blueprint delete delete-test-bp --force --format json is executed
        Then the command exits 0, reports deleted=True, and removes the file from disk
        """
        run_dovo(["blueprint", "create", "--name", "delete-test-bp"], cwd=initialized_project)
        bp_path = initialized_project / ".dovo" / "catalog" / "blueprints" / "delete-test-bp.yml"
        assert bp_path.is_file()

        result = run_dovo(
            ["blueprint", "delete", "delete-test-bp", "--force", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogDeleteResult"
        assert envelope.get("payload", {}).get("deleted") is True
        assert not bp_path.exists()
