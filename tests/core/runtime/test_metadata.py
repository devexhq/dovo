"""Integration contract tests for previous-step and historical-steps metadata propagation."""

from __future__ import annotations

from pathlib import Path

from worktree.common.models import FailurePolicy, OnFailureSpec
from worktree.core.db import RunStatus
from worktree.core.runtime import FailurePromptDecision, LoopPromptDecision, RunContext, run_steps
from worktree.core.step.models import LoopStepBlock, StepDefinition, StepResult, StepType


class _ScriptedFailurePrompter:
    """Test double returning a scripted queue of decisions for metadata-propagation tests."""

    def __init__(self, decisions: list[FailurePromptDecision]) -> None:
        self.decisions = list(decisions)

    def prompt_step_failure(
        self, *, step: StepDefinition, result: StepResult, diagnostic: str
    ) -> FailurePromptDecision:
        return self.decisions.pop(0)

    def prompt_loop_max_iterations(self, **kwargs: object) -> LoopPromptDecision:
        return LoopPromptDecision.ABORT


class RunStepsMetadataPropagationTests:
    """Contract tests for WT_PREVIOUS_STEP_*, WT_STEPS_JSON, and WT_STEP_ATTEMPT propagation."""

    def test_run_steps_first_step_sees_empty_previous_step_env_vars(self, tmp_path: Path) -> None:
        context = RunContext(
            steps=[
                StepDefinition(
                    id="first_step",
                    type=StepType.COMMAND,
                    command='echo "PREV_ID=[$WT_PREVIOUS_STEP_ID] PREV_STATUS=[$WT_PREVIOUS_STEP_STATUS]"',
                )
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert len(outcome.step_results) == 1
        assert outcome.step_results[0].step_id == "first_step"
        assert outcome.step_results[0].status == "completed"
        assert outcome.step_results[0].exit_code == 0
        assert outcome.step_results[0].stdout == "PREV_ID=[] PREV_STATUS=[]\n"

    def test_run_steps_second_step_sees_previous_step_id_name_index_status_exit_code_env_vars(
        self, tmp_path: Path
    ) -> None:
        context = RunContext(
            steps=[
                StepDefinition(
                    id="setup_step",
                    name="Setup Step",
                    type=StepType.COMMAND,
                    command="echo 'setup done'",
                ),
                StepDefinition(
                    id="verify_step",
                    name="Verify Step",
                    type=StepType.COMMAND,
                    command=(
                        'echo "PREV_ID=$WT_PREVIOUS_STEP_ID PREV_NAME=$WT_PREVIOUS_STEP_NAME '
                        "PREV_IDX=$WT_PREVIOUS_STEP_INDEX PREV_STATUS=$WT_PREVIOUS_STEP_STATUS "
                        'PREV_EXIT=$WT_PREVIOUS_STEP_EXIT_CODE"'
                    ),
                ),
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert len(outcome.step_results) == 2

        assert outcome.step_results[0].step_id == "setup_step"
        assert outcome.step_results[0].status == "completed"
        assert outcome.step_results[0].exit_code == 0
        assert outcome.step_results[0].stdout == "setup done\n"

        assert outcome.step_results[1].step_id == "verify_step"
        assert outcome.step_results[1].status == "completed"
        assert outcome.step_results[1].exit_code == 0
        assert (
            outcome.step_results[1].stdout
            == "PREV_ID=setup_step PREV_NAME=Setup Step PREV_IDX=1 PREV_STATUS=completed PREV_EXIT=0\n"
        )

    def test_run_steps_continue_on_failure_previous_step_status_is_ignored_with_exit_code_zero(
        self, tmp_path: Path
    ) -> None:
        context = RunContext(
            steps=[
                StepDefinition(
                    id="failing_step",
                    name="Failing Step",
                    type=StepType.COMMAND,
                    command="exit 3",
                    on_failure=OnFailureSpec(action=FailurePolicy.CONTINUE),
                ),
                StepDefinition(
                    id="next_step",
                    type=StepType.COMMAND,
                    command=(
                        'echo "PREV_ID=$WT_PREVIOUS_STEP_ID PREV_STATUS=$WT_PREVIOUS_STEP_STATUS '
                        'PREV_EXIT=$WT_PREVIOUS_STEP_EXIT_CODE"'
                    ),
                ),
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert len(outcome.step_results) == 2

        assert outcome.step_results[0].step_id == "failing_step"
        assert outcome.step_results[0].status == "ignored"
        assert outcome.step_results[0].exit_code == 0
        assert outcome.step_results[0].error_message == "Command failed with exit code 3."

        assert outcome.step_results[1].step_id == "next_step"
        assert outcome.step_results[1].status == "completed"
        assert outcome.step_results[1].exit_code == 0
        assert outcome.step_results[1].stdout == "PREV_ID=failing_step PREV_STATUS=ignored PREV_EXIT=0\n"

    def test_run_steps_prompt_user_retry_increments_step_attempt_env_var_from_one_to_two(self, tmp_path: Path) -> None:
        prompter = _ScriptedFailurePrompter([FailurePromptDecision.RETRY])
        context = RunContext(
            steps=[
                StepDefinition(
                    id="retry_on_prompt",
                    type=StepType.COMMAND,
                    command=(
                        'if [ "$WT_STEP_ATTEMPT" -eq 1 ]; then echo "fail1" >&2; exit 1; '
                        'else echo "success2"; exit 0; fi'
                    ),
                    on_failure=OnFailureSpec(action=FailurePolicy.PROMPT_USER),
                )
            ],
            cwd=tmp_path,
            use_sandbox=False,
            failure_prompter=prompter,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert len(outcome.step_results) == 1

        assert outcome.step_results[0].step_id == "retry_on_prompt"
        assert outcome.step_results[0].status == "completed"
        assert outcome.step_results[0].exit_code == 0
        assert outcome.step_results[0].stdout == "success2\n"
        assert outcome.step_results[0].attempts == 2

    def test_run_steps_three_step_run_propagates_steps_jinja_context_and_wt_steps_json_excluding_in_flight_step(
        self, tmp_path: Path
    ) -> None:
        context = RunContext(
            steps=[
                StepDefinition(
                    id="step_a",
                    name="Step Alpha",
                    type=StepType.COMMAND,
                    command='echo "A_STEPS=[{{ steps[0].id }}] A_JSON=$WT_STEPS_JSON"',
                ),
                StepDefinition(
                    id="step_b",
                    name="Step Beta",
                    type=StepType.COMMAND,
                    command=(
                        'echo "B_FIRST={{ steps[0].id }} B_LAST={{ steps[-1].id }} '
                        'B_A_STAT={{ steps.step_a.status }} B_JSON=$WT_STEPS_JSON"'
                    ),
                ),
                StepDefinition(
                    id="step_c",
                    name="Step Gamma",
                    type=StepType.COMMAND,
                    command=(
                        'echo "C_FIRST={{ steps[0].id }} C_SECOND={{ steps[1].id }} '
                        'C_LAST={{ steps[-1].id }} C_PREV={{ previous_step.id }} C_JSON=$WT_STEPS_JSON"'
                    ),
                ),
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert len(outcome.step_results) == 3

        assert outcome.step_results[0].step_id == "step_a"
        assert outcome.step_results[0].status == "completed"
        assert outcome.step_results[0].exit_code == 0
        assert outcome.step_results[0].stdout == "A_STEPS=[] A_JSON=[]\n"

        assert outcome.step_results[1].step_id == "step_b"
        assert outcome.step_results[1].status == "completed"
        assert outcome.step_results[1].exit_code == 0
        assert outcome.step_results[1].stdout == (
            "B_FIRST=step_a B_LAST=step_a B_A_STAT=completed "
            'B_JSON=[{"id": "step_a", "name": "Step Alpha", "index": "1", '
            '"status": "completed", "exit_code": "0"}]\n'
        )

        assert outcome.step_results[2].step_id == "step_c"
        assert outcome.step_results[2].status == "completed"
        assert outcome.step_results[2].exit_code == 0
        assert outcome.step_results[2].stdout == (
            "C_FIRST=step_a C_SECOND=step_b C_LAST=step_b C_PREV=step_b "
            'C_JSON=[{"id": "step_a", "name": "Step Alpha", "index": "1", '
            '"status": "completed", "exit_code": "0"}, {"id": "step_b", "name": "Step Beta", '
            '"index": "2", "status": "completed", "exit_code": "0"}]\n'
        )


class RunStepsOutputsPropagationTests:
    """[tier-1/integration] run_steps: a step's $WT_OUTPUT is readable by a later step via ${{ steps.<id>.outputs.<key> }}."""

    def test_run_steps_downstream_step_reads_upstream_step_output_via_dot_outputs_placeholder(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/integration] run_steps: step_a runs `echo "greeting=hello" >> "$WT_OUTPUT"`; step_b's command '{{ steps.step_a.outputs.greeting }}' interpolates to 'hello' before dispatch, so step_b's stdout contains 'hello'."""
        context = RunContext(
            steps=[
                StepDefinition(
                    id="step_a",
                    type=StepType.COMMAND,
                    command='echo "greeting=hello" >> "$WT_OUTPUT"',
                ),
                StepDefinition(
                    id="step_b",
                    type=StepType.COMMAND,
                    command='echo "{{ steps.step_a.outputs.greeting }}"',
                ),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            session_id="session-outputs",
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.step_results[1].stdout == "hello\n"

    def test_run_steps_downstream_step_output_reference_to_unknown_key_resolves_empty_string(
        self, tmp_path: Path
    ) -> None:
        """[tier-1/integration] run_steps: step_a writes no output; step_b's command echoing '[{{ steps.step_a.outputs.missing }}]' produces stdout '[]\\n'."""
        context = RunContext(
            steps=[
                StepDefinition(id="step_a", type=StepType.COMMAND, command="echo done"),
                StepDefinition(
                    id="step_b",
                    type=StepType.COMMAND,
                    command='echo "[{{ steps.step_a.outputs.missing }}]"',
                ),
            ],
            cwd=tmp_path,
            use_sandbox=False,
            session_id="session-outputs-missing",
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert outcome.step_results[1].stdout == "[]\n"


class RunStepsHistoricalMetadataAcrossLoopBoundaryTests:
    """[tier-1/integration] run_steps/_resolve_historical_steps_metadata: step name and status resolution survives a loop boundary."""

    def test_final_step_sees_correctly_named_steps_for_prior_step_and_both_loop_turns(self, tmp_path: Path) -> None:
        """[tier-1/integration] run_steps: a run of [top-level 'first' (name='First'), a 2-turn loop with one sub-step 'tick' (name='Tick'), top-level 'last'] gives 'last' a command '{{ steps[0].name }}|{{ steps[1].name }}|{{ steps[2].name }}' whose stdout is 'First|Tick|Tick\\n', proving _resolve_historical_steps_metadata names both loop-turn sub-step entries correctly alongside the top-level step."""
        context = RunContext(
            steps=[
                StepDefinition(id="first", name="First", type=StepType.COMMAND, command="echo first"),
                LoopStepBlock(
                    id="test-loop",
                    type="loop",
                    max_iterations=2,
                    until=["iteration.index >= 2"],
                    do=[StepDefinition(id="tick", name="Tick", type=StepType.COMMAND, command="echo tick")],
                ),
                StepDefinition(
                    id="last",
                    type=StepType.COMMAND,
                    command='echo "{{ steps[0].name }}|{{ steps[1].name }}|{{ steps[2].name }}"',
                ),
            ],
            cwd=tmp_path,
            use_sandbox=False,
        )

        outcome = run_steps(context)

        assert outcome.status == RunStatus.COMPLETED
        assert len(outcome.step_results) == 4
        assert outcome.step_results[-1].stdout == "First|Tick|Tick\n"
