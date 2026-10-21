"""Fluent blueprint data builder for Dovo CLI test suite."""

from __future__ import annotations

from dovo.core.catalog.definitions import BlueprintDefinition, LoopStepBlock, StepDefinition
from dovo.core.inputs import InputType, ParameterInput
from tests.harness.builders.step import StepBuilder


class BlueprintBuilder:
    """Fluent builder for constructing validated BlueprintDefinition models in tests."""

    def __init__(self, name: str = "test-blueprint") -> None:
        self._name: str = name
        self._use_worktree: bool = True
        self._timeout_seconds: int | None = None
        self._env: dict[str, str] = {}
        self._inputs: dict[str, ParameterInput] = {}
        self._steps: list[StepDefinition | StepBuilder | LoopStepBlock] = []

    def with_name(self, name: str) -> BlueprintBuilder:
        """Set the blueprint name."""
        self._name = name
        return self

    def with_use_worktree(self, use_worktree: bool) -> BlueprintBuilder:
        """Configure whether blueprint executes in an isolated worktree."""
        self._use_worktree = use_worktree
        return self

    def with_timeout(self, timeout_seconds: int) -> BlueprintBuilder:
        """Set execution timeout in seconds."""
        self._timeout_seconds = timeout_seconds
        return self

    def with_env(self, key: str, value: str) -> BlueprintBuilder:
        """Add an environment variable key-value pair."""
        self._env[key] = value
        return self

    def with_input(
        self,
        name: str,
        *,
        input_type: InputType | str = InputType.STRING,
        default: str | int | bool | None = None,
        required: bool = False,
        description: str | None = None,
        aliases: list[str] | None = None,
    ) -> BlueprintBuilder:
        """Declare a typed blueprint input parameter."""
        resolved_type = InputType(input_type) if isinstance(input_type, str) else input_type
        self._inputs[name] = ParameterInput(
            type=resolved_type,
            default=default,
            required=required,
            description=description,
            aliases=aliases or [],
        )
        return self

    def with_step(self, step: StepDefinition | StepBuilder | LoopStepBlock) -> BlueprintBuilder:
        """Append a step to the blueprint."""
        self._steps.append(step)
        return self

    def build(self) -> BlueprintDefinition:
        """Build and validate the BlueprintDefinition model."""
        resolved_steps: list[StepDefinition | LoopStepBlock] = []
        for step in self._steps:
            if isinstance(step, StepBuilder):
                resolved_steps.append(step.build())
            else:
                resolved_steps.append(step)

        return BlueprintDefinition(
            name=self._name,
            use_worktree=self._use_worktree,
            timeout_seconds=self._timeout_seconds,
            env=dict(self._env),
            inputs=dict(self._inputs),
            steps=resolved_steps,
        )
