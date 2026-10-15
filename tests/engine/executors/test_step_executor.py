"""Contract tests for StepExecution shorthand resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.models import StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from tests.harness.builders import StepBuilder


class StepExecutionResolutionTests:
    def test_unresolvable_uses_step_returns_failed_result_with_could_not_resolve_message(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", uses="base-step")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "failed"
        assert result.error_message == "Could not resolve step 's1'."

    def test_run_shorthand_step_executes_as_command(self, tmp_path: Path) -> None:
        step = StepDefinition(id="s1", run="echo ok")

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert "ok" in result.stdout


class CommandScriptEnvironmentPreservedTests:
    @pytest.mark.parametrize("step_type", [pytest.param("command", id="command"), pytest.param("script", id="script")])
    def test_command_and_script_children_still_inherit_ambient_and_keep_precedence(
        self, step_type: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UNRELATED", "1")
        monkeypatch.setenv("DOVO_STEP_ID", "ambient-id")
        monkeypatch.setenv("DOVO_STEP_NAME", "ambient-name")
        if step_type == "command":
            builder = StepBuilder.command('echo "$UNRELATED|$DOVO_STEP_ID|$DOVO_STEP_NAME"')
        else:
            (tmp_path / "probe.py").write_text(
                "import os\nprint('|'.join(os.environ[k] for k in ('UNRELATED', 'DOVO_STEP_ID', 'DOVO_STEP_NAME')))\n",
                encoding="utf-8",
            )
            builder = StepBuilder.script("probe.py")
        step = builder.with_id("probe").with_env("DOVO_STEP_NAME", "from-step").build()

        result = StepExecution(StepExecutionContext(step=step, worktree_path=tmp_path)).run()

        assert result.status == "completed"
        assert result.stdout.strip() == "1|probe|from-step"
