"""Contract tests for failure-orchestration helpers: policy resolution and diagnostics."""

from __future__ import annotations

import pytest

from dovo.common.models import FailurePolicy, OnFailureSpec
from dovo.engine.executors.models import StepResult
from dovo.engine.failure import (
    USER_CONTINUED_MARKER,
    effective_terminal_policy,
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

        assert updated == original.model_copy(
            update={"status": "ignored", "error_message": f"boom ({USER_CONTINUED_MARKER})"}
        )

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


class ResolveTerminalActionTests:
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
