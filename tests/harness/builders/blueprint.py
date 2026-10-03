"""Fluent blueprint data builder for Worktree CLI test suite."""

from __future__ import annotations

from tests.harness.builders.step import StepBuilder
from worktree.core.catalog.definitions import BlueprintDefaults, BlueprintDefinition, LoopStepBlock, StepDefinition
from worktree.core.inputs.models import InputType, ParameterInput


class BlueprintBuilder:
    """Fluent builder for constructing validated BlueprintDefinition models in tests."""

    def __init__(self, name: str = "test-blueprint") -> None:
        self._name: str = name
        self._description: str = ""
        self._summary: str = ""
        self._version: int | str = 1
        self._use_sandbox: bool = True
        self._timeout_seconds: int | None = None
        self._env: dict[str, str] = {}
        self._inputs: dict[str, ParameterInput] = {}
        self._defaults: BlueprintDefaults = BlueprintDefaults()
        self._steps: list[StepDefinition | StepBuilder | LoopStepBlock] = []

    @classmethod
    def workflow(cls, name: str = "test-workflow") -> BlueprintBuilder:
        """Create a BlueprintBuilder initialized for workflow blueprints."""
        return cls(name=name)

    @classmethod
    def task(cls, name: str = "test-task") -> BlueprintBuilder:
        """Create a BlueprintBuilder initialized for task blueprints."""
        return cls(name=name)

    def with_name(self, name: str) -> BlueprintBuilder:
        """Set the blueprint name."""
        self._name = name
        return self

    def with_description(self, description: str) -> BlueprintBuilder:
        """Set the blueprint description."""
        self._description = description
        return self

    def with_summary(self, summary: str) -> BlueprintBuilder:
        """Set the blueprint summary."""
        self._summary = summary
        return self

    def with_version(self, version: int | str) -> BlueprintBuilder:
        """Set the blueprint version."""
        self._version = version
        return self

    def with_use_sandbox(self, use_sandbox: bool) -> BlueprintBuilder:
        """Configure whether blueprint executes in an isolated sandbox."""
        self._use_sandbox = use_sandbox
        return self

    def with_timeout(self, timeout_seconds: int) -> BlueprintBuilder:
        """Set execution timeout in seconds."""
        self._timeout_seconds = timeout_seconds
        return self

    def with_env(self, key: str, value: str) -> BlueprintBuilder:
        """Add an environment variable key-value pair."""
        self._env[key] = value
        return self

    def with_env_vars(self, env: dict[str, str]) -> BlueprintBuilder:
        """Merge multiple environment variables."""
        self._env.update(env)
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

    def add_step(self, step: StepDefinition | StepBuilder | LoopStepBlock) -> BlueprintBuilder:
        """Alias for with_step."""
        return self.with_step(step)

    def with_defaults(self, defaults: BlueprintDefaults) -> BlueprintBuilder:
        """Configure blueprint defaults."""
        self._defaults = defaults
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
            description=self._description,
            summary=self._summary,
            version=self._version,
            use_sandbox=self._use_sandbox,
            timeout_seconds=self._timeout_seconds,
            env=dict(self._env),
            inputs=dict(self._inputs),
            defaults=self._defaults,
            steps=resolved_steps,
        )
