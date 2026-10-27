"""CLI integration tests for dovo blueprint delete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dovo.cli import app
from dovo.common.filesystem.services.global_root import resolve_global_paths
from dovo.common.lock import LockTimeoutError, WorkspaceLock
from dovo.core.catalog import Catalog
from dovo.core.catalog.models import CatalogItemType
from tests.harness.lock import raise_lock_timeout
from tests.harness.workspace_paths import initialized_workspace_paths

pytestmark = pytest.mark.usefixtures("workspace_config")

_PROMPT = "Are you sure you want to delete blueprint '{name}'?"
_CANCELLED_PAYLOAD = {
    "errors": [],
    "warnings": [],
    "remediations": [],
    "diagnostics": [],
    "error_code": None,
    "item": None,
    "deleted": False,
    "cancelled": True,
}
_NOT_FOUND_PAYLOAD = {
    "errors": ["Catalog blueprint 'ghost' not found."],
    "warnings": [],
    "remediations": [],
    "diagnostics": [],
    "error_code": "CATALOG_NOT_FOUND",
    "item": None,
    "deleted": False,
    "cancelled": False,
}


def _payload(stdout: str) -> dict[str, object]:
    """Return the JSON event payload that follows any prompt text in stdout."""
    return json.loads(stdout[stdout.index("{") :])["payload"]


class BlueprintDeleteCliIntegrationTests:
    """Typer runner integration tests for dovo blueprint delete."""

    def test_blueprint_delete_cli_with_force_exits_zero(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "del-blueprint")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "del-blueprint", "--force"]
        )

        assert result.exit_code == 0
        assert not (isolated_workspace / ".dovo" / "catalog" / "blueprints" / "del-blueprint.yml").exists()

    def test_blueprint_delete_cli_confirmation_declined_exits_zero(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "declined-blueprint")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "declined-blueprint"], input="n\n"
        )

        assert result.exit_code == 0
        assert "Deletion cancelled" in result.stdout

    def test_blueprint_delete_cli_renders_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "json-del-blueprint")

        result = cli_runner.invoke(
            app,
            [
                "-p",
                str(isolated_workspace),
                "blueprint",
                "delete",
                "json-del-blueprint",
                "--force",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["event_type"] == "CatalogDeleteResult"
        assert payload["payload"]["deleted"] is True

    def test_blueprint_delete_cli_missing_exits_one(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "missing-blueprint", "--force"]
        )

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_blueprint_delete_cli_lock_timeout_propagates(
        self, cli_runner: CliRunner, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 1 with result.exception a LockTimeoutError for `delete demo --force` and no 'Catalog Delete Failed' in stdout."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "demo")
        monkeypatch.setattr(WorkspaceLock, "__enter__", raise_lock_timeout)

        result = cli_runner.invoke(app, ["-p", str(isolated_workspace), "blueprint", "delete", "demo", "--force"])

        assert result.exit_code == 1
        assert isinstance(result.exception, LockTimeoutError)
        assert "Catalog Delete Failed" not in result.stdout


class BlueprintDeleteCliTests:
    """Typer runner tests for dovo blueprint delete's declined confirmation."""

    def test_declined_confirmation_returns_cancelled_json(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 0 with payload == the declined-delete JSON when stdin answers 'n', and keep the file on disk."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "kept-blueprint")

        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "blueprint", "delete", "kept-blueprint", "--format", "json"],
            input="n\n",
        )

        assert result.exit_code == 0
        assert _PROMPT.format(name="kept-blueprint") in result.stdout
        assert _payload(result.stdout) == _CANCELLED_PAYLOAD
        assert (isolated_workspace / ".dovo" / "catalog" / "blueprints" / "kept-blueprint.yml").is_file()

    def test_unanswered_prompt_returns_cancelled_json(self, cli_runner: CliRunner, isolated_workspace: Path) -> None:
        """Exit 0 with payload == the declined-delete JSON when stdin is closed without an answer, and keep the file."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "kept-blueprint")

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "kept-blueprint", "--format", "json"]
        )

        assert result.exit_code == 0
        assert _payload(result.stdout) == _CANCELLED_PAYLOAD
        assert (isolated_workspace / ".dovo" / "catalog" / "blueprints" / "kept-blueprint.yml").is_file()

    @pytest.mark.parametrize(
        ("extra_args", "invoke_input", "prompted"),
        [
            pytest.param([], "y\n", True, id="accepted"),
            pytest.param(["--force"], None, False, id="force"),
        ],
    )
    def test_accepted_or_forced_delete_returns_deleted_json(
        self,
        cli_runner: CliRunner,
        isolated_workspace: Path,
        extra_args: list[str],
        invoke_input: str | None,
        prompted: bool,
    ) -> None:
        """Exit 0 with deleted True, cancelled False, error_code None and item.key 'gone-blueprint', prompting only without --force, and remove the file."""
        Catalog(initialized_workspace_paths(isolated_workspace)).create(CatalogItemType.BLUEPRINT, "gone-blueprint")

        result = cli_runner.invoke(
            app,
            ["-p", str(isolated_workspace), "blueprint", "delete", "gone-blueprint", *extra_args, "--format", "json"],
            input=invoke_input,
        )

        assert result.exit_code == 0
        assert (_PROMPT.format(name="gone-blueprint") in result.stdout) is prompted
        payload = _payload(result.stdout)
        item = payload["item"]
        assert isinstance(item, dict)
        assert item["key"] == "gone-blueprint"
        assert {key: value for key, value in payload.items() if key != "item"} == {
            "errors": [],
            "warnings": [],
            "remediations": [],
            "diagnostics": [],
            "error_code": None,
            "deleted": True,
            "cancelled": False,
        }
        assert not (isolated_workspace / ".dovo" / "catalog" / "blueprints" / "gone-blueprint.yml").exists()

    def test_unknown_name_accepted_prompts_then_fails_json(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 1 with CATALOG_NOT_FOUND after prompting for an unknown name when stdin answers 'y'."""
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "ghost", "--format", "json"], input="y\n"
        )

        assert result.exit_code == 1
        assert _PROMPT.format(name="ghost") in result.stdout
        assert _payload(result.stdout) == _NOT_FOUND_PAYLOAD

    def test_unknown_name_declined_returns_cancelled_json(
        self, cli_runner: CliRunner, isolated_workspace: Path
    ) -> None:
        """Exit 0 with the declined-delete JSON after prompting for an unknown name when stdin answers 'n'."""
        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", "ghost", "--format", "json"], input="n\n"
        )

        assert result.exit_code == 0
        assert _PROMPT.format(name="ghost") in result.stdout
        assert _payload(result.stdout) == _CANCELLED_PAYLOAD

    @pytest.mark.parametrize(
        ("name", "error_code", "error"),
        [
            pytest.param(
                "dovo/fix-tests",
                "CATALOG_PROTECTED",
                "Cannot delete bundled catalog template 'dovo/fix-tests'.",
                id="protected",
            ),
            pytest.param(
                "user-only",
                "CATALOG_TIER_DELETE_FORBIDDEN",
                "Catalog item 'user-only' resolved from tier 'user'; only repo-tier items can be deleted.",
                id="wrong_tier",
            ),
        ],
    )
    def test_refused_target_accepted_prompts_then_fails_json(
        self, cli_runner: CliRunner, isolated_workspace: Path, name: str, error_code: str, error: str
    ) -> None:
        """Exit 1 with error_code and errors == [error] after prompting for a protected or user-tier blueprint when stdin answers 'y'."""
        user_blueprints = resolve_global_paths(None).user_catalog_dir / "blueprints"
        user_blueprints.mkdir(parents=True, exist_ok=True)
        (user_blueprints / "user-only.yml").write_text(
            'version: "1.0"\nname: user-only\ndescription: User tier blueprint\nsteps: []\n', encoding="utf-8"
        )

        result = cli_runner.invoke(
            app, ["-p", str(isolated_workspace), "blueprint", "delete", name, "--format", "json"], input="y\n"
        )

        assert result.exit_code == 1
        assert _PROMPT.format(name=name) in result.stdout
        payload = _payload(result.stdout)
        assert payload["error_code"] == error_code
        assert payload["errors"] == [error]
        assert payload["deleted"] is False
        assert payload["cancelled"] is False
        assert (user_blueprints / "user-only.yml").is_file()
