"""Contract tests for engine/services/resume.py: BlueprintResumeService.execute."""

from __future__ import annotations

from collections.abc import Callable

from dovo.common.filesystem.models import WorkspacePaths
from dovo.core.db import SessionsRepository
from dovo.engine.models import RunOutcome
from dovo.engine.services.resume import BlueprintResumeService
from tests.harness.sessions import seed_paused_session


def _seed(paths: WorkspacePaths, sessions: SessionsRepository, session_id: str) -> None:
    seed_paused_session(
        paths,
        sessions,
        session_id=session_id,
        steps=[{"id": "a", "run": "true", "on_failure": "prompt_user"}],
        paused_step_id="a",
    )


class BlueprintResumeServiceExecuteTests:
    def test_execute_with_explicit_session_resumes_it_and_returns_its_record(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: Callable[[RunOutcome | None], None],
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: an explicit paused session id resumes that session and returns ok with its session_record."""
        _seed(engine_paths, sessions_repo, "paused-1")
        stub_drive_run(None)

        result = BlueprintResumeService(paths=engine_paths, db=sessions_repo, session_id="paused-1").execute()

        assert result.ok
        assert result.session_record is not None
        assert result.session_record.session_id == "paused-1"

    def test_execute_without_session_resumes_the_latest_paused_run(
        self,
        engine_paths: WorkspacePaths,
        sessions_repo: SessionsRepository,
        stub_drive_run: Callable[[RunOutcome | None], None],
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: with no session id, the most recently started paused run is resumed."""
        _seed(engine_paths, sessions_repo, "paused-old")
        _seed(engine_paths, sessions_repo, "paused-new")
        latest = sessions_repo.get_latest_paused()
        assert latest is not None
        stub_drive_run(None)

        result = BlueprintResumeService(paths=engine_paths, db=sessions_repo).execute()

        assert result.session_record is not None
        assert result.session_record.session_id == latest.session_id

    def test_execute_without_session_and_no_paused_run_returns_failed_result(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: no session id and no paused run returns session_record None and the single error 'No paused session found to resume.'."""
        result = BlueprintResumeService(paths=engine_paths, db=sessions_repo).execute()

        assert not result.ok
        assert result.session_record is None
        assert result.errors == ["No paused session found to resume."]

    def test_execute_unknown_session_returns_the_engine_resume_error_message(
        self, engine_paths: WorkspacePaths, sessions_repo: SessionsRepository
    ) -> None:
        """[tier-1/integration] BlueprintResumeService.execute: a session id with no session row returns session_record None and one error naming the session id."""
        result = BlueprintResumeService(paths=engine_paths, db=sessions_repo, session_id="ghost").execute()

        assert not result.ok
        assert result.session_record is None
        assert len(result.errors) == 1
        assert "ghost" in result.errors[0]
