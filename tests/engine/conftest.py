"""Shared fixtures for engine/ domain tests."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.agents.models import AgentEnvOverrides, ResolvedAgentSettings
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.db.repositories.artifacts import ArtifactsRepository
from dovo.core.worktree import Worktree, WorktreeSession
from dovo.engine.executors.agent_step import build_agent_step_runner
from dovo.engine.executors.models import AgentStepRunner, StepExecutionContext
from dovo.engine.executors.step_executor import StepExecution
from dovo.engine.models import FailurePrompter, InteractionPrompter, RunObserver, RunOutcome, RunSettings
from dovo.engine.state_models import ExecutionStateTree
from dovo.engine.workspace import Workspace
from tests.harness.builders import WorkspaceBuilder
from tests.harness.workspace_paths import initialized_workspace_paths

type DriveRunCall = Callable[[WorkspacePaths, SessionsRepository, str], RunOutcome]
type StubDriveRun = Callable[[DriveRunCall | None], None]


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return initialized_workspace_paths(root)


@pytest.fixture
def engine_workspace(tmp_path: Path) -> Path:
    """Workspace root with an initialized database."""
    return WorkspaceBuilder(tmp_path / "workspace").with_database().build()


@pytest.fixture
def engine_paths(engine_workspace: Path) -> WorkspacePaths:
    """Return WorkspacePaths resolved for engine_workspace."""
    return _paths_for(engine_workspace)


@pytest.fixture
def sessions_repo(engine_paths: WorkspacePaths) -> SessionsRepository:
    """SessionsRepository bound to engine_paths' project database."""
    return SessionsRepository(db_path=engine_paths.database_file, project_id=engine_paths.project_id)


@pytest.fixture
def artifacts_repository(engine_paths: WorkspacePaths) -> ArtifactsRepository:
    """ArtifactsRepository bound to engine_paths' project database."""
    return ArtifactsRepository(db_path=engine_paths.database_file, project_id=engine_paths.project_id)


def _completed_outcome(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> RunOutcome:
    return RunOutcome(status=SessionStatus.COMPLETED, worktree_path=paths.root_dir, session_id=session_id)


@pytest.fixture
def stub_drive_run(monkeypatch: pytest.MonkeyPatch) -> StubDriveRun:
    """Replace drive_run so Engine persistence runs without executing steps.

    The installed stub returns on_call(paths, sessions, session_id), or a COMPLETED outcome
    for that session rooted at paths.root_dir when on_call is None.
    """

    def _install(on_call: DriveRunCall | None = None) -> None:
        call = on_call or _completed_outcome

        def fake_drive_run(
            paths: WorkspacePaths,
            sessions: SessionsRepository,
            session_id: str,
            *,
            observer: RunObserver | None,
            prompter: FailurePrompter | None,
            interaction_prompter: InteractionPrompter | None = None,
            no_tty: bool,
            env_overrides: AgentEnvOverrides | None = None,
        ) -> RunOutcome:
            return call(paths, sessions, session_id)

        monkeypatch.setattr("dovo.engine.engine.drive_run", fake_drive_run)

    return _install


@dataclass(frozen=True)
class SavedExecutionState:
    """One SessionsRepository.save_execution_state call, as the spy saw it."""

    execution_state_json: str
    status: SessionStatus | str | None

    @property
    def state(self) -> ExecutionStateTree:
        """Return the execution state this call carried."""
        return ExecutionStateTree.model_validate_json(self.execution_state_json)


type SaveHook = Callable[[list[SavedExecutionState]], bool]
type SpySaveExecutionState = Callable[[SaveHook | None], list[SavedExecutionState]]


@pytest.fixture
def spy_save_execution_state(monkeypatch: pytest.MonkeyPatch) -> SpySaveExecutionState:
    """Record every execution-state save; the returned list fills as saves happen.

    When hook is given it sees every call so far, the current one last, and the save is
    skipped, returning None as a lost write does, when hook returns False.
    """

    def _install(hook: SaveHook | None = None) -> list[SavedExecutionState]:
        recorded: list[SavedExecutionState] = []
        real = SessionsRepository.save_execution_state

        def spy(
            self: SessionsRepository,
            session_id: str,
            execution_state_json: str,
            *,
            expected_revision: int,
            next_revision: int,
            status: SessionStatus | str | None = None,
            error_message: str | None = None,
            worktree_id: str | None = None,
            worktree_kept: bool | None = None,
        ):
            recorded.append(SavedExecutionState(execution_state_json=execution_state_json, status=status))
            if hook is not None and not hook(recorded):
                return None
            return real(
                self,
                session_id,
                execution_state_json,
                expected_revision=expected_revision,
                next_revision=next_revision,
                status=status,
                error_message=error_message,
                worktree_id=worktree_id,
                worktree_kept=worktree_kept,
            )

        monkeypatch.setattr(SessionsRepository, "save_execution_state", spy)
        return recorded

    return _install


@pytest.fixture
def captured_contexts(monkeypatch: pytest.MonkeyPatch) -> list[StepExecutionContext]:
    """Collect the contexts StepCoordinator hands to StepExecution, in construction order."""
    captured: list[StepExecutionContext] = []

    class _CapturingStepExecution(StepExecution):
        def __init__(self, metadata: StepExecutionContext) -> None:
            captured.append(metadata)
            super().__init__(metadata)

    monkeypatch.setattr("dovo.engine.step_coordinator.StepExecution", _CapturingStepExecution)
    return captured


@dataclass(frozen=True)
class AgentRunnerBuild:
    """The arguments of one build_agent_step_runner call."""

    agent: ResolvedAgentSettings | None
    worktree_active: bool
    session_tmp_dir: Path | None
    main_checkout: Path
    session_log_dir: Path | None
    session_dir: Path | None


@pytest.fixture
def captured_builds(monkeypatch: pytest.MonkeyPatch) -> list[AgentRunnerBuild]:
    """Record each build_agent_step_runner call StepCoordinator makes, then build the real runner."""
    captured: list[AgentRunnerBuild] = []

    def _recording_build(
        agent: ResolvedAgentSettings | None,
        worktree_active: bool,
        *,
        session_tmp_dir: Path | None,
        main_checkout: Path,
        session_log_dir: Path | None,
        session_dir: Path | None,
    ) -> AgentStepRunner:
        build = AgentRunnerBuild(agent, worktree_active, session_tmp_dir, main_checkout, session_log_dir, session_dir)
        captured.append(build)
        return build_agent_step_runner(
            agent,
            worktree_active,
            session_tmp_dir=session_tmp_dir,
            main_checkout=main_checkout,
            session_log_dir=session_log_dir,
            session_dir=session_dir,
        )

    monkeypatch.setattr("dovo.engine.step_coordinator.build_agent_step_runner", _recording_build)
    return captured


@pytest.fixture
def worktree_root(tmp_path: Path) -> Path:
    """Git-backed workspace root with an initialized database, where worktrees can be created."""
    return WorkspaceBuilder(tmp_path / "workspace").with_git().with_database().build()


@pytest.fixture
def worktree_paths(worktree_root: Path) -> WorkspacePaths:
    """Return WorkspacePaths resolved for worktree_root."""
    return _paths_for(worktree_root)


class WorktreeSetup(NamedTuple):
    """A Workspace over worktree_root and the (target_dir, manager, session, error) its setup() returned."""

    workspace: Workspace
    target_dir: Path
    manager: Worktree | None
    session: WorktreeSession | None
    error: str | None


type WorktreeSetupFactory = Callable[..., WorktreeSetup]


@pytest.fixture
def worktree_setup(worktree_root: Path, worktree_paths: WorkspacePaths) -> WorktreeSetupFactory:
    """Run Workspace.setup() for RunSettings over worktree_root, using a worktree, with settings overriding fields."""

    def _setup(**settings: object) -> WorktreeSetup:
        context = dataclasses.replace(
            RunSettings(cwd=worktree_root, use_worktree=True, paths=worktree_paths), **settings
        )
        workspace = Workspace(context)
        return WorktreeSetup(workspace, *workspace.setup())

    return _setup
