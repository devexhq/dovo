"""End-to-end tests for dovo step and blueprint catalog management across all tiers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from e2e.conftest import DOVO_HOME, DovoRunner, parse_json


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
        assert envelope.get("event_type") == f"{kind.command}.create"
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
        assert envelope.get("event_type") == f"{kind.command}.validate"
        payload = envelope.get("payload", {})
        assert payload.get("ok") is True
        assert "valid" not in payload
        assert "status_label" not in payload

    def test_validate_terminal_reports_valid_definition_without_banner(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        """The terminal ends with `<Kind> is valid.` and prints no PASSED banner."""
        name = f"custom-valid-terminal-{kind.suffix}"
        run_dovo([kind.command, "create", "--name", name], cwd=initialized_project)

        result = run_dovo([kind.command, "validate", name], cwd=initialized_project)

        assert result.exit_code == 0
        assert f"{kind.command.capitalize()} is valid." in result.stdout
        assert "PASSED" not in result.stdout

    def test_validate_exit_codes_for_invalid_schema_and_missing_target(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        """An invalid schema and a missing catalog item both exit 1, with no FAILED banner."""
        bad_path = initialized_project / kind.invalid_file_name
        bad_path.write_text(kind.invalid_content, encoding="utf-8")
        invalid_res = run_dovo([kind.command, "validate", str(bad_path)], cwd=initialized_project)
        assert invalid_res.exit_code == 1
        assert "FAILED" not in invalid_res.stdout
        assert f"{kind.command.capitalize()} is valid." not in invalid_res.stdout

        missing_res = run_dovo([kind.command, "validate", kind.missing_target], cwd=initialized_project)
        assert missing_res.exit_code == 1

    @pytest.mark.parametrize("subcommand", ["list", "ls"])
    def test_list_includes_created_item(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind, subcommand: str
    ) -> None:
        """`list` and its `ls` alias both report the created item under the list event."""
        name = f"listed-test-{kind.suffix}"
        run_dovo([kind.command, "create", "--name", name], cwd=initialized_project)

        result = run_dovo([kind.command, subcommand, "--format", "json"], cwd=initialized_project)

        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope.get("event_type") == f"{kind.command}.list"
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
        assert envelope.get("event_type") == f"{kind.command}.show"
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

    def test_create_with_user_and_global_exits_two_with_tier_conflict(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        name = f"tier-conflict-{kind.suffix}"
        result = run_dovo(
            [kind.command, "create", "--name", name, "--user", "--global", "--format", "json"],
            cwd=initialized_project,
        )

        assert result.exit_code == 2
        assert json.loads(result.stdout).get("payload", {}).get("error_code") == "CATALOG_TIER_CONFLICT"
        for tier_catalog in (
            initialized_project / ".dovo" / "catalog",
            DOVO_HOME / "user" / "catalog",
            DOVO_HOME / "global" / "catalog",
        ):
            assert not (tier_catalog / kind.catalog_dir / f"{name}.yml").exists()

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
        assert envelope.get("event_type") == f"{kind.command}.delete"
        assert envelope.get("payload", {}).get("deleted") is True
        assert not item_path.exists()

    def test_delete_json_without_force_asks_on_stderr_and_keeps_stdout_json(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        name = f"ask-delete-{kind.suffix}"
        run_dovo([kind.command, "create", "--name", name], cwd=initialized_project)
        item_path = initialized_project / ".dovo" / "catalog" / kind.catalog_dir / f"{name}.yml"

        result = run_dovo([kind.command, "delete", name, "--format", "json"], cwd=initialized_project, input_text="")

        assert result.exit_code == 0
        assert f"Are you sure you want to delete {kind.command} '{name}'? [y/N]: " in result.stderr
        envelope = parse_json(result.stdout)
        assert envelope.get("event_type") == f"{kind.command}.delete"
        payload = envelope.get("payload")
        assert isinstance(payload, dict)
        assert (payload.get("deleted"), payload.get("cancelled")) == (False, True)
        assert item_path.is_file()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
    def test_unreadable_item_is_a_list_warning_with_a_record_and_unreadable_by_name(
        self, run_dovo: DovoRunner, initialized_project: Path, kind: CatalogKind
    ) -> None:
        """An item whose file cannot be read is a list warning with one diagnostics record, and show and validate by name fail with CATALOG_FILE_UNREADABLE."""
        name = f"locked-{kind.suffix}"
        run_dovo([kind.command, "create", "--name", name], cwd=initialized_project)
        locked = initialized_project / ".dovo" / "catalog" / kind.catalog_dir / f"{name}.yml"
        locked.chmod(0o000)
        try:
            listed = run_dovo([kind.command, "list", "--format", "json"], cwd=initialized_project)
            shown = run_dovo([kind.command, "show", name, "--format", "json"], cwd=initialized_project)
            validated = run_dovo([kind.command, "validate", name, "--format", "json"], cwd=initialized_project)
        finally:
            locked.chmod(0o644)

        assert listed.exit_code == 0
        list_payload = json.loads(listed.stdout)["payload"]
        assert list_payload["warnings"] == [
            f"Failed to read catalog {kind.command} '{locked}': [Errno 13] Permission denied: '{locked}'"
        ]
        assert len(list_payload["diagnostics"]) == 1
        for result in (shown, validated):
            assert result.exit_code == 1
            payload = json.loads(result.stdout)["payload"]
            assert payload["error_code"] == "CATALOG_FILE_UNREADABLE"
            assert len(payload["diagnostics"]) == 1
