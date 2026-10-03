"""Contract tests for failure-orchestration helpers: policy resolution and diagnostics."""

from __future__ import annotations

import pytest

from dovo.common.models import FailurePolicy, OnFailureSpec
from dovo.engine.executors.models import StepResult
from dovo.engine.failure import (
    USER_CONTINUED_MARKER,
    effective_terminal_policy,
    failed_step_message,
    mark_continued_after_prompt,
    resolve_terminal_action,
    step_failure_diagnostic,
)
from dovo.engine.models import StepAction


class FailurePolicyHelperTests:
    """Contract tests for effective_terminal_policy, mark_continued_after_prompt, step_failure_diagnostic."""

    def test_effective_terminal_policy_retry_action_returns_on_max_retries_policy(self) -> None:
        spec = OnFailureSpec(action=FailurePolicy.RETRY, on_max_retries=FailurePolicy.CONTINUE)

        assert effective_terminal_policy(spec) == FailurePolicy.CONTINUE

    def test_effective_terminal_policy_non_retry_action_returns_action_unchanged(self) -> None:
        spec = OnFailureSpec(action=FailurePolicy.CONTINUE)

        assert effective_terminal_policy(spec) == FailurePolicy.CONTINUE

    def test_mark_continued_after_prompt_sets_status_ignored_and_appends_continued_marker(self) -> None:
        original = StepResult(
            step_id="publish",
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.1,
            attempts=1,
            error_message="boom",
            errors=[],
            warnings=[],
            fixes=[],
        )

        updated = mark_continued_after_prompt(original)

        assert updated.step_id == "publish"
        assert updated.status == "ignored"
        assert updated.exit_code == 1
        assert updated.stdout == ""
        assert updated.stderr == ""
        assert updated.duration_seconds == 0.1
        assert updated.attempts == 1
        assert updated.error_message == f"boom ({USER_CONTINUED_MARKER})"
        assert updated.errors == []
        assert updated.warnings == []
        assert updated.fixes == []
        assert updated.ok is True

    def test_step_failure_diagnostic_prefers_error_message_over_stderr_and_exit_code(self) -> None:
        result = StepResult(
            step_id="publish",
            status="failed",
            exit_code=1,
            stdout="",
            stderr="stderr output",
            duration_seconds=0.1,
            error_message="explicit failure",
        )

        assert step_failure_diagnostic(result) == "explicit failure"


class FailedStepMessageTests:
    """[tier-1/unit] failed_step_message: diagnostic message format."""

    def test_formats_step_id_and_diagnostic_detail(self) -> None:
        """[tier-1/unit] failed_step_message: renders "Step '<id>' failed: <diagnostic>" using error_message when present."""
        result = StepResult(
            step_id="fail",
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message="Command failed with exit code 1.",
        )

        assert failed_step_message(result) == "Step 'fail' failed: Command failed with exit code 1."


class ResolveTerminalActionTests:
    """[tier-1/unit] resolve_terminal_action: non-prompt terminal policy to StepAction."""

    @pytest.mark.parametrize(
        ("policy", "action", "recorded_status", "error_message"),
        [
            pytest.param(FailurePolicy.CONTINUE, StepAction.CONTINUE, "ignored", None, id="continue"),
            pytest.param(FailurePolicy.ABORT, StepAction.ABORT, "failed", "Step 'fail' failed: boom", id="abort"),
        ],
    )
    def test_resolve_terminal_action_maps_policy_to_action_result_and_message(
        self, policy: FailurePolicy, action: StepAction, recorded_status: str, error_message: str | None
    ) -> None:
        """[tier-1/unit] resolve_terminal_action: CONTINUE returns (StepAction.CONTINUE, an ignored result carrying the user-continued marker, None); ABORT returns (StepAction.ABORT, the unchanged failed result, "Step '<id>' failed: <detail>")."""
        failed = StepResult(
            step_id="fail",
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message="boom",
        )

        resolved_action, recorded, message = resolve_terminal_action(policy, failed)

        assert resolved_action is action
        assert recorded is not None
        assert recorded.status == recorded_status
        assert message == error_message
        if policy == FailurePolicy.CONTINUE:
            assert recorded.error_message is not None
            assert recorded.error_message.endswith(f"({USER_CONTINUED_MARKER})")
        else:
            assert recorded == failed
