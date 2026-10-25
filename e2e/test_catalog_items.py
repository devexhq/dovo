"""End-to-end tests for dovo step and blueprint catalog management across all tiers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from e2e.conftest import DOVO_HOME, DovoRunner


@dataclass(frozen=True)
class CatalogKind:
    """Command name, catalog layout and fixture content for one catalog item kind."""

    command: str
    catalog_dir: str
    suffix: str
    missing_target: str
    invalid_file_name: str
    invalid_content: str
    shown_content_marker: str


STEP = CatalogKind(
    command="step",
    catalog_dir="steps",
    suffix="step",
    missing_target="non-existent-step-target",
    invalid_file_name="invalid-step.yml",
    invalid_content="id: bad-step\ntype: unknown_type\n",
    shown_content_marker="shown-test-step",
)

BLUEPRINT = CatalogKind(
    command="blueprint",
    catalog_dir="blueprints",
    suffix="bp",
    missing_target="non-existent-blueprint-target",
    invalid_file_name="invalid-bp.yml",
    invalid_content="version: '1.0'\nname: bad-bp\nsteps:\n  - name: s1\n    type: unknown_type\n",
    shown_content_marker="name: shown-test-bp",
)


@pytest.mark.e2e
@pytest.mark.fixture
@pytest.mark.parametrize("kind", [pytest.param(STEP, id="step"), pytest.param(BLUEPRINT, id="blueprint")])
class CatalogItemCliTests:
    """E2E tests for catalog item creation, validation, listing, inspection, and deletion."""

    def test_create_creates_repo_tier_item(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        name = f"custom-test-{kind.suffix}"
        result = run_dovo(
            [kind.command, "create", "--name", name, "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogCreateResult"
        payload = envelope.get("payload", {})
        assert payload.get("item", {}).get("name") == name

        item_file = initialized_project / ".dovo" / "catalog" / kind.catalog_dir / f"{name}.yml"
        assert item_file.is_file()

    def test_validate_succeeds_for_valid_definition(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        name = f"custom-valid-{kind.suffix}"
        run_dovo([kind.command, "create", "--name", name], cwd=initialized_project)

        result = run_dovo(
            [kind.command, "validate", name, "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogValidateResult"
        payload = envelope.get("payload", {})
        assert payload.get("valid") is True
        assert payload.get("status_label") == "PASSED"

    def test_validate_exit_codes_for_invalid_schema_and_missing_target(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        """An invalid schema and a missing catalog item both exit 1."""
        bad_path = initialized_project / kind.invalid_file_name
        bad_path.write_text(kind.invalid_content, encoding="utf-8")
        invalid_res = run_dovo([kind.command, "validate", str(bad_path)], cwd=initialized_project)
        assert invalid_res.exit_code == 1

        missing_res = run_dovo([kind.command, "validate", kind.missing_target], cwd=initialized_project)
        assert missing_res.exit_code == 1

    def test_list_includes_created_item(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        name = f"listed-test-{kind.suffix}"
        run_dovo([kind.command, "create", "--name", name], cwd=initialized_project)

        result = run_dovo([kind.command, "list", "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogListResult"
        items = envelope.get("payload", {}).get("items", [])
        names = [item["name"] for item in items]
        assert name in names

    def test_show_returns_metadata_and_yaml_content(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        name = f"shown-test-{kind.suffix}"
        run_dovo([kind.command, "create", "--name", name], cwd=initialized_project)

        result = run_dovo([kind.command, "show", name, "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogShowResult"
        payload = envelope.get("payload", {})
        assert payload.get("item", {}).get("name") == name
        assert "content" in payload
        assert kind.shown_content_marker in payload["content"]

    def test_create_user_and_global_tiers(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        user_name = f"tier-user-{kind.suffix}"
        res_user = run_dovo(
            [kind.command, "create", "--name", user_name, "--user", "--format", "json"],
            cwd=initialized_project,
        )
        assert res_user.exit_code == 0
        user_file = DOVO_HOME / "user" / "catalog" / kind.catalog_dir / f"{user_name}.yml"
        assert user_file.is_file()

        global_name = f"tier-global-{kind.suffix}"
        res_global = run_dovo(
            [kind.command, "create", "--name", global_name, "--global", "--format", "json"],
            cwd=initialized_project,
        )
        assert res_global.exit_code == 0
        global_file = DOVO_HOME / "global" / "catalog" / kind.catalog_dir / f"{global_name}.yml"
        assert global_file.is_file()

    def test_delete_removes_repo_tier_item(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        name = f"delete-test-{kind.suffix}"
        run_dovo([kind.command, "create", "--name", name], cwd=initialized_project)
        item_path = initialized_project / ".dovo" / "catalog" / kind.catalog_dir / f"{name}.yml"
        assert item_path.is_file()

        result = run_dovo(
            [kind.command, "delete", name, "--force", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == "CatalogDeleteResult"
        assert envelope.get("payload", {}).get("deleted") is True
        assert not item_path.exists()
