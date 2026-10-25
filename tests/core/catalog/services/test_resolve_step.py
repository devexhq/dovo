from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.common.error_codes import ErrorCode
from dovo.common.filesystem import WorkspacePaths
from dovo.common.tool_policy import ToolCapability, ToolPolicy, ToolRule
from dovo.core.catalog.definitions import ArtifactPublishSpec, StepDefinition, StepType
from dovo.core.catalog.exceptions import CatalogError, StepValidationError
from dovo.core.catalog.services.resolve_step import (
    load_step,
    load_step_by_name,
    load_step_by_path,
    merge_uses_step,
    resolve_step_definition,
)
from dovo.core.project.services.identity import generate_project_identity, save_project_identity


def _persist_project_identity(root: Path) -> None:
    """Persist a project identity so catalog-backed step resolution can resolve project_id."""
    save_project_identity(root / ".dovo" / "project.json", generate_project_identity())


class StepResolutionTests:
    """Unit tests verifying step shorthand expansion, inheritance, and field overlay behavior."""

    def test_step_loader_expands_run_shorthand_preserving_environment(self) -> None:
        raw = {"id": "test", "run": "npm test", "env": {"CI": "1"}}
        step = resolve_step_definition(raw)
        assert step.id == "test"
        assert step.type == StepType.COMMAND
        assert step.command == "npm test"
        assert step.env == {"CI": "1"}

    def test_step_loader_expands_run_shorthand_preserving_artifacts(self) -> None:
        raw = {"id": "test", "run": "make plan", "artifacts": [{"name": "plan", "path": "PLAN.md"}]}

        step = resolve_step_definition(raw)

        assert [(spec.name, spec.path) for spec in step.artifacts] == [("plan", "PLAN.md")]

    def test_resolve_step_uses_inherits_and_overlays_explicit_fields_only(
        self, tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
    ) -> None:
        steps_dir = tmp_path / ".dovo" / "catalog" / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        _persist_project_identity(tmp_path)
        base_yaml = (
            "id: base-step\n"
            "name: Base Step Name\n"
            "description: Base step description\n"
            "type: command\n"
            "command: echo base\n"
            "env:\n"
            "  BASE_VAR: base\n"
            "  SHARED_VAR: base_val\n"
            "timeout_seconds: 60\n"
            "on_failure: abort\n"
        )
        (steps_dir / "base-step.yaml").write_text(base_yaml, encoding="utf-8")

        overriding = {
            "id": "derived-step",
            "uses": "base-step",
            "name": "Derived Step Name",
            "env": {"OVERRIDE_VAR": "derived", "SHARED_VAR": "overridden"},
            "timeout_seconds": 300,
        }

        resolved = resolve_step_definition(overriding, paths=workspace_paths_factory(tmp_path, None))

        assert resolved.id == "derived-step"
        assert resolved.name == "Derived Step Name"
        assert resolved.description == "Base step description"
        assert resolved.type == StepType.COMMAND
        assert resolved.command == "echo base"
        assert resolved.env == {"BASE_VAR": "base", "SHARED_VAR": "overridden", "OVERRIDE_VAR": "derived"}
        assert resolved.timeout_seconds == 300

    def test_resolve_concrete_type_returns_same_definition(self) -> None:
        inst = StepDefinition(id="c", type=StepType.COMMAND, command="ls")
        assert resolve_step_definition(inst) == inst

    def test_resolve_step_recursive_uses(
        self, tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
    ) -> None:
        steps_dir = tmp_path / ".dovo" / "catalog" / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        _persist_project_identity(tmp_path)
        (steps_dir / "root.yaml").write_text("id: root\nrun: echo root\n", encoding="utf-8")
        (steps_dir / "mid.yaml").write_text("id: mid\nuses: root\nname: Mid Name\n", encoding="utf-8")

        leaf = {"id": "leaf", "uses": "mid", "name": "Leaf Name"}
        resolved = resolve_step_definition(leaf, paths=workspace_paths_factory(tmp_path, None))
        assert resolved.id == "leaf"
        assert resolved.name == "Leaf Name"
        assert resolved.type == StepType.COMMAND
        assert resolved.command == "echo root"

    def test_resolve_step_without_path_raises_validation_error(self) -> None:
        with pytest.raises(StepValidationError, match="without workspace paths"):
            resolve_step_definition({"id": "d", "uses": "base"})

    def test_resolve_step_missing_shorthand_or_type_raises_validation_error(self) -> None:
        with pytest.raises(StepValidationError, match="must specify one of 'run', 'uses', or 'type'"):
            resolve_step_definition(StepDefinition.model_construct(id="empty"))

    def test_resolve_step_malformed_dict_raises_validation_error(self) -> None:
        with pytest.raises(StepValidationError, match="Step validation failed"):
            resolve_step_definition({"id": "bad", "timeout_seconds": "invalid-int"})


class StepLoadingTests:
    """Unit tests verifying step loading from dicts, definitions, paths, and the catalog."""

    def test_load_step_by_name_resolves_catalog_step(
        self, tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
    ) -> None:
        steps_dir = tmp_path / ".dovo" / "catalog" / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        _persist_project_identity(tmp_path)
        (steps_dir / "catalog-step.yaml").write_text("id: catalog-step\nrun: echo hello\n", encoding="utf-8")

        loaded = load_step_by_name("catalog-step", paths=workspace_paths_factory(tmp_path, None))
        assert loaded is not None
        assert loaded.id == "catalog-step"
        assert loaded.run == "echo hello"

    def test_load_step_from_definition_returns_it(self) -> None:
        inst = StepDefinition(id="inst", type=StepType.COMMAND, command="echo 1")
        assert load_step(inst) is inst

    def test_load_step_invalid_dictionary_returns_none(self) -> None:
        assert load_step({"unexpected": "key", "id": 123}) is None

    def test_load_step_by_path_returns_definition(self, tmp_path: Path) -> None:
        step_file = tmp_path / "custom.yaml"
        step_file.write_text("id: custom\ntype: command\ncommand: echo hi\n", encoding="utf-8")

        loaded = load_step_by_path(step_file)
        assert loaded.id == "custom"

    @pytest.mark.parametrize(
        ("content", "code", "message_start"),
        [
            pytest.param(None, ErrorCode.CATALOG_NOT_FOUND, "Catalog file not found at '", id="missing"),
            pytest.param("- a\n", ErrorCode.CATALOG_SYNTAX_ERROR, "Failed to load catalog blueprint '", id="syntax"),
            pytest.param("id: 3\n", ErrorCode.CATALOG_SCHEMA_INVALID, "Model validation failed for '", id="schema"),
        ],
    )
    def test_load_step_by_path_raises_classified_catalog_error(
        self, tmp_path: Path, content: str | None, code: ErrorCode, message_start: str
    ) -> None:
        """Raise CatalogError with the reader's code for a missing or non-object file and CATALOG_SCHEMA_INVALID for an invalid step."""
        step_file = tmp_path / "custom.yaml"
        if content is not None:
            step_file.write_text(content, encoding="utf-8")

        with pytest.raises(CatalogError) as raised:
            load_step_by_path(step_file)

        assert raised.value.error_code == code
        assert raised.value.message.startswith(message_start)


class MergeUsesStepTests:
    """Contract tests for merge_uses_step's field-overlay semantics."""

    def test_merge_uses_step_overlays_only_explicitly_set_fields(self) -> None:
        base = StepDefinition(
            id="base-step",
            type=StepType.COMMAND,
            command="echo base",
            env={"BASE_VAR": "base", "SHARED_VAR": "base_val"},
            timeout_seconds=60,
        )
        using = StepDefinition(
            id="derived-step",
            uses="base-step",
            name="Derived Step Name",
            env={"OVERRIDE_VAR": "derived", "SHARED_VAR": "overridden"},
        )

        merged = merge_uses_step(using, base)

        assert merged is not None
        assert merged.id == "derived-step"
        assert merged.name == "Derived Step Name"
        assert merged.type == StepType.COMMAND
        assert merged.command == "echo base"
        assert merged.timeout_seconds == 60
        assert merged.env == {"BASE_VAR": "base", "SHARED_VAR": "overridden", "OVERRIDE_VAR": "derived"}

    @pytest.mark.parametrize(
        ("using_artifacts", "expected_path"),
        [
            pytest.param([ArtifactPublishSpec(name="plan", path="PLAN.md")], "PLAN.md", id="using_step_artifacts_win"),
            pytest.param(None, "BASE.md", id="omitted_inherits_base_artifacts"),
        ],
    )
    def test_artifacts_on_using_step_survive_the_merge(
        self, using_artifacts: list[ArtifactPublishSpec] | None, expected_path: str
    ) -> None:
        base = StepDefinition(
            id="base-step",
            type=StepType.COMMAND,
            command="echo base",
            artifacts=[ArtifactPublishSpec(name="base", path="BASE.md")],
        )
        using = (
            StepDefinition(id="derived", uses="base-step", artifacts=using_artifacts)
            if using_artifacts is not None
            else StepDefinition(id="derived", uses="base-step")
        )

        merged = merge_uses_step(using, base)

        assert merged is not None
        assert [spec.path for spec in merged.artifacts] == [expected_path]


_CATALOG_POLICY = ToolPolicy(allow=[ToolRule(capability=ToolCapability.READ, pattern="src/**")])
_STEP_POLICY = ToolPolicy(allow=[ToolRule(capability=ToolCapability.SHELL, pattern="git status")])
_TOOLS_BY_KEY: dict[str | None, ToolPolicy | None] = {
    None: None,
    "catalog": _CATALOG_POLICY,
    "step": _STEP_POLICY,
    "empty": ToolPolicy(),
}


class UsesToolsMergeTests:
    @pytest.mark.parametrize(
        ("using_tools", "base_tools", "expected"),
        [
            pytest.param(None, "catalog", "catalog", id="omitted-inherits-catalog"),
            pytest.param("step", "catalog", "step", id="explicit-replaces-catalog"),
            pytest.param("empty", "catalog", "empty", id="explicit-empty-replaces-catalog"),
            pytest.param(None, None, None, id="neither-stays-omitted"),
        ],
    )
    def test_merge_uses_step_preserves_omission_and_replacement(
        self, using_tools: str | None, base_tools: str | None, expected: str | None
    ) -> None:
        base = StepDefinition(id="base-step", type=StepType.COMMAND, command="echo base")
        if base_tools is not None:
            base = StepDefinition(id="base-step", type=StepType.AGENT, prompt="go", tools=_TOOLS_BY_KEY[base_tools])
        using_fields: dict[str, object] = {"id": "derived-step", "uses": "base-step"}
        if using_tools is not None:
            using_fields["tools"] = _TOOLS_BY_KEY[using_tools]
        using = StepDefinition.model_validate(using_fields)

        merged = merge_uses_step(using, base)

        assert merged is not None
        assert merged.tools == _TOOLS_BY_KEY[expected]
