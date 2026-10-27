"""Postconditions applied to a completed step dispatch: assertions, then artifact publishing."""

from __future__ import annotations

from pathlib import Path

from dovo.core.artifacts.store import ArtifactStore
from dovo.core.catalog.definitions import StepDefinition
from dovo.engine.executors.artifact_publish import publish_declared_artifacts
from dovo.engine.executors.assertions import evaluate_assertions
from dovo.engine.executors.models import StepDispatchOutcome, StepPostcondition


def _format_assertion_failure(step: StepDefinition, failed_conditions: list[str]) -> str:
    """Build the multi-line diagnostic block for a failed step assertion."""
    step_label = step.name or step.id
    lines = [f"Step '{step_label}' failed assertion checks:"]
    lines.extend(f"  [FAIL] {condition}" for condition in failed_conditions)
    return "\n".join(lines)


class AssertionPostcondition:
    """Fails a completed outcome when the step's assert: checks do not pass."""

    def __init__(self, step: StepDefinition, worktree_path: Path) -> None:
        self._step = step
        self._worktree_path = worktree_path

    def apply(self, outcome: StepDispatchOutcome, attempt: int) -> StepDispatchOutcome:
        """Return outcome unchanged when assert_ is unset or passes, else a failed copy with the assertion diagnostic."""
        if self._step.assert_ is None:
            return outcome

        result = evaluate_assertions(
            self._step.assert_,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            worktree_path=self._worktree_path,
        )
        if result.passed:
            return outcome

        return outcome.model_copy(
            update={
                "status": "failed",
                "error_message": _format_assertion_failure(self._step, result.failed_conditions),
            }
        )


class ArtifactPublishPostcondition:
    """Publishes the step's declared artifacts and fails a completed outcome when a required one is missing."""

    def __init__(
        self,
        step: StepDefinition,
        *,
        worktree_path: Path,
        store: ArtifactStore | None,
        loop_iteration: int | None,
        warnings: list[str],
    ) -> None:
        self._step = step
        self._worktree_path = worktree_path
        self._store = store
        self._loop_iteration = loop_iteration
        self._warnings = warnings

    def apply(self, outcome: StepDispatchOutcome, attempt: int) -> StepDispatchOutcome:
        """Publish declared artifacts, appending non-fatal warnings to the shared list and recorded failures to the outcome's diagnostics; fail on the first required failure."""
        published = publish_declared_artifacts(
            self._step,
            worktree_path=self._worktree_path,
            store=self._store,
            attempt=attempt,
            loop_iteration=self._loop_iteration,
        )
        self._warnings.extend(published.warnings)
        outcome = outcome.model_copy(update={"diagnostics": [*outcome.diagnostics, *published.diagnostics]})
        if published.error_message is None:
            return outcome

        return outcome.model_copy(update={"status": "failed", "error_message": published.error_message})


def build_postconditions(
    step: StepDefinition,
    *,
    worktree_path: Path,
    store: ArtifactStore | None,
    loop_iteration: int | None,
    warnings: list[str],
) -> list[StepPostcondition]:
    """Return [AssertionPostcondition, ArtifactPublishPostcondition] in evaluation order."""
    return [
        AssertionPostcondition(step, worktree_path),
        ArtifactPublishPostcondition(
            step, worktree_path=worktree_path, store=store, loop_iteration=loop_iteration, warnings=warnings
        ),
    ]
