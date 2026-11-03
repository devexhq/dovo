"""Regression tests: the suite gives the same result inside a `dovo run` step as in a plain shell."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Tests that failed when an agent step's TMPDIR and DOVO_* step variables leaked into the suite.
AFFECTED_TESTS = (
    "tests/cli/run/test_run.py::RunCliAutoInitTests",
    "tests/cli/init/test_init.py::InitCliFailureTests",
    "tests/cli/test_cli.py::IdentityGuardCliTests",
    "tests/engine/executors/test_step_executor_outputs.py::StepExecutionTempEnvVarTests",
)


def _agent_step_env(home: Path) -> dict[str, str]:
    """Return the environment an agent step hands a child: HOME with ~/.dovo, its TMPDIR under it and step vars."""
    session_tmp = home / ".dovo" / "storage" / "projects" / "dovo" / "tmp" / "blueprint_1"
    scratch = session_tmp / "steps" / "implement" / "agent" / "a1" / "scratch"
    scratch.mkdir(parents=True)
    env = {name: value for name, value in os.environ.items() if not name.startswith(("DOVO_", "PYTEST_"))}
    env.update(
        HOME=str(home),
        TMPDIR=str(scratch),
        TMP=str(scratch),
        TEMP=str(scratch),
        DOVO_AGENT_SCRATCH=str(scratch),
        DOVO_TEMP=str(session_tmp),
        DOVO_RUNNER_TEMP=str(session_tmp),
        DOVO_STEP_TEMP=str(session_tmp / "steps" / "implement"),
        DOVO_OUTPUT=str(session_tmp / "step_implement.output"),
        DOVO_STEP_ID="implement",
        DOVO_STEP_NAME="Implement",
        DOVO_STEP_INDEX="5",
        DOVO_STEP_ATTEMPT="1",
        DOVO_ITERATION_INDEX="1",
        DOVO_BLUEPRINT_NAME="ship",
        DOVO_BLUEPRINT_SHA="ship",
        DOVO_PREVIOUS_STEP_ID="next",
        DOVO_PREVIOUS_STEP_NAME="Next",
        DOVO_PREVIOUS_STEP_INDEX="4",
        DOVO_PREVIOUS_STEP_STATUS="completed",
        DOVO_PREVIOUS_STEP_EXIT_CODE="0",
        DOVO_STEPS_JSON="[]",
        DOVO_SESSION_ID="blueprint_1",
    )
    return env


class StepEnvironmentIsolationTests:
    """The harness shields the suite from the TMPDIR and DOVO_* variables a `dovo run` step passes on."""

    def test_affected_tests_pass_with_agent_step_tmpdir_and_step_variables(self, tmp_path: Path) -> None:
        home = tmp_path / "home"

        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *AFFECTED_TESTS, "-p", "no:cacheprovider", "-q"],
            cwd=REPO_ROOT,
            env=_agent_step_env(home),
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-4000:]
