"""Contract tests for engine/interrupted_steps.py: InterruptedStepCloser.close."""

from __future__ import annotations

import pytest

from dovo.common.timing import TimeSpan
from dovo.core.db import SessionRecord
from dovo.engine.executors.models import StepRecord
from dovo.engine.interrupted_steps import InterruptedStepCloser
from dovo.engine.models import DefinitionRef, DefinitionsManifest, IfBranch
from dovo.engine.state_models import (
    ExecutionIfNode,
    ExecutionIterationRecord,
    ExecutionLeafNode,
    ExecutionLoopNode,
    ExecutionPlanNode,
    ExecutionStateTree,
    NodeState,
    StepAttemptRecord,
)
from tests.harness.fakes import fixed_clock
from tests.harness.sessions import SEEDED_SPAN

_MANIFEST = DefinitionsManifest(
    blueprint=DefinitionRef(ref="repo:blueprint:bp", sha="abc", resolved_at="2026-09-28T00:00:00+00:00")
)
_OPEN_SPAN = TimeSpan(started_at="2026-01-02T03:04:00+00:00")
_CLOSED_SPAN = TimeSpan(
    started_at="2026-01-02T03:04:00+00:00", ended_at="2026-01-02T03:04:05+00:00", duration_seconds=5.0
)
_INTERRUPTED = StepRecord(
    step_id="tests",
    status="failed",
    exit_code=1,
    stdout="",
    stderr="",
    duration_seconds=5.0,
    error_message="Step attempt was interrupted before it recorded a result.",
)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Close every span at FIXED_NOW, 2026-01-02T03:04:05+00:00."""
    monkeypatch.setattr("dovo.common.timing.utc_now", fixed_clock)


def _completed(step_id: str, number: int = 1) -> StepAttemptRecord:
    """Return a closed, completed attempt of step_id."""
    result = StepRecord(step_id=step_id, status="completed", exit_code=0, stdout="", stderr="", duration_seconds=1.0)
    return StepAttemptRecord(number=number, span=SEEDED_SPAN, result=result)


def _record(nodes: list[ExecutionPlanNode], *, revision: int = 3, row_revision: int | None = 3) -> SessionRecord:
    """Return a running row holding a tree of nodes at revision, stored at row_revision."""
    tree = ExecutionStateTree(revision=revision, manifest=_MANIFEST, nodes=nodes)
    return SessionRecord(
        project_id="p",
        session_id="session-dead",
        blueprint_key="bp",
        blueprint_name="bp",
        execution_state_json=tree.model_dump_json(),
        execution_state_revision=row_revision,
    )


def _close(record: SessionRecord) -> ExecutionStateTree:
    """Close record's interrupted steps and parse the returned state."""
    closed = InterruptedStepCloser().close(record)
    assert closed is not None
    return ExecutionStateTree.model_validate_json(closed)


class InterruptedStepCloserTests:
    def test_open_attempt_is_closed_failed_and_step_failed(self) -> None:
        earlier = _completed("tests")
        leaf = ExecutionLeafNode(
            id="tests",
            state=NodeState.RUNNING,
            attempts=[earlier, StepAttemptRecord(number=2, span=_OPEN_SPAN)],
        )

        (closed,) = _close(_record([leaf])).nodes

        assert closed == ExecutionLeafNode(
            id="tests",
            state=NodeState.FAILED,
            attempts=[earlier, StepAttemptRecord(number=2, span=_CLOSED_SPAN, result=_INTERRUPTED)],
        )

    def test_running_loop_and_iteration_become_failed(self) -> None:
        done = ExecutionIterationRecord(
            number=1,
            state=NodeState.COMPLETED,
            steps=[ExecutionLeafNode(id="tests", state=NodeState.COMPLETED, attempts=[_completed("tests")])],
            until_passed=False,
        )
        loop = ExecutionLoopNode(
            id="fix",
            max_iterations=3,
            state=NodeState.RUNNING,
            iterations=[
                done,
                ExecutionIterationRecord(
                    number=2,
                    state=NodeState.RUNNING,
                    steps=[
                        ExecutionLeafNode(
                            id="tests", state=NodeState.RUNNING, attempts=[StepAttemptRecord(number=1, span=_OPEN_SPAN)]
                        ),
                        ExecutionLeafNode(id="lint"),
                    ],
                ),
            ],
        )

        (closed,) = _close(_record([loop])).nodes

        assert isinstance(closed, ExecutionLoopNode)
        assert closed.state is NodeState.FAILED
        assert closed.iterations[0] == done
        assert closed.iterations[1].state is NodeState.FAILED
        assert closed.iterations[1].steps == [
            ExecutionLeafNode(
                id="tests",
                state=NodeState.FAILED,
                attempts=[StepAttemptRecord(number=1, span=_CLOSED_SPAN, result=_INTERRUPTED)],
            ),
            ExecutionLeafNode(id="lint"),
        ]

    @pytest.mark.parametrize(
        "state", [NodeState.PENDING, NodeState.PAUSED, NodeState.COMPLETED, NodeState.FAILED, NodeState.SKIPPED]
    )
    def test_other_states_are_left_as_they_were(self, state: NodeState) -> None:
        other = ExecutionLeafNode(id="other", state=state, attempts=[StepAttemptRecord(number=1, span=_OPEN_SPAN)])
        loop = ExecutionLoopNode(id="later", max_iterations=2)
        running = ExecutionLeafNode(
            id="tests", state=NodeState.RUNNING, attempts=[StepAttemptRecord(number=1, span=_OPEN_SPAN)]
        )

        nodes = _close(_record([running, other, loop])).nodes

        assert nodes[1:] == [other, loop]

    def test_running_if_block_and_resulted_running_step_become_failed(self) -> None:
        resulted = ExecutionLeafNode(id="build", state=NodeState.RUNNING, attempts=[_completed("build")])
        gate = ExecutionIfNode(
            id="gate",
            state=NodeState.RUNNING,
            branch=IfBranch.THEN,
            steps=[ExecutionLeafNode(id="deploy", state=NodeState.COMPLETED, attempts=[_completed("deploy")])],
        )

        closed_leaf, closed_gate = _close(_record([resulted, gate])).nodes

        assert closed_leaf == resulted.model_copy(update={"state": NodeState.FAILED})
        assert closed_gate == gate.model_copy(update={"state": NodeState.FAILED})

    def test_returned_state_is_at_next_revision(self) -> None:
        leaf = ExecutionLeafNode(id="tests", state=NodeState.RUNNING)

        assert _close(_record([leaf], revision=3, row_revision=3)).revision == 4

    @pytest.mark.parametrize(
        "case", ["no-state", "unparseable", "revision-mismatch", "row-revision-none", "nothing-to-close"]
    )
    def test_unclosable_state_returns_none(self, case: str) -> None:
        running: list[ExecutionPlanNode] = [ExecutionLeafNode(id="tests", state=NodeState.RUNNING)]
        record = {
            "no-state": _record(running).model_copy(update={"execution_state_json": None}),
            "unparseable": _record(running).model_copy(update={"execution_state_json": "{not json"}),
            "revision-mismatch": _record(running, revision=2, row_revision=3),
            "row-revision-none": _record(running, revision=0, row_revision=None),
            "nothing-to-close": _record([ExecutionLeafNode(id="tests", state=NodeState.COMPLETED)]),
        }[case]

        assert InterruptedStepCloser().close(record) is None
