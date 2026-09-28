"""Single-tier CLI integration tests for wt resume."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tests.harness.catalog import write_runnable_blueprint, write_runnable_step
from worktree.cli import app
from worktree.cli.ui.dispatcher import ui_dispatcher
from worktree.common.filesystem.models import RepositoryPaths, WorkspacePaths
from worktree.common.filesystem.services.global_root import resolve_global_paths
from worktree.core.blueprint import Blueprint
from worktree.core.catalog import Catalog
from worktree.core.config.models import ConfigTier
from worktree.core.db import RunStatus, WorktreeDb
from worktree.core.engine.models import SessionRunPayload
from worktree.core.engine.writer import get_session_dir, snapshot_definitions, write_session_run_json
from worktree.core.project.services.storage import resolve_workspace_paths
from worktree.core.runtime.models import RunCheckpoint


def _paths_for(root: Path) -> WorkspacePaths:
    """Resolve the WorkspacePaths snapshot for root, reflecting its current project.json."""
    return resolve_workspace_paths(RepositoryPaths.from_root(root), resolve_global_paths(None))


def _seed_paused_session(
    resume_workspace: Path, *, session_id: str, blueprint_key: str, pending_step_id: str, next_step_index: int
) -> None:
    """Insert a RUNNING run row, then a saved checkpoint that pauses it."""
    paths = _paths_for(resume_workspace)
    db = WorktreeDb(database_file=paths.database_file, project_id=paths.project_id)
    db.runs.create(
        session_id=session_id, blueprint_name=blueprint_key, blueprint_key=blueprint_key, status=RunStatus.RUNNING
    )
    checkpoint = RunCheckpoint(
        next_step_index=next_step_index,
        pending_step_id=pending_step_id,
        diagnostic="Step failed during interactive prompt.",
        use_sandbox=False,
    )
    db.runs.save_pause(session_id, checkpoint.model_dump_json(), checkpoint.diagnostic)


def _seed_snapshotted_paused_session(
    resume_workspace: Path, *, session_id: str, blueprint_key: str, pending_step_id: str, next_step_index: int
) -> None:
    """Snapshot blueprint_key's catalog blueprint/steps into session_id's definitions/ dir, matching Engine.run's own persistence, then seed a matching paused row."""
    paths = _paths_for(resume_workspace)
    catalog = Catalog(paths)
    blueprint = Blueprint.load(blueprint_key, catalog=catalog)
    session_dir = get_session_dir(paths, session_id)
    warnings: list[str] = []
    manifest = snapshot_definitions(catalog, blueprint, session_dir, warnings)
    assert manifest is not None

    db = WorktreeDb(database_file=paths.database_file, project_id=paths.project_id)
    db.runs.create(
        session_id=session_id, blueprint_name=blueprint_key, blueprint_key=blueprint_key, status=RunStatus.RUNNING
    )
    checkpoint = RunCheckpoint(
        next_step_index=next_step_index,
        pending_step_id=pending_step_id,
        diagnostic="Step failed during interactive prompt.",
        use_sandbox=False,
    )
    db.runs.save_pause(session_id, checkpoint.model_dump_json(), checkpoint.diagnostic)
    write_session_run_json(
        session_dir,
        SessionRunPayload(
            session_id=session_id,
            name=blueprint.name,
            status="paused",
            started_at="2026-09-25T19:00:00+00:00",
            definitions=manifest,
        ),
    )


class ResumeCliIntegrationTests:
    """Typer runner integration tests for wt resume."""

    def test_resume_cli_from_checkpoint_completes_remaining_steps_exits_zero(
        self, cli_runner: CliRunner, resume_workspace: Path
    ) -> None:
        """wt resume <session_id>: paused checkpoint with use_sandbox=False resumes and completes, exit 0, run record status becomes COMPLETED."""
        write_runnable_blueprint(
            resume_workspace,
            key="resume-task",
            steps=[
                {"id": "s1", "run": "true"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
                {"id": "s3", "run": "touch resumed.marker"},
            ],
        )
        _seed_paused_session(
            resume_workspace,
            session_id="paused-session-1",
            blueprint_key="resume-task",
            pending_step_id="s2",
            next_step_index=1,
        )

        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "paused-session-1"])

        assert result.exit_code == 0
        record = WorktreeDb(
            database_file=_paths_for(resume_workspace).database_file, project_id=_paths_for(resume_workspace).project_id
        ).runs.get("paused-session-1")
        assert record is not None
        assert record.status == RunStatus.COMPLETED
        assert (resume_workspace / "resumed.marker").exists()

    def test_resume_cli_unknown_session_exits_one(self, cli_runner: CliRunner, resume_workspace: Path) -> None:
        """wt resume <unknown-id>: no matching paused session, exit 1, 'Resume Failed' in stdout."""
        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "does-not-exist"])

        assert result.exit_code == 1
        assert "Resume Failed" in result.stdout

    def test_resume_cli_json_format_emits_run_success_event(
        self, cli_runner: CliRunner, resume_workspace: Path
    ) -> None:
        """wt resume <session_id> --format json: NDJSON stream includes a RunSuccessEvent with payload.status == 'completed'."""
        write_runnable_blueprint(
            resume_workspace,
            key="resume-json-task",
            steps=[
                {"id": "s1", "run": "true"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
            ],
        )
        _seed_paused_session(
            resume_workspace,
            session_id="paused-session-2",
            blueprint_key="resume-json-task",
            pending_step_id="s2",
            next_step_index=1,
        )

        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "paused-session-2", "--format", "json"])

        assert result.exit_code == 0
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        success_events = [e for e in events if e["event_type"] == "RunSuccessEvent"]
        assert len(success_events) == 1
        assert success_events[0]["payload"]["status"] == "completed"

    def test_resume_cli_malformed_user_tier_exits_one_with_config_error_panel(
        self,
        cli_runner: CliRunner,
        resume_workspace: Path,
        write_tier_config: Callable[[ConfigTier, dict[str, Any] | str], Path],
    ) -> None:
        """[tier-3/integration] wt resume: malformed User tier config.json → exit 1, tier-attributed message in stdout, no unhandled exception.

        The top-level callback resolves config before the resume handler ever runs, so a
        tier failure here renders a "Config Error" panel, not "Resume Failed" — no paused
        session is ever looked up.
        """
        ui_dispatcher.set_output_format("terminal")
        write_tier_config(ConfigTier.USER, "{not valid json")

        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "unknown-session"])

        assert result.exit_code == 1
        assert "Config Error" in result.stdout
        assert "Invalid configuration in user layer" in result.stdout

    def test_resume_cli_completes_after_source_catalog_blueprint_deleted(
        self, cli_runner: CliRunner, resume_workspace: Path
    ) -> None:
        """[tier-3/integration] wt resume <session_id>: a session paused by wt run with a uses: step still resumes and completes exit 0 after both the catalog blueprint and step YAML files are deleted from disk."""
        write_runnable_step(
            resume_workspace, key="lint-check", definition={"id": "lint-check", "type": "command", "command": "true"}
        )
        write_runnable_blueprint(
            resume_workspace,
            key="snapshot-resume-task",
            steps=[
                {"id": "s1", "uses": "lint-check"},
                {"id": "s2", "run": "true", "on_failure": "continue"},
                {"id": "s3", "run": "touch resumed.marker"},
            ],
        )
        _seed_snapshotted_paused_session(
            resume_workspace,
            session_id="snap-resume-1",
            blueprint_key="snapshot-resume-task",
            pending_step_id="s2",
            next_step_index=1,
        )
        (resume_workspace / ".worktree" / "catalog" / "blueprints" / "snapshot-resume-task.yml").unlink()
        (resume_workspace / ".worktree" / "catalog" / "steps" / "lint-check.yml").unlink()

        result = cli_runner.invoke(app, ["-p", str(resume_workspace), "resume", "snap-resume-1"])

        assert result.exit_code == 0
        record = WorktreeDb(
            database_file=_paths_for(resume_workspace).database_file, project_id=_paths_for(resume_workspace).project_id
        ).runs.get("snap-resume-1")
        assert record is not None
        assert record.status == RunStatus.COMPLETED
        assert (resume_workspace / "resumed.marker").exists()
