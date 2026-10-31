"""Contract tests: `dovo run` and `dovo resume` reject a blueprint exactly as `dovo blueprint validate` does."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.catalog import Catalog
from dovo.core.catalog.definitions import BlueprintDefinition, StepDefinition
from dovo.core.catalog.models import CatalogItemType
from dovo.core.db import SessionsRepository, SessionStatus
from dovo.core.git.runner import GitRunner
from dovo.engine.services.resume import BlueprintResumeService
from dovo.engine.services.run import BlueprintRunService
from dovo.engine.writer import snapshot_blueprint_path
from tests.engine.conftest import StubDriveRun
from tests.harness.catalog import write_runnable_blueprint
from tests.harness.fakes import fixed_clock, fixed_new_id
from tests.harness.sessions import seed_paused_session

_KEY = "deploy"

INVALID_DOCUMENTS = [
    pytest.param({"steps": [{"id": "a", "run": "echo one"}, {"id": "a", "run": "echo two"}]}, id="duplicate-step-id"),
    pytest.param(
        {
            "inputs": {
                "env": {"type": "string", "aliases": ["e"]},
                "region": {"type": "string", "aliases": ["e"]},
            },
            "steps": [{"id": "a", "run": "true"}],
        },
        id="duplicate-input-alias",
    ),
    pytest.param({"steps": [{"id": "build", "type": "script", "script_path": "../build.sh"}]}, id="unsafe-script-path"),
    pytest.param({"steps": [{"id": "a", "run": "true"}, {"id": "b", "type": "agent"}]}, id="agent-without-prompt"),
    pytest.param(
        {
            "steps": [
                {
                    "id": "phases",
                    "type": "loop",
                    "until": ["steps.check.exit_code == 0"],
                    "untill": ["steps.check.exit_code == 0"],
                    "do": [{"id": "check", "run": "true"}],
                }
            ]
        },
        id="loop-unknown-key",
    ),
    pytest.param(
        {
            "steps": [
                {"id": "build", "type": "script", "script_path": "scripts/missing.sh"},
                {"id": "build", "run": "true"},
            ]
        },
        id="error-with-warning",
    ),
]


def _write_document(paths: WorkspacePaths, document: dict[str, object]) -> None:
    """Overwrite the catalog blueprint `deploy` and its run snapshot, when one exists, with document."""
    content = yaml.safe_dump({"name": _KEY, **document}, sort_keys=False)
    (paths.root_dir / ".dovo" / "catalog" / "blueprints" / f"{_KEY}.yml").write_text(content, encoding="utf-8")
    snapshot = snapshot_blueprint_path(paths.session_dir(_KEY), _KEY)
    if snapshot.parent.is_dir():
        snapshot.write_text(content, encoding="utf-8")


def _findings(result: object) -> tuple[object, ...]:
    """Return the error code, errors, warnings and remediations a result reports."""
    return tuple(getattr(result, field) for field in ("error_code", "errors", "warnings", "remediations"))


class ValidationParityTests:
    """The run and resume services report the error code, errors, warnings and remediations validate reports."""

    @pytest.mark.parametrize("document", INVALID_DOCUMENTS)
    def test_run_and_resume_report_what_validate_reports(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: StubDriveRun,
        document: dict[str, object],
    ) -> None:
        stub_drive_run(None)
        seed_paused_session(
            engine_paths, sessions_repo, session_id=_KEY, steps=[{"id": "a", "run": "true"}], paused_step_id="a"
        )
        _write_document(engine_paths, document)

        validated = Catalog(engine_paths).validate(
            _KEY, item_type=CatalogItemType.BLUEPRINT, blueprint_cls=BlueprintDefinition, step_cls=StepDefinition
        )
        ran = BlueprintRunService(
            name=_KEY,
            paths=engine_paths,
            sessions_db=sessions_repo,
            no_worktree=True,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()
        resumed = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id=_KEY,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert validated.ok is False
        assert _findings(ran) == _findings(validated)
        assert _findings(resumed) == _findings(validated)
        assert [row.session_id for row in sessions_repo.list()] == [_KEY]

    def test_resume_of_invalid_snapshot_leaves_the_session_paused_and_its_state_unchanged(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        stub_drive_run(None)
        before = seed_paused_session(
            engine_paths, sessions_repo, session_id=_KEY, steps=[{"id": "a", "run": "true"}], paused_step_id="a"
        )
        _write_document(engine_paths, {"steps": [{"id": "a", "run": "true"}, {"id": "a", "run": "true"}]})

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id=_KEY,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        after = sessions_repo.get(_KEY)
        assert result.errors == ["Step id 'a' is used by more than one step."]
        assert after is not None
        assert after.status == SessionStatus.PAUSED
        assert (after.execution_state_revision, after.execution_state_json) == (
            before.execution_state_revision,
            before.execution_state_json,
        )

    def test_run_with_only_warnings_starts_and_reports_them(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        stub_drive_run(None)
        write_runnable_blueprint(
            engine_paths.root_dir,
            key=_KEY,
            steps=[{"id": "build", "type": "script", "script_path": "scripts/missing.sh"}],
        )

        result = BlueprintRunService(
            name=_KEY,
            paths=engine_paths,
            sessions_db=sessions_repo,
            no_worktree=True,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert result.ok is True
        assert result.warnings[0] == (
            "Step 'build' script_path 'scripts/missing.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)."
        )

    def test_resume_looks_up_script_path_in_the_run_worktree(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository, stub_drive_run: StubDriveRun
    ) -> None:
        stub_drive_run(None)
        script = engine_paths.root_dir / "scripts" / "build.sh"
        script.parent.mkdir(parents=True)
        script.write_text("true\n", encoding="utf-8")
        steps: list[dict[str, object]] = [
            {"id": "build", "type": "script", "script_path": "scripts/build.sh", "on_failure": "prompt_user"}
        ]
        seed_paused_session(
            engine_paths,
            sessions_repo,
            session_id=_KEY,
            steps=steps,
            paused_step_id="build",
            use_worktree=True,
            worktree_id=_KEY,
        )
        Path(engine_paths.worktree_dir(_KEY)).mkdir(parents=True)

        result = BlueprintResumeService(
            paths=engine_paths,
            db=sessions_repo,
            session_id=_KEY,
            git=GitRunner(),
            clock=fixed_clock,
            new_id=fixed_new_id,
        ).execute()

        assert "Step 'build' script_path 'scripts/build.sh' does not exist on disk (CATALOG_SCRIPT_NOT_FOUND)." in (
            result.warnings
        )
