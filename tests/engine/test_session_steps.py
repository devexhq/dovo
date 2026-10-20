"""Contract tests for the read-only session step summaries."""

from __future__ import annotations

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.common.models import FailurePolicy
from dovo.common.redact import SecretRedactor
from dovo.core.db import SessionRecord, SessionsRepository
from dovo.core.sessions import StepRunSummary
from dovo.engine.executors.models import StepResult
from dovo.engine.models import DefinitionRef, DefinitionsManifest, SessionStepsResult
from dovo.engine.session_steps import NO_STEP_DATA_WARNING, summarize_session_steps
from dovo.engine.state_models import (
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionPlanNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)
from tests.harness.sessions import SEEDED_FAILURE, seed_failed_session

_MANIFEST = DefinitionsManifest(
    blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="abc", resolved_at="2026-09-28T00:00:00+00:00")
)
_REDACTOR = SecretRedactor([])


def _attempt(
    number: int, *, exit_code: int, duration_seconds: float, error_message: str | None = None
) -> StepAttemptRecord:
    """Build a finished attempt carrying a StepResult with stdout, stderr and outputs populated."""
    result = StepResult(
        step_id="step",
        status="completed" if exit_code == 0 else "failed",
        exit_code=exit_code,
        stdout="STDOUT-TEXT",
        stderr="STDERR-TEXT",
        duration_seconds=duration_seconds,
        error_message=error_message,
        outputs={"key": "OUTPUT-TEXT"},
    )
    return StepAttemptRecord(number=number, started_at="t0", completed_at="t1", result=result)


def _record(nodes: list[ExecutionPlanNode]) -> SessionRecord:
    """Build a session row whose execution_state_json holds nodes."""
    tree = ExecutionStateTree(manifest=_MANIFEST, nodes=nodes)
    return SessionRecord(
        project_id="proj-a",
        session_id="s",
        blueprint_name="bp",
        blueprint_key="bp",
        execution_state_json=tree.model_dump_json(),
    )


class SummarizeSessionStepsTests:
    """Contract tests for summarize_session_steps."""

    def test_every_node_state_value_is_reported_verbatim(self) -> None:
        record = _record([ExecutionLeafNode(id=f"step-{state.value}", state=state) for state in NodeState])

        result = summarize_session_steps(record, _REDACTOR)

        assert [(row.step_id, row.state) for row in result.steps] == [
            (f"step-{state.value}", state.value) for state in NodeState
        ]

    def test_leaf_with_retries_sums_duration_and_uses_last_attempt(self) -> None:
        leaf = ExecutionLeafNode(
            id="flaky",
            name="Flaky step",
            state=NodeState.COMPLETED,
            attempts=[
                _attempt(1, exit_code=2, duration_seconds=1.5, error_message="first failure"),
                _attempt(2, exit_code=0, duration_seconds=2.0, error_message="recovered with warning"),
            ],
        )

        result = summarize_session_steps(_record([leaf]), _REDACTOR)

        assert result.steps == [
            StepRunSummary(
                kind="step",
                step_id="flaky",
                name="Flaky step",
                state="completed",
                attempts=2,
                exit_code=0,
                duration_seconds=3.5,
                error_message="recovered with warning",
            )
        ]

    @pytest.mark.parametrize(
        ("attempts", "expected_attempts"),
        [
            pytest.param([], 0, id="pending-leaf"),
            pytest.param([StepAttemptRecord(number=1, started_at="t0")], 1, id="running-attempt-without-result"),
        ],
    )
    def test_leaf_without_results_has_none_values(
        self, attempts: list[StepAttemptRecord], expected_attempts: int
    ) -> None:
        leaf = ExecutionLeafNode(id="waiting", attempts=attempts)

        result = summarize_session_steps(_record([leaf]), _REDACTOR)

        assert result.steps == [
            StepRunSummary(kind="step", step_id="waiting", state="pending", attempts=expected_attempts)
        ]

    def test_loop_with_multiple_iterations_flattens_in_plan_order(self) -> None:

        def body_leaf(step_id: str) -> ExecutionLeafNode:
            return ExecutionLeafNode(
                id=step_id, state=NodeState.COMPLETED, attempts=[_attempt(1, exit_code=0, duration_seconds=1.0)]
            )

        loop = ExecutionLoopNode(
            id="build-loop",
            max_iterations=3,
            on_max_iterations=FailurePolicy.ABORT,
            state=NodeState.COMPLETED,
            iterations=[
                ExecutionIterationRecord(number=number, steps=[body_leaf("code"), body_leaf("review")])
                for number in (1, 2)
            ],
        )

        result = summarize_session_steps(_record([ExecutionLeafNode(id="setup"), loop]), _REDACTOR)

        assert [(row.kind, row.step_id, row.loop_id, row.iteration, row.iterations) for row in result.steps] == [
            ("step", "setup", None, None, None),
            ("loop", "build-loop", None, None, 2),
            ("step", "code", "build-loop", 1, None),
            ("step", "review", "build-loop", 1, None),
            ("step", "code", "build-loop", 2, None),
            ("step", "review", "build-loop", 2, None),
        ]
        loop_row = result.steps[1]
        assert (loop_row.attempts, loop_row.exit_code, loop_row.duration_seconds, loop_row.error_message) == (
            None,
            None,
            None,
            None,
        )

    def test_error_message_is_redacted_and_streams_are_dropped(self) -> None:
        redactor = SecretRedactor.from_environment(env={"DB_PIN": "1234"}, credential_envs=("DB_PIN",))
        leaf = ExecutionLeafNode(
            id="deploy",
            state=NodeState.FAILED,
            attempts=[_attempt(1, exit_code=1, duration_seconds=1.0, error_message="login with pin 1234 failed")],
        )

        result = summarize_session_steps(_record([leaf]), redactor)

        assert result.steps[0].error_message == "login with pin [REDACTED:DB_PIN] failed"
        dumped = result.model_dump_json()
        assert all(text not in dumped for text in ("STDOUT-TEXT", "STDERR-TEXT", "OUTPUT-TEXT", "1234"))

    def test_missing_state_returns_warning_and_no_steps(self) -> None:
        record = SessionRecord(project_id="proj-a", session_id="s", blueprint_name="bp", blueprint_key="bp")

        result = summarize_session_steps(record, _REDACTOR)

        assert result == SessionStepsResult(steps=[], warnings=[NO_STEP_DATA_WARNING])

    def test_unparseable_state_returns_reason_warning(self) -> None:
        record = SessionRecord(
            project_id="proj-a",
            session_id="s",
            blueprint_name="bp",
            blueprint_key="bp",
            execution_state_json="not json",
        )

        result = summarize_session_steps(record, _REDACTOR)

        assert result.steps == []
        assert len(result.warnings) == 1
        assert result.warnings[0].startswith("Step data could not be read: ")

    def test_summarizing_writes_nothing(self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository) -> None:
        row = seed_failed_session(
            engine_paths,
            sessions_repo,
            session_id="failed-session",
            steps=[{"id": "format", "run": "true"}, {"id": "tests", "run": "false"}],
            failed_step_id="tests",
        )
        session_dir = engine_paths.session_dir("failed-session")

        def snapshot() -> tuple[dict[str, bytes], SessionRecord | None]:
            files = {str(path): path.read_bytes() for path in session_dir.rglob("*") if path.is_file()}
            return files, sessions_repo.get("failed-session")

        before = snapshot()
        result = summarize_session_steps(row, _REDACTOR)

        assert before[0]
        assert [(step.step_id, step.state, step.error_message) for step in result.steps] == [
            ("format", "completed", None),
            ("tests", "failed", SEEDED_FAILURE),
        ]
        assert snapshot() == before


class SummarizeSessionStepsRejectedPatchTests:
    def test_last_attempt_path_is_reported_and_older_state_without_it_reports_none(self) -> None:
        rejected = _attempt(2, exit_code=203, duration_seconds=1.0, error_message="gate said no")
        assert rejected.result is not None
        rejected.result.rejected_patch_path = "P"
        saved = ExecutionLeafNode(
            id="saved",
            state=NodeState.FAILED,
            attempts=[_attempt(1, exit_code=203, duration_seconds=1.0), rejected],
        )
        legacy = ExecutionLeafNode(
            id="legacy", state=NodeState.FAILED, attempts=[_attempt(1, exit_code=1, duration_seconds=1.0)]
        )

        result = summarize_session_steps(_record([saved, legacy]), _REDACTOR)

        assert [(row.step_id, row.rejected_patch_path) for row in result.steps] == [("saved", "P"), ("legacy", None)]
