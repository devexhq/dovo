"""Tests for test harness fluent builders."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.harness import (
    BlueprintBuilder,
    StepBuilder,
    WorkspaceBuilder,
)
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.common.models import FailurePolicy, OnFailureSpec
from worktree.core.blueprint.models import BlueprintDefaults, BlueprintDefinition
from worktree.core.db.connection import resolve_db_path
from worktree.core.inputs.models import InputType, ParameterInput
from worktree.core.step.models import StepAssert, StepDefinition, StepType


class StepBuilderTests:
    """Verification tests for StepBuilder."""

    def test_default_build_returns_valid_command_step_definition(self) -> None:
        step = StepBuilder().build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
        )
        assert step == expected

    def test_command_factory_sets_command_and_type(self) -> None:
        step = StepBuilder.command("echo hi").build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo hi",
            timeout_seconds=30,
        )
        assert step == expected

    def test_run_factory_initializes_run_mode(self) -> None:
        step = StepBuilder.run("pytest").build()
        expected = StepDefinition(
            id="step-1",
            run="pytest",
            timeout_seconds=30,
        )
        assert step == expected

    def test_agent_factory_initializes_agent_step(self) -> None:
        step = StepBuilder.agent("write tests").build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.AGENT,
            prompt="write tests",
            timeout_seconds=30,
        )
        assert step == expected

    def test_script_factory_initializes_script_step(self) -> None:
        step = StepBuilder.script("scripts/run.sh").build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.SCRIPT,
            script_path="scripts/run.sh",
            timeout_seconds=30,
        )
        assert step == expected

    def test_uses_factory_initializes_step_inheritance(self) -> None:
        step = StepBuilder.uses("wt/ai-code-patcher").build()
        expected = StepDefinition(
            id="step-1",
            uses="wt/ai-code-patcher",
            timeout_seconds=30,
        )
        assert step == expected

    def test_with_uses_switches_to_step_inheritance(self) -> None:
        step = StepBuilder.command("echo test").with_uses("catalog/custom-step").build()
        expected = StepDefinition(
            id="step-1",
            uses="catalog/custom-step",
            timeout_seconds=30,
        )
        assert step == expected

    def test_with_id_updates_step_id(self) -> None:
        step = StepBuilder().with_id("custom-id").build()
        expected = StepDefinition(
            id="custom-id",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
        )
        assert step == expected

    def test_with_env_adds_environment_variables(self) -> None:
        step = StepBuilder().with_env("FOO", "bar").build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            env={"FOO": "bar"},
        )
        assert step == expected

    def test_with_env_vars_merges_multiple_variables(self) -> None:
        step = StepBuilder().with_env("A", "1").with_env_vars({"B": "2", "C": "3"}).build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            env={"A": "1", "B": "2", "C": "3"},
        )
        assert step == expected

    def test_with_timeout_updates_timeout_seconds(self) -> None:
        step = StepBuilder().with_timeout(60).build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=60,
        )
        assert step == expected

    def test_with_name_and_description_sets_metadata(self) -> None:
        step = StepBuilder().with_name("Run Test").with_description("Executes test suite").build()
        expected = StepDefinition(
            id="step-1",
            name="Run Test",
            description="Executes test suite",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
        )
        assert step == expected

    def test_with_retry_configures_on_failure_spec(self) -> None:
        step = StepBuilder().with_retry(max_retries=3, backoff_ms=100, on_max_retries=FailurePolicy.ABORT).build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            on_failure=OnFailureSpec(
                action=FailurePolicy.RETRY,
                max_retries=3,
                backoff_ms=100,
                on_max_retries=FailurePolicy.ABORT,
            ),
        )
        assert step == expected

    @pytest.mark.parametrize(
        ("on_failure", "expected_action"),
        [
            pytest.param(FailurePolicy.CONTINUE, FailurePolicy.CONTINUE, id="enum"),
            pytest.param("continue", FailurePolicy.CONTINUE, id="string"),
            pytest.param(
                OnFailureSpec(action=FailurePolicy.PROMPT_USER),
                FailurePolicy.PROMPT_USER,
                id="spec",
            ),
        ],
    )
    def test_with_on_failure_configures_failure_policy(
        self,
        on_failure: OnFailureSpec | FailurePolicy | str,
        expected_action: FailurePolicy,
    ) -> None:
        step = StepBuilder().with_on_failure(on_failure).build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            on_failure=OnFailureSpec(action=expected_action),
        )
        assert step == expected

    @pytest.mark.parametrize(
        ("apply_assert", "expected_assert"),
        [
            pytest.param(
                lambda builder: builder.assert_exit_code(0),
                StepAssert(exit_code=0),
                id="exit_code",
            ),
            pytest.param(
                lambda builder: builder.assert_output_contains("pass"),
                StepAssert(output_contains="pass"),
                id="output_contains",
            ),
            pytest.param(
                lambda builder: builder.assert_output_not_contains("error"),
                StepAssert(output_not_contains="error"),
                id="output_not_contains",
            ),
            pytest.param(
                lambda builder: builder.assert_file_exists("dist/app.js"),
                StepAssert(file_exists="dist/app.js"),
                id="file_exists",
            ),
        ],
    )
    def test_assert_method_configures_expected_criterion(
        self,
        apply_assert: Callable[[StepBuilder], StepBuilder],
        expected_assert: StepAssert,
    ) -> None:
        step = apply_assert(StepBuilder()).build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            assert_=expected_assert,
        )
        assert step == expected

    @pytest.mark.parametrize(
        ("apply_chain", "expected_assert"),
        [
            pytest.param(
                lambda builder: builder.assert_exit_code(0).assert_exit_code(1),
                StepAssert(exit_code=[0, 1]),
                id="exit_code",
            ),
            pytest.param(
                lambda builder: builder.assert_output_contains("first").assert_output_contains("second"),
                StepAssert(output_contains=["first", "second"]),
                id="output_contains",
            ),
            pytest.param(
                lambda builder: builder.assert_output_not_contains("bad1").assert_output_not_contains("bad2"),
                StepAssert(output_not_contains=["bad1", "bad2"]),
                id="output_not_contains",
            ),
            pytest.param(
                lambda builder: builder.assert_file_exists("dist/app.js").assert_file_exists("dist/style.css"),
                StepAssert(file_exists=["dist/app.js", "dist/style.css"]),
                id="file_exists",
            ),
        ],
    )
    def test_assert_methods_chained_multiple_times_accumulate_list(
        self,
        apply_chain: Callable[[StepBuilder], StepBuilder],
        expected_assert: StepAssert,
    ) -> None:
        step = apply_chain(StepBuilder()).build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            assert_=expected_assert,
        )
        assert step == expected

    def test_consecutive_asserts_merge_criteria(self) -> None:
        step = StepBuilder().assert_exit_code(0).assert_output_contains("ok").assert_file_exists("dist/app.js").build()
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            assert_=StepAssert(
                exit_code=0,
                output_contains="ok",
                file_exists="dist/app.js",
            ),
        )
        assert step == expected

    def test_assert_file_assertions_chained_together_configures_all_criteria(self) -> None:
        step = (
            StepBuilder()
            .assert_file_exists("dist/app.js")
            .assert_file_not_exists("dist/bundle.js")
            .assert_file_not_empty("dist/app.js")
            .build()
        )
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            assert_=StepAssert(
                file_exists="dist/app.js",
                file_not_exists="dist/bundle.js",
                file_not_empty="dist/app.js",
            ),
        )
        assert step == expected

    def test_assert_contains_and_not_contains_and_files_chained_together_configures_all_criteria(self) -> None:
        step = (
            StepBuilder()
            .assert_output_contains("build succeeded")
            .assert_output_not_contains("warning")
            .assert_file_exists("dist/app.js")
            .build()
        )
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            assert_=StepAssert(
                output_contains="build succeeded",
                output_not_contains="warning",
                file_exists="dist/app.js",
            ),
        )
        assert step == expected

    def test_assert_multiple_contains_not_contains_and_files_chained_together_accumulates_all_criteria(self) -> None:
        step = (
            StepBuilder()
            .assert_output_contains("step 1")
            .assert_output_contains("step 2")
            .assert_output_not_contains("error")
            .assert_output_not_contains("failed")
            .assert_file_exists("out1.txt")
            .assert_file_exists("out2.txt")
            .assert_file_not_exists("err.log")
            .assert_file_not_empty("out1.txt")
            .build()
        )
        expected = StepDefinition(
            id="step-1",
            type=StepType.COMMAND,
            command="echo test",
            timeout_seconds=30,
            assert_=StepAssert(
                output_contains=["step 1", "step 2"],
                output_not_contains=["error", "failed"],
                file_exists=["out1.txt", "out2.txt"],
                file_not_exists="err.log",
                file_not_empty="out1.txt",
            ),
        )
        assert step == expected


class BlueprintBuilderTests:
    """Verification tests for BlueprintBuilder."""

    def test_default_build_returns_valid_blueprint_definition(self) -> None:
        blueprint = BlueprintBuilder().build()
        assert blueprint == BlueprintDefinition(name="test-blueprint")

    def test_with_step_builder_instance_builds_and_includes_step(self) -> None:
        blueprint = BlueprintBuilder().with_step(StepBuilder.command("echo hi")).build()
        expected = BlueprintDefinition(
            name="test-blueprint",
            steps=[
                StepDefinition(
                    id="step-1",
                    type=StepType.COMMAND,
                    command="echo hi",
                    timeout_seconds=30,
                )
            ],
        )
        assert blueprint == expected

    def test_with_step_definition_instance_includes_step(self) -> None:
        step_def = StepDefinition(id="custom-step", type=StepType.COMMAND, command="echo custom")
        blueprint = BlueprintBuilder().with_step(step_def).build()
        assert blueprint == BlueprintDefinition(name="test-blueprint", steps=[step_def])

    def test_add_step_alias_appends_step(self) -> None:
        blueprint = BlueprintBuilder().add_step(StepBuilder.command("echo test")).build()
        expected = BlueprintDefinition(
            name="test-blueprint",
            steps=[
                StepDefinition(
                    id="step-1",
                    type=StepType.COMMAND,
                    command="echo test",
                    timeout_seconds=30,
                )
            ],
        )
        assert blueprint == expected

    def test_workflow_and_task_factories_set_name(self) -> None:
        workflow_blueprint = BlueprintBuilder.workflow("wf-1").build()
        task_blueprint = BlueprintBuilder.task("task-1").build()
        assert workflow_blueprint == BlueprintDefinition(name="wf-1")
        assert task_blueprint == BlueprintDefinition(name="task-1")

    def test_with_input_adds_parameter_input(self) -> None:
        blueprint = (
            BlueprintBuilder().with_input("env", input_type=InputType.STRING, default="prod", required=True).build()
        )
        expected = BlueprintDefinition(
            name="test-blueprint",
            inputs={
                "env": ParameterInput(
                    type=InputType.STRING,
                    default="prod",
                    required=True,
                )
            },
        )
        assert blueprint == expected

    def test_with_env_adds_environment_variables(self) -> None:
        blueprint = BlueprintBuilder().with_env("TARGET", "staging").build()
        assert blueprint == BlueprintDefinition(name="test-blueprint", env={"TARGET": "staging"})

    def test_with_env_vars_merges_environment_variables(self) -> None:
        blueprint = BlueprintBuilder().with_env_vars({"A": "1", "B": "2"}).build()
        assert blueprint == BlueprintDefinition(name="test-blueprint", env={"A": "1", "B": "2"})

    def test_with_defaults_sets_blueprint_defaults(self) -> None:
        defaults = BlueprintDefaults(on_failure=OnFailureSpec(action=FailurePolicy.CONTINUE))
        blueprint = BlueprintBuilder().with_defaults(defaults).build()
        assert blueprint == BlueprintDefinition(name="test-blueprint", defaults=defaults)

    def test_with_use_sandbox_and_timeout_sets_execution_options(self) -> None:
        blueprint = BlueprintBuilder().with_use_sandbox(False).with_timeout(300).build()
        assert blueprint == BlueprintDefinition(name="test-blueprint", use_sandbox=False, timeout_seconds=300)

    def test_with_metadata_sets_description_summary_and_version(self) -> None:
        blueprint = BlueprintBuilder().with_description("desc").with_summary("summary").with_version(2).build()
        expected = BlueprintDefinition(
            name="test-blueprint",
            description="desc",
            summary="summary",
            version=2,
        )
        assert blueprint == expected


class WorkspaceBuilderTests:
    """Verification tests for WorkspaceBuilder."""

    def test_default_build_without_root_creates_ephemeral_workspace(self) -> None:
        workspace = WorkspaceBuilder().build()
        try:
            assert workspace.is_dir()
            assert (workspace / ".worktree/config.json").is_file()
            assert resolve_db_path(resolve_global_paths(None)).is_file()
            assert (workspace / ".worktree/catalog").is_dir()
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_build_scaffolds_default_workspace_structure(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "custom").build()
        assert (workspace / ".worktree/config.json").is_file()
        assert resolve_db_path(resolve_global_paths(None)).is_file()
        assert (workspace / ".worktree/catalog").is_dir()

    def test_with_project_name_sets_custom_name_in_config(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "project_ws").with_project_name("custom-project").build()
        config_text = (workspace / ".worktree/config.json").read_text(encoding="utf-8")
        config_payload = json.loads(config_text)
        assert config_payload["project"]["name"] == "custom-project"

    def test_without_database_skips_sqlite_initialization(self, tmp_path: Path) -> None:
        WorkspaceBuilder(tmp_path / "no_db").without_database().build()
        assert not resolve_db_path(resolve_global_paths(None)).is_file()

    def test_without_catalog_templates_skips_seeding(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "no_catalog").without_catalog_templates().build()
        yaml_files = list((workspace / ".worktree/catalog").rglob("*.yml"))
        assert len(yaml_files) == 0
        assert not (workspace / ".worktree/catalog").exists()

    def test_without_config_skips_config_generation(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "no_cfg").without_config().build()
        assert not (workspace / ".worktree/config.json").exists()

    def test_with_git_initializes_valid_git_repository(self, tmp_path: Path) -> None:
        workspace = WorkspaceBuilder(tmp_path / "git_ws").with_git().build()
        assert (workspace / ".git").is_dir()
        assert (workspace / "README.md").is_file()

    def test_with_custom_config_data_writes_specified_payload(self, tmp_path: Path) -> None:
        custom_payload = {"version": 1, "custom": "val"}
        workspace = WorkspaceBuilder(tmp_path / "custom_cfg").with_config(data=custom_payload).build()
        config_text = (workspace / ".worktree/config.json").read_text(encoding="utf-8")
        config_payload = json.loads(config_text)
        assert config_payload["custom"] == "val"

    def test_with_config_overwrite_false_preserves_existing_config(self, tmp_path: Path) -> None:
        workspace_dir = tmp_path / "custom_preserve"
        initial_config = workspace_dir / ".worktree/config.json"
        initial_config.parent.mkdir(parents=True, exist_ok=True)
        initial_config.write_text('{"preserved": true}', encoding="utf-8")
        workspace = WorkspaceBuilder(workspace_dir).with_config(data={"overwritten": True}, overwrite=False).build()
        assert json.loads((workspace / ".worktree/config.json").read_text(encoding="utf-8")) == {"preserved": True}

    def test_with_catalog_templates_force_false_skips_existing_files(self, tmp_path: Path) -> None:
        workspace_dir = tmp_path / "custom_catalog_force"
        WorkspaceBuilder(workspace_dir).build()
        seeded_file = next((workspace_dir / ".worktree/catalog").rglob("*.yml"))
        seeded_file.write_text("custom: preserved\n", encoding="utf-8")
        WorkspaceBuilder(workspace_dir).with_catalog_templates(force=False).build()
        assert seeded_file.read_text(encoding="utf-8") == "custom: preserved\n"
