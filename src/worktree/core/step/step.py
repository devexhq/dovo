"""Step domain facade."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from worktree.common.filesystem.models import WorkspacePaths
from worktree.core.catalog import Catalog, CatalogFileNotFoundError, CatalogYamlError
from worktree.core.catalog.models import CatalogItemType
from worktree.core.step.assertions import evaluate_assertions
from worktree.core.step.exceptions import StepValidationError
from worktree.core.step.models import (
    AssertionResult,
    ConditionEvaluationResult,
    ExecutionIdentity,
    ExecutionMetadata,
    PreviousStepMetadata,
    StepAssert,
    StepDefinition,
    StepResult,
    StepType,
)
from worktree.core.step.services.conditions import (
    ParsedCondition,
    evaluate_condition,
    parse_condition_expression,
    validate_condition_expression,
)
from worktree.core.step.services.metadata import (
    build_execution_metadata,
    metadata_to_env,
    previous_step_metadata_from_result,
)


class Step:
    """Unified entrypoint for step definition, resolution, and execution."""

    definition_cls = StepDefinition
    definition_type = CatalogItemType.STEP
    instance: StepDefinition

    def __init__(self, *, instance: StepDefinition | None = None, definition: dict[str, Any] | None = None):
        if instance is not None:
            self.instance = instance
        elif definition is not None:
            self.instance = self.definition_cls.model_validate(definition)
        else:
            raise ValueError("Step requires an instance or definition.")

    @classmethod
    def load(
        cls,
        source: dict[str, Any] | Path | str | StepDefinition,
        *,
        paths: WorkspacePaths | None = None,
        catalog: Catalog | None = None,
    ) -> Step | None:
        """Load a step from a dictionary, file path, StepDefinition, or catalog step ID."""
        if isinstance(source, StepDefinition):
            return cls(instance=source)
        if isinstance(source, dict):
            try:
                return cls(definition=source)
            except (ValidationError, ValueError):
                return None
        if isinstance(source, Path) or (isinstance(source, str) and Path(source).is_file()):
            return cls.load_by_path(Path(source))
        return cls.load_by_name(str(source), paths=paths, catalog=catalog)

    @classmethod
    def load_by_name(
        cls,
        name: str,
        *,
        paths: WorkspacePaths | None = None,
        catalog: Catalog | None = None,
    ) -> Step | None:
        """Resolve a catalog step by name or key via the Catalog index."""
        if catalog is not None:
            cat = catalog
        elif paths is not None:
            cat = Catalog(paths)
        else:
            return None
        result = cat.get(name, item_type=cls.definition_type, definition_cls=cls.definition_cls)
        if result.ok and result.definition is not None:
            return cls(instance=result.definition)

        return None

    @classmethod
    def load_by_path(cls, path: str | Path) -> Step | None:
        """Resolve a catalog item by path, returning None when it cannot be loaded."""
        try:
            return cls(definition=Catalog.read_yaml(Path(path)))
        except (CatalogFileNotFoundError, CatalogYamlError, ValidationError):
            return None

    def resolve(
        self,
        *,
        paths: WorkspacePaths | None = None,
        catalog: Catalog | None = None,
    ) -> StepDefinition | None:
        """Resolve shorthand step fields (e.g. `uses: ...` or `run: ...`)."""
        return self.resolve_step_definition(paths=paths, catalog=catalog)

    # @staticmethod
    # def run(
    #     step: StepDefinition,
    #     sandbox_path: Path,
    #     *,
    #     context: dict[str, Any] | None = None,
    #     on_output: Callable[[str, str], None] | None = None,
    #     step_index: int = 1,
    #     initial_attempt: int = 1,
    #     iteration_index: int = 1,
    #     identity: ExecutionIdentity | None = None,
    #     previous_step: PreviousStepMetadata | None = None,
    #     steps: Sequence[PreviousStepMetadata] | None = None,
    # ) -> StepResult:
    #     """Execute a step synchronously within a sandbox directory."""
    #     exec_context = StepExecutionContext(
    #         step=step,
    #         sandbox_path=sandbox_path,
    #         context=context,
    #         on_output=on_output,
    #         step_index=step_index,
    #         initial_attempt=initial_attempt,
    #         iteration_index=iteration_index,
    #         identity=identity,
    #         previous_step=previous_step,
    #         steps=steps,
    #     )
    #     return StepExecution(exec_context).run()

    @staticmethod
    def evaluate_assertions(
        assert_config: StepAssert,
        *,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        sandbox_path: Path = Path("."),
    ) -> AssertionResult:
        """Evaluate a step's assert criteria."""
        return evaluate_assertions(
            assert_config,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            sandbox_path=sandbox_path,
        )

    @staticmethod
    def evaluate_condition(
        expression: str,
        *,
        iteration_index: int = 1,
        step_results: dict[str, StepResult] | None = None,
    ) -> ConditionEvaluationResult:
        """Parse and evaluate a single condition expression."""
        return evaluate_condition(
            expression,
            iteration_index=iteration_index,
            step_results=step_results,
        )

    @staticmethod
    def parse_condition(expression: str) -> ParsedCondition | None:
        """Parse condition string into structured ParsedCondition."""
        return parse_condition_expression(expression)

    @staticmethod
    def validate_condition(
        expression: str,
        known_step_ids: set[str] | None = None,
    ) -> list[str]:
        """Validate condition expression syntax and step references."""
        return validate_condition_expression(expression, known_step_ids=known_step_ids)

    @staticmethod
    def build_metadata(
        step: StepDefinition,
        *,
        step_index: int = 1,
        attempt: int = 1,
        iteration_index: int = 1,
        identity: ExecutionIdentity | None = None,
        previous_step: PreviousStepMetadata | None = None,
        steps: Sequence[PreviousStepMetadata] | None = None,
    ) -> ExecutionMetadata:
        """Build execution metadata container for step interpolation and environment."""
        return build_execution_metadata(
            step,
            step_index=step_index,
            attempt=attempt,
            iteration_index=iteration_index,
            identity=identity,
            previous_step=previous_step,
            steps=steps,
        )

    @staticmethod
    def metadata_to_env(metadata: ExecutionMetadata) -> dict[str, str]:
        """Convert step execution metadata to environment variables map."""
        return metadata_to_env(metadata)

    @staticmethod
    def previous_step_metadata(result: StepResult, *, step_index: int = 1) -> PreviousStepMetadata:
        """Construct PreviousStepMetadata from a completed StepResult."""
        return previous_step_metadata_from_result(result, step_index=step_index)

    def resolve_step_definition(
        self,
        *,
        paths: WorkspacePaths | None = None,
        catalog: Catalog | None = None,
    ) -> StepDefinition | None:
        """Resolve a step, following its dependencies if any."""
        if self.instance.run is not None:
            return self._resolve_run()

        if self.instance.uses is not None:
            return self._resolve_from_uses(paths=paths, catalog=catalog)

        if self.instance.type is not None:
            return self.instance

        return None

    def _resolve_run(self) -> StepDefinition:
        """Expand a 'run' shorthand step into a concrete COMMAND StepDefinition."""
        return StepDefinition.model_validate(
            {
                "id": self.instance.id,
                "name": self.instance.name,
                "description": self.instance.description,
                "type": StepType.COMMAND,
                "command": self.instance.run,
                "env": self.instance.env,
                "timeout_seconds": self.instance.timeout_seconds,
                "assert": self.instance.assert_,
                "on_failure": self.instance.on_failure,
            }
        )

    def _resolve_from_uses(
        self,
        *,
        paths: WorkspacePaths | None = None,
        catalog: Catalog | None = None,
    ) -> StepDefinition | None:
        """Load the referenced step and apply only fields explicitly set in this step."""
        if self.instance.uses is None:
            return None
        if paths is None and catalog is None:
            return None

        base_step = Step.load(str(self.instance.uses), paths=paths, catalog=catalog)
        if base_step is None:
            return None

        base_definition = base_step.resolve(paths=paths, catalog=catalog) or base_step.instance
        return merge_uses_step(self.instance, base_definition)


def merge_uses_step(using: StepDefinition, base_definition: StepDefinition) -> StepDefinition | None:
    """Merge a uses: step's explicitly-set fields onto a resolved base step definition, or None on validation failure."""
    fields_set = using.model_fields_set

    def _pick(field_name: str) -> object:
        return getattr(using, field_name) if field_name in fields_set else getattr(base_definition, field_name)

    try:
        return StepDefinition.model_validate(
            {
                "id": using.id,
                "name": _pick("name"),
                "type": base_definition.type,
                "description": _pick("description"),
                "command": base_definition.command,
                "prompt": _pick("prompt"),
                "script_path": _pick("script_path"),
                "tools": _pick("tools"),
                "env": {**base_definition.env, **using.env},
                "timeout_seconds": _pick("timeout_seconds"),
                "assert": _pick("assert_"),
                "on_failure": _pick("on_failure"),
            }
        )
    except (ValidationError, ValueError):
        return None


def resolve_step_definition(
    step: StepDefinition | dict[str, Any],
    *,
    paths: WorkspacePaths | None = None,
    catalog: Catalog | None = None,
) -> StepDefinition:
    """Resolve a step's 'run' or 'uses' shorthand into a concrete StepDefinition.

    Args:
        step: A StepDefinition instance or raw step dictionary mapping.
        paths: Resolved workspace snapshot for loading referenced catalog steps.
        catalog: Optional Catalog instance for loading referenced catalog steps.

    Returns:
        Expanded, concrete StepDefinition instance.

    Raises:
        StepValidationError: If the step cannot be resolved or fails validation.
    """
    if isinstance(step, dict):
        try:
            step_obj = Step(definition=step)
        except (ValidationError, ValueError) as exc:
            step_id = str(step.get("id", "<unknown>"))
            raise StepValidationError(f"Step validation failed for '{step_id}': {exc}") from exc
    else:
        step_obj = Step(instance=step)

    resolved = step_obj.resolve(paths=paths, catalog=catalog)
    if resolved is None:
        step_id = step_obj.instance.id
        if step_obj.instance.uses is not None and paths is None and catalog is None:
            raise StepValidationError(f"Cannot resolve step '{step_id}' using 'uses' without workspace paths.")
        raise StepValidationError(f"Step '{step_id}' must specify one of 'run', 'uses', or 'type'.")

    return resolved
