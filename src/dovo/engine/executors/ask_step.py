"""Ask step execution: resolve an interaction into a request and turn a validated answer into a step result."""

from __future__ import annotations

import json

from dovo.common.error_codes import ErrorCode
from dovo.common.redact import SecretRedactor
from dovo.core.inputs import build_providers, interpolate_string
from dovo.core.interaction import Interaction, InteractionAnswer, InteractionRequest
from dovo.engine.executors.artifact_namespace import ArtifactNamespaceProvider
from dovo.engine.executors.exceptions import EngineError
from dovo.engine.executors.metadata import build_execution_metadata
from dovo.engine.executors.models import AskResolution, StepExecutionContext, StepRecord


def resolve_ask_request(context: StepExecutionContext) -> AskResolution:
    """Interpolate the message, resolve the interaction, and return the request, or a settled result for failures and empty multiselects."""
    step = context.step
    spec = step.interaction
    if spec is None:
        return _failed_resolution(step.id, f"Step '{step.id}' has no interaction to ask.")

    metadata = build_execution_metadata(
        step,
        step_index=context.step_index,
        attempt=context.initial_attempt,
        identity=context.identity,
        previous_step=context.previous_step,
        steps=context.steps,
        session_tmp_dir=context.session_tmp_dir,
        session_id=context.session_id or "",
        loop_iteration=context.loop_iteration,
    )
    providers = build_providers(
        inputs=context.inputs,
        metadata=metadata,
        artifacts=ArtifactNamespaceProvider(context.artifact_store),
    )
    try:
        message = interpolate_string(spec.message, providers)
    except EngineError as error:
        if error.error_code is not ErrorCode.ARTIFACT_CONTENT_UNAVAILABLE:
            raise
        return _failed_resolution(step.id, error.message)

    resolved = Interaction(context.artifact_store, _redactor(context)).resolve(step.id, spec, message)
    if not resolved.ok or resolved.request is None:
        return _failed_resolution(step.id, resolved.errors[0])

    if resolved.request.mode == "multiselect" and not resolved.request.options:
        notice = f"No options to choose from for '{step.id}'; continuing with an empty selection."
        completed = complete_ask_step(context, resolved.request, InteractionAnswer())
        return AskResolution(result=completed.model_copy(update={"warnings": [notice]}))

    return AskResolution(request=resolved.request)


def complete_ask_step(
    context: StepExecutionContext, request: InteractionRequest, answer: InteractionAnswer
) -> StepRecord:
    """Build the step result from a validated answer: masked outputs, JSON stdout, and a failed result for a `fail: true` option."""
    redactor = _redactor(context)
    masked = answer.model_copy(
        update={
            "comment": None if answer.comment is None else redactor.redact_text(answer.comment),
            "text": None if answer.text is None else redactor.redact_text(answer.text),
        }
    )
    outputs = Interaction.outputs(request, masked)
    failing = Interaction.failing_option(request, masked)
    return StepRecord(
        step_id=request.step_id,
        status="completed" if failing is None else "failed",
        exit_code=0 if failing is None else 1,
        stdout=_stdout_json(outputs),
        stderr="",
        duration_seconds=0.0,
        attempts=context.initial_attempt,
        error_message=None if failing is None else f"Prompt '{request.step_id}' answered '{failing.value}'.",
        outputs=outputs,
    )


def _redactor(context: StepExecutionContext) -> SecretRedactor:
    """Build the run's redactor from the context's sensitive variables and repository .env."""
    env_file_dir = context.paths.root_dir if context.paths is not None else None
    return SecretRedactor.from_environment(credential_envs=context.sensitive_variables, env_file_dir=env_file_dir)


def _stdout_json(outputs: dict[str, str]) -> str:
    """Serialize outputs as one JSON object for stdout, with `selected_count` as an integer."""
    return json.dumps({**outputs, "selected_count": int(outputs["selected_count"])})


def _failed_resolution(step_id: str, message: str) -> AskResolution:
    """Wrap a failed resolve result as a failed StepRecord with exit code 1 and the ASK_* message."""
    return AskResolution(
        result=StepRecord(
            step_id=step_id,
            status="failed",
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message=message,
        )
    )
