"""Contract tests for engine/models.py: result-type success predicates and manifest strictness."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.common.error_codes import ErrorCode
from dovo.core.agents import default_tool_policy
from dovo.core.agents.models import ResolvedAgentSettings
from dovo.core.db import SessionStatus
from dovo.engine.models import AgentSettingsResolution, BlueprintRunResult, DefinitionRef, RunOutcome


class BlueprintRunResultTests:
    @pytest.mark.parametrize(
        ("error_code", "session_status", "errors", "expected"),
        [
            pytest.param(None, SessionStatus.COMPLETED, [], True, id="no-code-with-session"),
            pytest.param(None, None, [], True, id="no-code-without-session"),
            pytest.param(ErrorCode.RUN_STEP_FAILED, SessionStatus.FAILED, ["boom"], False, id="code-with-session"),
            pytest.param(ErrorCode.CATALOG_NOT_FOUND, None, ["missing"], False, id="code-without-session"),
        ],
    )
    def test_ok_is_true_exactly_when_error_code_is_null(
        self, error_code: ErrorCode | None, session_status: SessionStatus | None, errors: list[str], expected: bool
    ) -> None:
        session_id = None if session_status is None else "s"
        result = BlueprintRunResult(
            error_code=error_code, errors=errors, session_id=session_id, session_status=session_status
        )

        assert result.ok is expected


class RunOutcomeTests:
    @pytest.mark.parametrize(
        ("status", "errors", "expected"),
        [
            pytest.param(SessionStatus.COMPLETED, [], True, id="completed"),
            pytest.param(SessionStatus.COMPLETED, ["late error"], False, id="completed-with-errors"),
            pytest.param(SessionStatus.FAILED, [], False, id="failed"),
            pytest.param(SessionStatus.PAUSED, [], False, id="paused"),
        ],
    )
    def test_ok_requires_completed_status_and_no_errors(
        self, tmp_path: Path, status: SessionStatus, errors: list[str], expected: bool
    ) -> None:
        outcome = RunOutcome(status=status, worktree_path=tmp_path, errors=errors)

        assert outcome.ok is expected


class AgentSettingsResolutionTests:
    def test_ok_follows_error_code_not_settings(self) -> None:
        settings = ResolvedAgentSettings(
            provider="copilot",
            model="m",
            endpoint="http://localhost",
            temperature=0.0,
            max_tokens=1,
            tools=default_tool_policy(),
        )

        assert AgentSettingsResolution().ok is True
        assert AgentSettingsResolution(settings=settings).ok is True
        assert AgentSettingsResolution(error_code=ErrorCode.INTERNAL_ERROR, errors=["boom"]).ok is False


class DefinitionRefTests:
    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DefinitionRef.model_validate({"ref": "lint", "sha": "abc", "resolved_at": "t", "extra": 1})
