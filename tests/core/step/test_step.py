from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from worktree.common.filesystem import WorkspacePaths
from worktree.core.project.services.identity import generate_project_identity, save_project_identity
from worktree.core.step import (
    Step,
    StepDefinition,
    StepType,
    StepValidationError,
    merge_uses_step,
    resolve_step_definition,
)


def _persist_project_identity(root: Path) -> None:
    """Persist a project identity so catalog-backed step resolution can resolve project_id."""
    save_project_identity(root / ".worktree" / "project.json", generate_project_identity())


class StepShorthandExpansionTests:
    """Unit tests verifying shorthand step expansion contracts."""

    def test_step_loader_expands_run_shorthand_preserving_environment(self) -> None:
        """Shorthand 'run' step expands to command type preserving explicit environment variables."""
        raw = {"id": "test", "run": "npm test", "env": {"CI": "1"}}
        step = resolve_step_definition(raw)
        assert step.id == "test"
        assert step.type == StepType.COMMAND
        assert step.command == "npm test"
        assert step.env == {"CI": "1"}

    def test_step_facade_resolves_run_shorthand(self) -> None:
        """Step.load and Step.resolve expand shorthand run directly on facade."""
        raw = {"id": "test", "run": "pytest"}
        step_obj = Step.load(raw)
        assert step_obj is not None
        resolved = step_obj.resolve()
        assert resolved is not None
        assert resolved.type == StepType.COMMAND
        assert resolved.command == "pytest"


class StepResolutionTests:
    """Unit tests verifying step inheritance and field overlay behavior."""

    def test_resolve_step_uses_inherits_and_overlays_explicit_fields_only(
        self, tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
    ) -> None:
        """Inherited step overlays explicitly set fields while preserving base definition defaults."""
        steps_dir = tmp_path / ".worktree" / "catalog" / "steps"
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

    def test_step_load_by_name_via_catalog(
        self, tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
    ) -> None:
        """Step.load_by_name resolves indexed step from workspace catalog."""
        steps_dir = tmp_path / ".worktree" / "catalog" / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        _persist_project_identity(tmp_path)
        (steps_dir / "catalog-step.yaml").write_text("id: catalog-step\nrun: echo hello\n", encoding="utf-8")

        loaded = Step.load_by_name("catalog-step", paths=workspace_paths_factory(tmp_path, None))
        assert loaded is not None
        assert loaded.instance.id == "catalog-step"
        assert loaded.instance.run == "echo hello"

    def test_step_load_from_instance(self) -> None:
        """Step.load wraps an existing StepDefinition instance."""
        inst = StepDefinition(id="inst", type=StepType.COMMAND, command="echo 1")
        loaded = Step.load(inst)
        assert loaded is not None
        assert loaded.instance.id == "inst"

    def test_step_load_invalid_dictionary_returns_none(self) -> None:
        """Step.load returns None on schema-invalid dictionary payloads."""
        assert Step.load({"unexpected": "key", "id": 123}) is None

    def test_step_init_without_args_raises_value_error(self) -> None:
        """Step initialization requires either an instance or definition."""
        with pytest.raises(ValueError, match="requires an instance or definition"):
            Step()

    def test_step_load_by_path(self, tmp_path: Path) -> None:
        """Step.load_by_path loads valid YAML file and returns None for missing path."""
        step_file = tmp_path / "custom.yaml"
        step_file.write_text("id: custom\ntype: command\ncommand: echo hi\n", encoding="utf-8")

        loaded = Step.load_by_path(step_file)
        assert loaded is not None
        assert loaded.instance.id == "custom"

        missing = Step.load_by_path(tmp_path / "nonexistent.yaml")
        assert missing is None

    def test_step_resolve_concrete_type_returns_self_instance(self) -> None:
        """Step with explicit type resolves to its own instance unchanged."""
        inst = StepDefinition(id="c", type=StepType.COMMAND, command="ls")
        step_obj = Step(instance=inst)
        assert step_obj.resolve() == inst

    def test_resolve_step_recursive_uses(
        self, tmp_path: Path, workspace_paths_factory: Callable[[Path, Path | None], WorkspacePaths]
    ) -> None:
        """Step inheriting from another shorthand step resolves recursively."""
        steps_dir = tmp_path / ".worktree" / "catalog" / "steps"
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
        """Resolving a 'uses' step without path raises StepValidationError."""
        with pytest.raises(StepValidationError, match="without workspace paths"):
            resolve_step_definition({"id": "d", "uses": "base"})

    def test_resolve_step_missing_shorthand_or_type_raises_validation_error(self) -> None:
        """Step without run, uses, or type raises StepValidationError."""
        with pytest.raises(StepValidationError, match="must specify one of 'run', 'uses', or 'type'"):
            resolve_step_definition(StepDefinition.model_construct(id="empty"))

    def test_resolve_step_malformed_dict_raises_validation_error(self) -> None:
        """Malformed dictionary passed to resolve_step_definition raises StepValidationError."""
        with pytest.raises(StepValidationError, match="Step validation failed"):
            resolve_step_definition({"id": "bad", "timeout_seconds": "invalid-int"})


class MergeUsesStepTests:
    """Contract tests for merge_uses_step's field-overlay semantics."""

    def test_merge_uses_step_overlays_only_explicitly_set_fields(self) -> None:
        """merge_uses_step: a using-step with only name/env set inherits base command/type/timeout and overlays name plus merged env."""
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
