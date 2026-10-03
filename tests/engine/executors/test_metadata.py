"""Contract tests for execution metadata building and WT_* environment formatting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree.core.catalog.definitions import StepDefinition, StepType
from worktree.engine.executors.metadata import (
    build_execution_metadata,
    metadata_to_env,
    resolve_step_log_paths,
    resolve_step_temp_paths,
)
from worktree.engine.executors.models import PreviousStepMetadata


class MetadataBuilderTempPathsTests:
    """[tier-1/unit] resolve_step_temp_paths / build_execution_metadata: scratch-path computation and tmp-metadata population."""

    def test_resolve_step_temp_paths_returns_steps_subdir_and_output_file_rooted_at_session_tmp_dir(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/unit] resolve_step_temp_paths: given session_tmp_dir and step_id 's1', returns (session_tmp_dir/'steps'/'s1', session_tmp_dir/'step_s1.output')."""
        step_dir, output_file = resolve_step_temp_paths(tmp_path, "s1")

        assert step_dir == tmp_path / "steps" / "s1"
        assert output_file == tmp_path / "step_s1.output"

    @pytest.mark.parametrize(
        ("session_tmp_dir_given", "expect_populated"),
        [
            pytest.param(True, True, id="session_tmp_dir_given_populates_tmp_metadata"),
            pytest.param(False, False, id="session_tmp_dir_none_leaves_tmp_metadata_empty"),
        ],
    )
    def test_build_execution_metadata_tmp_field_reflects_session_tmp_dir_presence(
        self, tmp_path: Path, session_tmp_dir_given: bool, expect_populated: bool
    ) -> None:
        """[tier-1/unit] build_execution_metadata: metadata.tmp.session_dir/.step_dir/.output_file are non-empty iff session_tmp_dir was passed; empty TempMetadata() strings otherwise."""
        step = StepDefinition(id="s1", type=StepType.COMMAND, command="echo hi")
        session_tmp_dir = tmp_path if session_tmp_dir_given else None

        metadata = build_execution_metadata(step, session_tmp_dir=session_tmp_dir)

        assert bool(metadata.tmp.session_dir) is expect_populated
        assert bool(metadata.tmp.step_dir) is expect_populated
        assert bool(metadata.tmp.output_file) is expect_populated


class ResolveStepLogPathsTests:
    """[tier-1/domain] resolve_step_log_paths: per-attempt stdout/stderr log filename computation."""

    def test_resolve_step_log_paths_returns_stdout_and_stderr_paths_with_zero_padded_index(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/domain] resolve_step_log_paths: without iteration, names are <NN>_<step_id>_attempt_<n>.{stdout,stderr}.log."""
        paths = resolve_step_log_paths(tmp_path, step_index=1, step_id="setup", attempt=1)

        assert paths == (tmp_path / "01_setup_attempt_1.stdout.log", tmp_path / "01_setup_attempt_1.stderr.log")

    def test_resolve_step_log_paths_inserts_iter_segment_when_iteration_is_set(self, tmp_path: Path) -> None:
        """[tier-1/domain] resolve_step_log_paths: iteration=2 inserts an _iter_2 segment before _attempt_."""
        paths = resolve_step_log_paths(tmp_path, step_index=1, step_id="retry_check", attempt=1, iteration=2)

        assert paths == (
            tmp_path / "01_retry_check_iter_2_attempt_1.stdout.log",
            tmp_path / "01_retry_check_iter_2_attempt_1.stderr.log",
        )


class MetadataToEnvExcludesOutputsFromStepsJsonTests:
    """[tier-1/unit] metadata_to_env: WT_STEPS_JSON never serializes PreviousStepMetadata.outputs, regardless of its contents."""

    def test_wt_steps_json_omits_outputs_key_even_when_previous_step_outputs_is_non_empty(self) -> None:
        """[tier-1/unit] metadata_to_env: metadata.steps containing an entry with outputs={'greeting': 'hello'} yields a WT_STEPS_JSON whose parsed entry dict has no 'outputs' key at all (regression guard for PreviousStepMetadata.outputs' Field(exclude=True))."""
        step = StepDefinition(id="s2", type=StepType.COMMAND, command="echo hi")
        historical = PreviousStepMetadata(
            id="step_a",
            name="Step Alpha",
            index="1",
            status="completed",
            exit_code="0",
            outputs={"greeting": "hello"},
        )

        metadata = build_execution_metadata(step, steps=[historical])
        env = metadata_to_env(metadata)

        parsed = json.loads(env["WT_STEPS_JSON"])
        assert "outputs" not in parsed[0]


class MetadataSessionIdEnvTests:
    """[tier-1/unit] metadata_to_env: WT_SESSION_ID reflects ExecutionMetadata.session_id."""

    def test_metadata_to_env_includes_wt_session_id(self) -> None:
        """[tier-1/unit] metadata_to_env: ExecutionMetadata(session_id='wf_abc123') maps to env['WT_SESSION_ID'] == 'wf_abc123'."""
        step = StepDefinition(id="s1", type=StepType.COMMAND, command="echo hi")

        metadata = build_execution_metadata(step, session_id="wf_abc123")
        env = metadata_to_env(metadata)

        assert env["WT_SESSION_ID"] == "wf_abc123"
