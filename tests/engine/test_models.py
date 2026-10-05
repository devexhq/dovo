"""Contract tests for engine/models.py: result-type success predicates and manifest strictness."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dovo.core.agents.models import ResolvedAgentSettings
from dovo.core.db import SessionRecord, SessionStatus
from dovo.engine.models import AgentSettingsResolution, BlueprintRunResult, DefinitionRef, RunOutcome


class BlueprintRunResultTests:
    @pytest.mark.parametrize(
        ("has_record", "errors", "expected"),
        [
            pytest.param(True, [], True, id="record-no-errors"),
            pytest.param(False, [], False, id="no-record"),
            pytest.param(True, ["boom"], False, id="record-with-errors"),
        ],
    )
    def test_ok_requires_a_run_record_and_no_errors(self, has_record: bool, errors: list[str], expected: bool) -> None:
        """[tier-1/unit] BlueprintRunResult.ok: True only when session_record is set and errors is empty."""
        record = SessionRecord(session_id="s", blueprint_name="lint", blueprint_key="lint") if has_record else None

        result = BlueprintRunResult(session_record=record, errors=errors)

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
        """[tier-1/unit] RunOutcome.ok: True only for status COMPLETED with an empty errors list."""
        outcome = RunOutcome(status=status, worktree_path=tmp_path, errors=errors)

        assert outcome.ok is expected


class AgentSettingsResolutionTests:
    def test_ok_is_false_without_settings_and_true_with_them(self) -> None:
        """[tier-1/unit] AgentSettingsResolution.ok: False when settings is None, True when resolved settings are present."""
        settings = ResolvedAgentSettings(
            provider="copilot", model="m", endpoint="http://localhost", temperature=0.0, max_tokens=1
        )

        assert AgentSettingsResolution().ok is False
        assert AgentSettingsResolution(settings=settings).ok is True


class DefinitionRefTests:
    def test_unknown_field_is_rejected(self) -> None:
        """[tier-1/unit] DefinitionRef: an unexpected field raises ValidationError, so a manifest cannot carry undeclared keys."""
        with pytest.raises(ValidationError):
            DefinitionRef.model_validate({"ref": "lint", "sha": "abc", "resolved_at": "t", "extra": 1})
