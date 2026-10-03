"""Fluent step data builder for Worktree CLI test suite."""

from __future__ import annotations

from worktree.common.models import FailurePolicy, OnFailureSpec
from worktree.core.catalog.definitions import StepAssert, StepDefinition, StepType


def _merge_string_items(
    existing: str | list[str] | None,
    new_value: str | list[str],
) -> str | list[str]:
    """Merge string assertion criteria, concatenating into a list if multiple are provided."""
    if existing is None:
        return new_value
    existing_items = [existing] if isinstance(existing, str) else list(existing)
    new_items = [new_value] if isinstance(new_value, str) else list(new_value)
    return existing_items + new_items


def _merge_int_items(
    existing: int | list[int] | None,
    new_value: int | list[int],
) -> int | list[int]:
    """Merge int assertion criteria, concatenating into a list if multiple are provided."""
    if existing is None:
        return new_value
    existing_items = [existing] if isinstance(existing, int) else list(existing)
    new_items = [new_value] if isinstance(new_value, int) else list(new_value)
    return existing_items + new_items


class StepBuilder:
    """Fluent builder for constructing validated StepDefinition models in tests."""

    def __init__(self, command: str = "echo test") -> None:
        self._id: str = "step-1"
        self._name: str | None = None
        self._description: str | None = None
        self._type: StepType | None = StepType.COMMAND
        self._command: str | None = command
        self._prompt: str | None = None
        self._script_path: str | None = None
        self._run: str | None = None
        self._uses: str | None = None
        self._timeout: int = 30
        self._env: dict[str, str] = {}
        self._assert: StepAssert | None = None
        self._on_failure: OnFailureSpec = OnFailureSpec(action=FailurePolicy.ABORT)

    @classmethod
    def command(cls, cmd: str) -> StepBuilder:
        """Create a StepBuilder initialized for command execution."""
        return cls(command=cmd)

    @classmethod
    def run(cls, run_cmd: str) -> StepBuilder:
        """Create a StepBuilder initialized for bare shorthand run execution."""
        builder = cls()
        builder._type = None
        builder._command = None
        builder._run = run_cmd
        return builder

    @classmethod
    def agent(cls, prompt: str) -> StepBuilder:
        """Create a StepBuilder initialized for agent prompt execution."""
        builder = cls()
        builder._type = StepType.AGENT
        builder._command = None
        builder._prompt = prompt
        return builder

    @classmethod
    def script(cls, script_path: str) -> StepBuilder:
        """Create a StepBuilder initialized for script execution."""
        builder = cls()
        builder._type = StepType.SCRIPT
        builder._command = None
        builder._script_path = script_path
        return builder

    @classmethod
    def uses(cls, template_ref: str) -> StepBuilder:
        """Create a StepBuilder initialized for step inheritance via template reference."""
        builder = cls()
        builder._type = None
        builder._command = None
        builder._uses = template_ref
        return builder

    def with_uses(self, template_ref: str) -> StepBuilder:
        """Set the step inheritance template reference."""
        self._uses = template_ref
        self._type = None
        self._command = None
        self._prompt = None
        self._script_path = None
        self._run = None
        return self

    def with_id(self, step_id: str) -> StepBuilder:
        """Set the step identifier."""
        self._id = step_id
        return self

    def with_name(self, name: str) -> StepBuilder:
        """Set the human-readable step name."""
        self._name = name
        return self

    def with_description(self, description: str) -> StepBuilder:
        """Set the step description."""
        self._description = description
        return self

    def with_timeout(self, timeout_seconds: int) -> StepBuilder:
        """Set the execution timeout in seconds."""
        self._timeout = timeout_seconds
        return self

    def with_env(self, key: str, value: str) -> StepBuilder:
        """Add an environment variable key-value pair."""
        self._env[key] = value
        return self

    def with_env_vars(self, env: dict[str, str]) -> StepBuilder:
        """Merge multiple environment variables."""
        self._env.update(env)
        return self

    def with_retry(
        self,
        max_retries: int = 3,
        backoff_ms: int = 100,
        on_max_retries: FailurePolicy | str = FailurePolicy.ABORT,
    ) -> StepBuilder:
        """Configure step retry policy on failure."""
        terminal_policy = FailurePolicy(on_max_retries) if isinstance(on_max_retries, str) else on_max_retries
        self._on_failure = OnFailureSpec(
            action=FailurePolicy.RETRY,
            max_retries=max_retries,
            backoff_ms=backoff_ms,
            on_max_retries=terminal_policy,
        )
        return self

    def with_on_failure(self, on_failure: OnFailureSpec | FailurePolicy | str) -> StepBuilder:
        """Set explicit on_failure specification or policy."""
        if isinstance(on_failure, OnFailureSpec):
            self._on_failure = on_failure
        elif isinstance(on_failure, FailurePolicy):
            self._on_failure = OnFailureSpec(action=on_failure)
        else:
            self._on_failure = OnFailureSpec(action=FailurePolicy(on_failure))
        return self

    def with_assert(self, step_assert: StepAssert) -> StepBuilder:
        """Set explicit StepAssert model."""
        self._assert = step_assert
        return self

    def _update_assert(self, **kwargs: object) -> StepBuilder:
        current = self._assert.model_dump() if self._assert is not None else {}
        current.update(kwargs)
        self._assert = StepAssert(**current)
        return self

    def assert_exit_code(self, exit_code: int | list[int]) -> StepBuilder:
        """Configure expected exit code assertion."""
        current = self._assert.exit_code if self._assert is not None else None
        return self._update_assert(exit_code=_merge_int_items(current, exit_code))

    def assert_output_contains(self, text: str | list[str]) -> StepBuilder:
        """Configure expected output substring assertion."""
        current = self._assert.output_contains if self._assert is not None else None
        return self._update_assert(output_contains=_merge_string_items(current, text))

    def assert_output_not_contains(self, text: str | list[str]) -> StepBuilder:
        """Configure forbidden output substring assertion."""
        current = self._assert.output_not_contains if self._assert is not None else None
        return self._update_assert(output_not_contains=_merge_string_items(current, text))

    def assert_file_exists(self, path: str | list[str]) -> StepBuilder:
        """Configure expected file existence assertion."""
        current = self._assert.file_exists if self._assert is not None else None
        return self._update_assert(file_exists=_merge_string_items(current, path))

    def assert_file_not_exists(self, path: str | list[str]) -> StepBuilder:
        """Configure expected file non-existence assertion."""
        current = self._assert.file_not_exists if self._assert is not None else None
        return self._update_assert(file_not_exists=_merge_string_items(current, path))

    def assert_file_not_empty(self, path: str | list[str]) -> StepBuilder:
        """Configure expected non-empty file assertion."""
        current = self._assert.file_not_empty if self._assert is not None else None
        return self._update_assert(file_not_empty=_merge_string_items(current, path))

    def build(self) -> StepDefinition:
        """Build and validate the StepDefinition instance."""
        if self._run is not None:
            return StepDefinition(
                id=self._id,
                run=self._run,
                name=self._name,
                description=self._description,
                timeout_seconds=self._timeout,
                env=dict(self._env),
                assert_=self._assert,
                on_failure=self._on_failure,
            )
        if self._uses is not None:
            return StepDefinition(
                id=self._id,
                uses=self._uses,
                name=self._name,
                description=self._description,
                timeout_seconds=self._timeout,
                env=dict(self._env),
                assert_=self._assert,
                on_failure=self._on_failure,
            )
        return StepDefinition(
            id=self._id,
            type=self._type,
            command=self._command,
            prompt=self._prompt,
            script_path=self._script_path,
            name=self._name,
            description=self._description,
            timeout_seconds=self._timeout,
            env=dict(self._env),
            assert_=self._assert,
            on_failure=self._on_failure,
        )
