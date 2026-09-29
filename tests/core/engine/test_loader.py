"""Contract tests for EngineLoader: paused-run validation before coordinator dispatch."""

from __future__ import annotations

import pytest

from tests.harness.runs import seed_paused_run
from worktree.common.filesystem.models import WorkspacePaths
from worktree.core.db import RunRecord, RunsRepository, RunStatus
from worktree.core.engine import EngineLoader, EngineResumeError, EngineResumeStatus, RunStateStore
from worktree.core.engine.state_models import ExecutionLeafNode, NodeState
from worktree.core.engine.writer import snapshot_blueprint_path

_STEPS: list[dict[str, object]] = [{"id": "a", "run": "true"}, {"id": "b", "run": "exit 1"}]


def _seed(
    paths: WorkspacePaths,
    runs: RunsRepository,
    *,
    use_sandbox: bool = False,
    sandbox_id: str | None = None,
) -> RunRecord:
    return seed_paused_run(
        paths,
        runs,
        session_id="paused-1",
        steps=_STEPS,
        paused_step_id="b",
        use_sandbox=use_sandbox,
        sandbox_id=sandbox_id,
    )


def _break_state(paths: WorkspacePaths, runs: RunsRepository, fault: str) -> None:
    """Corrupt the seeded paused run's state document in the way fault names."""
    store = RunStateStore(runs, paths, "paused-1")
    loaded = store.load().state
    assert loaded is not None
    if fault == "paused-leaf-without-result":
        paused = loaded.nodes[1]
        assert isinstance(paused, ExecutionLeafNode)
        paused.attempts[-1] = paused.attempts[-1].model_copy(update={"result": None})
    else:
        for node in loaded.nodes:
            node.state = NodeState.COMPLETED
    assert store.save(loaded).ok


class EngineLoaderTests:
    """[tier-1/integration] EngineLoader.load_for_resume: classify and validate a paused run before dispatch."""

    def test_paused_run_returns_row_state_and_manifest(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: a paused row with a PAUSED leaf and intact snapshots returns (row, state, state.manifest) with row.session_id equal to the argument."""
        _seed(engine_paths, runs_repo)

        row, state, manifest = EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert row.session_id == "paused-1"
        assert manifest == state.manifest
        assert [node.state for node in state.nodes] == [NodeState.COMPLETED, NodeState.PAUSED]

    @pytest.mark.parametrize(
        ("fault", "expected_status"),
        [
            pytest.param("no-row", EngineResumeStatus.NOT_FOUND, id="not-found"),
            pytest.param("completed-row", EngineResumeStatus.WRONG_STATUS, id="wrong-status"),
            pytest.param("no-state", EngineResumeStatus.CORRUPT_STATE, id="missing-state"),
            pytest.param("corrupt-json", EngineResumeStatus.FAILED, id="corrupt-state"),
            pytest.param("deleted-snapshot", EngineResumeStatus.MISSING_SNAPSHOT, id="missing-snapshot"),
            pytest.param("invalid-snapshot-yaml", EngineResumeStatus.FAILED, id="invalid-snapshot"),
            pytest.param("paused-leaf-without-result", EngineResumeStatus.CORRUPT_STATE, id="paused-leaf-no-result"),
            pytest.param("all-nodes-terminal", EngineResumeStatus.CORRUPT_STATE, id="nothing-runnable"),
        ],
    )
    def test_invalid_run_raises_engine_resume_error_with_status(
        self,
        engine_paths: WorkspacePaths,
        runs_repo: RunsRepository,
        fault: str,
        expected_status: EngineResumeStatus,
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: each seeded fault raises EngineResumeError whose .status equals expected_status and whose message starts "Cannot resume session" (or "Session '<id>' not found." for NOT_FOUND)."""
        if fault == "no-state":
            runs_repo.create("paused-1", blueprint_name="x", blueprint_key="x", status=RunStatus.PAUSED)
        elif fault != "no-row":
            row = _seed(engine_paths, runs_repo)
            if fault == "completed-row":
                runs_repo.update_status("paused-1", RunStatus.COMPLETED)
            elif fault == "corrupt-json":
                runs_repo.save_execution_state(
                    "paused-1", "{not json", expected_revision=row.execution_state_revision or 0, next_revision=99
                )
            elif fault == "deleted-snapshot":
                snapshot_blueprint_path(engine_paths.session_dir("paused-1"), "paused-1").unlink()
            elif fault == "invalid-snapshot-yaml":
                snapshot_blueprint_path(engine_paths.session_dir("paused-1"), "paused-1").write_text("- a\n- list\n")
            else:
                _break_state(engine_paths, runs_repo, fault)

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert exc_info.value.status == expected_status
        prefix = "Session 'paused-1' not found." if fault == "no-row" else "Cannot resume session 'paused-1'"
        assert str(exc_info.value).startswith(prefix)

    def test_sandboxed_run_with_existing_directory_loads(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: use_sandbox=True with sandbox_id set and paths.sandbox_dir(sandbox_id) present returns the row with that sandbox_id unchanged."""
        _seed(engine_paths, runs_repo, use_sandbox=True, sandbox_id="sbx-1")
        engine_paths.sandbox_dir("sbx-1").mkdir(parents=True)

        row, _, _ = EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert row.sandbox_id == "sbx-1"

    @pytest.mark.parametrize(
        "sandbox_id", [pytest.param(None, id="no-id"), pytest.param("gone", id="deleted-directory")]
    )
    def test_sandboxed_run_without_retained_directory_raises_missing_sandbox_and_keeps_row(
        self, engine_paths: WorkspacePaths, runs_repo: RunsRepository, sandbox_id: str | None
    ) -> None:
        """[tier-1/integration] EngineLoader.load_for_resume: use_sandbox=True with sandbox_id None or a missing directory raises EngineResumeError(MISSING_SANDBOX) and runs_repo.get(session_id) still returns the row."""
        _seed(engine_paths, runs_repo, use_sandbox=True, sandbox_id=sandbox_id)

        with pytest.raises(EngineResumeError) as exc_info:
            EngineLoader.load_for_resume(runs_repo, engine_paths, "paused-1")

        assert exc_info.value.status == EngineResumeStatus.MISSING_SANDBOX
        assert "no longer exists" in str(exc_info.value)
        assert runs_repo.get("paused-1") is not None
