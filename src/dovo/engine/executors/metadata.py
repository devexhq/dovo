"""Execution metadata builder and environment variable formatting service."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.models import (
    BlueprintMetadata,
    ExecutionIdentity,
    ExecutionMetadata,
    IterationMetadata,
    PreviousStepMetadata,
    StepMetadata,
    StepResult,
    TempMetadata,
)


def resolve_step_temp_paths(session_tmp_dir: Path, step_id: str) -> tuple[Path, Path]:
    """Compute the step scratch directory and step output file path rooted at session_tmp_dir."""
    return session_tmp_dir / "steps" / step_id, session_tmp_dir / f"step_{step_id}.output"


def resolve_step_log_paths(
    session_log_dir: Path, *, step_index: int, step_id: str, attempt: int, iteration: int | None = None
) -> tuple[Path, Path]:
    """Compute the per-attempt stdout/stderr log file paths rooted at session_log_dir, disambiguated by loop iteration when set."""
    iteration_segment = f"_iter_{iteration}" if iteration is not None else ""
    stem = f"{step_index:02d}_{step_id}{iteration_segment}_attempt_{attempt}"
    return session_log_dir / f"{stem}.stdout.log", session_log_dir / f"{stem}.stderr.log"


def _build_tmp_metadata(session_tmp_dir: Path | None, step_id: str) -> TempMetadata:
    """Build TempMetadata paths rooted at session_tmp_dir, or empty when no scratch space exists."""
    if session_tmp_dir is None:
        return TempMetadata()
    step_dir, output_file = resolve_step_temp_paths(session_tmp_dir, step_id)
    return TempMetadata(session_dir=str(session_tmp_dir), step_dir=str(step_dir), output_file=str(output_file))


def build_execution_metadata(
    step: StepDefinition,
    *,
    step_index: int = 1,
    attempt: int = 1,
    iteration_index: int = 1,
    identity: ExecutionIdentity | None = None,
    previous_step: PreviousStepMetadata | None = None,
    steps: Sequence[PreviousStepMetadata] | None = None,
    session_tmp_dir: Path | None = None,
    session_id: str = "",
) -> ExecutionMetadata:
    """Build structured execution metadata for a single step attempt."""
    step_metadata = StepMetadata(
        id=step.id,
        name=step.name or "",
        index=step_index,
        attempt=attempt,
    )
    blueprint_metadata = (
        BlueprintMetadata(name=identity.blueprint_name, key=identity.blueprint_key)
        if identity is not None
        else BlueprintMetadata()
    )
    historical_steps = list(steps) if steps is not None else []
    if previous_step is not None:
        prior_metadata = previous_step
    elif historical_steps:
        prior_metadata = historical_steps[-1]
    else:
        prior_metadata = PreviousStepMetadata()

    return ExecutionMetadata(
        step=step_metadata,
        blueprint=blueprint_metadata,
        previous_step=prior_metadata,
        steps=historical_steps,
        iteration=IterationMetadata(index=iteration_index),
        tmp=_build_tmp_metadata(session_tmp_dir, step.id),
        session_id=session_id,
    )


def metadata_to_env(metadata: ExecutionMetadata) -> dict[str, str]:
    """Format full DOVO_* process environment variable map."""
    env = {
        "DOVO_STEP_ID": metadata.step.id,
        "DOVO_STEP_NAME": metadata.step.name,
        "DOVO_STEP_INDEX": str(metadata.step.index),
        "DOVO_STEP_ATTEMPT": str(metadata.step.attempt),
        "DOVO_ITERATION_INDEX": str(metadata.iteration.index),
        "DOVO_BLUEPRINT_NAME": metadata.blueprint.name,
        "DOVO_BLUEPRINT_SHA": metadata.blueprint.key,
        "DOVO_PREVIOUS_STEP_ID": metadata.previous_step.id,
        "DOVO_PREVIOUS_STEP_NAME": metadata.previous_step.name,
        "DOVO_PREVIOUS_STEP_INDEX": metadata.previous_step.index,
        "DOVO_PREVIOUS_STEP_STATUS": metadata.previous_step.status,
        "DOVO_PREVIOUS_STEP_EXIT_CODE": metadata.previous_step.exit_code,
        "DOVO_STEPS_JSON": json.dumps([item.model_dump() for item in metadata.steps]),
        "DOVO_SESSION_ID": metadata.session_id,
    }
    if metadata.tmp.session_dir:
        env["DOVO_TEMP"] = metadata.tmp.session_dir
        env["DOVO_RUNNER_TEMP"] = metadata.tmp.session_dir
        env["DOVO_STEP_TEMP"] = metadata.tmp.step_dir
        env["DOVO_OUTPUT"] = metadata.tmp.output_file
    return env


def previous_step_metadata_from_result(
    result: StepResult,
    *,
    step_index: int,
    step_name: str = "",
) -> PreviousStepMetadata:
    """Construct PreviousStepMetadata from a completed StepResult."""
    return PreviousStepMetadata(
        id=result.step_id,
        name=step_name,
        index=str(step_index),
        status=result.status,
        exit_code=str(result.exit_code),
        outputs=result.outputs,
    )
