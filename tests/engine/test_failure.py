"""Contract tests for failure-orchestration helpers: policy resolution and diagnostics."""

from __future__ import annotations

import pytest

from dovo.common.models import FailurePolicy, OnFailureSpec
from dovo.engine.executors.models import StepResult
from dovo.engine.failure import (
    USER_CONTINUED_MARKER,
    apply_prompt_decision,
    effective_terminal_policy,
    mark_continued_after_prompt,
    prompt_failure_decision,
    resolve_terminal_action,
    step_failure_diagnostic,
)
from dovo.engine.models import FailurePromptDecision, StepAction
from tests.harness.builders import StepBuilder
from tests.harness.sessions import ScriptedFailurePrompter


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


def _failed_result() -> StepResult:
    return StepResult(
        step_id="fail",
        status="failed",
        exit_code=1,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        error_message="boom",
    )


_PROMPT_STEP = StepBuilder.command("exit 1").with_id("fail").with_on_failure(FailurePolicy.PROMPT_USER).build()


class FailurePromptDecisionTests:
    """Contract tests for prompt_failure_decision and apply_prompt_decision."""

    @pytest.mark.parametrize(
        ("no_tty", "prompter", "warning"),
        [
            pytest.param(
                True,
                ScriptedFailurePrompter(),
                "Warning: step 'fail' requested prompt_user but the run is non-interactive; aborting.",
                id="no-tty",
            ),
            pytest.param(
                False,
                None,
                "Warning: step 'fail' requested prompt_user but no failure prompter is configured; aborting.",
                id="no-prompter",
            ),
        ],
    )
    def test_prompt_failure_decision_aborts_with_warning_when_non_interactive(
        self, no_tty: bool, prompter: ScriptedFailurePrompter | None, warning: str
    ) -> None:
        """Return (ABORT, warning) with the exact text and never consult the prompter."""
        decision = prompt_failure_decision(prompter, _PROMPT_STEP, _failed_result(), no_tty=no_tty)

        assert decision == (FailurePromptDecision.ABORT, warning)
        if prompter is not None:
            assert prompter.prompted == []

    def test_prompt_failure_decision_returns_the_prompter_decision(self) -> None:
        """Return (RETRY, None) from a prompter queued with RETRY, which saw the result's diagnostic 'boom'."""
        prompter = ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        failed = _failed_result()

        decision = prompt_failure_decision(prompter, _PROMPT_STEP, failed, no_tty=False)

        assert decision == (FailurePromptDecision.RETRY, None)
        assert prompter.prompted == [failed]
        assert prompter.diagnostics == ["boom"]

    @pytest.mark.parametrize(
        ("decision", "expected_action", "records", "message"),
        [
            pytest.param(FailurePromptDecision.RETRY, StepAction.RETRY, "nothing", None, id="retry"),
            pytest.param(FailurePromptDecision.CONTINUE, StepAction.CONTINUE, "continued", None, id="continue"),
            pytest.param(
                FailurePromptDecision.ABORT, StepAction.ABORT, "failed", "Step 'fail' failed: boom", id="abort"
            ),
        ],
    )
    def test_apply_prompt_decision_maps_each_decision(
        self, decision: FailurePromptDecision, expected_action: StepAction, records: str, message: str | None
    ) -> None:
        """Map RETRY to no record, CONTINUE to mark_continued_after_prompt(result), ABORT to the failed result and its message."""
        failed = _failed_result()
        expected_records = {"nothing": None, "continued": mark_continued_after_prompt(failed), "failed": failed}

        assert apply_prompt_decision(decision, failed) == (expected_action, expected_records[records], message)
